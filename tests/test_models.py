from datetime import date

import pytest
from pydantic import ValidationError

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
from autoscore.urls import domain_from_url, normalize_website


def csv_provenance() -> Provenance:
    return Provenance(
        source_type=SourceType.USER_CSV,
        method=ExtractionMethod.CSV_IMPORT,
        confidence=0.8,
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("exampleai.com", "https://exampleai.com"),
        ("https://www.ExampleAI.com/", "https://www.exampleai.com"),
        ("  http://exampleai.com/about/  ", "http://exampleai.com/about"),
    ],
)
def test_normalize_website(raw: str, expected: str) -> None:
    assert normalize_website(raw) == expected


@pytest.mark.parametrize("raw", ["", "not a url", "ftp://exampleai.com"])
def test_normalize_website_rejects_bad_input(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_website(raw)


def test_domain_strips_www_and_path() -> None:
    assert domain_from_url("https://www.ExampleAI.com/pricing") == "exampleai.com"


def test_company_derives_domain_and_key() -> None:
    company = Company(name="Example AI", website="exampleai.com")
    assert company.website == "https://exampleai.com"
    assert company.domain == "exampleai.com"
    assert company.key == "exampleai.com"


def test_company_without_website_uses_name_slug() -> None:
    company = Company(name="  Acme AI, Inc. ", website="")
    assert company.website is None
    assert company.key == "acme-ai-inc"


def test_missing_facts_are_unknown_not_false() -> None:
    company = Company(name="Example AI")
    assert company.stage.is_known is False
    assert company.stage.value is None
    assert company.employees.value is None  # unknown, not zero


def test_known_value_requires_provenance() -> None:
    with pytest.raises(ValidationError):
        Sourced[Stage](value=Stage.SEED)


def test_invalid_stage_rejected() -> None:
    with pytest.raises(ValidationError):
        Sourced[Stage](value="series_z", provenance=csv_provenance())


def test_confidence_must_be_between_0_and_1() -> None:
    with pytest.raises(ValidationError):
        Provenance(source_type=SourceType.PRESS, method=ExtractionMethod.RULE, confidence=1.5)


def test_known_signal_requires_evidence() -> None:
    with pytest.raises(ValidationError):
        Signal(
            company_key="exampleai.com",
            name="accelerator_only",
            value=Tristate.TRUE,
            provenance=csv_provenance(),
        )


def test_unknown_signal_needs_no_evidence() -> None:
    signal = Signal(company_key="exampleai.com", name="accelerator_only")
    assert signal.value is Tristate.UNKNOWN


def test_company_round_trips_through_json() -> None:
    company = Company(
        name="Example AI",
        website="exampleai.com",
        stage=Sourced[Stage](value=Stage.PRE_SEED, provenance=csv_provenance()),
        total_funding_usd=Sourced[int](value=500_000, provenance=csv_provenance()),
        last_funding_date=Sourced[date](value=date(2026, 3, 1), provenance=csv_provenance()),
    )
    restored = Company.model_validate_json(company.model_dump_json())
    assert restored.model_dump() == company.model_dump()