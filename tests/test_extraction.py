import sqlite3
from typing import Any

from autoscore.config import EXAMPLE_CONFIG_PATH, load_config
from autoscore.database import get_signals, save_company, save_page
from autoscore.discovery import page_type_for_url
from autoscore.enrichment import parse_pages
from autoscore.extraction import CompanyEvidence, PageSnapshot, extract_signals, run_extraction
from autoscore.fetcher import FetchResult
from autoscore.models import Company, Signal, Tristate
from autoscore.parsing import parse_html

SITE = "exampleai.com"
CSV_PROVENANCE = {"source_type": "user_csv", "method": "csv_import", "source_url": "seed.csv", "confidence": 0.8}
CONFIG: dict[str, Any] = {
    "semantic_fit": {
        "positive_themes": {
            "ai": {"points": 1, "terms": ["ai", "machine learning"]},
            "lead_gen": {"points": 2, "terms": ["lead generation", "enrichment"]},
        },
        "negative_themes": {"agency": {"points": -2, "terms": ["agency"]}},
    }
}


def make_page(path: str, body: str) -> PageSnapshot:
    url = f"https://{SITE}{path}"
    html = f"<html><body>{body}</body></html>"
    return PageSnapshot(url=url, page_type=page_type_for_url(url, SITE), html=html, text=parse_html(html, url).text)


def extract(*pages: PageSnapshot, **company_facts: Any) -> dict[str, Signal]:
    """Run every extractor on the given pages and known CSV facts; return signals by name."""
    data: dict[str, Any] = {"name": "Example AI", "website": SITE}
    for field_name, value in company_facts.items():
        data[field_name] = {"value": value, "provenance": CSV_PROVENANCE}
    company = Company.model_validate(data)
    homepage = next((page for page in pages if page.page_type == "homepage"), None)
    links = parse_html(homepage.html, homepage.url).links if homepage else []
    evidence = CompanyEvidence(company=company, site_domain=SITE, pages=list(pages), homepage_links=links)
    return {signal.name: signal for signal in extract_signals(evidence, CONFIG)}


HOME = make_page("", "<h1>AI-powered lead enrichment</h1><p>Said simply: we help sales teams.</p>")


def test_accelerator_only_true_for_yc_alias() -> None:
    assert extract(HOME, investors=["YC"])["investor_profile.accelerator_only"].value is Tristate.TRUE


def test_accelerator_only_false_with_institutional_investor() -> None:
    signal = extract(HOME, investors=["Y Combinator", "Sequoia"])["investor_profile.accelerator_only"]
    assert signal.value is Tristate.FALSE
    assert "Sequoia" in (signal.evidence or "")


def test_accelerator_only_unknown_without_investor_data() -> None:
    assert extract(HOME)["investor_profile.accelerator_only"].value is Tristate.UNKNOWN


def test_customers_page_means_customer_logos_present() -> None:
    signals = extract(HOME, make_page("/customers", "<p>Our customers</p>"))
    assert signals["launched_but_no_customer_logos"].value is Tristate.FALSE


def test_demo_cta_without_pricing_page() -> None:
    home = make_page("", "<a href='/demo'>Book a demo</a>")
    signal = extract(home)["demo_or_waitlist_without_pricing"]
    assert signal.value is Tristate.TRUE
    assert signal.provenance is not None
    assert signal.provenance.confidence == 0.6


def test_pricing_page_rules_out_demo_without_pricing() -> None:
    home = make_page("", "<a href='/pricing'>Pricing</a> <a href='/demo'>Book a demo</a>")
    signal = extract(home, make_page("/pricing", "<p>$49/month</p>"))["demo_or_waitlist_without_pricing"]
    assert signal.value is Tristate.FALSE


def test_help_center_link_detected() -> None:
    home = make_page("", "<a href='https://docs.exampleai.com'>Docs</a>")
    assert extract(home)["no_help_center_or_docs"].value is Tristate.FALSE


def test_missing_help_center_is_true_with_low_confidence() -> None:
    signal = extract(HOME)["no_help_center_or_docs"]
    assert signal.value is Tristate.TRUE
    assert signal.provenance is not None
    assert signal.provenance.confidence == 0.6


def test_two_support_channels_detected() -> None:
    home = make_page(
        "",
        "<script src='https://widget.intercom.io/widget/abc'></script><p>Email support@exampleai.com</p>",
    )
    signal = extract(home)["multiple_support_channels"]
    assert signal.value is Tristate.TRUE
    assert "support email" in (signal.evidence or "")


def test_single_support_channel_stays_unknown() -> None:
    home = make_page("", "<p>Email support@exampleai.com</p>")
    assert extract(home)["multiple_support_channels"].value is Tristate.UNKNOWN


def test_ai_support_agent_detected() -> None:
    home = make_page("", "<script src='https://widget.kapa.ai/kapa-widget.bundle.js'></script>")
    assert extract(home)["existing_ai_support_agent"].value is Tristate.TRUE


def test_sla_language_detected_with_snippet() -> None:
    home = make_page("", "<p>Enterprise plans include a 99.9% uptime SLA.</p>")
    signal = extract(home)["enterprise_sla_or_support_tiers"]
    assert signal.value is Tristate.TRUE
    assert "uptime SLA" in (signal.evidence or "")


def test_themes_found_and_absent() -> None:
    signals = extract(HOME)
    assert signals["theme.ai"].value is Tristate.TRUE  # "AI-powered"
    assert signals["theme.lead_gen"].value is Tristate.TRUE  # "enrichment"
    assert signals["theme.agency"].value is Tristate.FALSE


def test_said_does_not_match_ai() -> None:
    home = make_page("", "<p>Said simply: we help sales teams.</p>")
    assert extract(home)["theme.ai"].value is Tristate.FALSE


def test_no_pages_means_site_signals_unknown() -> None:
    signals = extract()
    for name in ["no_help_center_or_docs", "multiple_support_channels", "theme.ai", "demo_or_waitlist_without_pricing"]:
        assert signals[name].value is Tristate.UNKNOWN, name


def test_run_extraction_stores_signals_and_skips_excluded(connection: sqlite3.Connection) -> None:
    save_company(connection, Company(name="Example AI", website=SITE))
    berlin = Company.model_validate(
        {"name": "Berlin AI", "website": "berlinai.example", "hq_country": {"value": "DE", "provenance": CSV_PROVENANCE}}
    )
    save_company(connection, berlin)
    save_page(connection, SITE, FetchResult(url=f"https://{SITE}", html="<html><body><p>AI data pipelines</p></body></html>"))
    parse_pages(connection)

    summary = run_extraction(connection, load_config(EXAMPLE_CONFIG_PATH))

    assert (summary.companies, summary.excluded) == (1, 1)
    stored = {signal.name: signal for signal in get_signals(connection, SITE)}
    assert stored["theme.data"].value is Tristate.TRUE
    assert get_signals(connection, "berlinai.example") == []