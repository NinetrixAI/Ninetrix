"""Tests for agentfile.core.docker — Docker SDK wrapper."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentfile.core.models import VolumeSpec


class TestClient:
    def test_exits_when_docker_not_running(self):
        from docker.errors import DockerException
        with patch("docker.from_env", side_effect=DockerException("not running")):
            from agentfile.core.docker import _client
            with pytest.raises(SystemExit):
                _client()

    def test_returns_client(self):
        mock_client = MagicMock()
        with patch("docker.from_env", return_value=mock_client):
            from agentfile.core.docker import _client
            assert _client() is mock_client


class TestBuildImage:
    def test_builds_and_returns_tag(self, tmp_path):
        mock_proc = MagicMock()
        mock_proc.stdout = iter(["#1 [1/3] FROM python:3.12\n"])
        mock_proc.wait.return_value = 0

        with patch("subprocess.Popen", return_value=mock_proc):
            from agentfile.core.docker import build_image
            result = build_image(tmp_path, "ninetrix/test", "v1")

        assert result == "ninetrix/test:v1"

    def test_tag_already_in_name(self, tmp_path):
        mock_proc = MagicMock()
        mock_proc.stdout = iter([])
        mock_proc.wait.return_value = 0

        with patch("subprocess.Popen", return_value=mock_proc):
            from agentfile.core.docker import build_image
            result = build_image(tmp_path, "ninetrix/test:custom")

        assert result == "ninetrix/test:custom"

    def test_exits_on_build_failure(self, tmp_path):
        mock_proc = MagicMock()
        mock_proc.stdout = iter(["ERROR: build failed\n"])
        mock_proc.wait.return_value = 1

        with patch("subprocess.Popen", return_value=mock_proc):
            from agentfile.core.docker import build_image
            with pytest.raises(SystemExit):
                build_image(tmp_path, "ninetrix/test")


class TestRunContainer:
    @patch("subprocess.run")
    def test_basic_run(self, mock_run):
        from agentfile.core.docker import run_container
        run_container("ninetrix/test:latest", env={"KEY": "val"})

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "docker" in cmd
        assert "run" in cmd
        assert "--rm" in cmd
        assert "-it" in cmd
        assert "ninetrix/test:latest" in cmd
        assert "-e" in cmd

    @patch("subprocess.run")
    def test_non_interactive(self, mock_run):
        from agentfile.core.docker import run_container
        run_container("img:latest", interactive=False)

        cmd = mock_run.call_args[0][0]
        assert "-it" not in cmd

    @patch("subprocess.run")
    def test_port_bindings(self, mock_run):
        from agentfile.core.docker import run_container
        run_container("img:latest", port_bindings=["9100:9100", "8080:8080"])

        cmd = mock_run.call_args[0][0]
        assert "-p" in cmd
        assert "9100:9100" in cmd
        assert "8080:8080" in cmd

    @patch("subprocess.run")
    def test_cpu_limit(self, mock_run):
        from agentfile.core.docker import run_container
        run_container("img:latest", cpu=2.0)

        cmd = mock_run.call_args[0][0]
        assert "--cpus" in cmd
        assert "2.0" in cmd

    @patch("subprocess.run")
    def test_memory_limit(self, mock_run):
        from agentfile.core.docker import run_container
        run_container("img:latest", memory="512Mi")

        cmd = mock_run.call_args[0][0]
        assert "--memory" in cmd

    @patch("subprocess.run")
    def test_warm_pool_no_rm(self, mock_run):
        from agentfile.core.docker import run_container
        run_container("img:latest", warm_pool=True)

        cmd = mock_run.call_args[0][0]
        assert "--rm" not in cmd

    @patch("subprocess.run")
    def test_restart_policy(self, mock_run):
        from agentfile.core.docker import run_container
        run_container("img:latest", restart_policy="on-failure:3")

        cmd = mock_run.call_args[0][0]
        assert "--restart" in cmd
        assert "on-failure:3" in cmd
        assert "--rm" not in cmd

    @patch("subprocess.run")
    def test_volume_mounts(self, mock_run):
        vol = VolumeSpec(name="data", provider="local", host_path="/tmp/data", container_path="/data")
        from agentfile.core.docker import run_container
        run_container("img:latest", volumes=[vol])

        cmd = mock_run.call_args[0][0]
        assert "-v" in cmd
        # Should contain a bind mount string
        v_idx = cmd.index("-v")
        mount_str = cmd[v_idx + 1]
        assert "/data" in mount_str

    @patch("subprocess.run")
    def test_read_only_volume(self, mock_run):
        vol = VolumeSpec(
            name="data", provider="local", host_path="/tmp/data",
            container_path="/data", read_only=True,
        )
        from agentfile.core.docker import run_container
        run_container("img:latest", volumes=[vol])

        cmd = mock_run.call_args[0][0]
        v_idx = cmd.index("-v")
        assert cmd[v_idx + 1].endswith(":ro")

    @patch("subprocess.run")
    def test_host_docker_internal(self, mock_run):
        from agentfile.core.docker import run_container
        run_container("img:latest")

        cmd = mock_run.call_args[0][0]
        assert "--add-host=host.docker.internal:host-gateway" in cmd

    @patch("subprocess.run", side_effect=FileNotFoundError())
    def test_docker_cli_not_found(self, mock_run):
        from agentfile.core.docker import run_container
        with pytest.raises(SystemExit):
            run_container("img:latest")


class TestPushImage:
    def test_pushes_successfully(self):
        mock_client = MagicMock()
        mock_client.images.push.return_value = iter([
            {"status": "Pushing"},
            {"status": "Done"},
        ])

        with patch("agentfile.core.docker._client", return_value=mock_client):
            from agentfile.core.docker import push_image
            push_image("ninetrix/test:latest")

        mock_client.images.push.assert_called_once()

    def test_exits_on_push_error(self):
        mock_client = MagicMock()
        mock_client.images.push.return_value = iter([
            {"error": "unauthorized"},
        ])

        with patch("agentfile.core.docker._client", return_value=mock_client):
            from agentfile.core.docker import push_image
            with pytest.raises(SystemExit):
                push_image("ninetrix/test:latest")

    def test_exits_on_exception(self):
        from docker.errors import DockerException
        mock_client = MagicMock()
        mock_client.images.push.side_effect = DockerException("push failed")

        with patch("agentfile.core.docker._client", return_value=mock_client):
            from agentfile.core.docker import push_image
            with pytest.raises(SystemExit):
                push_image("ninetrix/test:latest")
