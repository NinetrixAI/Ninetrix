"""Pydantic response models for the Agentfile API."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


# ── Threads ───────────────────────────────────────────────────────────────────

class LogEntry(BaseModel):
    ts: str           # ISO timestamp string
    level: Literal["info", "tool", "error", "warn"]
    message: str
    agent_id: str = ""


class TimelineEvent(BaseModel):
    ts: str                         # ISO timestamp from checkpoint row
    agent_id: str
    trace_id: str
    parent_trace_id: str | None
    type: str   # "user_message" | "assistant_message" | "tool_call" | "tool_result"
    role: str   # "user" | "assistant" | "tool"
    content: str                    # text snippet (truncated to 500 chars)
    tool_name: str | None = None
    target_agent: str | None = None  # populated for transfer_to_agent calls
    tokens_used: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    duration_ms: int | None = None


class AgentSummary(BaseModel):
    agent_id: str
    trace_id: str
    parent_trace_id: str | None
    status: str
    steps: int
    tokens_used: int
    model: str


class ThreadSummary(BaseModel):
    thread_id: str
    agent_id: str        # last agent to write a checkpoint
    agent_name: str = "" # display name (same as agent_id until separate name stored)
    agents: list[str]    # all agents that participated
    trace_id: str
    status: str
    step_index: int
    started_at: datetime          # timestamp of first checkpoint for this thread
    updated_at: datetime
    duration_ms: int | None = None  # wall-clock ms from first to last checkpoint
    tokens_used: int     # total across all agents
    model: str
    trigger: str = "api"  # trigger type (not yet stored in local checkpoints)


class ThreadDetail(BaseModel):
    thread_id: str
    agent_id: str        # last agent to write a checkpoint
    agents: list[str]    # all agents that participated
    trace_id: str
    status: str
    step_index: int
    updated_at: datetime
    tokens_used: int     # total across all agents
    model: str
    history: list[dict[str, Any]]
    pending_tool_calls: list[dict[str, Any]]
    logs: list[LogEntry]


# ── Approvals ─────────────────────────────────────────────────────────────────

class ApprovalItem(BaseModel):
    trace_id: str
    thread_id: str
    agent_id: str
    step_index: int
    pending_tool_calls: list[dict[str, Any]]
    created_at: datetime


# ── Integrations ───────────────────────────────────────────────────────────────

class IntegrationTool(BaseModel):
    name: str
    description: str
    permissions: list[str]  # ["read", "write", ...]


class IntegrationCatalogItem(BaseModel):
    id: str
    name: str
    description: str
    auth_type: str          # "oauth2" | "apikey"
    icon: str
    tools: list[IntegrationTool]
    connected: bool
    status: str             # "connected" | "pending" | "disconnected"
    account_label: str | None = None


class ApiKeyPayload(BaseModel):
    key: str


# ── Agents ─────────────────────────────────────────────────────────────────────

class AgentStats(BaseModel):
    agent_id: str
    total_runs: int
    completed_runs: int
    error_runs: int
    running_runs: int
    total_tokens: int
    models: list[str]
    last_seen: datetime
    last_status: str


# ── Workspace tokens ───────────────────────────────────────────────────────────

class WorkspaceToken(BaseModel):
    id: str
    label: str
    created_at: datetime
    last_used_at: datetime | None = None


class CreateTokenPayload(BaseModel):
    label: str
