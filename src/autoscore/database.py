"""SQLite persistence using the standard library: plain SQL, no ORM"""

import sqlite3
from pathlib import Path
from autoscore.config import PROJECT_ROOT
from autoscore.models import Company, Signal, utc_now

DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "autoscore.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    key         TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    website     TEXT,
    data        TEXT NOT NULL,  -- full Company model as JSON
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
    id          INTEGER PRIMARY KEY,
    company_key TEXT NOT NULL REFERENCES companies (key) on DELETE CASCADE,
    name        TEXT NOT NULL,
    value       TEXT NOT NULL CHECK (value IN ('true', 'false', 'unknown')),
    evidence    TEXT,
    source_type TEXT,
    method      TEXT,
    source_url  TEXT,
    confidence  REAL,
    observed_at TEXT,
    event_date  TEXT,
    UNIQUE (company_key, name)
);
"""

PROVENANCE_COLUMNS = ("source_type", "method", "source_url", "confidence", "observed_at")

def connect(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open the database, enable foreign keys, and create tables if missing."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    # SQLite ignores foreign keys unless this is switched on for every connection.
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA)
    return connection


def list_tables(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    ).fetchall()
    return [row["name"] for row in rows]


def save_company(connection: sqlite3.Connection, company: Company) -> None:
    """Insert a company, or update it in place if its key already exists."""
    now = utc_now().isoformat()
    with connection:
        connection.execute(
            """
            INSERT INTO companies (key, name, website, data, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (key) DO UPDATE SET
                name = excluded.name,
                website = excluded.website,
                data = excluded.data,
                updated_at = excluded.updated_at
            """,
            (company.key, company.name, company.website, company.model_dump_json(), now, now),
        )


def get_company(connection: sqlite3.Connection, key: str) -> Company | None:
    row = connection.execute("SELECT data FROM companies WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    return Company.model_validate_json(row["data"])


def list_companies(connection: sqlite3.Connection) -> list[Company]:
    rows = connection.execute("SELECT data FROM companies ORDER BY name").fetchall()
    return [Company.model_validate_json(row["data"]) for row in rows]


def save_signal(connection: sqlite3.Connection, signal: Signal) -> None:
    """Insert a signal, or replace the previous value of the same signal for that company."""
    provenance = signal.provenance
    with connection:
        connection.execute(
            """
            INSERT INTO signals (
                company_key, name, value, evidence, source_type, method,
                source_url, confidence, observed_at, event_date
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (company_key, name) DO UPDATE SET
                value = excluded.value,
                evidence = excluded.evidence,
                source_type = excluded.source_type,
                method = excluded.method,
                source_url = excluded.source_url,
                confidence = excluded.confidence,
                observed_at = excluded.observed_at,
                event_date = excluded.event_date
            """,
            (
                signal.company_key,
                signal.name,
                str(signal.value),
                signal.evidence,
                str(provenance.source_type) if provenance else None,
                str(provenance.method) if provenance else None,
                provenance.source_url if provenance else None,
                provenance.confidence if provenance else None,
                provenance.observed_at.isoformat() if provenance else None,
                signal.event_date.isoformat() if signal.event_date else None,
            ),
        )


def get_signals(connection: sqlite3.Connection, company_key: str) -> list[Signal]:
    rows = connection.execute(
        "SELECT * FROM signals WHERE company_key = ? ORDER BY name", (company_key,)
    ).fetchall()
    return [_row_to_signal(row) for row in rows]


def _row_to_signal(row: sqlite3.Row) -> Signal:
    """Rebuild a Signal from a row; Pydantic converts the stored strings back to types."""
    data = dict(row)
    data.pop("id")
    provenance = {column: data.pop(column) for column in PROVENANCE_COLUMNS}
    data["provenance"] = provenance if provenance["source_type"] is not None else None
    return Signal.model_validate(data)