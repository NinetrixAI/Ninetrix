"""Tests for agentfile.commands.build — build_cmd error handling and _render_templates."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agentfile.core.models import AgentFile


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def valid_yaml(tmp_path: Path) -> Path:
    """A minimal valid agentfile.yaml on disk."""
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
    """An agentfile.yaml that fails model validation (zero tools)."""
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
def multi_agent_yaml(tmp_path: Path) -> Path:
    """An agentfile.yaml with two agents."""
    p = tmp_path / "agentfile.yaml"
    p.write_text("""\
schema_version: "1.1"

agents:
  agent-a:
    metadata:
      description: "Agent A"
    runtime:
      provider: anthropic
      model: claude-sonnet-4-6
    tools:
      - name: search
        source: mcp://brave-search

  agent-b:
    metadata:
      description: "Agent B"
    runtime:
      provider: openai
      model: gpt-4o
    tools:
      - name: search
        source: mcp://brave-search
""")
    return p


# ── build_cmd error handling ──────────────────────────────────────────────────


class TestBuildCmdErrors:
    """Tests for error paths in build_cmd — no real Docker involved."""

    def test_missing_file_exits(self, tmp_path: Path):
        """build_cmd exits 1 when the agentfile does not exist."""
        from click.testing import CliRunner
        from agentfile.commands.build import build_cmd

        runner = CliRunner()
        result = runner.invoke(build_cmd, ["--file", str(tmp_path / "missing.yaml")])
        assert result.exit_code == 1

    def test_validation_failure_exits(self, invalid_yaml: Path):
        """build_cmd exits 1 when agentfile fails model validation."""
        from click.testing import CliRunner
        from agentfile.commands.build import build_cmd

        runner = CliRunner()
        result = runner.invoke(build_cmd, ["--file", str(invalid_yaml)])
        assert result.exit_code == 1
        assert "Validation failed" in result.output or "at least one tool" in result.output

    def test_unknown_agent_filter_exits(self, valid_yaml: Path):
        """build_cmd exits 1 when --agent refers to an agent not in the file."""
        from click.testing import CliRunner
        from agentfile.commands.build import build_cmd

        runner = CliRunner()
        result = runner.invoke(
            build_cmd, ["--file", str(valid_yaml), "--agent", "ghost-agent"]
        )
        assert result.exit_code == 1
        assert "ghost-agent" in result.output

    def test_unknown_environment_exits(self, valid_yaml: Path):
        """build_cmd exits 1 when --environment is not defined in the file."""
        from click.testing import CliRunner
        from agentfile.commands.build import build_cmd

        runner = CliRunner()
        result = runner.invoke(
            build_cmd, ["--file", str(valid_yaml), "--environment", "nonexistent"]
        )
        assert result.exit_code == 1
        assert "nonexistent" in result.output

    def test_invalid_yaml_syntax_exits(self, tmp_path: Path):
        """build_cmd exits 1 when YAML cannot be parsed."""
        p = tmp_path / "agentfile.yaml"
        p.write_text("{{{not valid yaml")
        from click.testing import CliRunner
        from agentfile.commands.build import build_cmd

        runner = CliRunner()
        result = runner.invoke(build_cmd, ["--file", str(p)])
        assert result.exit_code == 1


class TestBuildCmdSuccess:
    """Tests for the happy path in build_cmd — Docker SDK is fully mocked."""

    @patch("agentfile.commands.build._render_templates")
    @patch("agentfile.commands.build.build_image")
    @patch("shutil.copy")
    def test_single_agent_calls_build_image(
        self,
        mock_copy,
        mock_build_image,
        mock_render,
        valid_yaml: Path,
    ):
        """build_cmd calls build_image once for a single-agent file."""
        mock_build_image.return_value = "ninetrix/my-agent:latest"

        from click.testing import CliRunner
        from agentfile.commands.build import build_cmd

        runner = CliRunner()
        result = runner.invoke(build_cmd, ["--file", str(valid_yaml)])
        assert result.exit_code == 0
        mock_build_image.assert_called_once()

    @patch("agentfile.commands.build._render_templates")
    @patch("agentfile.commands.build.build_image")
    @patch("shutil.copy")
    def test_agent_filter_builds_only_that_agent(
        self,
        mock_copy,
        mock_build_image,
        mock_render,
        multi_agent_yaml: Path,
    ):
        """--agent filter restricts build to the named agent."""
        mock_build_image.return_value = "ninetrix/agent-a:latest"

        from click.testing import CliRunner
        from agentfile.commands.build import build_cmd

        runner = CliRunner()
        result = runner.invoke(
            build_cmd, ["--file", str(multi_agent_yaml), "--agent", "agent-a"]
        )
        assert result.exit_code == 0
        # Only one call even though file has two agents
        assert mock_build_image.call_count == 1

    @patch("agentfile.commands.build._render_templates")
    @patch("agentfile.commands.build.build_image")
    @patch("shutil.copy")
    def test_push_flag_calls_push_image(
        self,
        mock_copy,
        mock_build_image,
        mock_render,
        valid_yaml: Path,
    ):
        """--push causes push_image to be called after a successful build."""
        mock_build_image.return_value = "ninetrix/my-agent:latest"

        from click.testing import CliRunner
        from agentfile.commands.build import build_cmd
        from unittest.mock import MagicMock

        mock_push = MagicMock()
        # push_image is imported lazily inside build_cmd — patch the module it comes from
        with patch("agentfile.core.docker.push_image", mock_push):
            runner = CliRunner()
            result = runner.invoke(build_cmd, ["--file", str(valid_yaml), "--push"])

        assert result.exit_code == 0
        mock_push.assert_called_once_with("ninetrix/my-agent:latest")

    @patch("agentfile.commands.build._render_templates")
    @patch("agentfile.commands.build.build_image")
    @patch("shutil.copy")
    def test_custom_tag_passed_to_build_image(
        self,
        mock_copy,
        mock_build_image,
        mock_render,
        valid_yaml: Path,
    ):
        """--tag is forwarded to build_image."""
        mock_build_image.return_value = "ninetrix/my-agent:v2.0"

        from click.testing import CliRunner
        from agentfile.commands.build import build_cmd

        runner = CliRunner()
        result = runner.invoke(build_cmd, ["--file", str(valid_yaml), "--tag", "v2.0"])
        assert result.exit_code == 0
        call_args = mock_build_image.call_args
        assert "v2.0" in str(call_args)


# ── _build_one helper ─────────────────────────────────────────────────────────


class TestBuildOne:
    """Tests for the _build_one worker-thread helper."""

    @patch("agentfile.commands.build._render_templates")
    @patch("shutil.copy")
    def test_returns_success_tuple(self, mock_copy, mock_render, valid_yaml: Path):
        """_build_one returns (True, full_tag, log_lines) on success."""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "Step 1/3\nStep 2/3\n"

        af = AgentFile.from_path(valid_yaml)
        agent = af.entry_agent

        with patch("subprocess.run", return_value=mock_proc):
            from agentfile.commands.build import _build_one

            ok, tag, lines = _build_one("my-agent", agent, af, str(valid_yaml), "latest")

        assert ok is True
        assert "my-agent" in tag
        assert "latest" in tag
        assert len(lines) == 2

    @patch("agentfile.commands.build._render_templates")
    @patch("shutil.copy")
    def test_returns_failure_tuple_on_docker_error(
        self, mock_copy, mock_render, valid_yaml: Path
    ):
        """_build_one returns (False, tag, [error_msg]) on build failure."""
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = "build failed badly"

        af = AgentFile.from_path(valid_yaml)
        agent = af.entry_agent

        with patch("subprocess.run", return_value=mock_proc):
            from agentfile.commands.build import _build_one

            ok, tag, lines = _build_one("my-agent", agent, af, str(valid_yaml), "latest")

        assert ok is False
        assert "build failed badly" in lines[-1]
