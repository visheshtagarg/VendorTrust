"""Submission and review logic shared by New Onboarding, Bulk Upload and the
Review Center. No Streamlit imports, so everything here is unit-testable.
"""
import hashlib
import json
import re
import uuid
from pathlib import Path

from core.engine import (
    BANK_CHANGE_RULES, BLOCKING_STATUSES, DocumentError, UNRESOLVABLE_RULES,
    digits_only, evaluate, extract_fields_from_pages, extract_pdf, is_resolved,
    recommend, validate_pdf,
)
from core.privacy import mask_free_text
from modules.adaptive_requests import build_internal_tasks, build_requests, build_vendor_message

REQUIRED_DOC_TYPES = ("PAN Card", "GST Certificate", "Bank Proof")
MIN_NOTE = 15
MIN_BANK_CHANGE_NOTE = 30


# --------------------------------------------------------------------------
# Input preparation
# --------------------------------------------------------------------------
def normalize_profile(raw):
    """Clean form / Excel values into the profile shape the engine expects."""
    def text(key):
        value = raw.get(key)
        value = "" if value is None else str(value).strip()
        return "" if value.lower() in ("nan", "none") else value

    bank = text("bank_account")
    if re.fullmatch(r"[0-9][0-9 \-]*", bank or "x"):
        bank = digits_only(bank)
    return {
        "legal_name": text("legal_name"),
        "pan": text("pan").upper(),
        "gstin": text("gstin").upper(),
        "email": text("email"),
        "phone": text("phone"),
        "address": text("address"),
        "bank_account": bank,
        "ifsc": text("ifsc").upper(),
    }


def prepare_documents(files, max_mb=20):
    """files: iterable of (doc_type, filename, bytes). Returns prepared docs.

    Raises DocumentError for invalid PDFs or when the same file is used in two slots.
    """
    prepared, seen = [], {}
    for doc_type, filename, data in files:
        safe_name = Path(filename).name
        validate_pdf(data, safe_name, max_mb)
        digest = hashlib.sha256(data).hexdigest()
        if digest in seen:
            raise DocumentError(
                f"The same file was uploaded as {seen[digest]} and {doc_type}. "
                "Upload a separate, correct document for each slot.")
        seen[digest] = doc_type
        text, pages, scanned = extract_pdf(data)
        prepared.append({
            "doc_type": doc_type, "filename": safe_name, "data": data, "sha256": digest,
            "text": text, "pages": pages, "fields": extract_fields_from_pages(pages),
            "needs_manual_review": scanned,
        })
    return prepared


def engine_payload(prepared):
    return [{"doc_type": d["doc_type"], "filename": d["filename"], "fields": d["fields"],
             "needs_manual_review": d["needs_manual_review"], "sha256": d.get("sha256")}
            for d in prepared]


# --------------------------------------------------------------------------
# Lookups
# --------------------------------------------------------------------------
def find_vendor(connection, pan=None, vendor_code=None):
    if vendor_code:
        row = connection.execute("SELECT * FROM vendors WHERE vendor_code=?", (vendor_code,)).fetchone()
        if row:
            return row
    if pan:
        return connection.execute(
            "SELECT * FROM vendors WHERE upper(pan)=upper(?) ORDER BY id DESC LIMIT 1", (pan,)
        ).fetchone()
    return None


def gather_context(connection, profile, prepared, existing):
    """Collect vendor-master context for the engine."""
    approved = existing is not None and existing["status"] == "Approved"
    context = {
        # Only an APPROVED master is a baseline for "bank details changed".
        # A vendor fixing a typo in an unapproved submission is not a change.
        "existing_bank_account": existing["bank_account"] if approved else None,
        "existing_contact": {"email": existing["email"], "phone": existing["phone"]} if approved else None,
        "shared_bank_vendors": [],
        "reused_document_vendors": [],
    }
    account = digits_only(profile.get("bank_account"))
    if account:
        rows = connection.execute(
            """SELECT vendor_code FROM vendors
               WHERE replace(replace(bank_account,' ',''),'-','')=? AND upper(pan)<>upper(?)""",
            (account, profile.get("pan") or ""),
        ).fetchall()
        context["shared_bank_vendors"] = sorted({r["vendor_code"] for r in rows})
    hashes = [d["sha256"] for d in prepared if d.get("sha256")]
    if hashes:
        placeholders = ",".join("?" * len(hashes))
        rows = connection.execute(
            f"""SELECT DISTINCT v.vendor_code FROM documents d JOIN vendors v ON v.id=d.vendor_id
                WHERE d.sha256 IN ({placeholders}) AND upper(v.pan)<>upper(?)""",
            (*hashes, profile.get("pan") or ""),
        ).fetchall()
        context["reused_document_vendors"] = sorted({r["vendor_code"] for r in rows})
    return context


