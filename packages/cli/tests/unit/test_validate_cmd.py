"""Tests for agentfile.commands.validate — _check_schema, _check_agents, validate_cmd."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def valid_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "agentfile.yaml"
    p.write_text("""\
schema_version: "1.1"

agents:
  my-agent:
    metadata:
      description: "Test agent"
    runtime:
      provider: anthropic
      model: claude-sonnet-4-6
    tools:
      - name: search
        source: mcp://brave-search
""")
    return p


@pytest.fixture
def invalid_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "agentfile.yaml"
    p.write_text("""\
schema_version: "1.1"

agents:
  bad-agent:
    metadata:
      description: "No tools"
    runtime:
      provider: anthropic
      model: claude-sonnet-4-6
    tools: []
""")
    return p


@pytest.fixture
def composio_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "agentfile.yaml"
    p.write_text("""\
schema_version: "1.1"

agents:
  my-agent:
    metadata:
      description: "Agent with Composio"
    runtime:
      provider: anthropic
      model: claude-sonnet-4-6
    tools:
      - name: github
        source: composio://GITHUB
        actions:
          - GITHUB_LIST_REPOS
""")
    return p


# ── _check_schema ─────────────────────────────────────────────────────────────


class TestCheckSchema:
    """Unit tests for _check_schema internal function."""

    def test_ok_on_valid_file(self, valid_yaml: Path):
        from agentfile.commands.validate import _check_schema
        results, af = _check_schema(str(valid_yaml), None)
        assert af is not None
        assert any(r["level"] == "ok" for r in results)
        assert not any(r["level"] == "error" for r in results)

    def test_error_on_missing_file(self, tmp_path: Path):
        from agentfile.commands.validate import _check_schema
        results, af = _check_schema(str(tmp_path / "missing.yaml"), None)
        assert af is None
        assert any(r["level"] == "error" for r in results)
        assert any("not found" in r["message"] for r in results)

    def test_error_on_invalid_yaml(self, invalid_yaml: Path):
        from agentfile.commands.validate import _check_schema
        results, af = _check_schema(str(invalid_yaml), None)
        # Parsed but fails validation — no errors from parse itself, errors from schema
        error_results = [r for r in results if r["level"] == "error"]
        assert len(error_results) > 0

    def test_error_on_unknown_environment(self, valid_yaml: Path):
        from agentfile.commands.validate import _check_schema
        results, af = _check_schema(str(valid_yaml), "nonexistent")
        assert af is None
        assert any(r["level"] == "error" and "not found" in r["message"] for r in results)

    def test_ok_result_includes_agent_count(self, valid_yaml: Path):
        from agentfile.commands.validate import _check_schema
        results, af = _check_schema(str(valid_yaml), None)
        ok_messages = [r["message"] for r in results if r["level"] == "ok"]
        assert any("1 agent" in m for m in ok_messages)


# ── _check_agents ─────────────────────────────────────────────────────────────


class TestCheckAgents:
    """Unit tests for _check_agents internal function."""

    def test_warns_when_api_key_missing(self, valid_yaml: Path, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        from agentfile.commands.validate import _check_schema, _check_agents

        _, af = _check_schema(str(valid_yaml), None)
        results = _check_agents(af)
        assert any(r["level"] == "warn" and "ANTHROPIC_API_KEY" in r["message"] for r in results)

    def test_ok_when_api_key_present(self, valid_yaml: Path, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
        from agentfile.commands.validate import _check_schema, _check_agents

        _, af = _check_schema(str(valid_yaml), None)
        results = _check_agents(af)
        assert any(r["level"] == "ok" and "ANTHROPIC_API_KEY" in r["message"] for r in results)

    def test_ok_for_composio_tool(self, composio_yaml: Path, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        from agentfile.commands.validate import _check_schema, _check_agents

        _, af = _check_schema(str(composio_yaml), None)
        results = _check_agents(af)
        assert any(r["level"] == "ok" and "GITHUB" in r["message"] for r in results)

    def test_warns_for_unknown_mcp_tool(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        p = tmp_path / "agentfile.yaml"
        p.write_text("""\
schema_version: "1.1"

agents:
  my-agent:
    metadata:
      description: "Agent with unknown MCP tool"
    runtime:
      provider: anthropic
      model: claude-sonnet-4-6
    tools:
      - name: unknown
        source: mcp://nonexistent-tool-xyz
