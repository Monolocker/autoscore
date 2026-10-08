"""Command-line entry point for autoscore."""

import argparse
import logging
import sys
from contextlib import closing
from pathlib import Path

from autoscore.config import CONFIG_PATH, PROJECT_ROOT, load_config, total_weight
from autoscore.database import DEFAULT_DB_PATH, connect, list_companies, list_tables
from autoscore.enrichment import fetch_homepages
from autoscore.fetcher import Fetcher
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


def run_fetch(refresh: bool) -> int:
    with closing(connect()) as connection, Fetcher() as fetcher:
        summary = fetch_homepages(connection, fetcher, refresh=refresh)

    print(f"Fetched {summary.fetched}, cached {summary.cached}, no website {summary.no_website}")
    for company_key, error in summary.failed:
        print(f"Failed {company_key}: {error}")
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
    fetch_parser = subcommands.add_parser("fetch", help="fetch company homepages (cached)")
    fetch_parser.add_argument("--refresh", action="store_true", help="ignore the cache and fetch again")
    args = parser.parse_args()

    if args.command == "status":
        exit_code = show_status()
    elif args.command == "import":
        exit_code = run_import(args.csv_path)
    else:
        exit_code = run_fetch(args.refresh)
    sys.exit(exit_code)