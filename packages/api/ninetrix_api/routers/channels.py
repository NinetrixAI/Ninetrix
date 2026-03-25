"""Channels router — inbound messaging channels that trigger agent runs (local API).

Provides CRUD for channels + agent bindings, plus webhook endpoints for
Telegram (and future WhatsApp, Slack, etc.).
"""
from __future__ import annotations

import json
import logging
import secrets
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ninetrix_api import db
from ninetrix_api.auth import verify_token

from ninetrix_channels import get_adapter
from ninetrix_channels.router import find_channel_by_bot_token, route_message

logger = logging.getLogger(__name__)

router = APIRouter()

# Public router — webhook endpoints called by Telegram / WhatsApp (no auth)
webhook_router = APIRouter()


# ── Request / response models ─────────────────────────────────────────────────

class CreateChannelPayload(BaseModel):
    channel_type: str        # telegram, whatsapp
    name: str
    config: dict = {}        # bot_token, etc.
    session_mode: str = "per_chat"
    routing_mode: str = "single"


class UpdateChannelPayload(BaseModel):
    name: str | None = None
    session_mode: str | None = None
    routing_mode: str | None = None
    enabled: bool | None = None


class BindAgentPayload(BaseModel):
    agent_name: str
    is_default: bool = True
    command: str | None = None   # "/search", "/support"


class SyncSessionPayload(BaseModel):
    channel_type: str        # "telegram", "whatsapp"
    external_chat_id: str
    external_user_id: str = ""
    agent_name: str
    thread_id: str


# ── Channel CRUD ──────────────────────────────────────────────────────────────

@router.get("")
async def list_channels(_: None = Depends(verify_token)):
    rows = await db.pool().fetch(
        "SELECT * FROM channels ORDER BY created_at DESC"
    )
    return [_channel_out(r) for r in rows]


@router.post("", status_code=201)
async def create_channel(
    payload: CreateChannelPayload,
    _: None = Depends(verify_token),
):
    adapter = get_adapter(payload.channel_type)
    if not adapter:
        raise HTTPException(400, f"Unsupported channel type: {payload.channel_type}")

    config = dict(payload.config)

    # Validate credentials with the platform
    ok, err = await adapter.validate_config(config)
    if not ok:
        raise HTTPException(400, err)

    # Fetch bot info
    bot_info = await adapter.get_bot_info(config)
    config["bot_username"] = bot_info.get("username", "")

    # Generate webhook secret + verification code
    config["webhook_secret"] = secrets.token_urlsafe(32)
    config["verification_code"] = f"{secrets.randbelow(900000) + 100000}"

    channel_id = str(uuid.uuid4())
    await db.pool().execute(
        """
        INSERT INTO channels (id, channel_type, name, config, session_mode, routing_mode)
        VALUES ($1, $2, $3, $4::jsonb, $5, $6)
        """,
        channel_id, payload.channel_type, payload.name,
        json.dumps(config), payload.session_mode, payload.routing_mode,
    )

    row = await db.pool().fetchrow("SELECT * FROM channels WHERE id = $1", channel_id)
    return _channel_out(row)


@router.get("/{channel_id}")
async def get_channel(channel_id: str, _: None = Depends(verify_token)):
    row = await db.pool().fetchrow("SELECT * FROM channels WHERE id = $1", channel_id)
    if not row:
        raise HTTPException(404, "Channel not found")

    out = _channel_out(row)
    # Include bound agents
    bindings = await db.pool().fetch(
        "SELECT * FROM channel_agent_bindings WHERE channel_id = $1 ORDER BY created_at",
        channel_id,
    )
    out["agents"] = [
        {
            "id": str(b["id"]),
            "agent_name": b["agent_name"],
            "is_default": b["is_default"],
            "command": b["command"],
        }
        for b in bindings
    ]
    return out


@router.patch("/{channel_id}")
async def update_channel(
    channel_id: str,
    payload: UpdateChannelPayload,
    _: None = Depends(verify_token),
):
    row = await db.pool().fetchrow("SELECT * FROM channels WHERE id = $1", channel_id)
    if not row:
        raise HTTPException(404, "Channel not found")

    updates = {}
    if payload.name is not None:
        updates["name"] = payload.name
    if payload.session_mode is not None:
        updates["session_mode"] = payload.session_mode
    if payload.routing_mode is not None:
        updates["routing_mode"] = payload.routing_mode
    if payload.enabled is not None:
        updates["enabled"] = payload.enabled

    if updates:
        set_clauses = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(updates))
        await db.pool().execute(
            f"UPDATE channels SET {set_clauses}, updated_at = NOW() WHERE id = $1",
            channel_id, *updates.values(),
        )

    row = await db.pool().fetchrow("SELECT * FROM channels WHERE id = $1", channel_id)
    return _channel_out(row)


