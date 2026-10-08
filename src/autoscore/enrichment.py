"""Website enrichment stage: fetch company pages and store them.

Milestone 5 fetches homepages only. Milestone 6 parses them and follows relevant links.
"""

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from autoscore.database import get_page_record, list_companies, save_page
from autoscore.fetcher import Fetcher
from autoscore.models import utc_now

logger = logging.getLogger(__name__)

SUCCESS_CACHE = timedelta(days=7)
ERROR_CACHE = timedelta(days=1)


@dataclass
class FetchSummary:
    fetched: int = 0
    cached: int = 0
    no_website: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)  # (company_key, error)


def is_fresh(record: sqlite3.Row, now: datetime) -> bool:
    """Successful fetches stay fresh for longer than failures."""
    fetched_at = datetime.fromisoformat(record["fetched_at"])
    max_age = SUCCESS_CACHE if record["error"] is None else ERROR_CACHE
    return now - fetched_at < max_age


def fetch_homepages(connection: sqlite3.Connection, fetcher: Fetcher, refresh: bool = False) -> FetchSummary:
    summary = FetchSummary()
    now = utc_now()
    for company in list_companies(connection):
        if company.website is None:
            summary.no_website += 1
            continue
        record = get_page_record(connection, company.key, company.website)
        if record is not None and not refresh and is_fresh(record, now):
            summary.cached += 1
            continue

        logger.info("Fetching %s (%s)", company.website, company.name)
        result = fetcher.fetch(company.website)
        save_page(connection, company.key, result)
        summary.fetched += 1
        if not result.ok:
            summary.failed.append((company.key, result.error or "unknown error"))
    return summary