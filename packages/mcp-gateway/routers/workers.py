from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from core.auth import verify_worker_token
from core.registry import registry
from models import ToolSchema

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws/workers/{worker_id}")
async def worker_websocket(
    websocket: WebSocket,
    worker_id: str,
    token: Optional[str] = Query(None),
    org_id: str = Query(default="default"),   # kept for logging only — token wins
    worker_name: Optional[str] = Query(default=None),
):
    """
    Long-lived WebSocket connection from an mcp-worker instance.

    Query params:
      token        — worker token (validated against saas-api in prod, env-secret in dev)
      org_id       — hint only; the verified organization from the token is always used
      worker_name  — human-readable label
    """
    # Verify token — the resolved org overrides whatever the worker claims
    verified_org = await verify_worker_token(token)
    if not verified_org:
        await websocket.close(code=4003, reason="Unauthorized")
        return

    await websocket.accept()

    name = worker_name or worker_id
    conn = registry.connect(worker_id, name, verified_org, websocket)
    logger.info(
        "Worker connected: id=%s name=%s org=%s (claimed=%s)",
        worker_id, name, verified_org, org_id,
    )

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type")

            if msg_type == "worker.register":
                tools = [ToolSchema(**t) for t in msg.get("tools", [])]
                servers = msg.get("servers", [])
                registry.register_tools(worker_id, tools, servers)
                logger.info(
                    "Worker %s registered %d tools from servers: %s",
                    worker_id, len(tools), servers,
                )
                await websocket.send_text(
                    json.dumps({"type": "worker.registered", "tool_count": len(tools)})
                )

            elif msg_type == "tool.result":
                registry.resolve_result(
                    call_id=msg.get("call_id"),
                    result=msg.get("result"),
                    error=msg.get("error"),
                )

            elif msg_type == "ping":
                registry.ping(worker_id)
                await websocket.send_text(json.dumps({"type": "pong"}))

            else:
                logger.debug("Unknown message from worker %s: %s", worker_id, msg_type)

    except WebSocketDisconnect:
        logger.info("Worker disconnected: %s", worker_id)
    except Exception as exc:
        logger.error("Worker %s error: %s", worker_id, exc)
    finally:
        registry.disconnect(worker_id)
        logger.info("Worker removed from registry: %s", worker_id)