@router.delete("/{channel_id}", status_code=204)
async def delete_channel(channel_id: str, _: None = Depends(verify_token)):
    result = await db.pool().execute("DELETE FROM channels WHERE id = $1", channel_id)
    if result == "DELETE 0":
        raise HTTPException(404, "Channel not found")


# ── Setup webhook with platform ───────────────────────────────────────────────

@router.post("/{channel_id}/setup-webhook")
async def setup_channel_webhook(
    channel_id: str,
    request: Request,
    _: None = Depends(verify_token),
):
    """Register webhook URL with the messaging platform (Telegram setWebhook, etc.)."""
    body = await request.json()
    base_url = body.get("base_url", "").rstrip("/")
    if not base_url:
        raise HTTPException(400, "base_url is required (your public API URL)")

    row = await db.pool().fetchrow("SELECT * FROM channels WHERE id = $1", channel_id)
    if not row:
        raise HTTPException(404, "Channel not found")

    config = row["config"] if isinstance(row["config"], dict) else json.loads(row["config"])
    adapter = get_adapter(row["channel_type"])
    if not adapter:
        raise HTTPException(400, f"No adapter for {row['channel_type']}")

    webhook_url = f"{base_url}/v1/channels/webhook/{row['channel_type']}"
    ok, err = await adapter.setup_webhook(config, webhook_url)
    if not ok:
        raise HTTPException(502, err)

    return {
        "status": "ok",
        "webhook_url": webhook_url,
        "bot_username": config.get("bot_username", ""),
        "verification_code": config.get("verification_code", ""),
    }


# ── Agent bindings ────────────────────────────────────────────────────────────

@router.post("/{channel_id}/agents", status_code=201)
async def bind_agent(
    channel_id: str,
    payload: BindAgentPayload,
    _: None = Depends(verify_token),
):
    row = await db.pool().fetchrow("SELECT id FROM channels WHERE id = $1", channel_id)
    if not row:
        raise HTTPException(404, "Channel not found")

    binding_id = str(uuid.uuid4())
    try:
        await db.pool().execute(
            """
            INSERT INTO channel_agent_bindings (id, channel_id, agent_name, is_default, command)
            VALUES ($1, $2, $3, $4, $5)
            """,
            binding_id, channel_id, payload.agent_name, payload.is_default, payload.command,
        )
    except Exception as e:
        if "unique" in str(e).lower():
            raise HTTPException(409, "Agent already bound to this channel")
        raise

    return {"id": binding_id, "agent_name": payload.agent_name, "is_default": payload.is_default}


@router.delete("/{channel_id}/agents/{agent_name}", status_code=204)
async def unbind_agent(
    channel_id: str,
    agent_name: str,
    _: None = Depends(verify_token),
):
    result = await db.pool().execute(
        "DELETE FROM channel_agent_bindings WHERE channel_id = $1 AND agent_name = $2",
        channel_id, agent_name,
    )
    if result == "DELETE 0":
        raise HTTPException(404, "Binding not found")


# ── Verification ──────────────────────────────────────────────────────────────

@router.post("/{channel_id}/verify")
async def verify_channel(
    channel_id: str,
    request: Request,
    _: None = Depends(verify_token),
):
    """Verify a channel by checking the verification code."""
    body = await request.json()
    code = str(body.get("code", "")).strip()

    row = await db.pool().fetchrow("SELECT * FROM channels WHERE id = $1", channel_id)
    if not row:
        raise HTTPException(404, "Channel not found")

    config = row["config"] if isinstance(row["config"], dict) else json.loads(row["config"])
    if config.get("verification_code") != code:
        raise HTTPException(400, "Invalid verification code")

    await db.pool().execute(
        "UPDATE channels SET verified = TRUE, updated_at = NOW() WHERE id = $1",
        channel_id,
    )
    return {"status": "verified"}


# ── Platform webhooks (public, no auth) ──────────────────────────────────────

