"""Debug helper: show every link on a company's homepage and how discovery classifies it.

Usage: uv run python scripts/inspect_links.py [domain name, i.e. example.ai]
"""

import sys
from contextlib import closing

from autoscore.database import connect, get_company, get_page
from autoscore.discovery import page_type_for_url, site_host
from autoscore.parsing import parse_html


def main(company_key: str) -> None:
    with closing(connect()) as connection:
        company = get_company(connection, company_key)
        if company is None or company.website is None:
            sys.exit(f"No company with a website for key: {company_key}")
        page = get_page(connection, company.key, company.website)

    if page is None or page["html"] is None:
        sys.exit(f"Homepage not fetched yet for {company_key}. Run: uv run autoscore fetch")

    base_url = page["final_url"] or page["url"]
    domain = site_host(base_url)
    for link in parse_html(page["html"], base_url).links:
        page_type = page_type_for_url(link.url, domain) or "off-site"
        # :<10 pads the label to 10 characters so the URLs line up in a column.
        print(f"{page_type:<10} {link.url}  [{link.text}]")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: uv run python scripts/inspect_links.py <company_key>")
    main(sys.argv[1])