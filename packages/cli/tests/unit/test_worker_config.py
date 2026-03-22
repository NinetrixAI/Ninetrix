"""Tests for agentfile.core.worker_config — mcp-worker.yaml I/O."""

from __future__ import annotations

import pytest
import yaml

from agentfile.core import worker_config


@pytest.fixture(autouse=True)
def isolate_worker_config(tmp_path, monkeypatch):
    """Redirect all worker config paths to temp dirs."""
    global_config = tmp_path / "global" / "mcp-worker.yaml"
    project_config = tmp_path / "project" / "mcp-worker.yaml"

    monkeypatch.setattr("agentfile.core.worker_config._GLOBAL_CONFIG", global_config)
    monkeypatch.setattr("agentfile.core.worker_config._PROJECT_CONFIG", project_config)

    return {"global": global_config, "project": project_config}


class TestFindConfigPath:
    def test_prefers_project_local(self, isolate_worker_config):
        isolate_worker_config["project"].parent.mkdir(parents=True, exist_ok=True)
        isolate_worker_config["project"].write_text("servers: []")
        assert worker_config.find_config_path() == isolate_worker_config["project"]

    def test_falls_back_to_global(self, isolate_worker_config):
        assert worker_config.find_config_path() == isolate_worker_config["global"]


class TestLoad:
    def test_creates_default_scaffold(self, isolate_worker_config):
        data = worker_config.load()
        assert "servers" in data
        assert isolate_worker_config["global"].exists()

    def test_reads_existing(self, isolate_worker_config):
        isolate_worker_config["global"].parent.mkdir(parents=True, exist_ok=True)
        isolate_worker_config["global"].write_text(yaml.dump({
            "gateway_url": "ws://gw:8080",
            "servers": [{"name": "github", "type": "npx"}],
        }))
        data = worker_config.load()
        assert data["gateway_url"] == "ws://gw:8080"
        assert len(data["servers"]) == 1

    def test_invalid_yaml_raises(self, isolate_worker_config):
        isolate_worker_config["global"].parent.mkdir(parents=True, exist_ok=True)
        isolate_worker_config["global"].write_text("{{{invalid")
        with pytest.raises(ValueError, match="Cannot parse"):
            worker_config.load()

    def test_adds_servers_key_if_missing(self, isolate_worker_config):
        isolate_worker_config["global"].parent.mkdir(parents=True, exist_ok=True)
        isolate_worker_config["global"].write_text(yaml.dump({"gateway_url": "ws://gw:8080"}))
        data = worker_config.load()
        assert "servers" in data


class TestSave:
    def test_writes_yaml(self, isolate_worker_config):
        path = worker_config.save({"gateway_url": "ws://test", "servers": []})
        assert path.exists()
        data = yaml.safe_load(path.read_text())
        assert data["gateway_url"] == "ws://test"


class TestListServers:
    def test_empty(self, isolate_worker_config):
        assert worker_config.list_servers() == []

    def test_with_servers(self, isolate_worker_config):
        isolate_worker_config["global"].parent.mkdir(parents=True, exist_ok=True)
        isolate_worker_config["global"].write_text(yaml.dump({
            "servers": [
                {"name": "github", "type": "npx"},
                {"name": "slack", "type": "npx"},
            ],
        }))
        assert worker_config.list_servers() == ["github", "slack"]


class TestHasServer:
    def test_true(self, isolate_worker_config):
        isolate_worker_config["global"].parent.mkdir(parents=True, exist_ok=True)
        isolate_worker_config["global"].write_text(yaml.dump({
            "servers": [{"name": "github"}],
        }))
        assert worker_config.has_server("github") is True

    def test_false(self, isolate_worker_config):
        assert worker_config.has_server("nonexistent") is False


class TestGetServer:
    def test_found(self, isolate_worker_config):
        isolate_worker_config["global"].parent.mkdir(parents=True, exist_ok=True)
        isolate_worker_config["global"].write_text(yaml.dump({
            "servers": [{"name": "github", "type": "npx", "package": "@mcp/github"}],
        }))
        server = worker_config.get_server("github")
        assert server is not None
        assert server["package"] == "@mcp/github"

    def test_not_found(self, isolate_worker_config):
        assert worker_config.get_server("nonexistent") is None


class TestAddServer:
    def test_add_new(self, isolate_worker_config):
        worker_config.add_server("github", {"type": "npx", "package": "@mcp/github"})
        assert worker_config.has_server("github")
        server = worker_config.get_server("github")
        assert server["type"] == "npx"

    def test_replace_existing(self, isolate_worker_config):
        worker_config.add_server("github", {"type": "npx", "package": "v1"})
        worker_config.add_server("github", {"type": "npx", "package": "v2"})
        servers = worker_config.list_servers()
        assert servers.count("github") == 1
        assert worker_config.get_server("github")["package"] == "v2"


class TestRemoveServer:
    def test_remove_existing(self, isolate_worker_config):
        worker_config.add_server("github", {"type": "npx"})
        assert worker_config.remove_server("github") is True
        assert worker_config.has_server("github") is False

    def test_remove_nonexistent(self, isolate_worker_config):
        assert worker_config.remove_server("ghost") is False
