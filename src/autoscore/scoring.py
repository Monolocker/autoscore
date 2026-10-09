"""Deterministic, explainable lead scoring driven by respective config.yaml.

Every point is a LineItem carrying its evidence. UNKNOWN inputs earn 0 points and
lower confidence; they are never treated as FALSE. Excluded companies score 0.
"""

import hashlib
import sqlite3
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from autoscore.config import total_weight
from autoscore.database import get_signals, latest_score_row, list_companies, save_score
from autoscore.filters import check_exclusions
from autoscore.models import Company, Signal, Sourced, Tristate

Config = dict[str, Any]
DAYS_PER_MONTH = 30.44

# icp_fit sections scored by matching a Company field against option values.
# None means there is no data source yet (the LLM stage will fill it).
OPTION_FIELDS: dict[str, str | None] = {
    "sector": "sector",
    "stage": "stage",
    "business_model": "business_model",
    "target_customers": "target_customers",
    "firm_expertise": None,
}


class LineItem(BaseModel):
    item: str
    points: float
    max_points: float
    known: bool
    reliability: float = 0.0  # confidence of the evidence behind a known item
    evidence: str


class DimensionScore(BaseModel):
    name: str
    points: float
    max_points: float
    confidence: float
    items: list[LineItem]


class ScoreResult(BaseModel):
    company_key: str
    company_name: str
    as_of: date
    config_version: str
    excluded: bool = False
    exclusion_reasons: list[str] = Field(default_factory=list)
    total: float = 0.0
    max_total: float = 0.0
    confidence: float = 0.0
    primary_service: str | None = None
    dimensions: list[DimensionScore] = Field(default_factory=list)

    def fingerprint(self) -> str:
        """Hash of the result without its date, so an unchanged score is recognized."""
        payload = self.model_dump_json(exclude={"as_of"})
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------- math helpers ----------


def linear(value: float, start: float, end: float) -> float:
    """Fraction of the way from start to end, clamped to [0, 1]."""
    if end <= start:
        return 1.0 if value >= end else 0.0
    return min(max((value - start) / (end - start), 0.0), 1.0)


def decay(value: float, full_until: float, zero_at: float) -> float:
    """1.0 up to full_until, falling linearly to 0.0 at zero_at."""
    return 1.0 - linear(value, full_until, zero_at)


def funding_points(section: dict[str, Any], amount: int) -> float:
    """Ramp up from zero_below to ramp_to_full_at, full until full_until, then points_above_full."""
    if amount > section["full_until"]:
        return float(section["points_above_full"])
    return float(section["max_points"]) * linear(amount, section["zero_below"], section["ramp_to_full_at"])


# ---------- line item builders ----------


