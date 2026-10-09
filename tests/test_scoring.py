import sqlite3
from datetime import date, timedelta
from typing import Any

import pytest

from autoscore.database import save_company
from autoscore.models import Company, ExtractionMethod, Provenance, Signal, SourceType, Tristate
from autoscore.scoring import (
    LineItem,
    ScoreResult,
    decay,
    format_explanation,
    funding_points,
    run_scoring,
    score_company,
)

AS_OF = date(2026, 10, 1)
CSV = Provenance(source_type=SourceType.USER_CSV, method=ExtractionMethod.CSV_IMPORT, confidence=0.8)
SITE = Provenance(
    source_type=SourceType.OFFICIAL_SITE,
    method=ExtractionMethod.RULE,
    source_url="https://exampleai.com",
    confidence=0.9,
)

CONFIG: dict[str, Any] = {
    "version": "test",
    "hard_exclusions": {
        "sector_gate": {"pass_if_any_true": ["is_tech_or_tech_adjacent", "sells_to_businesses"]},
        "stages_excluded": ["series_a"],
        "hq_country_required": "US",
        "statuses_excluded": ["acquired", "existing_client"],
    },
    "icp_fit": {
        "max_points": 22,
        "sector": {
            "max_points": 6,
            "preferred": {"values": ["ai"], "points": 6},
            "acceptable": {"values": ["crypto"], "points": 3},
        },
        "stage": {"max_points": 4, "preferred": {"values": ["seed"], "points": 4}},
        "total_funding_usd": {
            "max_points": 4,
            "zero_below": 100_000,
            "ramp_to_full_at": 500_000,
            "full_until": 1_000_000,
            "points_above_full": 2,
        },
        "company_age_months": {"max_points": 2, "full_between": [3, 24]},
        "employees": {"max_points": 2, "full_between": [2, 6], "partial": {"between": [7, 10], "points": 1}},
        "business_model": {"max_points": 1, "preferred": {"values": ["b2b_saas"], "points": 1}},
        "target_customers": {"max_points": 1, "businesses": {"values": ["smb"], "points": 1}},
        "firm_expertise": {"max_points": 2, "primary": {"values": ["lead_generation"], "points": 2}},
    },
    "service_need": {
        "max_points": 20,
        "per_service_cap": 10,
        "signals": {
            "capital_raising": {
                "investor_profile": {"accelerator_only": 7, "angels_only": 5},
                "stated_intent_to_raise": 5,
            },
            "ai_operations": {"multiple_support_channels": 3, "existing_ai_support_agent": -3},
        },
    },
    "urgency": {
        "max_points": 10,
        "signals": {
            "funding_timing": {
                "max_points": 8,
                "deployment_window": {"full_within_days": 90, "zero_after_days": 180},
                "next_raise_window": {"ramp_from_months": 3, "full_between_months": [6, 12], "zero_after_months": 18},
            },
            "recent_product_launch": {"max_points": 2, "full_within_days": 60, "zero_after_days": 180},
        },
    },
    "semantic_fit": {
        "max_points": 4,
        "positive_themes": {"ai": {"points": 2, "terms": ["ai"]}, "data": {"points": 2, "terms": ["data"]}},
        "negative_themes": {"agency": {"points": -2, "terms": ["agency"]}},
    },
}


def make_company(**facts: Any) -> Company:
    """A Company where every given fact is a known CSV value."""
    data: dict[str, Any] = {"name": "Example AI", "website": "exampleai.com"}
    for field_name, value in facts.items():
        data[field_name] = {"value": value, "provenance": CSV}
    return Company.model_validate(data)


def make_signal(name: str, value: Tristate = Tristate.TRUE, event_date: date | None = None) -> Signal:
    known = value is not Tristate.UNKNOWN
    return Signal(
        company_key="exampleai.com",
        name=name,
        value=value,
        evidence=f"evidence for {name}" if known else None,
        provenance=SITE if known else None,
        event_date=event_date,
    )


def find_item(result: ScoreResult, dimension_name: str, item_name: str) -> LineItem:
    for dimension in result.dimensions:
        if dimension.name == dimension_name:
            for item in dimension.items:
                if item.item == item_name:
                    return item
    raise AssertionError(f"No item {item_name!r} in {dimension_name}")


def dimension_points(result: ScoreResult, name: str) -> float:
    return next(dimension.points for dimension in result.dimensions if dimension.name == name)


@pytest.mark.parametrize(
    ("amount", "expected"),
    [(50_000, 0.0), (300_000, 2.0), (500_000, 4.0), (1_000_000, 4.0), (2_000_000, 2.0)],
)
def test_funding_curve(amount: int, expected: float) -> None:
    assert funding_points(CONFIG["icp_fit"]["total_funding_usd"], amount) == expected


def test_decay() -> None:
    assert decay(30, 90, 180) == 1.0
    assert decay(135, 90, 180) == 0.5
    assert decay(200, 90, 180) == 0.0


