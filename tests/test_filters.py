import copy
from typing import Any

import pytest

from autoscore.filters import check_exclusions
from autoscore.models import Company, ExtractionMethod, Provenance, SourceType

PROVENANCE = Provenance(source_type=SourceType.USER_CSV, method=ExtractionMethod.CSV_IMPORT, confidence=0.8)

CONFIG: dict[str, Any] = {
    "hard_exclusions": {
        "sector_gate": {"pass_if_any_true": ["is_tech_or_tech_adjacent", "sells_to_businesses"]},
        "stages_excluded": ["series_a", "series_b", "series_c_plus"],
        "hq_country_required": "US",
        "statuses_excluded": ["acquired", "existing_client", "shut_down"],
    },
    "icp_fit": {
        "sector": {
            "max_points": 6,
            "preferred": {"values": ["ai", "saas"], "points": 6},
            "acceptable": {"values": ["crypto"], "points": 4},
        },
        "business_model": {"max_points": 3, "preferred": {"values": ["b2b_saas"], "points": 3}},
        "target_customers": {
            "max_points": 4,
            "businesses": {"values": ["smb", "enterprise"], "points": 4},
            "consumers": {"values": ["consumer"], "points": 2},
        },
    },
}

US_TECH: dict[str, Any] = {"sector": "ai", "stage": "seed", "hq_country": "US", "status": "active"}


def make_company(existing_client: bool = False, **facts: Any) -> Company:
    """Build a Company where every given fact is a known CSV value."""
    data: dict[str, Any] = {"name": "Example AI", "existing_client": existing_client}
    for field_name, value in facts.items():
        data[field_name] = {"value": value, "provenance": PROVENANCE}
    return Company.model_validate(data)


def test_known_good_company_passes() -> None:
    result = check_exclusions(make_company(**US_TECH), CONFIG)
    assert not result.excluded
    assert result.unknown_checks == []


def test_unknown_facts_never_exclude() -> None:
    result = check_exclusions(make_company(), CONFIG)
    assert not result.excluded
    assert result.unknown_checks == ["sector_gate", "stage", "hq_country", "status"]


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"stage": "series_a"}, "Stage series_a is excluded"),
        ({"hq_country": "DE"}, "HQ country DE is not US"),
        ({"status": "acquired"}, "Status acquired is excluded"),
    ],
)
def test_known_disqualifying_fact_excludes(override: dict[str, Any], reason: str) -> None:
    result = check_exclusions(make_company(**{**US_TECH, **override}), CONFIG)
    assert result.reasons == [reason]


def test_existing_client_is_excluded() -> None:
    result = check_exclusions(make_company(existing_client=True, **US_TECH), CONFIG)
    assert result.reasons == ["Already a client"]


def test_non_tech_consumer_company_fails_sector_gate() -> None:
    company = make_company(
        sector="restaurants", target_customers=["consumer"], stage="seed", hq_country="US", status="active"
    )
    assert check_exclusions(company, CONFIG).excluded


def test_non_tech_b2b_company_passes_sector_gate() -> None:
    company = make_company(
        sector="logistics", business_model="b2b_services", stage="seed", hq_country="US", status="active"
    )
    assert not check_exclusions(company, CONFIG).excluded


def test_unknown_gate_check_in_config_is_rejected() -> None:
    bad_config = copy.deepcopy(CONFIG)
    bad_config["hard_exclusions"]["sector_gate"]["pass_if_any_true"] = ["is_profitable"]
    with pytest.raises(ValueError, match="is_profitable"):
        check_exclusions(make_company(**US_TECH), bad_config)