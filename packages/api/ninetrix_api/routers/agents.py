"""Agent endpoints — aggregated stats across all threads."""
from __future__ import annotations

from fastapi import APIRouter

from ninetrix_api import db
from ninetrix_api.models import AgentStats, Page

router = APIRouter()


@router.get("", response_model=Page[AgentStats])
async def list_agents(
    limit:  int = 100,
    offset: int = 0,
):
    """Return a paginated page of aggregated stats per agent.

    Query params:
    - **limit**: page size, 1–200 (default 100)
    - **offset**: row offset (default 0)
    """
    limit  = max(1, min(limit, 200))
    offset = max(0, offset)

    q = """
        WITH per_agent_thread AS (
            SELECT
                agent_id,
                thread_id,
                MAX((metadata->>'tokens_used')::bigint) AS tokens,
                MAX(CASE WHEN status IN ('completed', 'approved') THEN 1 ELSE 0 END) AS is_completed,
                MAX(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS is_error,
                MAX(CASE WHEN status IN ('in_progress', 'waiting_for_approval') THEN 1 ELSE 0 END) AS is_running
            FROM agentfile_checkpoints
            GROUP BY agent_id, thread_id
        ),
        agent_data AS (
            SELECT
                pat.agent_id,
                COUNT(*) AS total_runs,
                SUM(is_completed) AS completed_runs,
                SUM(is_error) AS error_runs,
                SUM(is_running) AS running_runs,
                COALESCE(SUM(tokens), 0) AS total_tokens,
                (
                    SELECT MAX(timestamp)
                    FROM agentfile_checkpoints
                    WHERE agent_id = pat.agent_id
                ) AS last_seen,
                (
                    SELECT status
                    FROM agentfile_checkpoints
                    WHERE agent_id = pat.agent_id
                    ORDER BY timestamp DESC
                    LIMIT 1
                ) AS last_status,
                (
                    SELECT COALESCE(array_agg(DISTINCT m ORDER BY m), ARRAY[]::text[])
                    FROM (
                        SELECT DISTINCT metadata->>'model' AS m
                        FROM agentfile_checkpoints
                        WHERE agent_id = pat.agent_id
                          AND metadata->>'model' IS NOT NULL
                          AND metadata->>'model' != ''
                    ) models_sub
                ) AS models
            FROM per_agent_thread pat
            GROUP BY pat.agent_id
        )
        SELECT *, COUNT(*) OVER() AS total_count
        FROM agent_data
        ORDER BY last_seen DESC
        LIMIT $1 OFFSET $2
    """
    rows = await db.pool().fetch(q, limit, offset)
    total = int(rows[0]["total_count"]) if rows else 0
    result = []
    for r in rows:
        result.append(AgentStats(
            agent_id=r["agent_id"],
            total_runs=int(r["total_runs"] or 0),
            completed_runs=int(r["completed_runs"] or 0),
            error_runs=int(r["error_runs"] or 0),
            running_runs=int(r["running_runs"] or 0),
            total_tokens=int(r["total_tokens"] or 0),
            models=list(r["models"] or []),
            last_seen=r["last_seen"],
            last_status=r["last_status"] or "unknown",
        ))
    return Page(items=result, total=total, limit=limit, offset=offset)
