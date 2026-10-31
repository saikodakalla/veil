"""Boundary and workflow tests for the private loopback API."""

import io
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from backend.app import main


@pytest.fixture
def client(monkeypatch):
    main.session.clear()
    monkeypatch.setattr(main.manager, "status", lambda: {"state": "ready", "name": "test model"})
    with TestClient(main.app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000), headers={"X-Redact-Client": "veil"}) as local:
        yield local
    # TestClient's lifespan closes the executor. Restore one for the next test.
    from concurrent.futures import ThreadPoolExecutor
    main.session.executor = ThreadPoolExecutor(max_workers=1)
    main.session.clear()


def import_fake(client, monkeypatch):
    def fake_ingest(path, folder):
        return {"text": "Patient Jane Smith", "pages": [{"width": 612, "height": 792, "ocr": False}]}
    monkeypatch.setattr(main.document_engine, "ingest_document", fake_ingest)
    response = client.post("/api/documents", files=[("files", ("Jane Smith record.txt", b"Patient Jane Smith", "text/plain"))])
    assert response.status_code == 200
    return response.json()["documents"][0]["id"]


def finish_analysis(client, document_id):
    for _ in range(100):
        result = client.get(f"/api/documents/{document_id}").json()
        if result["status"] not in {"queued", "processing"}:
            return result
        time.sleep(0.01)
    pytest.fail("Background analysis did not finish")


