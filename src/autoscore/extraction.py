"""Deterministic signal extraction: rules over company data and stored page text.

Every TRUE/FALSE signal carries evidence and provenance. When a rule cannot decide,
it returns UNKNOWN rather than guessing. Signals that need judgment (hiring intent,
founder backgrounds, fundraising intent) are left for the LLM stage.

Signal names match keys in config.yaml (a one-of group option is "group.option"),
so the scorer can look up their points.
"""

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from autoscore.database import list_companies, list_company_pages, save_signal
from autoscore.discovery import page_type_for_url, site_host
from autoscore.filters import check_exclusions
from autoscore.models import Company, ExtractionMethod, Provenance, Signal, SourceType, Tristate
from autoscore.parsing import Link, parse_html

MATCH_CONFIDENCE = 0.9      # a phrase, link, or widget was found
ABSENCE_CONFIDENCE = 0.6    # concluded from something NOT appearing on the pages we fetched

KNOWN_ACCELERATORS = {
    "y combinator", "techstars", "500 global", "antler", "entrepreneur first",
    "pioneer", "south park commons", "hf0", "neo",
}
INVESTOR_ALIASES = {"yc": "y combinator", "ycombinator": "y combinator"}

DEMO_TERMS = [
    "book a demo", "request a demo", "schedule a demo", "get a demo", "talk to sales",
    "contact sales", "join the waitlist", "join waitlist", "request access",
]
SLA_TERMS = [
    "SLA", "service level agreement", "dedicated support", "priority support", "premium support",
    "enterprise support", "24/7 support", "dedicated account manager",
]
HELP_WORDS = {"help", "docs", "documentation", "support", "kb", "knowledge-base", "help-center", "faq"}
HELP_LINK_TEXTS = {"docs", "documentation", "help", "help center", "support", "faq"}
HELP_HOSTS = ("intercom.help", "zendesk.com", "gitbook.io", "readme.io", "mintlify.app")
CHAT_WIDGETS = {
    "widget.intercom.io": "Intercom",
    "js.intercomcdn.com": "Intercom",
    "client.crisp.chat": "Crisp",
    "js.driftt.com": "Drift",
    "static.zdassets.com": "Zendesk",
    "embed.tawk.to": "Tawk.to",
    "wchat.freshchat.com": "Freshchat",
}
COMMUNITY_LINKS = {"discord.gg/": "Discord", "discord.com/invite": "Discord", "join.slack.com": "Slack community"}
AI_SUPPORT_WIDGETS = {
    "kapa.ai": "Kapa",
    "chatbase.co": "Chatbase",
    "ada.support": "Ada",
    "voiceflow.com": "Voiceflow",
    "inkeep.com": "Inkeep",
}
SUPPORT_EMAIL = re.compile(r"\b(?:support|help)@[\w.-]+\.[a-z]{2,}", re.IGNORECASE)


@dataclass
class PageSnapshot:
    url: str
    page_type: str | None
    html: str
    text: str


@dataclass
class CompanyEvidence:
    company: Company
    site_domain: str
    pages: list[PageSnapshot]  # homepage first
    homepage_links: list[Link] = field(default_factory=list)

    @property
    def homepage(self) -> PageSnapshot | None:
        return next((page for page in self.pages if page.page_type == "homepage"), None)

    def pages_of_type(self, page_type: str) -> list[PageSnapshot]:
        return [page for page in self.pages if page.page_type == page_type]


@dataclass
class ExtractionSummary:
    companies: int = 0
    excluded: int = 0
    counts: dict[str, int] = field(default_factory=lambda: {"true": 0, "false": 0, "unknown": 0})


# ---------- matching helpers ----------


def compile_terms(terms: list[str]) -> list[re.Pattern[str]]:
    """Case-insensitive whole-word patterns with flexible whitespace and an optional plural."""
    patterns = []
    for term in terms:
        body = r"\s+".join(re.escape(word) for word in term.split())
        patterns.append(re.compile(rf"(?<!\w){body}(?:e?s)?(?!\w)", re.IGNORECASE))
    return patterns


DEMO_PATTERNS = compile_terms(DEMO_TERMS)
SLA_PATTERNS = compile_terms(SLA_TERMS)


def snippet_around(line: str, match: re.Match[str], width: int = 80) -> str:
    start = max(match.start() - width, 0)
    end = min(match.end() + width, len(line))
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(line) else ""
    return f"{prefix}{line[start:end]}{suffix}"


def find_first(patterns: list[re.Pattern[str]], pages: list[PageSnapshot]) -> tuple[PageSnapshot, str] | None:
    """The first (page, snippet) where any pattern matches, checking pages in order."""
    for page in pages:
        for line in page.text.splitlines():
            for pattern in patterns:
                match = pattern.search(line)
                if match:
                    return page, snippet_around(line, match)
    return None


