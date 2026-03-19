"""Tests for agentfile.core.config — CLI configuration."""

from __future__ import annotations

import json

import pytest

from agentfile.core.config import (
    api_url_source,
    clear_api_url,
    get_api_url,
    read_config,
    resolve_api_url,
    set_api_url,
    write_config,
)


@pytest.fixture(autouse=True)
def isolate_config(tmp_path, monkeypatch):
    """Redirect CONFIG_FILE to a temp dir for every test."""
    config_file = tmp_path / ".agentfile" / "config.json"
    monkeypatch.setattr("agentfile.core.config.CONFIG_FILE", config_file)
    monkeypatch.delenv("AGENTFILE_API_URL", raising=False)
    return config_file


class TestReadConfig:
    def test_returns_empty_when_file_missing(self):
        assert read_config() == {}

    def test_reads_json(self, isolate_config):
        isolate_config.parent.mkdir(parents=True, exist_ok=True)
        isolate_config.write_text(json.dumps({"api_url": "http://test:8000"}))
        assert read_config() == {"api_url": "http://test:8000"}

    def test_returns_empty_on_corrupt_json(self, isolate_config):
        isolate_config.parent.mkdir(parents=True, exist_ok=True)
        isolate_config.write_text("not json {{{")
        assert read_config() == {}


class TestWriteConfig:
    def test_creates_file(self, isolate_config):
        write_config({"api_url": "http://test:8000"})
        assert isolate_config.exists()
        data = json.loads(isolate_config.read_text())
        assert data["api_url"] == "http://test:8000"

    def test_merges_with_existing(self, isolate_config):
        write_config({"api_url": "http://a"})
        write_config({"org_id": "org-1"})
        data = json.loads(isolate_config.read_text())
        assert data["api_url"] == "http://a"
        assert data["org_id"] == "org-1"

    def test_overwrites_existing_key(self, isolate_config):
        write_config({"api_url": "http://a"})
        write_config({"api_url": "http://b"})
        data = json.loads(isolate_config.read_text())
        assert data["api_url"] == "http://b"


class TestGetApiUrl:
    def test_returns_none_when_not_set(self):
        assert get_api_url() is None

    def test_returns_url_when_set(self, isolate_config):
        write_config({"api_url": "http://test:9000"})
        assert get_api_url() == "http://test:9000"

    def test_returns_none_for_empty_string(self, isolate_config):
        write_config({"api_url": ""})
        assert get_api_url() is None


class TestSetApiUrl:
    def test_persists_url(self, isolate_config):
        set_api_url("http://new:8000")
        assert get_api_url() == "http://new:8000"


class TestClearApiUrl:
    def test_removes_url(self, isolate_config):
        set_api_url("http://test:8000")
        clear_api_url()
        assert get_api_url() is None

    def test_noop_when_not_set(self, isolate_config):
        clear_api_url()
        assert get_api_url() is None


class TestResolveApiUrl:
    def test_env_var_wins(self, monkeypatch, isolate_config):
        set_api_url("http://config:8000")
        monkeypatch.setenv("AGENTFILE_API_URL", "http://env:9000")
        assert resolve_api_url() == "http://env:9000"

    def test_config_file_second(self, isolate_config):
        set_api_url("http://config:8000")
        assert resolve_api_url() == "http://config:8000"

    def test_localhost_fallback(self):
        assert resolve_api_url() == "http://localhost:8000"


class TestApiUrlSource:
    def test_env_var_source(self, monkeypatch):
        monkeypatch.setenv("AGENTFILE_API_URL", "http://env:9000")
        assert "env var" in api_url_source()

    def test_config_file_source(self, isolate_config):
        set_api_url("http://config:8000")
        assert "config file" in api_url_source()

    def test_default_source(self):
        assert "default" in api_url_source()
