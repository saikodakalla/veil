"""Document normalization and destructive, black-pixel redaction.

Original bytes are never copied into an export. Every output page is rebuilt
from a lossless raster, with selected rectangles burned into the pixels.
"""
from __future__ import annotations

from collections import defaultdict
import io
import math
from pathlib import Path
import re
import warnings
import zipfile
import xml.etree.ElementTree as ET

import fitz
from PIL import Image, ImageDraw, ImageOps, ImageStat

from .ocr import OCRError, recognize


SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".txt", ".docx"}
MAX_PAGES = 300
MAX_RASTER_PIXELS = 30_000_000
RENDER_DPI = 180


class DocumentError(ValueError):
    pass


def _rect(box: list[float]) -> list[float]:
    if len(box) != 4 or not all(isinstance(x, (int, float)) and math.isfinite(x) for x in box):
        raise DocumentError("Invalid redaction rectangle.")
    x0, y0, x1, y1 = (max(0.0, min(1.0, float(x))) for x in box)
    if x0 >= x1 or y0 >= y1:
        raise DocumentError("Redaction rectangles must have a positive area.")
    return [x0, y0, x1, y1]


def _join_words(words: list[dict]) -> tuple[str, list[dict]]:
    """Reconstruct reading-order text while preserving offsets and word boxes."""
    if not words:
        return "", []
    # Vision returns reading order. Sort only into approximate horizontal rows,
    # retaining page order across multiple blocks when native extraction supplies it.
    result, pieces, position = [], [], 0
    previous = None
    for word in words:
        # Some embedded fonts expose a visible hyphen as U+00AD. Preserve the
        # glyph's box while normalizing it for recognizers and exact matching.
        token = word["text"].strip().replace("\u00ad", "-")
        if not token:
            continue
        if previous is not None:
            a, b = previous["box"], word["box"]
            same_line = min(a[3], b[3]) > max(a[1], b[1]) and b[0] >= a[0]
            # Vision supplies exact original separators. Preserve punctuation
            # adjacency in email addresses, IDs, URLs, and phone numbers.
            separator = word.get("separator", " " if same_line else "\n")
            pieces.append(separator)
            position += len(separator)
        result.append({**word, "text": token, "start": position, "end": position + len(token)})
        pieces.append(token)
        position += len(token)
        previous = word
    return "".join(pieces), result


def _native_words(page: fitz.Page) -> list[dict]:
    width, height = page.rect.width, page.rect.height
    result = []
    for x0, y0, x1, y1, token, *_ in page.get_text("words", sort=True):
        box = fitz.Rect(x0, y0, x1, y1) * page.rotation_matrix
        box.intersect(page.rect)
        if box.is_empty:
            continue
        result.append({"text": token, "box": [box.x0/width, box.y0/height, box.x1/width, box.y1/height],
                       "confidence": 1.0})
    return result


def _page_is_blank(path: Path) -> bool:
    with Image.open(path) as image:
        small = image.convert("L").resize((300, 300))
        stat = ImageStat.Stat(small)
        # A blank white page is safe; a logo, handwriting, or faint print is not.
        return stat.mean[0] > 252 and stat.stddev[0] < 2.5


def _read_docx(path: Path) -> str:
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    try:
        with zipfile.ZipFile(path) as archive:
            if sum(info.file_size for info in archive.infolist()) > 80_000_000:
                raise DocumentError("This Word document is too large to normalize safely. Export it as PDF first.")
            if any(info.filename.startswith(("word/media/", "word/charts/", "word/embeddings/")) for info in archive.infolist()):
                raise DocumentError("This Word document contains embedded images. Export it as PDF first so every image can be scanned and reviewed.")
            targets = ["word/document.xml"]
            targets += sorted(name for name in archive.namelist()
                              if re.fullmatch(r"word/(header\d+|footer\d+|footnotes|endnotes|comments)\.xml", name))
            paragraphs = []
            for name in targets:
                if name not in archive.namelist():
                    continue
                tree = ET.fromstring(archive.read(name))
                if any(element.tag.rsplit("}", 1)[-1] in {"drawing", "pict", "object", "altChunk"} for element in tree.iter()):
                    raise DocumentError("This Word document contains visual or embedded content. Export it as PDF first so all content can be reviewed.")
                for paragraph in tree.findall(".//w:p", namespace):
                    text = "".join(element.text or "" for element in paragraph.iter()
                                   if element.tag in {f"{{{namespace['w']}}}t", f"{{{namespace['w']}}}delText"})
                    if text.strip():
                        paragraphs.append(text)
            return "\n\n".join(paragraphs)
    except (zipfile.BadZipFile, ET.ParseError, KeyError) as error:
        raise DocumentError("This Word file could not be read. Export it as a PDF and retry.") from error


