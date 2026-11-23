"""Original engine tests, updated to valid identifiers (PAN type C, correct GSTIN check digit)."""
from core.engine import evaluate_profile, extract_fields_from_pages, normalize
from tests.conftest import ACCOUNT, NAME, clean_docs, clean_profile, field


def test_missing_documents_never_approved():
    _, status, _, _ = evaluate_profile(clean_profile(), [])
    assert status == "Needs Documents"


def test_clean_documents_are_approve_ready_but_not_approved():
    _, status, findings, _ = evaluate_profile(clean_profile(), clean_docs())
    assert findings == []
    assert status == "Approve-ready"
    assert status != "Approved"


def test_form_document_mismatch_detected():
    docs = clean_docs()
    docs[0]["fields"][0] = field("PAN", "ZZZCZ9999Z")
    _, status, findings, _ = evaluate_profile(clean_profile(), docs)
    rules = {f["rule_id"] for f in findings}
    assert "FORM_DOCUMENT_MISMATCH_PAN" in rules
    assert "CROSS_DOCUMENT_CONFLICT_PAN" in rules  # PAN card and GST certificate disagree
    assert status == "Recommend Reject"


def test_normalized_labels_are_extracted_from_pdf_text():
    pages = [(1, "LEGAL_NAME\nABC INFRASTRUCTURE PVT LTD\nACCOUNT_HOLDER\nABC INFRASTRUCTURE PVT LTD\n"
                 "BANK_ACCOUNT\n123456789012\nIFSC\nHDFC0001234")]
    fields = {item["field_name"]: item["field_value"] for item in extract_fields_from_pages(pages)}
    assert fields["LEGAL_NAME"] == NAME
    assert fields["ACCOUNT_HOLDER"] == NAME
    assert fields["BANK_ACCOUNT"] == ACCOUNT


def test_legal_name_suffix_variants_are_normalized():
    assert normalize("ABC INFRASTRUCTURE PVT LTD") == normalize("ABC Infrastructure Private Limited")


def test_repeated_legal_name_mismatch_is_scored_once():
    docs = clean_docs(legal="ABC INFRASTRUCTURE PRIVATE LIMITED")
    docs[1]["fields"].append(field("LEGAL_NAME", "ABC INFRASTRUCTURE PRIVATE LIMITED", page=2))
    _, _, findings, _ = evaluate_profile(clean_profile(legal_name="XYZ INFRASTRUCTURE PVT LTD"), docs)
    legal = [f for f in findings if f["rule_id"] == "FORM_DOCUMENT_MISMATCH_LEGAL_NAME"]
    assert len(legal) == 1 and legal[0]["severity"] == "High"


def test_matching_new_bank_proof_flags_reviewable_bank_change():
    _, _, findings, _ = evaluate_profile(clean_profile(bank_account="999999999999"),
                                         clean_docs(account="999999999999"), existing_bank_account=ACCOUNT)
    rules = {f["rule_id"] for f in findings}
    assert "BANK_DETAILS_CHANGED" in rules
    assert "FORM_DOCUMENT_MISMATCH_BANK_ACCOUNT" not in rules


def test_form_bank_mismatch_remains_distinct_from_bank_change():
    _, _, findings, _ = evaluate_profile(clean_profile(bank_account="999999999999"),
                                         clean_docs(account="111111111111"), existing_bank_account=ACCOUNT)
    rules = {f["rule_id"] for f in findings}
    assert "FORM_DOCUMENT_MISMATCH_BANK_ACCOUNT" in rules
    assert "BANK_DETAILS_CHANGED" in rules
