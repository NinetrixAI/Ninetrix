from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from fastapi import WebSocket

from models import ToolSchema, WorkerStatus


@dataclass
class WorkerConnection:
    worker_id: str
    worker_name: str
    org_id: str
    websocket: WebSocket
    tools: list[ToolSchema] = field(default_factory=list)
    servers: list[str] = field(default_factory=list)
    # prefixed_name → (server_name, local_tool_name)
    tool_map: dict[str, tuple[str, str]] = field(default_factory=dict)
    connected_at: datetime = field(default_factory=datetime.utcnow)
    last_ping: datetime = field(default_factory=datetime.utcnow)


class WorkerRegistry:
    def __init__(self):
        # worker_id → WorkerConnection
        self._workers: dict[str, WorkerConnection] = {}
        # call_id → asyncio.Future
        self._pending: dict[str, asyncio.Future] = {}

    def connect(
        self,
        worker_id: str,
        worker_name: str,
        org_id: str,
        ws: WebSocket,
    ) -> WorkerConnection:
        conn = WorkerConnection(
            worker_id=worker_id,
            worker_name=worker_name,
            org_id=org_id,
            websocket=ws,
        )
        self._workers[worker_id] = conn
        return conn

    def disconnect(self, worker_id: str):
        self._workers.pop(worker_id, None)
        # Fail any in-flight calls that were waiting on this worker
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError(f"Worker {worker_id} disconnected"))

    def register_tools(self, worker_id: str, tools: list[ToolSchema], servers: list[str]):
        conn = self._workers.get(worker_id)
        if not conn:
            return
        conn.tools = tools
        conn.servers = servers
        # Build lookup: prefixed_name → (server_name, local_name)
        # Tool naming convention: "{server}__{tool}"
        conn.tool_map = {}
        for tool in tools:
            if "__" in tool.name:
                server_part, local_part = tool.name.split("__", 1)
                conn.tool_map[tool.name] = (server_part, local_part)
            else:
                conn.tool_map[tool.name] = ("", tool.name)

    def get_tools(self, org_id: str) -> list[ToolSchema]:
        seen: set[str] = set()
        tools: list[ToolSchema] = []
        for conn in self._workers.values():
            if conn.org_id == org_id:
                for tool in conn.tools:
                    if tool.name not in seen:
                        tools.append(tool)
                        seen.add(tool.name)
        return tools

    def get_worker_for_tool(
        self, org_id: str, tool_name: str
    ) -> Optional[tuple[WorkerConnection, str, str]]:
        """Return (connection, server_name, local_tool_name) or None."""
        for conn in self._workers.values():
            if conn.org_id == org_id and tool_name in conn.tool_map:
                server_name, local_name = conn.tool_map[tool_name]
                return conn, server_name, local_name
        return None

    def list_workers(self, org_id: Optional[str] = None) -> list[WorkerStatus]:
        result = []
        for conn in self._workers.values():
            if org_id and conn.org_id != org_id:
                continue
            result.append(
                WorkerStatus(
                    worker_id=conn.worker_id,
                    worker_name=conn.worker_name,
                    org_id=conn.org_id,
                    tool_count=len(conn.tools),
                    servers=conn.servers,
                    connected_at=conn.connected_at,
                    last_ping=conn.last_ping,
                )
            )
        return result

    async def send_call(
        self, conn: WorkerConnection, server: str, tool: str, args: dict
    ) -> Any:
        call_id = str(uuid.uuid4())
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[call_id] = future

        try:
            await conn.websocket.send_text(
                json.dumps(
                    {
                        "type": "tool.call",
                        "call_id": call_id,
                        "server": server,
                        "tool": tool,
                        "args": args,
                    }
                )
            )
            return await asyncio.wait_for(asyncio.shield(future), timeout=60.0)
        finally:
            self._pending.pop(call_id, None)

    def resolve_result(self, call_id: str, result: Any = None, error: Optional[str] = None):
        future = self._pending.get(call_id)
        if not future or future.done():
            return
        if error:
            future.set_exception(RuntimeError(error))
        else:
            future.set_result(result)

    def ping(self, worker_id: str):
        conn = self._workers.get(worker_id)
        if conn:
            conn.last_ping = datetime.utcnow()


# Singleton shared across all routers
registry = WorkerRegistry()
