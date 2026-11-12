"""Excel + ZIP bulk onboarding. Uses the same save_submission as the form."""
import re
import zipfile
from io import BytesIO
from pathlib import PurePosixPath

from core.engine import DocumentError
from services.submission import find_vendor, normalize_profile, prepare_documents, save_submission

MAX_ZIP_FILES = 300
MAX_ZIP_MEMBER_MB = 20
MAX_ZIP_TOTAL_MB = 200
MAX_COMPRESSION_RATIO = 100  # a PDF does not legitimately compress 100:1

COLUMNS = ["Case_ID", "Action", "Vendor_Code", "Legal_Name", "PAN", "GSTIN", "Email", "Phone",
           "Address", "Bank_Account", "IFSC", "Reviewer_Notes"]
REQUIRED_COLUMNS = {"Case_ID", "Action", "Legal_Name", "PAN", "GSTIN", "Bank_Account", "IFSC"}
DOC_SUFFIXES = {"PAN": "PAN Card", "GST": "GST Certificate", "BANK": "Bank Proof"}


class BulkError(ValueError):
    pass


def _clean_cell(value):
    text = "" if value is None else str(value).strip()
    if text.lower() in ("nan", "none", "nat"):
        return ""
    # Numeric cells typed into Excel without text formatting arrive as "123.0".
    if re.fullmatch(r"[0-9]+\.0", text):
        text = text[:-2]
    return text


def parse_cases_workbook(data):
    """Read the Cases sheet as text. Blank cells stay blank; numbers keep their digits."""
    import pandas as pd
    try:
        frame = pd.read_excel(BytesIO(data), sheet_name="Cases", dtype=str, keep_default_na=False)
    except ValueError as exc:
        raise BulkError(f"Could not read the 'Cases' sheet: {exc}") from exc
    frame.columns = [str(c).strip() for c in frame.columns]
    missing = REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        raise BulkError("Excel is missing required columns: " + ", ".join(sorted(missing)))
    cases = []
    for record in frame.to_dict(orient="records"):
        row = {key: _clean_cell(value) for key, value in record.items()}
        if any(row.values()):
            cases.append(row)
    return cases


def read_pdf_zip(data):
    """Return {lowercase filename: bytes} with size, count and name-collision checks."""
    try:
        archive = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise BulkError("The PDF archive is not a valid ZIP file.") from exc
    with archive:
        members = [m for m in archive.infolist() if not m.is_dir()
                   and not PurePosixPath(m.filename).name.startswith(".")
                   and "__MACOSX" not in m.filename]
        pdfs = [m for m in members if m.filename.lower().endswith(".pdf")]
        if len(pdfs) > MAX_ZIP_FILES:
            raise BulkError(f"The ZIP contains {len(pdfs)} PDFs; the limit is {MAX_ZIP_FILES}.")
        total = sum(m.file_size for m in pdfs)
        if total > MAX_ZIP_TOTAL_MB * 1024 * 1024:
            raise BulkError(f"The ZIP expands to more than {MAX_ZIP_TOTAL_MB} MB.")
        by_name = {}
        for member in pdfs:
            if member.file_size > MAX_ZIP_MEMBER_MB * 1024 * 1024:
                raise BulkError(f"{member.filename} is larger than {MAX_ZIP_MEMBER_MB} MB.")
            if member.compress_size and member.file_size / member.compress_size > MAX_COMPRESSION_RATIO:
                raise BulkError(f"{member.filename} has a suspicious compression ratio and was rejected.")
            by_name.setdefault(PurePosixPath(member.filename).name.lower(), []).append(member)
        collisions = {name: m for name, m in by_name.items() if len(m) > 1}
        if collisions:
            detail = "; ".join(f"{name}: " + ", ".join(x.filename for x in m) for name, m in collisions.items())
            raise BulkError("The same filename appears in more than one folder of the ZIP, so it is unclear "
                            "which file to use: " + detail)
        pdf_map = {}
        for name, (member,) in by_name.items():
            with archive.open(member) as handle:
                content = handle.read(MAX_ZIP_MEMBER_MB * 1024 * 1024 + 1)
            if len(content) > MAX_ZIP_MEMBER_MB * 1024 * 1024:
                raise BulkError(f"{member.filename} is larger than {MAX_ZIP_MEMBER_MB} MB.")
            pdf_map[name] = content
    if not pdf_map:
        raise BulkError("No PDF files were found inside the ZIP.")
    return pdf_map


