"""Decide which pages of a company site are worth fetching. Pure functions."""

from urllib.parse import urlsplit

from autoscore.parsing import Link

# Ordered by usefulness for qualification signals; when the cap is hit, later types are dropped.
PAGE_TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "careers": ("careers", "jobs", "hiring", "work-with-us"),
    "pricing": ("pricing", "plans"),
    "customers": ("customers", "case-studies", "testimonials"),
    "about": ("about", "company", "team"),
    "product": ("product", "features", "platform", "how-it-works", "api", "developers"),
    "blog": ("blog", "news", "changelog", "updates"),
    "solutions": ("solutions", "use-cases"),
}
# Public job boards companies commonly use as their careers page.
ATS_HOSTS = {
    "jobs.ashbyhq.com",
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "jobs.lever.co",
    "apply.workable.com",
}
MAX_SUBPAGES = 6


def site_host(url: str) -> str:
    return (urlsplit(url).hostname or "").removeprefix("www.")


def match_keyword(segment: str) -> str | None:
    """'about-us' -> 'about'; 'careers' -> 'careers'; 'join-waitlist' -> None."""
    for page_type, keywords in PAGE_TYPE_KEYWORDS.items():
        if any(segment == keyword or segment.startswith(f"{keyword}-") for keyword in keywords):
            return page_type
    return None


def page_type_for_url(url: str, site_domain: str) -> str | None:
    """Classify a URL as 'homepage', a page type, 'other', or None if it is off-site."""
    host = site_host(url)
    if host in ATS_HOSTS:
        return "careers"
    if host != site_domain:
        if not host.endswith(f".{site_domain}"):
            return None
        subdomain = host.removesuffix(f".{site_domain}")
        return match_keyword(subdomain) or "other"

    segments = [segment for segment in urlsplit(url).path.lower().split("/") if segment]
    if not segments:
        return "homepage"
    return match_keyword(segments[0]) or "other"


def select_subpages(
    links: list[Link], site_domain: str, max_pages: int = MAX_SUBPAGES
) -> list[tuple[str, str]]:
    """Pick at most one link per page type, in priority order, up to max_pages."""
    first_url_by_type: dict[str, str] = {}
    for link in links:
        page_type = page_type_for_url(link.url, site_domain)
        if page_type in PAGE_TYPE_KEYWORDS and page_type not in first_url_by_type:
            first_url_by_type[page_type] = link.url
    selected = [(page_type, first_url_by_type[page_type]) for page_type in PAGE_TYPE_KEYWORDS if page_type in first_url_by_type]
    return selected[:max_pages]