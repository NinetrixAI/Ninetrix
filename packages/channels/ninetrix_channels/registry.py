"""Adapter registry — maps channel_type strings to adapter instances."""
from __future__ import annotations

from ninetrix_channels.base import ChannelAdapter

adapter_registry: dict[str, ChannelAdapter] = {}


def register_adapter(adapter: ChannelAdapter) -> None:
    adapter_registry[adapter.channel_type] = adapter


def get_adapter(channel_type: str) -> ChannelAdapter | None:
    return adapter_registry.get(channel_type)


def _register_builtins() -> None:
    """Import and register built-in adapters."""
    from ninetrix_channels.telegram import TelegramAdapter
    register_adapter(TelegramAdapter())

    from ninetrix_channels.discord_adapter import DiscordAdapter
    register_adapter(DiscordAdapter())

    from ninetrix_channels.whatsapp import WhatsAppAdapter
    register_adapter(WhatsAppAdapter())


_register_builtins()
