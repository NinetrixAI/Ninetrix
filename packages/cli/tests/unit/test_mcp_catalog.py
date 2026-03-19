"""Tests for agentfile.core.mcp_catalog — MCP server catalog."""

from __future__ import annotations

import pytest

from agentfile.core.mcp_catalog import CATALOG, CatalogEntry, get, list_all


class TestCatalogEntry:
    def test_worker_yaml_block_basic(self):
        entry = CatalogEntry(
            description="Test",
            type="npx",
            package="@test/server",
        )
        block = entry.worker_yaml_block()
        assert block == {"type": "npx", "package": "@test/server"}

    def test_worker_yaml_block_with_args(self):
        entry = CatalogEntry(
            description="Filesystem",
            type="npx",
            package="@mcp/server-filesystem",
            args=["/workspace"],
        )
        block = entry.worker_yaml_block()
        assert block["args"] == ["/workspace"]

    def test_worker_yaml_block_with_env(self):
        entry = CatalogEntry(
            description="GitHub",
            type="npx",
            package="@mcp/server-github",
            required_env={"GITHUB_PERSONAL_ACCESS_TOKEN": "Token"},
            env_aliases={"GITHUB_TOKEN": "GITHUB_PERSONAL_ACCESS_TOKEN"},
        )
        block = entry.worker_yaml_block()
        assert "env" in block
        # Should use the alias as the source
        assert block["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == "${GITHUB_TOKEN}"

    def test_worker_yaml_block_env_no_alias(self):
        entry = CatalogEntry(
            description="Brave",
            type="npx",
            package="@mcp/brave-search",
            required_env={"BRAVE_API_KEY": "API key"},
        )
        block = entry.worker_yaml_block()
        assert block["env"]["BRAVE_API_KEY"] == "${BRAVE_API_KEY}"

    def test_missing_env_all_present(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "value")
        entry = CatalogEntry(
            description="Test",
            type="npx",
            package="test",
            required_env={"MY_KEY": "label"},
        )
        assert entry.missing_env() == []

    def test_missing_env_none_present(self, monkeypatch):
        monkeypatch.delenv("MISSING_KEY", raising=False)
        entry = CatalogEntry(
            description="Test",
            type="npx",
            package="test",
            required_env={"MISSING_KEY": "label"},
        )
        assert entry.missing_env() == ["MISSING_KEY"]

    def test_missing_env_alias_satisfies(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_123")
        monkeypatch.delenv("GITHUB_PERSONAL_ACCESS_TOKEN", raising=False)
        entry = CatalogEntry(
            description="GitHub",
            type="npx",
            package="test",
            required_env={"GITHUB_PERSONAL_ACCESS_TOKEN": "Token"},
            env_aliases={"GITHUB_TOKEN": "GITHUB_PERSONAL_ACCESS_TOKEN"},
        )
        assert entry.missing_env() == []

    def test_resolve_env_value_direct(self, monkeypatch):
        monkeypatch.setenv("BRAVE_API_KEY", "brave-123")
        entry = CatalogEntry(
            description="Test",
            type="npx",
            package="test",
            required_env={"BRAVE_API_KEY": "Key"},
        )
        assert entry.resolve_env_value("BRAVE_API_KEY") == "brave-123"

    def test_resolve_env_value_alias(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_alias")
        monkeypatch.delenv("GITHUB_PERSONAL_ACCESS_TOKEN", raising=False)
        entry = CatalogEntry(
            description="Test",
            type="npx",
            package="test",
            required_env={"GITHUB_PERSONAL_ACCESS_TOKEN": "Token"},
            env_aliases={"GITHUB_TOKEN": "GITHUB_PERSONAL_ACCESS_TOKEN"},
        )
        assert entry.resolve_env_value("GITHUB_PERSONAL_ACCESS_TOKEN") == "ghp_alias"

    def test_resolve_env_value_none(self, monkeypatch):
        monkeypatch.delenv("NONEXISTENT", raising=False)
        entry = CatalogEntry(
            description="Test",
            type="npx",
            package="test",
            required_env={"NONEXISTENT": "label"},
        )
        assert entry.resolve_env_value("NONEXISTENT") is None


class TestCatalog:
    def test_github_exists(self):
        assert "github" in CATALOG

    def test_filesystem_exists(self):
        assert "filesystem" in CATALOG

    def test_get_found(self):
        entry = get("github")
        assert entry is not None
        assert entry.type == "npx"
        assert "github" in entry.package.lower()

    def test_get_not_found(self):
        assert get("nonexistent-server") is None

    def test_list_all(self):
        all_entries = list_all()
        assert len(all_entries) > 0
        assert isinstance(all_entries, dict)
        for name, entry in all_entries.items():
            assert isinstance(entry, CatalogEntry)

    def test_known_servers(self):
        expected = [
            "github", "filesystem", "slack", "notion", "linear",
            "brave-search", "postgres", "sqlite", "fetch", "memory",
            "stripe", "google-drive", "puppeteer", "tavily",
        ]
        for name in expected:
            assert name in CATALOG, f"Expected '{name}' in catalog"

    def test_github_env_alias(self):
        github = CATALOG["github"]
        assert "GITHUB_TOKEN" in github.env_aliases
        assert github.env_aliases["GITHUB_TOKEN"] == "GITHUB_PERSONAL_ACCESS_TOKEN"

    def test_postgres_env_alias(self):
        pg = CATALOG["postgres"]
        assert "DATABASE_URL" in pg.env_aliases
