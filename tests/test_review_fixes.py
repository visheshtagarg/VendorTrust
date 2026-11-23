"""One regression test (or more) per issue raised in the two code reviews."""
import io
import json
import zipfile

import pytest

from core.engine import (
    UNRESOLVABLE_RULES, DocumentError, evaluate, evaluate_profile, extract_fields_from_pages,
    gstin_checksum_ok, normalize,
)
from core.privacy import mask_free_text
from modules.adaptive_requests import build_internal_tasks, build_requests, build_vendor_message
from services.bulk import BulkError, parse_cases_workbook, read_pdf_zip
from services.submission import (
    approval_blockers, bank_change_resolvers, prepare_documents, record_decision, resolve_finding,
    save_submission,
)
from tests.conftest import ACCOUNT, GSTIN, NAME, PAN, clean_docs, clean_profile, field

ROOT_PDFS = __import__("pathlib").Path(__file__).resolve().parents[1] / "demo_pdfs"


def _pdf(name):
    return (ROOT_PDFS / name).read_bytes()


# ---- 1. Approval without a verified bank account -----------------------------
@pytest.mark.parametrize("text", ["Account Number 123456789012", "Account Number: 1234 5678 9012",
                                  "A/C No. 123456789012", "Account No.:\n123456789012",
                                  "BANK_ACCOUNT\n123456789012", "Savings Account No - 123456789012"])
def test_account_number_variants_are_extracted(text):
    values = [f["field_value"] for f in extract_fields_from_pages([(1, text)]) if f["field_name"] == "BANK_ACCOUNT"]
    assert values == [ACCOUNT]


def test_account_not_found_blocks_approval_and_cannot_be_resolved():
    docs = clean_docs()
    docs[2]["fields"] = [f for f in docs[2]["fields"] if f["field_name"] != "BANK_ACCOUNT"]
    result = evaluate(clean_profile(), docs)
    finding = next(f for f in result["findings"] if f["rule_id"] == "FIELD_NOT_FOUND_BANK_ACCOUNT")
    assert finding["severity"] == "High"
    assert "FIELD_NOT_FOUND_BANK_ACCOUNT" in UNRESOLVABLE_RULES
    blockers = approval_blockers(result["findings"], [{"doc_type": t} for t in ("PAN Card", "GST Certificate", "Bank Proof")],
                                 [1], {"decision": None}, "alice")
    assert blockers
    assert result["machine_status"] == "Needs Documents"


def test_any_unresolved_missing_or_invalid_status_blocks_even_if_medium_or_low():
    finding = {"rule_id": "SOMETHING", "severity": "Low", "finding_status": "Not Found"}
    blockers = approval_blockers([finding], [{"doc_type": t} for t in ("PAN Card", "GST Certificate", "Bank Proof")],
                                 [1], {"decision": None}, "alice")
    assert any("unresolved" in b for b in blockers)


# ---- 2. Document type checked against content; duplicate files --------------
def test_gst_pdf_in_every_slot_is_rejected_before_saving():
    gst = _pdf("02_GST.pdf")
    with pytest.raises(DocumentError, match="same file"):
        prepare_documents([("PAN Card", "a.pdf", gst), ("GST Certificate", "b.pdf", gst), ("Bank Proof", "c.pdf", gst)])


def test_wrong_document_in_slot_is_unresolvable_mismatch():
    gst_fields = clean_docs()[1]["fields"]
    docs = [{"doc_type": "PAN Card", "filename": "p.pdf", "fields": gst_fields},
            {"doc_type": "GST Certificate", "filename": "g.pdf", "fields": gst_fields},
            {"doc_type": "Bank Proof", "filename": "b.pdf", "fields": gst_fields}]
    result = evaluate(clean_profile(), docs)
    mismatches = [f for f in result["findings"] if f["rule_id"] == "DOC_TYPE_CONTENT_MISMATCH"]
    assert {f["source_document"] for f in mismatches} == {"p.pdf", "b.pdf"}  # GST-in-PAN-slot is caught too
    assert "DOC_TYPE_CONTENT_MISMATCH" in UNRESOLVABLE_RULES


