"""Channel Manager — runs inside agent containers.

Manages persistent channel connections and bridges inbound messages to the
agent runtime. Started by the generated entrypoint.py when channel triggers
are configured in agentfile.yaml.

Usage (inside generated entrypoint):
    manager = ChannelManager(channel_config, on_message=handle_channel_msg)
    await manager.start()
    # ... agent runs ...
    await manager.stop()
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Awaitable, Callable

from ninetrix_channels.base import InboundMessage
from ninetrix_channels.registry import get_adapter

logger = logging.getLogger(__name__)

# Callback: (message_text, session_id, channel_type, username) -> response_text
AgentCallback = Callable[[str, str, str, str], Awaitable[str]]


def _split_message(text: str, max_len: int = 4000) -> list[str]:
    """Split a long message into chunks that fit platform limits."""
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        cut = text.rfind("\n", 0, max_len)
        if cut < max_len // 2:
            cut = max_len
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


# Platform-specific message length limits
_MAX_MSG_LEN: dict[str, int] = {
    "telegram": 4000,   # Telegram limit is 4096
    "discord": 1900,    # Discord limit is 2000
    "whatsapp": 4000,   # WhatsApp practical limit
}


class ChannelManager:
    """Manages all channel adapters inside a container.

    Each configured channel gets a persistent adapter that connects to the
    platform and forwards inbound messages to the agent via the callback.
    Responses are sent back through the same adapter.
    """

    def __init__(
        self,
        channel_config: dict[str, dict[str, Any]],
        on_message: AgentCallback,
        *,
        session_mode: str = "per_chat",
        allowed_ids: list[str] | None = None,
        reject_message: str = "",
    ) -> None:
        """Initialize the ChannelManager.

        Args:
            channel_config: {channel_type: config_dict} e.g.
                {"telegram": {"bot_token": "...", "chat_id": "..."}}
            on_message: async callback (text, session_id, channel_type, username) -> response
            session_mode: "per_chat" or "per_message"
            allowed_ids: if set, only these user/chat IDs get responses (allowlist)
            reject_message: optional message sent to blocked users (empty = silent ignore)
        """
        self._config = channel_config
        self._on_message = on_message
        self._session_mode = session_mode
        self._allowed_ids: set[str] | None = set(allowed_ids) if allowed_ids else None
        self._reject_message = reject_message
        self._tasks: dict[str, asyncio.Task] = {}
        self._adapters: dict[str, Any] = {}

    async def start(self) -> None:
        """Start all configured channel adapters.

        Config is keyed by bot_name, with channel_type inside each entry.
        Multiple bots of the same channel type are supported — each gets
        its own adapter instance.
        """
        for bot_name, config in self._config.items():
            channel_type = config.get("channel_type", bot_name)
            template_adapter = get_adapter(channel_type)
            if template_adapter is None:
                print(f"[channel] No adapter for type: {channel_type} (bot: {bot_name})", flush=True)
                continue

            if template_adapter.connection_mode != "persistent":
                print(f"[channel] Skipping {bot_name} — webhook mode", flush=True)
                continue

            # Create a FRESH adapter instance per bot (not the singleton from registry)
            # so multiple bots of the same type can run independently.
            adapter = type(template_adapter)()
            self._adapters[bot_name] = adapter

            async def _on_msg(msg: InboundMessage, _adapter=adapter, _config=config, _ctype=channel_type) -> None:
                await self._handle_message(msg, _adapter, _config, _ctype)

            task = asyncio.create_task(
                self._run_adapter(bot_name, adapter, config, _on_msg),
                name=f"channel-{bot_name}",
            )
            self._tasks[bot_name] = task
            print(f"[channel] Started {bot_name} ({channel_type})", flush=True)

    async def _run_adapter(self, channel_type: str, adapter: Any, config: dict, on_msg: Any) -> None:
        """Run a single adapter with automatic reconnect on failure."""
        retry_delay = 5
        max_retry_delay = 60
        max_retries = 5
        retries = 0

        while retries < max_retries:
            try:
                await adapter.connect(config, on_msg)
                # connect() should run forever — if it returns, reconnect
                print(f"[{channel_type}] adapter returned unexpectedly — reconnecting", flush=True)
                retries += 1
            except asyncio.CancelledError:
                raise
            except RuntimeError as exc:
                # Fatal errors (e.g. bridge process died) — don't retry endlessly
                print(f"[{channel_type}] fatal error: {exc}", flush=True)
                retries += 1
            except Exception as exc:
                print(f"[{channel_type}] adapter error — reconnecting in {retry_delay}s: {exc}", flush=True)
                retries += 1

            if retries >= max_retries:
                print(
                    f"[{channel_type}] giving up after {max_retries} retries. "
                    f"Re-pair with: ninetrix channel connect {channel_type}",
                    flush=True,
                )
                return

            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_retry_delay)

    async def _handle_message(
        self,
        msg: InboundMessage,
        adapter: Any,
        config: dict,
        channel_type: str,
    ) -> None:
        """Route an inbound message to the agent and send the response back."""
        username = msg.username or msg.user_id or "unknown"

        # Access control — check if this user/chat is allowed
        if self._allowed_ids is not None:
            # Check user_id, chat_id, and username against allowlist
            sender_ids = {msg.chat_id, msg.user_id or "", msg.username or ""}

            if msg.channel_type == "whatsapp":
                # WhatsApp uses LID format internally (@lid). We can't reliably
                # resolve LID → phone for all contacts. Instead: if the agent's
                # own phone number is in allowed_ids, allow all messages through
                # this WhatsApp connection (the user authorized this account).
                _wa_phone = msg.raw.get("phone", "") if isinstance(msg.raw, dict) else ""
                if _wa_phone:
                    sender_ids.add(_wa_phone)
                # Strip @domain and :device from JIDs
                for _sid in (msg.chat_id, msg.user_id or ""):
                    if "@" in _sid:
                        _local = _sid.split("@")[0]
                        sender_ids.add(_local)
                        sender_ids.add(_local.split(":")[0])
                # Check if the agent's connected phone is in allowed_ids —
                # if so, this WhatsApp account is authorized to receive messages
                # from anyone (LID resolution is unreliable for per-sender filtering).
                # Find any WhatsApp bot config to get the connected phone
                _wa_connected = config.get("connected_phone", "")
                if not _wa_connected:
                    for _bcfg in self._config.values():
                        if isinstance(_bcfg, dict) and _bcfg.get("channel_type") == "whatsapp":
                            _wa_connected = _bcfg.get("connected_phone", "")
                            if _wa_connected:
                                break
                if _wa_connected and _wa_connected in self._allowed_ids:
                    # Agent's phone is in allowed_ids → allow all WhatsApp messages
                    sender_ids.add(_wa_connected)
            if not sender_ids & self._allowed_ids:
                # Print helpful message showing which ID to add
                _clean_ids = {s for s in sender_ids if s and s != ""}
                # Suggest the most likely ID to add
                _suggest = ""
                if channel_type == "telegram":
                    _suggest = f'  Hint: add "{msg.chat_id}" to allowed_ids for this Telegram user'
                elif channel_type == "discord":
                    _suggest = f'  Hint: add "{msg.user_id}" to allowed_ids for this Discord user'
                elif channel_type == "whatsapp":
                    _wa_ph = msg.raw.get("phone", "") if isinstance(msg.raw, dict) else ""
                    _suggest = f'  Hint: add "{_wa_ph or msg.chat_id}" to allowed_ids for this WhatsApp user'
                print(
                    f"[{channel_type}] Blocked: @{username} "
                    f"(IDs: {_clean_ids} not in allowed_ids: {self._allowed_ids})\n{_suggest}",
                    flush=True,
                )
                if self._reject_message:
                    try:
                        await adapter.send_message(config, msg.chat_id, self._reject_message)
                    except Exception:
                        pass
                return

        # Determine session_id for conversation continuity
        if self._session_mode == "per_chat":
            session_id = msg.chat_id
        else:
            session_id = uuid.uuid4().hex

        logger.info("[%s] @%s: %s", channel_type, username, msg.text[:80])

        try:
            response = await self._on_message(
                msg.text, session_id, channel_type, username,
            )
        except Exception:
            logger.exception("Agent error processing message from %s", channel_type)
            response = "Something went wrong processing your message."

        if not response or response == "(no response)":
            response = "No response from agent."

        # Split long messages for platform limits
        max_len = _MAX_MSG_LEN.get(channel_type, 4000)
        for chunk in _split_message(response, max_len):
            try:
                ok = await adapter.send_message(config, msg.chat_id, chunk)
                if not ok:
                    print(f"[{channel_type}] send_message returned False for chat_id={msg.chat_id}", flush=True)
            except Exception as exc:
                print(f"[{channel_type}] send_message failed: {exc}", flush=True)

    async def stop(self) -> None:
        """Disconnect all adapters and cancel their tasks."""
        for channel_type, adapter in self._adapters.items():
            try:
                await adapter.disconnect()
            except Exception:
                logger.warning("Error disconnecting %s", channel_type, exc_info=True)

        for channel_type, task in self._tasks.items():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self._tasks.clear()
        self._adapters.clear()
        logger.info("Channel manager stopped")
