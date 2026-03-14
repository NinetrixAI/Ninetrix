"""Thread (run) endpoints."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from ninetrix_api import db
from ninetrix_api.models import AgentSummary, LogEntry, ThreadDetail, ThreadSummary, TimelineEvent

router = APIRouter()


def _extract_logs(history: list[dict], agent_id: str = "", ts: str = "") -> list[LogEntry]:
    """Convert agent message history into structured log entries."""
    logs: list[LogEntry] = []
    ts_val = ts or datetime.now(tz=timezone.utc).isoformat()

    for msg in history:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "user":
            text = content if isinstance(content, str) else _flatten_content(content)
            if text.strip():
                logs.append(LogEntry(ts=ts_val, level="info", message=f"[user] {text.strip()}", agent_id=agent_id))

        elif role == "assistant":
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text = block.get("text", "").strip()
                            if text:
                                logs.append(LogEntry(ts=ts_val, level="info", message=text, agent_id=agent_id))
                        elif block.get("type") == "tool_use":
                            name = block.get("name", "unknown")
                            logs.append(LogEntry(ts=ts_val, level="tool", message=f"⚡ {name}", agent_id=agent_id))
            elif isinstance(content, str) and content.strip():
                logs.append(LogEntry(ts=ts_val, level="info", message=content.strip(), agent_id=agent_id))

        elif role == "tool":
            # Tool results — treat as info
            text = content if isinstance(content, str) else _flatten_content(content)
            if text.strip():
                logs.append(LogEntry(ts=ts_val, level="info", message=f"[result] {text.strip()[:200]}", agent_id=agent_id))

        elif role == "thinking":
            text = content if isinstance(content, str) else _flatten_content(content)
            if text.strip():
                logs.append(LogEntry(ts=ts_val, level="info", message=f"[thinking] {text.strip()[:300]}", agent_id=agent_id))

    return logs


def _flatten_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif isinstance(item, str):
                parts.append(item)
        return " ".join(parts)
    return str(content)


_SORT_COLS = {
    "updated_at":  "updated_at",
    "step_index":  "step_index",
    "tokens_used": "tokens_used",
    "agent_id":    "agent_id",
    "status":      "status",
}

_VALID_STATUSES = {
    "in_progress", "waiting_for_approval", "completed",
    "error", "approved", "rejected",
}


@router.get("", response_model=list[ThreadSummary])
async def list_threads(
    sort:   str        = "updated_at",
    order:  str        = "desc",
    status: str | None = None,
):
    """Return the latest checkpoint per thread.

    Query params:
    - **sort**: `updated_at` (default) | `step_index` | `tokens_used` | `agent_id` | `status`
    - **order**: `desc` (default) | `asc`
    - **status**: filter by exact status value (e.g. `in_progress`, `waiting_for_approval`, `completed`, `error`)
    """
    col  = _SORT_COLS.get(sort, "updated_at")
    dir_ = "ASC" if order == "asc" else "DESC"

    # Validate status filter (ignore unknown values)
    status_filter = status if status in _VALID_STATUSES else None

    q = f"""
        SELECT * FROM (
            SELECT DISTINCT ON (thread_id)
                thread_id,
                agent_id,
                trace_id,
                status,
                step_index,
                timestamp                          AS updated_at,
                metadata->>'model'                 AS model
            FROM agentfile_checkpoints
            ORDER BY thread_id, step_index DESC
        ) latest
        LEFT JOIN LATERAL (
            SELECT
                array_agg(DISTINCT agent_id ORDER BY agent_id) AS agents,
                COALESCE((
                    SELECT SUM(agent_max)
                    FROM (
                        SELECT MAX((metadata->>'tokens_used')::bigint) AS agent_max
                        FROM agentfile_checkpoints c3
                        WHERE c3.thread_id = latest.thread_id
                        GROUP BY agent_id
                    ) per_agent
                ), 0) AS tokens_used,
                MIN(timestamp) AS started_at
            FROM agentfile_checkpoints c2
            WHERE c2.thread_id = latest.thread_id
        ) agg ON true
        WHERE ($1::text IS NULL OR status = $1)
        ORDER BY {col} {dir_}
    """
    rows = await db.pool().fetch(q, status_filter)
    result = []
    for r in rows:
        started_at = r["started_at"] if r["started_at"] else r["updated_at"]
        updated_at = r["updated_at"]
        duration_ms = (
            int((updated_at - started_at).total_seconds() * 1000)
            if started_at and updated_at and updated_at > started_at
            else None
        )
        result.append(ThreadSummary(
            thread_id=r["thread_id"],
            agent_id=r["agent_id"],
            agent_name=r["agent_id"],
            agents=list(r["agents"] or []),
            trace_id=r["trace_id"],
            status=r["status"],
            step_index=r["step_index"],
            started_at=started_at,
            updated_at=updated_at,
            duration_ms=duration_ms,
            tokens_used=r["tokens_used"] or 0,
            model=r["model"] or "",
            trigger="api",
        ))
    return result


@router.get("/{thread_id}", response_model=ThreadDetail)
async def get_thread(thread_id: str):
    """Return the latest checkpoint for a thread plus extracted logs.

    For multi-agent runs, tokens_used is summed across all agents and
    `agents` lists every agent that participated in this thread.
    Logs are merged from all agents and attributed with agent_id.
    """
    # Latest checkpoint overall (for status, step_index, model)
    q_latest = """
        SELECT
            thread_id, agent_id, trace_id, status,
            step_index, timestamp AS updated_at,
            checkpoint, metadata
        FROM agentfile_checkpoints
        WHERE thread_id = $1
        ORDER BY step_index DESC
        LIMIT 1
    """
    # Latest checkpoint per agent (for multi-agent log merging)
    q_per_agent = """
        SELECT DISTINCT ON (agent_id)
            agent_id,
            timestamp AS updated_at,
            checkpoint
        FROM agentfile_checkpoints
        WHERE thread_id = $1
        ORDER BY agent_id, step_index DESC
    """
    # Aggregates across all agents for this thread
    q_agg = """
        SELECT
            array_agg(DISTINCT agent_id ORDER BY agent_id) AS agents,
            COALESCE((
                SELECT SUM(agent_max)
                FROM (
                    SELECT MAX((metadata->>'tokens_used')::bigint) AS agent_max
                    FROM agentfile_checkpoints
                    WHERE thread_id = $1
                    GROUP BY agent_id
                ) per_agent
            ), 0) AS tokens_used
        FROM agentfile_checkpoints
        WHERE thread_id = $1
    """
    row = await db.pool().fetchrow(q_latest, thread_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    agg, per_agent_rows = await asyncio.gather(
        db.pool().fetchrow(q_agg, thread_id),
        db.pool().fetch(q_per_agent, thread_id),
    )

    snap = json.loads(row["checkpoint"]) if isinstance(row["checkpoint"], str) else dict(row["checkpoint"])
    meta = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else dict(row["metadata"])

    pending = snap.get("pending_tool_calls", [])

    # Merge history from all agents for the main history field (entry agent history)
    history = snap.get("history", [])

    # Build merged logs from all agents with attribution
    all_logs: list[LogEntry] = []
    for ar in per_agent_rows:
        a_snap = json.loads(ar["checkpoint"]) if isinstance(ar["checkpoint"], str) else dict(ar["checkpoint"])
        a_history = a_snap.get("history", [])
        ts_str = ar["updated_at"].isoformat() if ar["updated_at"] else ""
        all_logs.extend(_extract_logs(a_history, agent_id=ar["agent_id"], ts=ts_str))

    # If no per-agent rows (shouldn't happen), fall back
    if not all_logs:
        all_logs = _extract_logs(history, agent_id=row["agent_id"])

    return ThreadDetail(
        thread_id=row["thread_id"],
        agent_id=row["agent_id"],
        agents=list(agg["agents"] or []),
        trace_id=row["trace_id"],
        status=row["status"],
        step_index=row["step_index"],
        updated_at=row["updated_at"],
        tokens_used=agg["tokens_used"] or 0,
        model=meta.get("model", ""),
        history=history,
        pending_tool_calls=pending,
        logs=all_logs,
    )


@router.get("/{thread_id}/checkpoints")
async def get_checkpoints(thread_id: str):
    """Return all checkpoints for a thread in ascending step order."""
    q = """
        SELECT id, trace_id, agent_id, parent_trace_id,
               step_index, timestamp, status, metadata
        FROM agentfile_checkpoints
        WHERE thread_id = $1
        ORDER BY timestamp ASC, step_index ASC
    """
    rows = await db.pool().fetch(q, thread_id)
    if not rows:
        raise HTTPException(status_code=404, detail="Thread not found")
    return [
        {
            "id": r["id"],
            "trace_id": r["trace_id"],
            "agent_id": r["agent_id"],
            "parent_trace_id": r["parent_trace_id"],
            "step_index": r["step_index"],
            "timestamp": r["timestamp"].isoformat(),
            "status": r["status"],
            "metadata": dict(r["metadata"]) if r["metadata"] else {},
        }
        for r in rows
    ]


# ── Helper for timeline ────────────────────────────────────────────────────────

def _calc_duration_ms(current_ts: str, prev_ts: str) -> int | None:
    """Return milliseconds between two ISO timestamps, or None on failure."""
    if not current_ts or not prev_ts:
        return None
    try:
        dt_cur  = datetime.fromisoformat(current_ts.replace("Z", "+00:00"))
        dt_prev = datetime.fromisoformat(prev_ts.replace("Z", "+00:00"))
        ms = int((dt_cur - dt_prev).total_seconds() * 1000)
        return ms if ms >= 0 else None
    except (ValueError, AttributeError):
        return None


def _msg_to_events(msg: dict, row: object, meta: dict | None = None, prev_meta: dict | None = None, tool_id_name: dict | None = None) -> list[dict]:
    """Convert one history message into one or more TimelineEvent dicts.

    meta      — history_meta entry for *this* message (has ts, tokens_in, tokens_out)
    prev_meta — history_meta entry for the *previous* message; used to compute duration_ms
    """
    _meta = meta or {}
    _prev_meta = prev_meta or {}
    # Use per-message timestamp when available (set by runner); fall back to checkpoint ts
    ts = _meta.get("ts") or (row["timestamp"].isoformat() if row["timestamp"] else "")  # type: ignore[index]
    agent_id = row["agent_id"]  # type: ignore[index]
    trace_id = row["trace_id"]  # type: ignore[index]
    parent_trace_id = row["parent_trace_id"]  # type: ignore[index]

    # Token counts — populated for assistant (and thinking) messages
    tokens_in  = _meta.get("tokens_in")
    tokens_out = _meta.get("tokens_out")
    tokens_used = ((tokens_in or 0) + (tokens_out or 0)) or None

    # Duration: elapsed since previous message's timestamp
    # — assistant messages  → LLM latency (user → assistant)
    # — tool_result messages → tool execution time (tool_call → tool_result)
    prev_ts = _prev_meta.get("ts", "")
    duration_ms = _calc_duration_ms(ts, prev_ts) if prev_ts else None

    role = msg.get("role", "")
    content = msg.get("content", "")
    events: list[dict] = []

    if role == "user":
        text = content if isinstance(content, str) else _flatten_content(content)
        text = text.strip()
        if text:
            events.append(dict(
                ts=ts, agent_id=agent_id, trace_id=trace_id,
                parent_trace_id=parent_trace_id,
                type="user_message", role="user",
                content=text[:500],
            ))

    elif role == "assistant":
        if isinstance(content, list):
            # Anthropic native format: content is a list of typed blocks
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    text = block.get("text", "").strip()
                    if text:
                        events.append(dict(
                            ts=ts, agent_id=agent_id, trace_id=trace_id,
                            parent_trace_id=parent_trace_id,
                            type="assistant_message", role="assistant",
                            content=text[:500],
                            tokens_in=tokens_in,
                            tokens_out=tokens_out,
                            tokens_used=tokens_used,
                            duration_ms=duration_ms,
                        ))
                elif block.get("type") == "tool_use":
                    tool_name = block.get("name", "unknown")
                    args = block.get("input", {})
                    target_agent = None
                    if tool_name == "transfer_to_agent":
                        target_agent = args.get("agent", None)
                    snippet = json.dumps(args)[:300] if args else ""
                    events.append(dict(
                        ts=ts, agent_id=agent_id, trace_id=trace_id,
                        parent_trace_id=parent_trace_id,
                        type="tool_call", role="assistant",
                        content=snippet,
                        tool_name=tool_name,
                        target_agent=target_agent,
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                        tokens_used=tokens_used,
                        duration_ms=duration_ms,
                    ))
        elif isinstance(content, str) and content.strip():
            # OpenAI/litellm format: content is a plain string
            events.append(dict(
                ts=ts, agent_id=agent_id, trace_id=trace_id,
                parent_trace_id=parent_trace_id,
                type="assistant_message", role="assistant",
                content=content.strip()[:500],
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                tokens_used=tokens_used,
                duration_ms=duration_ms,
            ))

        # OpenAI/litellm format: tool calls are a separate top-level field, not inside content
        for tc in (msg.get("tool_calls") or []):
            fn = tc.get("function", {})
            tool_name = fn.get("name", "unknown")
            args_str = fn.get("arguments", "{}")
            try:
                args = json.loads(args_str) if isinstance(args_str, str) else (args_str or {})
            except (json.JSONDecodeError, TypeError):
                args = {}
            target_agent = args.get("agent") if tool_name == "transfer_to_agent" else None
            snippet = json.dumps(args)[:300] if args else ""
            events.append(dict(
                ts=ts, agent_id=agent_id, trace_id=trace_id,
                parent_trace_id=parent_trace_id,
                type="tool_call", role="assistant",
                content=snippet,
                tool_name=tool_name,
                target_agent=target_agent,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                tokens_used=tokens_used,
                duration_ms=duration_ms,
            ))

    elif role == "tool":
        text = content if isinstance(content, str) else _flatten_content(content)
        text = text.strip()
        if text:
            # Resolve tool_name via tool_call_id (OpenAI format)
            tc_id = msg.get("tool_call_id", "")
            resolved_tool_name = (tool_id_name or {}).get(tc_id) if tc_id else None
            events.append(dict(
                ts=ts, agent_id=agent_id, trace_id=trace_id,
                parent_trace_id=parent_trace_id,
                type="tool_result", role="tool",
                content=text[:500],
                tool_name=resolved_tool_name,
                duration_ms=duration_ms,
            ))

    elif role == "thinking":
        text = content if isinstance(content, str) else _flatten_content(content)
        text = text.strip()
        if text:
            events.append(dict(
                ts=ts, agent_id=agent_id, trace_id=trace_id,
                parent_trace_id=parent_trace_id,
                type="thinking", role="thinking",
                content=text[:10000],
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                tokens_used=tokens_used,
                duration_ms=duration_ms,
            ))

    return events


@router.get("/{thread_id}/timeline", response_model=list[TimelineEvent])
async def get_timeline(thread_id: str):
    """Unified chronological event stream across all agents in a thread.

    Builds events by diffing consecutive checkpoints per agent to detect
    new history messages, then returns them sorted by checkpoint timestamp.
    """
    q = """
        SELECT agent_id, trace_id, parent_trace_id, step_index,
               timestamp, checkpoint
        FROM agentfile_checkpoints
        WHERE thread_id = $1
        ORDER BY timestamp ASC, step_index ASC
    """
    rows = await db.pool().fetch(q, thread_id)
    if not rows:
        raise HTTPException(status_code=404, detail="Thread not found")

    agent_prev_len:  dict[str, int]  = {}
    agent_prev_meta: dict[str, dict] = {}  # last seen history_meta entry per agent
    events: list[dict] = []

    for row in rows:
        snap = json.loads(row["checkpoint"]) if isinstance(row["checkpoint"], str) else dict(row["checkpoint"])
        history = snap.get("history", [])
        raw_meta = snap.get("history_meta") or []
        # Pad meta to match history length for old checkpoints that don't have it
        history_meta = raw_meta + [{}] * max(0, len(history) - len(raw_meta))
        prev = agent_prev_len.get(row["agent_id"], 0)
        new_msgs = history[prev:]
        new_meta = history_meta[prev:]
        agent_prev_len[row["agent_id"]] = len(history)

        # Build tool_call_id → tool_name map from full history so tool_result
        # events can carry the tool name (OpenAI format stores it only on the call).
        tool_id_name: dict[str, str] = {}
        for m in history:
            if m.get("role") == "assistant":
                for tc in (m.get("tool_calls") or []):
                    tc_id = tc.get("id", "")
                    fn_name = (tc.get("function") or {}).get("name", "")
                    if tc_id and fn_name:
                        tool_id_name[tc_id] = fn_name

        prev_meta = agent_prev_meta.get(row["agent_id"], {})
        for msg, meta in zip(new_msgs, new_meta):
            events.extend(_msg_to_events(msg, row, meta, prev_meta, tool_id_name))
            if meta.get("ts"):   # only advance prev when we have a real timestamp
                prev_meta = meta
        agent_prev_meta[row["agent_id"]] = prev_meta

    return events


@router.get("/{thread_id}/agents", response_model=list[AgentSummary])
async def get_agents(thread_id: str):
    """Per-agent breakdown for a thread."""
    q = """
        SELECT DISTINCT ON (agent_id)
            agent_id,
            trace_id,
            parent_trace_id,
            status,
            step_index,
            metadata
        FROM agentfile_checkpoints
        WHERE thread_id = $1
        ORDER BY agent_id, step_index DESC
    """
    q_tokens = """
        SELECT agent_id, COALESCE(SUM((metadata->>'tokens_used')::bigint), 0) AS tokens_used
        FROM agentfile_checkpoints
        WHERE thread_id = $1
        GROUP BY agent_id
    """
    rows, token_rows = await asyncio.gather(
        db.pool().fetch(q, thread_id),
        db.pool().fetch(q_tokens, thread_id),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Thread not found")

    token_map = {r["agent_id"]: int(r["tokens_used"]) for r in token_rows}

    summaries = []
    for r in rows:
        meta = json.loads(r["metadata"]) if isinstance(r["metadata"], str) else dict(r["metadata"] or {})
        summaries.append(AgentSummary(
            agent_id=r["agent_id"],
            trace_id=r["trace_id"],
            parent_trace_id=r["parent_trace_id"],
            status=r["status"],
            steps=r["step_index"],
            tokens_used=token_map.get(r["agent_id"], 0),
            model=meta.get("model", ""),
        ))
    return summaries
