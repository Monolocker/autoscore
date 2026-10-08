import sqlite3

import httpx

from autoscore.database import save_company
from autoscore.enrichment import fetch_company_pages, parse_pages
from autoscore.fetcher import Fetcher
from autoscore.models import Company

HOME_HTML = """<html><head><title>Example AI</title></head><body>
<a href="/pricing">Pricing</a> <a href="/careers">Careers</a>
<a href="https://twitter.com/exampleai">Twitter</a>
<p>Lead enrichment for B2B teams.</p>
</body></html>"""


def fake_site(calls: list[str]) -> Fetcher:
    """A fake website that records every host/path requested."""

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.url.host}{request.url.path}")
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/":
            return httpx.Response(200, html=HOME_HTML)
        return httpx.Response(200, html=f"<html><body><h1>{request.url.path}</h1></body></html>")

    return Fetcher(transport=httpx.MockTransport(handler), sleep=lambda seconds: None, min_delay_seconds=0)


def test_fetches_homepage_and_relevant_subpages_only(connection: sqlite3.Connection) -> None:
    save_company(connection, Company(name="Example AI", website="exampleai.com"))
    save_company(connection, Company(name="No Site Co"))
    calls: list[str] = []

    with fake_site(calls) as fetcher:
        summary = fetch_company_pages(connection, fetcher)

    assert (summary.fetched, summary.cached, summary.no_website) == (3, 0, 1)
    assert "exampleai.com/careers" in calls
    assert "exampleai.com/pricing" in calls
    assert not any(call.startswith("twitter.com") for call in calls)


def test_second_run_is_served_from_cache(connection: sqlite3.Connection) -> None:
    save_company(connection, Company(name="Example AI", website="exampleai.com"))
    calls: list[str] = []

    with fake_site(calls) as fetcher:
        fetch_company_pages(connection, fetcher)
        calls_after_first_run = len(calls)
        second = fetch_company_pages(connection, fetcher)

    assert (second.fetched, second.cached) == (0, 3)
    assert len(calls) == calls_after_first_run


def test_refresh_ignores_cache(connection: sqlite3.Connection) -> None:
    save_company(connection, Company(name="Example AI", website="exampleai.com"))
    calls: list[str] = []

    with fake_site(calls) as fetcher:
        fetch_company_pages(connection, fetcher)
        summary = fetch_company_pages(connection, fetcher, refresh=True)

    assert summary.fetched == 3
    assert connection.execute("SELECT COUNT(*) FROM pages").fetchone()[0] == 3


def test_parse_pages_stores_text_and_skips_unchanged(connection: sqlite3.Connection) -> None:
    save_company(connection, Company(name="Example AI", website="exampleai.com"))
    calls: list[str] = []
    with fake_site(calls) as fetcher:
        fetch_company_pages(connection, fetcher)

    first = parse_pages(connection)
    second = parse_pages(connection)

    assert (first.parsed, first.unchanged) == (3, 0)
    assert (second.parsed, second.unchanged) == (0, 3)
    homepage_text = connection.execute(
        """
        SELECT page_texts.text FROM page_texts
        JOIN pages ON pages.id = page_texts.page_id
        WHERE pages.url = ?
        """,
        ("https://exampleai.com",),
    ).fetchone()[0]
    assert "Lead enrichment for B2B teams." in homepage_text