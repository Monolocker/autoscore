"""Polite HTTP fetching: timeout, retries w/ backoff, per-domain rate limit, robots.txt

Fetching only. Parsing HTML comes in a later stage
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Self
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

USER_AGENT = "autoscore/0.1 (+https://github.com/Monolocker/autoscore)"
RETRY_STATUS_CODES = {429, 500, 502, 503, 504}

@dataclass
class FetchResult:
    url: str
    final_url: str | None = None
    status_code: int | None = None 
    content_type: str | None = None
    html: str | None = None
    error: str | None = None 

    @property
    def ok(self) -> bool: 
        return self.error is None and self.html is not None
    

class Fetcher:
    """Fetches pages politely. Uses a context manager so the HTTP client is closed."""

    def __init__(
        self,
        timeout_seconds: float = 10.0,
        max_attempts: int = 3,
        backoff_seconds: float = 1.0,
        min_delay_seconds: float = 1.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        # transport and sleep are injectable so tests can fake the network and skip real waiting.
        self.client = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=timeout_seconds,
            follow_redirects=True,
            transport=transport,
        )
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds
        self.min_delay_seconds = min_delay_seconds
        self.sleep = sleep
        self._last_request_at: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def close(self) -> None:
        self.client.close()

    def fetch(self, url: str) -> FetchResult:
        """Fetch one page. Never raises for network problems; errors go in the result."""
        if not self.allowed_by_robots(url):
            return FetchResult(url=url, error="Disallowed by robots.txt")
        try:
            response = self._get(url)
        except httpx.RequestError as error:
            return FetchResult(url=url, error=f"{type(error).__name__}: {error}")

        result = FetchResult(
            url=url,
            final_url=str(response.url),
            status_code=response.status_code,
            content_type=response.headers.get("content-type", ""),
        )
        if response.status_code != 200:         # If not successful request
            result.error = f"HTTP {response.status_code}"
        elif "html" not in (result.content_type or "").lower():
            result.error = f"Not HTML: {result.content_type}"
        else:
            result.html = response.text
        return result

    def allowed_by_robots(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            self._robots[origin] = self._load_robots(origin)
        robots = self._robots[origin]
        return robots is None or robots.can_fetch(USER_AGENT, url)

    def _load_robots(self, origin: str) -> RobotFileParser | None:
        """Return the parsed robots.txt, or None when there is none (everything allowed)."""
        self._wait_for_turn(urlsplit(origin).hostname or "")
        try:
            # Single attempt: if the site is down, the page fetch will retry and report it.
            response = self.client.get(f"{origin}/robots.txt")
        except httpx.RequestError:
            return None
        parser = RobotFileParser()
        if response.status_code in (401, 403):
            parser.disallow_all = True
            return parser
        if response.status_code != 200:
            return None
        parser.parse(response.text.splitlines())
        return parser

    def _get(self, url: str) -> httpx.Response:
        """GET with retries on network errors and retryable status codes."""
        host = urlsplit(url).hostname or ""
        for attempt in range(1, self.max_attempts + 1):
            self._wait_for_turn(host)
            try:
                response = self.client.get(url)
            except httpx.TransportError:
                if attempt == self.max_attempts:
                    raise
            else:
                if response.status_code not in RETRY_STATUS_CODES or attempt == self.max_attempts:
                    return response
            self.sleep(self.backoff_seconds * 2 ** (attempt - 1))
        raise RuntimeError("unreachable")

    def _wait_for_turn(self, host: str) -> None:
        """Enforce a minimum delay between requests to the same host."""
        last = self._last_request_at.get(host)
        if last is not None:
            remaining = self.min_delay_seconds - (time.monotonic() - last)
            if remaining > 0:
                self.sleep(remaining)
        self._last_request_at[host] = time.monotonic()
