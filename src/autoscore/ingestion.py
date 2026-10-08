""""Import seed companies from a CSV file.

Empty cells stay UNKNOWN. Bad rows are skipped and reported; one bad row
never stops the rest of the import
"""

import csv
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from autoscore.database import save_company
from autoscore.models import Company, ExtractionMethod, Provenance, SourceType

LIST_SEPARATOR = ";"
IDENTITY_COLUMNS = {"name", "website", "existing_client"}
# Kept in CSV for later milestone (calibration, analyst notes); not yet imported
IGNORED_COLUMNS = {"analyst_verdict", "notes"}

def to_snake(text: str) -> str:
    """'Pre-Seed' -> 'pre_seed', 'B2B SaaS' -> 'b2b_saas'"""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")

def parse_usd(text: str) -> int:
    """'$500k' -> 500000, '1.5M' -> 1500000, '750,000' -> 750000."""
    cleaned = text.lower().replace("$", "").replace(",", "").replace("_", "").strip()
    multiplier = 1
    if cleaned.endswith("k"):
        multiplier, cleaned = 1_000, cleaned[:-1]
    elif cleaned.endswith("m"):
        multiplier, cleaned = 1_000_000, cleaned[:-1]
    try:
        amount = round(float(cleaned) * multiplier)
    except ValueError:
        raise ValueError(f"Not a dollar amount: {text!r}") from None
    if amount < 0:
        raise ValueError(f"Dollar amount cannot be negative {text!r}")
    return amount

def parse_int(text: str) -> int:
    try:
        value = int(text.replace(",", ""))
    except ValueError:
        raise ValueError(f"Not a whole number: {text!r}") from None
    if value < 0:
        raise ValueError(f"Number cannot be negative {text!r}")
    return value

def parse_date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"Not a YYYY-MM-DD date: {text!r}") from None 

def parse_list(text: str) -> list[str]:
    return [item.strip() for item in text.split(LIST_SEPARATOR) if item.strip()]

def parse_snake_list(text: str) -> list[str]:
    return [to_snake(item) for item in parse_list(text)]

def parse_bool(text: str) -> bool:
    lowered = text.lower()
    if lowered in {"true", "yes", "y", "1"}:
        return True
    if lowered in {"false", "no", "n", "0"}:
        return False
    raise ValueError(f"Not true/false: {text!r}")


# CSV column -> parser for a non-empty cell. Column names match Company fields.
FIELD_PARSERS: dict[str, Callable[[str], Any]] = {
    "description": str,
    "sector": to_snake,
    "business_model": to_snake,
    "target_customers": parse_snake_list,
    "stage": to_snake,
    "total_funding_usd": parse_usd,
    "last_funding_date": parse_date,
    "founded_date": parse_date,
    "employees": parse_int,
    "hq_country": str.upper,
    "investors": parse_list,
    "accelerator": str,
    "revenue_status": to_snake,
    "status": to_snake,
}


@dataclass
class RowError:
    line: int
    name: str
    reason: str


@dataclass
class ImportResult:
    imported: int = 0
    errors: list[RowError] = field(default_factory=list)
    unknown_columns: list[str] = field(default_factory=list)


def describe_error(error: ValueError) -> str:
    """Turn a parsing or validation error into one readable line."""
    if isinstance(error, ValidationError):
        return "; ".join(
            f"{'.'.join(str(part) for part in issue['loc'])}: {issue['msg']}"
            for issue in error.errors()
        )
    return str(error)


def row_to_company(row: dict[str | None, Any], provenance: Provenance) -> Company:
    """Build a Company from one CSV row. Empty cells are left UNKNOWN."""
    cells = {column: (value or "").strip() for column, value in row.items() if column}
    data: dict[str, Any] = {
        "name": cells.get("name", ""),
        "website": cells.get("website") or None,
        "existing_client": parse_bool(cells["existing_client"]) if cells.get("existing_client") else False,
    }
    for column, parse in FIELD_PARSERS.items():
        text = cells.get(column, "")
        if text:
            data[column] = {"value": parse(text), "provenance": provenance}
    return Company.model_validate(data)


def import_csv(connection: sqlite3.Connection, csv_path: Path, confidence: float) -> ImportResult:
    """Import every valid row; collect errors for the rest."""
    provenance = Provenance(
        source_type=SourceType.USER_CSV,
        method=ExtractionMethod.CSV_IMPORT,
        source_url=csv_path.name,
        confidence=confidence,
    )
    result = ImportResult()
    # utf-8-sig quietly removes the invisible marker Excel adds to the start of CSV files.
    with csv_path.open(newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.DictReader(csv_file)
        columns = set(reader.fieldnames or [])
        if "name" not in columns:
            raise ValueError(f"{csv_path.name} has no 'name' column")
        known_columns = IDENTITY_COLUMNS | IGNORED_COLUMNS | set(FIELD_PARSERS)
        result.unknown_columns = sorted(columns - known_columns)

        for row in reader:
            try:
                company = row_to_company(row, provenance)
            except ValueError as error:  # Pydantic's ValidationError is a ValueError too
                result.errors.append(
                    RowError(reader.line_num, (row.get("name") or "").strip(), describe_error(error))
                )
                continue
            save_company(connection, company)
            result.imported += 1
    return result