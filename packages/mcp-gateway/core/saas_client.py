"""HTTP client for mcp-gateway → saas-api internal calls.

Provides two operations with in-memory caching:
  1. verify_token(token)   → org_id   (5-min TTL)
  2. get_tool_credential(org_id, integration_id, tool_name) → env_vars dict
  3. get_integration_auth_url(org_id, integration_id) → auth_url | None

All calls use X-Gateway-Secret for authentication.
Only active when MCP_GATEWAY_SAAS_API_URL is set (prod mode).
In dev mode (no SAAS_API_URL), the gateway falls back to the shared env-secret.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Optional

import httpx

log = logging.getLogger(__name__)

SAAS_API_URL: str = os.getenv("MCP_GATEWAY_SAAS_API_URL", "").rstrip("/")
GATEWAY_SERVICE_SECRET: str = os.getenv("MCP_GATEWAY_SERVICE_SECRET", "dev-gateway-secret")

# In-memory token cache: token_hash → (org_id, expires_at)
_token_cache: dict[str, tuple[str, float]] = {}
_TOKEN_TTL = 300  # 5 minutes


def _headers() -> dict[str, str]:
    return {"X-Gateway-Secret": GATEWAY_SERVICE_SECRET}


async def verify_token(token: str) -> Optional[str]:
    """
    Resolve a worker/agent token to an org_id.
    Returns None if the token is invalid (caller should 401/403).
    Caches valid results for 5 minutes to avoid per-request HTTP round-trips.
    """
    if not SAAS_API_URL:
        return None  # dev mode — caller uses env-secret fallback

    token_hash = hashlib.sha256(token.encode()).hexdigest()

    # Cache hit
    cached = _token_cache.get(token_hash)
    if cached:
        org_id, expires_at = cached
        if time.monotonic() < expires_at:
            return org_id
        del _token_cache[token_hash]

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(
                f"{SAAS_API_URL}/internal/v1/gateway/verify-token",
                json={"token": token},
                headers=_headers(),
            )
        if resp.status_code == 401:
            return None
        resp.raise_for_status()
        data = resp.json()
        org_id = data.get("org_id") or data.get("workspace_id")
        _token_cache[token_hash] = (org_id, time.monotonic() + _TOKEN_TTL)
        return org_id

    except httpx.HTTPStatusError as exc:
        log.warning("verify_token: saas-api returned %s", exc.response.status_code)
        return None
    except Exception as exc:
        log.error("verify_token: saas-api unreachable: %s", exc)
        return None


async def get_tool_credential(
    worker_token: str,
    integration_id: str,
    tool_name: Optional[str] = None,
) -> dict[str, str]:
    """
    Fetch credentials for ONE integration from saas-api.
    Returns env_vars dict (e.g. {"SLACK_BOT_TOKEN": "xoxb-..."}).
    Raises RuntimeError on auth failure or disconnected integration.
    """
    if not SAAS_API_URL:
        return {}  # dev mode — no credential injection

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.post(
                f"{SAAS_API_URL}/internal/v1/gateway/tool-credential",
                json={
                    "worker_token": worker_token,
                    "integration_id": integration_id,
                    "tool_name": tool_name,
                },
                headers=_headers(),
            )
        if resp.status_code == 404:
            raise RuntimeError(f"Integration '{integration_id}' not connected")
        if resp.status_code == 401:
            raise RuntimeError("Worker token rejected by saas-api")
        resp.raise_for_status()
        return resp.json().get("env_vars", {})

    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Failed to fetch credentials for '{integration_id}': {exc}") from exc


async def get_integration_auth_url(
    org_id: str,
    integration_id: str,
) -> Optional[str]:
    """
    Return an OAuth2 authorization URL if the integration is not connected,
    or None if it is already connected or the integration doesn't support OAuth2.
    """
    if not SAAS_API_URL:
        return None  # dev mode

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(
                f"{SAAS_API_URL}/internal/v1/gateway/integration-auth-url",
                json={"org_id": org_id, "integration_id": integration_id},
                headers=_headers(),
            )
        resp.raise_for_status()
        data = resp.json()
        if data.get("connected"):
            return None
        return data.get("auth_url")
    except Exception as exc:
        log.debug("get_integration_auth_url failed: %s", exc)
        return None
