from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Header, Request

from core.auth import verify_token
from core import saas_client
from core.registry import registry

logger = logging.getLogger(__name__)

router = APIRouter()

# JSON-RPC error codes
_TOOL_NOT_FOUND   = -32601
_INTERNAL_ERROR   = -32603
_AUTH_REQUIRED    = -32010   # integration not connected; data.auth_url has the OAuth URL


@router.post("/v1/mcp/{org_id}")
async def handle_mcp(
    org_id: str,
    request: Request,
    authorization: Optional[str] = Header(None),
):
    """
    Agent-facing JSON-RPC 2.0 endpoint.

    Security: the organization resolved from the Bearer token ALWAYS wins.
    The {org_id} URL parameter is ignored — it exists only for
    readability/routing and cannot be used to access another organization's tools.
    """
    # Token is the authority — URL param is never trusted
    effective = await verify_token(authorization)

    body = await request.json()

    if isinstance(body, list):
        return [await _handle_single(effective, item) for item in body]
    return await _handle_single(effective, body)


async def _handle_single(org_id: str, body: dict) -> dict:
    req_id = body.get("id")
    method = body.get("method", "")
    params = body.get("params", {})

    try:
        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "ninetrix-mcp-gateway", "version": "0.2.0"},
            }

        elif method == "tools/list":
            tools = registry.get_tools(org_id)
            result = {"tools": [t.model_dump() for t in tools]}

        elif method == "tools/call":
            tool_name = params.get("name", "")
            args = params.get("arguments", {})

            entry = registry.get_worker_for_tool(org_id, tool_name)
            if not entry:
                # Check if the missing tool belongs to a disconnected integration
                # Tool name format: "{integration_id}__{tool_name}"
                integration_id = tool_name.split("__")[0] if "__" in tool_name else None
                if integration_id:
                    auth_url = await saas_client.get_integration_auth_url(
                        org_id, integration_id
                    )
                    if auth_url is not None:
                        return _auth_required_error(req_id, integration_id, tool_name, auth_url)

                return _error(req_id, _TOOL_NOT_FOUND, f"Tool not found: {tool_name}")

            conn, server_name, local_tool_name = entry
            logger.debug(
                "Routing %s → worker=%s server=%s tool=%s org=%s",
                tool_name, conn.worker_id, server_name, local_tool_name, org_id,
            )
            result = await registry.send_call(conn, server_name, local_tool_name, args)

        elif method == "ping":
            result = {}

        else:
            return _error(req_id, _TOOL_NOT_FOUND, f"Method not found: {method}")

        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    except Exception as exc:
        logger.error("Error handling %s for org %s: %s", method, org_id, exc)
        return _error(req_id, _INTERNAL_ERROR, str(exc))


def _error(req_id: Any, code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": code, "message": message},
    }


def _auth_required_error(
    req_id: Any, integration_id: str, tool_name: str, auth_url: str
) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {
            "code": _AUTH_REQUIRED,
            "message": f"Integration '{integration_id}' requires authorization",
            "data": {
                "integration_id": integration_id,
                "tool_name": tool_name,
                "auth_url": auth_url,
                "action": "open_auth_url",
            },
        },
    }
