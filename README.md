# VendorTrust — vendor onboarding verification prototype

VendorTrust checks a vendor's onboarding form against three supporting PDFs (PAN card, GST
registration certificate, bank proof), explains every discrepancy with a rule ID and evidence,
recommends **Approved / Pending / Rejected**, tells the vendor in plain language what is still
needed, and records a human decision with a full audit trail.

## Run (Windows PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Existing `data/vendortrust.db` files are upgraded automatically on start (new columns and one new
table are added; nothing is deleted). Back up `data/` first anyway.

## Outcomes

| Engine status | Problem-statement outcome | Meaning |
|---|---|---|
| Approve-ready | Approved (after human sign-off) | All checks passed. External PAN/GST/bank verification is not connected. |
| Needs Documents | Pending | Something is missing, unreadable, invalid or contradicts the form; the vendor must resubmit. |
| Pending Review | Pending | A reviewer must confirm something that can be legitimate (bank change, proprietor account, branch GSTIN). |
| Recommend Reject | Rejected (after human sign-off) | Hard failure: GSTIN/PAN link broken, PANs conflict across documents, bank account already used by another PAN, or a document file reused by another PAN. |

The recommendation is recomputed in the Review Center as findings are resolved.

## Controls that block approval

* Any finding in `UNRESOLVABLE_RULES` (core/engine.py), e.g. the bank account not found on the bank
  proof, a form-to-document identifier mismatch, a wrong document in a slot, or any reject rule.
  These cannot be overridden with a note.
* Any unresolved High or Medium finding, and any unresolved Missing / Not Found / Invalid /
  Needs Manual Review / Mismatch finding regardless of severity.
* Two-person rule: whoever confirmed a bank change cannot approve that submission.
* A bank-change resolution needs a 30+ character note; other resolutions need 15+.

## Demo data

`python scripts/generate_demo_pdfs.py` regenerates everything below. All identifiers are fictional
but pass the engine's format rules (PAN holder type, GSTIN check digit and state code).

* `demo_pdfs/` — clean company case. Form values are in `demo_pdfs/form_values.json`
  (ABC Infrastructure Pvt. Ltd., PAN ABCCA1234F, GSTIN 07ABCCA1234F1Z8, account 123456789012,
  IFSC HDFC0001234, address in New Delhi). Expected result: **Approve-ready**, no findings.
* `edge_cases/` — the four named edge cases; see `EDGE_CASES.md`.
* `samples/bulk/` — a 4-case workbook and matching PDF ZIP for Bulk Upload.

## Bulk upload

Open **Bulk Upload**, download the template (or the sample), add one case per row in `Cases`, and
upload it with a ZIP containing `CASEID_PAN.pdf`, `CASEID_GST.pdf` and `CASEID_BANK.pdf` for every
case. All cells are read as text, so blank cells stay blank and account numbers keep leading zeros.
`NEW` with a PAN that already exists is an error (use `UPDATE`). ZIP limits: 300 PDFs, 20 MB per PDF,
200 MB uncompressed; duplicate filenames in different folders are rejected. Bulk and the online form
share one save path (`services/submission.py:save_submission`).

## Project layout

```
app.py                     Streamlit UI only
core/engine.py             extraction, validators, rules, recommendation
core/privacy.py            masking for tables, notes and the audit trail
modules/adaptive_requests  vendor message + internal task generation
services/submission.py     save, resolve, approve/reject, send message (no Streamlit; tested)
services/bulk.py           Excel parsing, ZIP safety, bulk case processing
tests/                     53 tests: original 8, one or more per review item, 4 edge cases
```

## Limitations

External PAN / GST / bank verification is not connected. OCR is not enabled, so a scanned bank proof
cannot be approved (the account number must be read from a document). The risk score is a heuristic,
not a fraud probability. Reviewer names are self-entered, not authenticated, so the two-person rule is
a process control, not a security control.
