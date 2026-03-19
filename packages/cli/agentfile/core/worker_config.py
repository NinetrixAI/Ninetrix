"""Read and write ~/.agentfile/mcp-worker.yaml (or ./mcp-worker.yaml if present).

This module owns all mutations to the worker config file.  It is the single
place that `ninetrix mcp add / remove` touch to keep the worker in sync.

Lookup order (first found wins):
  1. ./mcp-worker.yaml          — project-local, can be version-controlled
  2. ~/.agentfile/mcp-worker.yaml — global default, created by `ninetrix dev`
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


_GLOBAL_CONFIG = Path.home() / ".agentfile" / "mcp-worker.yaml"
_PROJECT_CONFIG = Path("mcp-worker.yaml")

# Default scaffold written when the file does not yet exist
_DEFAULT_SCAFFOLD = """\
# mcp-worker configuration
# Edit this file to add/remove MCP servers.
# Run `ninetrix mcp add <server>` to add a server from the built-in catalog.
#
# All top-level fields can also be overridden with environment variables:
#   gateway_url    → MCP_GATEWAY_URL
#   org_id         → MCP_ORG_ID
#   worker_name    → MCP_WORKER_NAME
#   token          → MCP_GATEWAY_TOKEN

gateway_url: "ws://mcp-gateway:8080"
org_id: "default"
worker_name: "default"
token: "dev-secret"

servers: []
"""


def find_config_path() -> Path:
    """Return the active worker config path (project-local preferred over global)."""
    if _PROJECT_CONFIG.exists():
        return _PROJECT_CONFIG
    return _GLOBAL_CONFIG


def load() -> dict[str, Any]:
    """Load and return the worker config as a dict.  Creates the file if absent."""
    path = find_config_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_DEFAULT_SCAFFOLD)
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Cannot parse {path}: {exc}") from exc
    if "servers" not in data:
        data["servers"] = []
    return data


def save(data: dict[str, Any]) -> Path:
    """Write *data* back to the active config path.  Returns the path written."""
    path = find_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True))
    return path


def list_servers() -> list[str]:
    """Return names of all configured servers."""
    data = load()
    return [s["name"] for s in data.get("servers", []) if isinstance(s, dict) and "name" in s]


def has_server(name: str) -> bool:
    return name in list_servers()


def get_server(name: str) -> dict | None:
    """Return the server block for *name*, or None if not present."""
    data = load()
    for s in data.get("servers", []):
        if isinstance(s, dict) and s.get("name") == name:
            return s
    return None


def add_server(name: str, block: dict[str, Any]) -> Path:
    """Add or replace a server block.  Returns the config path written."""
    data = load()
    servers: list = data.get("servers", [])
    # Replace existing entry if present
    for i, s in enumerate(servers):
        if isinstance(s, dict) and s.get("name") == name:
            servers[i] = {"name": name, **block}
            data["servers"] = servers
            return save(data)
    # Append new entry
    servers.append({"name": name, **block})
    data["servers"] = servers
    return save(data)


def remove_server(name: str) -> bool:
    """Remove a server by name.  Returns True if it was present, False otherwise."""
    data = load()
    before = len(data.get("servers", []))
    data["servers"] = [
        s for s in data.get("servers", [])
        if not (isinstance(s, dict) and s.get("name") == name)
    ]
    if len(data["servers"]) == before:
        return False
    save(data)
    return True
