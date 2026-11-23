"""VendorTrust rules engine.

Extracts fields from text-based PDFs, compares them with the onboarding form,
and returns explainable findings plus an engine recommendation that maps to the
problem statement's Approved / Pending / Rejected outcomes. A human reviewer
always signs off; the engine only recommends.
"""
import re

try:  # PyMuPDF is only needed for PDF I/O, not for rule evaluation.
    import pymupdf as fitz
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz

# --------------------------------------------------------------------------
# Identifier formats and validators
# --------------------------------------------------------------------------
PAN_RE = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
GSTIN_RE = re.compile(r"\b[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b")
IFSC_RE = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")
BANK_ACCOUNT_FORMAT_RE = re.compile(r"[0-9]{9,18}")

# 4th character of a PAN identifies the holder type.
PAN_ENTITY_TYPES = {
    "P": "Individual / Proprietor", "C": "Company", "H": "Hindu Undivided Family",
    "F": "Firm / LLP", "A": "Association of Persons", "T": "Trust",
    "B": "Body of Individuals", "L": "Local Authority",
    "J": "Artificial Juridical Person", "G": "Government",
}

_GSTIN_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def gstin_check_char(first14):
    """Return the expected 15th (checksum) character of a GSTIN."""
    total = 0
    for index, char in enumerate(first14.upper()):
        product = _GSTIN_CHARS.index(char) * (2 if index % 2 else 1)
        total += product // 36 + product % 36
    return _GSTIN_CHARS[(36 - total % 36) % 36]


def gstin_checksum_ok(gstin):
    gstin = str(gstin or "").strip().upper()
    return bool(GSTIN_RE.fullmatch(gstin)) and gstin_check_char(gstin[:14]) == gstin[14]


def pan_entity_type(pan):
    pan = str(pan or "").strip().upper()
    if not PAN_RE.fullmatch(pan):
        return None
    return PAN_ENTITY_TYPES.get(pan[3])


def digits_only(value):
    return re.sub(r"[^0-9]", "", str(value or ""))


# GST state codes -> state name and address keywords (state name, common
# aliases and major cities). Used to compare the GSTIN state with an address.
GST_STATES = {
    "01": ("Jammu and Kashmir", ["JAMMU AND KASHMIR", "JAMMU & KASHMIR", "SRINAGAR", "JAMMU"]),
    "02": ("Himachal Pradesh", ["HIMACHAL PRADESH", "SHIMLA"]),
    "03": ("Punjab", ["PUNJAB", "LUDHIANA", "AMRITSAR", "JALANDHAR", "MOHALI"]),
    "04": ("Chandigarh", ["CHANDIGARH"]),
    "05": ("Uttarakhand", ["UTTARAKHAND", "DEHRADUN", "ROORKEE", "HARIDWAR", "HALDWANI"]),
    "06": ("Haryana", ["HARYANA", "GURUGRAM", "GURGAON", "FARIDABAD", "PANIPAT"]),
    "07": ("Delhi", ["DELHI", "NEW DELHI"]),
    "08": ("Rajasthan", ["RAJASTHAN", "JAIPUR", "JODHPUR", "UDAIPUR"]),
    "09": ("Uttar Pradesh", ["UTTAR PRADESH", "LUCKNOW", "NOIDA", "KANPUR", "GHAZIABAD", "VARANASI"]),
    "10": ("Bihar", ["BIHAR", "PATNA"]),
    "11": ("Sikkim", ["SIKKIM", "GANGTOK"]),
    "12": ("Arunachal Pradesh", ["ARUNACHAL PRADESH", "ITANAGAR"]),
    "13": ("Nagaland", ["NAGALAND", "KOHIMA"]),
    "14": ("Manipur", ["MANIPUR", "IMPHAL"]),
    "15": ("Mizoram", ["MIZORAM", "AIZAWL"]),
    "16": ("Tripura", ["TRIPURA", "AGARTALA"]),
    "17": ("Meghalaya", ["MEGHALAYA", "SHILLONG"]),
    "18": ("Assam", ["ASSAM", "GUWAHATI"]),
    "19": ("West Bengal", ["WEST BENGAL", "KOLKATA"]),
    "20": ("Jharkhand", ["JHARKHAND", "RANCHI", "JAMSHEDPUR"]),
    "21": ("Odisha", ["ODISHA", "ORISSA", "BHUBANESWAR"]),
    "22": ("Chhattisgarh", ["CHHATTISGARH", "RAIPUR"]),
    "23": ("Madhya Pradesh", ["MADHYA PRADESH", "BHOPAL", "INDORE"]),
    "24": ("Gujarat", ["GUJARAT", "AHMEDABAD", "SURAT", "VADODARA", "RAJKOT"]),
    "25": ("Dadra and Nagar Haveli and Daman and Diu", ["DAMAN", "DIU"]),
    "26": ("Dadra and Nagar Haveli and Daman and Diu", ["DADRA", "NAGAR HAVELI", "SILVASSA", "DAMAN", "DIU"]),
    "27": ("Maharashtra", ["MAHARASHTRA", "MUMBAI", "PUNE", "NAGPUR", "THANE", "NASHIK"]),
    "28": ("Andhra Pradesh", ["ANDHRA PRADESH"]),
    "29": ("Karnataka", ["KARNATAKA", "BENGALURU", "BANGALORE", "MYSURU", "MYSORE", "MANGALURU"]),
    "30": ("Goa", ["GOA", "PANAJI"]),
    "31": ("Lakshadweep", ["LAKSHADWEEP"]),
    "32": ("Kerala", ["KERALA", "KOCHI", "THIRUVANANTHAPURAM"]),
    "33": ("Tamil Nadu", ["TAMIL NADU", "CHENNAI", "COIMBATORE", "MADURAI"]),
    "34": ("Puducherry", ["PUDUCHERRY", "PONDICHERRY"]),
    "35": ("Andaman and Nicobar Islands", ["ANDAMAN", "NICOBAR", "PORT BLAIR"]),
    "36": ("Telangana", ["TELANGANA", "HYDERABAD", "SECUNDERABAD"]),
    "37": ("Andhra Pradesh", ["ANDHRA PRADESH", "VISAKHAPATNAM", "VIJAYAWADA", "AMARAVATI"]),
    "38": ("Ladakh", ["LADAKH", "LEH"]),
}


