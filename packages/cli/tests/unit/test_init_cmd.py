"""Tests for agentfile.commands.init — init_cmd scaffolding."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


class TestInitCmd:
    """Tests for init_cmd — scaffolds a new agentfile.yaml."""

    def test_creates_file_with_yes_flag(self, tmp_path: Path):
        """--yes creates agentfile.yaml without prompting."""
        from click.testing import CliRunner
        from agentfile.commands.init import init_cmd

        out = tmp_path / "agentfile.yaml"
        runner = CliRunner()
        result = runner.invoke(init_cmd, ["--yes", "--name", "my-agent", str(out)])
        assert result.exit_code == 0
        assert out.exists()

    def test_generated_yaml_is_valid(self, tmp_path: Path):
        """The scaffolded YAML parses without errors."""
        from click.testing import CliRunner
        from agentfile.commands.init import init_cmd

        out = tmp_path / "agentfile.yaml"
        runner = CliRunner()
        runner.invoke(init_cmd, ["--yes", "--name", "my-agent", str(out)])

        data = yaml.safe_load(out.read_text())
        assert "agents" in data
        assert "schema_version" in data

    def test_agent_name_in_output(self, tmp_path: Path):
        """The agent name option appears in the generated file."""
        from click.testing import CliRunner
        from agentfile.commands.init import init_cmd

        out = tmp_path / "agentfile.yaml"
        runner = CliRunner()
        runner.invoke(init_cmd, ["--yes", "--name", "research-bot", str(out)])

        content = out.read_text()
        assert "research-bot" in content

    def test_provider_option_used(self, tmp_path: Path):
        """--provider sets the provider in the generated file."""
        from click.testing import CliRunner
        from agentfile.commands.init import init_cmd

        out = tmp_path / "agentfile.yaml"
        runner = CliRunner()
        runner.invoke(init_cmd, ["--yes", "--provider", "openai", str(out)])

        content = out.read_text()
        assert "openai" in content

    def test_default_provider_is_anthropic(self, tmp_path: Path):
        """When no --provider given, default is anthropic."""
        from click.testing import CliRunner
        from agentfile.commands.init import init_cmd

        out = tmp_path / "agentfile.yaml"
        runner = CliRunner()
        runner.invoke(init_cmd, ["--yes", str(out)])

        content = out.read_text()
        assert "anthropic" in content

    def test_name_spaces_become_hyphens_in_key(self, tmp_path: Path):
        """Agent names with spaces are sanitized to valid YAML keys."""
        from click.testing import CliRunner
        from agentfile.commands.init import init_cmd

        out = tmp_path / "agentfile.yaml"
        runner = CliRunner()
        runner.invoke(init_cmd, ["--yes", "--name", "My Cool Agent", str(out)])

        data = yaml.safe_load(out.read_text())
        assert "my-cool-agent" in data["agents"]

    def test_output_path_as_argument(self, tmp_path: Path):
        """The output path argument determines where the file is written."""
        from click.testing import CliRunner
        from agentfile.commands.init import init_cmd

        custom_out = tmp_path / "custom" / "path.yaml"
        custom_out.parent.mkdir(parents=True)
        runner = CliRunner()
        runner.invoke(init_cmd, ["--yes", str(custom_out)])

        assert custom_out.exists()

    def test_all_providers_produce_valid_yaml(self, tmp_path: Path):
        """Every supported provider generates syntactically valid YAML."""
        from click.testing import CliRunner
        from agentfile.commands.init import init_cmd, _PROVIDERS

        runner = CliRunner()
        for provider in _PROVIDERS:
            out = tmp_path / f"{provider}.yaml"
            result = runner.invoke(init_cmd, ["--yes", "--provider", provider, str(out)])
            assert result.exit_code == 0, f"Failed for provider {provider}: {result.output}"
            data = yaml.safe_load(out.read_text())
            assert "agents" in data

    def test_google_provider_uses_gemini_model(self, tmp_path: Path):
        """Google provider uses a Gemini model, not claude."""
        from click.testing import CliRunner
        from agentfile.commands.init import init_cmd

        out = tmp_path / "agentfile.yaml"
        runner = CliRunner()
        runner.invoke(init_cmd, ["--yes", "--provider", "google", str(out)])

        content = out.read_text()
        assert "gemini" in content

    def test_exits_0_on_success(self, tmp_path: Path):
        from click.testing import CliRunner
        from agentfile.commands.init import init_cmd

        runner = CliRunner()
        out = tmp_path / "agentfile.yaml"
        result = runner.invoke(init_cmd, ["--yes", str(out)])
        assert result.exit_code == 0

    def test_schema_version_is_latest(self, tmp_path: Path):
        """Generated file uses the latest schema version."""
        from click.testing import CliRunner
        from agentfile.commands.init import init_cmd
        from agentfile.core.models import LATEST_SCHEMA_VERSION

        out = tmp_path / "agentfile.yaml"
        runner = CliRunner()
        runner.invoke(init_cmd, ["--yes", str(out)])

        data = yaml.safe_load(out.read_text())
        assert data.get("schema_version") == LATEST_SCHEMA_VERSION
