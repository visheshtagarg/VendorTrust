"""Regenerate every synthetic demo PDF, the edge-case packs and the bulk sample.

Run from the project folder:  python scripts/generate_demo_pdfs.py

All identifiers are fictional but pass the format rules the engine enforces
(PAN holder-type character, GSTIN check digit and state code).
"""
import json
import sys
import zipfile
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.engine import gstin_check_char, pan_entity_type  # noqa: E402
from services.bulk import build_bulk_template  # noqa: E402


def gstin(state, pan):
    first14 = f"{state}{pan}1Z"
    return first14 + gstin_check_char(first14)


# --------------------------------------------------------------------------
# Synthetic entities
# --------------------------------------------------------------------------
ABC = {
    "legal_name": "ABC INFRASTRUCTURE PVT LTD", "trade_name": "ABC INFRA", "pan": "ABCCA1234F",
    "gstin": gstin("07", "ABCCA1234F"), "address": "21 TEST MARKET, NEW DELHI, DELHI 110001",
    "bank_account": "123456789012", "ifsc": "HDFC0001234", "holder": "ABC INFRASTRUCTURE PVT LTD",
    "email": "accounts@abcinfra.example.com", "phone": "9876543210",
}
ABC_NEW_BANK = dict(ABC, bank_account="987654321098", ifsc="ICIC0000456")
KUMAR = {
    "legal_name": "RAMESH KUMAR", "trade_name": "KUMAR TRADERS", "pan": "BXRPK4821L",
    "gstin": gstin("27", "BXRPK4821L"), "address": "14 MARKET YARD, PUNE, MAHARASHTRA 411037",
    "bank_account": "50100234567891", "ifsc": "SBIN0004321", "holder": "RAMESH KUMAR",
    "email": "kumartraders@example.com", "phone": "9822001122",
}
XYZ = {
    "legal_name": "XYZ CONSULTANTS PVT LTD", "trade_name": "XYZ CONSULTANTS", "pan": "XYZCT5678M",
    "gstin": gstin("27", "XYZCT5678M"), "address": "5 HARBOUR ROAD, MUMBAI, MAHARASHTRA 400001",
    # Same account as ABC Infrastructure: the shared-bank fraud case.
    "bank_account": "123456789012", "ifsc": "HDFC0001234", "holder": "XYZ CONSULTANTS PVT LTD",
    "email": "billing@xyzconsult.example.com", "phone": "9000011111",
}
DELTA = {
    "legal_name": "DELTA LOGISTICS PVT LTD", "trade_name": "DELTA LOGISTICS", "pan": "DELCL2468Q",
    "gstin": gstin("27", "DELCL2468Q"), "address": "PLOT 7, MIDC BHOSARI, PUNE, MAHARASHTRA 411026",
    "bank_account": "30211234567", "ifsc": "KKBK0000958", "holder": "DELTA LOGISTICS PVT LTD",
    "email": "ap@deltalogistics.example.com", "phone": "9011122233",
}
for entity in (ABC, KUMAR, XYZ, DELTA):
    assert pan_entity_type(entity["pan"]), entity["pan"]


# --------------------------------------------------------------------------
# PDF rendering
# --------------------------------------------------------------------------
def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica-Bold", 8)
    canvas.drawCentredString(A4[0] / 2, 12 * mm, "DEMO ONLY - NOT A GOVERNMENT / BANK DOCUMENT - NOT VALID FOR KYC")
    canvas.setFont("Helvetica", 8)
    canvas.drawRightString(A4[0] - 15 * mm, 8 * mm, f"Page {doc.page}")
    canvas.restoreState()


def render(path, title, category, rows, note):
    path.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(path), pagesize=A4, topMargin=18 * mm, bottomMargin=22 * mm,
                            title=title, author="VendorTrust demo generator")
    table = Table([["Field", "Value"]] + rows, colWidths=[60 * mm, 110 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3b57")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#9aa7b4")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f5f8")]),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story = [
        Paragraph("VENDORTRUST - TEST EVIDENCE", styles["Title"]),
        Paragraph("DEMO SAMPLE - NOT VALID FOR REAL-WORLD VERIFICATION", styles["Heading4"]),
        Spacer(1, 6 * mm), Paragraph(title, styles["Heading2"]),
        Paragraph(f"Document category: {category} (synthetic test data)", styles["Normal"]),
        Spacer(1, 5 * mm), table, Spacer(1, 6 * mm),
        Paragraph(f"Important: {note}", styles["Normal"]), Spacer(1, 3 * mm),
        Paragraph("This synthetic sample is created only to test PDF text extraction and form-to-document "
                  "consistency in the VendorTrust prototype. It is not proof of identity, tax registration, "
                  "or bank-account ownership.", styles["Normal"]),
    ]
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)


def pan_pdf(path, e):
    render(path, "PAN Details - Sample Information Sheet", "PAN Card / PAN allotment evidence",
           [["PAN", e["pan"]], ["NAME", e["legal_name"]], ["ENTITY_TYPE", pan_entity_type(e["pan"]).upper()]],
           "The PAN value and name are fictional test values. No PAN card is reproduced.")


def gst_pdf(path, e, address=None):
    render(path, "GST Registration Details - Sample Information Sheet", "GST Registration Certificate",
           [["GSTIN", e["gstin"]], ["LEGAL_NAME", e["legal_name"]], ["TRADE_NAME", e["trade_name"]],
            ["ADDRESS", address or e["address"]], ["PAN", e["pan"]]],
           "The GSTIN and registration details are fictional test values. This is not a GST certificate.")