def states_in_address(address):
    """Return the set of state names whose keywords appear in an address."""
    text = " " + re.sub(r"[^A-Z&]+", " ", str(address or "").upper()) + " "
    found = set()
    for state_name, keywords in GST_STATES.values():
        for keyword in keywords:
            if f" {keyword} " in text:
                found.add(state_name)
                break
    return found


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------
_SEP = r"[ \t_-]*"
# Account number: label, optional separator, value on the same or next line.
# Spaces inside the number are allowed and stripped; 9-18 digits are required.
ACCOUNT_RE = re.compile(
    r"(?im)^[ \t]*(?:(?:SAVINGS|CURRENT|SB|CA)[ \t]+)?"
    r"(?:BANK" + _SEP + r"ACCOUNT(?:" + _SEP + r"(?:NUMBER|NO\.?|#))?"
    r"|ACCOUNT" + _SEP + r"(?:NUMBER|NO\.?|#)"
    r"|A/C" + r"[ \t]*(?:NUMBER|NO\.?|#)?)"
    r"[ \t]*[:\-#.]?[ \t]*(?:\n[ \t]*)?([0-9][0-9 ]{7,24}[0-9])[ \t]*$"
)

_LABELS = {
    "LEGAL_NAME": r"LEGAL" + _SEP + r"NAME(?:" + _SEP + r"OF" + _SEP + r"(?:THE" + _SEP + r")?BUSINESS)?"
                  r"|NAME" + _SEP + r"OF" + _SEP + r"(?:THE" + _SEP + r")?BUSINESS"
                  r"|BUSINESS" + _SEP + r"LEGAL" + _SEP + r"NAME",
    "TRADE_NAME": r"TRADE" + _SEP + r"NAME(?:" + _SEP + r"(?:IF" + _SEP + r"ANY))?",
    "ACCOUNT_HOLDER": r"ACCOUNT" + _SEP + r"HOLDER(?:" + _SEP + r"NAME)?"
                      r"|NAME" + _SEP + r"OF" + _SEP + r"(?:THE" + _SEP + r")?ACCOUNT" + _SEP + r"HOLDER"
                      r"|ACCOUNT" + _SEP + r"NAME",
    "NAME": r"NAME",
    "ADDRESS": r"REGISTERED" + _SEP + r"ADDRESS|BUSINESS" + _SEP + r"ADDRESS"
               r"|ADDRESS" + _SEP + r"OF" + _SEP + r"PRINCIPAL" + _SEP + r"PLACE" + _SEP + r"OF" + _SEP + r"BUSINESS"
               r"|PRINCIPAL" + _SEP + r"PLACE" + _SEP + r"OF" + _SEP + r"BUSINESS|ADDRESS",
}


def _label_value_re(label_pattern):
    # Either "Label: value", "Label:\nvalue", "Label - value" or "LABEL\nvalue".
    # The value may not contain ':' so that an empty label followed by another
    # "Label: value" line is never captured as this label's value.
    return re.compile(
        r"(?im)^[ \t]*(?:" + label_pattern + r")[ \t]*"
        r"(?:[:\-][ \t]*(?:\n[ \t]*)?|\n[ \t]*)"
        r"([^\n:]*[^\n:\s])[ \t]*$"
    )


LABEL_VALUE_RES = {name: _label_value_re(pattern) for name, pattern in _LABELS.items()}
# Back-compatible names used elsewhere.
LEGAL_NAME_RE = LABEL_VALUE_RES["LEGAL_NAME"]
ACCOUNT_HOLDER_RE = LABEL_VALUE_RES["ACCOUNT_HOLDER"]
NAME_RE = LABEL_VALUE_RES["NAME"]
ADDRESS_RE = LABEL_VALUE_RES["ADDRESS"]