def test_bank_account_only_trusted_from_bank_proof():
    docs = clean_docs()
    docs[2]["fields"] = [f for f in docs[2]["fields"] if f["field_name"] != "BANK_ACCOUNT"]
    docs[1]["fields"].append(field("BANK_ACCOUNT", ACCOUNT))  # account only appears on the GST doc
    rules = {f["rule_id"] for f in evaluate(clean_profile(), docs)["findings"]}
    assert "FIELD_NOT_FOUND_BANK_ACCOUNT" in rules


def test_sha256_is_stored_and_reuse_across_vendors_is_flagged(connection, uploads):
    files = [("PAN Card", "p.pdf", _pdf("01_PAN.pdf")), ("GST Certificate", "g.pdf", _pdf("02_GST.pdf")),
             ("Bank Proof", "b.pdf", _pdf("03_Bank_Proof.pdf"))]
    save_submission(connection, clean_profile(), prepare_documents(files), None, uploads)
    assert connection.execute("SELECT COUNT(*) FROM documents WHERE length(sha256)=64").fetchone()[0] == 3
    other = clean_profile(pan="PQRCS1234K", gstin="07PQRCS1234K1Z" + "0", bank_account="555566667777")
    result = save_submission(connection, other, prepare_documents(files), None, uploads)
    assert "DOCUMENT_REUSED_ACROSS_VENDORS" in {f["rule_id"] for f in result["findings"]}
    assert result["recommendation"]["outcome"] == "Rejected"


# ---- 3. Unresolvable rules -----------------------------------------------------
def test_unresolvable_set_contains_reviewed_rules():
    for rule in ("FORM_DOCUMENT_MISMATCH_BANK_ACCOUNT", "GSTIN_PAN_LINK", "CROSS_DOCUMENT_CONFLICT_PAN",
                 "CROSS_DOCUMENT_CONFLICT_BANK_ACCOUNT", "BANK_ACCOUNT_SHARED_WITH_OTHER_PAN",
                 "DOC_TYPE_CONTENT_MISMATCH"):
        assert rule in UNRESOLVABLE_RULES


def test_resolving_gstin_pan_link_is_refused(connection, uploads):
    profile = clean_profile(gstin="07ZZZCZ9999Z1Z" + "0")
    files = [("PAN Card", "p.pdf", _pdf("01_PAN.pdf")), ("GST Certificate", "g.pdf", _pdf("02_GST.pdf")),
             ("Bank Proof", "b.pdf", _pdf("03_Bank_Proof.pdf"))]
    result = save_submission(connection, profile, prepare_documents(files), None, uploads)
    row = connection.execute("SELECT id FROM findings WHERE rule_id='GSTIN_PAN_LINK'").fetchone()
    with pytest.raises(ValueError, match="cannot be overridden"):
        resolve_finding(connection, result["vendor_id"], result["submission_id"], row["id"], "alice",
                        "I looked at it and it seems fine to me")


# ---- 4. Engine recommends Rejected / Pending / Approved -----------------------
def test_recommendation_outcomes_map_to_problem_statement():
    assert evaluate(clean_profile(), clean_docs())["recommendation"]["outcome"] == "Approved"
    assert evaluate(clean_profile(), [])["recommendation"]["outcome"] == "Pending"
    bad = evaluate(clean_profile(pan="ZZZCZ9999Z"), clean_docs())
    assert bad["recommendation"]["outcome"] == "Rejected"
    assert any("GSTIN_PAN_LINK" in r for r in bad["recommendation"]["reasons"])


# ---- 5. No false "bank changed" on an unapproved resubmission -----------------
def test_typo_fix_on_unapproved_vendor_is_not_a_bank_change(connection, uploads):
    files = [("PAN Card", "p.pdf", _pdf("01_PAN.pdf")), ("GST Certificate", "g.pdf", _pdf("02_GST.pdf")),
             ("Bank Proof", "b.pdf", _pdf("03_Bank_Proof.pdf"))]
    first = save_submission(connection, clean_profile(bank_account="123456789013"), prepare_documents(files), None, uploads)
    existing = connection.execute("SELECT * FROM vendors WHERE id=?", (first["vendor_id"],)).fetchone()
    second = save_submission(connection, clean_profile(), prepare_documents(files), existing, uploads)
    assert "BANK_DETAILS_CHANGED" not in {f["rule_id"] for f in second["findings"]}
    assert second["recommendation"]["outcome"] == "Approved"


