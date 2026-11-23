"""Masking helpers shared by the UI, services and audit writers.

These are prototype-level controls: they reduce accidental exposure in tables,
notes and the audit trail. They are not encryption or access control.
"""
import re

SENSITIVE_KEYS = {"pan", "gstin", "bank_account", "phone", "email", "address"}
SENSITIVE_FIELD_NAMES = {"PAN", "GSTIN", "BANK_ACCOUNT", "PHONE", "EMAIL", "ADDRESS",
                         "GSTIN_PAN_LINK"}

_GSTIN_TEXT = re.compile(r"\b[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]\b", re.I)
_PAN_TEXT = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b", re.I)
_EMAIL_TEXT = re.compile(r"\b([A-Z0-9._%+-]+)@([A-Z0-9.-]+\.[A-Z]{2,})\b", re.I)
# Six or more digits, optionally separated by single spaces or hyphens
# (account numbers, phone numbers, Aadhaar-like numbers).
_LONG_NUMBER_TEXT = re.compile(r"(?<![0-9])[0-9](?:[ -]?[0-9]){5,}(?![0-9])")


def mask(value):
    value = str(value or "")
    if not value:
        return ""
    return ("*" * max(0, len(value) - 4)) + value[-4:]


def mask_free_text(text):
    """Redact identifiers that a reviewer may type into a free-text note."""
    if text is None:
        return text
    text = str(text)
    text = _GSTIN_TEXT.sub(lambda m: mask(m.group(0)), text)
    text = _PAN_TEXT.sub(lambda m: mask(m.group(0)), text)
    text = _EMAIL_TEXT.sub(lambda m: "***@" + m.group(2), text)
    text = _LONG_NUMBER_TEXT.sub(lambda m: mask(re.sub(r"[ -]", "", m.group(0))), text)
    return text


def mask_record(record):
    """Mask identifiers while keeping names and IFSC readable for review."""
    safe = dict(record)
    field_norm = str(safe.get("field_name", "")).upper()
    for key, value in list(safe.items()):
        if not value:
            continue
        key_norm = str(key).lower()
        if key_norm in SENSITIVE_KEYS:
            safe[key] = mask(value)
        elif key_norm in {"field_value", "observed_value", "expected_value"} and field_norm in SENSITIVE_FIELD_NAMES:
            safe[key] = mask(value)
        elif key_norm in {"resolution_note", "review_note", "rationale", "details", "explanation"}:
            safe[key] = mask_free_text(value)
    return safe


def mask_records(records):
    return [mask_record(record) for record in records]
