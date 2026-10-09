"""Command-line entry point for autoscore."""

import argparse
import logging
import sys
from contextlib import closing
from pathlib import Path

from autoscore.config import CONFIG_PATH, PROJECT_ROOT, load_config, total_weight
from autoscore.database import DEFAULT_DB_PATH, connect, list_companies, list_tables
from autoscore.enrichment import fetch_company_pages, parse_pages
from autoscore.extraction import run_extraction
from autoscore.fetcher import Fetcher
from autoscore.filters import check_exclusions
from autoscore.ingestion import import_csv


def show_status() -> int:
    config = load_config()
    print(f"Python {sys.version.split()[0]}")
    print(f"Config: {CONFIG_PATH.name} (version {config['version']})")
    print(f"Total weight: {total_weight(config)}/100")

    # closing() guarantees the connection is closed, even if an error occurs.
    with closing(connect()) as connection:
        tables = list_tables(connection)
        company_count = len(list_companies(connection))
    db_display = DEFAULT_DB_PATH.relative_to(PROJECT_ROOT)
    print(f"Database: {db_display} (tables: {', '.join(tables)})")
    print(f"Companies: {company_count}")
    return 0


def run_import(csv_path: Path) -> int:
    if not csv_path.exists():
        print(f"File not found: {csv_path}", file=sys.stderr)
        return 1
    config = load_config()
    confidence = config["confidence"]["source_reliability"]["user_csv"]

    with closing(connect()) as connection:
        result = import_csv(connection, csv_path, confidence)

    print(f"Imported {result.imported} companies from {csv_path.name}")
    if result.unknown_columns:
        print(f"Ignored unknown columns: {', '.join(result.unknown_columns)}")
    for error in result.errors:
        print(f"Skipped line {error.line} ({error.name or 'no name'}): {error.reason}")
    return 0


def run_filter() -> int:
    config = load_config()
    with closing(connect()) as connection:
        companies = list_companies(connection)

    for company in companies:
        result = check_exclusions(company, config)
        label = "EXCLUDED" if result.excluded else "PASS"
        detail = "; ".join(result.reasons)
        if result.unknown_checks:
            detail = f"{detail}  (unknown: {', '.join(result.unknown_checks)})".strip()
        # :<9 and :<28 pad the columns so the output lines up.
        print(f"{label:<9} {company.key:<28} {detail}")
    return 0


def run_fetch(refresh: bool) -> int:
    config = load_config()
    with closing(connect()) as connection, Fetcher() as fetcher:
        summary = fetch_company_pages(connection, fetcher, refresh=refresh, config=config)

    print(
        f"Fetched {summary.fetched} pages, cached {summary.cached}, "
        f"excluded {summary.excluded}, companies without website {summary.no_website}"
    )
    for url, error in summary.failed:
        print(f"Failed {url}: {error}")
    return 0


def run_parse() -> int:
    with closing(connect()) as connection:
        summary = parse_pages(connection)

    print(f"Parsed {summary.parsed} pages, unchanged {summary.unchanged}")
    for url in summary.likely_js_rendered:
        print(f"Possibly JavaScript-rendered (very little visible text): {url}")
    return 0


def run_extract() -> int:
    config = load_config()
    with closing(connect()) as connection:
        summary = run_extraction(connection, config)

    counts = summary.counts
    print(
        f"Extracted signals for {summary.companies} companies (excluded {summary.excluded}): "
        f"{counts['true']} true, {counts['false']} false, {counts['unknown']} unknown"
    )
    return 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # httpx logs every request at INFO; our own "Fetching ..." lines are enough.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    parser = argparse.ArgumentParser(prog="autoscore", description="Startup lead qualification pipeline")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("status", help="show config and database status")
    import_parser = subcommands.add_parser("import", help="import seed companies from a CSV file")
    import_parser.add_argument("csv_path", type=Path, help="path to the CSV file")
    subcommands.add_parser("filter", help="show which companies pass the hard exclusion gate")
    fetch_parser = subcommands.add_parser("fetch", help="fetch homepages and relevant subpages (cached)")
    fetch_parser.add_argument("--refresh", action="store_true", help="ignore the cache and fetch again")
    subcommands.add_parser("parse", help="extract text from fetched pages that changed")
    subcommands.add_parser("extract", help="extract deterministic signals from company data and pages")
    args = parser.parse_args()

    if args.command == "status":
        exit_code = show_status()
    elif args.command == "import":
        exit_code = run_import(args.csv_path)
    elif args.command == "filter":
        exit_code = run_filter()
    elif args.command == "fetch":
        exit_code = run_fetch(args.refresh)
    elif args.command == "parse":
        exit_code = run_parse()
    else:
        exit_code = run_extract()
    sys.exit(exit_code)