"""Tests for agentfile.commands.run — helper functions and run_cmd env var logic."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── _docker_url ───────────────────────────────────────────────────────────────


class TestDockerUrl:
    """Tests for _docker_url — rewrites localhost to host.docker.internal."""

    def test_rewrites_localhost(self):
        from agentfile.commands.run import _docker_url
        assert _docker_url("http://localhost:8000") == "http://host.docker.internal:8000"

    def test_rewrites_127_0_0_1(self):
        from agentfile.commands.run import _docker_url
        assert _docker_url("http://127.0.0.1:5432") == "http://host.docker.internal:5432"

    def test_leaves_remote_url_unchanged(self):
        from agentfile.commands.run import _docker_url
        url = "https://api.ninetrix.io"
        assert _docker_url(url) == url

    def test_leaves_docker_internal_unchanged(self):
        from agentfile.commands.run import _docker_url
        url = "http://host.docker.internal:8080"
        assert _docker_url(url) == url


# ── _load_dotenv_key ──────────────────────────────────────────────────────────


class TestLoadDotenvKey:
    """Tests for _load_dotenv_key — reads a key from a .env file."""

    def test_reads_simple_key(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("MY_KEY=abc123\n")
        from agentfile.commands.run import _load_dotenv_key
        assert _load_dotenv_key("MY_KEY") == "abc123"

    def test_returns_none_when_file_missing(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from agentfile.commands.run import _load_dotenv_key
        assert _load_dotenv_key("MY_KEY") is None

    def test_returns_none_for_missing_key(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("OTHER_KEY=val\n")
        from agentfile.commands.run import _load_dotenv_key
        assert _load_dotenv_key("MY_KEY") is None

    def test_strips_quotes(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text('MY_KEY="quoted-value"\n')
        from agentfile.commands.run import _load_dotenv_key
        assert _load_dotenv_key("MY_KEY") == "quoted-value"

    def test_strips_single_quotes(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("MY_KEY='single-quoted'\n")
        from agentfile.commands.run import _load_dotenv_key
        assert _load_dotenv_key("MY_KEY") == "single-quoted"

    def test_key_with_equals_in_value(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("DB_URL=postgres://user:pass@host/db\n")
        from agentfile.commands.run import _load_dotenv_key
        assert _load_dotenv_key("DB_URL") == "postgres://user:pass@host/db"

    def test_skips_comment_lines(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("# MY_KEY=should-not-be-returned\nMY_KEY=real\n")
        from agentfile.commands.run import _load_dotenv_key
        assert _load_dotenv_key("MY_KEY") == "real"


# ── run_cmd error handling ────────────────────────────────────────────────────


@pytest.fixture
def valid_agentfile(tmp_path: Path) -> Path:
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
def webhook_agentfile(tmp_path: Path) -> Path:
    p = tmp_path / "agentfile.yaml"
    p.write_text("""\
schema_version: "1.1"

agents:
  webhook-agent:
    metadata:
      description: "Webhook agent"
    runtime:
      provider: anthropic
      model: claude-sonnet-4-6
    tools:
      - name: search
        source: mcp://brave-search
    triggers:
      - type: webhook
        endpoint: /run
        port: 9100
