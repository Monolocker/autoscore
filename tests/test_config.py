from pathlib import Path

import pytest

from autoscore.config import (
    CONFIG_PATH,
    DIMENSIONS,
    EXAMPLE_CONFIG_PATH,
    load_config,
    total_weight,
)

# The example config is always tested. The private config is tested only if it exists locally.
CONFIG_FILES = [EXAMPLE_CONFIG_PATH]
if CONFIG_PATH.exists():
    CONFIG_FILES.append(CONFIG_PATH)


@pytest.mark.parametrize("path", CONFIG_FILES, ids=lambda path: path.name)
def test_every_dimension_present(path: Path) -> None:
    config = load_config(path)
    for dimension in DIMENSIONS:
        assert dimension in config, f"Missing dimension: {dimension}"


@pytest.mark.parametrize("path", CONFIG_FILES, ids=lambda path: path.name)
def test_weights_sum_to_100(path: Path) -> None:
    assert total_weight(load_config(path)) == 100
