"""Organization token management — create, list, revoke personal access tokens.

These endpoints are intentionally unprotected: they manage token *metadata*
(labels, ids) only — the raw token is shown exactly once at creation and is
never stored. Revoking a token requires knowing its UUID from the list, which
is only visible to someone who can already reach the API.
"""
from __future__ import annotations

import hashlib
import secrets

from fastapi import APIRouter, HTTPException

import db
from models import CreateTokenPayload, OrgToken

router = APIRouter()

_TOKEN_PREFIX = "nxt_"


@router.get("")
async def list_tokens() -> list[OrgToken]:
    """List all organization tokens (no raw values — those are shown once at creation)."""
    rows = await db.pool().fetch(
        """
        SELECT id::text, label, created_at, last_used_at
        FROM org_tokens
        WHERE org_id = 'default'
        ORDER BY created_at DESC
        """
    )
    return [OrgToken(**dict(row)) for row in rows]


@router.post("")
async def create_token(payload: CreateTokenPayload) -> dict:
    """Create a new token. Returns the raw value exactly once — copy it now."""
    raw = _TOKEN_PREFIX + secrets.token_urlsafe(32)
    h = hashlib.sha256(raw.encode()).hexdigest()
    await db.pool().execute(
        """
        INSERT INTO org_tokens (org_id, token_hash, label)
        VALUES ('default', $1, $2)
        """,
        h, payload.label,
    )
    return {"token": raw, "label": payload.label}


@router.delete("/{token_id}")
async def revoke_token(token_id: str) -> dict:
    """Revoke a token by its UUID."""
    result = await db.pool().execute(
        "DELETE FROM org_tokens WHERE id::text = $1 AND org_id = 'default'",
        token_id,
    )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Token not found")
    return {"status": "revoked"}
