from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)


class MCPServer:
    """
    Wraps a single MCP server process.

    Starts the server as a stdio subprocess, initialises the MCP session,
    collects the tool list (prefixed as "{name}__{tool}"), and provides
    an async call_tool() method.
    """

    def __init__(self, name: str, command: str, args: list[str], env: Optional[dict] = None):
        self.name = name
        self.command = command
        self.args = args
        self.env = env or {}
        self.session: Optional[ClientSession] = None
        self._stdio_cm = None
        self._session_cm = None
        self._tools: list[dict] = []

    async def start(self):
        params = StdioServerParameters(
            command=self.command,
            args=self.args,
            env=self.env or None,
        )

        self._stdio_cm = stdio_client(params)
        try:
            read, write = await self._stdio_cm.__aenter__()
        except Exception:
            self._stdio_cm = None
            raise

        try:
            self._session_cm = ClientSession(read, write)
            self.session = await self._session_cm.__aenter__()
            await self.session.initialize()

            resp = await self.session.list_tools()
            for t in resp.tools:
                schema: dict = {}
                if hasattr(t, "inputSchema"):
                    if hasattr(t.inputSchema, "model_dump"):
                        schema = t.inputSchema.model_dump()
                    elif isinstance(t.inputSchema, dict):
                        schema = t.inputSchema

                self._tools.append(
                    {
                        "name": f"{self.name}__{t.name}",
                        "description": t.description or "",
                        "inputSchema": schema,
                    }
                )

            logger.info("Started MCP server %r — %d tool(s)", self.name, len(self._tools))
        except Exception:
            await self.stop()
            raise

    async def stop(self):
        for cm_attr in ("_session_cm", "_stdio_cm"):
            cm = getattr(self, cm_attr, None)
            if cm is not None:
                try:
                    await cm.__aexit__(None, None, None)
                except Exception as exc:
                    logger.debug("Error stopping %s on %s: %s", cm_attr, self.name, exc)

    async def call_tool(self, tool_name: str, args: dict) -> dict[str, Any]:
        if self.session is None:
            raise RuntimeError(f"Server {self.name!r} is not started")

        _timeout = float(os.environ.get("MCP_TOOL_TIMEOUT", "30"))
        result = await asyncio.wait_for(
            self.session.call_tool(tool_name, args),
            timeout=_timeout,
        )

        content = []
        for item in result.content:
            if hasattr(item, "text"):
                content.append({"type": "text", "text": item.text})
            elif hasattr(item, "model_dump"):
                content.append(item.model_dump())
            else:
                content.append({"type": "text", "text": str(item)})

        return {"content": content, "isError": getattr(result, "isError", False)}

    @property
    def tools(self) -> list[dict]:
        return self._tools
