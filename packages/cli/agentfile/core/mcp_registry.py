"""MCP server registry: built-in definitions + user overrides."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

import yaml


# ── Types ──────────────────────────────────────────────────────────────────────

ServerType = Literal["npx", "uvx", "docker", "python"]


@dataclass
class MCPServerDef:
    """How to launch an MCP server subprocess."""

    type: ServerType
    package: str
    args: list[str] = field(default_factory=list)
    env_keys: list[str] = field(default_factory=list)
    description: str = ""


# ── Built-in registry ─────────────────────────────────────────────────────────

BUILTIN_REGISTRY: dict[str, MCPServerDef] = {
    "tavily": MCPServerDef(
        type="npx",
        package="tavily-mcp",
        env_keys=["TAVILY_API_KEY"],
        description="Tavily Search API — AI-optimised web search, free tier available",
    ),
    "duckduckgo": MCPServerDef(
        type="npx",
        package="duckduckgo-mcp-server",
        description="DuckDuckGo web search — no API key required (blocked from cloud/datacenter IPs; use tavily instead)",
    ),
    "brave-search": MCPServerDef(
        type="npx",
        package="@modelcontextprotocol/server-brave-search",
        env_keys=["BRAVE_API_KEY"],
        description="Brave Search API — web search with privacy focus",
    ),
    "filesystem": MCPServerDef(
        type="npx",
        package="@modelcontextprotocol/server-filesystem",
        args=["/data"],
        description="Read/write access to a local filesystem directory",
    ),
    "github": MCPServerDef(
        type="npx",
        package="@modelcontextprotocol/server-github",
        env_keys=["GITHUB_PERSONAL_ACCESS_TOKEN"],
        description="GitHub API — repos, issues, PRs, code search",
    ),
    "fetch": MCPServerDef(
        type="uvx",
        package="mcp-server-fetch",
        description="HTTP fetch tool — retrieve any URL as text",
    ),
    "sqlite": MCPServerDef(
        type="uvx",
        package="mcp-server-sqlite",
        args=["--db-path", "/data/db.sqlite"],
        description="SQLite database — read/write SQL queries",
    ),
    "memory": MCPServerDef(
        type="npx",
        package="@modelcontextprotocol/server-memory",
        description="In-process key-value memory store across turns",
    ),
    "postgres": MCPServerDef(
        type="uvx",
        package="mcp-server-postgres",
        env_keys=["DATABASE_URL"],
        description="PostgreSQL database — read/write SQL queries",
    ),
    "slack": MCPServerDef(
        type="npx",
        package="@modelcontextprotocol/server-slack",
        env_keys=["SLACK_BOT_TOKEN", "SLACK_TEAM_ID"],
        description="Slack — send messages, list channels",
    ),
    "puppeteer": MCPServerDef(
        type="npx",
        package="@modelcontextprotocol/server-puppeteer",
        description="Browser automation with Puppeteer (headless Chrome)",
    ),
}


# ── User config ───────────────────────────────────────────────────────────────

def _user_config_path() -> Path:
    """Return ~/.agentfile/mcp.yaml (creates the directory if needed)."""
    config_dir = Path.home() / ".agentfile"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir / "mcp.yaml"


def load_user_registry() -> dict[str, MCPServerDef]:
    """
    Read ~/.agentfile/mcp.yaml and return MCPServerDef entries.

    YAML format:
        my-server:
          type: npx
          package: "@acme/mcp-server"
          args: ["--flag"]
          env_keys: ["MY_KEY"]
          description: "My custom server"

    Missing file is not an error — returns {}.
    """
    path = _user_config_path()
    if not path.exists():
        return {}

    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        print(f"[warning] Could not parse ~/.agentfile/mcp.yaml: {exc}", file=sys.stderr)
        return {}

    result: dict[str, MCPServerDef] = {}
    for name, fields in raw.items():
        if not isinstance(fields, dict):
            continue
        result[str(name)] = MCPServerDef(
            type=fields.get("type", "npx"),
            package=fields.get("package", ""),
            args=list(fields.get("args") or []),
            env_keys=list(fields.get("env_keys") or []),
            description=fields.get("description", ""),
        )
    return result


def get_merged_registry() -> dict[str, MCPServerDef]:
    """Return BUILTIN_REGISTRY merged with user overrides (user wins)."""
    merged = dict(BUILTIN_REGISTRY)
    merged.update(load_user_registry())
    return merged


def resolve(name: str) -> Optional[MCPServerDef]:
    """Look up an MCP server by registry key. Returns None if not found."""
    return get_merged_registry().get(name)