@webhook_router.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """Handle incoming Telegram Bot API updates → route to agent."""
    body = await request.json()

    # Extract bot_token from the update to identify which channel this belongs to
    # Telegram doesn't send the bot_token in the webhook payload, so we need to
    # match by chat_id or use a different strategy. For now, we check all channels.
    adapter = get_adapter("telegram")
    if not adapter:
        return {"ok": True}

    msg = await adapter.parse_webhook(body)

    # Handle verification flow (6-digit codes)
    message_obj = body.get("message", {})
    text = (message_obj.get("text") or "").strip()
    chat_id = str(message_obj.get("chat", {}).get("id", ""))

    if text.startswith("/start") and chat_id:
        await _handle_telegram_start(chat_id, message_obj)
        return {"ok": True}

    if text.isdigit() and len(text) == 6 and chat_id:
        await _handle_telegram_verification(chat_id, text)
        return {"ok": True}

    if not msg:
        return {"ok": True}

    # Find channel by checking all telegram channels for matching config
    # In production, we'd index by webhook_secret header for O(1) lookup
    channel = await _find_telegram_channel_for_update(body)
    if not channel:
        logger.warning("No matching channel for Telegram update")
        return {"ok": True}

    msg.channel_id = str(channel["id"])
    pool = db.pool()

    result = await route_message(pool, msg, channel, adapter)
    if not result:
        # No agent bound — send helpful message
        config = channel["config"] if isinstance(channel["config"], dict) else json.loads(channel["config"])
        await adapter.send_message(config, msg.chat_id, "No agent is connected to this channel yet.")
        return {"ok": True}

    agent_name, thread_id, run_id = result

    # Dispatch to agent container
    dispatched = await _dispatch_to_local_agent(agent_name, msg.text, thread_id)
    if not dispatched:
        config = channel["config"] if isinstance(channel["config"], dict) else json.loads(channel["config"])
        await adapter.send_message(
            config, msg.chat_id,
            f"Agent '{agent_name}' is not running. Start it with `ninetrix run` or `ninetrix up`.",
        )

    return {"ok": True, "run_id": run_id, "thread_id": thread_id}


# ── Session sync (called by CLI bridge) ──────────────────────────────────────

@router.post("/sessions/sync")
async def sync_session(payload: SyncSessionPayload):
    """Upsert a channel_sessions row so the CLI bridge's sessions appear in the dashboard.

    Called by the CLI ChannelBridge after each successful message dispatch.
    If no matching channel exists in the DB, creates a minimal one.
    """
    pool = db.pool()

    # Find or create the channel
    ch = await pool.fetchrow(
        "SELECT id FROM channels WHERE channel_type = $1 AND verified = TRUE LIMIT 1",
        payload.channel_type,
    )
    if not ch:
        # Auto-create a minimal channel record so sessions have a parent
        ch_id = str(uuid.uuid4())
        await pool.execute(
            """
            INSERT INTO channels (id, channel_type, name, config, session_mode, routing_mode, verified, enabled)
            VALUES ($1, $2, $3, '{}'::jsonb, 'per_chat', 'single', TRUE, TRUE)
            ON CONFLICT DO NOTHING
            """,
            ch_id, payload.channel_type, f"{payload.channel_type} (auto)",
        )
        channel_id = ch_id
    else:
        channel_id = str(ch["id"])

    # Upsert session
    await pool.execute(
        """
        INSERT INTO channel_sessions
            (channel_id, external_chat_id, external_user_id, agent_name, thread_id)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (channel_id, external_chat_id, agent_name) DO UPDATE
            SET last_message_at = NOW(),
                external_user_id = COALESCE(NULLIF($3, ''), channel_sessions.external_user_id)
        """,
        channel_id, payload.external_chat_id, payload.external_user_id,
        payload.agent_name, payload.thread_id,
    )

    return {"ok": True}


# ── Internal helpers ─────────────────────────────────────────────────────────

async def _find_telegram_channel_for_update(body: dict) -> dict | None:
    """Find the channel that owns this Telegram bot by checking webhook_secret header
    or falling back to scanning all telegram channels."""
    rows = await db.pool().fetch(
        "SELECT * FROM channels WHERE channel_type = 'telegram' AND verified = TRUE AND enabled = TRUE"
    )
    if not rows:
        return None
    if len(rows) == 1:
        return dict(rows[0])

    # Multiple channels — try to match by chat_id from sessions
    chat_id = str(
        body.get("message", {}).get("chat", {}).get("id", "")
        or body.get("callback_query", {}).get("message", {}).get("chat", {}).get("id", "")
    )
    if chat_id:
        session = await db.pool().fetchrow(
            "SELECT channel_id FROM channel_sessions WHERE external_chat_id = $1 LIMIT 1",
            chat_id,
        )
        if session:
            for r in rows:
                if str(r["id"]) == str(session["channel_id"]):
                    return dict(r)

    # Default to first channel
    return dict(rows[0])


