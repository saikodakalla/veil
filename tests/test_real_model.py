"""Optional acceptance checks using the real clinical model and real local OCR.

Install the model in the app first, then run from the repository root:
    VEIL_TEST_MODEL=1 .venv/bin/python -m pytest tests/test_real_model.py -q

These tests never install/download a model, use a hosted inference service, or
mock the recognizer. All records are synthetic. The normal test suite skips them
so a new checkout can run its unit checks before downloading model weights.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import socket
import urllib.request

import fitz
from PIL import Image
import pytest

from backend.app.detection import detect
from backend.app.documents import export_redacted, ingest_document, map_spans, synthetic_sample
from backend.app.model_manager import manager
from backend.app.ocr import ocr_status, recognize

pytestmark = pytest.mark.skipif(
    os.environ.get("VEIL_TEST_MODEL") != "1",
    reason="Set VEIL_TEST_MODEL=1 after installing the local clinical model.",
)


@pytest.fixture(autouse=True)
def deny_python_network(monkeypatch):
    """Fail on every application-level network attempt, including lazy load."""
    def denied(*_args, **_kwargs):
        raise AssertionError("The local redaction acceptance test attempted network access.")

    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(urllib.request, "urlopen", denied)
    if manager.status()["state"] != "ready":
        pytest.fail("Install the clinical model in Veil before running VEIL_TEST_MODEL=1 checks.")
    if not ocr_status()["available"]:
        pytest.fail("Real local OCR is required: run Setup.command on Mac, or install Tesseract.")


def _assert_export_pixels_and_objects(output: Path, findings: list[dict], tmp_path: Path) -> str:
    """Inspect the fresh PDF and re-read its actual pixels with local OCR."""
    visible = []
    with fitz.open(output) as document:
        assert document.page_count > 0
        assert document.embfile_count() == 0
        assert document.get_xml_metadata() == ""
        assert all(not value for key, value in document.metadata.items() if key != "format")
        for page_index, page in enumerate(document):
            assert page.get_text() == ""
            assert not list(page.annots() or [])
            assert not list(page.widgets() or [])
            assert not page.get_links()
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), alpha=False)
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            for finding in findings:
                for box in finding["boxes"]:
                    if box["page"] != page_index:
                        continue
                    x0, y0, x1, y1 = box["rect"]
                    interior = image.crop((int(x0*image.width)+2, int(y0*image.height)+2,
                                           int(x1*image.width)-2, int(y1*image.height)-2))
                    assert interior.width > 0 and interior.height > 0
                    assert interior.getextrema() == ((0, 0), (0, 0), (0, 0))
            rendered = tmp_path / f"export-ocr-page-{page_index}.png"
            image.save(rendered)
            # Fresh OCR on the exported file; no original text, cached words, or
            # detection offsets are used to decide what remains readable.
            visible.extend(word["text"] for word in recognize(rendered))
    return " ".join(visible)


@pytest.mark.parametrize("input_kind", ["searchable_pdf", "scanned_image"])
def test_real_medical_record_is_redacted_and_clinical_content_survives(tmp_path, input_kind):
    original = synthetic_sample(tmp_path / "synthetic.pdf")
    source = original
    if input_kind == "scanned_image":
        source = tmp_path / "scanned.png"
        with fitz.open(original) as document:
            document[0].get_pixmap(matrix=fitz.Matrix(2.5, 2.5), alpha=False).save(source)
    normalized = ingest_document(source, tmp_path / "pages")
    assert bool(normalized["pages"][0]["ocr"]) == (input_kind == "scanned_image")
    spans = detect(normalized["text"])
    assert {"PATIENT", "ID", "DATE", "CLINICIAN", "EMAIL", "PHONE", "LOCATION"} <= {span["label"] for span in spans}
    # The transformer itself participated; these are not rules-only exports.
    assert any("model" in span["source"] for span in spans)
    findings = map_spans(normalized, spans)
    assert findings and all(finding["boxes"] for finding in findings)
    exported = export_redacted(normalized, findings, tmp_path / "redacted.pdf")
    observed = _assert_export_pixels_and_objects(exported, findings, tmp_path)
    letters_and_digits = re.sub(r"[^a-z0-9]", "", observed.lower())
    for identifier in ("eleanor", "bennett", "marcus", "chen", "482913", "4912783652", "4165550198", "cedar", "toronto", "m4b1b3", "examplecom", "1978", "2026", "2027", "march14", "october24", "january22"):
        assert identifier not in letters_and_digits, f"Synthetic identifier remained readable after {input_kind} export: {identifier}"
    for clinical_term in ("metformin", "lisinopril", "hypertension", "diabetes"):
        assert clinical_term in letters_and_digits, f"Clinical content was lost after {input_kind} export: {clinical_term}"
    assert original.is_file()  # Export leaves the source intact.


def test_actual_transformer_sees_entities_beyond_first_window():
    prefix = "No acute cardiopulmonary abnormality. " * 400
    text = prefix + "\nPROCEDURE: Chest xray. COMPARISON: last seen on 1/1/2020. Findings discussed with Dr. Beatrice Williams."
    # Call the actual transformer directly to exclude the rule layer from this
    # acceptance condition. Both entities occur far beyond its 512-token window.
    with manager.inference_lock:
        entities = manager.get_pipeline()(text)
    tail = [entity for entity in entities if int(entity["start"]) >= len(prefix)]
    assert any(entity["entity_group"] == "HCW" and "Williams" in text[int(entity["start"]):int(entity["end"])] for entity in tail)
    assert any(entity["entity_group"] == "DATE" and "2020" in text[int(entity["start"]):int(entity["end"])] for entity in tail)
