"""Discord channel adapter.

Uses discord.py to maintain a persistent gateway WebSocket connection.
Always runs in persistent mode — Discord does not support stateless webhooks
for bot message handling.

Requires: pip install discord.py>=2.3
"""
from __future__ import annotations

import asyncio
import logging
from typing import Literal

from ninetrix_channels.base import ChannelAdapter, InboundMessage, MessageCallback

logger = logging.getLogger(__name__)


class DiscordAdapter(ChannelAdapter):
    channel_type = "discord"

    def __init__(self) -> None:
        self._running = False
        self._client = None

    @property
    def connection_mode(self) -> Literal["webhook", "persistent"]:
        return "persistent"

    # ── Required methods ──────────────────────────────────────────────────

    async def validate_config(self, config: dict) -> tuple[bool, str]:
        bot_token = config.get("bot_token", "").strip()
        if not bot_token:
            return False, "bot_token is required"
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://discord.com/api/v10/users/@me",
                    headers={"Authorization": f"Bot {bot_token}"},
                )
                if resp.status_code != 200:
                    return False, f"Invalid Discord bot token (HTTP {resp.status_code})"
        except Exception as exc:
            return False, f"Failed to validate Discord token: {exc}"
        return True, ""

    async def send_message(self, config: dict, chat_id: str, text: str) -> bool:
        """Send a message to a Discord channel by ID."""
        if self._client is None:
            # Fallback: use HTTP API directly if client not connected
            return await self._send_via_http(config, chat_id, text)

        try:
            channel = self._client.get_channel(int(chat_id))
            if channel is None:
                # Channel not in cache — try fetching it
                channel = await self._client.fetch_channel(int(chat_id))
            if channel is not None:
                for chunk in _split_message(text, 2000):
                    await channel.send(chunk)
                return True
            logger.error("Discord channel %s not found", chat_id)
            return False
        except Exception:
            logger.exception("Discord send_message failed for channel %s", chat_id)
            return False

    async def get_bot_info(self, config: dict) -> dict:
        bot_token = config.get("bot_token", "")
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://discord.com/api/v10/users/@me",
                    headers={"Authorization": f"Bot {bot_token}"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return {
                        "id": data.get("id", ""),
                        "username": data.get("username", ""),
                        "discriminator": data.get("discriminator", ""),
                        "bot": data.get("bot", True),
                    }
        except Exception:
            logger.exception("Failed to get Discord bot info")
        return {}

    # ── Persistent mode ───────────────────────────────────────────────────

    async def connect(
        self, config: dict, on_message: MessageCallback,
    ) -> None:
        """Start the discord.py client and listen for messages.

        Runs indefinitely until disconnect() is called.
        """
        try:
            import discord
        except ImportError:
            raise ImportError(
                "discord.py is required for the Discord adapter. "
                "Install it with: pip install discord.py>=2.3"
            )

        bot_token = config.get("bot_token", "")
        if not bot_token:
            raise ValueError("bot_token is required for Discord connect()")

        # Optional: restrict to specific guild IDs
        guild_ids = config.get("guild_ids", [])

        intents = discord.Intents.default()
        intents.message_content = True

        client = discord.Client(intents=intents)
        self._client = client
        self._running = True

        # Capture the callback before defining the event handler to avoid
        # name shadowing — discord.py requires the handler to be named
        # "on_message", which would shadow the parameter.
        _msg_callback = on_message

        @client.event
        async def on_ready():
            print(
                f"[discord] Bot connected: {client.user} "
                f"(guilds: {len(client.guilds)})",
                flush=True,
            )

        @client.event
        async def on_message(message):
            # Ignore own messages
            if message.author == client.user:
                return

            # Ignore bot messages
            if message.author.bot:
                return

            # Optional guild filter
            if guild_ids and message.guild:
                if str(message.guild.id) not in guild_ids:
                    return

            # In DMs, always respond.
            # In guild channels, only respond when mentioned.
            if message.guild is not None:
                mentioned = client.user in message.mentions
                if not mentioned:
                    return
            else:
                mentioned = False

            # Extract text content
            text = (message.content or "").strip()

            # Strip the mention from the message text
            if mentioned and text:
                text = text.replace(f"<@{client.user.id}>", "").strip()
                text = text.replace(f"<@!{client.user.id}>", "").strip()

            if not text:
                # Most likely: Message Content Intent not enabled in Discord
                # Developer Portal. The bot sees the mention but content is empty.
                if mentioned:
                    print(
                        "[discord] Received mention but message content is empty. "
                        "Enable MESSAGE CONTENT INTENT in Discord Developer Portal → Bot → Privileged Gateway Intents.",
                        flush=True,
                    )
                return

            msg = InboundMessage(
                channel_id=config.get("channel_id", ""),
                chat_id=str(message.channel.id),
                channel_type="discord",
                user_id=str(message.author.id),
                username=str(message.author),
                text=text,
                raw={
                    "guild_id": str(message.guild.id) if message.guild else None,
                    "channel_name": getattr(message.channel, "name", "DM"),
                },
            )

            try:
                await _msg_callback(msg)
            except Exception:
                logger.exception("Error in Discord message callback")

        try:
            await client.start(bot_token)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            err_msg = str(exc)
            if "Improper token" in err_msg or "401" in err_msg or "Unauthorized" in err_msg:
                print(
                    f"[discord] Invalid bot token. Reset it in Discord Developer Portal "
                    f"and reconnect: ninetrix channel connect discord --bot <name>",
                    flush=True,
                )
                # Don't retry on auth errors — raise RuntimeError to stop retries
                raise RuntimeError(f"Discord auth failed: {err_msg}") from exc
            logger.exception("Discord client error")
            raise
        finally:
            if not client.is_closed():
                await client.close()

    async def disconnect(self) -> None:
        self._running = False
        if self._client and not self._client.is_closed():
            await self._client.close()
            self._client = None

    # ── Helpers ───────────────────────────────────────────────────────────

    async def _send_via_http(self, config: dict, chat_id: str, text: str) -> bool:
        """Send a message using the Discord HTTP API (fallback when client not connected)."""
        bot_token = config.get("bot_token", "")
        if not bot_token:
            return False
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                for chunk in _split_message(text, 2000):
                    resp = await client.post(
                        f"https://discord.com/api/v10/channels/{chat_id}/messages",
                        headers={"Authorization": f"Bot {bot_token}"},
                        json={"content": chunk},
                    )
                    if resp.status_code not in (200, 201):
                        logger.error("Discord HTTP send failed: %s", resp.text[:300])
                        return False
            return True
        except Exception:
            logger.exception("Discord HTTP send failed")
            return False


def _split_message(text: str, max_len: int = 2000) -> list[str]:
    """Split a long message into chunks that fit Discord's 2000 char limit."""
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        # Try to split at a newline
        cut = text.rfind("\n", 0, max_len)
        if cut < max_len // 2:
            cut = max_len
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks
