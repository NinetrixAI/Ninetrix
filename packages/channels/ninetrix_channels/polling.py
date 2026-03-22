"""Long-polling manager for channels that support it (Telegram getUpdates).

Used in local mode so users don't need ngrok/cloudflare tunnels.
SaaS mode uses webhooks instead.

Usage:
    poller = ChannelPoller(db_pool, on_message_callback)
    await poller.start()   # scans DB for verified channels, starts polling loops
    await poller.stop()    # graceful shutdown
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Awaitable

from pathlib import Path

import httpx

from ninetrix_channels.base import InboundMessage

logger = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}"

# Telegram long-poll timeout (seconds). Telegram holds the connection open
# for this long, returning immediately when a new update arrives.
_POLL_TIMEOUT = 30

# How often to re-scan DB for new/removed channels (seconds)
_CHANNEL_SCAN_INTERVAL = 30


OnMessageCallback = Callable[[InboundMessage, dict], Awaitable[None]]

_CHANNELS_YAML = Path.home() / ".agentfile" / "channels.yaml"


def _sync_to_channels_yaml(channel_type: str, config: dict) -> None:
    """Write verified channel config to ~/.agentfile/channels.yaml.

    This keeps the CLI's ChannelBridge in sync with channels created
    via the dashboard. The CLI reads this file during `ninetrix run`.
    Uses yaml if available, falls back to writing a simple YAML manually.
    """
    try:
        _CHANNELS_YAML.parent.mkdir(parents=True, exist_ok=True)

        existing: dict = {}
        if _CHANNELS_YAML.exists():
            try:
                import yaml
                with open(_CHANNELS_YAML) as f:
                    existing = yaml.safe_load(f) or {}
                if not isinstance(existing, dict):
                    existing = {}
            except ImportError:
                existing = {}

        existing[channel_type] = {
            "bot_token": config.get("bot_token", ""),
            "bot_username": config.get("bot_username", ""),
            "chat_id": config.get("chat_id", ""),
            "verified": True,
        }

        try:
            import yaml
            with open(_CHANNELS_YAML, "w") as f:
                yaml.dump(existing, f, default_flow_style=False)
        except ImportError:
            # Fallback: write simple YAML without pyyaml
            lines = []
            for ctype, cdata in existing.items():
                lines.append(f"{ctype}:")
                for k, v in cdata.items():
                    if isinstance(v, bool):
                        lines.append(f"  {k}: {'true' if v else 'false'}")
                    else:
                        lines.append(f"  {k}: '{v}'")
            with open(_CHANNELS_YAML, "w") as f:
                f.write("\n".join(lines) + "\n")

        _CHANNELS_YAML.chmod(0o600)
        logger.info("Synced %s channel to %s", channel_type, _CHANNELS_YAML)
    except Exception:
        logger.warning("Failed to sync channel to channels.yaml", exc_info=True)


class ChannelPoller:
    """Manages long-polling loops for all verified local channels."""

    def __init__(self, db_pool: Any, on_message: OnMessageCallback) -> None:
        self._pool = db_pool
        self._on_message = on_message
        self._tasks: dict[str, asyncio.Task] = {}   # channel_id → polling task
        self._offsets: dict[str, int] = {}           # channel_id → last update_id
        self._scanner_task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        """Start the poller. Scans DB for channels and launches polling loops."""
        self._running = True
        self._scanner_task = asyncio.create_task(self._scan_loop())
        logger.info("Channel poller started")

    async def stop(self) -> None:
        """Stop all polling loops gracefully."""
        self._running = False
        if self._scanner_task:
            self._scanner_task.cancel()
            try:
                await self._scanner_task
            except asyncio.CancelledError:
                pass

        for channel_id, task in self._tasks.items():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        logger.info("Channel poller stopped")

    async def _scan_loop(self) -> None:
        """Periodically scan DB for channels that need polling."""
        while self._running:
            try:
                await self._sync_channels()
            except Exception:
                logger.exception("Error scanning channels")
            await asyncio.sleep(_CHANNEL_SCAN_INTERVAL)

    async def _sync_channels(self) -> None:
        """Sync running polling tasks with UNVERIFIED channels in DB.

        Only polls unverified channels — they need polling to receive /start
        and 6-digit verification codes from users. Once verified, the CLI's
        ChannelBridge (started by `ninetrix run`) takes over polling.
        """
        rows = await self._pool.fetch(
            "SELECT id, channel_type, config FROM channels WHERE enabled = TRUE AND verified = FALSE"
        )

        active_ids = set()
        for row in rows:
            channel_id = str(row["id"])
            channel_type = row["channel_type"]
            active_ids.add(channel_id)

            # Only poll channels that support it (telegram)
            if channel_type != "telegram":
                continue

            # Already polling this channel
            if channel_id in self._tasks and not self._tasks[channel_id].done():
                continue

            config = row["config"] if isinstance(row["config"], dict) else json.loads(row["config"])
            bot_token = config.get("bot_token", "")
            if not bot_token:
                continue

            # Delete any existing webhook before polling (Telegram requires this)
            await self._delete_webhook(bot_token)

            # Start polling loop
            self._tasks[channel_id] = asyncio.create_task(
                self._poll_telegram(channel_id, bot_token)
            )
            logger.info("Started polling for channel %s", channel_id)

        # Stop polling for removed/disabled channels
        for channel_id in list(self._tasks):
            if channel_id not in active_ids:
                self._tasks[channel_id].cancel()
                del self._tasks[channel_id]
                self._offsets.pop(channel_id, None)
                logger.info("Stopped polling for channel %s (removed/disabled)", channel_id)

    async def _delete_webhook(self, bot_token: str) -> None:
        """Delete any existing webhook so getUpdates works."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{_API.format(token=bot_token)}/deleteWebhook",
                    json={"drop_pending_updates": False},
                )
                if resp.status_code == 200:
                    logger.debug("Deleted webhook for bot (polling mode)")
        except Exception:
            logger.warning("Failed to delete webhook", exc_info=True)

    async def _poll_telegram(self, channel_id: str, bot_token: str) -> None:
        """Long-poll loop for a single Telegram bot."""
        offset = self._offsets.get(channel_id, 0)

        async with httpx.AsyncClient(timeout=_POLL_TIMEOUT + 10) as client:
            while self._running:
                try:
                    params: dict[str, Any] = {
                        "timeout": _POLL_TIMEOUT,
                        "allowed_updates": ["message"],
                    }
                    if offset:
                        params["offset"] = offset

                    resp = await client.get(
                        f"{_API.format(token=bot_token)}/getUpdates",
                        params=params,
                    )

                    if resp.status_code == 409:
                        # Another instance is polling or webhook is still set
                        logger.warning("409 Conflict on getUpdates for channel %s — retrying after webhook delete", channel_id)
                        await self._delete_webhook(bot_token)
                        await asyncio.sleep(2)
                        continue

                    if resp.status_code != 200:
                        logger.error("getUpdates failed (%d): %s", resp.status_code, resp.text[:200])
                        await asyncio.sleep(5)
                        continue

                    data = resp.json()
                    updates = data.get("result", [])

                    for update in updates:
                        update_id = update.get("update_id", 0)
                        offset = update_id + 1
                        self._offsets[channel_id] = offset

                        await self._handle_update(channel_id, update, bot_token)

                except asyncio.CancelledError:
                    raise
                except httpx.ReadTimeout:
                    # Normal — long poll timed out with no updates
                    continue
                except Exception:
                    logger.exception("Polling error for channel %s", channel_id)
                    await asyncio.sleep(5)

    async def _handle_update(self, channel_id: str, update: dict, bot_token: str) -> None:
        """Process a single Telegram update from getUpdates."""
        message = update.get("message")
        if not message:
            return

        text = (message.get("text") or "").strip()
        if not text:
            return

        chat_id = str(message["chat"]["id"])
        from_user = message.get("from", {})
        user_id = str(from_user.get("id", "")) if from_user.get("id") else None
        username = from_user.get("username") or from_user.get("first_name")
        first_name = from_user.get("first_name", "")

        # Handle verification flow (same as webhook handler)
        if text.startswith("/start"):
            await self._send_telegram(bot_token, chat_id, (
                f"Hi {first_name}! To connect this chat as a Ninetrix channel, "
                "enter the 6-digit verification code from your dashboard."
            ))
            return

        if text.isdigit() and len(text) == 6:
            row = await self._pool.fetchrow(
                """
                SELECT id, config FROM channels
                WHERE channel_type = 'telegram' AND verified = FALSE
                  AND config->>'verification_code' = $1
                ORDER BY created_at DESC LIMIT 1
                """,
                text,
            )
            if row:
                config = row["config"] if isinstance(row["config"], dict) else json.loads(row["config"])
                config["chat_id"] = chat_id
                await self._pool.execute(
                    "UPDATE channels SET config = $2::jsonb, verified = TRUE, updated_at = NOW() WHERE id = $1",
                    row["id"], json.dumps(config),
                )
                # Sync to ~/.agentfile/channels.yaml so `ninetrix run` can find it
                _sync_to_channels_yaml("telegram", config)
                await self._send_telegram(bot_token, chat_id,
                    "Channel verified! Messages you send here will now trigger agent runs."
                )
            return

        # Regular message — dispatch to agent
        msg = InboundMessage(
            channel_id=channel_id,
            chat_id=chat_id,
            user_id=user_id,
            username=username,
            text=text,
            raw=update,
        )

        # Load full channel record for routing
        row = await self._pool.fetchrow("SELECT * FROM channels WHERE id = $1", channel_id)
        if row:
            channel = dict(row)
            await self._on_message(msg, channel)

    async def _send_telegram(self, bot_token: str, chat_id: str, text: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"{_API.format(token=bot_token)}/sendMessage",
                    json={"chat_id": chat_id, "text": text},
                )
        except Exception:
            logger.warning("Failed to send Telegram message", exc_info=True)
