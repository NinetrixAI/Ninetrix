"""Agentfile API server — serves the local web dashboard."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI


class _NoHealthFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "GET /health" not in record.getMessage()


logging.getLogger("uvicorn.access").addFilter(_NoHealthFilter())
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from ninetrix_api import db
from ninetrix_api.auth import init_machine_secret
from ninetrix_api.routers import agents, approvals, channels, integrations, runners, threads, tokens


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    await db.create_runner_events_table()
    await db.create_integration_tables()
    init_machine_secret()
    # Start channel polling (Telegram getUpdates) — no tunnel needed for local dev
    from ninetrix_channels.polling import ChannelPoller
    poller = ChannelPoller(db.pool(), channels.handle_polled_message)
    await poller.start()
    yield
    await poller.stop()
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
app.include_router(runners.router, prefix="/internal/v1/runners", tags=["runners"])
app.include_router(channels.router, prefix="/v1/channels", tags=["channels"])
app.include_router(channels.webhook_router, prefix="/v1/channels", tags=["channels-webhook"])


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/internal/auth/token")
async def dashboard_token():
    """Return the machine secret for the local dashboard.

    Safe because this API only binds to localhost — same trust boundary as
    the filesystem where ~/.agentfile/.api-secret lives.
    """
    from ninetrix_api.auth import _machine_secret
    return {"token": _machine_secret}


@app.get("/internal/v1/channels/config")
async def channels_config():
    """Return verified channel configs including secrets (bot_token).

    Used by the CLI to sync dashboard-created channels to channels.yaml.
    Internal-only — same localhost trust boundary.
    """
    import json
    rows = await db.pool().fetch(
        "SELECT channel_type, config FROM channels WHERE verified = TRUE AND enabled = TRUE"
    )
    result = {}
    for r in rows:
        cfg = r["config"] if isinstance(r["config"], dict) else json.loads(r["config"])
        result[r["channel_type"]] = {
            "bot_token": cfg.get("bot_token", ""),
            "bot_username": cfg.get("bot_username", ""),
            "chat_id": cfg.get("chat_id", ""),
            "verified": True,
        }
    return result


@app.get("/")
async def root():
    return RedirectResponse(url="/dashboard/")


# Serve the pre-built Next.js dashboard (static export) at /dashboard
_dashboard_dir = Path(__file__).parent / "static" / "dashboard"
if _dashboard_dir.exists():

    @app.get("/dashboard")
    async def dashboard_index():
        """Serve dashboard index directly to avoid 307 redirect from /dashboard to /dashboard/."""
        from fastapi.responses import FileResponse

        return FileResponse(
            _dashboard_dir / "index.html", media_type="text/html"
        )

    app.mount("/dashboard", StaticFiles(directory=str(_dashboard_dir), html=True), name="dashboard")