""")
        from agentfile.commands.validate import _check_schema, _check_agents

        _, af = _check_schema(str(p), None)
        results = _check_agents(af)
        assert any(r["level"] == "warn" and "nonexistent-tool-xyz" in r["message"] for r in results)

    def test_structured_output_reported(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        p = tmp_path / "agentfile.yaml"
        p.write_text("""\
schema_version: "1.1"

agents:
  my-agent:
    metadata:
      description: "Structured output agent"
    runtime:
      provider: anthropic
      model: claude-sonnet-4-6
    tools:
      - name: search
        source: mcp://brave-search
    output_type:
      type: object
      properties:
        summary: {type: string}
        score: {type: number}
      required: [summary]
""")
        from agentfile.commands.validate import _check_schema, _check_agents

        _, af = _check_schema(str(p), None)
        results = _check_agents(af)
        assert any("Structured output" in r["message"] for r in results)


# ── validate_cmd ──────────────────────────────────────────────────────────────


class TestValidateCmd:
    """CLI-level tests for validate_cmd."""

    @patch("agentfile.commands.validate._check_template_render", return_value=[])
    def test_valid_file_exits_0(self, mock_render, valid_yaml: Path, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        from click.testing import CliRunner
        from agentfile.commands.validate import validate_cmd

        runner = CliRunner()
        result = runner.invoke(validate_cmd, ["--file", str(valid_yaml)])
        assert result.exit_code == 0

    def test_missing_file_exits_1(self, tmp_path: Path):
        from click.testing import CliRunner
        from agentfile.commands.validate import validate_cmd

        runner = CliRunner()
        result = runner.invoke(validate_cmd, ["--file", str(tmp_path / "missing.yaml")])
        assert result.exit_code == 1

    def test_invalid_file_exits_1(self, invalid_yaml: Path, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        from click.testing import CliRunner
        from agentfile.commands.validate import validate_cmd

        runner = CliRunner()
        result = runner.invoke(validate_cmd, ["--file", str(invalid_yaml)])
        assert result.exit_code == 1

    @patch("agentfile.commands.validate._check_template_render", return_value=[])
    def test_json_output_mode(self, mock_render, valid_yaml: Path, monkeypatch):
        """--json flag outputs valid JSON and exits with correct code."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        from click.testing import CliRunner
        from agentfile.commands.validate import validate_cmd

        runner = CliRunner()
        result = runner.invoke(validate_cmd, ["--file", str(valid_yaml), "--json"])
        assert result.exit_code == 0
        # stdout should be valid JSON
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) > 0

    def test_json_output_has_level_fields(self, valid_yaml: Path, monkeypatch):
        """Each JSON result item has 'level', 'category', 'message' keys."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        from click.testing import CliRunner
        from agentfile.commands.validate import validate_cmd

        runner = CliRunner()
        result = runner.invoke(
            validate_cmd, ["--file", str(valid_yaml), "--json", "--no-render"]
        )
        data = json.loads(result.output)
        for item in data:
            assert "level" in item
            assert "category" in item
            assert "message" in item

    @patch("agentfile.commands.validate._check_template_render", return_value=[])
    def test_no_render_skips_template_check(self, mock_render, valid_yaml: Path, monkeypatch):
        """--no-render skips _check_template_render."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        from click.testing import CliRunner
        from agentfile.commands.validate import validate_cmd

        runner = CliRunner()
        runner.invoke(validate_cmd, ["--file", str(valid_yaml), "--no-render"])
        mock_render.assert_not_called()

    @patch("agentfile.commands.validate._check_template_render", return_value=[])
    def test_unknown_environment_exits_1(self, mock_render, valid_yaml: Path):
        from click.testing import CliRunner
        from agentfile.commands.validate import validate_cmd

        runner = CliRunner()
        result = runner.invoke(
            validate_cmd, ["--file", str(valid_yaml), "--environment", "nonexistent"]
        )
        assert result.exit_code == 1


# ── _r helper ─────────────────────────────────────────────────────────────────


class TestResultHelper:
    """Tests for the _r helper that creates result dicts."""

    def test_ok_result_shape(self):
        from agentfile.commands.validate import _r
        r = _r("ok", "parse", "All good")
        assert r == {"level": "ok", "category": "parse", "message": "All good"}

    def test_error_result_shape(self):
        from agentfile.commands.validate import _r
        r = _r("error", "schema", "Bad schema")
        assert r["level"] == "error"

    def test_warn_result_shape(self):
        from agentfile.commands.validate import _r
        r = _r("warn", "env", "Missing key")
        assert r["level"] == "warn"
