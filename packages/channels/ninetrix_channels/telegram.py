"""Telegram channel adapter.

Supports two modes:
- Webhook (SaaS): Telegram pushes updates to a registered URL.
- Persistent (local/container): long-polls getUpdates inside the process.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Literal

import httpx

from ninetrix_channels.base import ChannelAdapter, InboundMessage, MessageCallback

logger = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}"

# Telegram long-poll timeout (seconds). Telegram holds the connection open
# for this long, returning immediately when a new update arrives.
_POLL_TIMEOUT = 30


class TelegramAdapter(ChannelAdapter):
    channel_type = "telegram"

    def __init__(self) -> None:
        self._running = False

    @property
    def connection_mode(self) -> Literal["webhook", "persistent"]:
        return "persistent"

    # ── Required methods ──────────────────────────────────────────────────

    async def validate_config(self, config: dict) -> tuple[bool, str]:
        bot_token = config.get("bot_token", "").strip()
        if not bot_token:
            return False, "bot_token is required"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{_API.format(token=bot_token)}/getMe")
            if resp.status_code != 200:
                return False, "Invalid Telegram bot token"
        return True, ""

    async def send_message(self, config: dict, chat_id: str, text: str) -> bool:
        bot_token = config.get("bot_token", "")
        if not bot_token:
            return False
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{_API.format(token=bot_token)}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            )
            if resp.status_code != 200:
                logger.error("Telegram sendMessage failed: %s", resp.text[:300])
                return False
        return True

    async def get_bot_info(self, config: dict) -> dict:
        bot_token = config.get("bot_token", "")
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{_API.format(token=bot_token)}/getMe")
            if resp.status_code == 200:
                return resp.json().get("result", {})
        return {}

    # ── Webhook mode (SaaS) ───────────────────────────────────────────────

    async def parse_webhook(self, body: dict) -> InboundMessage | None:
        # We only handle regular text messages (not callback queries, edits, etc.)
        message = body.get("message")
        if not message:
            return None

        text = (message.get("text") or "").strip()
        if not text:
            return None

        # Skip bot commands that are part of the verification flow
        if text.startswith("/start"):
            return None

        chat_id = str(message["chat"]["id"])
        from_user = message.get("from", {})
        user_id = str(from_user.get("id", "")) if from_user.get("id") else None
        username = from_user.get("username") or from_user.get("first_name")

        return InboundMessage(
            channel_id="",  # filled by the router after channel lookup
            chat_id=chat_id,
            channel_type="telegram",
            user_id=user_id,
            username=username,
            text=text,
            raw=body,
        )

    async def setup_webhook(self, config: dict, webhook_url: str) -> tuple[bool, str]:
        bot_token = config.get("bot_token", "")
        webhook_secret = config.get("webhook_secret", "")
        async with httpx.AsyncClient(timeout=10) as client:
            payload: dict = {"url": webhook_url}
            if webhook_secret:
                payload["secret_token"] = webhook_secret
            resp = await client.post(
                f"{_API.format(token=bot_token)}/setWebhook",
                json=payload,
            )
            if resp.status_code != 200:
                return False, f"Failed to set Telegram webhook: {resp.text[:300]}"
        return True, ""

    # ── Persistent mode (local/container) ─────────────────────────────────

    async def connect(
        self, config: dict, on_message: MessageCallback,
    ) -> None:
        """Long-poll Telegram getUpdates inside the container.

        Runs indefinitely until disconnect() is called or a fatal error occurs.
        """
        bot_token = config.get("bot_token", "")
        if not bot_token:
            raise ValueError("bot_token is required for Telegram connect()")

        self._running = True
        offset = 0

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_POLL_TIMEOUT + 10),
        ) as client:
            # Delete any existing webhook so getUpdates works
            try:
                await client.post(
                    f"{_API.format(token=bot_token)}/deleteWebhook",
                    json={"drop_pending_updates": False},
                )
            except Exception:
                logger.warning("Failed to delete webhook before polling")

            logger.info("Telegram polling started")

            while self._running:
                try:
                    params: dict = {
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
                        logger.warning(
                            "409 Conflict on getUpdates — deleting webhook and retrying"
                        )
                        await client.post(
                            f"{_API.format(token=bot_token)}/deleteWebhook",
                            json={"drop_pending_updates": False},
                        )
                        await asyncio.sleep(2)
                        continue

                    if resp.status_code != 200:
                        logger.error(
                            "getUpdates failed (%d): %s",
                            resp.status_code, resp.text[:200],
                        )
                        await asyncio.sleep(5)
                        continue

                    updates = resp.json().get("result", [])
                    for update in updates:
                        update_id = update.get("update_id", 0)
                        offset = update_id + 1

                        msg = self._parse_update(update, config)
                        if msg:
                            try:
                                await on_message(msg)
                            except Exception:
                                logger.exception("Error in on_message callback")

                except asyncio.CancelledError:
                    raise
                except httpx.ReadTimeout:
                    # Normal — long poll timed out with no updates
                    continue
                except Exception:
                    logger.exception("Telegram polling error")
                    await asyncio.sleep(5)

    async def disconnect(self) -> None:
        self._running = False

    def _parse_update(self, update: dict, config: dict) -> InboundMessage | None:
        """Parse a single Telegram update into an InboundMessage."""
        message = update.get("message")
        if not message:
            return None

        text = (message.get("text") or "").strip()
        if not text:
            return None

        # Skip /start — used during verification flow only
        if text.startswith("/start"):
            return None

        chat_id = str(message["chat"]["id"])
        from_user = message.get("from", {})
        user_id = str(from_user.get("id", "")) if from_user.get("id") else None
        username = from_user.get("username") or from_user.get("first_name")

        return InboundMessage(
            channel_id=config.get("channel_id", ""),
            chat_id=chat_id,
            channel_type="telegram",
            user_id=user_id,
            username=username,
            text=text,
            raw=update,
        )
