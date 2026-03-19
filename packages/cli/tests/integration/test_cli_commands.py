"""Integration tests for CLI commands — exercising Click command invocation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from agentfile.cli import cli


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def with_agentfile(tmp_path):
    """Create a temporary directory with a valid agentfile.yaml."""
    content = """\
schema_version: "1.1"

agents:
  test-agent:
    metadata:
      description: "CLI test agent"
      role: "tester"
      goal: "test things"
    runtime:
      provider: anthropic
      model: claude-sonnet-4-6
      temperature: 0.2
    tools:
      - name: search
        source: mcp://brave-search
"""
    af_path = tmp_path / "agentfile.yaml"
    af_path.write_text(content)
    return af_path


@pytest.fixture
def with_multi_agent(tmp_path):
    content = """\
schema_version: "1.1"

agents:
  leader:
    metadata:
      description: "Entry agent"
      role: "coordinator"
    runtime:
      provider: anthropic
      model: claude-sonnet-4-6
    tools:
      - name: search
        source: mcp://brave-search
    collaborators: [helper]

  helper:
    metadata:
      description: "Helper"
    runtime:
      provider: openai
      model: gpt-4o
    tools:
      - name: fs
        source: mcp://filesystem
    collaborators: [leader]
"""
    af_path = tmp_path / "agentfile.yaml"
    af_path.write_text(content)
    return af_path


# ── ninetrix --version ────────────────────────────────────────────────────────


class TestVersion:
    def test_version_flag(self, runner):
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "version" in result.output.lower() or "." in result.output


# ── ninetrix init ─────────────────────────────────────────────────────────────


class TestInit:
    def test_init_default(self, runner, tmp_path):
        out_path = tmp_path / "agentfile.yaml"
        result = runner.invoke(cli, ["init", "--yes", str(out_path)])
        assert result.exit_code == 0
        assert out_path.exists()
        data = yaml.safe_load(out_path.read_text())
        assert "agents" in data

    def test_init_with_name_and_provider(self, runner, tmp_path):
        out_path = tmp_path / "agentfile.yaml"
        result = runner.invoke(cli, [
            "init", "--name", "my-bot", "--provider", "openai", "--yes", str(out_path),
        ])
        assert result.exit_code == 0
        data = yaml.safe_load(out_path.read_text())
        agents = data.get("agents", {})
        assert len(agents) > 0

    def test_init_custom_output(self, runner, tmp_path):
        out_path = tmp_path / "custom.yaml"
        result = runner.invoke(cli, ["init", "--yes", str(out_path)])
        assert result.exit_code == 0
        assert out_path.exists()


# ── ninetrix validate ─────────────────────────────────────────────────────────


class TestValidate:
    def test_validate_valid_file(self, runner, with_agentfile):
        result = runner.invoke(cli, ["validate", "-f", str(with_agentfile), "--no-render"])
        assert result.exit_code == 0

    def test_validate_missing_file(self, runner, tmp_path):
        result = runner.invoke(cli, ["validate", "-f", str(tmp_path / "missing.yaml"), "--no-render"])
        assert result.exit_code != 0

    def test_validate_invalid_file(self, runner, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("not_a_mapping: true")
        result = runner.invoke(cli, ["validate", "-f", str(bad), "--no-render"])
        assert result.exit_code != 0

    def test_validate_json_output(self, runner, with_agentfile):
        result = runner.invoke(cli, ["validate", "-f", str(with_agentfile), "--no-render", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert all("level" in r and "message" in r for r in data)

    def test_validate_multi_agent(self, runner, with_multi_agent):
        result = runner.invoke(cli, ["validate", "-f", str(with_multi_agent), "--no-render"])
        assert result.exit_code == 0


# ── ninetrix ls ───────────────────────────────────────────────────────────────


class TestLs:
    def test_ls_agents(self, runner, with_agentfile):
        result = runner.invoke(cli, ["ls", "-f", str(with_agentfile), "--no-docker"])
        assert result.exit_code == 0
        assert "test-agent" in result.output

    def test_ls_tools(self, runner, with_agentfile):
        result = runner.invoke(cli, ["ls", "-f", str(with_agentfile), "--no-docker", "--tools"])
        assert result.exit_code == 0
        assert "search" in result.output
        assert "mcp" in result.output.lower()

    def test_ls_triggers_none(self, runner, with_agentfile):
        result = runner.invoke(cli, ["ls", "-f", str(with_agentfile), "--no-docker", "--triggers"])
        assert result.exit_code == 0
        assert "0 trigger" in result.output

    def test_ls_json_output(self, runner, with_agentfile):
        result = runner.invoke(cli, ["ls", "-f", str(with_agentfile), "--no-docker", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert data[0]["agent"] == "test-agent"

    def test_ls_multi_agent(self, runner, with_multi_agent):
        result = runner.invoke(cli, ["ls", "-f", str(with_multi_agent), "--no-docker"])
        assert result.exit_code == 0
        assert "leader" in result.output
        assert "helper" in result.output

    def test_ls_missing_file(self, runner, tmp_path):
        result = runner.invoke(cli, ["ls", "-f", str(tmp_path / "nope.yaml"), "--no-docker"])
        assert result.exit_code != 0


# ── ninetrix schema ───────────────────────────────────────────────────────────


class TestSchema:
    def test_schema_dump(self, runner):
        result = runner.invoke(cli, ["schema", "dump"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "properties" in data or "type" in data

    def test_schema_docs(self, runner):
        result = runner.invoke(cli, ["schema", "docs"])
        assert result.exit_code == 0

    def test_schema_validate_valid(self, runner, with_agentfile):
        result = runner.invoke(cli, ["schema", "validate", str(with_agentfile)])
        assert result.exit_code == 0
        assert "valid" in result.output.lower()

    def test_schema_validate_missing(self, runner, tmp_path):
        result = runner.invoke(cli, ["schema", "validate", str(tmp_path / "ghost.yaml")])
        assert result.exit_code != 0


# ── ninetrix migrate ──────────────────────────────────────────────────────────


class TestMigrate:
    def test_migrate_old_schema(self, runner, tmp_path):
        p = tmp_path / "agentfile.yaml"
        p.write_text(yaml.dump({
            "version": "1.0",
            "agents": {"a": {"metadata": {}, "runtime": {}, "tools": [{"name": "t", "source": "mcp://t"}]}},
        }))
        result = runner.invoke(cli, ["migrate", "-f", str(p)])
        assert result.exit_code == 0
        data = yaml.safe_load(p.read_text())
        assert data["schema_version"] == "1.1"

    def test_migrate_already_latest(self, runner, with_agentfile):
        result = runner.invoke(cli, ["migrate", "-f", str(with_agentfile)])
        assert result.exit_code == 0
        assert "nothing to do" in result.output


# ── ninetrix doctor ───────────────────────────────────────────────────────────


class TestDoctor:
    def test_doctor_runs(self, runner, with_agentfile):
        """Doctor should run without crashing, even if Docker isn't available."""
        result = runner.invoke(cli, ["doctor", "-f", str(with_agentfile)])
        # May exit 1 if Docker not available, but should not crash
        assert result.exit_code in (0, 1)
        assert "doctor" in result.output.lower() or "Docker" in result.output