def audit(connection, vendor_id, submission_id, event_type, details):
    connection.execute(
        "INSERT INTO audit_events(vendor_id,submission_id,event_type,details) VALUES(?,?,?,?)",
        (vendor_id, submission_id, event_type, mask_free_text(json.dumps(details))),
    )


# --------------------------------------------------------------------------
# Save a submission (single implementation for UI and bulk)
# --------------------------------------------------------------------------
def save_submission(connection, profile, prepared, existing, uploads_dir, source="ONLINE_FORM",
                    audit_extra=None):
    context = gather_context(connection, profile, prepared, existing)
    result = evaluate(profile, engine_payload(prepared), **context)
    score, machine_status, findings = result["score"], result["machine_status"], result["findings"]
    recommendation = result["recommendation"]

    if existing:
        vendor_id, vendor_code = existing["id"], existing["vendor_code"]
        if existing["status"] != "Approved":
            # Not yet approved: the vendor row simply tracks the latest proposal.
            connection.execute(
                """UPDATE vendors SET legal_name=?,pan=?,gstin=?,email=?,phone=?,address=?,bank_account=?,
                   ifsc=?,status=?,risk_score=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (profile["legal_name"], profile["pan"], profile["gstin"], profile["email"], profile["phone"],
                 profile["address"], profile["bank_account"], profile["ifsc"], machine_status, score, vendor_id))
        # An approved master stays unchanged until a reviewer approves this submission.
    else:
        vendor_code = "VT-" + uuid.uuid4().hex[:8].upper()
        vendor_id = connection.execute(
            """INSERT INTO vendors(vendor_code,legal_name,pan,gstin,email,phone,address,bank_account,ifsc,status,risk_score)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (vendor_code, profile["legal_name"], profile["pan"], profile["gstin"], profile["email"],
             profile["phone"], profile["address"], profile["bank_account"], profile["ifsc"],
             machine_status, score)).lastrowid

    submission_id = connection.execute(
        """INSERT INTO submissions(vendor_id,status,decision,risk_score,recommendation,recommendation_reasons,source)
           VALUES(?,?,?,?,?,?,?)""",
        (vendor_id, machine_status, None, score, recommendation["label"],
         json.dumps(recommendation["reasons"]), source)).lastrowid

    uploads_dir = Path(uploads_dir)
    uploads_dir.mkdir(parents=True, exist_ok=True)
    for document in prepared:
        stored = uploads_dir / f"{vendor_id}_{submission_id}_{document['doc_type'].replace(' ', '_')}_{document['filename']}"
        stored.write_bytes(document["data"])
        document_id = connection.execute(
            """INSERT INTO documents(submission_id,vendor_id,doc_type,filename,stored_path,extracted_text,sha256)
               VALUES(?,?,?,?,?,?,?)""",
            (submission_id, vendor_id, document["doc_type"], document["filename"], str(stored),
             document["text"], document.get("sha256"))).lastrowid
        for item in document["fields"]:
            connection.execute(
                """INSERT INTO extracted_fields(document_id,vendor_id,field_name,field_value,page_number,confidence,source)
                   VALUES(?,?,?,?,?,?,?)""",
                (document_id, vendor_id, item["field_name"], item["field_value"], item["page_number"],
                 item["confidence"], item["source"]))

    for f in findings:
        connection.execute(
            """INSERT INTO findings(submission_id,vendor_id,field_name,observed_value,expected_value,rule_id,severity,
               finding_status,explanation,source_document,source_page,confidence,recommended_action)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (submission_id, vendor_id, f["field_name"], f["observed_value"], f["expected_value"], f["rule_id"],
             f["severity"], f["finding_status"], f["explanation"], f["source_document"], f["source_page"],
             f["confidence"], f["recommended_action"]))

    connection.execute("INSERT INTO snapshots(vendor_id,submission_id,snapshot_json) VALUES(?,?,?)",
                       (vendor_id, submission_id, json.dumps(profile)))

    for item in build_requests(findings):
        if item["audience"] != "vendor":
            continue
        connection.execute(
            """INSERT INTO document_requests(vendor_id,submission_id,request_text,reason,status)
               SELECT ?,?,?,?,'Suggested' WHERE NOT EXISTS (SELECT 1 FROM document_requests
               WHERE vendor_id=? AND submission_id=? AND request_text=?)""",
            (vendor_id, submission_id, item["request"], item["reason"], vendor_id, submission_id, item["request"]))
    vendor_message = build_vendor_message(profile["legal_name"], findings, recommendation)
    internal_tasks = build_internal_tasks(findings)
    if vendor_message:
        connection.execute(
            "INSERT INTO vendor_messages(vendor_id,submission_id,audience,message_text,status) VALUES(?,?,?,?,?)",
            (vendor_id, submission_id, "vendor", vendor_message, "Draft"))
    if internal_tasks:
        connection.execute(
            "INSERT INTO vendor_messages(vendor_id,submission_id,audience,message_text,status) VALUES(?,?,?,?,?)",
            (vendor_id, submission_id, "internal", "\n".join(f"- {t}" for t in internal_tasks), "Open"))

    details = {"score": score, "machine_status": machine_status, "recommendation": recommendation["label"],
               "source": source, "resubmission": bool(existing)}
    details.update(audit_extra or {})
    audit(connection, vendor_id, submission_id,
          "BULK_ONBOARDING_SUBMITTED" if source == "BULK_UPLOAD" else "ONBOARDING_SUBMITTED", details)

    return {"vendor_id": vendor_id, "vendor_code": vendor_code, "submission_id": submission_id,
            "score": score, "machine_status": machine_status, "recommendation": recommendation,
            "findings": findings, "vendor_message": vendor_message, "internal_tasks": internal_tasks,
            "is_resubmission": bool(existing), "entity_type": result["entity_type"]}


# --------------------------------------------------------------------------
# Review
# --------------------------------------------------------------------------
def resolve_finding(connection, vendor_id, submission_id, finding_id, reviewer, note):
    reviewer, note = (reviewer or "").strip(), (note or "").strip()
    finding = connection.execute("SELECT * FROM findings WHERE id=? AND submission_id=?",
                                 (finding_id, submission_id)).fetchone()
    if finding is None:
        raise ValueError("Finding not found for this submission.")
    if finding["rule_id"] in UNRESOLVABLE_RULES:
        raise ValueError("This finding cannot be overridden. A new submission with corrected evidence is required.")
    if is_resolved(dict(finding)):
        raise ValueError("This finding is already resolved.")
    if not reviewer:
        raise ValueError("Enter the reviewer name / ID before resolving the finding.")
    if finding["rule_id"] in BANK_CHANGE_RULES and len(note) < MIN_BANK_CHANGE_NOTE:
        raise ValueError(f"For a bank-detail change, explain in at least {MIN_BANK_CHANGE_NOTE} characters how the "
                         "change was confirmed on previously approved contact details.")
    if len(note) < MIN_NOTE:
        raise ValueError(f"Please provide a meaningful resolution note (at least {MIN_NOTE} characters).")
    safe_note = mask_free_text(note)
    connection.execute(
        """UPDATE findings SET finding_status='Resolved', resolution_note=?, resolved_by=?,
           resolved_at=CURRENT_TIMESTAMP WHERE id=? AND submission_id=?""",
        (safe_note, reviewer, finding_id, submission_id))
    audit(connection, vendor_id, submission_id, "FINDING_RESOLVED",
          {"finding_id": finding_id, "rule_id": finding["rule_id"], "original_status": finding["finding_status"],
           "reviewer": reviewer, "note": safe_note})


def bank_change_resolvers(connection, submission_id):
    placeholders = ",".join("?" * len(BANK_CHANGE_RULES))
    rows = connection.execute(
        f"SELECT resolved_by FROM findings WHERE submission_id=? AND rule_id IN ({placeholders}) AND resolved_by IS NOT NULL",
        (submission_id, *sorted(BANK_CHANGE_RULES))).fetchall()
    return {str(r["resolved_by"]).strip().lower() for r in rows if r["resolved_by"]}


def approval_blockers(findings, docs, extracted, submission, approver=None, resolvers=()):
    blockers = []
    if not submission:
        return ["No submission exists."]
    if submission.get("decision") in ("Approved", "Rejected"):
        blockers.append("This submission already has a final human decision.")
    missing = sorted(set(REQUIRED_DOC_TYPES) - {d["doc_type"] for d in docs})
    if missing:
        blockers.append("Missing required documents: " + ", ".join(missing) + ".")
    if not extracted:
        blockers.append("No extractable evidence fields are available.")
    locked = sorted({f["rule_id"] for f in findings if f.get("rule_id") in UNRESOLVABLE_RULES})
    if locked:
        blockers.append("Findings that cannot be overridden: " + ", ".join(locked) + ".")
    open_items = [f for f in findings if not is_resolved(f) and f.get("rule_id") not in UNRESOLVABLE_RULES
                  and (f.get("severity") in ("High", "Medium") or f.get("finding_status") in BLOCKING_STATUSES)]
    if open_items:
        blockers.append(f"{len(open_items)} unresolved finding(s) need a reviewer resolution.")
    approver_norm = (approver or "").strip().lower()
    if approver_norm and approver_norm in set(resolvers):
        blockers.append("Two-person rule: the approver must be different from the reviewer who confirmed the bank change.")
    return blockers


def current_recommendation(findings):
    return recommend(findings)


def record_decision(connection, vendor, submission, decision, reviewer, rationale, findings, docs, extracted):
    reviewer, rationale = (reviewer or "").strip(), (rationale or "").strip()
    if not reviewer or not rationale:
        raise ValueError("Reviewer name and rationale are mandatory.")
    if decision not in ("Approved", "Rejected"):
        raise ValueError("Decision must be Approved or Rejected.")
    vendor_id, submission_id = vendor["id"], submission["id"]
    if decision == "Approved":
        blockers = approval_blockers(findings, docs, extracted, submission, reviewer,
                                     bank_change_resolvers(connection, submission_id))
        if blockers:
            raise ValueError("Approval blocked: " + " ".join(blockers))
        row = connection.execute(
            "SELECT snapshot_json FROM snapshots WHERE vendor_id=? AND submission_id=? ORDER BY id DESC LIMIT 1",
            (vendor_id, submission_id)).fetchone()
        proposed = json.loads(row["snapshot_json"]) if row else {}
        connection.execute(
            """UPDATE vendors SET legal_name=?,pan=?,gstin=?,email=?,phone=?,address=?,bank_account=?,ifsc=?,
               status=?,risk_score=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            tuple(proposed.get(k, vendor[k]) for k in ("legal_name", "pan", "gstin", "email", "phone",
                                                       "address", "bank_account", "ifsc"))
            + (decision, submission["risk_score"], vendor_id))
    elif vendor["status"] != "Approved":
        # Rejecting a change request never revokes an already-approved master.
        connection.execute("UPDATE vendors SET status=?,risk_score=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                           (decision, submission["risk_score"], vendor_id))
    safe_rationale = mask_free_text(rationale)
    recommendation = recommend(findings)
    connection.execute("UPDATE submissions SET status=?, decision=?, reviewer=?, review_note=? WHERE id=?",
                       (decision, decision, reviewer, safe_rationale, submission_id))
    connection.execute(
        "INSERT INTO decisions(vendor_id,submission_id,decision,risk_score,rationale,reviewer) VALUES(?,?,?,?,?,?)",
        (vendor_id, submission_id, decision, submission["risk_score"], safe_rationale, reviewer))
    audit(connection, vendor_id, submission_id, "HUMAN_REVIEW",
          {"decision": decision, "engine_recommendation": recommendation["label"],
           "agrees_with_engine": recommendation["outcome"] == decision, "rationale": safe_rationale,
           "reviewer": reviewer})


def send_vendor_message(connection, vendor, submission_id, reviewer):
    """Regenerate the consolidated message from the current open findings and mark it Sent."""
    reviewer = (reviewer or "").strip()
    if not reviewer:
        raise ValueError("Enter your reviewer name / ID before sending a request to the vendor.")
    findings = [dict(r) for r in connection.execute("SELECT * FROM findings WHERE submission_id=?", (submission_id,))]
    message = build_vendor_message(vendor["legal_name"], findings, recommend(findings))
    if not message:
        raise ValueError("There is nothing to request from the vendor for this submission.")
    connection.execute("UPDATE vendor_messages SET status='Superseded' WHERE submission_id=? AND audience='vendor' "
                       "AND status='Draft'", (submission_id,))
    connection.execute(
        """INSERT INTO vendor_messages(vendor_id,submission_id,audience,message_text,status,sent_by,sent_at)
           VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
        (vendor["id"], submission_id, "vendor", message, "Sent", reviewer))
    open_count = connection.execute(
        "UPDATE document_requests SET status='Open' WHERE vendor_id=? AND submission_id=? AND status='Suggested'",
        (vendor["id"], submission_id)).rowcount
    audit(connection, vendor["id"], submission_id, "VENDOR_MESSAGE_SENT",
          {"reviewer": reviewer, "request_count": open_count, "message": message})
    return message
