from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from core.registry import registry

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok", "connected_workers": len(registry._workers)}


@router.get("/admin/workers")
async def list_workers(org_id: Optional[str] = Query(None)):
    workers = registry.list_workers(org_id)
    return {"workers": [w.model_dump() for w in workers], "count": len(workers)}


@router.get("/admin/tools")
async def list_tools(org_id: str = Query("default")):
    tools = registry.get_tools(org_id)
    return {"tools": [t.model_dump() for t in tools], "count": len(tools)}
