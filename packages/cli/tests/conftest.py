"""Shared test fixtures for the agentfile CLI test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    """Return the path to the test fixtures directory."""
    return FIXTURES_DIR


@pytest.fixture
def minimal_yaml(fixtures_dir: Path) -> Path:
    return fixtures_dir / "minimal.yaml"


@pytest.fixture
def multi_agent_yaml(fixtures_dir: Path) -> Path:
    return fixtures_dir / "multi_agent.yaml"


@pytest.fixture
def full_featured_yaml(fixtures_dir: Path) -> Path:
    return fixtures_dir / "full_featured.yaml"


@pytest.fixture
def invalid_no_agents_yaml(fixtures_dir: Path) -> Path:
    return fixtures_dir / "invalid_no_agents.yaml"


@pytest.fixture
def old_schema_yaml(fixtures_dir: Path) -> Path:
    return fixtures_dir / "old_schema.yaml"


@pytest.fixture
def sample_agent_yaml(tmp_path: Path) -> Path:
    """Create a temporary valid agentfile.yaml for tests that need a writable copy."""
    content = """\
schema_version: "1.1"

agents:
  my-agent:
    metadata:
      description: "Test agent"
      role: "helper"
    runtime:
      provider: anthropic
      model: claude-sonnet-4-6
    tools:
      - name: search
        source: mcp://brave-search
"""
    p = tmp_path / "agentfile.yaml"
    p.write_text(content)
    return p
