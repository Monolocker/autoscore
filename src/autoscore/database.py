"""SQLite persistence using the standard library: plain SQL, no ORM."""

import hashlib
import sqlite3
from pathlib import Path

from autoscore.config import PROJECT_ROOT
from autoscore.fetcher import FetchResult
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
    id           INTEGER PRIMARY KEY,
    company_key  TEXT NOT NULL REFERENCES companies (key) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    value        TEXT NOT NULL CHECK (value IN ('true', 'false', 'unknown')),
    evidence     TEXT,
    source_type  TEXT,
    method       TEXT,
    source_url   TEXT,
    confidence   REAL,
    observed_at  TEXT,
    event_date   TEXT,
    UNIQUE (company_key, name)
);

CREATE TABLE IF NOT EXISTS pages (
    id            INTEGER PRIMARY KEY,
    company_key   TEXT NOT NULL REFERENCES companies (key) ON DELETE CASCADE,
    url           TEXT NOT NULL,
    final_url     TEXT,
    status_code   INTEGER,
    content_type  TEXT,
    content_hash  TEXT,           -- sha256 of the HTML; lets later stages skip unchanged pages
    html          TEXT,
    error         TEXT,
    fetched_at    TEXT NOT NULL,
    UNIQUE (company_key, url)
);

CREATE TABLE IF NOT EXISTS page_texts (
    page_id      INTEGER PRIMARY KEY REFERENCES pages (id) ON DELETE CASCADE,
    title        TEXT,
    description  TEXT,
    text         TEXT NOT NULL,
    source_hash  TEXT NOT NULL,   -- parse key: parser version + content_hash of the HTML parsed
    parsed_at    TEXT NOT NULL
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


def save_page(connection: sqlite3.Connection, company_key: str, result: FetchResult) -> None:
    """Insert a fetched page, or replace the previous fetch of the same URL."""
    content_hash = None
    if result.html is not None:
        content_hash = hashlib.sha256(result.html.encode("utf-8")).hexdigest()
    with connection:
        connection.execute(
            """
            INSERT INTO pages (
                company_key, url, final_url, status_code, content_type,
                content_hash, html, error, fetched_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (company_key, url) DO UPDATE SET
                final_url = excluded.final_url,
                status_code = excluded.status_code,
                content_type = excluded.content_type,
                content_hash = excluded.content_hash,
                html = excluded.html,
                error = excluded.error,
                fetched_at = excluded.fetched_at
            """,
            (
                company_key,
                result.url,
                result.final_url,
                result.status_code,
                result.content_type,
                content_hash,
                result.html,
                result.error,
                utc_now().isoformat(),
            ),
        )


def get_page(connection: sqlite3.Connection, company_key: str, url: str) -> sqlite3.Row | None:
    """Return the stored fetch of a page (metadata and HTML), or None if never fetched."""
    return connection.execute(
        "SELECT * FROM pages WHERE company_key = ? AND url = ?", (company_key, url)
    ).fetchone()


def list_html_pages(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    """Pages that have HTML, with the hash they were last parsed from (NULL if never parsed)."""
    return connection.execute(
        """
        SELECT pages.id, pages.company_key, pages.url, pages.final_url, pages.html,
               pages.content_hash, page_texts.source_hash
        FROM pages
        LEFT JOIN page_texts ON page_texts.page_id = pages.id
        WHERE pages.html IS NOT NULL
        ORDER BY pages.company_key, pages.url
        """
    ).fetchall()


def save_page_text(
    connection: sqlite3.Connection,
    page_id: int,
    title: str | None,
    description: str | None,
    text: str,
    source_hash: str,
) -> None:
    """Store parsed text for a page, replacing any earlier parse."""
    with connection:
        connection.execute(
            """
            INSERT INTO page_texts (page_id, title, description, text, source_hash, parsed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (page_id) DO UPDATE SET
                title = excluded.title,
                description = excluded.description,
                text = excluded.text,
                source_hash = excluded.source_hash,
                parsed_at = excluded.parsed_at
            """,
            (page_id, title, description, text, source_hash, utc_now().isoformat()),
        )


def list_company_pages(connection: sqlite3.Connection, company_key: str) -> list[sqlite3.Row]:
    """Parsed pages for one company: URL, HTML, and extracted text."""
    return connection.execute(
        """
        SELECT pages.url, pages.final_url, pages.html, page_texts.text
        FROM pages
        JOIN page_texts ON page_texts.page_id = pages.id
        WHERE pages.company_key = ? AND pages.html IS NOT NULL
        ORDER BY pages.url
        """,
        (company_key,),
    ).fetchall()