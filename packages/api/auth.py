"""Machine secret + token verification for the Ninetrix API."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import secrets
from pathlib import Path

from fastapi import HTTPException, Request

import db

_SECRET_FILE = Path.home() / ".agentfile" / ".api-secret"
_machine_secret: str = ""
_runner_token_hashes: set[str] = set()


def init_machine_secret() -> None:
    """Generate or load the machine secret on API startup.

    The secret is written to ~/.agentfile/.api-secret (mode 0600) so the
    CLI on the same machine can read it automatically — zero config for local use.

    Also loads AGENTFILE_RUNNER_TOKENS (comma-separated) from the environment,
    which allows containers deployed via `agentfile compose` to authenticate
    without filesystem access to ~/.agentfile/.api-secret.
    """
    global _machine_secret, _runner_token_hashes
    if _SECRET_FILE.exists():
        _machine_secret = _SECRET_FILE.read_text().strip()
    else:
        _machine_secret = secrets.token_urlsafe(32)
        _SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SECRET_FILE.write_text(_machine_secret)
        _SECRET_FILE.chmod(0o600)

    # Load pre-shared runner tokens (for compose / containerised deployments).
    # Accepts both AGENTFILE_RUNNER_TOKENS (comma-separated) and the singular form.
    raw = os.environ.get("AGENTFILE_RUNNER_TOKENS") or os.environ.get("AGENTFILE_RUNNER_TOKEN", "")
    _runner_token_hashes = {
        hashlib.sha256(tok.strip().encode()).hexdigest()
        for tok in raw.split(",")
        if tok.strip()
    }


async def verify_token(request: Request) -> None:
    """FastAPI dependency — validates Authorization: Bearer <token>.

    Accepted tokens (checked in order):
    1. Machine secret  — auto-shared with CLI on the same machine via filesystem
    2. Organization token — hashed SHA-256 row in org_tokens table
    """
    auth = request.headers.get("Authorization", "")
    token = auth.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Authorization required")

    # 1. Machine secret — constant-time compare prevents timing attacks
    if _machine_secret and hmac.compare_digest(token, _machine_secret):
        return

    # 2. Pre-shared runner tokens (set via AGENTFILE_RUNNER_TOKENS env var)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    if token_hash in _runner_token_hashes:
        return

    # 3. Hashed token lookup in DB
    try:
        row = await db.pool().fetchrow(
            "SELECT id FROM org_tokens WHERE token_hash = $1", token_hash
        )
    except Exception:
        raise HTTPException(status_code=503, detail="Auth service unavailable")

    if not row:
        raise HTTPException(status_code=401, detail="Invalid token")

    # Bump last_used_at without blocking the response
    asyncio.create_task(
        db.pool().execute(
            "UPDATE org_tokens SET last_used_at = NOW() WHERE id = $1",
            row["id"],
        )
    )
