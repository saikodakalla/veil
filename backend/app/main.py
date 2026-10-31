"""Loopback-only API and static application host.

Documents live in an owner-only temporary directory, never in the repository.
There is deliberately no network inference path or external document service.
"""

from __future__ import annotations

import atexit
import ipaddress
import json
import logging
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from . import documents as document_engine
from .detection import detect
from .model_manager import ModelUnavailable, manager

LOGGER = logging.getLogger("veil")
SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".docx", ".txt"}
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_BATCH_BYTES = 200 * 1024 * 1024
MAX_FILES = 100
CATEGORIES = ["PATIENT", "CLINICIAN", "ID", "DATE", "AGE", "PHONE", "EMAIL", "LOCATION", "URL"]
DEFAULT_POLICY = {"categories": CATEGORIES.copy(), "custom_terms": []}
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


class Policy(BaseModel):
    categories: list[str] = Field(default_factory=lambda: CATEGORIES.copy(), max_length=30)
    custom_terms: list[str] = Field(default_factory=list, max_length=500)


class AnalyzeRequest(BaseModel):
    policy: Policy = Field(default_factory=Policy)


class ReviewRequest(BaseModel):
    excluded_finding_ids: list[str] = Field(default_factory=list, max_length=100000)
    manual_boxes: list[dict[str, Any]] = Field(default_factory=list, max_length=10000)
    reviewed: bool = False


class ExportRequest(BaseModel):
    document_ids: list[str] = Field(default_factory=list, max_length=MAX_FILES)


class Session:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.id = uuid.uuid4().hex
        self.root = Path(tempfile.mkdtemp(prefix="veil-"))
        self.root.chmod(0o700)
        self.documents: dict[str, dict[str, Any]] = {}
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="veil-local")
        self.policy = DEFAULT_POLICY.copy()

    def clear(self) -> None:
        with self.lock:
            old_root = self.root
            self.id = uuid.uuid4().hex
            self.documents = {}
            self.policy = DEFAULT_POLICY.copy()
            self.root = Path(tempfile.mkdtemp(prefix="veil-"))
            self.root.chmod(0o700)
        shutil.rmtree(old_root, ignore_errors=True)

    def close(self) -> None:
        self.executor.shutdown(wait=True, cancel_futures=True)
        shutil.rmtree(self.root, ignore_errors=True)


session = Session()
atexit.register(session.close)


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    session.close()


app = FastAPI(title="Veil local redaction", docs_url=None, redoc_url=None, lifespan=lifespan)


@app.middleware("http")
async def local_only(request: Request, call_next):
    """Protect loopback service from DNS rebinding and cross-origin writes."""
    try:
        if request.client and not ipaddress.ip_address(request.client.host).is_loopback:
            return JSONResponse({"detail": "Veil accepts local connections only."}, status_code=403)
    except ValueError:
        return JSONResponse({"detail": "Veil accepts local connections only."}, status_code=403)
    host = request.url.hostname
    if host not in {"127.0.0.1", "localhost", "::1"}:
        return JSONResponse({"detail": "Veil accepts local connections only."}, status_code=403)
    if request.headers.get("sec-fetch-site") == "cross-site":
        return JSONResponse({"detail": "Cross-site requests are blocked."}, status_code=403)
    origin = request.headers.get("origin")
    if origin:
        parsed = urlparse(origin)
        if parsed.scheme not in {"http", "https"} or parsed.netloc != request.url.netloc:
            return JSONResponse({"detail": "Cross-origin requests are blocked."}, status_code=403)
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        if request.headers.get("x-redact-client") != "veil":
            return JSONResponse({"detail": "Missing local application header."}, status_code=403)
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_BATCH_BYTES + 1024 * 1024:
                return JSONResponse({"detail": "The request exceeds the 200 MB batch limit."}, status_code=413)
        except ValueError:
            return JSONResponse({"detail": "Invalid request size."}, status_code=400)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )
    return response


