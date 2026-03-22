"""Integration test fixtures."""

from __future__ import annotations


import pytest

from click.testing import CliRunner


@pytest.fixture
def cli_runner() -> CliRunner:
    """Provide a Click test runner with isolated filesystem."""
    return CliRunner()
