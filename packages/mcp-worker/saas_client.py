"""HTTP client for mcp-worker → saas-api credential fetching.

Only active when MCP_SAAS_API_URL is set (prod/SaaS mode).
In dev/enterprise mode (no SAAS_API_URL), this module is a no-op and
credentials must be provided via mcp-worker.yaml env blocks.

The worker calls get_tool_credential() once per integration when that
integration's MCP server is first needed (lazy startup). Credentials
are passed as env vars to the MCP server subprocess and are NOT stored
in the worker process memory beyond that point.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

log = logging.getLogger(__name__)

SAAS_API_URL: str = os.getenv("MCP_SAAS_API_URL", "").rstrip("/")
WORKER_TOKEN: str = os.getenv("MCP_GATEWAY_TOKEN", "")


def is_saas_mode() -> bool:
    return bool(SAAS_API_URL and WORKER_TOKEN)


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {WORKER_TOKEN}"}


async def get_tool_credential(
    integration_id: str,
    tool_name: Optional[str] = None,
) -> dict[str, str]:
    """
    Fetch env vars for a single integration from saas-api.
    Calls GET /v1/integrations/credentials (Bearer-auth, no shared secret required)
    and returns only the env-var map for the requested integration_id.
    Returns {} in dev mode or on failure (caller falls back to yaml env block).
    """
    if not is_saas_mode():
        return {}

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                f"{SAAS_API_URL}/v1/integrations/credentials",
                headers=_auth_headers(),
            )
        if resp.status_code == 401:
            log.warning("Worker token rejected by saas-api — check MCP_GATEWAY_TOKEN")
            return {}
        resp.raise_for_status()
        all_creds: dict[str, dict[str, str]] = resp.json()
        env_vars = all_creds.get(integration_id, {})
        if env_vars:
            log.info(
                "Fetched %d credential(s) for integration '%s'",
                len(env_vars), integration_id,
            )
        else:
            log.info("Integration '%s' not connected in saas-api", integration_id)
        return env_vars
    except Exception as exc:
        log.warning("Could not fetch credentials for '%s': %s", integration_id, exc)
        return {}


async def refresh_credential(integration_id: str) -> dict[str, str]:
    """
    Trigger a token refresh for a Google Workspace integration (access_token expired).
    Re-fetches from saas-api which will call Google's /token endpoint internally.
    Returns new env_vars or {} on failure.
    """
    if not is_saas_mode():
        return {}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{SAAS_API_URL}/internal/v1/runners/credentials/refresh",
                json={"integration_id": integration_id},
                headers=_auth_headers(),
            )
        resp.raise_for_status()
        # After refresh, re-fetch the updated credential
        return await get_tool_credential(integration_id)
    except Exception as exc:
        log.warning("Credential refresh failed for '%s': %s", integration_id, exc)
        return {}
