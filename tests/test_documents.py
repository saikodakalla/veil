from pathlib import Path
import io
import zipfile

import fitz
from PIL import Image
import pytest

from backend.app import documents
from backend.app import ocr
from backend.app.documents import (
    DocumentError, export_redacted, ingest_document, map_spans,
    render_page, synthetic_sample,
)


def test_native_pdf_mapping_burns_pixels_and_discards_all_source_objects(tmp_path, monkeypatch):
    original = synthetic_sample(tmp_path / "example.pdf")
    with fitz.open(original) as source:
        page = source[0]
        page.add_text_annot((570, 120), "SECRET ANNOTATION ELEANOR")
        source.embfile_add("patient.txt", b"SECRET ATTACHMENT ELEANOR")
        source.save(tmp_path / "annotated.pdf")
    # Use ordinary PDF fixture for the native text path; annotation/attachment
    # source sanitization is independently checked below.
    document = ingest_document(original, tmp_path / "pages")
    name = "Eleanor Bennett"
    start = document["text"].index(name)
    findings = map_spans(document, [{"start": start, "end": start+len(name), "label": "NAME"}])
    assert findings[0]["boxes"]
    target = export_redacted(document, findings, tmp_path / "redacted.pdf")
    with fitz.open(target) as result:
        assert result.page_count == 1
        assert result[0].get_text() == ""
        assert not list(result[0].annots() or [])
        assert not list(result[0].widgets() or [])
        assert result.embfile_count() == 0
        assert all(not value for key, value in result.metadata.items() if key != "format")
        assert b"Eleanor" not in target.read_bytes()
        assert b"Synthetic clinical example" not in target.read_bytes()
        pix = result[0].get_pixmap(matrix=fitz.Matrix(2.5, 2.5))
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        for box in findings[0]["boxes"]:
            x0, y0, x1, y1 = box["rect"]
            # The whole interior is actually black, not a removable PDF overlay.
            crop = image.crop((int(x0*image.width)+2, int(y0*image.height)+2,
                               int(x1*image.width)-2, int(y1*image.height)-2))
            assert crop.getextrema() == ((0, 0), (0, 0), (0, 0))
    assert original.is_file()
    # Injected annotation/attachment cannot enter exports even when present.
    monkeypatch.setattr(documents, "recognize", lambda _: [
        {"text": "Clinical text", "box": [0.1, 0.1, 0.3, 0.2], "confidence": 1.0}])
    annotated = ingest_document(tmp_path / "annotated.pdf", tmp_path / "annotated-pages")
    sanitized = export_redacted(annotated, [], tmp_path / "sanitized.pdf")
    with fitz.open(sanitized) as result:
        assert result.embfile_count() == 0
        assert not list(result[0].annots() or [])


def test_text_reflow_preserves_names_across_pages(tmp_path):
    source = tmp_path / "note.txt"
    source.write_text("Patient: Eleanor Bennett\n" + "Normal clinical findings.\n" * 100)
    document = ingest_document(source, tmp_path / "pages")
    assert len(document["pages"]) >= 2
    start = document["text"].index("Eleanor")
    findings = map_spans(document, [{"start": start, "end": start+len("Eleanor Bennett"), "label": "NAME"}])
    assert findings[0]["boxes"][0]["page"] == 0
    target = export_redacted(document, findings, tmp_path / "redacted.pdf")
    with fitz.open(target) as result:
        assert len(result) == len(document["pages"])
        assert all(page.get_text() == "" for page in result)


def test_scanned_page_requires_ocr_even_with_a_text_layer(tmp_path, monkeypatch):
    image = Image.new("RGB", (400, 200), "white")
    from PIL import ImageDraw
    ImageDraw.Draw(image).text((20, 20), "SCANNED PATIENT NAME", fill="black")
    png = io.BytesIO()
    image.save(png, format="PNG")
    source = fitz.open()
    page = source.new_page()
    page.insert_text((50, 50), "Ordinary text layer")
    page.insert_image(fitz.Rect(50, 100, 450, 300), stream=png.getvalue())
    source.save(tmp_path / "mixed.pdf")
    source.close()
    calls = []
    def unavailable(path):
        calls.append(path)
        raise documents.OCRError("OCR unavailable")
    monkeypatch.setattr(documents, "recognize", unavailable)
    with pytest.raises(DocumentError, match="OCR unavailable"):
        ingest_document(tmp_path / "mixed.pdf", tmp_path / "pages")
    assert len(calls) == 1


