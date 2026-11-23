import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database.connection import apply_schema  # noqa: E402

PAN = "ABCCA1234F"
GSTIN = "07ABCCA1234F1Z8"
NAME = "ABC INFRASTRUCTURE PVT LTD"
ACCOUNT = "123456789012"
IFSC = "HDFC0001234"


def field(name, value, page=1):
    return {"field_name": name, "field_value": value, "page_number": page, "confidence": 0.9}


def clean_docs(account=ACCOUNT, holder=NAME, pan=PAN, gstin=GSTIN, legal=NAME, ifsc=IFSC):
    return [
        {"doc_type": "PAN Card", "filename": "pan.pdf", "fields": [field("PAN", pan), field("NAME", legal)]},
        {"doc_type": "GST Certificate", "filename": "gst.pdf",
         "fields": [field("GSTIN", gstin), field("LEGAL_NAME", legal), field("PAN", pan),
                    field("ADDRESS", "21 TEST MARKET, NEW DELHI, DELHI 110001")]},
        {"doc_type": "Bank Proof", "filename": "bank.pdf",
         "fields": [field("IFSC", ifsc), field("BANK_ACCOUNT", account), field("ACCOUNT_HOLDER", holder)]},
    ]


def clean_profile(**overrides):
    profile = {"legal_name": NAME, "pan": PAN, "gstin": GSTIN, "ifsc": IFSC, "bank_account": ACCOUNT,
               "address": "21 Test Market, New Delhi, Delhi 110001", "email": "a@abc.example.com",
               "phone": "9876543210"}
    profile.update(overrides)
    return profile


@pytest.fixture
def connection():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def uploads(tmp_path):
    return tmp_path / "uploads"
