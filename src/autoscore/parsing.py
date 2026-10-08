"""Turn raw HTML into clean text and links. Pure functions: no network, no database."""

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from autoscore.urls import normalize_website

# Bump this whenever text extraction changes, so stored pages are re-parsed.
PARSER_VERSION = 2

NON_CONTENT_TAGS = ["script", "style", "noscript", "svg", "template", "iframe"]
# Elements that start a new line. Everything else (span, em, a, strong...) is inline text.
BLOCK_TAGS = [
    "address", "article", "aside", "blockquote", "br", "button", "dd", "div", "dl", "dt",
    "figcaption", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr",
    "li", "main", "nav", "ol", "p", "pre", "section", "table", "td", "th", "tr", "ul",
]
SKIPPED_PREFIXES = ("#", "mailto:", "tel:", "javascript:")


@dataclass
class Link:
    url: str
    text: str


@dataclass
class ParsedPage:
    title: str | None
    description: str | None
    text: str
    links: list[Link] = field(default_factory=list)


def clean_url(url: str) -> str | None:
    """Normalize an absolute link the same way company websites are normalized."""
    try:
        return normalize_website(url)
    except ValueError:
        return None


def clean_text(raw: str) -> str:
    """Collapse whitespace, tidy punctuation, drop empty and immediately repeated lines."""
    lines: list[str] = []
    for line in raw.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        line = re.sub(r" ([.,!?;:])", r"\1", line)  # "fast ." -> "fast."
        if line and (not lines or lines[-1] != line):
            lines.append(line)
    return "\n".join(lines)


def meta_content(soup: BeautifulSoup, **attrs: str) -> str | None:
    tag = soup.find("meta", attrs=attrs)
    if isinstance(tag, Tag):
        content = tag.get("content")
        if content:
            return str(content).strip() or None
    return None


def extract_links(soup: BeautifulSoup, base_url: str) -> list[Link]:
    """Absolute, normalized, de-duplicated links in page order."""
    links: list[Link] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        if not isinstance(anchor, Tag):
            continue
        href = str(anchor["href"]).strip()
        if not href or href.lower().startswith(SKIPPED_PREFIXES):
            continue
        url = clean_url(urljoin(base_url, href))
        if url is None or url in seen:
            continue
        seen.add(url)
        links.append(Link(url=url, text=anchor.get_text(" ", strip=True)[:100]))
    return links


def extract_text(container: BeautifulSoup | Tag) -> str:
    """Visible text with one line per block element; inline elements stay on the same line."""
    for tag in container.find_all(BLOCK_TAGS):
        tag.insert_before("\n")
        tag.insert_after("\n")
    return clean_text(container.get_text(separator=" "))


def parse_html(html: str, base_url: str) -> ParsedPage:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else None
    description = meta_content(soup, name="description") or meta_content(soup, property="og:description")
    links = extract_links(soup, base_url)

    for tag in soup.find_all(NON_CONTENT_TAGS):
        tag.decompose()
    text = extract_text(soup.body or soup)
    return ParsedPage(title=title or None, description=description, text=text, links=links)


def looks_js_rendered(html: str, text: str) -> bool:
    """Lots of HTML but almost no visible text usually means JavaScript renders the content."""
    return len(html) > 20_000 and len(text) < 300