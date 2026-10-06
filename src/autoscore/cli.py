"""Command-line entry point for autoscore."""

import sys
from contextlib import closing

from autoscore.config import CONFIG_PATH, PROJECT_ROOT, load_config, total_weight
from autoscore.database import DEFAULT_DB_PATH, connect, list_tables


def main() -> None:
    config = load_config()
    print(f"Python {sys.version.split()[0]}")
    print(f"Config: {CONFIG_PATH.name} (version {config['version']})")
    print(f"Total weight: {total_weight(config)}/100")

    # closing() guarantees the connection is closed, even if an error occurs.
    with closing(connect()) as connection:
        tables = list_tables(connection)
    db_display = DEFAULT_DB_PATH.relative_to(PROJECT_ROOT)
    print(f"Database: {db_display} (tables: {', '.join(tables)})")