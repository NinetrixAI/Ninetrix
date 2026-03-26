"""Tests for agentfile.core.docker — Docker SDK wrapper."""

from __future__ import annotations

import io
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
    @staticmethod
    def _mock_proc(lines, rc=0):
        proc = MagicMock()
        proc.stdout = io.StringIO("".join(lines))
        proc.poll = MagicMock(side_effect=[None] * len(lines) + [rc])
        proc.wait.return_value = rc
        return proc

    def test_builds_and_returns_tag(self, tmp_path):
        mock_proc = self._mock_proc(["#1 [1/3] FROM python:3.12\n"])

        with patch("subprocess.Popen", return_value=mock_proc):
            from agentfile.core.docker import build_image
            result = build_image(tmp_path, "ninetrix/test", "v1")

        assert result == "ninetrix/test:v1"

    def test_tag_already_in_name(self, tmp_path):
        mock_proc = self._mock_proc([])

        with patch("subprocess.Popen", return_value=mock_proc):
            from agentfile.core.docker import build_image
            result = build_image(tmp_path, "ninetrix/test:custom")

        assert result == "ninetrix/test:custom"

    def test_exits_on_build_failure(self, tmp_path):
        mock_proc = self._mock_proc(["ERROR: build failed\n"], rc=1)

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
        # Env vars should be passed via --env-file (not -e) to avoid ps aux exposure.
        assert "--env-file" in cmd
        assert "-e" not in cmd

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


class TestEnvFileSecurity:
    """Tests for env-file based secret passing (prevents ps aux exposure)."""

    @patch("subprocess.run")
    def test_env_file_created_with_env_vars(self, mock_run):
        """Env vars should be written to a temp file and passed via --env-file."""
        from agentfile.core.docker import run_container
        run_container("img:latest", env={"API_KEY": "sk-secret", "FOO": "bar"})

        cmd = mock_run.call_args[0][0]
        assert "--env-file" in cmd
        # The secret should NOT appear anywhere in the command line
        assert "sk-secret" not in " ".join(cmd)

    @patch("subprocess.run")
    def test_env_file_cleaned_up(self, mock_run):
        """The temp env file should be deleted after the container run."""
        import os
        from agentfile.core.docker import run_container
        run_container("img:latest", env={"KEY": "val"})

        cmd = mock_run.call_args[0][0]
        env_file_idx = cmd.index("--env-file")
        env_file_path = cmd[env_file_idx + 1]
        # File should have been cleaned up in the finally block
        assert not os.path.exists(env_file_path)

    @patch("subprocess.run")
    def test_env_file_cleaned_up_on_error(self, mock_run):
        """The temp env file should be cleaned up even if subprocess raises."""
        import os
        mock_run.side_effect = RuntimeError("boom")
        from agentfile.core.docker import run_container
        try:
            run_container("img:latest", env={"KEY": "val"})
        except RuntimeError:
            pass

        cmd = mock_run.call_args[0][0]
        env_file_idx = cmd.index("--env-file")
        env_file_path = cmd[env_file_idx + 1]
        assert not os.path.exists(env_file_path)

    @patch("subprocess.run")
    def test_env_file_permissions(self, mock_run):
        """The env file should have 0600 permissions while it exists."""
        import os
        import stat

        perms_seen = []

        def capture_perms(cmd, **kwargs):
            idx = cmd.index("--env-file")
            path = cmd[idx + 1]
            if os.path.exists(path):
                mode = os.stat(path).st_mode
                perms_seen.append(stat.S_IMODE(mode))
            return MagicMock(returncode=0, stderr="")

        mock_run.side_effect = capture_perms

        from agentfile.core.docker import run_container
        run_container("img:latest", env={"SECRET": "hunter2"})

        assert len(perms_seen) == 1
        assert perms_seen[0] == 0o600

    @patch("subprocess.run")
    def test_empty_env_still_uses_env_file(self, mock_run):
        """Even with no env vars, --env-file should be used (empty file)."""
        from agentfile.core.docker import run_container
        run_container("img:latest", env={})

        cmd = mock_run.call_args[0][0]
        assert "--env-file" in cmd

    @patch("subprocess.run")
    def test_env_file_content_format(self, mock_run):
        """Verify the env file content matches Docker --env-file format."""
        import os

        content_seen = []

        def capture_content(cmd, **kwargs):
            idx = cmd.index("--env-file")
            path = cmd[idx + 1]
            if os.path.exists(path):
                with open(path) as f:
                    content_seen.append(f.read())
            return MagicMock(returncode=0, stderr="")

        mock_run.side_effect = capture_content

        from agentfile.core.docker import run_container
        run_container("img:latest", env={"A": "1", "B": "hello world"})

        assert len(content_seen) == 1
        lines = content_seen[0].strip().split("\n")
        parsed = dict(line.split("=", 1) for line in lines)
        assert parsed["A"] == "1"
        assert parsed["B"] == "hello world"


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
