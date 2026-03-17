"""Token resolution for CLI → API authentication.

Resolution order (first match wins):
  1. AGENTFILE_API_TOKEN env var        — CI/CD, scripts, Docker
  2. ~/.agentfile/auth.json             — saved by `ninetrix auth login`
  3. ~/.agentfile/.cloud-secret         — written by saas-api on localhost startup;
                                          only used when the API URL is localhost:8001
  4. ~/.agentfile/.api-secret           — machine secret written by the local api on startup;
                                          only used when the API URL is localhost (any port)
"""
from __future__ import annotations

import json
import os
import urllib.parse
from pathlib import Path

TOKEN_FILE        = Path.home() / ".agentfile" / "auth.json"
SECRET_FILE       = Path.home() / ".agentfile" / ".api-secret"
CLOUD_SECRET_FILE = Path.home() / ".agentfile" / ".cloud-secret"


def read_token(api_url: str) -> str | None:
    """Return the best available token for the given API URL, or None."""
    # 1. Env var — highest priority, works everywhere
    if t := os.environ.get("AGENTFILE_API_TOKEN"):
        return t

    # 2. Stored token from `ninetrix auth login`
    if TOKEN_FILE.exists():
        try:
            data = json.loads(TOKEN_FILE.read_text())
            if t := data.get("token"):
                return t
        except Exception:
            pass

    try:
        parsed = urllib.parse.urlparse(api_url)
        host = parsed.hostname or ""
        port = parsed.port
    except Exception:
        host, port = "", None

    is_localhost = host in ("localhost", "127.0.0.1")

    # 3. Cloud secret — saas-api dev instance on localhost:8001
    if is_localhost and port == 8001 and CLOUD_SECRET_FILE.exists():
        if t := CLOUD_SECRET_FILE.read_text().strip():
            return t

    # 4. Machine secret — local open-source api on localhost (any port)
    if is_localhost and SECRET_FILE.exists():
        return SECRET_FILE.read_text().strip()

    return None


def auth_headers(api_url: str) -> dict[str, str]:
    """Return an Authorization header dict, or empty dict if no token available."""
    token = read_token(api_url)
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def save_token(token: str) -> None:
    """Persist a token to disk (mode 0600)."""
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps({"token": token}))
    TOKEN_FILE.chmod(0o600)


def clear_token() -> None:
    """Remove the stored token file."""
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()
