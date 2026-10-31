from pathlib import Path

import pytest

from backend.app import detection
from backend.app.model_manager import ModelManager, ModelUnavailable


def test_clinical_and_canadian_identifiers_have_exact_offsets():
    text = (
        "Patient: John Smith\nMRN: ABC123456\nDOB: 04/12/1974\nAge: 52\n"
        "Phone: (416) 555-0123\nHealth card: 1234-567-890-AB\n"
        "Email: john@example.com\nAddress: 42 Maple Street, Toronto, ON M5V 2T6\n"
        "Dr. Sarah Miller reviewed the patient."
    )
    spans = detection.merge_spans(text, detection.rule_spans(text))
    found = {span["text"] for span in spans}
    for expected in ["John Smith", "ABC123456", "04/12/1974", "52", "(416) 555-0123", "1234-567-890-AB", "john@example.com", "42 Maple Street, Toronto, ON M5V 2T6", "Sarah Miller"]:
        assert expected in found
    assert all(text[s["start"]:s["end"]] == s["text"] for s in spans)


def test_policy_and_custom_terms():
    text = "Patient: Alex Example\nSeen January 4, 2025 at Acme Research."
    spans = detection.rule_spans(text, {"categories": ["PATIENT"], "custom_terms": ["Acme Research"]})
    assert {span["label"] for span in spans} == {"PATIENT", "CUSTOM"}
    assert not any(span["label"] == "DATE" for span in spans)


def test_ocr_split_identifiers_still_match_original_offsets():
    text = "MRN- 482913\nHealth card: 4912- 783- 652\nPhone ( 416 ) 555- 0123\nDOB: 03/ 12/ 1976\nEmail: alex@ example.com"
    spans = detection.merge_spans(text, detection.rule_spans(text))
    found = {span["text"] for span in spans}
    assert {"482913", "4912- 783- 652", "( 416 ) 555- 0123", "03/ 12/ 1976", "alex@ example.com"} <= found


def test_merge_never_exposes_an_overlapping_character():
    text = "John Smith"
    spans = [detection._span(text, 0, 4, "PATIENT", .9, "model"), detection._span(text, 0, 10, "PATIENT", 1, "rules")]
    assert detection.merge_spans(text, spans)[0]["text"] == "John Smith"


def test_missing_model_fails_closed_without_network(tmp_path, monkeypatch):
    import urllib.request

    def forbidden(*args, **kwargs):
        raise AssertionError("Inference attempted a network request")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    local_manager = ModelManager(tmp_path)
    monkeypatch.setattr(detection, "manager", local_manager)
    with pytest.raises(ModelUnavailable):
        detection.detect("Patient: John Smith\nMRN: 12345678")
    assert local_manager.status()["state"] == "missing"


def test_model_rules_are_union_not_a_rewrite(monkeypatch):
    text = "Contact Alice Martin at alice@example.com."

    class StubManager:
        import threading
        inference_lock = threading.Lock()

        def get_pipeline(self):
            return lambda _: [{"entity_group": "PATIENT", "start": 8, "end": 20, "score": .97}]

    monkeypatch.setattr(detection, "manager", StubManager())
    spans = detection.detect(text)
    assert {span["text"] for span in spans} == {"Alice Martin", "alice@example.com"}


def test_long_model_input_is_passed_without_character_truncation(monkeypatch):
    text = ("Normal clinical findings. " * 2000) + "\nPatient: Last Person"
    received = []

    class StubManager:
        import threading
        inference_lock = threading.Lock()

        def get_pipeline(self):
            def pipe(value):
                received.append(value)
                return []
            return pipe

    monkeypatch.setattr(detection, "manager", StubManager())
    spans = detection.detect(text)
    assert received == [text]
    assert any(span["text"] == "Last Person" for span in spans)