_KNOWN_LABELS = "|".join(f"(?:{p})" for p in _LABELS.values()) + (
    r"|PAN|GSTIN|IFSC|BANK" + _SEP + r"ACCOUNT|BANK" + _SEP + r"NAME|ACCOUNT" + _SEP + r"TYPE"
    r"|ENTITY" + _SEP + r"TYPE|FIELD|VALUE|DATE.*|STATE|PIN.*"
)
_LABEL_ONLY_RE = re.compile(r"(?i)^(?:" + _KNOWN_LABELS + r")$")


def _looks_like_label(value):
    value = value.strip()
    return bool(_LABEL_ONLY_RE.fullmatch(value)) or bool(re.fullmatch(r"[A-Z]+(?:_[A-Z]+)+", value))


class DocumentError(ValueError):
    pass


def validate_pdf(data: bytes, filename: str, max_mb: int = 20):
    if not filename.lower().endswith(".pdf"):
        raise DocumentError("Only PDF files are accepted in vendor onboarding.")
    if not data.startswith(b"%PDF-"):
        raise DocumentError("The uploaded file is not a valid PDF signature.")
    if len(data) > max_mb * 1024 * 1024:
        raise DocumentError(f"File exceeds the {max_mb} MB upload limit.")
    try:
        with fitz.open(stream=data, filetype="pdf") as pdf:
            if pdf.page_count == 0:
                raise DocumentError("The PDF contains no pages.")
            if pdf.page_count > 100:
                raise DocumentError("The PDF exceeds the 100-page limit.")
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError("The PDF is corrupt, encrypted or unreadable.") from exc


def extract_pdf(data: bytes):
    pages = []
    try:
        with fitz.open(stream=data, filetype="pdf") as pdf:
            for page_number, page in enumerate(pdf, start=1):
                pages.append((page_number, page.get_text("text") or ""))
    except Exception as exc:
        raise DocumentError("Could not read PDF content.") from exc
    full_text = "\n".join(text for _, text in pages).strip()
    if len(re.sub(r"\s+", "", full_text)) < 10:
        return "", [], True
    return full_text, pages, False


def extract_fields_from_pages(pages):
    """Extract identifiers and labelled values from text-based PDF pages."""
    results = []
    patterns = {"PAN": PAN_RE, "GSTIN": GSTIN_RE, "IFSC": IFSC_RE}

    def add(field, value, page_number, confidence):
        results.append({"field_name": field, "field_value": value, "page_number": page_number,
                        "confidence": confidence, "source": "PDF text extraction"})

    for page_number, text in pages:
        upper = text.upper()
        for field, pattern in patterns.items():
            for match in pattern.finditer(upper):
                add(field, match.group(0), page_number, 0.95)

        for match in ACCOUNT_RE.finditer(text):
            number = digits_only(match.group(1))
            if BANK_ACCOUNT_FORMAT_RE.fullmatch(number):
                add("BANK_ACCOUNT", number, page_number, 0.90)

        confidences = {"LEGAL_NAME": 0.90, "TRADE_NAME": 0.85, "ACCOUNT_HOLDER": 0.90,
                       "NAME": 0.80, "ADDRESS": 0.85}
        for field, regex in LABEL_VALUE_RES.items():
            for match in regex.finditer(text):
                value = match.group(1).strip().strip(" ,;")
                if value and not _looks_like_label(value):
                    add(field, value, page_number, confidences[field])

    unique = {}
    for item in results:
        unique[(item["field_name"], item["field_value"], item["page_number"])] = item
    return list(unique.values())


# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------
def normalize(value):
    """Normalise legal names and identifiers for comparison.

    Handles M/s, Messrs and a leading "The", '&' vs 'AND', and common
    company suffix abbreviations. No fuzzy spelling correction is attempted.
    """
    text = str(value or "").upper().strip()
    text = text.replace("&", " AND ")
    text = re.sub(r"^\s*(?:M\s*/\s*S\.?|MESSRS\.?)\s+", "", text)
    text = re.sub(r"^\s*THE\s+", "", text)
    text = re.sub(r"\bPVT\b", "PRIVATE", text)
    text = re.sub(r"\bLTD\b", "LIMITED", text)
    text = re.sub(r"\bCO\b", "COMPANY", text)
    text = re.sub(r"\bCORP\b", "CORPORATION", text)
    return re.sub(r"[^A-Z0-9]", "", text)