def public_document(item: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
    """Expose no filesystem paths or document content in the polling list."""
    data = {key: item.get(key) for key in (
        "id", "name", "size", "status", "pages", "finding_count", "reviewed", "error",
        "created_at", "synthetic", "progress", "stage", "ocr", "export_count",
    )}
    if full:
        data.update({
            "findings": item.get("findings", []),
            "excluded_finding_ids": item.get("excluded_finding_ids", []),
            "manual_boxes": item.get("manual_boxes", []),
            "page_sizes": [{"width": page["width"], "height": page["height"]}
                           for page in item.get("document", {}).get("pages", [])],
            "policy": item.get("policy", DEFAULT_POLICY),
        })
    return data


def get_document(document_id: str) -> dict[str, Any]:
    with session.lock:
        item = session.documents.get(document_id)
        if not item:
            raise HTTPException(404, "Document is no longer in this session.")
        return item


def normalize_policy(policy: Policy) -> dict[str, Any]:
    unknown = set(policy.categories) - set(CATEGORIES)
    if unknown:
        raise HTTPException(422, "Unknown redaction category.")
    terms = [term.strip() for term in policy.custom_terms if term.strip()]
    if any(len(term) > 500 for term in terms):
        raise HTTPException(422, "Custom terms must be at most 500 characters.")
    return {"categories": list(dict.fromkeys(policy.categories)), "custom_terms": terms}


def create_record(name: str, size: int, synthetic: bool = False) -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex, "name": Path(name).name[:255], "size": size,
        "status": "awaiting", "pages": 0, "finding_count": 0, "reviewed": False,
        "error": None, "created_at": time.time(), "synthetic": synthetic,
        "progress": 0, "stage": "Ready to analyze", "ocr": False, "export_count": 0,
        "findings": [], "excluded_finding_ids": [], "manual_boxes": [],
    }


def ingest_local(path: Path, item: dict[str, Any], target: Path) -> dict[str, Any]:
    try:
        document = document_engine.ingest_document(path, target)
        if not document.get("pages"):
            raise ValueError("This document has no readable pages.")
        if not document.get("text", "").strip():
            raise ValueError("No readable text was found. Verify the scan is clear and supported.")
        item["document"] = document
        item["pages"] = len(document["pages"])
        item["ocr"] = any(page.get("ocr") for page in document["pages"])
    except Exception as exc:
        # Deliberately omit exception text: third-party parsers can quote source content.
        item.update(status="error", error=safe_error(exc, "Unable to read this document. Try an unlocked PDF or a clearer scan."), stage="Import failed")
    return item


def safe_error(exc: Exception, default: str) -> str:
    """Only app-owned errors are allowed to reach the UI."""
    message = str(exc)
    if isinstance(exc, (document_engine.DocumentError, ModelUnavailable)):
        return message[:500]
    return default


@app.get("/api/health")
def health():
    return {"ok": True, "app": "Veil", "local_only": True}


@app.get("/api/state")
def state():
    with session.lock:
        return {"session_id": session.id, "documents": [public_document(item) for item in session.documents.values()],
                "model": manager.status(), "policy": session.policy, "limits": {"max_files": MAX_FILES, "max_file_bytes": MAX_FILE_BYTES}}


@app.get("/api/model")
def model_status():
    return manager.status()


@app.post("/api/model/install")
def install_model():
    return manager.start_install()


