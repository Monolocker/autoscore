"""Hard exclusion gate: thin, deterministic checks that run before any enrichment.

A company is excluded only on known facts. unknown never excludes. Insteadm it is 
reported so an analyst can see which checks could not run.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from autoscore.models import Company

Config = dict[str, Any]


@dataclass
class ExclusionResult:
    reasons: list[str] = field(default_factory=list)
    unknown_checks: list[str] = field(default_factory=list)

    @property
    def excluded(self) -> bool:
        return bool(self.reasons)


def configured_values(section: dict[str, Any]) -> set[str]:
    """Union of every 'values' list one level below a config section (preferred, acceptable...)."""
    values: set[str] = set()
    for option in section.values():
        if isinstance(option, dict):
            values.update(option.get("values", []))
    return values


def is_tech_or_tech_adjacent(company: Company, config: Config) -> bool | None:
    sector = company.sector.value
    if sector is None:
        return None
    return sector in configured_values(config["icp_fit"]["sector"])


def sells_to_businesses(company: Company, config: Config) -> bool | None:
    business_model = company.business_model.value
    if business_model is not None:
        b2b_models = configured_values(config["icp_fit"]["business_model"])
        if business_model in b2b_models or business_model.startswith("b2b"):
            return True
    targets = company.target_customers.value
    if targets is not None:
        business_segments = set(config["icp_fit"]["target_customers"]["businesses"]["values"])
        return bool(business_segments & set(targets))
    return None


SECTOR_GATE_CHECKS: dict[str, Callable[[Company, Config], bool | None]] = {
    "is_tech_or_tech_adjacent": is_tech_or_tech_adjacent,
    "sells_to_businesses": sells_to_businesses,
}


def check_exclusions(company: Company, config: Config) -> ExclusionResult:
    rules = config["hard_exclusions"]
    result = ExclusionResult()

    # Sector gate: pass if any check is True; exclude only if every check is known False.
    gate_names = rules["sector_gate"]["pass_if_any_true"]
    gate_results: list[bool | None] = []
    for name in gate_names:
        if name not in SECTOR_GATE_CHECKS:
            raise ValueError(f"Unknown sector_gate check in config: {name}")
        gate_results.append(SECTOR_GATE_CHECKS[name](company, config))
    if not any(gate_results):
        if all(value is False for value in gate_results):
            result.reasons.append(f"Sector gate failed: {', '.join(gate_names)} all false")
        else:
            result.unknown_checks.append("sector_gate")

    stage = company.stage.value
    if stage is None:
        result.unknown_checks.append("stage")
    elif stage in rules["stages_excluded"]:
        result.reasons.append(f"Stage {stage} is excluded")

    country = company.hq_country.value
    required_country = rules["hq_country_required"]
    if country is None:
        result.unknown_checks.append("hq_country")
    elif country != required_country:
        result.reasons.append(f"HQ country {country} is not {required_country}")

    excluded_statuses = rules["statuses_excluded"]
    if company.existing_client and "existing_client" in excluded_statuses:
        result.reasons.append("Already a client")
    status = company.status.value
    if status is None:
        result.unknown_checks.append("status")
    elif status in excluded_statuses:
        result.reasons.append(f"Status {status} is excluded")

    return result