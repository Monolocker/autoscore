"""Turn raw HTML into clean text and links. Pure functions, no network or database"""

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from autoscore.urls import normalize_website

NON_CONTENT_TAGS = ["script", "style", "noscript", "svg", "template", "iframe"]
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
    """Normalize an absolute link the same way company websites are normalized"""
    try:
        return normalize_website(url)
    except ValueError:
        return None 
    
def clean_text(raw: str) -> str:
    """Collapse whitespace, drop empty lines, and drop immediately repeated lines"""
    lines: list[str] = []
    for line in raw.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
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


def parse_html(html: str, base_url: str) -> ParsedPage:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else None
    description = meta_content(soup, name="description") or meta_content(soup, property="og:description")
    links = extract_links(soup, base_url)

    for tag in soup.find_all(NON_CONTENT_TAGS):
        tag.decompose()
    body = soup.body or soup
    text = clean_text(body.get_text(separator="\n"))
    return ParsedPage(title=title or None, description=description, text=text, links=links)


def looks_js_rendered(html: str, text: str) -> bool:
    """Lots of HTML but almost no visible text usually means JavaScript renders the content."""
    return len(html) > 20_000 and len(text) < 300