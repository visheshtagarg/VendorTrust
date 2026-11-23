# VendorTrust — Final Project Submission

VendorTrust is an evidence-driven vendor onboarding and review prototype. It accepts vendor form data and three supporting PDFs (PAN, GST registration and bank proof), extracts document fields, checks consistency, explains findings, generates vendor-facing requests or internal tasks, and records human review decisions in an audit trail.

## Run locally (Windows)

1. Extract this folder.
2. Install Python 3.12 if it is not already installed.
3. Double-click `RUN_VENDORTRUST.bat`.

Or use PowerShell from this folder:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m streamlit run app.py
```

## Demo workflow

### First bulk batch

Open **Bulk Upload** and upload both files:

- `samples/bulk/VendorTrust_Final_Bulk_Cases.xlsx`
- `samples/bulk/VendorTrust_Final_Bulk_PDFs.zip`

The workbook has a `Cases` sheet and the ZIP contains exactly three uniquely named PDFs per case.

Expected results from the current project processing pipeline:

| Case | Scenario | Expected recommendation |
|---|---|---|
| CASE001 | Clean ABC Infrastructure onboarding | Approve-ready (human sign-off required) |
| CASE002 | Separate vendor with form/bank account mismatch | Pending: needs documents or correction |
| CASE003 | Proprietorship; owner holds bank account | Pending: needs human review |
| CASE004 | Maharashtra GSTIN, Karnataka form address | Pending: needs human review |
| CASE005 | Different PAN shares CASE001 bank account | Recommend reject |

**Why CASE002 is a separate vendor:** the clean ABC record must remain approved as the baseline for the later bank-change scenario. A mismatched UPDATE to the same vendor would replace the latest submission state and interfere with the clean onboarding → approval → bank-change demonstration. The mismatch rule is demonstrated on a separate synthetic vendor instead.

### Bank-change workflow (separate second batch)

1. In Review Center, human-approve CASE001 after checking its evidence.
2. Then open Bulk Upload and upload:
   - `samples/CASE006_Bank_Change/VendorTrust_CASE006_Bank_Change.xlsx`
   - `samples/CASE006_Bank_Change/VendorTrust_CASE006_PDFs.zip`
3. CASE006 is an UPDATE for the already-approved ABC vendor. It changes the bank account and contact details together.
4. Follow the in-app callback task using the previously approved phone. Resolve as reviewer `bob` with a note of at least 30 characters.
5. Have a different reviewer, `carol`, approve. The resolver must not approve the same bank-change submission.

Do not approve CASE002 or CASE005.

## Tests and validation

The included automated suite contains 53 tests. Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The five-case bulk workbook was also passed through the application's actual workbook parser, PDF ZIP reader and bulk processing function using an in-memory SQLite database. The expected recommendations above were returned.

## Important limitations

- This is a prototype; external PAN, GST and bank verification APIs are not connected.
- OCR is not enabled for scanned/image-only PDFs; evidence must contain extractable text.
- Risk scores are heuristic review signals, not fraud probabilities.
- Reviewer names are self-entered and are not authenticated.
- Synthetic sample PDFs are for software demonstration only and are not valid KYC documents.

All supplied sample entities and evidence are fictional.
