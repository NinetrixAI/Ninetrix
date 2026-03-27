"""Anonymous CLI telemetry via PostHog.

Tracks command usage (build, run, deploy, etc.) to understand how the CLI
is used. Never tracks content, secrets, or personally identifiable information.

Opt-out: `ninetrix telemetry off` or set NINETRIX_TELEMETRY=off env var.
Config stored in ~/.agentfile/config.json under "telemetry_enabled".
"""
from __future__ import annotations

import hashlib
import os
import platform
import uuid
from pathlib import Path

_POSTHOG_KEY = "phc_O6KUyRgueCHGzW0s0D9taTdJaYOb8AWhXI2uZgIXeab"
_POSTHOG_HOST = "https://eu.i.posthog.com"
_CONFIG_FILE = Path.home() / ".agentfile" / "config.json"

# Cached state
_client = None
_distinct_id: str | None = None
_enabled: bool | None = None


def _is_enabled() -> bool:
    """Check if telemetry is enabled. Cached after first call."""
    global _enabled
    if _enabled is not None:
        return _enabled

    # Env var overrides everything
    env = os.environ.get("NINETRIX_TELEMETRY", "").lower()
    if env in ("off", "false", "0", "no"):
        _enabled = False
        return False

    # Check config file
    try:
        import json
        if _CONFIG_FILE.exists():
            data = json.loads(_CONFIG_FILE.read_text())
            if data.get("telemetry_enabled") is False:
                _enabled = False
                return False
    except Exception:
        pass

    _enabled = True
    return True


def set_enabled(enabled: bool) -> None:
    """Enable or disable telemetry. Saves to config file."""
    global _enabled
    _enabled = enabled

    import json
    _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if _CONFIG_FILE.exists():
        try:
            data = json.loads(_CONFIG_FILE.read_text())
        except Exception:
            pass
    data["telemetry_enabled"] = enabled
    _CONFIG_FILE.write_text(json.dumps(data, indent=2))


def _get_distinct_id() -> str:
    """Get or create an anonymous, stable machine ID."""
    global _distinct_id
    if _distinct_id:
        return _distinct_id

    # Try to read stored ID
    id_file = Path.home() / ".agentfile" / ".telemetry-id"
    if id_file.exists():
        _distinct_id = id_file.read_text().strip()
        if _distinct_id:
            return _distinct_id

    # Generate a new anonymous ID (hash of random UUID — not reversible)
    raw = uuid.uuid4().hex
    _distinct_id = hashlib.sha256(raw.encode()).hexdigest()[:16]
    id_file.parent.mkdir(parents=True, exist_ok=True)
    id_file.write_text(_distinct_id)
    return _distinct_id


def _get_client():
    """Lazy-init the PostHog client."""
    global _client
    if _client is not None:
        return _client

    try:
        from posthog import Posthog
        _client = Posthog(
            project_api_key=_POSTHOG_KEY,
            host=_POSTHOG_HOST,
            # CLI is short-lived — sync mode sends immediately instead of
            # queueing in a background thread that dies when the process exits.
            sync_mode=True,
        )
        _client.debug = False
        return _client
    except ImportError:
        return None


def track(event: str, properties: dict | None = None) -> None:
    """Track a CLI event. Non-blocking, never raises."""
    if not _is_enabled():
        return

    try:
        client = _get_client()
        if not client:
            return

        props = {
            "cli_version": _get_cli_version(),
            "os": platform.system(),
            "os_version": platform.release(),
            "python_version": platform.python_version(),
            "source": "cli",
        }
        if properties:
            props.update(properties)

        client.capture(
            distinct_id=_get_distinct_id(),
            event=event,
            properties=props,
        )
    except Exception:
        pass  # never let telemetry break the CLI


def identify(user_email: str = "", org_id: str = "") -> None:
    """Link the anonymous ID to a user after auth. Non-blocking."""
    if not _is_enabled():
        return

    try:
        client = _get_client()
        if not client:
            return

        props: dict = {"source": "cli"}
        if user_email:
            props["email"] = user_email
        if org_id:
            props["org_id"] = org_id

        client.identify(
            distinct_id=_get_distinct_id(),
            properties=props,
        )
    except Exception:
        pass


def shutdown() -> None:
    """Flush pending events. Call before CLI exits."""
    try:
        if _client:
            _client.flush()
    except Exception:
        pass


def _get_cli_version() -> str:
    try:
        from importlib.metadata import version
        return version("ninetrix")
    except Exception:
        return "unknown"
