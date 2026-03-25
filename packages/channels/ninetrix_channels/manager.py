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
    ) -> None:
        """Initialize the ChannelManager.

        Args:
            channel_config: {channel_type: config_dict} e.g.
                {"telegram": {"bot_token": "...", "chat_id": "..."}}
            on_message: async callback (text, session_id, channel_type, username) -> response
            session_mode: "per_chat" or "per_message"
        """
        self._config = channel_config
        self._on_message = on_message
        self._session_mode = session_mode
        self._tasks: dict[str, asyncio.Task] = {}
        self._adapters: dict[str, Any] = {}

    async def start(self) -> None:
        """Start all configured channel adapters."""
        for channel_type, config in self._config.items():
            adapter = get_adapter(channel_type)
            if adapter is None:
                logger.warning("No adapter registered for channel type: %s", channel_type)
                continue

            if adapter.connection_mode != "persistent":
                logger.info(
                    "Skipping %s — webhook mode (handled externally)", channel_type
                )
                continue

            self._adapters[channel_type] = adapter

            async def _on_msg(msg: InboundMessage, _adapter=adapter, _config=config, _ctype=channel_type) -> None:
                await self._handle_message(msg, _adapter, _config, _ctype)

            task = asyncio.create_task(
                self._run_adapter(channel_type, adapter, config, _on_msg),
                name=f"channel-{channel_type}",
            )
            self._tasks[channel_type] = task
            logger.info("Started %s channel adapter (persistent mode)", channel_type)

    async def _run_adapter(self, channel_type: str, adapter: Any, config: dict, on_msg: Any) -> None:
        """Run a single adapter with automatic reconnect on failure."""
        retry_delay = 5
        max_retry_delay = 60

        while True:
            try:
                await adapter.connect(config, on_msg)
                # connect() should run forever — if it returns, reconnect
                logger.warning("%s adapter returned unexpectedly — reconnecting", channel_type)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("%s adapter error — reconnecting in %ds", channel_type, retry_delay)

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
        # Determine session_id for conversation continuity
        if self._session_mode == "per_chat":
            session_id = msg.chat_id
        else:
            session_id = uuid.uuid4().hex

        username = msg.username or msg.user_id or "unknown"
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
                await adapter.send_message(config, msg.chat_id, chunk)
            except Exception:
                logger.exception("Failed to send message via %s", channel_type)

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