def _text_pdf(text: str) -> fitz.Document:
    if not text.strip():
        raise DocumentError("This document has no readable text.")
    if len(text) > 3_000_000:
        raise DocumentError("This text document is too large. Split it into smaller files.")
    document = fitz.open()
    # PyMuPDF's built-in font supports clinical English. An available system font
    # is embedded for Unicode where possible, still entirely on device.
    font_path = next((p for p in [Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
                                  Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]
                      if p.is_file()), None)
    font = fitz.Font(fontfile=str(font_path)) if font_path else fitz.Font("helv")
    font_name = "body" if font_path else "helv"
    size, line_height, available = 10.5, 16.0, 496
    lines = []
    for paragraph in text.replace("\t", "    ").splitlines():
        if not paragraph:
            lines.append("")
            continue
        line = ""
        for token in re.findall(r"\S+\s*", paragraph):
            if font.text_length(line + token, fontsize=size) <= available:
                line += token
                continue
            if line:
                lines.append(line.rstrip())
                line = ""
            # Very long IDs/URLs need wrapping too, with no hidden clipped text.
            for character in token:
                if font.text_length(line + character, fontsize=size) > available:
                    lines.append(line)
                    line = ""
                line += character
        if line:
            lines.append(line.rstrip())
    page = None
    y = 60.0
    for line in lines:
        if page is None or y > 780:
            page = document.new_page(width=612, height=842)
            if font_path:
                page.insert_font(fontname=font_name, fontfile=str(font_path))
            y = 60.0
        if line:
            page.insert_text((58, y), line, fontsize=size, fontname=font_name, color=(0.08, 0.1, 0.13))
        y += line_height
        if len(document) > MAX_PAGES:
            document.close()
            raise DocumentError(f"Documents are limited to {MAX_PAGES} pages per file.")
    return document


