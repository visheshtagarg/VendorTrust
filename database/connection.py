import sqlite3
import threading
from contextlib import contextmanager
from config import DB, ROOT

# Each Streamlit browser session gets its own database file. The app sets the
# path at the start of every script run; anything else (tests, scripts) falls
# back to the shared default DB.
_local = threading.local()


def set_db_path(path):
    _local.path = path


def current_db_path():
    return getattr(_local, "path", None) or DB


@contextmanager
def db():
    connection = sqlite3.connect(current_db_path(), timeout=30, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

def connect():
    """Compatibility helper for existing modules."""
    connection = sqlite3.connect(current_db_path(), timeout=30, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    return connection

# Columns added after the first release. Existing databases are upgraded in
# place; nothing is deleted.
_ADDED_COLUMNS = {
    "documents": {"sha256": "TEXT"},
    "submissions": {"recommendation": "TEXT", "recommendation_reasons": "TEXT", "source": "TEXT"},
    "findings": {"resolution_note": "TEXT", "resolved_by": "TEXT", "resolved_at": "TEXT"},
}


def apply_schema(connection):
    """Create tables and upgrade older databases. Safe to run repeatedly."""
    schema = (ROOT / "database" / "schema.sql").read_text(encoding="utf-8")
    connection.executescript(schema)
    for table, columns in _ADDED_COLUMNS.items():
        existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        for column, column_type in columns.items():
            if column not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_documents_sha256 ON documents(sha256)")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    connection.execute("INSERT OR IGNORE INTO schema_version(version) VALUES (1)")
    connection.execute("INSERT OR IGNORE INTO schema_version(version) VALUES (3)")


def init_db():
    with db() as connection:
        apply_schema(connection)