# --------------------------------------------------------------------------
# Rule catalogue
# --------------------------------------------------------------------------
# Findings that can never be overridden by a reviewer note. The vendor must
# resubmit, or the case is rejected.
UNRESOLVABLE_RULES = {
    "FORM_DOCUMENT_MISMATCH_BANK_ACCOUNT", "FORM_DOCUMENT_MISMATCH_PAN",
    "FORM_DOCUMENT_MISMATCH_GSTIN", "FORM_DOCUMENT_MISMATCH_IFSC",
    "FIELD_NOT_FOUND_BANK_ACCOUNT", "FIELD_NOT_FOUND_IFSC",
    "GSTIN_PAN_LINK", "CROSS_DOCUMENT_CONFLICT_PAN", "CROSS_DOCUMENT_CONFLICT_BANK_ACCOUNT",
    "BANK_ACCOUNT_SHARED_WITH_OTHER_PAN", "DOCUMENT_REUSED_ACROSS_VENDORS",
    "DOC_TYPE_CONTENT_MISMATCH", "DUPLICATE_DOCUMENT_IN_SUBMISSION", "DOC_REQUIRED",
    "INVALID_FORMAT_PAN", "INVALID_FORMAT_GSTIN", "INVALID_FORMAT_IFSC",
    "INVALID_FORMAT_BANK_ACCOUNT", "INVALID_PAN_HOLDER_TYPE", "INVALID_CHECKSUM_GSTIN",
    "FORM_REQUIRED_PAN", "FORM_REQUIRED_GSTIN", "FORM_REQUIRED_IFSC", "FORM_REQUIRED_BANK_ACCOUNT",
}

# Hard failures: the engine recommends rejection.
REJECT_RULES = {
    "GSTIN_PAN_LINK", "CROSS_DOCUMENT_CONFLICT_PAN",
    "BANK_ACCOUNT_SHARED_WITH_OTHER_PAN", "DOCUMENT_REUSED_ACROSS_VENDORS",
}

# Bank-change controls: internal callback, 30+ char note, two-person approval.
BANK_CHANGE_RULES = {"BANK_DETAILS_CHANGED", "BANK_AND_CONTACT_CHANGED"}

NEEDS_DOCUMENT_STATUSES = {"Missing", "Not Found", "Invalid"}
NEEDS_DOCUMENT_RULES = {
    "DOC_TYPE_CONTENT_MISMATCH", "DUPLICATE_DOCUMENT_IN_SUBMISSION",
    "FORM_DOCUMENT_MISMATCH_BANK_ACCOUNT", "FORM_DOCUMENT_MISMATCH_PAN",
    "FORM_DOCUMENT_MISMATCH_GSTIN", "FORM_DOCUMENT_MISMATCH_IFSC",
    "CROSS_DOCUMENT_CONFLICT_BANK_ACCOUNT",
}
# Any unresolved finding with one of these statuses blocks approval, whatever its severity.
BLOCKING_STATUSES = {"Missing", "Not Found", "Invalid", "Needs Manual Review", "Mismatch"}
RESOLVED_STATUSES = {"resolved", "closed"}

OUTCOME_REJECT = "Recommend Reject"
OUTCOME_NEEDS_DOCUMENTS = "Needs Documents"
OUTCOME_REVIEW = "Pending Review"
OUTCOME_APPROVE_READY = "Approve-ready"

RECOMMENDATION_LABELS = {
    OUTCOME_REJECT: ("Rejected", "Recommend reject"),
    OUTCOME_NEEDS_DOCUMENTS: ("Pending", "Pending: needs documents or correction"),
    OUTCOME_REVIEW: ("Pending", "Pending: needs human review"),
    OUTCOME_APPROVE_READY: ("Approved", "Approve-ready (human sign-off required)"),
}


def is_resolved(finding):
    return str(finding.get("finding_status", "")).lower() in RESOLVED_STATUSES


def is_resolvable(finding):
    return finding.get("rule_id") not in UNRESOLVABLE_RULES


def recommend(findings):
    """Map findings to an engine recommendation.

    Returns a dict with machine_status, outcome (Approved / Pending / Rejected,
    as in the problem statement), a readable label and the reasons.
    Resolved findings are ignored, so the recommendation updates as a reviewer
    works through the case.
    """
    open_findings = [f for f in findings if not is_resolved(f)]
    reject = [f for f in findings if f.get("rule_id") in REJECT_RULES]
    needs_docs = [f for f in open_findings
                  if f.get("finding_status") in NEEDS_DOCUMENT_STATUSES
                  or f.get("rule_id") in NEEDS_DOCUMENT_RULES]
    review = [f for f in open_findings if f.get("severity") in ("High", "Medium")
              or f.get("finding_status") in BLOCKING_STATUSES]
    if reject:
        status, decisive = OUTCOME_REJECT, reject
    elif needs_docs:
        status, decisive = OUTCOME_NEEDS_DOCUMENTS, needs_docs
    elif review:
        status, decisive = OUTCOME_REVIEW, review
    else:
        status, decisive = OUTCOME_APPROVE_READY, []
    outcome, label = RECOMMENDATION_LABELS[status]
    reasons = []
    for finding in decisive:
        reason = f"{finding.get('rule_id')}: {finding.get('explanation')}"
        if reason not in reasons:
            reasons.append(reason)
    if not reasons:
        reasons = ["All format, cross-document and fraud checks passed. External PAN/GST/bank "
                   "verification is not connected, so a human must still sign off."]
    return {"machine_status": status, "outcome": outcome, "label": label, "reasons": reasons}


