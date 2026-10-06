"""Core data models shared across the pipeline.

UNKNOWN is explicit: a Sourced value of None means "no evidence either way",
which is different from a known False or zero.
"""

import re
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from autoscore.urls import domain_from_url, normalize_website


class SourceType(StrEnum):
    """Where a fact came from. Values match confidence.source_reliability in the config."""

    USER_CSV = "user_csv"
    OFFICIAL_SITE = "official_site"
    YC_DIRECTORY = "yc_directory"
    PRESS = "press"
    LLM_EXTRACTED = "llm_extracted"
    INFERRED = "inferred"


class ExtractionMethod(StrEnum):
    CSV_IMPORT = "csv_import"
    HTML_PARSE = "html_parse"
    RULE = "rule"
    LLM = "llm"
    MANUAL = "manual"


class Stage(StrEnum):
    PRE_SEED = "pre_seed"
    SEED = "seed"
    PRE_SERIES_A_BRIDGE = "pre_series_a_bridge"
    SERIES_A = "series_a"
    SERIES_B = "series_b"
    SERIES_C_PLUS = "series_c_plus"


class CompanyStatus(StrEnum):
    ACTIVE = "active"
    ACQUIRED = "acquired"
    SHUT_DOWN = "shut_down"


class Tristate(StrEnum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


def utc_now() -> datetime:
    return datetime.now(UTC)


def slugify(text: str) -> str:
    """Lowercase text and replace every run of non-alphanumeric characters with "-"."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


class Provenance(BaseModel):
    """Where a value came from, how it was obtained, and how much to trust it."""

    model_config = ConfigDict(frozen=True)

    source_type: SourceType
    method: ExtractionMethod
    source_url: str | None = None
    observed_at: datetime = Field(default_factory=utc_now)
    confidence: float = Field(ge=0.0, le=1.0)


class Sourced[T](BaseModel):
    """A value plus its provenance. value=None means UNKNOWN."""

    model_config = ConfigDict(frozen=True)

    value: T | None = None
    provenance: Provenance | None = None

    @model_validator(mode="after")
    def known_values_need_provenance(self) -> Self:
        if self.value is not None and self.provenance is None:
            raise ValueError("A known value must have provenance")
        return self

    @property
    def is_known(self) -> bool:
        return self.value is not None


class Company(BaseModel):
    """A startup lead. Identity fields are plain; descriptive facts are Sourced."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1)
    website: str | None = None
    domain: str | None = None
    # Firm knowledge is authoritative, so this is a plain bool rather than Sourced.
    existing_client: bool = False

    description: Sourced[str] = Sourced()
    sector: Sourced[str] = Sourced()
    business_model: Sourced[str] = Sourced()
    target_customers: Sourced[list[str]] = Sourced()
    stage: Sourced[Stage] = Sourced()
    total_funding_usd: Sourced[int] = Sourced()
    last_funding_date: Sourced[date] = Sourced()
    founded_date: Sourced[date] = Sourced()
    employees: Sourced[int] = Sourced()
    hq_country: Sourced[str] = Sourced()
    investors: Sourced[list[str]] = Sourced()
    accelerator: Sourced[str] = Sourced()
    revenue_status: Sourced[str] = Sourced()
    status: Sourced[CompanyStatus] = Sourced()

    @field_validator("website")
    @classmethod
    def normalize_website_field(cls, website: str | None) -> str | None:
        if not website:
            return None
        return normalize_website(website)

    @field_validator("domain")
    @classmethod
    def normalize_domain_field(cls, domain: str | None) -> str | None:
        if not domain:
            return None
        return domain_from_url(domain)

    @model_validator(mode="after")
    def derive_domain(self) -> Self:
        if self.domain is None and self.website is not None:
            self.domain = domain_from_url(self.website)
        return self

    @property
    def key(self) -> str:
        """Stable identity: the domain when known, otherwise a slug of the name."""
        return self.domain or slugify(self.name)


class Signal(BaseModel):
    """One qualification signal for one company. The scorer assigns points later."""

    model_config = ConfigDict(str_strip_whitespace=True)

    company_key: str = Field(min_length=1)
    name: str = Field(min_length=1)
    value: Tristate = Tristate.UNKNOWN
    evidence: str | None = None
    provenance: Provenance | None = None
    # When the underlying event happened (e.g. a funding date); drives recency decay.
    event_date: date | None = None

    @model_validator(mode="after")
    def known_signals_need_evidence(self) -> Self:
        if self.value is not Tristate.UNKNOWN:
            if not self.evidence:
                raise ValueError(f"Signal '{self.name}' is {self.value} but has no evidence")
            if self.provenance is None:
                raise ValueError(f"Signal '{self.name}' is {self.value} but has no provenance")
        return self