def test_unknown_company_scores_zero_with_zero_confidence() -> None:
    result = score_company(Company(name="Example AI", website="exampleai.com"), [], CONFIG, AS_OF)
    assert not result.excluded
    assert (result.total, result.confidence) == (0.0, 0.0)
    assert find_item(result, "icp_fit", "firm_expertise").evidence == "no data source yet (LLM stage)"


def test_points_come_with_evidence() -> None:
    company = make_company(sector="ai", stage="seed", hq_country="US")
    result = score_company(company, [make_signal("investor_profile.accelerator_only")], CONFIG, AS_OF)
    sector = find_item(result, "icp_fit", "sector")
    assert sector.points == 6
    assert "sector = ai (preferred)" in sector.evidence
    group = find_item(result, "service_need", "capital_raising: investor_profile")
    assert group.points == 7
    assert "evidence for investor_profile.accelerator_only" in group.evidence


def test_group_takes_best_true_option_only() -> None:
    signals = [make_signal("investor_profile.accelerator_only"), make_signal("investor_profile.angels_only")]
    result = score_company(make_company(hq_country="US"), signals, CONFIG, AS_OF)
    assert find_item(result, "service_need", "capital_raising: investor_profile").points == 7


def test_group_with_some_options_unknown_stays_unknown() -> None:
    signals = [make_signal("investor_profile.accelerator_only", Tristate.FALSE)]
    result = score_company(make_company(hq_country="US"), signals, CONFIG, AS_OF)
    assert find_item(result, "service_need", "capital_raising: investor_profile").known is False


def test_service_cap_negative_points_and_primary_service() -> None:
    signals = [
        make_signal("investor_profile.accelerator_only"),
        make_signal("stated_intent_to_raise"),
        make_signal("multiple_support_channels"),
        make_signal("existing_ai_support_agent"),
    ]
    result = score_company(make_company(hq_country="US"), signals, CONFIG, AS_OF)
    assert find_item(result, "service_need", "ai_operations: existing_ai_support_agent").points == -3
    assert dimension_points(result, "service_need") == 10  # capital_raising 12 capped at 10; ai_operations 3 - 3 = 0
    assert result.primary_service == "capital_raising"


@pytest.mark.parametrize("days_ago", [30, 274])  # deployment window; next-raise window (~9 months)
def test_funding_timing_uses_the_better_window(days_ago: int) -> None:
    company = make_company(hq_country="US", last_funding_date=AS_OF - timedelta(days=days_ago))
    result = score_company(company, [], CONFIG, AS_OF)
    assert find_item(result, "urgency", "funding_timing").points == 8.0


def test_dated_signal_decays_with_age() -> None:
    signals = [make_signal("recent_product_launch", event_date=AS_OF - timedelta(days=120))]
    result = score_company(make_company(hq_country="US"), signals, CONFIG, AS_OF)
    assert find_item(result, "urgency", "recent_product_launch").points == 1.0  # halfway between 60 and 180 days


def test_negative_theme_cannot_push_dimension_below_zero() -> None:
    result = score_company(make_company(hq_country="US"), [make_signal("theme.agency")], CONFIG, AS_OF)
    assert find_item(result, "semantic_fit", "theme: agency").points == -2
    assert dimension_points(result, "semantic_fit") == 0


def test_excluded_company_scores_zero_with_reason() -> None:
    result = score_company(make_company(hq_country="DE"), [], CONFIG, AS_OF)
    assert result.excluded
    assert result.total == 0
    assert result.exclusion_reasons == ["HQ country DE is not US"]


def test_confidence_reflects_known_inputs() -> None:
    company = make_company(
        sector="ai",
        stage="seed",
        total_funding_usd=500_000,
        founded_date=AS_OF - timedelta(days=300),
        employees=4,
        business_model="b2b_saas",
        target_customers=["smb"],
        hq_country="US",
    )
    result = score_company(company, [], CONFIG, AS_OF)
    icp = next(dimension for dimension in result.dimensions if dimension.name == "icp_fit")
    assert icp.points == 20
    assert icp.confidence == 0.73  # 20 of 22 possible points known, at reliability 0.8


def test_fingerprint_ignores_the_date() -> None:
    company = make_company(sector="ai", stage="seed", hq_country="US")
    today = score_company(company, [], CONFIG, AS_OF)
    tomorrow = score_company(company, [], CONFIG, AS_OF + timedelta(days=1))
    assert today.fingerprint() == tomorrow.fingerprint()


def test_explanation_marks_unknowns_and_earned_points() -> None:
    text = format_explanation(score_company(make_company(stage="seed", hq_country="US"), [], CONFIG, AS_OF))
    assert "sector: unknown" in text
    assert "stage = seed (preferred)" in text


def test_run_scoring_stores_only_changed_scores(connection: sqlite3.Connection) -> None:
    save_company(connection, make_company(sector="ai", stage="seed", hq_country="US"))
    first = run_scoring(connection, CONFIG, as_of=AS_OF)
    second = run_scoring(connection, CONFIG, as_of=AS_OF)
    assert [stored for _, stored in first] == [True]
    assert [stored for _, stored in second] == [False]
    assert connection.execute("SELECT COUNT(*) FROM scores").fetchone()[0] == 1