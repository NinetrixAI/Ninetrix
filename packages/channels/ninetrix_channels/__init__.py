"""Ninetrix Channels — pluggable inbound messaging adapters."""
from __future__ import annotations

from ninetrix_channels.base import ChannelAdapter, InboundMessage, MessageCallback
from ninetrix_channels.manager import ChannelManager
from ninetrix_channels.registry import adapter_registry, get_adapter

__all__ = [
    "ChannelAdapter",
    "ChannelManager",
    "InboundMessage",
    "MessageCallback",
    "adapter_registry",
    "get_adapter",
]
