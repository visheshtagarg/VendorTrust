import json
import os
import shutil
import time
import uuid
from pathlib import Path

import streamlit as st
import pandas as pd
import html


def _vendortrust_safe_dataframe(data, **kwargs):
    """Render tabular data as HTML to avoid Streamlit's PyArrow DLL dependency."""
    if isinstance(data, pd.DataFrame):
        frame = data.copy()
    else:
        frame = pd.DataFrame(data)
    if kwargs.get("hide_index", False):
        frame = frame.reset_index(drop=True)
    table_html = frame.to_html(index=not kwargs.get("hide_index", False), escape=True,
                               border=0, classes="vendortrust-html-table")
    # The HTML must start at column 0: Markdown treats lines indented by 4+
    # spaces as a code block and would show the raw <table> tags as text.
    style = (
        "<style>"
        ".vendortrust-html-table {width:100%; border-collapse:collapse; font-size:0.88rem;}"
        ".vendortrust-html-table th {background:#F1F5F9; color:#475569; font-weight:650;"
        " text-align:left; padding:9px 11px; border:1px solid #DCE4EE;}"
        ".vendortrust-html-table td {padding:9px 11px; border:1px solid #E2E8F0;"
        " color:#172337; vertical-align:top;}"
        ".vendortrust-html-table tr:nth-child(even) td {background:#F8FAFC;}"
        "</style>"
    )
    st.markdown(style + "\n\n" + table_html, unsafe_allow_html=True)


# Replace Streamlit's Arrow-backed dataframe renderer throughout this prototype.
st.dataframe = _vendortrust_safe_dataframe

from config import ROOT, DATA, UPLOADS, MAX_UPLOAD_MB, DB
from database.connection import db, init_db, set_db_path
from core.engine import (
    BANK_CHANGE_RULES, BLOCKING_STATUSES, DocumentError, UNRESOLVABLE_RULES, evaluate_profile, is_resolved,
)
from core.privacy import mask, mask_free_text, mask_record, mask_records
from modules.adaptive_requests import build_internal_tasks, build_vendor_message
from services.bulk import (
    BulkError, build_bulk_template, duplicate_case_ids, parse_cases_workbook, process_bulk_case, read_pdf_zip,
)
from services.submission import (
    REQUIRED_DOC_TYPES, approval_blockers, bank_change_resolvers, current_recommendation, find_vendor,
    normalize_profile, prepare_documents, record_decision, resolve_finding, save_submission, send_vendor_message,
)

st.set_page_config(page_title="VendorTrust", page_icon="🛡️", layout="wide")

# --- VendorTrust visual layer: CSS-only; workflow and business logic unchanged ---
st.markdown("""
<style>
:root {
  --vt-navy: #14243A;
  --vt-blue: #2563EB;
  --vt-teal: #0F9488;
  --vt-ink: #172337;
  --vt-muted: #64748B;
  --vt-line: #E2E8F0;
  --vt-surface: #FFFFFF;
  --vt-canvas: #F4F7FB;
}
.stApp {
  background:
    radial-gradient(ellipse at 82% 0%, rgba(37,99,235,.055), transparent 34%),
    var(--vt-canvas);
  color: var(--vt-ink);
}
[data-testid="stHeader"] { background: rgba(244,247,251,.88); }
[data-testid="stToolbar"] { right: 1.5rem; }
.block-container {
  padding-top: 2.1rem;
  padding-bottom: 3rem;
  max-width: 1440px;
}
h1, h2, h3 {
  color: var(--vt-navy) !important;
  letter-spacing: -0.035em;
}
h1 { font-weight: 800 !important; }
h2 { font-weight: 730 !important; }
h3 { font-weight: 680 !important; }
p, label, .stCaption { color: var(--vt-ink); }
small, [data-testid="stCaptionContainer"] { color: var(--vt-muted) !important; }

/* Sidebar */
section[data-testid="stSidebar"] {
  background: linear-gradient(180deg, #14243A 0%, #1D3554 58%, #173B50 100%);
  border-right: 1px solid rgba(255,255,255,.10);
}
section[data-testid="stSidebar"] * { color: #F8FAFC !important; }
section[data-testid="stSidebar"] h1,
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3 {
  color: #FFFFFF !important;
}
section[data-testid="stSidebar"] [data-testid="stRadio"] label {
  padding: .42rem .65rem;
  border-radius: 10px;
  margin: .12rem 0;
  transition: background .18s ease;
}
section[data-testid="stSidebar"] [data-testid="stRadio"] label:hover {
  background: rgba(255,255,255,.10);
}
section[data-testid="stSidebar"] [data-testid="stRadio"] [aria-checked="true"] + div {
  color: #FFFFFF !important;
}

/* Metric tiles */
[data-testid="stMetric"] {
  background: linear-gradient(145deg, #FFFFFF, #F8FBFF);
  border: 1px solid var(--vt-line);
  border-radius: 16px;
  padding: 18px 20px;
  box-shadow: 0 5px 18px rgba(15, 35, 60, .045);
}
[data-testid="stMetricLabel"] {
  color: var(--vt-muted) !important;
  font-weight: 650;
}
[data-testid="stMetricValue"] {
  color: var(--vt-navy) !important;
  font-weight: 800;
}

/* Inputs and select boxes */
.stTextInput input, .stTextArea textarea, .stNumberInput input,
.stDateInput input, [data-baseweb="select"] > div {
  border-radius: 10px !important;
  border-color: #D6DFEA !important;
  background: #FFFFFF !important;
}
.stTextInput input:focus, .stTextArea textarea:focus,
.stNumberInput input:focus {
  border-color: var(--vt-blue) !important;
  box-shadow: 0 0 0 2px rgba(37,99,235,.12) !important;
}
[data-testid="stFileUploader"] section {
  background: #F8FAFC;
  border: 1.5px dashed #B8C7D9;
  border-radius: 14px;
}

/* Buttons */
.stButton > button, .stDownloadButton > button, button[kind="secondary"] {
  border-radius: 10px !important;
  border: 1px solid #CBD5E1 !important;
  font-weight: 650 !important;
  padding: .48rem 1rem !important;
  transition: transform .15s ease, box-shadow .15s ease, background .15s ease;
}
.stButton > button[kind="primary"], .stButton > button:focus-visible {
  background: linear-gradient(110deg, #2563EB, #0F9488) !important;
  color: #FFFFFF !important;
  border: 0 !important;
}
.stButton > button:hover, .stDownloadButton > button:hover {
  transform: translateY(-1px);
  box-shadow: 0 7px 18px rgba(20,36,58,.12);
  border-color: #8EA7C3 !important;
}

/* Tables, alerts, expanders */
[data-testid="stDataFrame"], [data-testid="stTable"] {
  border: 1px solid var(--vt-line);
  border-radius: 12px;
  overflow: hidden;
  background: #FFFFFF;
}
[data-testid="stAlert"] {
  border-radius: 12px;
  border-width: 1px;
}
details[data-testid="stExpander"] {
  background: #FFFFFF;
  border: 1px solid var(--vt-line);
  border-radius: 12px;
  overflow: hidden;
}
details[data-testid="stExpander"] summary {
  font-weight: 650;
}
hr { border-color: var(--vt-line); }
a { color: #1D4ED8 !important; }
[data-testid="stProgressBar"] > div > div { background: var(--vt-teal); }
</style>
""", unsafe_allow_html=True)