""")
    return p


class TestRunCmdErrors:
    """Error paths in run_cmd — no real Docker or network."""

    def test_missing_file_exits(self, tmp_path: Path):
        from click.testing import CliRunner
        from agentfile.commands.run import run_cmd

        runner = CliRunner()
        result = runner.invoke(run_cmd, ["--file", str(tmp_path / "missing.yaml")])
        assert result.exit_code == 1

    def test_unknown_environment_exits(self, valid_agentfile: Path):
        from click.testing import CliRunner
        from agentfile.commands.run import run_cmd

        runner = CliRunner()
        result = runner.invoke(
            run_cmd, ["--file", str(valid_agentfile), "--environment", "nonexistent"]
        )
        assert result.exit_code == 1
        assert "nonexistent" in result.output


class TestRunCmdEnvVars:
    """Tests that run_cmd injects correct environment variables into the container."""

    @patch("agentfile.commands.run._is_local_api_running", return_value=False)
    @patch("agentfile.commands.run._is_gateway_running", return_value=False)
    @patch("agentfile.commands.run._image_exists", return_value=True)
    @patch("agentfile.commands.run.run_container")
    @patch("agentfile.commands.run._inject_integration_credentials")
    def test_injects_provider_model_temperature(
        self,
        mock_inject,
        mock_run_container,
        mock_image_exists,
        mock_gw,
        mock_api,
        valid_agentfile: Path,
        monkeypatch,
    ):
        """run_cmd always injects AGENTFILE_PROVIDER/MODEL/TEMPERATURE."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        from click.testing import CliRunner
        from agentfile.commands.run import run_cmd

        runner = CliRunner()
        result = runner.invoke(run_cmd, ["--file", str(valid_agentfile)])

        assert result.exit_code == 0
        call_kwargs = mock_run_container.call_args
        env = call_kwargs[0][1] if call_kwargs[0] else call_kwargs[1].get("env", {})
        assert env.get("AGENTFILE_PROVIDER") == "anthropic"
        assert env.get("AGENTFILE_MODEL") == "claude-sonnet-4-6"
        assert "AGENTFILE_TEMPERATURE" in env

    @patch("agentfile.commands.run._is_local_api_running", return_value=False)
    @patch("agentfile.commands.run._is_gateway_running", return_value=False)
    @patch("agentfile.commands.run._image_exists", return_value=True)
    @patch("agentfile.commands.run.run_container")
    @patch("agentfile.commands.run._inject_integration_credentials")
    def test_injects_anthropic_api_key_from_env(
        self,
        mock_inject,
        mock_run_container,
        mock_image_exists,
        mock_gw,
        mock_api,
        valid_agentfile: Path,
        monkeypatch,
    ):
        """run_cmd forwards ANTHROPIC_API_KEY from host env into container env."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-12345")

        from click.testing import CliRunner
        from agentfile.commands.run import run_cmd

        runner = CliRunner()
        result = runner.invoke(run_cmd, ["--file", str(valid_agentfile)])

        assert result.exit_code == 0
        call_kwargs = mock_run_container.call_args
        env = call_kwargs[0][1] if call_kwargs[0] else call_kwargs[1].get("env", {})
        assert env.get("ANTHROPIC_API_KEY") == "sk-test-12345"

    @patch("agentfile.commands.run._is_local_api_running", return_value=False)
    @patch("agentfile.commands.run._is_gateway_running", return_value=False)
    @patch("agentfile.commands.run._image_exists", return_value=True)
    @patch("agentfile.commands.run.run_container")
    @patch("agentfile.commands.run._inject_integration_credentials")
    def test_injects_thread_id_for_durability(
        self,
        mock_inject,
        mock_run_container,
        mock_image_exists,
        mock_gw,
        mock_api,
        valid_agentfile: Path,
        monkeypatch,
    ):
        """run_cmd injects AGENTFILE_THREAD_ID when durability is on (default)."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        from click.testing import CliRunner
        from agentfile.commands.run import run_cmd

        runner = CliRunner()
        result = runner.invoke(
            run_cmd, ["--file", str(valid_agentfile), "--thread-id", "my-thread-abc"]
        )

        assert result.exit_code == 0
        call_kwargs = mock_run_container.call_args
        env = call_kwargs[0][1] if call_kwargs[0] else call_kwargs[1].get("env", {})
        assert env.get("AGENTFILE_THREAD_ID") == "my-thread-abc"

    @patch("agentfile.commands.run._is_local_api_running", return_value=False)
    @patch("agentfile.commands.run._is_gateway_running", return_value=False)
    @patch("agentfile.commands.run._image_exists", return_value=True)
    @patch("agentfile.commands.run.run_container")
    @patch("agentfile.commands.run._inject_integration_credentials")
    def test_webhook_trigger_disables_interactive(
        self,
        mock_inject,
        mock_run_container,
        mock_image_exists,
        mock_gw,
        mock_api,
        webhook_agentfile: Path,
        monkeypatch,
    ):
        """Webhook trigger causes run_container to be called with interactive=False."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        from click.testing import CliRunner
        from agentfile.commands.run import run_cmd

        runner = CliRunner()
        result = runner.invoke(run_cmd, ["--file", str(webhook_agentfile)])

        assert result.exit_code == 0
        call_kwargs = mock_run_container.call_args
        # interactive kwarg should be False
        kwargs = call_kwargs[1] if call_kwargs[1] else {}
        assert kwargs.get("interactive") is False

    @patch("agentfile.commands.run._is_local_api_running", return_value=False)
    @patch("agentfile.commands.run._is_gateway_running", return_value=False)
    @patch("agentfile.commands.run._image_exists", return_value=True)
    @patch("agentfile.commands.run.run_container")
    @patch("agentfile.commands.run._inject_integration_credentials")
    def test_webhook_trigger_adds_port_binding(
        self,
        mock_inject,
        mock_run_container,
        mock_image_exists,
        mock_gw,
        mock_api,
        webhook_agentfile: Path,
        monkeypatch,
    ):
        """Webhook trigger causes port_bindings to include the webhook port."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        from click.testing import CliRunner
        from agentfile.commands.run import run_cmd

        runner = CliRunner()
        result = runner.invoke(run_cmd, ["--file", str(webhook_agentfile)])

        assert result.exit_code == 0
        call_kwargs = mock_run_container.call_args
        kwargs = call_kwargs[1] if call_kwargs[1] else {}
        port_bindings = kwargs.get("port_bindings", [])
        assert "9100:9100" in port_bindings

    @patch("agentfile.commands.run._is_local_api_running", return_value=False)
    @patch("agentfile.commands.run._is_gateway_running", return_value=False)
    @patch("agentfile.commands.run._image_exists", return_value=True)
    @patch("agentfile.commands.run.run_container")
    @patch("agentfile.commands.run._inject_integration_credentials")
    def test_extra_env_pairs_forwarded(
        self,
        mock_inject,
        mock_run_container,
        mock_image_exists,
        mock_gw,
        mock_api,
        valid_agentfile: Path,
        monkeypatch,
    ):
        """--env KEY=VALUE pairs are forwarded to run_container."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        from click.testing import CliRunner
        from agentfile.commands.run import run_cmd

        runner = CliRunner()
        result = runner.invoke(
            run_cmd,
            ["--file", str(valid_agentfile), "--env", "MY_CUSTOM=hello"],
        )

        assert result.exit_code == 0
        call_kwargs = mock_run_container.call_args
        env = call_kwargs[0][1] if call_kwargs[0] else call_kwargs[1].get("env", {})
        assert env.get("MY_CUSTOM") == "hello"

    @patch("agentfile.commands.run._is_local_api_running", return_value=False)
    @patch("agentfile.commands.run._is_gateway_running", return_value=False)
    @patch("agentfile.commands.run._image_exists", return_value=True)
    @patch("agentfile.commands.run.run_container")
    @patch("agentfile.commands.run._inject_integration_credentials")
    def test_agentfile_env_overrides_forwarded(
        self,
        mock_inject,
        mock_run_container,
        mock_image_exists,
        mock_gw,
        mock_api,
        valid_agentfile: Path,
        monkeypatch,
    ):
        """AGENTFILE_* host env vars are forwarded to the container via setdefault."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("AGENTFILE_MAX_TURNS", "50")

        from click.testing import CliRunner
        from agentfile.commands.run import run_cmd

        runner = CliRunner()
        result = runner.invoke(run_cmd, ["--file", str(valid_agentfile)])

        assert result.exit_code == 0
        call_kwargs = mock_run_container.call_args
        env = call_kwargs[0][1] if call_kwargs[0] else call_kwargs[1].get("env", {})
        assert env.get("AGENTFILE_MAX_TURNS") == "50"


# ── _is_local_api_running / _is_gateway_running ───────────────────────────────


class TestLocalApiHelpers:
    """Tests for the helper functions that detect local services."""

    def test_is_local_api_running_returns_false_on_connection_error(self):
        import httpx
        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            from agentfile.commands.run import _is_local_api_running
            assert _is_local_api_running() is False

    def test_is_local_api_running_returns_true_on_200(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("httpx.get", return_value=mock_resp):
            from agentfile.commands.run import _is_local_api_running
            assert _is_local_api_running() is True

    def test_is_local_api_running_returns_false_on_500(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("httpx.get", return_value=mock_resp):
            from agentfile.commands.run import _is_local_api_running
            assert _is_local_api_running() is False

    def test_is_gateway_running_returns_false_on_error(self):
        import httpx
        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            from agentfile.commands.run import _is_gateway_running
            assert _is_gateway_running() is False

    def test_is_gateway_running_returns_true_on_200(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("httpx.get", return_value=mock_resp):
            from agentfile.commands.run import _is_gateway_running
            assert _is_gateway_running() is True
