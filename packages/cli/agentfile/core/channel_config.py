"""Persistent channel configuration stored in ~/.agentfile/channels.yaml.

Stores bot tokens and channel metadata locally so the CLI can auto-configure
channels without requiring the API to be running.

File format:
  telegram:
    bot_token: "123456:ABC..."
    bot_username: "my_bot"
    chat_id: "987654321"         # set after verification
    verified: true
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_CONFIG_DIR = Path.home() / ".agentfile"
_CHANNELS_FILE = _CONFIG_DIR / "channels.yaml"


def _load() -> dict[str, Any]:
    if not _CHANNELS_FILE.exists():
        return {}
    try:
        with open(_CHANNELS_FILE) as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data: dict[str, Any]) -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(_CHANNELS_FILE, "w") as f:
        yaml.dump(data, f, default_flow_style=False)
    _CHANNELS_FILE.chmod(0o600)


def get_channel(channel_type: str) -> dict[str, Any] | None:
    """Return saved config for a channel type, or None."""
    data = _load()
    return data.get(channel_type)


def save_channel(channel_type: str, config: dict[str, Any]) -> None:
    """Save config for a channel type."""
    data = _load()
    data[channel_type] = config
    _save(data)


def remove_channel(channel_type: str) -> None:
    """Remove a channel config."""
    data = _load()
    data.pop(channel_type, None)
    _save(data)


def is_configured(channel_type: str) -> bool:
    """Return True if a channel is configured (has a bot token)."""
    ch = get_channel(channel_type)
    return bool(ch and ch.get("bot_token"))


def is_verified(channel_type: str) -> bool:
    """Return True if a channel is configured AND verified."""
    ch = get_channel(channel_type)
    return bool(ch and ch.get("verified"))
