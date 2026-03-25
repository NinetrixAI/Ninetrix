"""Runner event ingestion — agents phone home with lifecycle events.

Accepts the same PostEventsPayload structure as saas-api so agents can point
AGENTFILE_API_URL at either this local server or the cloud SaaS API
interchangeably.

Authentication: machine secret (auto-shared with CLI on same host) or an
organization token stored in the org_tokens table.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from typing import Any

from ninetrix_api import db
from ninetrix_api.auth import verify_token

log = logging.getLogger(__name__)
router = APIRouter()


# ── Request models ─────────────────────────────────────────────────────────────

class RunnerEvent(BaseModel):
    type: str
    sequence_num: int | None = None
    occurred_at: datetime | None = None
    data: dict[str, Any] = {}


class PostEventsPayload(BaseModel):
    events: list[RunnerEvent]


# ── Endpoint ───────────────────────────────────────────────────────────────────

@router.post("/events")
async def ingest_events(
    payload: PostEventsPayload,
    _: None = Depends(verify_token),
) -> dict:
    """Receive runner lifecycle events from an agent container.

    Handles the same event types as saas-api:
      thread_started   → upsert initial checkpoint row
      checkpoint       → upsert full checkpoint (history + history_meta + tokens)
      thread_completed → update final status
      thread_error     → update status to error
    """
    if not payload.events:
        return {"saved": 0}

    now = datetime.now(timezone.utc)
    saved = 0

    try:
        async with db.pool().acquire() as conn:
            for event in payload.events:
                data = event.data

                if event.type == "thread_started":
                    thread_id = data.get("thread_id", "")
                    if not thread_id:
                        continue
                    trace_id     = data.get("trace_id") or f"run_{thread_id[:8]}"
                    agent_id     = data.get("agent_id", "unknown")
                    model        = data.get("model", "")
                    await conn.execute(
                        """
                        INSERT INTO agentfile_checkpoints
                            (trace_id, thread_id, agent_id, step_index, status,
                             checkpoint, metadata)
                        VALUES ($1, $2, $3, 0, 'in_progress', '{}',
                                jsonb_build_object('model', $4::text))
                        ON CONFLICT (thread_id, step_index) DO UPDATE SET
                            trace_id  = EXCLUDED.trace_id,
                            agent_id  = EXCLUDED.agent_id,
                            status    = 'in_progress',
                            metadata  = jsonb_build_object('model', $4::text),
                            "timestamp" = NOW()
                        """,
                        trace_id, thread_id, agent_id, model,
                    )
                    saved += 1
                    log.info("thread_started | thread=%s agent=%s", thread_id, agent_id)

                elif event.type == "checkpoint":
                    thread_id = data.get("thread_id", "")
                    if not thread_id:
                        continue
                    trace_id        = data.get("trace_id") or f"run_{thread_id[:8]}"
                    parent_trace_id = data.get("parent_trace_id") or None
                    agent_id        = data.get("agent_id", "unknown")
                    step_index      = int(data.get("step_index", 0) or 0)
                    status          = data.get("status", "in_progress")
                    history         = data.get("history", [])
                    history_meta    = data.get("history_meta", [])
                    tokens_used     = int(data.get("tokens_used", 0) or 0)
                    input_tokens    = int(data.get("input_tokens", 0) or 0)
                    output_tokens   = int(data.get("output_tokens", 0) or 0)
                    model           = data.get("model", "")
                    turn_start_history_len = int(data.get("turn_start_history_len", 0) or 0)
                    pending_tool_calls     = data.get("pending_tool_calls", []) or []
                    checkpoint_json = json.dumps({
                        "history":                history,
                        "history_meta":           history_meta,
                        "turn_start_history_len": turn_start_history_len,
                        "pending_tool_calls":     pending_tool_calls,
                    })
                    run_cost_usd      = float(data.get("run_cost_usd") or 0)
                    budget_usd        = float(data.get("budget_usd") or 0)
                    budget_warning    = bool(data.get("budget_warning", False))
                    budget_soft_warned = bool(data.get("budget_soft_warned", False))
                    metadata_json = json.dumps({
                        "tokens_used":       tokens_used,
                        "model":             model,
                        "input_tokens":      input_tokens,
                        "output_tokens":     output_tokens,
                        "run_cost_usd":      run_cost_usd,
                        "budget_usd":        budget_usd,
                        "budget_warning":    budget_warning,
                        "budget_soft_warned": budget_soft_warned,
                    })
                    await conn.execute(
                        """
                        INSERT INTO agentfile_checkpoints
                            (trace_id, parent_trace_id, thread_id, agent_id, step_index,
                             status, checkpoint, metadata)
                        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb)
                        ON CONFLICT (thread_id, step_index) DO UPDATE SET
                            status          = EXCLUDED.status,
                            checkpoint      = EXCLUDED.checkpoint,
                            metadata        = EXCLUDED.metadata,
                            parent_trace_id = EXCLUDED.parent_trace_id,
                            "timestamp"     = NOW()
                        """,
                        trace_id, parent_trace_id, thread_id, agent_id,
                        step_index, status, checkpoint_json, metadata_json,
                    )
                    saved += 1
                    log.info("checkpoint | thread=%s step=%d status=%s tokens=%d",
                             thread_id, step_index, status, tokens_used)

                elif event.type in ("thread_completed", "thread_error", "thread_idle"):
                    thread_id   = data.get("thread_id", "")
                    if not thread_id:
                        continue
                    if event.type == "thread_completed":
                        new_status = "completed"
                    elif event.type == "thread_idle":
                        new_status = "idle"
                    else:
                        new_status = "error"
                    tokens_used   = int(data.get("tokens_used", 0) or 0)
                    model         = data.get("model", "")
                    metadata_json = json.dumps({"tokens_used": tokens_used, "model": model})
                    await conn.execute(
                        """
                        UPDATE agentfile_checkpoints
                        SET status   = $2,
                            metadata = metadata || $3::jsonb
                        WHERE (thread_id, step_index) = (
                            SELECT thread_id, step_index
                            FROM agentfile_checkpoints
                            WHERE thread_id = $1
                            ORDER BY step_index DESC
                            LIMIT 1
                        )
                        """,
                        thread_id, new_status, metadata_json,
                    )
                    saved += 1
                    log.info("thread status | thread=%s → %s", thread_id, new_status)

                elif event.type == "thread_budget_exceeded":
                    thread_id = data.get("thread_id", "")
                    if not thread_id:
                        continue
                    run_cost_usd = float(data.get("run_cost_usd") or 0)
                    budget_usd   = float(data.get("budget_usd") or 0)
                    extra_meta   = json.dumps({
                        "run_cost_usd":   run_cost_usd,
                        "budget_usd":     budget_usd,
                        "budget_warning": True,
                    })
                    await conn.execute(
                        """
                        UPDATE agentfile_checkpoints
                        SET status   = 'budget_exceeded',
                            metadata = metadata || $2::jsonb
                        WHERE (thread_id, step_index) = (
                            SELECT thread_id, step_index
                            FROM agentfile_checkpoints
                            WHERE thread_id = $1
                            ORDER BY step_index DESC
                            LIMIT 1
                        )
                        """,
                        thread_id, extra_meta,
                    )
                    saved += 1
                    log.info("thread status | thread=%s → budget_exceeded (cost=$%.4f / $%.2f)",
                             thread_id, run_cost_usd, budget_usd)

                elif event.type == "thread_rate_limited":
                    thread_id   = data.get("thread_id", "")
                    wait_seconds = float(data.get("wait_seconds") or 0)
                    rate_limit  = data.get("rate_limit", "")
                    if not thread_id:
                        continue
                    extra_meta = json.dumps({
                        "rate_limited":    True,
                        "rate_limit_waits": 1,   # incremented via jsonb arithmetic below
                    })
                    await conn.execute(
                        """
                        UPDATE agentfile_checkpoints
                        SET metadata = metadata
                            || jsonb_build_object(
                                'rate_limited', true,
                                'rate_limit_waits',
                                COALESCE((metadata->>'rate_limit_waits')::int, 0) + 1
                            )
                        WHERE (thread_id, step_index) = (
                            SELECT thread_id, step_index
                            FROM agentfile_checkpoints
                            WHERE thread_id = $1
                            ORDER BY step_index DESC
                            LIMIT 1
                        )
                        """,
                        thread_id,
                    )
                    saved += 1
                    log.info("rate_limited | thread=%s wait=%.1fs limit=%s",
                             thread_id, wait_seconds, rate_limit)

                elif event.type == "budget_warning":
                    # Budget warning events are informational — log them.
                    # The cost data is already embedded in checkpoint metadata via the checkpoint event.
                    thread_id = data.get("thread_id", "")
                    pct_used  = data.get("pct_used", 0)
                    run_cost  = data.get("run_cost_usd", 0)
                    budget    = data.get("budget_usd", 0)
                    saved += 1
                    log.warning("budget_warning | thread=%s pct=%s%% cost=$%.4f budget=$%.2f",
                                thread_id, pct_used, run_cost, budget)

                # ── Workflow events ───────────────────────────────────────────

                elif event.type == "workflow_started":
                    thread_id = data.get("thread_id", "")
                    if not thread_id:
                        continue
                    trace_id     = data.get("trace_id") or f"run_{thread_id[:8]}"
                    wf_name      = data.get("workflow_name", "workflow")
                    checkpoint_json = json.dumps({
                        "history":      [{"role": "user", "content": f"Workflow: {wf_name}"}],
                        "history_meta": [{"ts": now.isoformat()}],
                    })
                    metadata_json = json.dumps({
                        "type": "workflow", "workflow_name": wf_name, "tokens_used": 0,
                    })
                    await conn.execute(
                        """
                        INSERT INTO agentfile_checkpoints
                            (trace_id, thread_id, agent_id, step_index, status,
                             checkpoint, metadata)
                        VALUES ($1, $2, $3, 0, 'in_progress', $4::jsonb, $5::jsonb)
                        ON CONFLICT (thread_id, step_index) DO UPDATE SET
                            trace_id    = EXCLUDED.trace_id,
                            agent_id    = EXCLUDED.agent_id,
                            status      = 'in_progress',
                            checkpoint  = EXCLUDED.checkpoint,
                            metadata    = EXCLUDED.metadata,
                            "timestamp" = NOW()
                        """,
                        trace_id, thread_id, f"workflow:{wf_name}",
                        checkpoint_json, metadata_json,
                    )
                    saved += 1
                    log.info("workflow_started | thread=%s workflow=%s", thread_id, wf_name)

                elif event.type == "workflow_step_completed":
                    thread_id = data.get("thread_id", "")
                    if not thread_id:
                        continue
                    step_name = data.get("step_name", "?")
                    cached    = bool(data.get("cached", False))
                    label     = f"⚡ {step_name} (cached)" if cached else f"✓ {step_name}"
                    await conn.execute(
                        """
                        UPDATE agentfile_checkpoints
                        SET checkpoint = jsonb_set(
                                jsonb_set(checkpoint, '{history}',
                                    (checkpoint->'history') || $2::jsonb),
                                '{history_meta}',
                                (checkpoint->'history_meta') || $3::jsonb)
                        WHERE thread_id = $1 AND step_index = 0
                        """,
                        thread_id,
                        json.dumps([{"role": "assistant", "content": label}]),
                        json.dumps([{"ts": now.isoformat()}]),
                    )
                    saved += 1

                elif event.type == "workflow_completed":
                    thread_id  = data.get("thread_id", "")
                    if not thread_id:
                        continue
                    terminated = bool(data.get("terminated", False))
                    new_status = "completed"
                    extra_meta = json.dumps({
                        "completed_steps":    data.get("completed_steps", []),
                        "skipped_steps":      data.get("skipped_steps", []),
                        "terminated":         terminated,
                        "termination_reason": data.get("termination_reason", ""),
                    })
                    await conn.execute(
                        """
                        UPDATE agentfile_checkpoints
                        SET status   = $2,
                            metadata = metadata || $3::jsonb
                        WHERE thread_id = $1 AND step_index = 0
                        """,
                        thread_id, new_status, extra_meta,
                    )
                    saved += 1
                    log.info("workflow_completed | thread=%s terminated=%s", thread_id, terminated)

                # ── Team events ───────────────────────────────────────────────

                elif event.type == "team_started":
                    thread_id = data.get("thread_id", "")
                    if not thread_id:
                        continue
                    trace_id     = data.get("trace_id") or f"run_{thread_id[:8]}"
                    team_name    = data.get("team_name", "team")
                    agent_names  = data.get("agents", [])
                    checkpoint_json = json.dumps({
                        "history":      [{"role": "user", "content": f"Team: {team_name}"}],
                        "history_meta": [{"ts": now.isoformat()}],
                    })
                    metadata_json = json.dumps({
                        "type": "team", "team_name": team_name,
                        "agents": agent_names, "tokens_used": 0,
                    })
                    await conn.execute(
                        """
                        INSERT INTO agentfile_checkpoints
                            (trace_id, thread_id, agent_id, step_index, status,
                             checkpoint, metadata)
                        VALUES ($1, $2, $3, 0, 'in_progress', $4::jsonb, $5::jsonb)
                        ON CONFLICT (thread_id, step_index) DO UPDATE SET
                            trace_id    = EXCLUDED.trace_id,
                            agent_id    = EXCLUDED.agent_id,
                            status      = 'in_progress',
                            checkpoint  = EXCLUDED.checkpoint,
                            metadata    = EXCLUDED.metadata,
                            "timestamp" = NOW()
                        """,
                        trace_id, thread_id, f"team:{team_name}",
                        checkpoint_json, metadata_json,
                    )
                    saved += 1
                    log.info("team_started | thread=%s team=%s agents=%s",
                             thread_id, team_name, agent_names)

                elif event.type == "team_routed":
                    thread_id = data.get("thread_id", "")
                    if not thread_id:
                        continue
                    routed_to = data.get("routed_to", "?")
                    await conn.execute(
                        """
                        UPDATE agentfile_checkpoints
                        SET checkpoint = jsonb_set(
                                jsonb_set(checkpoint, '{history}',
                                    (checkpoint->'history') || $2::jsonb),
                                '{history_meta}',
                                (checkpoint->'history_meta') || $3::jsonb)
                        WHERE thread_id = $1 AND step_index = 0
                        """,
                        thread_id,
                        json.dumps([{"role": "assistant",
                                     "content": f"→ Routed to: {routed_to}"}]),
                        json.dumps([{"ts": now.isoformat()}]),
                    )
                    saved += 1

                elif event.type == "team_completed":
                    thread_id   = data.get("thread_id", "")
                    if not thread_id:
                        continue
                    routed_to   = data.get("routed_to", "")
                    tokens_used = int(data.get("tokens_used", 0) or 0)
                    await conn.execute(
                        """
                        UPDATE agentfile_checkpoints
                        SET status   = 'completed',
                            metadata = metadata || $2::jsonb
                        WHERE thread_id = $1 AND step_index = 0
                        """,
                        thread_id,
                        json.dumps({"tokens_used": tokens_used, "routed_to": routed_to}),
                    )
                    saved += 1
                    log.info("team_completed | thread=%s routed_to=%s tokens=%d",
                             thread_id, routed_to, tokens_used)

                elif event.type in ("agent_transfer_started",
                                    "agent_transfer_completed",
                                    "agent_transfer_failed"):
                    # Informational events for sub-agent / collaborator transfers.
                    # The actual trace data arrives via checkpoint events with
                    # parent_trace_id set — these are just logged for debugging.
                    _from = data.get("from", "?")
                    _to   = data.get("to", "?")
                    saved += 1
                    log.info("%s | %s → %s (thread=%s)",
                             event.type, _from, _to, data.get("thread_id", ""))

                elif event.type == "heartbeat":
                    agent_id = data.get("agent_id", "")
                    if agent_id:
                        await conn.execute(
                            """
                            INSERT INTO agent_heartbeats (agent_id, last_seen)
                            VALUES ($1, NOW())
                            ON CONFLICT (agent_id) DO UPDATE SET last_seen = NOW()
                            """,
                            agent_id,
                        )
                        saved += 1

                else:
                    # Store unknown events in runner_events for debugging
                    await conn.execute(
                        """
                        INSERT INTO runner_events
                            (event_type, thread_id, trace_id, agent_id, payload)
                        VALUES ($1, $2, $3, $4, $5::jsonb)
                        """,
                        event.type,
                        data.get("thread_id", ""),
                        data.get("trace_id") or None,
                        data.get("agent_id") or None,
                        json.dumps(data),
                    )
                    saved += 1

    except Exception:
        log.exception("ingest_events | unhandled error")
        raise

    return {"saved": saved}


@router.get("/threads/{thread_id}/latest")
async def get_latest_checkpoint(
    thread_id: str,
    _: None = Depends(verify_token),
) -> dict:
    """Return the latest checkpoint for a thread — used by agents on startup
    to resume from their last known-good state (durable runs).

    Returns 404 if no checkpoint exists for this thread_id.
    """
    try:
        async with db.pool().acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT trace_id, step_index, status, checkpoint, metadata
                FROM agentfile_checkpoints
                WHERE thread_id = $1
                ORDER BY step_index DESC
                LIMIT 1
                """,
                thread_id,
            )
    except Exception:
        log.exception("get_latest_checkpoint | unhandled error")
        raise

    if row is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="No checkpoint found")

    cp = row["checkpoint"] if isinstance(row["checkpoint"], dict) else json.loads(row["checkpoint"])
    return {
        "thread_id":              thread_id,
        "trace_id":               row["trace_id"],
        "step_index":             row["step_index"],
        "status":                 row["status"],
        "history":                cp.get("history", []),
        "history_meta":           cp.get("history_meta", []),
        "turn_start_history_len": cp.get("turn_start_history_len", 0),
        "pending_tool_calls":     cp.get("pending_tool_calls", []),
    }
