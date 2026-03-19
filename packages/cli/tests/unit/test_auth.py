"""Tests for agentfile.core.auth — token resolution."""

from __future__ import annotations

import json

import pytest

from agentfile.core.auth import (
    auth_headers,
    clear_token,
    read_token,
    save_token,
)


@pytest.fixture(autouse=True)
def isolate_auth(tmp_path, monkeypatch):
    """Redirect all auth file paths to temp dir."""
    token_file = tmp_path / "auth.json"
    secret_file = tmp_path / ".api-secret"
    cloud_secret_file = tmp_path / ".cloud-secret"
    monkeypatch.setattr("agentfile.core.auth.TOKEN_FILE", token_file)
    monkeypatch.setattr("agentfile.core.auth.SECRET_FILE", secret_file)
    monkeypatch.setattr("agentfile.core.auth.CLOUD_SECRET_FILE", cloud_secret_file)
    monkeypatch.delenv("AGENTFILE_API_TOKEN", raising=False)
    return {"token": token_file, "secret": secret_file, "cloud_secret": cloud_secret_file}


class TestReadToken:
    def test_env_var_wins(self, monkeypatch):
        monkeypatch.setenv("AGENTFILE_API_TOKEN", "env-token-123")
        assert read_token("http://localhost:8000") == "env-token-123"

    def test_auth_json(self, isolate_auth):
        isolate_auth["token"].write_text(json.dumps({"token": "stored-token"}))
        assert read_token("http://some-api.com") == "stored-token"

    def test_cloud_secret_for_localhost_8001(self, isolate_auth):
        isolate_auth["cloud_secret"].write_text("cloud-secret-val")
        assert read_token("http://localhost:8001") == "cloud-secret-val"

    def test_cloud_secret_not_used_for_other_ports(self, isolate_auth):
        isolate_auth["cloud_secret"].write_text("cloud-secret-val")
        assert read_token("http://localhost:8000") is None

    def test_machine_secret_for_localhost(self, isolate_auth):
        isolate_auth["secret"].write_text("machine-secret")
        assert read_token("http://localhost:8000") == "machine-secret"

    def test_machine_secret_for_127_0_0_1(self, isolate_auth):
        isolate_auth["secret"].write_text("machine-secret")
        assert read_token("http://127.0.0.1:8000") == "machine-secret"

    def test_no_token_returns_none(self):
        assert read_token("http://remote-api.com") is None

    def test_priority_order(self, isolate_auth, monkeypatch):
        """env var > auth.json > cloud secret > machine secret."""
        isolate_auth["token"].write_text(json.dumps({"token": "stored"}))
        isolate_auth["cloud_secret"].write_text("cloud")
        isolate_auth["secret"].write_text("machine")
        monkeypatch.setenv("AGENTFILE_API_TOKEN", "env")
        assert read_token("http://localhost:8001") == "env"

    def test_auth_json_before_secret(self, isolate_auth):
        isolate_auth["token"].write_text(json.dumps({"token": "stored"}))
        isolate_auth["secret"].write_text("machine")
        assert read_token("http://localhost:8000") == "stored"

    def test_corrupt_auth_json_falls_through(self, isolate_auth):
        isolate_auth["token"].write_text("not-json")
        isolate_auth["secret"].write_text("machine")
        assert read_token("http://localhost:8000") == "machine"


class TestAuthHeaders:
    def test_returns_bearer_header(self, monkeypatch):
        monkeypatch.setenv("AGENTFILE_API_TOKEN", "my-token")
        headers = auth_headers("http://any")
        assert headers == {"Authorization": "Bearer my-token"}

    def test_returns_empty_when_no_token(self):
        assert auth_headers("http://remote") == {}


class TestSaveToken:
    def test_saves_and_reads(self, isolate_auth):
        save_token("new-token-abc")
        assert read_token("http://remote") == "new-token-abc"

    def test_file_permissions(self, isolate_auth):
        save_token("secret")
        mode = isolate_auth["token"].stat().st_mode & 0o777
        assert mode == 0o600


class TestClearToken:
    def test_removes_file(self, isolate_auth):
        save_token("to-delete")
        clear_token()
        assert not isolate_auth["token"].exists()

    def test_noop_when_no_file(self, isolate_auth):
        clear_token()  # should not raise
