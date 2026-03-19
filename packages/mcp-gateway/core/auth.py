"""Token verification for the MCP Gateway.

Two modes controlled by environment:

  Dev mode  (MCP_GATEWAY_SAAS_API_URL not set):
    - Tokens compared against MCP_GATEWAY_SECRET env var
    - Token format: "{org_id}:{secret}" → returns org_id
                    "{secret}"           → returns "default"
    - REQUIRE_AUTH=false allows unauthenticated access (local docker-compose)

  Prod mode (MCP_GATEWAY_SAAS_API_URL set):
    - Token sent to saas-api POST /internal/v1/gateway/verify-token
    - Returns the org_id
    - 5-minute in-memory cache to avoid per-request HTTP calls
    - REQUIRE_AUTH is implicitly true (saas-api always validates)
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import HTTPException

from core import saas_client

GATEWAY_SECRET: str = os.getenv("MCP_GATEWAY_SECRET", "dev-secret")
REQUIRE_AUTH: bool = os.getenv("MCP_GATEWAY_REQUIRE_AUTH", "false").lower() == "true"
_PROD_MODE: bool = bool(saas_client.SAAS_API_URL)


async def verify_token(authorization: Optional[str]) -> str:
    """
    Validate the Authorization header and return the resolved org_id.

    In prod mode: delegates to saas-api (result cached 5 min).
    In dev mode:  validates against MCP_GATEWAY_SECRET env var.
    """
    token = ""
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()

    # ── Prod mode: saas-api token verification ─────────────────────────────────
    if _PROD_MODE:
        if not token:
            raise HTTPException(status_code=401, detail="Missing authorization token")
        org_id = await saas_client.verify_token(token)
        if org_id is None:
            raise HTTPException(status_code=403, detail="Invalid or expired token")
        return org_id

    # ── Dev mode: env-secret check ─────────────────────────────────────────────
    if not REQUIRE_AUTH:
        if token:
            if ":" in token:
                org_id, secret = token.split(":", 1)
                if secret == GATEWAY_SECRET:
                    return org_id
            if token == GATEWAY_SECRET:
                return "default"
        # dev mode: allow unauthenticated access
        return "default"

    # REQUIRE_AUTH=true but no SAAS_API_URL — strict env-secret mode
    if not token:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    if ":" in token:
        org_id, secret = token.split(":", 1)
        if secret == GATEWAY_SECRET:
            return org_id
    if token == GATEWAY_SECRET:
        return "default"

    raise HTTPException(status_code=403, detail="Invalid token")


async def verify_worker_token(token: Optional[str]) -> str:
    """
    Validate a worker WebSocket token (passed as query param, not Bearer header).
    Same logic as verify_token but accepts a raw token string.
    Returns the resolved org_id.
    """
    if _PROD_MODE:
        if not token:
            return ""  # caller will close WS with 4001
        org_id = await saas_client.verify_token(token)
        return org_id or ""

    # Dev mode
    if not REQUIRE_AUTH:
        if token and ":" in token:
            org_id, secret = token.split(":", 1)
            if secret == GATEWAY_SECRET:
                return org_id
        if token == GATEWAY_SECRET or not REQUIRE_AUTH:
            return "default"
        return "default"

    if not token:
        return ""
    if ":" in token:
        org_id, secret = token.split(":", 1)
        if secret == GATEWAY_SECRET:
            return org_id
    if token == GATEWAY_SECRET:
        return "default"
    return ""
