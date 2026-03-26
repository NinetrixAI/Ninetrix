"""Persistent channel configuration stored in ~/.agentfile/channels.yaml.

Stores bot tokens and channel metadata locally so the CLI can auto-configure
channels without requiring the API to be running.

Named bot format (v2):
  support_bot:
    channel_type: telegram
    bot_token: "123456:ABC..."
    bot_username: "support_bot"
    chat_id: "987654321"
    verified: true
  community_discord:
    channel_type: discord
    bot_token: "MTIz..."
    bot_username: "CommunityBot"
    verified: true

Legacy format (v1 — auto-migrated on load):
  telegram:
    bot_token: "123456:ABC..."
    ...
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_CONFIG_DIR = Path.home() / ".agentfile"
_CHANNELS_FILE = _CONFIG_DIR / "channels.yaml"

# Channel types we know about — used to detect legacy format
_KNOWN_CHANNEL_TYPES = {"telegram", "discord", "whatsapp"}


def _load() -> dict[str, Any]:
    if not _CHANNELS_FILE.exists():
        return {}
    try:
        with open(_CHANNELS_FILE) as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            return {}
        return _migrate_legacy(data)
    except Exception:
        return {}


def _migrate_legacy(data: dict[str, Any]) -> dict[str, Any]:
    """Migrate legacy format (keyed by channel_type) to named bot format.

    Legacy:  {"telegram": {"bot_token": "...", "verified": true}}
    Named:   {"telegram": {"channel_type": "telegram", "bot_token": "...", "verified": true}}

    Detection: if a top-level key is a known channel type AND the value
    doesn't already have a "channel_type" field, it's legacy format.
    """
    migrated = False
    for key in list(data.keys()):
        val = data[key]
        if isinstance(val, dict) and key in _KNOWN_CHANNEL_TYPES and "channel_type" not in val:
            val["channel_type"] = key
            migrated = True
    if migrated:
        _save(data)
    return data


def _save(data: dict[str, Any]) -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(_CHANNELS_FILE, "w") as f:
        yaml.dump(data, f, default_flow_style=False)
    _CHANNELS_FILE.chmod(0o600)


# ── Named bot API (v2) ───────────────────────────────────────────────────

def get_bot(bot_name: str) -> dict[str, Any] | None:
    """Return saved config for a named bot, or None."""
    data = _load()
    return data.get(bot_name)


def save_bot(bot_name: str, config: dict[str, Any]) -> None:
    """Save config for a named bot. Must include channel_type."""
    data = _load()
    data[bot_name] = config
    _save(data)


def remove_bot(bot_name: str) -> None:
    """Remove a named bot config."""
    data = _load()
    data.pop(bot_name, None)
    _save(data)


def list_bots() -> dict[str, dict[str, Any]]:
    """Return all configured bots: {bot_name: config}."""
    return _load()


def is_bot_verified(bot_name: str) -> bool:
    """Return True if a named bot is configured AND verified."""
    bot = get_bot(bot_name)
    return bool(bot and bot.get("verified"))


def find_bots_by_type(channel_type: str) -> dict[str, dict[str, Any]]:
    """Return all bots of a given channel type."""
    return {
        name: cfg
        for name, cfg in _load().items()
        if isinstance(cfg, dict) and cfg.get("channel_type") == channel_type
    }


# ── Backward-compatible API (delegates to named bot API) ─────────────────
# These use channel_type as the bot name for single-bot setups.

def get_channel(channel_type: str) -> dict[str, Any] | None:
    """Return saved config for a channel type (legacy compat).

    Looks up by channel_type as bot name first, then searches all bots
    for a matching channel_type field.
    """
    # Direct lookup (legacy format or bot named after channel type)
    bot = get_bot(channel_type)
    if bot:
        return bot
    # Search by channel_type field
    bots = find_bots_by_type(channel_type)
    if bots:
        return next(iter(bots.values()))
    return None


def save_channel(channel_type: str, config: dict[str, Any]) -> None:
    """Save config using channel_type as bot name (legacy compat)."""
    config.setdefault("channel_type", channel_type)
    save_bot(channel_type, config)


def remove_channel(channel_type: str) -> None:
    """Remove channel config (legacy compat)."""
    remove_bot(channel_type)


def is_configured(channel_type: str) -> bool:
    """Return True if a channel is configured (has a bot token)."""
    ch = get_channel(channel_type)
    return bool(ch and (ch.get("bot_token") or ch.get("auth_dir")))


def is_verified(channel_type: str) -> bool:
    """Return True if a channel is configured AND verified."""
    ch = get_channel(channel_type)
    return bool(ch and ch.get("verified"))