def _add_finding(findings, field_name, observed, expected, rule_id, severity,
                 status, explanation, recommended_action, source_document=None,
                 source_page=None, confidence=None):
    findings.append({
        "field_name": field_name, "observed_value": observed, "expected_value": expected,
        "rule_id": rule_id, "severity": severity, "finding_status": status,
        "explanation": explanation, "source_document": source_document,
        "source_page": source_page, "confidence": confidence,
        "recommended_action": recommended_action,
    })


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------
KEY_FIELDS = {"PAN Card": {"PAN"}, "GST Certificate": {"GSTIN"},
              "Bank Proof": {"BANK_ACCOUNT", "IFSC"}}
# Which document types are trusted sources for each field.
FIELD_SOURCES = {
    "PAN": {"PAN Card", "GST Certificate"},
    "GSTIN": {"GST Certificate"},
    "IFSC": {"Bank Proof"},
    "BANK_ACCOUNT": {"Bank Proof"},
    "ACCOUNT_HOLDER": {"Bank Proof"},
    "LEGAL_NAME": {"GST Certificate"},
    "TRADE_NAME": {"GST Certificate"},
    "ADDRESS": {"GST Certificate"},
}
_FIELD_LABELS = {"PAN": "PAN", "GSTIN": "GSTIN", "IFSC": "IFSC", "BANK_ACCOUNT": "bank account number"}


