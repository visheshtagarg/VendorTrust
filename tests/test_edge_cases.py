"""The four named edge cases, run end-to-end on the PDFs in edge_cases/."""
import json
from pathlib import Path

import pytest

from services.submission import (
    approval_blockers, bank_change_resolvers, find_vendor, normalize_profile, prepare_documents,
    record_decision, resolve_finding, save_submission,
)

EDGE = Path(__file__).resolve().parents[1] / "edge_cases"


def files(folder, bank=None):
    folder = EDGE / folder
    return [("PAN Card", "01_PAN.pdf", (folder / "01_PAN.pdf").read_bytes()),
            ("GST Certificate", "02_GST.pdf", (folder / "02_GST.pdf").read_bytes()),
            ("Bank Proof", "03_Bank_Proof.pdf", (bank or folder / "03_Bank_Proof.pdf").read_bytes())]


def submit(connection, uploads, form, docs):
    profile = normalize_profile(form)
    return save_submission(connection, profile, prepare_documents(docs), find_vendor(connection, pan=profile["pan"]),
                           uploads)


def rows(connection, sql, params):
    return [dict(r) for r in connection.execute(sql, params)]


def state(connection, result):
    sid = result["submission_id"]
    return (rows(connection, "SELECT * FROM findings WHERE submission_id=?", (sid,)),
            rows(connection, "SELECT * FROM documents WHERE submission_id=?", (sid,)),
            rows(connection, "SELECT ef.* FROM extracted_fields ef JOIN documents d ON d.id=ef.document_id "
                             "WHERE d.submission_id=?", (sid,)),
            rows(connection, "SELECT * FROM submissions WHERE id=?", (sid,))[0])


def approve(connection, result, reviewer):
    findings, docs, extracted, submission = state(connection, result)
    vendor = connection.execute("SELECT * FROM vendors WHERE id=?", (result["vendor_id"],)).fetchone()
    record_decision(connection, vendor, submission, "Approved", reviewer, "Evidence reviewed and consistent.",
                    findings, docs, extracted)


@pytest.mark.parametrize("variant,expected_rules", [
    ("step2a_bank_change_only", {"BANK_DETAILS_CHANGED"}),
    ("step2b_bank_and_contact_change", {"BANK_DETAILS_CHANGED", "BANK_AND_CONTACT_CHANGED"}),
])
def test_ec1_approved_vendor_changes_bank_account(connection, uploads, variant, expected_rules):
    forms = json.loads((EDGE / "EC1_bank_account_change" / "forms.json").read_text())
    first = submit(connection, uploads, forms["step1_original"], files("EC1_bank_account_change/step1_original_onboarding"))
    assert first["recommendation"]["outcome"] == "Approved"
    approve(connection, first, "alice")

    change = submit(connection, uploads, forms[variant], files(
        "EC1_bank_account_change/step1_original_onboarding",
        bank=EDGE / "EC1_bank_account_change" / "step2_change_request" / "03_Bank_Proof_NEW.pdf"))
    assert {f["rule_id"] for f in change["findings"]} == expected_rules
    assert change["recommendation"]["label"] == "Pending: needs human review"
    # Internal callback, not a vendor re-upload request.
    assert change["vendor_message"] is None
    assert any("Call back" in t for t in change["internal_tasks"])
    # The approved master keeps the old account until approval.
    master = connection.execute("SELECT bank_account FROM vendors WHERE id=?", (first["vendor_id"],)).fetchone()
    assert master[0] == "123456789012"

    for finding in state(connection, change)[0]:
        with pytest.raises(ValueError, match="30"):
            resolve_finding(connection, change["vendor_id"], change["submission_id"], finding["id"], "bob", "Called them.")
        resolve_finding(connection, change["vendor_id"], change["submission_id"], finding["id"], "bob",
                        "Called CFO on the phone number in the approved master; confirmed new account.")
    assert bank_change_resolvers(connection, change["submission_id"]) == {"bob"}
    with pytest.raises(ValueError, match="Two-person"):
        approve(connection, change, "Bob")
    approve(connection, change, "carol")
    master = connection.execute("SELECT bank_account, status FROM vendors WHERE id=?", (first["vendor_id"],)).fetchone()
    assert tuple(master) == ("987654321098", "Approved")


def test_ec2_proprietorship_account_in_owner_name(connection, uploads):
    form = json.loads((EDGE / "EC2_proprietorship_owner_account" / "form.json").read_text())
    result = submit(connection, uploads, form, files("EC2_proprietorship_owner_account"))
    by_rule = {f["rule_id"]: f for f in result["findings"]}
    assert result["entity_type"] == "Individual / Proprietor"
    assert set(by_rule) == {"PROPRIETOR_TRADE_NAME_USED", "ACCOUNT_HOLDER_PROPRIETOR_OWNER"}
    assert by_rule["ACCOUNT_HOLDER_PROPRIETOR_OWNER"]["severity"] == "Medium"
    assert "FORM_DOCUMENT_MISMATCH_ACCOUNT_HOLDER" not in by_rule
    assert result["recommendation"]["label"] == "Pending: needs human review"
    finding = next(f for f in state(connection, result)[0] if f["rule_id"] == "ACCOUNT_HOLDER_PROPRIETOR_OWNER")
    resolve_finding(connection, result["vendor_id"], result["submission_id"], finding["id"], "alice",
                    "Holder matches the PAN holder; proprietorship.")
    approve(connection, result, "alice")


def test_ec3_same_bank_account_two_pans_is_reject(connection, uploads):
    forms = json.loads((EDGE / "EC3_shared_bank_account" / "forms.json").read_text())
    submit(connection, uploads, forms["vendor_A"], files("EC3_shared_bank_account/vendor_A_existing"))
    result = submit(connection, uploads, forms["vendor_B"], files("EC3_shared_bank_account/vendor_B_new"))
    assert {f["rule_id"] for f in result["findings"]} == {"BANK_ACCOUNT_SHARED_WITH_OTHER_PAN"}
    assert result["recommendation"]["outcome"] == "Rejected"
    assert result["machine_status"] == "Recommend Reject"
    assert "shared" not in result["vendor_message"].lower()
    findings, docs, extracted, submission = state(connection, result)
    assert approval_blockers(findings, docs, extracted, submission, "alice")
    with pytest.raises(ValueError, match="cannot be overridden"):
        resolve_finding(connection, result["vendor_id"], result["submission_id"], findings[0]["id"], "alice",
                        "Looks fine to me after a quick look")


def test_ec4_gstin_state_differs_from_address_goes_to_review(connection, uploads):
    form = json.loads((EDGE / "EC4_gstin_state_vs_address" / "form.json").read_text())
    result = submit(connection, uploads, form, files("EC4_gstin_state_vs_address"))
    assert [f["rule_id"] for f in result["findings"]] == ["GSTIN_STATE_ADDRESS_MISMATCH"]
    assert result["recommendation"]["outcome"] == "Pending"
    assert result["recommendation"]["label"] == "Pending: needs human review"
    assert "branch" in result["vendor_message"].lower()
