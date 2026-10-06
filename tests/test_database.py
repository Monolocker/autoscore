import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from autoscore.database import (
    connect,
    get_company,
    get_signals,
    list_companies,
    list_tables,
    save_company,
    save_signal,
)
from autoscore.models import (
    Company,
    ExtractionMethod,
    Provenance,
    Signal,
    Sourced,
    SourceType,
    Stage,
    Tristate,
)


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """A fresh database file per test, in a temp folder pytest cleans up."""
    db = connect(tmp_path / "test.sqlite")
    yield db
    db.close()


def site_provenance() -> Provenance:
    return Provenance(
        source_type=SourceType.OFFICIAL_SITE,
        method=ExtractionMethod.HTML_PARSE,
        source_url="https://exampleai.com/careers",
        confidence=0.9,
    )


def example_company() -> Company:
    return Company(
        name="Example AI",
        website="exampleai.com",
        stage=Sourced[Stage](value=Stage.SEED, provenance=site_provenance()),
    )


def test_schema_creates_tables(connection: sqlite3.Connection) -> None:
    assert list_tables(connection) == ["companies", "signals"]


def test_company_round_trip(connection: sqlite3.Connection) -> None:
    company = example_company()
    save_company(connection, company)
    loaded = get_company(connection, "exampleai.com")
    assert loaded is not None
    assert loaded.model_dump() == company.model_dump()
    assert loaded.employees.is_known is False  # UNKNOWN survives storage


def test_saving_twice_updates_instead_of_duplicating(connection: sqlite3.Connection) -> None:
    save_company(connection, example_company())
    first_created_at = connection.execute("SELECT created_at FROM companies").fetchone()[0]

    renamed = example_company().model_copy(update={"name": "Example AI Inc"})
    save_company(connection, renamed)

    companies = list_companies(connection)
    assert len(companies) == 1
    assert companies[0].name == "Example AI Inc"
    created_at = connection.execute("SELECT created_at FROM companies").fetchone()[0]
    assert created_at == first_created_at


def test_missing_company_returns_none(connection: sqlite3.Connection) -> None:
    assert get_company(connection, "nope.com") is None


def test_signal_round_trip(connection: sqlite3.Connection) -> None:
    save_company(connection, example_company())
    signal = Signal(
        company_key="exampleai.com",
        name="hiring_first_sales_or_growth_role",
        value=Tristate.TRUE,
        evidence="Careers page lists 'Founding Account Executive'.",
        provenance=site_provenance(),
        event_date=date(2026, 9, 20),
    )
    save_signal(connection, signal)
    assert get_signals(connection, "exampleai.com") == [signal]


def test_signal_update_replaces_previous_value(connection: sqlite3.Connection) -> None:
    save_company(connection, example_company())
    save_signal(connection, Signal(company_key="exampleai.com", name="accelerator_only"))
    save_signal(
        connection,
        Signal(
            company_key="exampleai.com",
            name="accelerator_only",
            value=Tristate.TRUE,
            evidence="Only listed investor is Y Combinator.",
            provenance=site_provenance(),
        ),
    )
    signals = get_signals(connection, "exampleai.com")
    assert len(signals) == 1
    assert signals[0].value is Tristate.TRUE


def test_signal_for_unknown_company_rejected(connection: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        save_signal(connection, Signal(company_key="ghost.com", name="accelerator_only"))