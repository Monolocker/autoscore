"""Website enrichment stage: fetch company pages, then parse stored HTML into text.

Fetching and parsing are separate steps so the parser can be improved and re-run
on stored pages without contacting any website again.
"""

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from autoscore.database import get_page, list_companies, list_html_pages, save_page, save_page_text
from autoscore.discovery import MAX_SUBPAGES, select_subpages, site_host
from autoscore.fetcher import Fetcher
from autoscore.filters import check_exclusions
from autoscore.models import utc_now
from autoscore.parsing import PARSER_VERSION, looks_js_rendered, parse_html

logger = logging.getLogger(__name__)

SUCCESS_CACHE = timedelta(days=7)
ERROR_CACHE = timedelta(days=1)


@dataclass
class FetchSummary:
    fetched: int = 0
    cached: int = 0
    excluded: int = 0
    no_website: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)  # (url, error)


@dataclass
class ParseSummary:
    parsed: int = 0
    unchanged: int = 0
    likely_js_rendered: list[str] = field(default_factory=list)


def is_fresh(record: sqlite3.Row, now: datetime) -> bool:
    """Successful fetches stay fresh for longer than failures."""
    fetched_at = datetime.fromisoformat(record["fetched_at"])
    max_age = SUCCESS_CACHE if record["error"] is None else ERROR_CACHE
    return now - fetched_at < max_age


def _fetch_cached(
    connection: sqlite3.Connection,
    fetcher: Fetcher,
    company_key: str,
    url: str,
    refresh: bool,
    now: datetime,
    summary: FetchSummary,
) -> None:
    record = get_page(connection, company_key, url)
    if record is not None and not refresh and is_fresh(record, now):
        summary.cached += 1
        return
    logger.info("Fetching %s", url)
    result = fetcher.fetch(url)
    save_page(connection, company_key, result)
    summary.fetched += 1
    if not result.ok:
        summary.failed.append((url, result.error or "unknown error"))


def fetch_company_pages(
    connection: sqlite3.Connection,
    fetcher: Fetcher,
    refresh: bool = False,
    max_subpages: int = MAX_SUBPAGES,
    config: dict[str, Any] | None = None,
) -> FetchSummary:
    """Fetch each homepage, then the relevant subpages it links to.

    When a config is given, companies failing the hard exclusion gate are skipped.
    """
    summary = FetchSummary()
    now = utc_now()
    for company in list_companies(connection):
        if config is not None:
            exclusion = check_exclusions(company, config)
            if exclusion.excluded:
                summary.excluded += 1
                logger.info("Skipping %s (excluded: %s)", company.name, "; ".join(exclusion.reasons))
                continue
        if company.website is None:
            summary.no_website += 1
            continue
        _fetch_cached(connection, fetcher, company.key, company.website, refresh, now, summary)

        homepage = get_page(connection, company.key, company.website)
        if homepage is None or homepage["html"] is None:
            continue
        # Use the post-redirect URL so relative links and the site domain are correct.
        base_url = homepage["final_url"] or homepage["url"]
        links = parse_html(homepage["html"], base_url).links
        for _page_type, url in select_subpages(links, site_host(base_url), max_subpages):
            _fetch_cached(connection, fetcher, company.key, url, refresh, now, summary)
    return summary


def parse_pages(connection: sqlite3.Connection) -> ParseSummary:
    """Parse every stored page whose HTML or parser version changed since its last parse."""
    summary = ParseSummary()
    for page in list_html_pages(connection):
        # The key combines parser version and HTML hash: new HTML or a better parser
        # both trigger a re-parse, while identical work is skipped.
        parse_key = f"v{PARSER_VERSION}:{page['content_hash']}"
        if page["source_hash"] == parse_key:
            summary.unchanged += 1
            continue
        parsed = parse_html(page["html"], page["final_url"] or page["url"])
        save_page_text(connection, page["id"], parsed.title, parsed.description, parsed.text, parse_key)
        summary.parsed += 1
        if looks_js_rendered(page["html"], parsed.text):
            summary.likely_js_rendered.append(page["url"])
    return summary