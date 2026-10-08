import sqlite3

import httpx

from autoscore.database import save_company
from autoscore.enrichment import fetch_homepages
from autoscore.fetcher import Fetcher
from autoscore.models import Company


def counting_fetcher(calls: list[str]) -> Fetcher:
    """A fake website that records every path requested."""

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, html="<html><body>Hello</body></html>")

    return Fetcher(transport=httpx.MockTransport(handler), sleep=lambda seconds: None, min_delay_seconds=0)


def test_homepage_is_stored_then_served_from_cache(connection: sqlite3.Connection) -> None:
    save_company(connection, Company(name="Example AI", website="exampleai.com"))
    save_company(connection, Company(name="No Site Co"))
    calls: list[str] = []

    with counting_fetcher(calls) as fetcher:
        first = fetch_homepages(connection, fetcher)
        second = fetch_homepages(connection, fetcher)

    assert (first.fetched, first.cached, first.no_website) == (1, 0, 1)
    assert (second.fetched, second.cached) == (0, 1)
    assert calls.count("/") == 1
    row = connection.execute("SELECT status_code, content_hash, error FROM pages").fetchone()
    assert row["status_code"] == 200
    assert row["content_hash"] is not None
    assert row["error"] is None


def test_refresh_ignores_cache(connection: sqlite3.Connection) -> None:
    save_company(connection, Company(name="Example AI", website="exampleai.com"))
    calls: list[str] = []

    with counting_fetcher(calls) as fetcher:
        fetch_homepages(connection, fetcher)
        summary = fetch_homepages(connection, fetcher, refresh=True)

    assert summary.fetched == 1
    assert calls.count("/") == 2
    assert connection.execute("SELECT COUNT(*) FROM pages").fetchone()[0] == 1