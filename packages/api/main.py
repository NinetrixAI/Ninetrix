"""Agentfile API server — serves the local web dashboard."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ninetrix_api import db
from ninetrix_api.auth import init_machine_secret
from ninetrix_api.routers import agents, approvals, integrations, runners, threads, tokens


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    await db.create_runner_events_table()
    await db.create_integration_tables()
    init_machine_secret()
    yield
    await db.close()


app = FastAPI(
    title="Agentfile API",
    version="0.1.0",
    description="Local API server for the Agentfile web dashboard",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(threads.router, prefix="/threads", tags=["threads"])
app.include_router(agents.router, prefix="/agents", tags=["agents"])
app.include_router(approvals.router, prefix="/approvals", tags=["approvals"])
app.include_router(integrations.router, prefix="/integrations", tags=["integrations"])
app.include_router(tokens.router, prefix="/tokens", tags=["tokens"])
app.include_router(runners.router, prefix="/v1/runners", tags=["runners"])


@app.get("/health")
async def health():
    return {"status": "ok"}
