from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class ToolSchema(BaseModel):
    name: str
    description: str = ""
    inputSchema: dict[str, Any] = {}


class WorkerStatus(BaseModel):
    worker_id: str
    worker_name: str
    org_id: str
    tool_count: int
    servers: list[str]
    connected_at: datetime
    last_ping: datetime


class MCPRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: Any = None
    method: str
    params: dict[str, Any] = {}


class MCPResponse(BaseModel):
    jsonrpc: str = "2.0"
    id: Any = None
    result: Optional[Any] = None
    error: Optional[dict] = None
