"""Persistent CLI configuration stored in ~/.agentfile/config.json.

Holds non-secret settings (API URL, workspace).  Secrets (tokens, machine
secrets) live in auth.json / .api-secret — never here.

Resolution order for get_api_url() (first match wins):
  1. AGENTFILE_API_URL env var      — CI/CD, Docker, project-level override
  2. ~/.agentfile/config.json       — set once after install via `ninetrix auth login`
                                      or `ninetrix config set api-url …`
  3. None                           — caller falls back to auto-detect / localhost
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CONFIG_FILE = Path.home() / ".agentfile" / "config.json"

_CLOUD_DEFAULT = "https://api.ninetrix.io"


# ── Low-level read / write ──────────────────────────────────────────────────

def read_config() -> dict[str, Any]:
    """Return the parsed config dict, or {} if the file is missing / unreadable."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text()) or {}
    except Exception:
        return {}


def write_config(data: dict[str, Any]) -> None:
    """Persist *data* to config.json, merging with any existing keys."""
    current = read_config()
    current.update(data)
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(current, indent=2))
    # 0644 — api_url is not secret, but limit to the owning user anyway
    CONFIG_FILE.chmod(0o644)


# ── API URL ─────────────────────────────────────────────────────────────────

def get_api_url() -> str | None:
    """Return the configured API URL, or None if not set.

    Does NOT fall back to localhost — callers decide the final default so they
    can distinguish "user explicitly configured cloud" from "nothing set".
    Env var is intentionally NOT checked here; call sites check it first.
    """
    return read_config().get("api_url") or None


def set_api_url(url: str) -> None:
    """Persist an API URL to config.json."""
    write_config({"api_url": url})


def resolve_api_url() -> str:
    """Return the best available API URL for use in CLI commands.

    Resolution order:
      1. AGENTFILE_API_URL env var
      2. ~/.agentfile/config.json api_url
      3. http://localhost:8000  (local dev fallback)
    """
    return (
        os.environ.get("AGENTFILE_API_URL")
        or get_api_url()
        or "http://localhost:8000"
    )


# ── SaaS API URL (for vault-requiring commands like mcp connect) ─────────────

def resolve_saas_url() -> str | None:
    """Return the saas-api URL for commands that require the credential vault.

    Resolution order (first match wins):
      1. AGENTFILE_API_URL env var  — explicit override always wins
      2. ~/.agentfile/config.json   — saved by `ninetrix auth login`
      3. localhost:8001 probe       — auto-detect local saas-api dev instance
      4. auth.json token present    — user is logged in, assume cloud default
      5. None                       — caller should print a login hint
    """
    # 1. Explicit override
    if url := os.environ.get("AGENTFILE_API_URL"):
        return url

    # 2. Saved by auth login
    if url := get_api_url():
        return url

    # 3. Auto-probe local saas-api (silent, 1s timeout — zero config for local dev)
    try:
        import httpx
        r = httpx.get("http://localhost:8001/health", timeout=1.0)
        if r.is_success:
            return "http://localhost:8001"
    except Exception:
        pass

    # 4. Token exists → user logged in at some point, cloud is the safe default
    _auth_file = Path.home() / ".agentfile" / "auth.json"
    if _auth_file.exists():
        try:
            if json.loads(_auth_file.read_text()).get("token"):
                return _CLOUD_DEFAULT
        except Exception:
            pass

    return None


# ── Source labelling (for ninetrix config show) ─────────────────────────────

def api_url_source() -> str:
    """Describe where the current API URL comes from."""
    if os.environ.get("AGENTFILE_API_URL"):
        return "env var (AGENTFILE_API_URL)"
    if get_api_url():
        return f"config file ({CONFIG_FILE})"
    return "default (localhost:8000)"