@app.post("/api/documents")
async def upload_documents(files: list[UploadFile] = File(...)):
    if not files or len(files) > MAX_FILES:
        raise HTTPException(422, f"Choose between 1 and {MAX_FILES} files.")
    with session.lock:
        if len(session.documents) + len(files) > MAX_FILES:
            raise HTTPException(422, f"A session can contain at most {MAX_FILES} documents.")
        root = session.root
        generation = session.id
    pending: list[tuple[dict[str, Any], Path | None, Path | None]] = []
    folders: list[Path] = []
    total = 0
    try:
        # Stage and validate the complete batch before committing anything to
        # session state. A rejected later file cannot leave a partial import.
        for upload in files:
            name = upload.filename or "document"
            suffix = Path(name).suffix.lower()
            item = create_record(name, 0)
            if suffix not in SUPPORTED_EXTENSIONS:
                item.update(status="error", stage="Import failed", error="Unsupported file. Choose PDF, PNG, JPG, TIFF, Word (.docx), or text.")
                pending.append((item, None, None))
                continue
            folder = root / item["id"]
            folder.mkdir(mode=0o700, parents=True)
            folders.append(folder)
            path = folder / f"source{suffix}"
            size = 0
            with path.open("wb") as output:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    total += len(chunk)
                    if size > MAX_FILE_BYTES or total > MAX_BATCH_BYTES:
                        raise HTTPException(413, "Files exceed the limit: 50 MB per file and 200 MB per batch.")
                    output.write(chunk)
            path.chmod(0o600)
            if size == 0:
                raise HTTPException(422, "Empty files cannot be imported.")
            item["size"] = size
            pending.append((item, path, folder))
        imported = []
        for item, path, folder in pending:
            with session.lock:
                if generation != session.id:
                    raise HTTPException(409, "Session changed during import. Import these files again.")
            if path is not None:
                item = await run_in_threadpool(ingest_local, path, item, folder)
            imported.append(item)
        with session.lock:
            if generation != session.id:
                raise HTTPException(409, "Session changed during import. Import these files again.")
            if len(session.documents) + len(imported) > MAX_FILES:
                raise HTTPException(422, f"A session can contain at most {MAX_FILES} documents.")
            for item in imported:
                session.documents[item["id"]] = item
        return {"documents": [public_document(item) for item in imported]}
    except BaseException:
        for folder in folders:
            shutil.rmtree(folder, ignore_errors=True)
        raise
    finally:
        for upload in files:
            await upload.close()


def analyze_worker(document_id: str, generation: str, policy: dict[str, Any]) -> None:
    with session.lock:
        item = session.documents.get(document_id)
        if not item or generation != session.id:
            return
        item.update(status="processing", stage="Finding sensitive information", progress=20)
        document = item["document"]
    try:
        spans = detect(document["text"], policy)
        mapped = document_engine.map_spans(document, spans)
        if any(not finding.get("boxes") for finding in mapped):
            raise RuntimeError("Detected information could not be located safely on the page.")
        with session.lock:
            if generation != session.id or document_id not in session.documents:
                return
            item.update(status="review", stage="Ready for review", progress=100,
                        findings=mapped, finding_count=len(mapped), policy=policy,
                        reviewed=False, excluded_finding_ids=[], manual_boxes=[], error=None)
    except Exception as exc:
        with session.lock:
            if generation == session.id and document_id in session.documents:
                item.update(status="error", progress=0, stage="Analysis failed", reviewed=False,
                            findings=[], finding_count=0,
                            error=safe_error(exc, "Analysis did not finish. Recheck the model and try again; this document cannot be exported yet."))
        LOGGER.warning("Local document analysis failed (%s)", type(exc).__name__)


def queue_analysis(item: dict[str, Any], policy: dict[str, Any]) -> None:
    if item["status"] in {"queued", "processing"}:
        return
    if not item.get("document"):
        raise HTTPException(422, "This file could not be read. Remove it and import a supported document.")
    item.update(status="queued", stage="Waiting for local model", progress=5,
                reviewed=False, error=None)
    session.executor.submit(analyze_worker, item["id"], session.id, policy)


def require_model() -> None:
    if manager.status().get("state") != "ready":
        raise HTTPException(409, "Install the local model before analyzing documents.")