def evaluate(profile, documents, existing_bank_account=None, existing_contact=None,
             shared_bank_vendors=None, reused_document_vendors=None):
    """Evaluate one submission.

    profile: form values (legal_name, pan, gstin, email, phone, address, bank_account, ifsc).
    documents: [{doc_type, filename, fields, needs_manual_review, sha256?}].
    existing_bank_account / existing_contact: the APPROVED vendor master values,
        or None for a first-time / not-yet-approved vendor.
    shared_bank_vendors: vendor codes with a different PAN that use the same account.
    reused_document_vendors: vendor codes with a different PAN that submitted an
        identical document file (same sha256).
    """
    findings, all_fields = [], []
    profile = dict(profile or {})
    required_types = set(KEY_FIELDS)
    supplied_types = {doc["doc_type"] for doc in documents}
    missing_docs = sorted(required_types - supplied_types)

    # ---- Per-document checks -------------------------------------------------
    hashes = {}
    for doc in documents:
        tagged = [dict(item, doc_type=doc["doc_type"], filename=doc["filename"])
                  for item in doc.get("fields", [])]
        all_fields.extend(tagged)
        digest = doc.get("sha256")
        if digest:
            hashes.setdefault(digest, []).append(doc)
        if doc.get("needs_manual_review"):
            _add_finding(findings, "DOCUMENT_READABILITY", doc["filename"], "Readable text-based PDF",
                         "DOC_SCAN_REVIEW", "Medium", "Needs Manual Review",
                         f"No extractable text was found in the {doc['doc_type']}. It may be a scanned PDF; OCR is not enabled.",
                         "Request a text-searchable PDF or review the document manually.", doc["filename"])
            continue
        found = {item["field_name"] for item in doc.get("fields", [])}
        expected_keys = KEY_FIELDS.get(doc["doc_type"], set())
        missing_keys = expected_keys - found
        looks_like_gst = doc["doc_type"] == "PAN Card" and "GSTIN" in found
        if missing_keys or looks_like_gst:
            detail = (f"it does not contain {', '.join(sorted(missing_keys))}" if missing_keys
                      else "it contains a GSTIN, which suggests a GST certificate was uploaded")
            _add_finding(findings, "DOCUMENT_TYPE", doc["filename"], doc["doc_type"],
                         "DOC_TYPE_CONTENT_MISMATCH", "High", "Mismatch",
                         f"The file uploaded as {doc['doc_type']} does not look like one: {detail}.",
                         f"Upload the correct {doc['doc_type']}.", doc["filename"])

    for digest, docs in hashes.items():
        if len(docs) > 1:
            _add_finding(findings, "DOCUMENT_TYPE", ", ".join(d["filename"] for d in docs),
                         "A different file for each document type", "DUPLICATE_DOCUMENT_IN_SUBMISSION",
                         "High", "Mismatch",
                         "The same file was uploaded for " + ", ".join(d["doc_type"] for d in docs) + ".",
                         "Upload a separate, correct document for each slot.")

    if missing_docs:
        _add_finding(findings, "REQUIRED_DOCUMENTS", ", ".join(sorted(supplied_types)) or "None",
                     ", ".join(sorted(required_types)), "DOC_REQUIRED", "High", "Missing",
                     "One or more mandatory supporting documents were not submitted: " + ", ".join(missing_docs) + ".",
                     "Upload the missing PAN Card, GST Registration Certificate and/or Bank Proof.")

    def from_sources(field):
        allowed = FIELD_SOURCES.get(field)
        return [f for f in all_fields if f["field_name"] == field
                and (allowed is None or f["doc_type"] in allowed)]

    def doc_of(doc_type):
        return next((d["filename"] for d in documents if d["doc_type"] == doc_type), None)

    # ---- Form format checks --------------------------------------------------
    form = {
        "PAN": str(profile.get("pan") or "").strip().upper(),
        "GSTIN": str(profile.get("gstin") or "").strip().upper(),
        "IFSC": str(profile.get("ifsc") or "").strip().upper(),
        "BANK_ACCOUNT": digits_only(profile.get("bank_account")) if str(profile.get("bank_account") or "").strip() else "",
    }
    raw_account = str(profile.get("bank_account") or "").strip()
    format_rules = {"PAN": PAN_RE, "GSTIN": GSTIN_RE, "IFSC": IFSC_RE}
    for label, pattern in format_rules.items():
        if form[label] and not pattern.fullmatch(form[label]):
            _add_finding(findings, label, form[label], f"Valid {label} format", f"INVALID_FORMAT_{label}",
                         "High", "Invalid", f"The {label} entered in the form does not match the expected format.",
                         "Correct the value in the form and resubmit.")
    if raw_account and (re.search(r"[^0-9 \-]", raw_account) or not BANK_ACCOUNT_FORMAT_RE.fullmatch(form["BANK_ACCOUNT"])):
        _add_finding(findings, "BANK_ACCOUNT", raw_account, "9 to 18 digits", "INVALID_FORMAT_BANK_ACCOUNT",
                     "High", "Invalid", "The bank account number entered in the form must contain 9 to 18 digits.",
                     "Correct the bank account number and resubmit.")
    entity_type = pan_entity_type(form["PAN"])
    if PAN_RE.fullmatch(form["PAN"]) and entity_type is None:
        _add_finding(findings, "PAN", form["PAN"], "4th character one of " + "".join(PAN_ENTITY_TYPES),
                     "INVALID_PAN_HOLDER_TYPE", "High", "Invalid",
                     f"The 4th character of the PAN ('{form['PAN'][3]}') is not a valid holder type.",
                     "Check the PAN for typing errors and resubmit.")
    if GSTIN_RE.fullmatch(form["GSTIN"]) and not gstin_checksum_ok(form["GSTIN"]):
        _add_finding(findings, "GSTIN", form["GSTIN"], "Valid GSTIN check digit", "INVALID_CHECKSUM_GSTIN",
                     "High", "Invalid",
                     "The GSTIN check digit is wrong, so the number is mistyped or not a real GSTIN.",
                     "Check the GSTIN for typing errors and resubmit.")
    if GSTIN_RE.fullmatch(form["GSTIN"]) and form["GSTIN"][:2] not in GST_STATES:
        _add_finding(findings, "GSTIN", form["GSTIN"], "Valid GST state code", "INVALID_FORMAT_GSTIN",
                     "High", "Invalid", "The first two digits of the GSTIN are not a valid GST state code.",
                     "Check the GSTIN for typing errors and resubmit.")

    # ---- Form vs document identifiers ----------------------------------------
    for field in ("PAN", "GSTIN", "IFSC", "BANK_ACCOUNT"):
        expected = form[field]
        values = from_sources(field)
        if not expected:
            _add_finding(findings, field, ", ".join(sorted({x["field_value"] for x in values})) or "Not provided",
                         "Required form value", f"FORM_REQUIRED_{field}", "High", "Missing",
                         f"The {_FIELD_LABELS[field]} was not provided in the onboarding form.",
                         f"Provide the vendor's {_FIELD_LABELS[field]}.")
        elif values:
            for item in values:
                if normalize(item["field_value"]) != normalize(expected):
                    _add_finding(findings, field, item["field_value"], expected,
                                 f"FORM_DOCUMENT_MISMATCH_{field}", "High", "Mismatch",
                                 f"The {_FIELD_LABELS[field]} in the {item['doc_type']} does not match the value entered in the form.",
                                 "Ask the vendor to correct the form or upload the correct document.",
                                 item["filename"], item.get("page_number"), item.get("confidence"))
        else:
            source_types = " or ".join(sorted(FIELD_SOURCES[field]))
            _add_finding(findings, field, "Not found in submitted documents", expected,
                         f"FIELD_NOT_FOUND_{field}", "High", "Not Found",
                         f"No {_FIELD_LABELS[field]} could be read from the {source_types}.",
                         "Request a clear, text-searchable document that shows this value.")

    # ---- Conflicting values inside the submitted documents -------------------
    for field in ("PAN", "GSTIN", "IFSC", "BANK_ACCOUNT"):
        values = from_sources(field)
        if len({normalize(item["field_value"]) for item in values}) > 1:
            _add_finding(findings, field, ", ".join(sorted({x["field_value"] for x in values})),
                         "One consistent value across documents", f"CROSS_DOCUMENT_CONFLICT_{field}",
                         "High", "Mismatch",
                         f"Different {_FIELD_LABELS[field]} values appear in the submitted documents.",
                         "Do not proceed; investigate which document is genuine.")

    if GSTIN_RE.fullmatch(form["GSTIN"]) and PAN_RE.fullmatch(form["PAN"]) and form["GSTIN"][2:12] != form["PAN"]:
        _add_finding(findings, "GSTIN_PAN_LINK", form["GSTIN"][2:12], form["PAN"], "GSTIN_PAN_LINK", "High", "Mismatch",
                     "The PAN embedded in the GSTIN does not match the PAN in the form. A GSTIN is always issued against the holder's PAN, so these cannot belong to the same entity.",
                     "Recommend rejection unless the vendor submits a consistent PAN and GSTIN in a new submission.",
                     confidence=1.0)

    # ---- Names ----------------------------------------------------------------
    legal_name = str(profile.get("legal_name") or "").strip()
    gst_legal = from_sources("LEGAL_NAME")
    gst_trade = from_sources("TRADE_NAME")
    pan_names = [f for f in all_fields if f["doc_type"] == "PAN Card" and f["field_name"] in ("NAME", "LEGAL_NAME")]
    is_individual = entity_type == "Individual / Proprietor"
    trade_norms = {normalize(t["field_value"]) for t in gst_trade}
    gst_legal_norms = {normalize(g["field_value"]) for g in gst_legal}

    if not gst_legal:
        if "GST Certificate" in supplied_types:
            _add_finding(findings, "LEGAL_NAME", "Not extracted", legal_name or "Required legal name",
                         "REQUIRED_FIELD_MISSING_LEGAL_NAME", "High", "Not Found",
                         "The legal name could not be read from the GST Certificate.",
                         "Request a legible GST Certificate showing the legal name of business.",
                         doc_of("GST Certificate"))
    elif legal_name:
        for item in gst_legal:
            if normalize(item["field_value"]) == normalize(legal_name):
                continue
            if is_individual and normalize(legal_name) in trade_norms:
                _add_finding(findings, "LEGAL_NAME", item["field_value"], legal_name,
                             "PROPRIETOR_TRADE_NAME_USED", "Low", "Info",
                             "The form uses the GST trade name. For a proprietorship the GST legal name is the owner's personal name, so this is expected.",
                             "No action needed.", item["filename"], item.get("page_number"), item.get("confidence"))
            else:
                _add_finding(findings, "LEGAL_NAME", item["field_value"], legal_name,
                             "FORM_DOCUMENT_MISMATCH_LEGAL_NAME", "High", "Mismatch",
                             "The legal name on the GST Certificate differs from the onboarding form.",
                             "Confirm whether the names refer to the same legal entity; request a corrected form or clarification.",
                             item["filename"], item.get("page_number"), item.get("confidence"))

    # The PAN holder name and the GST legal name are always the same person/entity.
    if pan_names and gst_legal:
        for item in pan_names:
            if normalize(item["field_value"]) not in gst_legal_norms:
                _add_finding(findings, "PAN_NAME", item["field_value"], gst_legal[0]["field_value"],
                             "CROSS_DOCUMENT_NAME_MISMATCH_PAN_GST", "High", "Mismatch",
                             "The name on the PAN document differs from the legal name on the GST Certificate. A GST registration is issued in the PAN holder's name.",
                             "Check for spelling variants; otherwise request consistent documents.",
                             item["filename"], item.get("page_number"), item.get("confidence"))

    holders = from_sources("ACCOUNT_HOLDER")
    if not holders:
        if "Bank Proof" in supplied_types:
            _add_finding(findings, "ACCOUNT_HOLDER", "Not extracted", legal_name or "Required account holder name",
                         "REQUIRED_FIELD_MISSING_ACCOUNT_HOLDER", "High", "Not Found",
                         "The account holder name could not be read from the Bank Proof.",
                         "Request a cancelled cheque or bank statement that shows the account holder name.",
                         doc_of("Bank Proof"))
    else:
        owner_norms = {normalize(p["field_value"]) for p in pan_names} | gst_legal_norms
        for item in holders:
            holder = normalize(item["field_value"])
            if legal_name and holder == normalize(legal_name):
                continue
            if is_individual and (holder in owner_norms or holder in trade_norms):
                _add_finding(findings, "ACCOUNT_HOLDER", item["field_value"], legal_name,
                             "ACCOUNT_HOLDER_PROPRIETOR_OWNER", "Medium", "Needs Manual Review",
                             "The account is held in the proprietor's own name (or trade name). The PAN shows an individual/proprietor, so this is expected for the entity type, but a reviewer should confirm it.",
                             "Confirm the account belongs to the proprietor named on the PAN and GST documents.",
                             item["filename"], item.get("page_number"), item.get("confidence"))
            elif not is_individual and holder in gst_legal_norms:
                continue  # Legal-name mismatch, if any, is already reported against the form.
            elif not is_individual and holder in trade_norms:
                _add_finding(findings, "ACCOUNT_HOLDER", item["field_value"], legal_name,
                             "ACCOUNT_HOLDER_TRADE_NAME", "Medium", "Needs Manual Review",
                             "The account is held in the GST trade name rather than the legal name.",
                             "Confirm with the bank proof that the account belongs to the legal entity.",
                             item["filename"], item.get("page_number"), item.get("confidence"))
            else:
                _add_finding(findings, "ACCOUNT_HOLDER", item["field_value"], legal_name,
                             "FORM_DOCUMENT_MISMATCH_ACCOUNT_HOLDER", "High", "Mismatch",
                             "The bank account holder does not match the vendor's legal name, trade name or PAN holder. Payments could go to a third party.",
                             "Confirm account ownership; request bank proof in the vendor's name.",
                             item["filename"], item.get("page_number"), item.get("confidence"))

    # ---- GSTIN state vs address (can be a legitimate branch registration) -----
    address = str(profile.get("address") or "").strip()
    gst_address = next((a["field_value"] for a in from_sources("ADDRESS")), "")
    if GSTIN_RE.fullmatch(form["GSTIN"]) and form["GSTIN"][:2] in GST_STATES:
        gst_state = GST_STATES[form["GSTIN"][:2]][0]
        for label, text in (("form address", address), ("GST certificate address", gst_address)):
            states = states_in_address(text)
            if states and gst_state not in states:
                _add_finding(findings, "ADDRESS", text, f"Address in {gst_state} (GSTIN state code {form['GSTIN'][:2]})",
                             "GSTIN_STATE_ADDRESS_MISMATCH", "Medium", "Needs Manual Review",
                             f"The GSTIN is registered in {gst_state}, but the {label} is in {', '.join(sorted(states))}. This can be legitimate for a branch registration.",
                             "Confirm whether this GSTIN is a branch/additional place of business registration.")
                break

    # ---- Fraud signals from the vendor master ---------------------------------
    if shared_bank_vendors:
        _add_finding(findings, "BANK_ACCOUNT", form["BANK_ACCOUNT"], "Account not used by another PAN",
                     "BANK_ACCOUNT_SHARED_WITH_OTHER_PAN", "High", "Mismatch",
                     "This bank account is already registered to a vendor with a different PAN ("
                     + ", ".join(sorted(shared_bank_vendors)) + "). This is a strong payment-fraud signal.",
                     "Recommend rejection and escalate to the fraud / compliance team.", confidence=1.0)
    if reused_document_vendors:
        _add_finding(findings, "DOCUMENT_TYPE", "Identical file hash", "Unique document per vendor",
                     "DOCUMENT_REUSED_ACROSS_VENDORS", "High", "Mismatch",
                     "An identical document file was already submitted by a vendor with a different PAN ("
                     + ", ".join(sorted(reused_document_vendors)) + ").",
                     "Recommend rejection and escalate; genuine documents are not shared between entities.",
                     confidence=1.0)

    if existing_bank_account not in (None, ""):
        previous = digits_only(existing_bank_account)
        if form["BANK_ACCOUNT"] and form["BANK_ACCOUNT"] != previous:
            _add_finding(findings, "BANK_ACCOUNT", form["BANK_ACCOUNT"], previous, "BANK_DETAILS_CHANGED",
                         "High", "Mismatch",
                         "The proposed bank account differs from the approved vendor master. Confirm the change through an independently trusted channel before approval.",
                         "Call the vendor on the contact details already on file (not those in this submission) to confirm the change.",
                         "Bank Proof", None, 1.0)
            contact = existing_contact or {}
            changed = []
            if str(contact.get("email") or "").strip() and \
                    str(profile.get("email") or "").strip().lower() != str(contact.get("email")).strip().lower():
                changed.append("email")
            if digits_only(contact.get("phone")) and digits_only(profile.get("phone")) != digits_only(contact.get("phone")):
                changed.append("phone")
            if changed:
                _add_finding(findings, "CONTACT_DETAILS", ", ".join(changed), "Unchanged contact details",
                             "BANK_AND_CONTACT_CHANGED", "High", "Mismatch",
                             "The bank account and the " + " and ".join(changed) + " changed in the same submission. This is the classic business-email-compromise pattern.",
                             "Do not use the new contact details to confirm the change. Call back on the previously approved contact details.",
                             None, None, 1.0)

    # ---- De-duplicate, score, recommend ---------------------------------------
    unique = {}
    for finding in findings:
        key = (finding.get("field_name"), finding.get("rule_id"), normalize(finding.get("observed_value")),
               normalize(finding.get("expected_value")), finding.get("severity"), finding.get("finding_status"))
        unique.setdefault(key, finding)
    findings = list(unique.values())

    score = min(100, sum({"High": 25, "Medium": 12, "Low": 4}.get(f["severity"], 0) for f in findings))
    recommendation = recommend(findings)
    return {"score": score, "machine_status": recommendation["machine_status"], "findings": findings,
            "fields": all_fields, "recommendation": recommendation, "entity_type": entity_type}


def evaluate_profile(profile, documents, existing_bank_account=None, **context):
    """Backward-compatible wrapper returning (score, machine_status, findings, fields)."""
    result = evaluate(profile, documents, existing_bank_account=existing_bank_account, **context)
    return result["score"], result["machine_status"], result["findings"], result["fields"]
