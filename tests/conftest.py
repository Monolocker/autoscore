"""Shared pytest fixtures. pytest loads this file automatically."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from autoscore.database import connect


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """A fresh database file per test, in a temp folder pytest cleans up."""
    db = connect(tmp_path / "test.sqlite")
    yield db
    db.close()