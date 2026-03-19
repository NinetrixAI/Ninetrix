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

import db
from auth import verify_token

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
                        ON CONFLICT (thread_id, step_index) DO NOTHING
                        """,
                        trace_id, thread_id, agent_id, model,
                    )
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
                    checkpoint_json = json.dumps({
                        "history":      history,
                        "history_meta": history_meta,
                    })
                    metadata_json = json.dumps({
                        "tokens_used":   tokens_used,
                        "model":         model,
                        "input_tokens":  input_tokens,
                        "output_tokens": output_tokens,
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
                    log.info("checkpoint | thread=%s step=%d status=%s tokens=%d",
                             thread_id, step_index, status, tokens_used)

                elif event.type in ("thread_completed", "thread_error"):
                    thread_id   = data.get("thread_id", "")
                    if not thread_id:
                        continue
                    new_status    = "completed" if event.type == "thread_completed" else "error"
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
                    log.info("thread status | thread=%s → %s", thread_id, new_status)

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

    except Exception:
        log.exception("ingest_events | unhandled error")
        raise

    return {"saved": len(payload.events)}