async def _handle_telegram_start(chat_id: str, message: dict) -> None:
    """Handle /start — tell the user to enter their verification code."""
    first_name = message.get("from", {}).get("first_name", "")
    # Find any unverified telegram channel to get its bot_token
    row = await db.pool().fetchrow(
        """
        SELECT config->>'bot_token' AS bot_token
        FROM channels
        WHERE channel_type = 'telegram' AND verified = FALSE
        ORDER BY created_at DESC LIMIT 1
        """
    )
    if not row:
        return
    bot_token = row["bot_token"]
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": (
                    f"Hi {first_name}! To connect this chat as a Ninetrix channel, "
                    "enter the 6-digit verification code from your dashboard."
                ),
            },
        )


async def _handle_telegram_verification(chat_id: str, code: str) -> None:
    """Handle 6-digit verification code — verify the channel and store chat_id."""
    row = await db.pool().fetchrow(
        """
        SELECT id, config
        FROM channels
        WHERE channel_type = 'telegram' AND verified = FALSE
          AND config->>'verification_code' = $1
        ORDER BY created_at DESC LIMIT 1
        """,
        code,
    )
    if not row:
        return

    config = row["config"] if isinstance(row["config"], dict) else json.loads(row["config"])
    config["chat_id"] = chat_id

    await db.pool().execute(
        "UPDATE channels SET config = $2::jsonb, verified = TRUE, updated_at = NOW() WHERE id = $1",
        row["id"], json.dumps(config),
    )

    bot_token = config.get("bot_token", "")
    if bot_token:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": "Channel verified! Messages you send here will now trigger agent runs.",
                },
            )


async def _dispatch_to_local_agent(agent_name: str, message: str, thread_id: str) -> bool:
    """POST to a locally running agent container's webhook endpoint.

    Tries known ports: 9100 (default webhook), then 9000 (invoke).
    Returns True if dispatch succeeded.
    """
    # Check heartbeats to find the agent
    row = await db.pool().fetchrow(
        "SELECT agent_id, last_seen FROM agent_heartbeats WHERE agent_id = $1",
        agent_name,
    )

    # Try the standard webhook port
    for port in [9100, 9000]:
        for host in ["localhost", "host.docker.internal"]:
            url = f"http://{host}:{port}/run"
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.post(url, json={"message": message, "thread_id": thread_id})
                    if resp.status_code in (200, 202):
                        logger.info("Dispatched to %s at %s (thread=%s)", agent_name, url, thread_id)
                        return True
            except (httpx.ConnectError, httpx.ConnectTimeout):
                continue

    logger.warning("Could not dispatch to agent %s — no reachable endpoint", agent_name)
    return False


async def handle_polled_message(msg: "InboundMessage", channel: dict) -> None:
    """Callback for the ChannelPoller — routes a polled message to an agent.

    This is the same logic as the webhook handler, but called from the
    polling loop instead of an HTTP request.
    """
    from ninetrix_channels import get_adapter
    from ninetrix_channels.router import route_message as _route

    adapter = get_adapter(channel["channel_type"])
    if not adapter:
        return

    pool = db.pool()
    result = await _route(pool, msg, channel, adapter)
    if not result:
        config = channel["config"] if isinstance(channel["config"], dict) else json.loads(channel["config"])
        await adapter.send_message(config, msg.chat_id, "No agent is connected to this channel yet.")
        return

    agent_name, thread_id, run_id = result
    dispatched = await _dispatch_to_local_agent(agent_name, msg.text, thread_id)
    if not dispatched:
        config = channel["config"] if isinstance(channel["config"], dict) else json.loads(channel["config"])
        await adapter.send_message(
            config, msg.chat_id,
            f"Agent '{agent_name}' is not running. Start it with `ninetrix run` or `ninetrix up`.",
        )
    else:
        logger.info("Polled message dispatched: agent=%s thread=%s run=%s", agent_name, thread_id, run_id)


def _channel_out(row) -> dict:
    """Format a channel DB row for API response."""
    config = row["config"] if isinstance(row["config"], dict) else json.loads(row["config"])
    # Strip sensitive fields from response
    safe_config = {k: v for k, v in config.items() if k not in ("bot_token", "webhook_secret")}
    return {
        "id": str(row["id"]),
        "channel_type": row["channel_type"],
        "name": row["name"],
        "config": safe_config,
        "session_mode": row["session_mode"],
        "routing_mode": row["routing_mode"],
        "verified": row["verified"],
        "enabled": row["enabled"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }
