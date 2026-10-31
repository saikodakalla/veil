"""Clinical PHI detection by a local transformer plus high-recall patterns.

Returns offsets in the original, unmodified text; never asks an LLM to rewrite
medical content. Every span can be inspected and changed before export.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from .model_manager import manager

CATEGORIES = ("PATIENT", "CLINICIAN", "ID", "DATE", "AGE", "PHONE", "EMAIL", "LOCATION", "URL")
MODEL_LABELS = {
    "PATIENT": "PATIENT", "HCW": "CLINICIAN", "HOSPITAL": "LOCATION",
    "VENDOR": "LOCATION", "ID": "ID", "DATE": "DATE", "AGE": "AGE", "PHONE": "PHONE",
}

# Group 1 is the sensitive value when the pattern has a contextual prefix.
RULES = [
    ("EMAIL", r"(?<![\w.+-])[\w.!#$%&'*+/=?^`{|}~-]+[ \t]*@[ \t]*[\w-]+(?:[ \t]*\.[ \t]*[\w-]+)+", 0),
    ("URL", r"\b(?:https?://|www\.)[^\s<>\[\]{}]+", 0),
    ("ID", r"\b(?:MRN|medical\s+record(?:\s+(?:number|no\.?))?|patient\s*(?:id|number|no\.?)|health\s*(?:card|insurance)(?:\s*(?:number|no\.?))?|OHIP|CPSO|SSN|SIN|social\s+(?:security|insurance)(?:\s+number)?|NHS(?:\s*(?:number|no\.?))?|account\s*(?:number|no\.?)|accession(?:\s*(?:number|no\.?))?|member\s*(?:id|number)|(?:insurance|policy)\s*(?:id|number)|chart\s*(?:number|no\.?))\s*[:#=\-]?\s*([A-Z0-9][A-Z0-9\- ]{2,60}[A-Z0-9])(?=$|[\r\n,;.]|\s+[a-z])", 1),
    ("ID", r"\b\d{3}[ \t-]+\d{2}[ \t-]+\d{4}\b", 0),
    ("ID", r"\b\d{3}[ \t-]+\d{3}[ \t-]+\d{3}\b", 0),
    ("ID", r"\b\d{4}[ \t-]*\d{3}[ \t-]*\d{3}(?:[ \t-]*[A-Z]{2})?\b", 0),
    ("PHONE", r"(?<!\w)(?:\+\d{1,3}[ \t.-]*)?(?:\([ \t]*\d{3}[ \t]*\)|\d{3})[ \t.-]*\d{3}[ \t.-]*\d{4}(?:[ \t]*(?:ext\.?|x)[ \t]*\d{1,6})?(?!\w)", 0),
    ("DATE", r"\b(?:19|20)\d{2}[ \t]*[-/][ \t]*\d{1,2}[ \t]*[-/][ \t]*\d{1,2}\b", 0),
    ("DATE", r"\b\d{1,2}[ \t]*[/-][ \t]*\d{1,2}[ \t]*[/-][ \t]*(?:\d{4}|\d{2})\b", 0),
    ("DATE", r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+(?:19|20)\d{2})?\b", 0),
    ("DATE", r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+(?:19|20)\d{2}\b", 0),
    ("AGE", r"\b(\d{1,3})\s*[- ]?(?:years?[- ]old|year[- ]old|y/?o|yrs?\.?\s*old)\b", 1),
    ("AGE", r"\bage\s*[:=]?\s*(\d{1,3})\b", 1),
    ("LOCATION", r"\b\d{1,6}\s+[A-Z0-9][A-Z0-9.'\- ]{1,55}\s+(?:Street|St\.?|Avenue|Ave\.?|Road|Rd\.?|Boulevard|Blvd\.?|Drive|Dr\.?|Lane|Ln\.?|Court|Ct\.?|Way|Crescent|Cres\.?|Place|Pl\.?|Terrace|Trail)\b(?:\s*(?:,|#|Apt\.?|Unit|Suite)\s*[A-Z0-9-]+)?", 0),
    ("LOCATION", r"(?:^|\n)\s*(?:(?:Home|Mailing|Residential|Facility)\s+)?Address\s*[:=]\s*([^\r\n\t]+)", 1),
    ("LOCATION", r"\b[A-CEGHJ-NPR-TVXY]\d[A-CEGHJ-NPR-TV-Z][ -]?\d[A-CEGHJ-NPR-TV-Z]\d\b", 0),
    ("LOCATION", r"\b(?:ZIP|postal\s*code)\s*[:=]?\s*(\d{5}(?:-\d{4})?)\b", 1),
    ("PATIENT", r"(?:^|\n)\s*(?:Patient(?:\s+Name)?|Name)\s*[:=]\s*([^\r\n\t,;:]{2,70}?)(?=\s+(?:MRN|DOB|Age|Date|Phone|Address|Sex|Gender)\s*:|[\r\n\t,;:]|$)", 1),
    ("CLINICIAN", r"\b(?:Dr\.?|Doctor)\s+([A-Z][a-z]+(?:[-'][A-Z]?[a-z]+)?(?:\s+[A-Z]\.)?(?:\s+[A-Z][a-z]+(?:[-'][A-Z]?[a-z]+)?){0,2})", 1),
]
COMPILED_RULES = [(label, re.compile(pattern, re.I if label != "CLINICIAN" else 0), group) for label, pattern, group in RULES]


def _policy_categories(policy: dict | None) -> set[str]:
    if not policy or "categories" not in policy:
        return set(CATEGORIES)
    return set(policy["categories"]) & set(CATEGORIES)


def _span(text: str, start: int, end: int, label: str, score: float, source: str) -> dict:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return {
        "id": hashlib.sha256(f"{start}:{end}:{label}".encode()).hexdigest()[:16],
        "start": start, "end": end, "label": label, "text": text[start:end],
        "source": source, "score": round(float(score), 4),
    }


def rule_spans(text: str, policy: dict | None = None) -> list[dict]:
    """Exposed for unit tests; production detect() always also runs the model."""
    allowed = _policy_categories(policy)
    spans = []
    for label, pattern, group in COMPILED_RULES:
        if label not in allowed:
            continue
        for match in pattern.finditer(text):
            start, end = match.span(group)
            # Contextual ID regex may contain extra whitespace; stop at a double
            # space to avoid eating the next label in tabular clinical text.
            if label == "ID" and group:
                fragment = text[start:end]
                end = start + len(re.split(r"\s{2,}", fragment, maxsplit=1)[0])
            spans.append(_span(text, start, end, label, 1.0, "rules"))
    for raw_term in (policy or {}).get("custom_terms", []):
        term = str(raw_term).strip()
        if not term or len(term) > 500:
            continue
        for match in re.finditer(re.escape(term), text, re.I):
            spans.append(_span(text, match.start(), match.end(), "CUSTOM", 1.0, "custom"))
    return spans


def merge_spans(text: str, spans: list[dict]) -> list[dict]:
    """Union overlapping detections; preserve all covered characters."""
    merged: list[dict[str, Any]] = []
    for current in sorted(spans, key=lambda item: (item["start"], -item["end"])):
        if current["end"] <= current["start"]:
            continue
        if merged and (current["start"] < merged[-1]["end"] or (
            current["label"] == merged[-1]["label"]
            and current["start"] - merged[-1]["end"] <= 2
            and text[merged[-1]["end"]:current["start"]].isspace()
        )):
            previous = merged.pop()
            source = previous["source"] if previous["source"] == current["source"] else "model + rules"
            label = previous["label"]
            if current["label"] == "CUSTOM":
                label = "CUSTOM"
            merged.append(_span(text, previous["start"], max(previous["end"], current["end"]), label, max(previous["score"], current["score"]), source))
        else:
            merged.append(dict(current))
    return merged


def detect(text: str, policy: dict | None = None) -> list[dict]:
    """Analyze every token, including overlapping windows in long documents.

    Raises ModelUnavailable if the clinical model cannot run. There is no
    rules-only fallback masquerading as successful medical de-identification.
    """
    model_pipeline = manager.get_pipeline()
    if not text.strip():
        return []
    allowed = _policy_categories(policy)
    with manager.inference_lock:
        entities = model_pipeline(text)
    spans = rule_spans(text, policy)
    for entity in entities:
        label = MODEL_LABELS.get(entity.get("entity_group", entity.get("entity", "")))
        if label not in allowed:
            continue
        start, end = int(entity["start"]), int(entity["end"])
        # A model token may end in the middle of a WordPiece word. Redact the
        # whole word rather than leaving the identifying suffix visible.
        while start > 0 and text[start - 1].isalnum() and text[start].isalnum():
            start -= 1
        while end < len(text) and text[end - 1].isalnum() and text[end].isalnum():
            end += 1
        spans.append(_span(text, start, end, label, entity["score"], "model"))
    return merge_spans(text, spans)