def test_local_boundary_blocks_foreign_hosts_origins_and_missing_header(client):
    assert client.get("/api/state", headers={"Host": "attacker.example"}).status_code == 403
    assert client.get("/api/state", headers={"Origin": "https://attacker.example"}).status_code == 403
    assert client.get("/api/state", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.delete("/api/session", headers={"X-Redact-Client": "wrong"}).status_code == 403
    assert client.get("/api/health").json()["local_only"] is True
    assert "frame-ancestors 'none'" in client.get("/api/health").headers["content-security-policy"]


def test_model_missing_never_falls_back_or_exports(client, monkeypatch):
    document_id = import_fake(client, monkeypatch)
    monkeypatch.setattr(main.manager, "status", lambda: {"state": "missing"})
    assert client.post(f"/api/documents/{document_id}/analyze", json={}).status_code == 409
    assert client.get(f"/api/documents/{document_id}/export").status_code == 409
    assert client.patch(f"/api/documents/{document_id}/review", json={"reviewed": True}).status_code == 409


def test_inference_failure_is_not_a_clean_result(client, monkeypatch):
    document_id = import_fake(client, monkeypatch)
    def fail(*args):
        raise RuntimeError("Sensitive Patient Jane Smith leaked from dependency")
    monkeypatch.setattr(main, "detect", fail)
    assert client.post(f"/api/documents/{document_id}/analyze", json={}).status_code == 200
    result = finish_analysis(client, document_id)
    assert result["status"] == "error"
    assert "Jane Smith" not in result["error"]
    assert client.get(f"/api/documents/{document_id}/export").status_code == 409


def test_review_is_required_and_zip_uses_generic_names(client, monkeypatch):
    document_id = import_fake(client, monkeypatch)
    span = {"id": "name1", "start": 8, "end": 18, "label": "PATIENT", "text": "Jane Smith", "source": "model", "score": .99}
    monkeypatch.setattr(main, "detect", lambda *args: [span])
    monkeypatch.setattr(main.document_engine, "map_spans", lambda doc, spans: [{**span, "boxes": [{"page": 0, "rect": [.1, .1, .4, .15]}]}])
    def fake_export(document, findings, destination):
        destination.write_bytes(b"%PDF-1.4\nSANITIZED\n%%EOF")
        return destination
    monkeypatch.setattr(main.document_engine, "export_redacted", fake_export)
    client.post(f"/api/documents/{document_id}/analyze", json={})
    result = finish_analysis(client, document_id)
    assert result["status"] == "review"
    assert client.get(f"/api/documents/{document_id}/export").status_code == 409
    response = client.patch(f"/api/documents/{document_id}/review", json={"reviewed": True, "manual_boxes": [{"page": 0, "rect": [.2, .2, .3, .3]}]})
    assert response.status_code == 200
    assert response.json()["finding_count"] == 2
    response = client.post("/api/export", json={"document_ids": [document_id]})
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == ["document-001-redacted.pdf", "manifest.json"]
        assert b"Jane Smith" not in archive.read("manifest.json")
    item = client.get(f"/api/documents/{document_id}").json()
    assert item["status"] == "exported"
    assert client.delete("/api/session").status_code == 200
    assert client.get(f"/api/documents/{document_id}").status_code == 404


def test_manual_rectangles_and_finding_ids_are_validated(client, monkeypatch):
    document_id = import_fake(client, monkeypatch)
    main.session.documents[document_id].update(status="review", findings=[])
    for box in [{"page": -1, "rect": [.1, .1, .2, .2]}, {"page": 0, "rect": [0, 0, 2, .2]}, {"page": 0, "rect": [0, 0, float("inf"), .2]}]:
        # nonfinite values cannot be encoded by a conforming JSON client
        if box["rect"][2] == float("inf"):
            with pytest.raises(main.HTTPException):
                main.normalize_manual_boxes([box], 1)
        else:
            assert client.patch(f"/api/documents/{document_id}/review", json={"manual_boxes": [box]}).status_code == 422
    assert client.patch(f"/api/documents/{document_id}/review", json={"excluded_finding_ids": ["missing"]}).status_code == 422


def test_unsupported_and_empty_uploads_fail_visibly(client):
    response = client.post("/api/documents", files=[("files", ("patient.exe", b"bad", "application/octet-stream"))])
    assert response.status_code == 200
    assert response.json()["documents"][0]["status"] == "error"
    response = client.post("/api/documents", files=[("files", ("empty.txt", b"", "text/plain"))])
    assert response.status_code == 422


def test_rejected_batch_does_not_leave_partial_imports(client, monkeypatch):
    existing = import_fake(client, monkeypatch)
    folders_before = set(main.session.root.iterdir())
    response = client.post("/api/documents", files=[
        ("files", ("valid.txt", b"A valid first document", "text/plain")),
        ("files", ("empty.txt", b"", "text/plain")),
    ])
    assert response.status_code == 422
    assert [item["id"] for item in client.get("/api/state").json()["documents"]] == [existing]
    assert set(main.session.root.iterdir()) == folders_before


def test_bulk_scan_never_resets_existing_review_or_manual_masks(client, monkeypatch):
    document_id = import_fake(client, monkeypatch)
    manual = [{"id": "manual-1", "page": 0, "rect": [.1, .1, .2, .2]}]
    main.session.documents[document_id].update(status="review", reviewed=True, manual_boxes=manual, excluded_finding_ids=["prior"])
    response = client.post("/api/analyze-all", json={})
    assert response.status_code == 200
    assert response.json()["documents"] == []
    result = client.get(f"/api/documents/{document_id}").json()
    assert result["reviewed"] is True
    assert result["manual_boxes"] == manual
    assert result["excluded_finding_ids"] == ["prior"]


def test_real_pdf_roundtrip_drops_source_text_and_metadata(client, monkeypatch):
    """Exercise actual ingestion, geometry, preview, export through HTTP."""
    import fitz
    from backend.app.detection import rule_spans

    source = fitz.open()
    page = source.new_page()
    page.insert_text((60, 80), "Patient: Jane Smith")
    page.insert_text((60, 100), "MRN: 12345678")
    page.insert_text((60, 130), "Diagnosis: seasonal allergies.")
    source.set_metadata({"title": "Jane Smith patient record", "author": "Dr Secret"})
    original = source.tobytes()
    source.close()
    monkeypatch.setattr(main, "detect", lambda text, policy: rule_spans(text, policy))
    response = client.post("/api/documents", files=[("files", ("secret-patient.pdf", original, "application/pdf"))])
    assert response.status_code == 200
    document_id = response.json()["documents"][0]["id"]
    assert response.json()["documents"][0]["status"] == "awaiting"
    client.post(f"/api/documents/{document_id}/analyze", json={})
    details = finish_analysis(client, document_id)
    assert details["status"] == "review"
    assert any(finding["text"] == "Jane Smith" for finding in details["findings"])
    preview = client.get(f"/api/documents/{document_id}/pages/0.png?redacted=true")
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/png"
    assert preview.content.startswith(b"\x89PNG")
    assert client.patch(f"/api/documents/{document_id}/review", json={"reviewed": True}).status_code == 200
    exported = client.get(f"/api/documents/{document_id}/export")
    assert exported.status_code == 200
    assert exported.headers["content-disposition"].endswith('"document-redacted.pdf"')
    result = fitz.open(stream=exported.content, filetype="pdf")
    assert len(result) == 1
    assert result[0].get_text() == ""
    assert result.metadata["title"] == ""
    assert result.metadata["author"] == ""
    assert len(result[0].get_images()) > 0
    assert b"Jane Smith" not in exported.content
    assert b"Dr Secret" not in exported.content
    result.close()
    public = client.get("/api/state").json()["documents"][0]
    assert "document" not in public and "findings" not in public