def bank_pdf(path, e):
    render(path, "Bank Account Details - Sample Information Sheet",
           "Bank Proof / cancelled cheque or bank statement",
           [["ACCOUNT_HOLDER", e["holder"]], ["BANK_ACCOUNT", e["bank_account"]], ["IFSC", e["ifsc"]],
            ["BANK_NAME", "DEMO BANK - NOT A REAL BANK RECORD"], ["ACCOUNT_TYPE", "CURRENT"]],
           "The account number and IFSC are fictional placeholders. No cheque, statement, seal or signature is reproduced.")


def form(e, **overrides):
    values = {"legal_name": e["legal_name"], "pan": e["pan"], "gstin": e["gstin"], "email": e["email"],
              "phone": e["phone"], "address": e["address"], "bank_account": e["bank_account"], "ifsc": e["ifsc"]}
    values.update(overrides)
    return values


def full_set(folder, e, prefix=""):
    pan_pdf(folder / f"{prefix}01_PAN.pdf", e)
    gst_pdf(folder / f"{prefix}02_GST.pdf", e)
    bank_pdf(folder / f"{prefix}03_Bank_Proof.pdf", e)


def main():
    demo = ROOT / "demo_pdfs"
    full_set(demo, ABC)
    (demo / "form_values.json").write_text(json.dumps(form(ABC, legal_name="ABC Infrastructure Pvt. Ltd."), indent=2))

    edge = ROOT / "edge_cases"
    # EC1 - approved vendor changes bank account (and, in variant B, email/phone too)
    ec1 = edge / "EC1_bank_account_change"
    full_set(ec1 / "step1_original_onboarding", ABC)
    bank_pdf(ec1 / "step2_change_request" / "03_Bank_Proof_NEW.pdf", ABC_NEW_BANK)
    (ec1 / "forms.json").write_text(json.dumps({
        "step1_original": form(ABC),
        "step2a_bank_change_only": form(ABC_NEW_BANK),
        "step2b_bank_and_contact_change": form(ABC_NEW_BANK, email="accounts.newteam@abcinfra-payments.example.com",
                                               phone="9123456780"),
    }, indent=2))
    # EC2 - proprietorship: account held by the owner, form uses the trade name
    ec2 = edge / "EC2_proprietorship_owner_account"
    full_set(ec2, KUMAR)
    (ec2 / "form.json").write_text(json.dumps(form(KUMAR, legal_name="Kumar Traders"), indent=2))
    # EC3 - same bank account used by two PANs
    ec3 = edge / "EC3_shared_bank_account"
    full_set(ec3 / "vendor_A_existing", ABC)
    full_set(ec3 / "vendor_B_new", XYZ)
    (ec3 / "forms.json").write_text(json.dumps({"vendor_A": form(ABC), "vendor_B": form(XYZ)}, indent=2))
    # EC4 - GSTIN state differs from the head-office address (branch registration)
    ec4 = edge / "EC4_gstin_state_vs_address"
    full_set(ec4, DELTA)
    (ec4 / "form.json").write_text(json.dumps(
        form(DELTA, address="88 RESIDENCY ROAD, BENGALURU, KARNATAKA 560025"), indent=2))

    # Bulk sample: the clean case plus EC2, EC4 and EC3 in a single batch.
    bulk = ROOT / "samples" / "bulk"
    bulk.mkdir(parents=True, exist_ok=True)
    cases = [
        ("CASE001", ABC, {"legal_name": "ABC Infrastructure Pvt. Ltd."}, "Clean case: expect Approve-ready"),
        ("CASE002", KUMAR, {"legal_name": "Kumar Traders"}, "EC2 proprietorship: expect Pending review"),
        ("CASE003", DELTA, {"address": "88 Residency Road, Bengaluru, Karnataka 560025"},
         "EC4 branch GSTIN: expect Pending review"),
        ("CASE004", XYZ, {}, "EC3 shares CASE001's bank account: expect Recommend reject"),
    ]
    rows = []
    with zipfile.ZipFile(bulk / "VendorTrust_Bulk_Sample_PDFs.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for case_id, entity, overrides, note in cases:
            values = form(entity, **overrides)
            rows.append([case_id, "NEW", "", values["legal_name"], values["pan"], values["gstin"], values["email"],
                         values["phone"], values["address"], values["bank_account"], values["ifsc"], note])
            tmp = bulk / "_tmp"
            pan_pdf(tmp / f"{case_id}_PAN.pdf", entity)
            gst_pdf(tmp / f"{case_id}_GST.pdf", entity)
            bank_pdf(tmp / f"{case_id}_BANK.pdf", entity)
            for suffix in ("PAN", "GST", "BANK"):
                archive.write(tmp / f"{case_id}_{suffix}.pdf", arcname=f"{case_id}_{suffix}.pdf")
    for file in (bulk / "_tmp").iterdir():
        file.unlink()
    (bulk / "_tmp").rmdir()
    (bulk / "VendorTrust_Bulk_Sample.xlsx").write_bytes(build_bulk_template(rows))
    print("Generated demo_pdfs/, edge_cases/ and samples/bulk/")
    for entity in (ABC, KUMAR, XYZ, DELTA):
        print(f"  {entity['legal_name']:<28} PAN {entity['pan']}  GSTIN {entity['gstin']}")


if __name__ == "__main__":
    main()
