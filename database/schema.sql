PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS vendors(
 id INTEGER PRIMARY KEY,
 vendor_code TEXT UNIQUE,
 legal_name TEXT NOT NULL,
 trade_name TEXT,
 pan TEXT,
 gstin TEXT,
 email TEXT,
 phone TEXT,
 address TEXT,
 bank_account TEXT,
 ifsc TEXT,
 status TEXT DEFAULT 'New',
 risk_score REAL DEFAULT 0,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS submissions(
 id INTEGER PRIMARY KEY,
 vendor_id INTEGER REFERENCES vendors(id),
 submitted_at TEXT DEFAULT CURRENT_TIMESTAMP,
 status TEXT DEFAULT 'Processing',
 decision TEXT,
 risk_score REAL,
 reviewer TEXT,
 review_note TEXT,
 recommendation TEXT,
 recommendation_reasons TEXT,
 source TEXT
);

CREATE TABLE IF NOT EXISTS documents(
 id INTEGER PRIMARY KEY,
 submission_id INTEGER REFERENCES submissions(id),
 vendor_id INTEGER REFERENCES vendors(id),
 doc_type TEXT,
 filename TEXT,
 stored_path TEXT,
 extracted_text TEXT,
 sha256 TEXT,
 uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS extracted_fields(
 id INTEGER PRIMARY KEY,
 document_id INTEGER REFERENCES documents(id),
 vendor_id INTEGER REFERENCES vendors(id),
 field_name TEXT,
 field_value TEXT,
 page_number INTEGER,
 confidence REAL,
 source TEXT
);

CREATE TABLE IF NOT EXISTS findings(
 id INTEGER PRIMARY KEY,
 submission_id INTEGER REFERENCES submissions(id),
 vendor_id INTEGER REFERENCES vendors(id),
 field_name TEXT,
 observed_value TEXT,
 expected_value TEXT,
 rule_id TEXT,
 severity TEXT,
 finding_status TEXT,
 explanation TEXT,
 source_document TEXT,
 source_page INTEGER,
 confidence REAL,
 recommended_action TEXT,
 resolution_note TEXT,
 resolved_by TEXT,
 resolved_at TEXT,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS snapshots(
 id INTEGER PRIMARY KEY,
 vendor_id INTEGER REFERENCES vendors(id),
 submission_id INTEGER,
 snapshot_json TEXT,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS simulations(
 id INTEGER PRIMARY KEY,
 vendor_id INTEGER REFERENCES vendors(id),
 scenario_json TEXT,
 base_score REAL,
 simulated_score REAL,
 simulated_decision TEXT,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS document_requests(
 id INTEGER PRIMARY KEY,
 vendor_id INTEGER REFERENCES vendors(id),
 submission_id INTEGER,
 request_text TEXT,
 reason TEXT,
 status TEXT DEFAULT 'Open',
 created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS vendor_messages(
 id INTEGER PRIMARY KEY,
 vendor_id INTEGER REFERENCES vendors(id),
 submission_id INTEGER,
 audience TEXT DEFAULT 'vendor',
 message_text TEXT,
 status TEXT DEFAULT 'Draft',
 sent_by TEXT,
 sent_at TEXT,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS policies(
 id INTEGER PRIMARY KEY,
 policy_name TEXT,
 config_json TEXT,
 active INTEGER DEFAULT 0,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS policy_runs(
 id INTEGER PRIMARY KEY,
 policy_id INTEGER REFERENCES policies(id),
 run_name TEXT,
 results_json TEXT,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS decisions(
 id INTEGER PRIMARY KEY,
 vendor_id INTEGER REFERENCES vendors(id),
 submission_id INTEGER,
 decision TEXT,
 risk_score REAL,
 rationale TEXT,
 reviewer TEXT,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS access_logs(
 id INTEGER PRIMARY KEY,
 actor TEXT,
 action TEXT,
 vendor_id INTEGER,
 field_name TEXT,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_events(
 id INTEGER PRIMARY KEY,
 vendor_id INTEGER,
 submission_id INTEGER,
 event_type TEXT,
 details TEXT,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_vendors_pan ON vendors(pan);
CREATE INDEX IF NOT EXISTS idx_vendors_gstin ON vendors(gstin);
CREATE INDEX IF NOT EXISTS idx_vendors_bank ON vendors(bank_account);
CREATE INDEX IF NOT EXISTS idx_submissions_vendor ON submissions(vendor_id);
CREATE INDEX IF NOT EXISTS idx_documents_submission ON documents(submission_id);
CREATE INDEX IF NOT EXISTS idx_findings_submission ON findings(submission_id);