@app.post("/api/documents/{document_id}/analyze")
def analyze_document(document_id: str, body: AnalyzeRequest):
    require_model()
    policy = normalize_policy(body.policy)
    with session.lock:
        item = get_document(document_id)
        session.policy = policy
        queue_analysis(item, policy)
        return public_document(item)


@app.post("/api/analyze-all")
def analyze_all(body: AnalyzeRequest):
    require_model()
    policy = normalize_policy(body.policy)
    with session.lock:
        session.policy = policy
        queued = []
        for item in session.documents.values():
            # Adding a new file must never silently discard an existing review
            # or its manual masks. Re-analysis is an explicit per-file action.
            if item.get("document") and item["status"] in {"awaiting", "error"}:
                queue_analysis(item, policy)
                queued.append(public_document(item))
        return {"documents": queued}


@app.get("/api/documents/{document_id}")
def read_document(document_id: str):
    return public_document(get_document(document_id), full=True)


def normalize_manual_boxes(boxes: list[dict[str, Any]], page_count: int) -> list[dict[str, Any]]:
    normalized = []
    for box in boxes:
        try:
            page = int(box["page"])
            if "rect" in box:
                rect = [float(number) for number in box["rect"]]
            else:
                x, y, width, height = [float(box[key]) for key in ("x", "y", "width", "height")]
                rect = [x, y, x + width, y + height]
            if len(rect) != 4 or not 0 <= page < page_count:
                raise ValueError()
            x0, y0, x1, y1 = rect
            if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
                raise ValueError()
            normalized.append({"id": str(box.get("id", uuid.uuid4().hex))[:64], "page": page, "rect": rect})
        except (KeyError, TypeError, ValueError, OverflowError):
            raise HTTPException(422, "Manual redactions need a valid page and a rectangle inside that page.")
    return normalized


@app.patch("/api/documents/{document_id}/review")
def review_document(document_id: str, body: ReviewRequest):
    with session.lock:
        item = get_document(document_id)
        if item["status"] not in {"review", "exported"}:
            raise HTTPException(409, "Wait for analysis to finish before reviewing.")
        valid_ids = {finding["id"] for finding in item["findings"]}
        if set(body.excluded_finding_ids) - valid_ids:
            raise HTTPException(422, "One or more findings no longer exist. Refresh the document.")
        manual = normalize_manual_boxes(body.manual_boxes, item["pages"])
        item.update(excluded_finding_ids=list(dict.fromkeys(body.excluded_finding_ids)),
                    manual_boxes=manual, reviewed=body.reviewed, status="review",
                    finding_count=len(item["findings"]) - len(set(body.excluded_finding_ids)) + len(manual),
                    stage="Reviewed · ready to export" if body.reviewed else "Ready for review")
        return public_document(item, full=True)


def active_findings(item: dict[str, Any]) -> list[dict[str, Any]]:
    excluded = set(item.get("excluded_finding_ids", []))
    findings = [finding for finding in item.get("findings", []) if finding["id"] not in excluded]
    findings.extend({"id": box["id"], "label": "MANUAL", "source": "manual", "boxes": [box]}
                    for box in item.get("manual_boxes", []))
    return findings


@app.get("/api/documents/{document_id}/pages/{page}.png")
def preview_page(document_id: str, page: int, redacted: bool = False):
    item = get_document(document_id)
    if not item.get("document") or not 0 <= page < item["pages"]:
        raise HTTPException(404, "Page not found.")
    boxes = [box["rect"] for finding in active_findings(item) for box in finding.get("boxes", [])
             if box["page"] == page] if redacted else None
    content = document_engine.render_page(item["document"], page, boxes)
    return Response(content=content, media_type="image/png")