# ---- 6. Shared bank account at onboarding -------------------------------------
def test_shared_bank_account_with_other_pan_is_reject():
    result = evaluate(clean_profile(), clean_docs(), shared_bank_vendors=["VT-OTHER"])
    finding = next(f for f in result["findings"] if f["rule_id"] == "BANK_ACCOUNT_SHARED_WITH_OTHER_PAN")
    assert finding["severity"] == "High" and finding["rule_id"] in UNRESOLVABLE_RULES
    assert result["machine_status"] == "Recommend Reject"


# ---- 7. Bank + contact change and two-person approval -------------------------
def test_bank_and_contact_change_together_is_flagged():
    result = evaluate(clean_profile(bank_account="999999999999", email="new@evil.example.com"),
                      clean_docs(account="999999999999"), existing_bank_account=ACCOUNT,
                      existing_contact={"email": "a@abc.example.com", "phone": "9876543210"})
    assert {"BANK_DETAILS_CHANGED", "BANK_AND_CONTACT_CHANGED"} <= {f["rule_id"] for f in result["findings"]}


def test_contact_change_alone_is_not_flagged():
    result = evaluate(clean_profile(email="new@abc.example.com"), clean_docs(), existing_bank_account=ACCOUNT,
                      existing_contact={"email": "a@abc.example.com", "phone": "9876543210"})
    assert result["findings"] == []


def test_bank_change_resolver_cannot_approve():
    findings = [{"rule_id": "BANK_DETAILS_CHANGED", "severity": "High", "finding_status": "Resolved"}]
    docs = [{"doc_type": t} for t in ("PAN Card", "GST Certificate", "Bank Proof")]
    assert any("Two-person" in b for b in approval_blockers(findings, docs, [1], {"decision": None}, "Alice", {"alice"}))
    assert approval_blockers(findings, docs, [1], {"decision": None}, "Bob", {"alice"}) == []


# ---- 8. Vendor messages ---------------------------------------------------------
def test_vendor_message_has_no_rule_codes_and_bank_change_is_internal():
    findings = evaluate(clean_profile(legal_name="Other Name Pvt Ltd", bank_account="999999999999"),
                        clean_docs(account="999999999999", holder="SOMEONE ELSE"),
                        existing_bank_account=ACCOUNT)["findings"]
    message = build_vendor_message("Other Name Pvt Ltd", findings)
    assert message and "Dear Other Name Pvt Ltd" in message
    assert "_" not in message.replace("Vendor Management Team", "")  # no RULE_CODES
    assert "upload a clear cancelled cheque" not in message.lower()
    assert any("Call back" in t for t in build_internal_tasks(findings))
    assert all(i["audience"] == "internal" for i in build_requests(findings) if i["rule_id"] == "BANK_DETAILS_CHANGED")


def test_reject_message_does_not_reveal_fraud_signal():
    result = evaluate(clean_profile(), clean_docs(), shared_bank_vendors=["VT-OTHER"])
    message = build_vendor_message(NAME, result["findings"], result["recommendation"])
    assert "VT-OTHER" not in message and "shared" not in message.lower()


# ---- 9. Extraction gaps -------------------------------------------------------
def test_empty_legal_name_does_not_capture_next_label():
    fields = extract_fields_from_pages([(1, "Legal Name:\nTrade Name: SHELL CO")])
    assert not [f for f in fields if f["field_name"] == "LEGAL_NAME"]


