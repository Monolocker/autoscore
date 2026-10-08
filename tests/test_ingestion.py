import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from autoscore.database import connect, get_company, list_companies
from autoscore.ingestion import import_csv, parse_usd, to_snake
from autoscore.models import SourceType, Stage

CSV_TEXT = """\
name,website,stage,total_funding_usd,investors,employees,hq_country,analyst_verdict,mystery_column
Example AI,exampleai.com,Pre-Seed,$500k,Y Combinator; Angel One,4,us,strong_fit,x
Blank Facts Co,blankfacts.com,,,,,,,
Bad Stage Co,badstage.com,Series Z,,,,,,
,noname.com,seed,,,,,,
"""


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    db = connect(tmp_path / "test.sqlite")
    yield db
    db.close()


@pytest.fixture
def seed_csv(tmp_path: Path) -> Path:
    path = tmp_path / "seed.csv"
    path.write_text(CSV_TEXT, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("text", "expected"),
    [("$500k", 500_000), ("1.5M", 1_500_000), ("750,000", 750_000), ("250000", 250_000)],
)
def test_parse_usd(text: str, expected: int) -> None:
    assert parse_usd(text) == expected


@pytest.mark.parametrize("text", ["lots", "-5"])
def test_parse_usd_rejects_bad_input(text: str) -> None:
    with pytest.raises(ValueError):
        parse_usd(text)


@pytest.mark.parametrize(("text", "expected"), [("Pre-Seed", "pre_seed"), ("B2B SaaS", "b2b_saas")])
def test_to_snake(text: str, expected: str) -> None:
    assert to_snake(text) == expected


def test_import_reports_counts_errors_and_unknown_columns(
    connection: sqlite3.Connection, seed_csv: Path
) -> None:
    result = import_csv(connection, seed_csv, confidence=0.8)
    assert result.imported == 2
    assert [error.line for error in result.errors] == [4, 5]
    assert result.unknown_columns == ["mystery_column"]


def test_imported_values_are_parsed_and_sourced(connection: sqlite3.Connection, seed_csv: Path) -> None:
    import_csv(connection, seed_csv, confidence=0.8)
    company = get_company(connection, "exampleai.com")
    assert company is not None
    assert company.stage.value is Stage.PRE_SEED
    assert company.total_funding_usd.value == 500_000
    assert company.investors.value == ["Y Combinator", "Angel One"]
    assert company.employees.value == 4
    assert company.hq_country.value == "US"
    assert company.stage.provenance is not None
    assert company.stage.provenance.source_type is SourceType.USER_CSV
    assert company.stage.provenance.confidence == 0.8


def test_empty_cells_stay_unknown(connection: sqlite3.Connection, seed_csv: Path) -> None:
    import_csv(connection, seed_csv, confidence=0.8)
    company = get_company(connection, "blankfacts.com")
    assert company is not None
    assert company.stage.is_known is False
    assert company.employees.is_known is False


def test_reimport_does_not_duplicate(connection: sqlite3.Connection, seed_csv: Path) -> None:
    import_csv(connection, seed_csv, confidence=0.8)
    import_csv(connection, seed_csv, confidence=0.8)
    assert len(list_companies(connection)) == 2


def test_csv_without_name_column_rejected(connection: sqlite3.Connection, tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text("website\nexampleai.com\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no 'name' column"):
        import_csv(connection, path, confidence=0.8)