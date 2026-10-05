"""Load and sanity-check the scoring rubric."""

from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
EXAMPLE_CONFIG_PATH = PROJECT_ROOT / "config.example.yaml"
DIMENSIONS = ("icp_fit", "service_need", "urgency", "semantic_fit")


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    """Load the scoring rubric from YAML."""
    if not path.exists():
        hint = ""
        if path == CONFIG_PATH:
            hint = f"\nCreate it with: cp {EXAMPLE_CONFIG_PATH.name} {CONFIG_PATH.name}"
        raise FileNotFoundError(f"Config not found: {path}{hint}")
    with path.open(encoding="utf-8") as config_file:
        return yaml.safe_load(config_file)


def total_weight(config: dict[str, Any]) -> int:
    """Sum the max_points of every scoring dimension."""
    return sum(config[dimension]["max_points"] for dimension in DIMENSIONS)