def test_address_and_pan_name_on_next_line_are_extracted():
    text = (ROOT_PDFS / "02_GST.pdf")
    from core.engine import extract_pdf
    _, pages, _ = extract_pdf(text.read_bytes())
    names = {f["field_name"] for f in extract_fields_from_pages(pages)}
    assert {"ADDRESS", "TRADE_NAME", "LEGAL_NAME"} <= names
    _, pages, _ = extract_pdf(_pdf("01_PAN.pdf"))
    assert "NAME" in {f["field_name"] for f in extract_fields_from_pages(pages)}


def test_pan_name_is_compared_with_gst_legal_name():
    docs = clean_docs()
    docs[0]["fields"][1] = field("NAME", "TOTALLY DIFFERENT LTD")
    assert "CROSS_DOCUMENT_NAME_MISMATCH_PAN_GST" in {f["rule_id"] for f in evaluate(clean_profile(), docs)["findings"]}


def test_state_code_vs_address():
    result = evaluate(clean_profile(address="Mumbai, Maharashtra"), clean_docs())
    assert "GSTIN_STATE_ADDRESS_MISMATCH" in {f["rule_id"] for f in result["findings"]}
    assert result["recommendation"]["outcome"] == "Pending"


def test_ms_prefix_and_ampersand_normalize():
    assert normalize("M/s ABC Infrastructure Pvt. Ltd.") == normalize("ABC INFRASTRUCTURE PVT LTD")
    assert normalize("A & B Traders") == normalize("A AND B TRADERS")
    assert evaluate(clean_profile(legal_name="M/s ABC Infrastructure Pvt. Ltd."), clean_docs())["findings"] == []


@pytest.mark.parametrize("account", ["12", "12345678", "1234567890123456789", "12AB34567890"])
def test_form_bank_account_format(account):
    rules = {f["rule_id"] for f in evaluate(clean_profile(bank_account=account), clean_docs())["findings"]}
    assert "INVALID_FORMAT_BANK_ACCOUNT" in rules


# ---- 10. PAN holder type and GSTIN checksum ------------------------------------
def test_pan_type_and_gstin_checksum_rules():
    assert not gstin_checksum_ok("07ABCDE1234F1Z5")
    assert gstin_checksum_ok("07ABCDE1234F1Z2")
    assert gstin_checksum_ok(GSTIN)
    rules = {f["rule_id"] for f in evaluate(clean_profile(pan="ABCDE1234F", gstin="07ABCDE1234F1Z5"),
                                            clean_docs(pan="ABCDE1234F", gstin="07ABCDE1234F1Z5"))["findings"]}
    assert {"INVALID_PAN_HOLDER_TYPE", "INVALID_CHECKSUM_GSTIN"} <= rules


# ---- 11. Reviewer name and note masking ----------------------------------------
def test_resolution_note_is_masked_and_stored_separately(connection, uploads):
    files = [("PAN Card", "p.pdf", _pdf("01_PAN.pdf")), ("GST Certificate", "g.pdf", _pdf("02_GST.pdf")),
             ("Bank Proof", "b.pdf", _pdf("03_Bank_Proof.pdf"))]
    result = save_submission(connection, clean_profile(address="Mumbai, Maharashtra"), prepare_documents(files),
                             None, uploads)
    row = connection.execute("SELECT * FROM findings WHERE rule_id='GSTIN_STATE_ADDRESS_MISMATCH'").fetchone()
    resolve_finding(connection, result["vendor_id"], result["submission_id"], row["id"], "alice",
                    "Branch confirmed, account 1234 5678 9012 and PAN ABCCA1234F")
    stored = connection.execute("SELECT * FROM findings WHERE id=?", (row["id"],)).fetchone()
    assert "9012" in stored["resolution_note"] and "1234 5678" not in stored["resolution_note"]
    assert "ABCCA1234F" not in stored["resolution_note"]
    assert "Reviewer resolution" not in stored["explanation"]
    audit_rows = " ".join(r[0] for r in connection.execute("SELECT details FROM audit_events"))
    assert "123456789012" not in audit_rows and "ABCCA1234F" not in audit_rows


def test_mask_free_text():
    assert mask_free_text("acct 123456789012") == "acct ********9012"
    assert "x@" not in mask_free_text("mail x@corp.com")


