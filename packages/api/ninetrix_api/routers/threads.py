"""Thread (run) endpoints."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ninetrix_api import db
from ninetrix_api.models import AgentSummary, AnalyticsSummary, CreateScorePayload, DailyStats, LogEntry, Page, RunScore, SessionSummary, ThreadDetail, ThreadSummary, TimelineEvent

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
    "started_at":  "started_at",
    "step_index":  "step_index",
    "tokens_used": "tokens_used",
    "agent_id":    "agent_id",
    "status":      "status",
}

_VALID_STATUSES = {
    "in_progress", "waiting_for_approval", "completed",
    "error", "approved", "rejected", "budget_exceeded",
}


@router.get("", response_model=Page[ThreadSummary])
async def list_threads(
    sort:     str        = "updated_at",
    order:    str        = "desc",
    status:   str | None = None,
    search:   str | None = None,
    agent_id: str | None = None,
    model:    str | None = None,
    limit:    int        = 50,
    offset:   int        = 0,
):
    """Return a paginated page of latest checkpoints per thread.

    Query params:
    - **sort**: `updated_at` (default) | `started_at` | `step_index` | `tokens_used` | `agent_id` | `status`
    - **order**: `desc` (default) | `asc`
    - **status**: filter by exact status value (e.g. `in_progress`, `completed`, `error`)
    - **search**: case-insensitive substring match across thread_id, agent_id, and model
    - **agent_id**: filter by exact agent_id
    - **model**: filter by exact model name
    - **limit**: page size, 1–200 (default 50)
    - **offset**: row offset (default 0)
    """
    col  = _SORT_COLS.get(sort, "updated_at")
    dir_ = "ASC" if order == "asc" else "DESC"
    limit  = max(1, min(limit, 200))
    offset = max(0, offset)

    status_filter = status if status in _VALID_STATUSES else None
    search_pattern = f"%{search}%" if search and search.strip() else None
    agent_filter = agent_id if agent_id and agent_id.strip() else None
    model_filter = model if model and model.strip() else None

    q = f"""
        SELECT *, COUNT(*) OVER() AS total_count
        FROM (
            SELECT DISTINCT ON (thread_id)
                thread_id,
                agent_id,
                trace_id,
                status,
                step_index,
                timestamp                          AS updated_at,
                metadata->>'model'                 AS model,
                (metadata->>'run_cost_usd')::float AS run_cost_usd,
                (metadata->>'budget_usd')::float   AS budget_usd,
                (metadata->>'budget_soft_warned')::boolean AS budget_soft_warned,
                (metadata->>'rate_limited')::boolean       AS rate_limited,
                COALESCE((metadata->>'rate_limit_waits')::int, 0) AS rate_limit_waits
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
          AND ($4::text IS NULL OR (thread_id ILIKE $4 OR agent_id ILIKE $4 OR model ILIKE $4))
          AND ($5::text IS NULL OR agent_id = $5)
          AND ($6::text IS NULL OR model = $6)
        ORDER BY {col} {dir_}
        LIMIT $2 OFFSET $3
    """
    rows = await db.pool().fetch(q, status_filter, limit, offset, search_pattern, agent_filter, model_filter)
    total = int(rows[0]["total_count"]) if rows else 0
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
            run_cost_usd=float(r["run_cost_usd"] or 0),
            budget_usd=float(r["budget_usd"] or 0),
            budget_soft_warned=bool(r["budget_soft_warned"]),
            rate_limited=bool(r["rate_limited"]),
            rate_limit_waits=int(r["rate_limit_waits"] or 0),
        ))
    return Page(items=result, total=total, limit=limit, offset=offset)


@router.get("/analytics", response_model=AnalyticsSummary)
async def get_analytics(days: int = 30):
    """Aggregated analytics: daily stats, top agents, top models.

    Query params:
    - **days**: number of days to look back (default 30, max 90)
    """
    days = max(1, min(days, 90))

    # Daily stats — one row per day with run counts, tokens, cost, durations
    daily_q = """
        WITH latest_per_thread AS (
            SELECT DISTINCT ON (thread_id)
                thread_id, status, timestamp AS updated_at,
                COALESCE((metadata->>'tokens_used')::bigint, 0) AS tokens_used,
                COALESCE((metadata->>'run_cost_usd')::float, 0) AS run_cost_usd
            FROM agentfile_checkpoints
            WHERE timestamp >= NOW() - make_interval(days => $1)
            ORDER BY thread_id, step_index DESC
        ),
        with_started AS (
            SELECT l.*,
                   (SELECT MIN(timestamp) FROM agentfile_checkpoints c2 WHERE c2.thread_id = l.thread_id) AS started_at
            FROM latest_per_thread l
        )
        SELECT
            (started_at AT TIME ZONE 'UTC')::date AS day,
            COUNT(*)::int AS runs,
            COUNT(*) FILTER (WHERE status = 'completed')::int AS completed,
            COUNT(*) FILTER (WHERE status = 'error')::int AS errors,
            COALESCE(SUM(tokens_used), 0)::bigint AS tokens,
            COALESCE(SUM(run_cost_usd), 0)::float AS cost_usd,
            AVG(EXTRACT(EPOCH FROM (updated_at - started_at)) * 1000)
                FILTER (WHERE updated_at > started_at)::float AS avg_duration_ms,
            PERCENTILE_CONT(0.95) WITHIN GROUP (
                ORDER BY EXTRACT(EPOCH FROM (updated_at - started_at)) * 1000
            ) FILTER (WHERE updated_at > started_at)::float AS p95_duration_ms
        FROM with_started
        GROUP BY day
        ORDER BY day ASC
    """
    daily_rows = await db.pool().fetch(daily_q, days)

    daily = [
        DailyStats(
            date=str(r["day"]),
            runs=r["runs"],
            completed=r["completed"],
            errors=r["errors"],
            tokens=int(r["tokens"]),
            cost_usd=float(r["cost_usd"]),
            avg_duration_ms=float(r["avg_duration_ms"]) if r["avg_duration_ms"] else None,
            p95_duration_ms=float(r["p95_duration_ms"]) if r["p95_duration_ms"] else None,
        )
        for r in daily_rows
    ]

    total_runs = sum(d.runs for d in daily)
    total_tokens = sum(d.tokens for d in daily)
    total_cost = sum(d.cost_usd for d in daily)
    total_errors = sum(d.errors for d in daily)
    error_rate = total_errors / total_runs if total_runs > 0 else 0.0
    all_durations = [d.avg_duration_ms for d in daily if d.avg_duration_ms]
    avg_duration = sum(all_durations) / len(all_durations) if all_durations else None

    # Top agents
    agent_q = """
        WITH latest AS (
            SELECT DISTINCT ON (thread_id)
                thread_id, agent_id,
                COALESCE((metadata->>'tokens_used')::bigint, 0) AS tokens_used,
                COALESCE((metadata->>'run_cost_usd')::float, 0) AS run_cost_usd
            FROM agentfile_checkpoints
            WHERE timestamp >= NOW() - make_interval(days => $1)
            ORDER BY thread_id, step_index DESC
        )
        SELECT agent_id,
               COUNT(*)::int AS runs,
               COALESCE(SUM(tokens_used), 0)::bigint AS tokens,
               COALESCE(SUM(run_cost_usd), 0)::float AS cost_usd
        FROM latest
        GROUP BY agent_id
        ORDER BY runs DESC
        LIMIT 10
    """
    agent_rows = await db.pool().fetch(agent_q, days)
    top_agents = [
        {"agent_id": r["agent_id"], "runs": r["runs"], "tokens": int(r["tokens"]), "cost_usd": float(r["cost_usd"])}
        for r in agent_rows
    ]

    # Top models
    model_q = """
        WITH latest AS (
            SELECT DISTINCT ON (thread_id)
                thread_id,
                metadata->>'model' AS model,
                COALESCE((metadata->>'tokens_used')::bigint, 0) AS tokens_used,
                COALESCE((metadata->>'run_cost_usd')::float, 0) AS run_cost_usd
            FROM agentfile_checkpoints
            WHERE timestamp >= NOW() - make_interval(days => $1)
            ORDER BY thread_id, step_index DESC
        )
        SELECT COALESCE(model, 'unknown') AS model,
               COUNT(*)::int AS runs,
               COALESCE(SUM(tokens_used), 0)::bigint AS tokens,
               COALESCE(SUM(run_cost_usd), 0)::float AS cost_usd
        FROM latest
        GROUP BY model
        ORDER BY runs DESC
        LIMIT 10
    """
    model_rows = await db.pool().fetch(model_q, days)
    top_models = [
        {"model": r["model"], "runs": r["runs"], "tokens": int(r["tokens"]), "cost_usd": float(r["cost_usd"])}
        for r in model_rows
    ]

    return AnalyticsSummary(
        days=daily,
        total_runs=total_runs,
        total_tokens=total_tokens,
        total_cost_usd=total_cost,
        avg_duration_ms=avg_duration,
        error_rate=error_rate,
        top_agents=top_agents,
        top_models=top_models,
    )


@router.get("/sessions", response_model=list[SessionSummary])
async def list_sessions(limit: int = 50, offset: int = 0):
    """Return conversations grouped by channel chat or multi-agent parent.

    Groups runs into sessions using:
    1. channel_sessions.external_chat_id — multi-message channel conversations
    2. parent_trace_id — multi-agent orchestration chains
    Remaining runs appear as standalone sessions.
    """
    limit = max(1, min(limit, 100))
    offset = max(0, offset)

    # 1. Channel-based sessions (from channel_sessions table)
    channel_q = """
        SELECT
            cs.external_chat_id AS session_id,
            ch.channel_type,
            ch.name AS channel_name,
            array_agg(DISTINCT cs.thread_id) AS thread_ids,
            array_agg(DISTINCT cs.agent_name) AS agent_ids,
            COUNT(DISTINCT cs.thread_id)::int AS total_runs,
            MAX(cs.last_message_at) AS last_active,
            MIN(cs.created_at) AS first_active
        FROM channel_sessions cs
        JOIN channels ch ON ch.id = cs.channel_id
        GROUP BY cs.external_chat_id, ch.channel_type, ch.name
        ORDER BY last_active DESC
        LIMIT $1 OFFSET $2
    """
    ch_rows = await db.pool().fetch(channel_q, limit, offset)

    sessions: list[SessionSummary] = []
    seen_threads: set[str] = set()

    for r in ch_rows:
        tids = list(r["thread_ids"] or [])
        seen_threads.update(tids)
        # Fetch aggregated token/cost for these threads
        stats = await _session_thread_stats(tids)
        ch_type = r["channel_type"] or "channel"
        ch_name = r["channel_name"] or ch_type
        sessions.append(SessionSummary(
            session_id=r["session_id"],
            session_type="channel",
            label=f"{ch_name} #{r['session_id'][-6:]}",
            thread_ids=tids,
            agent_ids=list(r["agent_ids"] or []),
            total_runs=r["total_runs"],
            total_tokens=stats["tokens"],
            total_cost_usd=stats["cost"],
            last_active=r["last_active"],
            first_active=r["first_active"],
        ))

    # 2a. Multi-agent sessions — threads that share a parent_trace_id (cross-thread handoffs)
    parent_q = """
        SELECT
            parent_trace_id AS session_id,
            array_agg(DISTINCT thread_id) AS thread_ids,
            array_agg(DISTINCT agent_id) AS agent_ids,
            COUNT(DISTINCT thread_id)::int AS total_runs,
            MAX(timestamp) AS last_active,
            MIN(timestamp) AS first_active
        FROM agentfile_checkpoints
        WHERE parent_trace_id IS NOT NULL
          AND parent_trace_id != ''
        GROUP BY parent_trace_id
        HAVING COUNT(DISTINCT thread_id) > 1
        ORDER BY last_active DESC
        LIMIT $1
    """
    parent_rows = await db.pool().fetch(parent_q, limit)

    for r in parent_rows:
        tids = [t for t in (r["thread_ids"] or []) if t not in seen_threads]
        if not tids:
            continue
        seen_threads.update(tids)
        stats = await _session_thread_stats(tids)
        agents = list(r["agent_ids"] or [])
        sessions.append(SessionSummary(
            session_id=r["session_id"],
            session_type="multi_agent",
            label=f"{agents[0] if agents else 'agent'} +{len(agents) - 1}" if len(agents) > 1 else (agents[0] if agents else "agents"),
            thread_ids=tids,
            agent_ids=agents,
            total_runs=len(tids),
            total_tokens=stats["tokens"],
            total_cost_usd=stats["cost"],
            last_active=r["last_active"],
            first_active=r["first_active"],
        ))

    # 2b. Multi-agent sessions — single threads with multiple agents (in-thread handoffs)
    multi_agent_q = """
        SELECT
            thread_id,
            array_agg(DISTINCT agent_id ORDER BY agent_id) AS agent_ids,
            COALESCE(MAX((metadata->>'tokens_used')::bigint), 0) AS tokens_used,
            COALESCE(MAX((metadata->>'run_cost_usd')::float), 0) AS run_cost_usd,
            MAX(timestamp) AS last_active,
            MIN(timestamp) AS first_active
        FROM agentfile_checkpoints
        WHERE thread_id NOT IN (SELECT unnest($2::text[]))
        GROUP BY thread_id
        HAVING COUNT(DISTINCT agent_id) > 1
        ORDER BY last_active DESC
        LIMIT $1
    """
    ma_rows = await db.pool().fetch(multi_agent_q, limit, list(seen_threads) or ["__none__"])

    for r in ma_rows:
        tid = r["thread_id"]
        if tid in seen_threads:
            continue
        seen_threads.add(tid)
        agents = list(r["agent_ids"] or [])
        sessions.append(SessionSummary(
            session_id=tid,
            session_type="multi_agent",
            label=f"{agents[0]} +{len(agents) - 1}" if len(agents) > 1 else agents[0],
            thread_ids=[tid],
            agent_ids=agents,
            total_runs=1,
            total_tokens=int(r["tokens_used"] or 0),
            total_cost_usd=float(r["run_cost_usd"] or 0),
            last_active=r["last_active"],
            first_active=r["first_active"],
        ))

    # Sort by last_active descending
    sessions.sort(key=lambda s: s.last_active, reverse=True)
    return sessions[:limit]


async def _session_thread_stats(thread_ids: list[str]) -> dict:
    """Fetch aggregated tokens + cost for a set of thread_ids."""
    if not thread_ids:
        return {"tokens": 0, "cost": 0.0}
    q = """
        SELECT
            COALESCE(SUM(DISTINCT (metadata->>'tokens_used')::bigint), 0) AS tokens,
            COALESCE(SUM(DISTINCT (metadata->>'run_cost_usd')::float), 0) AS cost
        FROM (
            SELECT DISTINCT ON (thread_id)
                thread_id, metadata
            FROM agentfile_checkpoints
            WHERE thread_id = ANY($1)
            ORDER BY thread_id, step_index DESC
        ) latest
    """
    row = await db.pool().fetchrow(q, thread_ids)
    return {"tokens": int(row["tokens"] or 0), "cost": float(row["cost"] or 0)}


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
                content=text,
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
                            content=text,
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
                    snippet = json.dumps(args) if args else ""
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
                        duration_ms=None,  # tool dispatch is instantaneous; execution time is on tool_result
                    ))
        elif isinstance(content, str) and content.strip():
            # OpenAI/litellm format: content is a plain string
            events.append(dict(
                ts=ts, agent_id=agent_id, trace_id=trace_id,
                parent_trace_id=parent_trace_id,
                type="assistant_message", role="assistant",
                content=content.strip(),
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
            snippet = json.dumps(args) if args else ""
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
                duration_ms=None,  # tool dispatch is instantaneous; execution time is on tool_result
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
                content=text,
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
    seq = 0  # global sequence counter preserving history order across checkpoints

    # Type priority for tiebreaking when events share the same timestamp.
    # Lower = earlier. user_message must appear before the LLM response it triggered.
    _TYPE_ORDER = {
        "user_message": 0,
        "thinking": 1,
        "assistant_message": 2,
        "tool_call": 3,
        "tool_result": 4,
    }

    for row in rows:
        snap = json.loads(row["checkpoint"]) if isinstance(row["checkpoint"], str) else dict(row["checkpoint"])
        history = snap.get("history", [])
        raw_meta = snap.get("history_meta") or []
        # Pad meta to match history length for old checkpoints that don't have it
        history_meta = raw_meta + [{}] * max(0, len(history) - len(raw_meta))
        prev = agent_prev_len.get(row["agent_id"], 0)
        new_msgs = history[prev:]
        new_meta = history_meta[prev:]
        agent_prev_len[row["agent_id"]] = max(prev, len(history))

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
            new_evts = _msg_to_events(msg, row, meta, prev_meta, tool_id_name)
            for evt in new_evts:
                evt["_seq"] = seq
                seq += 1
            events.extend(new_evts)
            if meta.get("ts"):   # only advance prev when we have a real timestamp
                prev_meta = meta
        agent_prev_meta[row["agent_id"]] = prev_meta

    # Sort by: (1) timestamp, (2) type priority, (3) original history sequence.
    # This ensures user_message always precedes the assistant response at the same ts,
    # and tool_call precedes tool_result, while preserving insertion order as final tiebreaker.
    events.sort(key=lambda e: (
        e.get("ts", ""),
        _TYPE_ORDER.get(e.get("type", ""), 9),
        e.get("_seq", 0),
    ))

    # Strip internal sort key before returning
    for e in events:
        e.pop("_seq", None)

    return events


_TERMINAL_STATUSES = {"completed", "error", "budget_exceeded", "rejected", "interrupted"}


@router.get("/{thread_id}/stream")
async def stream_thread(thread_id: str, request: Request):
    """SSE stream that emits new timeline events as checkpoints arrive (polls DB every 1s).

    Event format:
      data: {"type": "update", "thread_id": "...", "status": "...", "step_index": N, "events": [...]}

    Terminal event:
      event: done
      data: {}
    """
    async def generate():
        exists = await db.pool().fetchrow(
            "SELECT 1 FROM agentfile_checkpoints WHERE thread_id = $1 LIMIT 1",
            thread_id,
        )
        if exists is None:
            yield f"event: error\ndata: {json.dumps({'detail': 'Thread not found'})}\n\n"
            return

        agent_prev_len: dict[str, int] = {}
        agent_prev_meta: dict[str, dict] = {}
        last_status: str | None = None

        q = """
            SELECT agent_id, trace_id, parent_trace_id, step_index,
                   timestamp, checkpoint, status
            FROM agentfile_checkpoints
            WHERE thread_id = $1
            ORDER BY timestamp ASC, step_index ASC
        """

        while True:
            if await request.is_disconnected():
                break

            rows = await db.pool().fetch(q, thread_id)

            # Build tool_call_id → name map from full history (needed for OpenAI format)
            full_tool_id_name: dict[str, str] = {}
            for row in rows:
                snap = json.loads(row["checkpoint"]) if isinstance(row["checkpoint"], str) else dict(row["checkpoint"])
                for m in snap.get("history", []):
                    if m.get("role") == "assistant":
                        for tc in (m.get("tool_calls") or []):
                            tc_id = tc.get("id", "")
                            fn_name = (tc.get("function") or {}).get("name", "")
                            if tc_id and fn_name:
                                full_tool_id_name[tc_id] = fn_name

            new_events: list[dict] = []
            current_status: str | None = None

            for row in rows:
                current_status = row["status"]
                snap = json.loads(row["checkpoint"]) if isinstance(row["checkpoint"], str) else dict(row["checkpoint"])
                history = snap.get("history", [])
                raw_meta = snap.get("history_meta") or []
                history_meta = raw_meta + [{}] * max(0, len(history) - len(raw_meta))

                prev = agent_prev_len.get(row["agent_id"], 0)
                new_msgs = history[prev:]
                new_meta = history_meta[prev:]
                # Use max to prevent the cursor from moving backwards when earlier
                # checkpoint rows (with shorter history) process before later ones
                agent_prev_len[row["agent_id"]] = max(prev, len(history))

                prev_meta = agent_prev_meta.get(row["agent_id"], {})
                for msg, meta in zip(new_msgs, new_meta):
                    new_events.extend(_msg_to_events(msg, row, meta, prev_meta, full_tool_id_name))
                    if meta.get("ts"):
                        prev_meta = meta
                agent_prev_meta[row["agent_id"]] = prev_meta

            new_events.sort(key=lambda e: e.get("ts", ""))

            if new_events or current_status != last_status:
                payload = json.dumps({
                    "type": "update",
                    "thread_id": thread_id,
                    "status": current_status,
                    "step_index": rows[-1]["step_index"] if rows else 0,
                    "events": new_events,
                })
                yield f"data: {payload}\n\n"
                last_status = current_status

            if current_status in _TERMINAL_STATUSES:
                yield "event: done\ndata: {}\n\n"
                break

            await asyncio.sleep(1.0)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{thread_id}/scores", response_model=list[RunScore])
async def get_scores(thread_id: str):
    """Get all scores for a run."""
    rows = await db.pool().fetch(
        "SELECT * FROM run_scores WHERE thread_id = $1 ORDER BY created_at DESC",
        thread_id,
    )
    return [
        RunScore(
            id=str(r["id"]),
            thread_id=r["thread_id"],
            name=r["name"],
            value=r["value"],
            label=r["label"],
            comment=r["comment"],
            scorer=r["scorer"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


@router.post("/{thread_id}/scores", response_model=RunScore)
async def add_score(thread_id: str, payload: CreateScorePayload):
    """Add a score to a run (manual annotation or programmatic)."""
    row = await db.pool().fetchrow(
        """
        INSERT INTO run_scores (thread_id, name, value, label, comment, scorer)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING *
        """,
        thread_id, payload.name, payload.value, payload.label,
        payload.comment, payload.scorer,
    )
    return RunScore(
        id=str(row["id"]),
        thread_id=row["thread_id"],
        name=row["name"],
        value=row["value"],
        label=row["label"],
        comment=row["comment"],
        scorer=row["scorer"],
        created_at=row["created_at"],
    )


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
