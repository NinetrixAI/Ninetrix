"""ninetrix channel — connect messaging platforms to your agents.

Commands:
  ninetrix channel connect telegram    interactive Telegram bot setup
  ninetrix channel disconnect telegram remove Telegram configuration
  ninetrix channel status              show connected channels
"""
from __future__ import annotations

import time

import click
import httpx
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from agentfile.core.channel_config import (
    get_channel, save_channel, remove_channel, is_configured, is_verified,
)

console = Console()

_TG_API = "https://api.telegram.org/bot{token}"


def _validate_telegram_token(token: str) -> dict | None:
    """Validate a Telegram bot token. Returns bot info dict or None."""
    try:
        resp = httpx.get(f"{_TG_API.format(token=token)}/getMe", timeout=10)
        if resp.status_code == 200:
            return resp.json().get("result", {})
    except Exception:
        pass
    return None


def _delete_telegram_webhook(token: str) -> None:
    """Delete any existing webhook (required for polling mode)."""
    try:
        httpx.post(
            f"{_TG_API.format(token=token)}/deleteWebhook",
            json={"drop_pending_updates": False},
            timeout=10,
        )
    except Exception:
        pass


def _send_telegram_message(token: str, chat_id: str, text: str) -> bool:
    try:
        resp = httpx.post(
            f"{_TG_API.format(token=token)}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception:
        return False


def _poll_for_messages(token: str, timeout: int = 30) -> list[dict]:
    """Poll Telegram getUpdates once with long-polling."""
    try:
        resp = httpx.get(
            f"{_TG_API.format(token=token)}/getUpdates",
            params={"timeout": timeout, "allowed_updates": ["message"]},
            timeout=timeout + 10,
        )
        if resp.status_code == 200:
            return resp.json().get("result", [])
    except Exception:
        pass
    return []


def _register_with_api(
    token: str,
    bot_username: str,
    chat_id: str,
    agent_name: str | None,
) -> bool:
    """Register the channel with the Ninetrix API (local or cloud).

    This creates the channel in the API DB, sets up the webhook (for cloud),
    and binds the agent. Works with both local API and saas-api.
    Returns True if registration succeeded.
    """
    from agentfile.core.config import resolve_api_url
    from agentfile.core.auth import auth_headers

    api_url = resolve_api_url()
    headers = auth_headers(api_url)
    if not headers:
        # No auth available — skip API registration (local-only mode)
        return False

    try:
        # 1. Create channel
        resp = httpx.post(
            f"{api_url}/v1/channels",
            headers=headers,
            json={
                "channel_type": "telegram",
                "name": f"@{bot_username}",
                "config": {"bot_token": token},
                "session_mode": "per_chat",
                "routing_mode": "single",
            },
            timeout=15,
        )
        if resp.status_code not in (200, 201):
            console.print(f"  [dim]API registration: {resp.status_code} — {resp.text[:100]}[/dim]")
            return False

        channel_data = resp.json()
        channel_id = channel_data.get("id")

        # 2. Verify channel (send the code we already verified via polling)
        httpx.post(
            f"{api_url}/v1/channels/{channel_id}/verify",
            headers=headers,
            json={"code": channel_data.get("config", {}).get("verification_code", "000000")},
            timeout=10,
        )

        # 3. Bind agent if name provided
        if agent_name:
            httpx.post(
                f"{api_url}/v1/channels/{channel_id}/agents",
                headers=headers,
                json={
                    "agent_name": agent_name,
                    "is_default": True,
                },
                timeout=10,
            )

        is_cloud = "ninetrix.io" in api_url or "localhost:8001" in api_url
        mode = "cloud (webhook)" if is_cloud else "local (polling)"
        console.print(f"  [dim]Registered with API → {mode}[/dim]")
        return True

    except httpx.ConnectError:
        # API not running — that's fine for local-only use
        return False
    except Exception as exc:
        console.print(f"  [dim]API registration skipped: {exc}[/dim]")
        return False


def setup_telegram_interactive(agent_name: str | None = None) -> bool:
    """Interactive Telegram setup flow. Returns True if verified successfully."""
    console.print()
    console.print(Panel(
        "[bold]Connect Telegram[/bold]\n\n"
        "This will connect a Telegram bot so people can message\n"
        "your agent directly from Telegram.",
        border_style="purple",
        width=60,
    ))
    console.print()

    # Check if already configured
    existing = get_channel("telegram")
    if existing and existing.get("verified"):
        console.print(f"  [green]✓[/green] Telegram already connected: [bold]@{existing.get('bot_username', '?')}[/bold]")
        reconfig = Prompt.ask("  Reconfigure?", choices=["y", "n"], default="n")
        if reconfig == "n":
            return True
        console.print()

    # Step 1: Get bot token
    console.print("  [bold]Step 1:[/bold] Create a Telegram bot\n")
    console.print("    1. Open Telegram and search for [bold]@BotFather[/bold]")
    console.print("    2. Send [bold]/newbot[/bold]")
    console.print("    3. Pick a name and username for your bot")
    console.print("    4. Copy the bot token BotFather gives you")
    console.print()

    while True:
        token = Prompt.ask("  [bold]Paste your bot token[/bold]").strip()
        if not token:
            console.print("  [red]Token cannot be empty[/red]")
            continue

        console.print("  [dim]Validating...[/dim]", end="")
        bot_info = _validate_telegram_token(token)
        if bot_info:
            console.print(f"\r  [green]✓[/green] Bot verified: [bold]@{bot_info.get('username', '?')}[/bold]   ")
            break
        else:
            console.print("\r  [red]✗[/red] Invalid token. Make sure you copied the full token from BotFather.   ")
            retry = Prompt.ask("  Try again?", choices=["y", "n"], default="y")
            if retry == "n":
                return False

    bot_username = bot_info.get("username", "")

    # Delete any existing webhook so polling works (for local mode)
    _delete_telegram_webhook(token)

    # Save config immediately (even before verification)
    save_channel("telegram", {
        "bot_token": token,
        "bot_username": bot_username,
        "verified": False,
    })

    # Step 2: Verify by having user send /start
    console.print()
    console.print(f"  [bold]Step 2:[/bold] Open [bold]@{bot_username}[/bold] in Telegram and send [bold]/start[/bold]")
    console.print()
    console.print("  [dim]Waiting for your message...[/dim]", end="", highlight=False)

    # Poll for the /start message
    verified = False
    chat_id = None
    max_wait = 120  # 2 minutes
    start_time = time.time()

    while time.time() - start_time < max_wait:
        updates = _poll_for_messages(token, timeout=5)
        for update in updates:
            msg = update.get("message", {})
            text = (msg.get("text") or "").strip()
            if text.startswith("/start"):
                chat_id = str(msg["chat"]["id"])
                verified = True
                break
        if verified:
            break

    if not verified:
        console.print(f"\r  [yellow]⏱[/yellow] Timed out waiting for /start. You can retry with: [bold]ninetrix channel connect telegram[/bold]   ")
        return False

    # Acknowledge the update offset so it doesn't replay
    _poll_for_messages(token, timeout=0)

    # Send confirmation to Telegram
    if agent_name:
        welcome = (
            f"Hey there! I'm *{agent_name}*.\n\n"
            "All set up and ready to go. Just send me a message whenever you need anything."
        )
    else:
        welcome = "Hey there! All set up and ready to go. Send me a message whenever you need anything."
    _send_telegram_message(token, chat_id, welcome)

    console.print(f"\r  [green]✓[/green] Connected! Chat ID: [bold]{chat_id}[/bold]                       ")

    # Save verified config locally
    save_channel("telegram", {
        "bot_token": token,
        "bot_username": bot_username,
        "chat_id": chat_id,
        "verified": True,
    })

    # Register with API (local or cloud) — creates channel + binds agent
    _register_with_api(token, bot_username, chat_id, agent_name)

    console.print()
    console.print(f"  [green]Done![/green] Messages to [bold]@{bot_username}[/bold] will trigger your agent.")
    console.print(f"  [dim]Config saved to ~/.agentfile/channels.yaml[/dim]")
    console.print()

    return True


# ── CLI commands ─────────────────────────────────────────────────────────────

@click.group("channel")
def channel_cmd():
    """Connect messaging platforms (Telegram, WhatsApp) to your agents."""
    pass


@channel_cmd.command("connect")
@click.argument("platform", type=click.Choice(["telegram"]))
@click.option("--agent", "-a", default=None, help="Agent name to bind to this channel")
def connect(platform: str, agent: str | None):
    """Connect a messaging platform to your agents."""
    if platform == "telegram":
        setup_telegram_interactive(agent_name=agent)


@channel_cmd.command("disconnect")
@click.argument("platform", type=click.Choice(["telegram"]))
def disconnect(platform: str):
    """Remove a messaging platform connection."""
    ch = get_channel(platform)
    if not ch:
        console.print(f"  [dim]{platform} is not connected.[/dim]")
        return

    # Delete webhook before removing config
    if platform == "telegram" and ch.get("bot_token"):
        _delete_telegram_webhook(ch["bot_token"])

    remove_channel(platform)
    console.print(f"  [green]✓[/green] {platform} disconnected.")
    console.print(f"  [dim]Removed from ~/.agentfile/channels.yaml[/dim]")


@channel_cmd.command("status")
def status():
    """Show connected channels."""
    console.print()
    console.print("[bold]Channels[/bold]\n")

    found = False
    for platform in ["telegram", "whatsapp"]:
        ch = get_channel(platform)
        if ch:
            found = True
            verified = ch.get("verified", False)
            status_str = "[green]✓ verified[/green]" if verified else "[yellow]⚠ not verified[/yellow]"
            bot = ch.get("bot_username", "?")
            console.print(f"  {platform:12s}  @{bot:20s}  {status_str}")

    if not found:
        console.print("  [dim]No channels connected.[/dim]")
        console.print("  [dim]Run: ninetrix channel connect telegram[/dim]")

    console.print()