def test_send_requires_reviewer(connection, uploads):
    from services.submission import send_vendor_message
    files = [("PAN Card", "p.pdf", _pdf("01_PAN.pdf")), ("GST Certificate", "g.pdf", _pdf("02_GST.pdf")),
             ("Bank Proof", "b.pdf", _pdf("03_Bank_Proof.pdf"))]
    result = save_submission(connection, clean_profile(address="Mumbai, Maharashtra"), prepare_documents(files),
                             None, uploads)
    vendor = connection.execute("SELECT * FROM vendors WHERE id=?", (result["vendor_id"],)).fetchone()
    with pytest.raises(ValueError, match="reviewer"):
        send_vendor_message(connection, vendor, result["submission_id"], "  ")
    message = send_vendor_message(connection, vendor, result["submission_id"], "alice")
    assert "branch" in message.lower()
    event = connection.execute("SELECT details FROM audit_events WHERE event_type='VENDOR_MESSAGE_SENT'").fetchone()
    assert json.loads(event[0])["reviewer"] == "alice"


# ---- Bulk upload bugs ----------------------------------------------------------
def _xlsx(rows):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Cases"
    ws.append(["Case_ID", "Action", "Vendor_Code", "Legal_Name", "PAN", "GSTIN", "Email", "Phone", "Address",
               "Bank_Account", "IFSC"])
    for row in rows:
        ws.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def test_b1_excel_blank_cells_and_numbers_are_preserved():
    data = _xlsx([["C1", "NEW", None, NAME, PAN, GSTIN, None, 9876543210, None, 123456789012, "HDFC0001234"],
                  ["C2", "NEW", None, NAME, PAN, GSTIN, None, None, None, "001234567890", "HDFC0001234"]])
    cases = parse_cases_workbook(data)
    assert cases[0]["Email"] == "" and cases[0]["Address"] == ""
    assert cases[0]["Bank_Account"] == "123456789012" and cases[0]["Phone"] == "9876543210"
    assert cases[1]["Bank_Account"] == "001234567890"


def _zip(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return buffer.getvalue()


def test_b3_zip_name_collisions_and_bombs_are_rejected():
    pdf = _pdf("01_PAN.pdf")
    with pytest.raises(BulkError, match="more than one folder"):
        read_pdf_zip(_zip([("a/C1_PAN.pdf", pdf), ("b/C1_PAN.pdf", pdf)]))
    with pytest.raises(BulkError, match="compression ratio"):
        read_pdf_zip(_zip([("C1_PAN.pdf", b"%PDF-" + b"0" * 5_000_000)]))
    assert set(read_pdf_zip(_zip([("x/C1_PAN.pdf", pdf)]))) == {"c1_pan.pdf"}


def test_b2_template_matches_demo_pdfs(connection, uploads):
    from services.bulk import build_bulk_template, process_bulk_case
    case = parse_cases_workbook(build_bulk_template())[0]
    pdf_map = {"case001_pan.pdf": _pdf("01_PAN.pdf"), "case001_gst.pdf": _pdf("02_GST.pdf"),
               "case001_bank.pdf": _pdf("03_Bank_Proof.pdf")}
    result = process_bulk_case(connection, case, pdf_map, uploads)
    assert result["Findings"] == 0 and result["Recommendation"].startswith("Approve-ready")


def test_b4_new_with_existing_pan_is_an_error(connection, uploads):
    from services.bulk import build_bulk_template, process_bulk_case
    case = parse_cases_workbook(build_bulk_template())[0]
    pdf_map = {"case001_pan.pdf": _pdf("01_PAN.pdf"), "case001_gst.pdf": _pdf("02_GST.pdf"),
               "case001_bank.pdf": _pdf("03_Bank_Proof.pdf")}
    process_bulk_case(connection, case, pdf_map, uploads)
    with pytest.raises(BulkError, match="already exists"):
        process_bulk_case(connection, dict(case, Case_ID="CASE001"), pdf_map, uploads)


def test_b5_single_save_path():
    import inspect
    import services.bulk
    source = inspect.getsource(services.bulk.process_bulk_case)
    assert "save_submission(" in source and "INSERT INTO" not in source
