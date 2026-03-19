from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable, Optional

import websockets
import websockets.exceptions

logger = logging.getLogger(__name__)

# Type alias for the tool-call handler provided by main.py
ToolCallHandler = Callable[[str, str, str, dict], Awaitable[dict]]


class GatewayClient:
    """
    Maintains a persistent WebSocket connection to the MCP Gateway.

    On connect → sends worker.register with the full tool manifest.
    Receives tool.call messages → delegates to on_tool_call → sends tool.result.
    Reconnects with exponential back-off on disconnect.
    """

    PING_INTERVAL = 30  # seconds between keepalive pings
    RECONNECT_DELAY = 5  # seconds before reconnect attempt

    def __init__(
        self,
        gateway_url: str,
        worker_id: str,
        org_id: str,
        worker_name: str,
        token: str,
        on_tool_call: ToolCallHandler,
    ):
        self.gateway_url = gateway_url.rstrip("/")
        self.worker_id = worker_id
        self.org_id = org_id
        self.worker_name = worker_name
        self.token = token
        self.on_tool_call = on_tool_call

        self._tools: list[dict] = []
        self._servers: list[str] = []
        self._ws: Optional[websockets.WebSocketClientProtocol] = None

    def set_tools(self, tools: list[dict], servers: list[str]):
        self._tools = tools
        self._servers = servers

    def _build_url(self) -> str:
        return (
            f"{self.gateway_url}/ws/workers/{self.worker_id}"
            f"?token={self.token}"
            f"&org_id={self.org_id}"
            f"&worker_name={self.worker_name}"
        )

    async def connect(self):
        """Connect (and reconnect) to the gateway indefinitely."""
        url = self._build_url()
        delay = self.RECONNECT_DELAY

        while True:
            try:
                async with websockets.connect(url, ping_interval=None) as ws:
                    self._ws = ws
                    delay = self.RECONNECT_DELAY  # reset back-off on success
                    logger.info("Connected to gateway at %s", self.gateway_url)

                    # Register immediately
                    await ws.send(
                        json.dumps(
                            {
                                "type": "worker.register",
                                "tools": self._tools,
                                "servers": self._servers,
                            }
                        )
                    )

                    async for raw in ws:
                        await self._handle_message(ws, json.loads(raw))

            except (
                websockets.exceptions.ConnectionClosedError,
                websockets.exceptions.ConnectionClosedOK,
                OSError,
            ) as exc:
                logger.warning("Gateway connection lost (%s). Reconnecting in %ds…", exc, delay)
            except Exception as exc:
                logger.error("Unexpected gateway error: %s. Reconnecting in %ds…", exc, delay)
            finally:
                self._ws = None

            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)  # exponential back-off, cap at 60s

    async def ping_loop(self):
        """Send periodic application-level pings to keep the connection alive."""
        while True:
            await asyncio.sleep(self.PING_INTERVAL)
            ws = self._ws
            if ws:
                try:
                    await ws.send(json.dumps({"type": "ping"}))
                except Exception:
                    pass

    async def _handle_message(self, ws, msg: dict):
        msg_type = msg.get("type")

        if msg_type == "tool.call":
            call_id = msg["call_id"]
            server = msg["server"]
            tool = msg["tool"]
            args = msg.get("args", {})

            try:
                result = await self.on_tool_call(call_id, server, tool, args)
                await ws.send(
                    json.dumps({"type": "tool.result", "call_id": call_id, "result": result})
                )
            except Exception as exc:
                logger.error("Tool call error %s/%s: %s", server, tool, exc)
                await ws.send(
                    json.dumps(
                        {"type": "tool.result", "call_id": call_id, "error": str(exc)}
                    )
                )

        elif msg_type == "worker.registered":
            logger.info("Gateway confirmed registration: %d tool(s)", msg.get("tool_count", 0))

        elif msg_type == "pong":
            pass

        else:
            logger.debug("Unhandled gateway message: %s", msg_type)
