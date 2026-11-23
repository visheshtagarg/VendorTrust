from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
UPLOADS = DATA / "uploads"
DB = DATA / "vendortrust.db"

MAX_UPLOAD_MB = 20
MAX_PDF_PAGES = 100
PENDING_REVIEW_STATUS = "Pending Review"
EXTERNAL_VERIFICATION_STATUS = "Not Connected"

for directory in (DATA, UPLOADS):
    directory.mkdir(parents=True, exist_ok=True)
