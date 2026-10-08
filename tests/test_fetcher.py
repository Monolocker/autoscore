from collections.abc import Callable

import httpx

from autoscore.fetcher import Fetcher

HOME_HTML = "<html><body><h1>Example AI</h1></body></html>"
Handler = Callable[[httpx.Request], httpx.Response]


def make_fetcher(handler: Handler, sleeps: list[float] | None = None, **options: float) -> Fetcher:
    """A Fetcher wired to a fake website. Sleeps are recorded instead of actually waiting."""
    recorded = sleeps if sleeps is not None else []
    settings = {"min_delay_seconds": 0.0, **options}
    return Fetcher(transport=httpx.MockTransport(handler), sleep=recorded.append, **settings)


def test_fetch_returns_html() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(200, html=HOME_HTML)
        return httpx.Response(404)

    with make_fetcher(handler) as fetcher:
        result = fetcher.fetch("https://exampleai.com")
    assert result.ok
    assert result.html == HOME_HTML
    assert result.status_code == 200


def test_retries_server_errors_with_backoff() -> None:
    page_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        page_calls.append(request.url.path)
        if len(page_calls) < 3:
            return httpx.Response(503)
        return httpx.Response(200, html=HOME_HTML)

    sleeps: list[float] = []
    with make_fetcher(handler, sleeps=sleeps) as fetcher:
        result = fetcher.fetch("https://exampleai.com")
    assert result.ok
    assert len(page_calls) == 3
    assert sleeps == [1.0, 2.0]


def test_gives_up_after_max_attempts() -> None:
    page_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        page_calls.append(request.url.path)
        return httpx.Response(503)

    with make_fetcher(handler) as fetcher:
        result = fetcher.fetch("https://exampleai.com")
    assert result.error == "HTTP 503"
    assert len(page_calls) == 3


def test_client_errors_are_not_retried() -> None:
    page_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        page_calls.append(request.url.path)
        return httpx.Response(404)

    with make_fetcher(handler) as fetcher:
        result = fetcher.fetch("https://exampleai.com/missing")
    assert result.error == "HTTP 404"
    assert len(page_calls) == 1


def test_robots_disallow_is_respected() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
        return httpx.Response(200, html=HOME_HTML)

    with make_fetcher(handler) as fetcher:
        blocked = fetcher.fetch("https://exampleai.com/private")
        allowed = fetcher.fetch("https://exampleai.com/")
    assert blocked.error == "Disallowed by robots.txt"
    assert allowed.ok
    assert requested == ["/robots.txt", "/"]  # robots fetched once; /private never requested


def test_non_html_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=b"%PDF-1.7", headers={"content-type": "application/pdf"})

    with make_fetcher(handler) as fetcher:
        result = fetcher.fetch("https://exampleai.com/deck.pdf")
    assert result.error is not None
    assert result.error.startswith("Not HTML")


def test_network_error_is_reported_not_raised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("DNS lookup failed", request=request)

    sleeps: list[float] = []
    with make_fetcher(handler, sleeps=sleeps) as fetcher:
        result = fetcher.fetch("https://dead-site.invalid")
    assert result.error is not None
    assert result.error.startswith("ConnectError")
    assert sleeps == [1.0, 2.0]


def test_rate_limit_waits_between_requests_to_same_host() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, html=HOME_HTML)

    sleeps: list[float] = []
    with make_fetcher(handler, sleeps=sleeps, min_delay_seconds=5.0) as fetcher:
        fetcher.fetch("https://exampleai.com")  # robots.txt request, then the page
    assert len(sleeps) == 1
    assert 4.9 < sleeps[0] <= 5.0