def export_item(item: dict[str, Any], destination: Path) -> Path:
    if item["status"] not in {"review", "exported"} or not item.get("reviewed"):
        raise HTTPException(409, "Review this document and confirm the redactions before export.")
    try:
        result = document_engine.export_redacted(item["document"], active_findings(item), destination)
    except Exception:
        raise HTTPException(500, "The redacted file could not be created. No unredacted file was exported.")
    with session.lock:
        item.update(status="exported", stage="Exported locally", export_count=item.get("export_count", 0) + 1)
    return Path(result)


@app.get("/api/documents/{document_id}/export")
def export_document(document_id: str):
    item = get_document(document_id)
    target = session.root / item["id"] / "redacted.pdf"
    result = export_item(item, target)
    return FileResponse(result, media_type="application/pdf", filename="document-redacted.pdf")


@app.post("/api/export")
def export_batch(body: ExportRequest):
    with session.lock:
        identifiers = body.document_ids or [item["id"] for item in session.documents.values()
                                           if item.get("reviewed") and item["status"] in {"review", "exported"}]
        if not identifiers:
            raise HTTPException(409, "Review at least one document before exporting.")
        if len(identifiers) != len(set(identifiers)):
            raise HTTPException(422, "Duplicate document identifiers.")
        items = [get_document(identifier) for identifier in identifiers]
        if any(item["status"] not in {"review", "exported"} or not item.get("reviewed") for item in items):
            raise HTTPException(409, "Every selected document must finish analysis and review before export.")
        export_root = session.root / f"export-{uuid.uuid4().hex}"
        export_root.mkdir(mode=0o700)
    archive_path = export_root / "veil-redacted.zip"
    manifest = []
    try:
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for index, item in enumerate(items, 1):
                name = f"document-{index:03d}-redacted.pdf"
                target = export_item(item, export_root / name)
                bundle.write(target, arcname=name)
                manifest.append({"file": name, "pages": item["pages"], "redactions": item["finding_count"]})
            bundle.writestr("manifest.json", json.dumps({"app": "Veil", "documents": manifest}, indent=2))
    except Exception:
        shutil.rmtree(export_root, ignore_errors=True)
        raise
    return FileResponse(archive_path, media_type="application/zip", filename="veil-redacted.zip",
                        background=BackgroundTask(shutil.rmtree, export_root, ignore_errors=True))


@app.delete("/api/documents/{document_id}")
def delete_document(document_id: str):
    with session.lock:
        item = get_document(document_id)
        session.documents.pop(document_id, None)
        folder = session.root / item["id"]
    shutil.rmtree(folder, ignore_errors=True)
    return {"deleted": True}


@app.delete("/api/session")
def clear_session():
    session.clear()
    return {"cleared": True, "session_id": session.id}


@app.post("/api/demo")
async def create_demo():
    with session.lock:
        if len(session.documents) >= MAX_FILES:
            raise HTTPException(422, "Clear some documents before loading the sample.")
        item = create_record("Synthetic clinical note.pdf", 0, synthetic=True)
        folder = session.root / item["id"]
        folder.mkdir(mode=0o700)
        generation = session.id
    source = document_engine.synthetic_sample(folder / "source.pdf")
    item["size"] = Path(source).stat().st_size
    item = await run_in_threadpool(ingest_local, Path(source), item, folder)
    with session.lock:
        if generation != session.id:
            shutil.rmtree(folder, ignore_errors=True)
            raise HTTPException(409, "Session changed. Load the sample again.")
        session.documents[item["id"]] = item
    return {"documents": [public_document(item)]}


@app.get("/{path:path}")
def frontend(path: str):
    if path.startswith("api/"):
        raise HTTPException(404, "API route not found.")
    candidate = (FRONTEND_DIST / path).resolve()
    if candidate.is_relative_to(FRONTEND_DIST.resolve()) and candidate.is_file():
        return FileResponse(candidate)
    index = FRONTEND_DIST / "index.html"
    if index.is_file():
        return FileResponse(index)
    return JSONResponse({"detail": "Frontend is not built. Run ./start.sh to install and launch Veil."}, status_code=503)
