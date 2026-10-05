"""Command-line entry point for autoscore."""

import sys

from autoscore.config import CONFIG_PATH, load_config, total_weight


def main() -> None:
    config = load_config()
    print(f"Python {sys.version.split()[0]}")
    print(f"Config: {CONFIG_PATH.name} (version {config['version']})")
    print(f"Total weight: {total_weight(config)}/100")
