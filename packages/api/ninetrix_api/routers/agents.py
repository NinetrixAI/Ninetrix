"""Agent endpoints — aggregated stats across all threads."""
from __future__ import annotations

from fastapi import APIRouter

from ninetrix_api import db
from ninetrix_api.models import AgentStats

router = APIRouter()


@router.get("", response_model=list[AgentStats])
async def list_agents():
    """Return aggregated stats per agent across all threads."""
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
        )
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
        ORDER BY last_seen DESC
    """
    rows = await db.pool().fetch(q)
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
    return result