def duplicate_case_ids(cases):
    ids = [c.get("Case_ID", "").strip().upper() for c in cases]
    return {i for i in ids if i and ids.count(i) > 1}


def process_bulk_case(connection, case, pdf_map, uploads_dir, max_mb=20):
    case_id = case.get("Case_ID", "").strip()
    action = (case.get("Action", "") or "NEW").strip().upper()
    if not case_id:
        raise BulkError("Case_ID is required.")
    if action not in {"NEW", "UPDATE"}:
        raise BulkError("Action must be NEW or UPDATE.")
    profile = normalize_profile({
        "legal_name": case.get("Legal_Name"), "pan": case.get("PAN"), "gstin": case.get("GSTIN"),
        "email": case.get("Email"), "phone": case.get("Phone"), "address": case.get("Address"),
        "bank_account": case.get("Bank_Account"), "ifsc": case.get("IFSC"),
    })
    for field in ("legal_name", "pan", "gstin", "bank_account", "ifsc"):
        if not profile[field]:
            raise BulkError(f"{field.replace('_', ' ').title()} is required.")

    files = []
    for suffix, doc_type in DOC_SUFFIXES.items():
        filename = f"{case_id}_{suffix}.pdf"
        data = pdf_map.get(filename.lower())
        if not data:
            raise BulkError(f"Missing PDF: {filename}. Include it in the uploaded ZIP.")
        files.append((doc_type, filename, data))
    try:
        prepared = prepare_documents(files, max_mb)
    except DocumentError as exc:
        raise BulkError(str(exc)) from exc

    vendor_code = case.get("Vendor_Code", "").strip()
    existing = find_vendor(connection, pan=profile["pan"], vendor_code=vendor_code or None)
    if action == "UPDATE" and not existing:
        raise BulkError("UPDATE requires an existing Vendor_Code or a matching PAN.")
    if action == "NEW" and existing:
        raise BulkError(f"A vendor with this PAN already exists ({existing['vendor_code']}). "
                        "Use Action = UPDATE to submit changes for an existing vendor.")
    if action == "UPDATE" and vendor_code and existing["vendor_code"] != vendor_code:
        raise BulkError("Vendor_Code was not found.")
    if action == "UPDATE" and existing["pan"] and existing["pan"].upper() != profile["pan"]:
        raise BulkError("The PAN in this row differs from the existing vendor's PAN. A PAN cannot change; "
                        "onboard the new entity with Action = NEW.")

    result = save_submission(connection, profile, prepared, existing, uploads_dir, source="BULK_UPLOAD",
                             audit_extra={"case_id": case_id, "action": action})
    return {"Case_ID": case_id, "Vendor_Code": result["vendor_code"], "Submission_ID": result["submission_id"],
            "Action": action, "Risk_Score": result["score"],
            "Recommendation": result["recommendation"]["label"],
            "Machine_Status": result["machine_status"], "Findings": len(result["findings"]),
            "Result": "Processed"}


def build_bulk_template(rows=None):
    """Workbook with every column formatted as text, so Excel keeps leading zeros."""
    import pandas as pd
    from openpyxl.utils import get_column_letter
    rows = rows or [
        ["CASE001", "NEW", "", "ABC Infrastructure Pvt. Ltd.", "ABCCA1234F", "07ABCCA1234F1Z8",
         "accounts@abcinfra.example.com", "9876543210", "21 Test Market, New Delhi, Delhi 110001",
         "123456789012", "HDFC0001234", "Clean case: matches the demo PDFs"],
    ]
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame(rows, columns=COLUMNS).astype(str).to_excel(writer, index=False, sheet_name="Cases")
        guide = []
        for row in rows:
            for suffix, doc_type in DOC_SUFFIXES.items():
                guide.append([row[0], doc_type, f"{row[0]}_{suffix}.pdf"])
        pd.DataFrame(guide, columns=["Case_ID", "Document_Type", "Required_Filename"]).to_excel(
            writer, index=False, sheet_name="PDF_Naming_Guide")
        sheet = writer.sheets["Cases"]
        for index, _ in enumerate(COLUMNS, start=1):
            letter = get_column_letter(index)
            sheet.column_dimensions[letter].width = 22
            for cell in sheet[letter]:
                cell.number_format = "@"
    return buffer.getvalue()