def format_value(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def unknown_item(item: str, max_points: float, note: str = "unknown") -> LineItem:
    return LineItem(item=item, points=0.0, max_points=max_points, known=False, evidence=note)


def fact_item(item: str, points: float, max_points: float, sourced: Sourced[Any], detail: str) -> LineItem:
    """A scored item backed by a known Company fact."""
    if sourced.provenance is None:
        raise ValueError(f"{item}: known value without provenance")
    return LineItem(
        item=item,
        points=round(points, 2),
        max_points=max_points,
        known=True,
        reliability=sourced.provenance.confidence,
        evidence=f"{detail} [{sourced.provenance.source_type}]",
    )


def signal_item(item: str, points: float, max_points: float, signal: Signal, detail: str | None = None) -> LineItem:
    """A scored item backed by a known signal."""
    reliability = signal.provenance.confidence if signal.provenance else 0.0
    return LineItem(
        item=item,
        points=round(points, 2),
        max_points=max_points,
        known=True,
        reliability=reliability,
        evidence=f"{signal.value}: {detail or signal.evidence}",
    )


def dimension_confidence(items: list[LineItem]) -> float:
    possible = sum(item.max_points for item in items)
    if possible == 0:
        return 0.0
    known = sum(item.max_points * item.reliability for item in items if item.known)
    return round(known / possible, 2)


def build_dimension(
    name: str, max_points: float, items: list[LineItem], raw_points: float | None = None
) -> DimensionScore:
    raw = sum(item.points for item in items) if raw_points is None else raw_points
    return DimensionScore(
        name=name,
        points=round(min(max(raw, 0.0), max_points), 2),
        max_points=max_points,
        confidence=dimension_confidence(items),
        items=items,
    )


# ---------- ICP fit ----------


def best_option(section: dict[str, Any], value: Any) -> tuple[float, str]:
    """Highest-scoring option whose values match; a list matches if any element does."""
    candidates = {str(item) for item in (value if isinstance(value, list) else [value])}
    best_points, best_name = 0.0, "no match"
    for option_name, option in section.items():
        if isinstance(option, dict) and "values" in option and candidates & set(option["values"]):
            if option["points"] > best_points:
                best_points, best_name = float(option["points"]), option_name
    return best_points, best_name


def score_option_item(name: str, section: dict[str, Any], sourced: Sourced[Any] | None) -> LineItem:
    max_points = section["max_points"]
    if sourced is None:
        return unknown_item(name, max_points, "no data source yet (LLM stage)")
    if not sourced.is_known:
        return unknown_item(name, max_points)
    points, option = best_option(section, sourced.value)
    return fact_item(name, points, max_points, sourced, f"{name} = {format_value(sourced.value)} ({option})")


def score_funding_item(section: dict[str, Any], sourced: Sourced[int]) -> LineItem:
    if sourced.value is None:
        return unknown_item("total_funding_usd", section["max_points"])
    points = funding_points(section, sourced.value)
    return fact_item("total_funding_usd", points, section["max_points"], sourced, f"total funding ${sourced.value:,}")


def score_age_item(section: dict[str, Any], sourced: Sourced[date], as_of: date) -> LineItem:
    if sourced.value is None:
        return unknown_item("company_age_months", section["max_points"])
    months = (as_of - sourced.value).days / DAYS_PER_MONTH
    low, high = section["full_between"]
    points = section["max_points"] if low <= months <= high else 0.0
    detail = f"founded {sourced.value} ({months:.0f} months ago)"
    return fact_item("company_age_months", points, section["max_points"], sourced, detail)


def score_employees_item(section: dict[str, Any], sourced: Sourced[int]) -> LineItem:
    if sourced.value is None:
        return unknown_item("employees", section["max_points"])
    count = sourced.value
    low, high = section["full_between"]
    points = 0.0
    if low <= count <= high:
        points = section["max_points"]
    elif "partial" in section:
        partial_low, partial_high = section["partial"]["between"]
        if partial_low <= count <= partial_high:
            points = section["partial"]["points"]
    return fact_item("employees", points, section["max_points"], sourced, f"{count} employees")


def score_icp(section: dict[str, Any], company: Company, as_of: date) -> DimensionScore:
    items: list[LineItem] = []
    for name, sub in section.items():
        if name == "max_points":
            continue
        if name in OPTION_FIELDS:
            field_name = OPTION_FIELDS[name]
            sourced = getattr(company, field_name) if field_name else None
            items.append(score_option_item(name, sub, sourced))
        elif name == "total_funding_usd":
            items.append(score_funding_item(sub, company.total_funding_usd))
        elif name == "company_age_months":
            items.append(score_age_item(sub, company.founded_date, as_of))
        elif name == "employees":
            items.append(score_employees_item(sub, company.employees))
        else:
            raise ValueError(f"Unknown icp_fit section in config: {name}")
    return build_dimension("icp_fit", section["max_points"], items)


# ---------- service need ----------


def score_signal_entry(label: str, signal_name: str, points: float, signals: dict[str, Signal]) -> LineItem:
    signal = signals.get(signal_name)
    if signal is None or signal.value is Tristate.UNKNOWN:
        return unknown_item(label, abs(points))
    earned = points if signal.value is Tristate.TRUE else 0.0
    return signal_item(label, earned, abs(points), signal)


def score_group_entry(label: str, group: str, options: dict[str, float], signals: dict[str, Signal]) -> LineItem:
    """One-of group: the best TRUE option scores. Known-zero only if every option is FALSE."""
    max_points = max(options.values())
    best: tuple[float, Signal] | None = None
    all_false = True
    for option, points in options.items():
        signal = signals.get(f"{group}.{option}")
        if signal is None or signal.value is not Tristate.FALSE:
            all_false = False
        if signal is not None and signal.value is Tristate.TRUE and (best is None or points > best[0]):
            best = (points, signal)

    if best is not None:
        points, signal = best
        option_name = signal.name.split(".")[-1]
        return signal_item(label, points, max_points, signal, f"{option_name} ({signal.evidence})")
    if all_false:
        first_signal = signals[f"{group}.{next(iter(options))}"]
        return signal_item(label, 0.0, max_points, first_signal, "every option is false")
    return unknown_item(label, max_points)


def score_service_need(section: dict[str, Any], signals: dict[str, Signal]) -> tuple[DimensionScore, str | None]:
    cap = section["per_service_cap"]
    items: list[LineItem] = []
    service_points: dict[str, float] = {}
    for service, entries in section["signals"].items():
        service_items: list[LineItem] = []
        for entry_name, entry in entries.items():
            label = f"{service}: {entry_name}"
            if isinstance(entry, dict):
                service_items.append(score_group_entry(label, entry_name, entry, signals))
            else:
                service_items.append(score_signal_entry(label, entry_name, entry, signals))
        items.extend(service_items)
        service_points[service] = min(max(sum(item.points for item in service_items), 0.0), cap)

    best_service = max(service_points, key=lambda service: service_points[service], default=None)
    primary = best_service if best_service is not None and service_points[best_service] > 0 else None
    dimension = build_dimension("service_need", section["max_points"], items, raw_points=sum(service_points.values()))
    return dimension, primary


# ---------- urgency ----------


def score_funding_timing(entry: dict[str, Any], sourced: Sourced[date], as_of: date) -> LineItem:
    max_points = entry["max_points"]
    if sourced.value is None:
        return unknown_item("funding_timing", max_points)
    days = max((as_of - sourced.value).days, 0)

    deploy = entry["deployment_window"]
    deployment = decay(days, deploy["full_within_days"], deploy["zero_after_days"])

    raise_window = entry["next_raise_window"]
    months = days / DAYS_PER_MONTH
    low, high = raise_window["full_between_months"]
    if months < low:
        next_raise = linear(months, raise_window["ramp_from_months"], low)
    elif months <= high:
        next_raise = 1.0
    else:
        next_raise = decay(months, high, raise_window["zero_after_months"])

    window = "deployment window" if deployment >= next_raise else "next-raise window"
    detail = f"last funding {sourced.value} ({days} days ago), {window}"
    return fact_item("funding_timing", max_points * max(deployment, next_raise), max_points, sourced, detail)


def score_dated_signal(name: str, entry: dict[str, Any], signals: dict[str, Signal], as_of: date) -> LineItem:
    max_points = entry["max_points"]
    signal = signals.get(name)
    if signal is None or signal.value is Tristate.UNKNOWN:
        return unknown_item(name, max_points)
    if signal.value is Tristate.FALSE:
        return signal_item(name, 0.0, max_points, signal)
    if signal.event_date is None:
        return unknown_item(name, max_points, f"true but undated, so recency is unknown: {signal.evidence}")
    days = max((as_of - signal.event_date).days, 0)
    fraction = decay(days, entry["full_within_days"], entry["zero_after_days"])
    return signal_item(name, max_points * fraction, max_points, signal, f"{signal.evidence} ({days} days ago)")


def score_urgency(section: dict[str, Any], company: Company, signals: dict[str, Signal], as_of: date) -> DimensionScore:
    items: list[LineItem] = []
    for name, entry in section["signals"].items():
        if name == "funding_timing":  # computed from the company's last funding date
            items.append(score_funding_timing(entry, company.last_funding_date, as_of))
        else:
            items.append(score_dated_signal(name, entry, signals, as_of))
    return build_dimension("urgency", section["max_points"], items)


# ---------- semantic fit ----------


def score_semantic(section: dict[str, Any], signals: dict[str, Signal]) -> DimensionScore:
    items: list[LineItem] = []
    for group in ("positive_themes", "negative_themes"):
        for theme_name, theme in section.get(group, {}).items():
            items.append(score_signal_entry(f"theme: {theme_name}", f"theme.{theme_name}", theme["points"], signals))
    return build_dimension("semantic_fit", section["max_points"], items)


# ---------- company score ----------


def score_company(company: Company, signals: list[Signal], config: Config, as_of: date) -> ScoreResult:
    result = ScoreResult(
        company_key=company.key,
        company_name=company.name,
        as_of=as_of,
        config_version=str(config["version"]),
        max_total=float(total_weight(config)),
    )
    exclusion = check_exclusions(company, config)
    if exclusion.excluded:
        result.excluded = True
        result.exclusion_reasons = exclusion.reasons
        return result

    by_name = {signal.name: signal for signal in signals}
    need, primary = score_service_need(config["service_need"], by_name)
    dimensions = [
        score_icp(config["icp_fit"], company, as_of),
        need,
        score_urgency(config["urgency"], company, by_name, as_of),
        score_semantic(config["semantic_fit"], by_name),
    ]
    weight = sum(dimension.max_points for dimension in dimensions)
    result.dimensions = dimensions
    result.primary_service = primary
    result.total = round(sum(dimension.points for dimension in dimensions), 2)
    if weight:
        result.confidence = round(sum(d.max_points * d.confidence for d in dimensions) / weight, 2)
    return result


def format_explanation(result: ScoreResult) -> str:
    lines = [
        f"{result.company_name} ({result.company_key}): {result.total:.1f}/{result.max_total:g}, "
        f"confidence {result.confidence:.2f}, as of {result.as_of}"
    ]
    if result.excluded:
        lines.append(f"EXCLUDED: {'; '.join(result.exclusion_reasons)}")
        return "\n".join(lines)

    lines.append(f"Primary service: {result.primary_service or 'none yet'}")
    for dimension in result.dimensions:
        lines.append("")
        lines.append(
            f"{dimension.name}  {dimension.points:.1f}/{dimension.max_points:g}  "
            f"(confidence {dimension.confidence:.2f})"
        )
        for item in dimension.items:
            if not item.known:
                marker = "?"
            elif item.points > 0:
                marker = "+"
            elif item.points < 0:
                marker = "-"
            else:
                marker = "0"
            lines.append(f"  {marker} {item.points:>5.1f}/{item.max_points:<3g} {item.item}: {item.evidence}")
    lines.append("")
    lines.append("Legend: + earned, - penalty, 0 known but no points, ? unknown (research gap)")
    return "\n".join(lines)


# ---------- I/O edge ----------


def run_scoring(
    connection: sqlite3.Connection, config: Config, as_of: date | None = None
) -> list[tuple[ScoreResult, bool]]:
    """Score every company; store a new score row only when the result changed."""
    as_of = as_of or date.today()
    results: list[tuple[ScoreResult, bool]] = []
    for company in list_companies(connection):
        result = score_company(company, get_signals(connection, company.key), config, as_of)
        stored = save_score(
            connection,
            company.key,
            result.total,
            result.confidence,
            result.excluded,
            result.fingerprint(),
            result.model_dump_json(),
        )
        results.append((result, stored))
    return results


def load_latest_score(connection: sqlite3.Connection, company_key: str) -> ScoreResult | None:
    row = latest_score_row(connection, company_key)
    if row is None:
        return None
    return ScoreResult.model_validate_json(row["result_json"])