def ingest_document(path: str | Path, output_dir: str | Path) -> dict:
    """Normalize a supported file, produce preview rasters, and map text geometry.

    Searchable pages use their text layer; any page with raster images also runs
    OCR across the entire visible page. Missing OCR is a hard error for scans.
    """
    source, directory = Path(path), Path(output_dir)
    extension = source.suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise DocumentError("Use PDF, PNG, JPG, TIFF, TXT, or text-only DOCX files.")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    source_doc = None
    image_frames = []
    notes = []
    try:
        if extension == ".pdf":
            source_doc = fitz.open(source)
            if source_doc.needs_pass:
                raise DocumentError("This PDF is password protected. Save an unlocked copy and import it again.")
        elif extension in {".txt", ".docx"}:
            if extension == ".docx":
                text = _read_docx(source)
                notes.append("Word text, tables, headers, footers, and notes were reflowed. For original formatting or embedded images, import a PDF export.")
            else:
                try:
                    text = source.read_text(encoding="utf-8-sig")
                except UnicodeDecodeError as error:
                    raise DocumentError("Save this text file as UTF-8, then import it again.") from error
            source_doc = _text_pdf(text)
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(source) as image:
                    if getattr(image, "n_frames", 1) > MAX_PAGES:
                        raise DocumentError(f"Documents are limited to {MAX_PAGES} pages per file.")
                    for frame in range(getattr(image, "n_frames", 1)):
                        image.seek(frame)
                        if image.width * image.height > MAX_RASTER_PIXELS:
                            raise DocumentError("This image is too large. Resize it below 30 megapixels and retry.")
                        flat = ImageOps.exif_transpose(image).convert("RGB")
                        frame_path = directory / f"page-{frame+1:04d}.png"
                        flat.save(frame_path, format="PNG")
                        image_frames.append((frame_path, flat.width * 72/RENDER_DPI, flat.height * 72/RENDER_DPI))
        total = len(source_doc) if source_doc is not None else len(image_frames)
        if total == 0:
            raise DocumentError("This document has no pages.")
        if total > MAX_PAGES:
            raise DocumentError(f"Documents are limited to {MAX_PAGES} pages per file.")
        pages, text_parts, global_offset = [], [], 0
        for index in range(total):
            if source_doc is not None:
                page = source_doc[index]
                width, height = page.rect.width, page.rect.height
                if width < 1 or height < 1 or width * height > 10_000_000:
                    raise DocumentError("This PDF has an invalid or oversized page.")
                scale = min(RENDER_DPI/72, math.sqrt(MAX_RASTER_PIXELS/(width*height)))
                preview = directory / f"page-{index+1:04d}.png"
                pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False, annots=True)
                pixmap.save(preview)
                words = _native_words(page)
                has_images = bool(page.get_images())
                has_annotations = bool(list(page.annots() or []) or list(page.widgets() or []))
                # Visible form values / free-text annotations are absent from
                # page.get_text(), but get_pixmap(annots=True) paints them.
                needs_ocr = has_images or has_annotations or not words
                if needs_ocr and not _page_is_blank(preview):
                    # OCR is canonical for raster/mixed pages so no image area can
                    # escape detection merely because another area has a text layer.
                    words = recognize(preview)
                    if not words:
                        raise DocumentError(f"Page {index+1} contains visible content but OCR found no readable text. Use a clearer scan or redact it manually before import.")
                method = "Apple Vision / local OCR" if needs_ocr and words else "PDF text"
            else:
                preview, width, height = image_frames[index]
                words = [] if _page_is_blank(preview) else recognize(preview)
                if not words and not _page_is_blank(preview):
                    raise DocumentError(f"Page {index+1} contains visible content but OCR found no readable text. Use a clearer scan or redact it manually before import.")
                method = "Apple Vision / local OCR"
                needs_ocr = True
            text, mapped = _join_words(words)
            if global_offset + len(text) > 3_000_000:
                raise DocumentError("This document contains too much text. Split it into smaller files.")
            for word in mapped:
                word["start"] += global_offset
                word["end"] += global_offset
            pages.append({"index": index, "width": width, "height": height,
                          "text": text, "start": global_offset, "end": global_offset+len(text),
                          "words": mapped, "preview_path": str(preview.resolve()),
                          "ocr": needs_ocr, "extraction": method})
            text_parts.append(text)
            global_offset += len(text) + 2
        return {"text": "\n\n".join(text_parts), "pages": pages, "notes": notes,
                "source_path": str(source.resolve()), "format": extension.lstrip(".")}
    except OCRError as error:
        raise DocumentError(str(error)) from error
    except DocumentError:
        raise
    except (fitz.FileDataError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise DocumentError("This file could not be read safely. Try a standard PDF export or a smaller image.") from error
    finally:
        if source_doc is not None:
            source_doc.close()


def map_spans(document: dict, spans: list[dict]) -> list[dict]:
    """Map detector offsets onto whole visible words, conservatively rounded out."""
    result = []
    for index, span in enumerate(spans):
        start, end = int(span["start"]), int(span["end"])
        if start < 0 or end <= start or end > len(document["text"]):
            raise DocumentError("A detection has invalid text offsets. Re-analyze the document before export.")
        boxes = []
        for page in document["pages"]:
            if end <= page["start"] or start >= page["end"]:
                continue
            matches = [word for word in page["words"] if word["start"] < end and word["end"] > start]
            # Join adjacent words only within a line. Never bridge line breaks.
            grouped = []
            for word in matches:
                box = word["box"]
                if grouped:
                    last = grouped[-1]
                    overlap = min(last[3], box[3]) - max(last[1], box[1])
                    if overlap > 0.5 * min(last[3]-last[1], box[3]-box[1]) and 0 <= box[0]-last[2] < 0.04:
                        grouped[-1] = [min(last[0], box[0]), min(last[1], box[1]), max(last[2], box[2]), max(last[3], box[3])]
                        continue
                grouped.append(box[:])
            boxes.extend({"page": page["index"], "rect": _rect(box)} for box in grouped)
        if not boxes and document["text"][start:end].strip():
            raise DocumentError("A detection could not be located on the page. Re-analyze the document before export.")
        result.append({**span, "id": span.get("id", f"finding-{index+1}"),
                       "text": document["text"][start:end], "boxes": boxes})
    return result


def _paint(image: Image.Image, rectangles: list[list[float]]) -> Image.Image:
    clean = Image.new("RGB", image.size, "white")
    clean.paste(image.convert("RGB"))
    draw = ImageDraw.Draw(clean)
    width, height = clean.size
    for rectangle in rectangles:
        x0, y0, x1, y1 = _rect(rectangle)
        pad = max(2, round(height * (y1-y0) * 0.06))
        draw.rectangle((max(0, math.floor(x0*width)-pad), max(0, math.floor(y0*height)-pad),
                        min(width-1, math.ceil(x1*width)+pad), min(height-1, math.ceil(y1*height)+pad)),
                       fill=(0, 0, 0))
    return clean


def render_page(document: dict, page: int, boxes: list[list[float]] | None = None) -> bytes:
    if page < 0 or page >= len(document["pages"]):
        raise DocumentError("Page does not exist.")
    with Image.open(document["pages"][page]["preview_path"]) as original:
        clean = _paint(original, boxes or [])
        buffer = io.BytesIO()
        clean.save(buffer, format="PNG")
        return buffer.getvalue()


def export_redacted(document: dict, findings: list[dict], destination: str | Path) -> Path:
    """Create a new PDF containing only sanitized page images.

    No fonts, hidden text, annotations, form fields, attachments, EXIF, or source
    document metadata are carried across. Export never modifies the original.
    """
    rectangles = defaultdict(list)
    for finding in findings:
        if finding.get("enabled", True) is False or finding.get("selected", True) is False:
            continue
        for box in finding.get("boxes", []):
            page = int(box["page"])
            if not 0 <= page < len(document["pages"]):
                raise DocumentError("A redaction points to a page that does not exist.")
            rectangles[page].append(_rect(box["rect"]))
    output = fitz.open()
    try:
        for page_info in document["pages"]:
            encoded = render_page(document, page_info["index"], rectangles[page_info["index"]])
            page = output.new_page(width=page_info["width"], height=page_info["height"])
            page.insert_image(page.rect, stream=encoded)
        output.set_metadata({})
        output.del_xml_metadata()
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        output.save(target, garbage=4, deflate=True, clean=True, encryption=fitz.PDF_ENCRYPT_NONE)
        target.chmod(0o600)
        # Structural verification is cheap and makes regressions fail closed.
        with fitz.open(target) as verify:
            if verify.embfile_count() or any(page.get_text().strip() or list(page.annots() or []) or list(page.widgets() or []) for page in verify):
                target.unlink(missing_ok=True)
                raise DocumentError("Export verification failed. No output was released.")
        return target
    finally:
        output.close()


def synthetic_sample(destination: str | Path) -> Path:
    """Create a clearly synthetic clinical fixture; contains no real patient data."""
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    document = fitz.open()
    page = document.new_page(width=612, height=792)
    page.insert_text((54, 54), "NORTHLINE MEDICAL", fontsize=11, fontname="hebo", color=(0.12, 0.2, 0.2))
    page.insert_text((54, 76), "SYNTHETIC EXAMPLE - NOT A REAL PATIENT", fontsize=8, color=(0.4, 0.45, 0.45))
    page.draw_line((54, 94), (558, 94), color=(0.8, 0.84, 0.84), width=0.5)
    page.insert_text((54, 130), "Clinical consultation", fontsize=22, fontname="hebo", color=(0.12, 0.18, 0.18))
    lines = [
        "Patient: Eleanor Bennett", "Date of birth: March 14, 1978", "Medical record number: MRN-482913",
        "Health card: 4912-783-652", "Address: 184 Cedar Avenue, Toronto, ON M4B 1B3",
        "Phone: (416) 555-0198", "Email: eleanor.bennett@example.com", "Visit date: October 24, 2026",
        "Attending physician: Dr. Marcus Chen", "", "REASON FOR VISIT", "",
        "Follow-up for type 2 diabetes mellitus and essential hypertension.",
        "The patient reports improved exercise tolerance and no chest pain.", "", "ASSESSMENT", "",
        "Blood pressure 128/78 mmHg. Heart rate 72 bpm. HbA1c 6.8%.",
        "No evidence of peripheral neuropathy. Renal function remains stable.", "", "PLAN", "",
        "Continue metformin 500 mg twice daily and lisinopril 10 mg daily.",
        "Repeat HbA1c in three months. Encourage regular physical activity.", "",
        "Follow-up appointment: January 22, 2027", "Signed electronically by Dr. Marcus Chen",
    ]
    y = 169
    for line in lines:
        page.insert_text((54, y), line, fontsize=10, fontname="helv", color=(0.16, 0.2, 0.2))
        y += 18
    document.set_metadata({"title": "Synthetic clinical example"})
    document.save(target)
    document.close()
    return target