def site_signal(
    company: Company, name: str, value: Tristate, evidence: str, url: str, confidence: float
) -> Signal:
    return Signal(
        company_key=company.key,
        name=name,
        value=value,
        evidence=evidence,
        provenance=Provenance(
            source_type=SourceType.OFFICIAL_SITE,
            method=ExtractionMethod.RULE,
            source_url=url,
            confidence=confidence,
        ),
    )


def unknown(company: Company, name: str) -> Signal:
    return Signal(company_key=company.key, name=name)


# ---------- extractors (pure: evidence in, signals out) ----------


def normalize_investor(name: str) -> str:
    cleaned = " ".join(name.lower().split())
    return INVESTOR_ALIASES.get(cleaned, cleaned)


def investor_signal(evidence: CompanyEvidence) -> Signal:
    company = evidence.company
    name = "investor_profile.accelerator_only"
    investors = company.investors
    if not investors.value or investors.provenance is None:
        return unknown(company, name)

    accelerators = set(KNOWN_ACCELERATORS)
    if company.accelerator.value:
        accelerators.add(normalize_investor(company.accelerator.value))
    others = [investor for investor in investors.value if normalize_investor(investor) not in accelerators]

    listed = ", ".join(investors.value)
    if others:
        value, detail = Tristate.FALSE, f"non-accelerator investors: {', '.join(others)}"
    else:
        value, detail = Tristate.TRUE, "all are accelerators"
    # Derived from the investors field, so it inherits that field's source and confidence.
    return Signal(
        company_key=company.key,
        name=name,
        value=value,
        evidence=f"Investors listed: {listed}; {detail}",
        provenance=Provenance(
            source_type=investors.provenance.source_type,
            method=ExtractionMethod.RULE,
            source_url=investors.provenance.source_url,
            confidence=investors.provenance.confidence,
        ),
    )


def customer_logo_signal(evidence: CompanyEvidence) -> Signal:
    name = "launched_but_no_customer_logos"
    customer_pages = evidence.pages_of_type("customers")
    if customer_pages:
        url = customer_pages[0].url
        return site_signal(evidence.company, name, Tristate.FALSE, f"Customers page exists: {url}", url, MATCH_CONFIDENCE)
    return unknown(evidence.company, name)


def demo_without_pricing_signal(evidence: CompanyEvidence) -> Signal:
    name = "demo_or_waitlist_without_pricing"
    pricing_pages = evidence.pages_of_type("pricing")
    if pricing_pages:
        url = pricing_pages[0].url
        return site_signal(evidence.company, name, Tristate.FALSE, f"Pricing page exists: {url}", url, MATCH_CONFIDENCE)
    found = find_first(DEMO_PATTERNS, evidence.pages)
    if found is None:
        return unknown(evidence.company, name)
    page, snippet = found
    return site_signal(
        evidence.company,
        name,
        Tristate.TRUE,
        f'Call to action "{snippet}" and no pricing page linked from the homepage',
        page.url,
        ABSENCE_CONFIDENCE,
    )


def is_help_link(link: Link, site_domain: str) -> bool:
    if link.text.strip().lower() in HELP_LINK_TEXTS:
        return True
    host = site_host(link.url)
    if host.endswith(HELP_HOSTS):
        return True
    if host.endswith(f".{site_domain}"):
        return host.removesuffix(f".{site_domain}") in HELP_WORDS
    if host == site_domain:
        segments = [segment for segment in urlsplit(link.url).path.lower().split("/") if segment]
        return bool(segments) and segments[0] in HELP_WORDS
    return False


def help_center_signal(evidence: CompanyEvidence) -> Signal:
    name = "no_help_center_or_docs"
    homepage = evidence.homepage
    if homepage is None:
        return unknown(evidence.company, name)
    for link in evidence.homepage_links:
        if is_help_link(link, evidence.site_domain):
            return site_signal(
                evidence.company,
                name,
                Tristate.FALSE,
                f'Homepage links to "{link.text or link.url}": {link.url}',
                homepage.url,
                MATCH_CONFIDENCE,
            )
    return site_signal(
        evidence.company,
        name,
        Tristate.TRUE,
        "Homepage links include no help center, docs, or support page",
        homepage.url,
        ABSENCE_CONFIDENCE,
    )