def test_manual_redaction_pixels_and_invalid_geometry(tmp_path):
    source = synthetic_sample(tmp_path / "example.pdf")
    document = ingest_document(source, tmp_path / "pages")
    encoded = render_page(document, 0, [[0.1, 0.1, 0.9, 0.2]])
    with Image.open(io.BytesIO(encoded)) as image:
        assert image.getpixel((int(image.width*0.5), int(image.height*0.15))) == (0, 0, 0)
    with pytest.raises(DocumentError):
        export_redacted(document, [{"boxes": [{"page": 0, "rect": [0, 0, float("nan"), 1]}]}], tmp_path / "bad.pdf")
    with pytest.raises(DocumentError):
        map_spans(document, [{"start": 1, "end": 10_000_000}])


def test_text_only_docx_includes_headers_and_rejects_embedded_images(tmp_path):
    from docx import Document
    doc = Document()
    doc.add_paragraph("Patient: Eleanor Bennett")
    doc.sections[0].header.paragraphs[0].text = "MRN-482913"
    path = tmp_path / "patient.docx"
    doc.save(path)
    normalized = ingest_document(path, tmp_path / "pages")
    assert "Eleanor Bennett" in normalized["text"]
    assert "MRN-482913" in normalized["text"]
    image = tmp_path / "picture.png"
    Image.new("RGB", (100, 100), "white").save(image)
    doc.add_picture(str(image))
    doc.save(path)
    with pytest.raises(DocumentError, match="embedded images"):
        ingest_document(path, tmp_path / "pages2")


def test_rotated_pdf_box_maps_to_render(tmp_path):
    source = fitz.open()
    page = source.new_page(width=400, height=300)
    page.insert_text((50, 100), "Eleanor Bennett", fontsize=18)
    page.set_rotation(90)
    source.save(tmp_path / "rotated.pdf")
    source.close()
    document = ingest_document(tmp_path / "rotated.pdf", tmp_path / "pages")
    start = document["text"].index("Eleanor")
    findings = map_spans(document, [{"start": start, "end": start+len("Eleanor Bennett")}])
    target = export_redacted(document, findings, tmp_path / "redacted.pdf")
    image = Image.open(io.BytesIO(render_page(document, 0, [b["rect"] for b in findings[0]["boxes"]])))
    for box in findings[0]["boxes"]:
        x0, y0, x1, y1 = box["rect"]
        assert image.getpixel((int((x0+x1)*0.5*image.width), int((y0+y1)*0.5*image.height))) == (0, 0, 0)


def test_blank_page_is_valid_without_ocr(tmp_path, monkeypatch):
    source = fitz.open()
    source.new_page()
    source.save(tmp_path / "blank.pdf")
    source.close()
    def unexpected(path):
        raise AssertionError("Blank page should not require OCR")
    monkeypatch.setattr(documents, "recognize", unexpected)
    document = ingest_document(tmp_path / "blank.pdf", tmp_path / "pages")
    assert document["text"] == ""
    assert len(document["pages"]) == 1


def test_visible_annotation_forces_ocr_and_preserves_punctuation(tmp_path, monkeypatch):
    source = fitz.open()
    page = source.new_page()
    page.insert_text((40, 40), "Native text")
    page.add_freetext_annot(fitz.Rect(40, 80, 300, 130), "Patient: Eleanor Bennett")
    source.save(tmp_path / "annotation.pdf")
    source.close()
    calls = []
    def recognize(path):
        calls.append(path)
        return [
            {"text": "Email:", "box": [0.1, 0.1, 0.2, 0.15], "separator": "\n"},
            {"text": "eleanor@", "box": [0.21, 0.1, 0.4, 0.15], "separator": " "},
            {"text": "example.com", "box": [0.4, 0.1, 0.6, 0.15], "separator": ""},
        ]
    monkeypatch.setattr(documents, "recognize", recognize)
    document = ingest_document(tmp_path / "annotation.pdf", tmp_path / "pages")
    assert len(calls) == 1
    assert document["text"] == "Email: eleanor@example.com"
    assert document["pages"][0]["ocr"]


def test_mac_bridge_does_not_require_a_compiler(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(ocr.importlib.util, "find_spec", lambda _: object())
    expected = [{"text": "Patient", "box": [0.1, 0.1, 0.4, 0.2]}]
    monkeypatch.setattr(ocr, "_recognize_vision_bridge", lambda _: expected)
    def no_compiler():
        raise AssertionError("Native bridge must not compile Swift")
    monkeypatch.setattr(ocr, "prepare_ocr", no_compiler)
    assert ocr.recognize(tmp_path / "scan.png") == expected