SESSIONS_DIR = DATA / "sessions"
SESSION_MAX_IDLE_SECONDS = 6 * 60 * 60


@st.cache_resource
def _start_fresh():
    """Wipe saved data once per app start, so every launch begins empty.

    st.cache_resource makes this run only once per server process. Streamlit
    reruns the script on every click, so without it data would be erased mid-demo.
    """
    for suffix in ("", "-wal", "-shm"):
        Path(f"{DB}{suffix}").unlink(missing_ok=True)
    for item in UPLOADS.iterdir():
        if item.name == ".gitkeep":
            continue
        if item.is_dir():
            shutil.rmtree(item, ignore_errors=True)
        else:
            item.unlink(missing_ok=True)
    shutil.rmtree(SESSIONS_DIR, ignore_errors=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    return True


def _remove_idle_sessions():
    """Delete session folders nobody has used for a while, so the disk does not fill up."""
    now = time.time()
    for folder in SESSIONS_DIR.iterdir():
        try:
            if folder.is_dir() and now - folder.stat().st_mtime > SESSION_MAX_IDLE_SECONDS:
                shutil.rmtree(folder, ignore_errors=True)
        except FileNotFoundError:
            pass


_start_fresh()
# Every browser session (new tab, page refresh) starts with its own empty
# database and uploads folder. Data stays while that tab is in use and is never
# visible to other viewers.
if "vt_session_dir" not in st.session_state:
    _remove_idle_sessions()
    st.session_state["vt_session_dir"] = str(SESSIONS_DIR / uuid.uuid4().hex)
SESSION_DIR = Path(st.session_state["vt_session_dir"])
SESSION_UPLOADS = SESSION_DIR / "uploads"
SESSION_UPLOADS.mkdir(parents=True, exist_ok=True)
os.utime(SESSION_DIR)  # mark the session as active
set_db_path(SESSION_DIR / "vendortrust.db")
init_db()
# Run legacy duplicate-request cleanup once per database, not on every Streamlit rerun.
with db() as _connection:
    already_cleaned = _connection.execute("SELECT 1 FROM schema_version WHERE version=2").fetchone()
    if not already_cleaned:
        before_count = _connection.execute("SELECT COUNT(*) FROM document_requests").fetchone()[0]
        _connection.execute("""
            DELETE FROM document_requests
            WHERE id NOT IN (
                SELECT MIN(id) FROM document_requests
                GROUP BY vendor_id, submission_id, request_text
            )
        """)
        after_count = _connection.execute("SELECT COUNT(*) FROM document_requests").fetchone()[0]
        _connection.execute("INSERT OR IGNORE INTO schema_version(version) VALUES (2)")
        _connection.execute(
            "INSERT INTO audit_events(vendor_id,submission_id,event_type,details) VALUES(NULL,NULL,?,?)",
            ("DOCUMENT_REQUEST_DEDUPLICATION", json.dumps({"deleted_rows": before_count - after_count,
             "dedupe_key": "vendor_id + submission_id + request_text"}))
        )

REQUIRED_DOCS = {
    "PAN Card": "PAN card / PAN allotment document establishing the legal entity's PAN.",
    "GST Certificate": "Current GST registration certificate showing GSTIN, legal name and registered address.",
    "Bank Proof": "Cancelled cheque or bank statement showing account holder, account number and IFSC.",
}
SENSITIVE_PROFILE_KEYS = ("pan", "gstin", "bank_account", "phone", "email", "address")
OUTCOME_STYLE = {"Rejected": st.error, "Pending": st.warning, "Approved": st.success}

st.sidebar.title("🛡️ VendorTrust")
page = st.sidebar.radio(
    "Workspace",
    ["Overview", "New Onboarding", "Bulk Upload", "Vendor Explorer", "Evidence Center",
     "Identity Graph", "Digital Twin", "Change Monitor", "Policy Lab",
     "Review Center", "Privacy Center", "Audit Trail"]
)


def rows(query, params=()):
    with db() as connection:
        return [dict(row) for row in connection.execute(query, params).fetchall()]


def audit(vendor_id, submission_id, event_type, details):
    with db() as connection:
        connection.execute(
            "INSERT INTO audit_events(vendor_id,submission_id,event_type,details) VALUES(?,?,?,?)",
            (vendor_id, submission_id, event_type, mask_free_text(json.dumps(details)))
        )


def vendor_list():
    return rows("SELECT * FROM vendors ORDER BY id DESC")


def show_recommendation(recommendation, heading="Engine recommendation"):
    style = OUTCOME_STYLE.get(recommendation["outcome"], st.info)
    style(f"**{heading}: {recommendation['label']}**  \nProblem-statement outcome: **{recommendation['outcome']}**")
    with st.expander("Why the engine recommends this", expanded=recommendation["outcome"] != "Approved"):
        for reason in recommendation["reasons"]:
            st.markdown(f"- {reason}")


def show_result(result):
    """Render the outcome of a saved submission (form or bulk)."""
    if result["is_resubmission"]:
        st.info(f"This PAN already exists, so this was saved as a new submission for {result['vendor_code']}. "
                "An approved vendor master is not changed until a reviewer approves the new submission.")
    st.success(f"Submission saved for {result['vendor_code']}.")
    st.metric("Heuristic risk score", f"{result['score']}/100")
    show_recommendation(result["recommendation"])
    if result.get("entity_type"):
        st.caption(f"PAN holder type: {result['entity_type']}")
    st.info("External Verification: Not Connected. This checks uploaded document content and consistency only; "
            "it does not authenticate PAN, GST or bank records. A human reviewer signs off every decision.")
    if result["findings"]:
        st.subheader("Verification findings")
        st.dataframe(mask_records(result["findings"]), use_container_width=True, hide_index=True)
    if result["vendor_message"]:
        st.subheader("Message to the vendor (draft)")
        st.text_area("Consolidated request", result["vendor_message"], height=220, disabled=True,
                     key=f"msg_{result['submission_id']}")
        st.caption("A reviewer sends this from the Review Center. It contains no internal rule codes.")
    if result["internal_tasks"]:
        st.subheader("Internal tasks (not sent to the vendor)")
        for task in result["internal_tasks"]:
            st.markdown(f"- {task}")


if page == "Overview":
    st.title("VendorTrust")
    st.caption("Evidence-driven vendor onboarding and continuous trust intelligence")
    vendors = vendor_list()
    cols = st.columns(4)
    cols[0].metric("Total vendors", len(vendors))
    cols[1].metric("Human approved", sum(v["status"] == "Approved" for v in vendors))
    cols[2].metric("Pending / needs action", sum(v["status"] not in ("Approved", "Rejected", "Recommend Reject")
                                                 for v in vendors))
    cols[3].metric("Rejected / recommended reject",
                   sum(v["status"] in ("Rejected", "Recommend Reject") for v in vendors))
    st.caption("Engine outcomes: Approve-ready → Approved after human sign-off · Needs Documents / Pending Review → "
               "Pending · Recommend Reject → Rejected after human sign-off.")
    if vendors:
        st.dataframe(mask_records(vendors), use_container_width=True, hide_index=True)
    else:
        st.info("No vendors onboarded yet.")

elif page == "Bulk Upload":
    import pandas as pd

    st.title("Excel + PDF Bulk Onboarding")
    st.caption("Upload one Excel workbook containing vendor cases and one ZIP containing the mapped PDF evidence.")
    c1, c2, c3 = st.columns(3)
    c1.download_button("Download Excel template", data=build_bulk_template(),
                       file_name="VendorTrust_Bulk_Upload_Template.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    sample_dir = ROOT / "samples" / "bulk"
    if (sample_dir / "VendorTrust_Bulk_Sample.xlsx").exists():
        c2.download_button("Download sample workbook (4 cases)",
                           data=(sample_dir / "VendorTrust_Bulk_Sample.xlsx").read_bytes(),
                           file_name="VendorTrust_Bulk_Sample.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if (sample_dir / "VendorTrust_Bulk_Sample_PDFs.zip").exists():
        c3.download_button("Download sample PDF ZIP",
                           data=(sample_dir / "VendorTrust_Bulk_Sample_PDFs.zip").read_bytes(),
                           file_name="VendorTrust_Bulk_Sample_PDFs.zip", mime="application/zip")
    st.markdown("""
    **How to prepare your files**
    1. Fill the `Cases` sheet: one row per case. Use `NEW` for a new vendor and `UPDATE` for an existing vendor.
       `NEW` with a PAN that already exists is rejected; use `UPDATE`.
    2. Put exactly three PDFs per case in a ZIP file, named `CASEID_PAN.pdf`, `CASEID_GST.pdf`, and `CASEID_BANK.pdf`.
       Filenames must be unique across the whole ZIP, including subfolders.
    3. Limits: up to 300 PDFs, 20 MB per PDF, 200 MB uncompressed in total.
    4. Each row is processed independently; an error in one case does not stop the others.
    """)
    excel_file = st.file_uploader("Vendor cases workbook (.xlsx)", type=["xlsx"], key="bulk_excel")
    pdf_zip = st.file_uploader("Supporting PDF documents (.zip)", type=["zip"], key="bulk_zip")
    if st.button("Run bulk verification", type="primary", disabled=(excel_file is None or pdf_zip is None)):
        results = []
        try:
            cases = parse_cases_workbook(excel_file.getvalue())
            pdf_map = read_pdf_zip(pdf_zip.getvalue())
        except BulkError as exc:
            st.error(str(exc))
            cases = []
        if cases:
            duplicates = duplicate_case_ids(cases)
            progress, status = st.progress(0), st.empty()
            for index, case in enumerate(cases, start=1):
                case_id = case.get("Case_ID", "").strip()
                status.write(f"Processing {index}/{len(cases)}: {case_id or 'Missing Case_ID'}")
                try:
                    if case_id.upper() in duplicates:
                        raise BulkError("Duplicate Case_ID in workbook. Each Case_ID must be unique within a batch.")
                    with db() as connection:  # one transaction per case
                        results.append(process_bulk_case(connection, case, pdf_map, SESSION_UPLOADS, MAX_UPLOAD_MB))
                except (BulkError, DocumentError, ValueError) as exc:
                    results.append({"Case_ID": case_id, "Result": "Error", "Error": str(exc)})
                progress.progress(index / len(cases))
            status.success("Bulk processing finished.")
            st.session_state["bulk_results"] = results
    if st.session_state.get("bulk_results"):
        st.subheader("Batch results")
        result_df = pd.DataFrame(st.session_state["bulk_results"])
        st.dataframe(result_df, use_container_width=True, hide_index=True)
        st.download_button("Download batch results (CSV)", data=result_df.to_csv(index=False).encode("utf-8"),
                           file_name="VendorTrust_Bulk_Results.csv", mime="text/csv")
        st.info("Cases are saved as submissions. Open the Review Center to resolve findings, send vendor "
                "requests and record the human decision.")

elif page == "New Onboarding":
    st.title("New Vendor Onboarding")
    st.markdown("### Required supporting documents")
    st.caption("Upload genuine, legible supporting documents. TXT files are not accepted in normal onboarding.")
    for doc_type, description in REQUIRED_DOCS.items():
        st.markdown(f"- **{doc_type}** — {description}")
    st.info(f"Accepted format: PDF only · Maximum size: {MAX_UPLOAD_MB} MB per file. Scanned PDFs may require "
            "manual review because OCR is not enabled. A different file is required for each slot.")

    with st.form("onboard"):
        st.subheader("Vendor information")
        name = st.text_input("Legal name *")
        pan = st.text_input("PAN *")
        gst = st.text_input("GSTIN *")
        email = st.text_input("Email")
        phone = st.text_input("Phone")
        address = st.text_area("Registered address")
        bank = st.text_input("Bank account *")
        ifsc = st.text_input("IFSC *")
        st.subheader("Upload documents")
        uploads = {}
        for doc_type, description in REQUIRED_DOCS.items():
            uploads[doc_type] = st.file_uploader(f"{doc_type} (PDF) *", type=["pdf"], accept_multiple_files=False,
                                                 key=f"upload_{doc_type}", help=description)
        submitted = st.form_submit_button("Run document checks", type="primary")

    if submitted:
        profile = normalize_profile({"legal_name": name, "pan": pan, "gstin": gst, "email": email, "phone": phone,
                                     "address": address, "bank_account": bank, "ifsc": ifsc})
        errors = [f"{label} is required." for label, key in
                  (("Legal name", "legal_name"), ("PAN", "pan"), ("GSTIN", "gstin"), ("Bank account", "bank_account"),
                   ("IFSC", "ifsc")) if not profile[key]]
        errors += [f"{doc_type} is mandatory." for doc_type, uploaded in uploads.items() if uploaded is None]
        if errors:
            for error in errors:
                st.error(error)
        else:
            try:
                prepared = prepare_documents([(doc_type, uploaded.name, uploaded.getvalue())
                                              for doc_type, uploaded in uploads.items()], MAX_UPLOAD_MB)
            except DocumentError as exc:
                st.error(str(exc))
                prepared = []
            if prepared:
                with db() as connection:
                    existing = find_vendor(connection, pan=profile["pan"])
                    result = save_submission(connection, profile, prepared, existing, SESSION_UPLOADS, source="ONLINE_FORM")
                show_result(result)

elif page == "Vendor Explorer":
    st.title("Vendor Explorer")
    vendors = vendor_list()
    if vendors:
        selected = st.selectbox("Select vendor", vendors, format_func=lambda v: f"{v['vendor_code']} — {v['legal_name']}")
        st.json(mask_record(selected))
        with st.form("reveal_vendor_form"):
            actor = st.text_input("Your name / reviewer ID", key="reveal_actor")
            reveal_clicked = st.form_submit_button("Reveal sensitive vendor details")
        if reveal_clicked:
            if actor.strip():
                with db() as connection:
                    connection.execute(
                        "INSERT INTO access_logs(actor,action,vendor_id,field_name) VALUES(?,?,?,?)",
                        (actor.strip(), "REVEAL_VENDOR_PII", selected["id"], "PAN,GSTIN,bank_account,phone,email,address")
                    )
                st.json(selected)
                st.success("Reveal action logged in Privacy Center.")
            else:
                st.warning("Enter your name / reviewer ID to reveal.")
        findings = rows("SELECT * FROM findings WHERE vendor_id=? ORDER BY id DESC", (selected["id"],))
        st.subheader("Findings")
        st.dataframe(mask_records(findings), use_container_width=True, hide_index=True)
    else:
        st.info("No vendors available.")

elif page == "Evidence Center":
    st.title("Evidence Center")
    st.caption("Uploaded documents, extracted fields and verification findings.")
    vendors = vendor_list()
    if not vendors:
        st.info("No vendors have been onboarded yet.")
    else:
        selected = st.selectbox(
            "Select vendor", vendors,
            format_func=lambda v: f"{v['vendor_code']} — {v['legal_name']}",
            key="evidence_vendor_select",
        )
        vendor_id = selected["id"]
        submissions = rows(
            "SELECT * FROM submissions WHERE vendor_id=? ORDER BY id DESC", (vendor_id,)
        )
        if submissions:
            submission_options = submissions
            current_submission = st.selectbox(
                "Submission", submission_options,
                format_func=lambda x: f"Submission #{x['id']} · {x['status']} · {x['submitted_at']}",
                key="evidence_submission_select",
            )
            submission_id = current_submission["id"]
        else:
            submission_id = None
            st.warning("Vendor record exists, but no onboarding submission is linked to it.")

        st.subheader("1. Uploaded documents")
        documents = rows(
            "SELECT id, doc_type, filename, stored_path, uploaded_at FROM documents "
            "WHERE vendor_id=? AND submission_id=? ORDER BY id",
            (vendor_id, submission_id),
        ) if submission_id else []
        if documents:
            st.dataframe(
                [{"Document type": d["doc_type"], "Filename": d["filename"],
                  "Uploaded at": d["uploaded_at"], "Stored file exists": Path(d["stored_path"]).exists() if d.get("stored_path") else False}
                 for d in documents],
                use_container_width=True, hide_index=True,
            )
        else:
            st.warning("No documents are recorded for this submission.")

        st.subheader("2. Extracted fields")
        extracted = rows(
            "SELECT d.doc_type, d.filename, ef.field_name, ef.field_value, "
            "ef.page_number, ef.confidence, ef.source "
            "FROM extracted_fields ef JOIN documents d ON d.id=ef.document_id "
            "WHERE ef.vendor_id=? AND d.submission_id=? ORDER BY d.id, ef.id",
            (vendor_id, submission_id),
        ) if submission_id else []
        if extracted:
            st.dataframe(mask_records(extracted), use_container_width=True, hide_index=True)
        elif documents:
            st.warning("Documents are present, but no text fields were extracted. Scanned PDFs may require manual review; OCR is not enabled.")
        else:
            st.info("No extracted fields are available.")

        st.subheader("3. Verification findings")
        findings = rows(
            "SELECT field_name, observed_value, expected_value, rule_id, severity, "
            "finding_status, explanation, source_document, source_page, confidence, recommended_action "
            "FROM findings WHERE vendor_id=? AND submission_id=? ORDER BY id",
            (vendor_id, submission_id),
        ) if submission_id else []
        if findings:
            st.dataframe(mask_records(findings), use_container_width=True, hide_index=True)
        elif documents:
            st.success("Checks recorded no exceptions for this submission. This is not external verification.")
        else:
            st.info("Findings will appear after a submission has been checked.")

        if submission_id and (extracted or findings):
            with st.expander("Authorized evidence reveal"):
                evidence_actor = st.text_input("Reviewer name / ID", key="evidence_reveal_actor")
                if st.button("Reveal evidence values", key="reveal_evidence_button"):
                    if not evidence_actor.strip():
                        st.warning("Enter reviewer name / ID before revealing evidence.")
                    else:
                        with db() as connection:
                            connection.execute(
                                "INSERT INTO access_logs(actor,action,vendor_id,field_name) VALUES(?,?,?,?)",
                                (evidence_actor.strip(), "REVEAL_SUBMISSION_EVIDENCE", vendor_id,
                                 f"submission_id={submission_id}; extracted_fields; findings")
                            )
                        st.subheader("Unmasked extracted fields")
                        st.dataframe(extracted, use_container_width=True, hide_index=True)
                        st.subheader("Unmasked findings")
                        st.dataframe(findings, use_container_width=True, hide_index=True)
                        st.success("Evidence reveal recorded in Privacy Center.")

        st.subheader("4. Verification boundary")
        st.info("External PAN, GST and bank verification is not connected. Successful PDF extraction does not authenticate a document or prove that the vendor is genuine.")

elif page == "Identity Graph":
    st.title("Vendor Identity Graph")
    vendors = vendor_list()
    if vendors:
        import networkx as nx
        import plotly.graph_objects as go
        graph = nx.Graph()
        attribute_owners = {}
        for vendor in vendors:
            vendor_node = f"{vendor['vendor_code']} — {vendor['legal_name']}"
            graph.add_node(vendor_node, kind="vendor")
            for field in ("pan", "gstin", "bank_account", "phone", "email", "address"):
                value = str(vendor.get(field) or "").strip().upper()
                if value:
                    attribute_owners.setdefault((field, value), []).append(vendor_node)
        shared = {key: owners for key, owners in attribute_owners.items() if len(owners) > 1}
        for (field, value), owners in shared.items():
            attr_node = f"{field.upper()}: {mask(value) if field in ('pan', 'gstin', 'bank_account', 'phone', 'email', 'address') else value}"
            graph.add_node(attr_node, kind="shared_attribute")
            for owner in owners:
                graph.add_edge(owner, attr_node)
        if shared:
            pos = nx.spring_layout(graph, seed=7)
            edge_x, edge_y = [], []
            for a, b in graph.edges:
                edge_x += [pos[a][0], pos[b][0], None]
                edge_y += [pos[a][1], pos[b][1], None]
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=edge_x, y=edge_y, mode="lines", hoverinfo="none"))
            fig.add_trace(go.Scatter(
                x=[pos[n][0] for n in graph.nodes],
                y=[pos[n][1] for n in graph.nodes],
                text=list(graph.nodes), mode="markers+text", textposition="top center"
            ))
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe([{"attribute": f, "shared_value": mask(v) if f in ("pan", "gstin", "bank_account", "phone", "email", "address") else v,
                           "vendor_count": len(owners), "vendors": ", ".join(owners)}
                          for (f, v), owners in shared.items()], use_container_width=True, hide_index=True)
        else:
            st.info("No shared identity attributes found across vendors.")
        st.caption("A shared attribute is an investigation signal, not proof of fraud.")
    else:
        st.info("Add vendors to build the identity graph.")

elif page == "Digital Twin":
    st.title("Vendor Digital Twin")
    vendors = vendor_list()
    if vendors:
        selected = st.selectbox("Vendor scenario", vendors, format_func=lambda v: f"{v['vendor_code']} — {v['legal_name']}")
        st.caption("What-if evaluation starts from the latest submitted snapshot and stored evidence. The approved vendor master remains unchanged.")
        field = st.selectbox("Change attribute", ["legal_name", "pan", "gstin", "bank_account", "ifsc", "address"])
        value = st.text_input("Simulated value")
        if st.button("Simulate scenario", disabled=not value.strip()):
            latest_submission = rows(
                "SELECT id, risk_score FROM submissions WHERE vendor_id=? ORDER BY id DESC LIMIT 1",
                (selected["id"],),
            )
            if not latest_submission:
                st.warning("No submitted evidence is available for this vendor. Submit a case before running a what-if evaluation.")
            else:
                sid = latest_submission[0]["id"]
                snapshot_rows = rows(
                    "SELECT snapshot_json FROM snapshots WHERE vendor_id=? AND submission_id=? ORDER BY id DESC LIMIT 1",
                    (selected["id"], sid),
                )
                if not snapshot_rows:
                    st.error("The latest submission has no stored snapshot; simulation cannot be run safely.")
                else:
                    # Use latest submitted state, not the approved master, as baseline.
                    scenario = json.loads(snapshot_rows[0]["snapshot_json"] or "{}")
                    if not isinstance(scenario, dict) or not scenario:
                        st.error("The latest submission snapshot is invalid; simulation cannot be run.")
                    else:
                        scenario[field] = value.strip()
                        evidence_docs = rows(
                            "SELECT id, doc_type, filename FROM documents WHERE vendor_id=? AND submission_id=? ORDER BY id",
                            (selected["id"], sid),
                        )
                        scenario_documents = []
                        for doc in evidence_docs:
                            fields = rows(
                                "SELECT field_name, field_value, page_number, confidence, source FROM extracted_fields WHERE document_id=? ORDER BY id",
                                (doc["id"],),
                            )
                            scenario_documents.append({
                                "doc_type": doc["doc_type"], "filename": doc["filename"],
                                "fields": fields, "needs_manual_review": not bool(fields),
                            })
                        approved = selected.get("status") == "Approved"
                        simulated_score, simulated_status, simulated_findings, _ = evaluate_profile(
                            scenario, scenario_documents,
                            existing_bank_account=selected.get("bank_account") if approved else None,
                            existing_contact=({"email": selected.get("email"), "phone": selected.get("phone")}
                                              if approved else None),
                        )
                        base_score = int(latest_submission[0].get("risk_score") or 0)
                        st.metric("Simulated heuristic risk score", f"{simulated_score}/100", delta=simulated_score-base_score)
                        st.write({
                            "simulated_status": simulated_status,
                            "changed_field": field,
                            "simulated_value": mask(value) if field in ("pan", "gstin", "bank_account", "phone", "email", "address") else value,
                        })
                        if simulated_findings:
                            st.dataframe(mask_records(simulated_findings), use_container_width=True, hide_index=True)
                        else:
                            st.success("No rule-based discrepancies were found against the stored evidence.")
                        # Persist only the changed field name, never raw identifiers.
                        with db() as connection:
                            connection.execute(
                                "INSERT INTO simulations(vendor_id,scenario_json,base_score,simulated_score,simulated_decision) VALUES(?,?,?,?,?)",
                                (selected["id"], json.dumps({"changed_field": field}),
                                 base_score, simulated_score, simulated_status)
                            )
                            connection.execute(
                                "INSERT INTO audit_events(vendor_id,submission_id,event_type,details) VALUES(?,?,?,?)",
                                (selected["id"], sid, "DIGITAL_TWIN_SIMULATION", json.dumps({
                                    "changed_field": field, "simulated_score": simulated_score,
                                    "simulated_status": simulated_status,
                                }))
                            )
                        st.caption("Simulation saved without storing raw identifiers. It does not alter the vendor's actual status or master data.")
    else:
        st.info("No vendors available.")

elif page == "Change Monitor":
    st.title("Change Monitor")
    vendors = vendor_list()
    if vendors:
        selected = st.selectbox("Vendor", vendors, format_func=lambda v: f"{v['vendor_code']} — {v['legal_name']}")
        snapshots = rows("SELECT snapshot_json,created_at FROM snapshots WHERE vendor_id=? ORDER BY id", (selected["id"],))
        if len(snapshots) >= 2:
            old, new = json.loads(snapshots[-2]["snapshot_json"]), json.loads(snapshots[-1]["snapshot_json"])
            changes = [{"field": key, "before": mask(old.get(key)) if key in ("pan", "gstin", "bank_account", "phone", "email", "address") else old.get(key),
                        "after": mask(new.get(key)) if key in ("pan", "gstin", "bank_account", "phone", "email", "address") else new.get(key)}
                       for key in new if old.get(key) != new.get(key)]
            st.dataframe(changes, use_container_width=True, hide_index=True)
        else:
            st.info("A resubmission is needed to compare historical snapshots.")
    else:
        st.info("No vendors available.")

elif page == "Policy Lab":
    st.title("Policy Lab")
    vendors = vendor_list()
    threshold = st.slider("Manual review risk threshold", 0, 100, 25)
    st.caption("Illustrative policy simulation only. It does not change stored vendor decisions. "
               "Rule-based reject recommendations always apply regardless of the threshold.")
    if st.button("Run policy simulation"):
        results = []
        for vendor in vendors:
            if vendor["status"] in ("Recommend Reject", "Rejected"):
                result = vendor["status"]
            else:
                result = "Pending Review" if (vendor["risk_score"] or 0) >= threshold else "Approve-ready"
            results.append({"vendor": vendor["legal_name"], "risk_score": vendor["risk_score"],
                            "current_status": vendor["status"], "simulated_result": result})
        st.dataframe(results, use_container_width=True, hide_index=True)

elif page == "Review Center":
    st.title("Human Review Center")
    pending = rows(
        """SELECT v.* FROM vendors v
           WHERE EXISTS (
               SELECT 1 FROM submissions s
               WHERE s.vendor_id=v.id
                 AND s.id=(SELECT MAX(s2.id) FROM submissions s2 WHERE s2.vendor_id=v.id)
                 AND COALESCE(s.decision,'') NOT IN ('Approved','Rejected'))
           ORDER BY v.id DESC""")
    if not pending:
        st.info("No cases are currently awaiting human review.")
    else:
        acting_reviewer = st.text_input(
            "Acting reviewer name / ID (required for every action on this page)", key="acting_reviewer").strip()
        selected = st.selectbox("Case to review", pending,
                                format_func=lambda v: f"{v['vendor_code']} — {v['legal_name']} ({v['status']})",
                                key="review_vendor_select")
        vendor_id = selected["id"]
        current_submission = rows("SELECT * FROM submissions WHERE vendor_id=? ORDER BY id DESC LIMIT 1",
                                  (vendor_id,))[0]
        submission_id = current_submission["id"]
        st.info(f"Reviewing latest submission #{submission_id} · source {current_submission.get('source') or 'ONLINE_FORM'}"
                f" · submitted {current_submission['submitted_at']}"
                + (" · vendor master is currently **Approved** (this is a change request)"
                   if selected["status"] == "Approved" else ""))

        docs = rows("SELECT id, doc_type, filename, stored_path, uploaded_at, sha256 FROM documents "
                    "WHERE vendor_id=? AND submission_id=? ORDER BY id", (vendor_id, submission_id))
        extracted = rows(
            "SELECT d.doc_type, d.filename, ef.field_name, ef.field_value, ef.page_number, ef.confidence, ef.source "
            "FROM extracted_fields ef JOIN documents d ON d.id=ef.document_id "
            "WHERE ef.vendor_id=? AND d.submission_id=? ORDER BY d.id, ef.id", (vendor_id, submission_id))
        findings = rows("SELECT * FROM findings WHERE vendor_id=? AND submission_id=? ORDER BY id",
                        (vendor_id, submission_id))

        recommendation = current_recommendation(findings)
        show_recommendation(recommendation, "Current engine recommendation")
        if current_submission.get("recommendation"):
            st.caption(f"Recommendation at submission time: {current_submission['recommendation']}. "
                       "The current recommendation updates as findings are resolved.")

        st.subheader("1. Evidence documents")
        if docs:
            st.dataframe([{"Document type": d["doc_type"], "Filename": d["filename"], "Uploaded at": d["uploaded_at"],
                           "SHA-256 (prefix)": (d.get("sha256") or "")[:12],
                           "Stored file exists": Path(d["stored_path"]).exists() if d.get("stored_path") else False}
                          for d in docs], use_container_width=True, hide_index=True)
        else:
            st.error("No supporting documents are recorded for this submission.")

        st.subheader("2. Extracted fields")
        if extracted:
            st.dataframe(mask_records(extracted), use_container_width=True, hide_index=True)
        else:
            st.warning("No extracted fields are available. Approval is blocked.")

        st.subheader("3. Verification findings")
        if findings:
            st.dataframe(mask_records(findings), use_container_width=True, hide_index=True)
        else:
            st.success("No exceptions were recorded. This does not establish external authenticity.")

        if extracted or findings:
            with st.expander("Authorized reveal of review evidence"):
                if st.button("Reveal full evidence values", key="review_reveal_evidence"):
                    if not acting_reviewer:
                        st.warning("Enter the acting reviewer name / ID at the top of the page first.")
                    else:
                        with db() as connection:
                            connection.execute(
                                "INSERT INTO access_logs(actor,action,vendor_id,field_name) VALUES(?,?,?,?)",
                                (acting_reviewer, "REVEAL_REVIEW_EVIDENCE", vendor_id,
                                 f"submission_id={submission_id}; extracted_fields; findings"))
                        st.dataframe(extracted, use_container_width=True, hide_index=True)
                        st.dataframe(findings, use_container_width=True, hide_index=True)
                        st.success("Reveal access logged in Privacy Center.")

        # ---- 3A. Resolve findings -------------------------------------------
        open_findings = [f for f in findings if not is_resolved(f)
                         and (f.get("severity") in ("High", "Medium") or f.get("finding_status") in BLOCKING_STATUSES)]
        locked = [f for f in findings if f.get("rule_id") in UNRESOLVABLE_RULES]
        resolvable = [f for f in open_findings if f.get("rule_id") not in UNRESOLVABLE_RULES]
        if locked:
            st.subheader("3A. Findings that cannot be overridden")
            st.error("These findings cannot be resolved with a note. The vendor must submit corrected evidence, "
                     "or the case should be rejected: " + ", ".join(sorted({f["rule_id"] for f in locked})))
        if resolvable:
            st.subheader("3B. Resolve a finding")
            labels = {f"{f['rule_id']} · {f['severity']} · Finding #{f['id']}": f for f in resolvable}
            chosen_label = st.selectbox("Finding to resolve", list(labels), key="resolve_finding_select")
            chosen = labels[chosen_label]
            st.caption(chosen["explanation"])
            if chosen["rule_id"] in BANK_CHANGE_RULES:
                st.warning("Bank change: confirm by calling the contact details in the APPROVED vendor master, "
                           "not those in this submission. A different reviewer must approve afterwards.")
            resolution_note = st.text_area("Resolution note (mandatory; identifiers are masked automatically)",
                                           key="resolution_note")
            if st.button("Mark finding resolved", key="resolve_finding_button"):
                try:
                    with db() as connection:
                        resolve_finding(connection, vendor_id, submission_id, chosen["id"], acting_reviewer,
                                        resolution_note)
                    st.success("Finding marked resolved.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))

        # ---- 4. Vendor communication ----------------------------------------
        st.subheader("4. Communication")
        draft = build_vendor_message(selected["legal_name"], findings, recommendation)
        internal_tasks = build_internal_tasks(findings)
        if internal_tasks:
            st.markdown("**Internal tasks for the review team (never sent to the vendor)**")
            for task in internal_tasks:
                st.markdown(f"- {task}")
        if draft:
            st.text_area("Consolidated message to the vendor (generated from the open findings)", draft, height=220,
                         disabled=True, key=f"draft_{submission_id}_{len(findings)}")
            if st.button("Send request to vendor", key="send_vendor_message",
                         disabled=not acting_reviewer or current_submission.get("decision") in ("Approved", "Rejected")):
                try:
                    with db() as connection:
                        send_vendor_message(connection, selected, submission_id, acting_reviewer)
                    st.success("Message recorded as sent and logged in the audit trail.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
            if not acting_reviewer:
                st.caption("Enter the acting reviewer name at the top of the page to send.")
        else:
            st.info("Nothing needs to be requested from the vendor.")
        history = rows("SELECT audience, status, sent_by, sent_at, created_at, message_text FROM vendor_messages "
                       "WHERE submission_id=? ORDER BY id DESC", (submission_id,))
        if history:
            with st.expander(f"Message history ({len(history)})"):
                st.dataframe(history, use_container_width=True, hide_index=True)

        # ---- 5. Human decision ----------------------------------------------
        st.subheader("5. Human decision")
        with db() as connection:
            resolvers = bank_change_resolvers(connection, submission_id)
        blockers = approval_blockers(findings, docs, extracted, current_submission, acting_reviewer, resolvers)
        if not acting_reviewer:
            blockers = blockers + ["Enter the acting reviewer name / ID."]
        if resolvers:
            st.info("Two-person rule active: the bank change was confirmed by "
                    + ", ".join(sorted(resolvers)) + ". A different reviewer must approve.")
        if blockers:
            st.error("Approval blocked: " + " ".join(blockers))
        st.info("External verification is not connected. A human decision here is not equivalent to "
                "PAN/GST/bank authentication.")
        rationale = st.text_area("Reviewer rationale (mandatory)", key="review_rationale")
        c1, c2 = st.columns(2)
        approve_clicked = c1.button("Approve", type="primary", disabled=bool(blockers))
        reject_clicked = c2.button("Reject" + (" (engine recommends)" if recommendation["outcome"] == "Rejected" else ""),
                                   disabled=not acting_reviewer)
        if approve_clicked or reject_clicked:
            decision = "Approved" if approve_clicked else "Rejected"
            try:
                with db() as connection:
                    vendor_row = connection.execute("SELECT * FROM vendors WHERE id=?", (vendor_id,)).fetchone()
                    record_decision(connection, vendor_row, current_submission, decision, acting_reviewer, rationale,
                                    findings, docs, extracted)
                st.success(f"Human review decision saved: {decision}")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

elif page == "Privacy Center":
    st.title("Privacy Center")
    st.caption("Prototype-level masking and access log view. This is not production IAM or encryption.")
    logs = rows("SELECT * FROM access_logs ORDER BY id DESC")
    st.dataframe(logs, use_container_width=True, hide_index=True)

elif page == "Audit Trail":
    st.title("Audit Trail")
    st.caption("Identifiers in event details are masked, including legacy events recorded before masking was added.")
    events = rows("SELECT * FROM audit_events ORDER BY id DESC")
    for event in events:
        event["details"] = mask_free_text(event.get("details"))
    st.dataframe(events, use_container_width=True, hide_index=True)
