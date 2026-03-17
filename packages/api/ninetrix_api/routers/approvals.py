"""Human-in-the-loop approval endpoints."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from ninetrix_api import db
from ninetrix_api.models import ApprovalItem

router = APIRouter()


@router.get("", response_model=list[ApprovalItem])
async def list_pending_approvals():
    """Return all checkpoints currently waiting for human approval."""
    q = """
        SELECT
            trace_id, thread_id, agent_id, step_index,
            timestamp AS created_at,
            checkpoint
        FROM agentfile_checkpoints
        WHERE status = 'waiting_for_approval'
        ORDER BY timestamp ASC
    """
    rows = await db.pool().fetch(q)
    result = []
    for r in rows:
        snap = json.loads(r["checkpoint"]) if isinstance(r["checkpoint"], str) else dict(r["checkpoint"])
        result.append(ApprovalItem(
            trace_id=r["trace_id"],
            thread_id=r["thread_id"],
            agent_id=r["agent_id"],
            step_index=r["step_index"],
            created_at=r["created_at"],
            pending_tool_calls=snap.get("pending_tool_calls", []),
        ))
    return result


@router.post("/{trace_id}/{step_index}/approve", status_code=200)
async def approve(trace_id: str, step_index: int):
    """Approve a pending tool call — the running container will poll this and resume."""
    row = await db.pool().fetchrow(
        """
        UPDATE agentfile_checkpoints
        SET status = 'approved'
        WHERE trace_id = $1 AND step_index = $2 AND status = 'waiting_for_approval'
        RETURNING id
        """,
        trace_id,
        step_index,
    )
    if row is None:
        raise HTTPException(
            status_code=409,
            detail="No pending approval found for this trace_id + step_index",
        )
    return {"ok": True, "trace_id": trace_id, "step_index": step_index, "status": "approved"}


@router.post("/{trace_id}/{step_index}/reject", status_code=200)
async def reject(trace_id: str, step_index: int):
    """Reject a pending tool call — the container will skip the tool and continue."""
    row = await db.pool().fetchrow(
        """
        UPDATE agentfile_checkpoints
        SET status = 'rejected'
        WHERE trace_id = $1 AND step_index = $2 AND status = 'waiting_for_approval'
        RETURNING id
        """,
        trace_id,
        step_index,
    )
    if row is None:
        raise HTTPException(
            status_code=409,
            detail="No pending approval found for this trace_id + step_index",
        )
    return {"ok": True, "trace_id": trace_id, "step_index": step_index, "status": "rejected"}
