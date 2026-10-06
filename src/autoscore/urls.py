"""URL normalization helpers shared by ingestion and fetching"""

from urllib.parse import urlsplit

def normalize_website(raw_url: str) -> str:
    """Return a canonical URL for a company website
    
    Accepts bare domains and full URLs. This function lowercases
    the host, drops the trailing slash, and discards query strings
    and fragments, which never identify a company
    """
    cleaned = raw_url.strip()
    if not cleaned:
        raise ValueError("Website is empty")
    if "://" not in cleaned:
        cleaned = f"https://{cleaned}"
    parts = urlsplit(cleaned)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme: {parts.scheme}")
    host = (parts.hostname or "").lower()
    if "." not in host or " " in host:
        raise ValueError(f"Website has no valid domain: {raw_url!r}")
    path = parts.path.rstrip("/")
    return f"{parts.scheme}://{host}{path}"

def domain_from_url(url: str) -> str:
    """Return the host of a URL w/o leading "www. "."""
    host = urlsplit(normalize_website(url)).hostname or ""
    return host.removeprefix("www.")