def support_signals(evidence: CompanyEvidence) -> list[Signal]:
    company = evidence.company
    channels: dict[str, str] = {}  # channel label -> URL of the page where it was seen
    ai_agent: tuple[str, str] | None = None
    for page in evidence.pages:
        html = page.html.lower()
        for marker, label in CHAT_WIDGETS.items():
            if marker in html:
                channels.setdefault(f"chat widget ({label})", page.url)
        for marker, label in COMMUNITY_LINKS.items():
            if marker in html:
                channels.setdefault(label, page.url)
        if SUPPORT_EMAIL.search(page.html):
            channels.setdefault("support email", page.url)
        for marker, label in AI_SUPPORT_WIDGETS.items():
            if ai_agent is None and marker in html:
                ai_agent = (label, page.url)

    signals: list[Signal] = []
    if len(channels) >= 2:
        first_url = next(iter(channels.values()))
        signals.append(
            site_signal(
                company,
                "multiple_support_channels",
                Tristate.TRUE,
                f"Support channels found: {', '.join(channels)}",
                first_url,
                MATCH_CONFIDENCE,
            )
        )
    else:
        signals.append(unknown(company, "multiple_support_channels"))

    if ai_agent is not None:
        label, url = ai_agent
        signals.append(
            site_signal(
                company,
                "existing_ai_support_agent",
                Tristate.TRUE,
                f"{label} AI assistant embedded on {url}",
                url,
                MATCH_CONFIDENCE,
            )
        )
    else:
        signals.append(unknown(company, "existing_ai_support_agent"))
    return signals


def sla_signal(evidence: CompanyEvidence) -> Signal:
    name = "enterprise_sla_or_support_tiers"
    found = find_first(SLA_PATTERNS, evidence.pages)
    if found is not None:
        page, snippet = found
        return site_signal(evidence.company, name, Tristate.TRUE, f'"{snippet}"', page.url, MATCH_CONFIDENCE)
    pricing_pages = evidence.pages_of_type("pricing")
    if pricing_pages:
        url = pricing_pages[0].url
        return site_signal(
            evidence.company,
            name,
            Tristate.FALSE,
            "No SLA or support-tier wording on the pricing page",
            url,
            ABSENCE_CONFIDENCE,
        )
    return unknown(evidence.company, name)


def theme_signals(evidence: CompanyEvidence, config: dict[str, Any]) -> list[Signal]:
    company = evidence.company
    semantic = config["semantic_fit"]
    themes = {**semantic.get("positive_themes", {}), **semantic.get("negative_themes", {})}
    signals: list[Signal] = []
    for theme_name, theme in themes.items():
        name = f"theme.{theme_name}"
        if not evidence.pages:
            signals.append(unknown(company, name))
            continue
        found = find_first(compile_terms(theme["terms"]), evidence.pages)
        if found is not None:
            page, snippet = found
            signals.append(site_signal(company, name, Tristate.TRUE, f'"{snippet}"', page.url, MATCH_CONFIDENCE))
        else:
            terms = ", ".join(theme["terms"])
            signals.append(
                site_signal(
                    company,
                    name,
                    Tristate.FALSE,
                    f"No match for [{terms}] on {len(evidence.pages)} fetched page(s)",
                    evidence.pages[0].url,
                    ABSENCE_CONFIDENCE,
                )
            )
    return signals


def extract_signals(evidence: CompanyEvidence, config: dict[str, Any]) -> list[Signal]:
    """Every deterministic signal for one company. Always returns the same set of names,
    so a re-run overwrites any stale value (a TRUE that is now UNKNOWN gets replaced)."""
    return [
        investor_signal(evidence),
        customer_logo_signal(evidence),
        demo_without_pricing_signal(evidence),
        help_center_signal(evidence),
        *support_signals(evidence),
        sla_signal(evidence),
        *theme_signals(evidence, config),
    ]


# ---------- I/O edge: load evidence from the database, store signals ----------


def load_evidence(connection: sqlite3.Connection, company: Company) -> CompanyEvidence:
    rows = list_company_pages(connection, company.key)
    homepage_row = next((row for row in rows if row["url"] == company.website), None)
    if homepage_row is not None:
        base_url = homepage_row["final_url"] or homepage_row["url"]
    else:
        base_url = company.website or ""
    site_domain = site_host(base_url)

    pages = [
        PageSnapshot(
            url=row["url"],
            page_type=page_type_for_url(row["url"], site_domain),
            html=row["html"],
            text=row["text"],
        )
        for row in rows
    ]
    pages.sort(key=lambda page: page.page_type != "homepage")  # homepage first, others keep URL order
    links = parse_html(homepage_row["html"], base_url).links if homepage_row is not None else []
    return CompanyEvidence(company=company, site_domain=site_domain, pages=pages, homepage_links=links)


def run_extraction(connection: sqlite3.Connection, config: dict[str, Any]) -> ExtractionSummary:
    """Extract and store signals for every company that passes the exclusion gate."""
    summary = ExtractionSummary()
    for company in list_companies(connection):
        if check_exclusions(company, config).excluded:
            summary.excluded += 1
            continue
        evidence = load_evidence(connection, company)
        for signal in extract_signals(evidence, config):
            save_signal(connection, signal)
            summary.counts[str(signal.value)] += 1
        summary.companies += 1
    return summary