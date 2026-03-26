"""ninetrix channel — connect messaging platforms to your agents.

Commands:
  ninetrix channel connect telegram    interactive Telegram bot setup
  ninetrix channel connect discord     interactive Discord bot setup
  ninetrix channel disconnect telegram remove Telegram configuration
  ninetrix channel disconnect discord  remove Discord configuration
  ninetrix channel status              show connected channels
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import click
import httpx
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from agentfile.core.channel_config import (
    get_channel, save_channel, remove_channel,
    get_bot, save_bot, remove_bot, list_bots, is_bot_verified,
)

console = Console()

_TG_API = "https://api.telegram.org/bot{token}"


def _tg_url(bot_token: str, method: str) -> str:
    """Build a Telegram API URL. Centralises token embedding."""
    return f"{_TG_API.format(token=bot_token)}/{method}"


def _validate_telegram_token(token: str) -> dict | None:
    """Validate a Telegram bot token. Returns bot info dict or None."""
    try:
        resp = httpx.get(_tg_url(token, "getMe"), timeout=10)
        if resp.status_code == 200:
            return resp.json().get("result", {})
    except Exception:
        pass
    return None


def _delete_telegram_webhook(token: str) -> None:
    """Delete any existing webhook (required for polling mode)."""
    try:
        httpx.post(
            _tg_url(token, "deleteWebhook"),
            json={"drop_pending_updates": False},
            timeout=10,
        )
    except Exception:
        pass


def _send_telegram_message(token: str, chat_id: str, text: str) -> bool:
    try:
        resp = httpx.post(
            _tg_url(token, "sendMessage"),
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
            _tg_url(token, "getUpdates"),
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


def setup_telegram_interactive(agent_name: str | None = None, bot_name: str | None = None) -> bool:
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
    _bot_key = bot_name or "telegram"
    existing = get_bot(_bot_key)
    if existing and existing.get("verified"):
        console.print(f"  [green]✓[/green] Telegram already connected: [bold]@{existing.get('bot_username', '?')}[/bold] (bot: {_bot_key})")
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

    # Auto-name the bot after the username if no explicit name given
    if not bot_name:
        _bot_key = bot_username or "telegram"
    else:
        _bot_key = bot_name

    # Save config immediately (even before verification)
    save_bot(_bot_key, {
        "channel_type": "telegram",
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
        console.print("\r  [yellow]⏱[/yellow] Timed out waiting for /start. You can retry with: [bold]ninetrix channel connect telegram[/bold]   ")
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
    save_bot(_bot_key, {
        "channel_type": "telegram",
        "bot_token": token,
        "bot_username": bot_username,
        "chat_id": chat_id,
        "verified": True,
    })

    # Register with API (local or cloud) — creates channel + binds agent
    _register_with_api(token, bot_username, chat_id, agent_name)

    console.print()
    console.print(f"  [green]Done![/green] Messages to [bold]@{bot_username}[/bold] will trigger your agent.")
    console.print(f"  [dim]Bot name: {_bot_key} — Config saved to ~/.agentfile/channels.yaml[/dim]")
    console.print()

    return True


# ── Discord setup ────────────────────────────────────────────────────────────

_DISCORD_API = "https://discord.com/api/v10"


def _validate_discord_token(token: str) -> dict | None:
    """Validate a Discord bot token. Returns bot user dict or None."""
    try:
        resp = httpx.get(
            f"{_DISCORD_API}/users/@me",
            headers={"Authorization": f"Bot {token}"},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


def setup_discord_interactive(agent_name: str | None = None, bot_name: str | None = None) -> bool:
    """Interactive Discord setup flow. Returns True if setup succeeded."""
    console.print()
    console.print(Panel(
        "[bold]Connect Discord[/bold]\n\n"
        "This will connect a Discord bot so people can message\n"
        "your agent by mentioning it in any server channel or DM.",
        border_style="purple",
        width=60,
    ))
    console.print()

    # Check if already configured
    _bot_key = bot_name or "discord"
    existing = get_bot(_bot_key)
    if existing and existing.get("verified"):
        console.print(f"  [green]✓[/green] Discord already connected: [bold]{existing.get('bot_username', '?')}[/bold] (bot: {_bot_key})")
        reconfig = Prompt.ask("  Reconfigure?", choices=["y", "n"], default="n")
        if reconfig == "n":
            return True
        console.print()

    # Step 1: Get bot token
    console.print("  [bold]Step 1:[/bold] Create a Discord bot\n")
    console.print("    1. Go to [bold]https://discord.com/developers/applications[/bold]")
    console.print("    2. Click [bold]New Application[/bold] → name it → create")
    console.print("    3. Go to [bold]Bot[/bold] tab → click [bold]Reset Token[/bold] → copy")
    console.print("    4. Enable [bold]Message Content Intent[/bold] under Privileged Gateway Intents")
    console.print()

    while True:
        token = Prompt.ask("  [bold]Paste your bot token[/bold]").strip()
        if not token:
            console.print("  [red]Token cannot be empty[/red]")
            continue

        console.print("  [dim]Validating...[/dim]", end="")
        bot_info = _validate_discord_token(token)
        if bot_info:
            bot_username = bot_info.get("username", "?")
            console.print(f"\r  [green]✓[/green] Bot verified: [bold]{bot_username}[/bold]   ")
            break
        else:
            console.print("\r  [red]✗[/red] Invalid token. Make sure you copied the full bot token.   ")
            retry = Prompt.ask("  Try again?", choices=["y", "n"], default="y")
            if retry == "n":
                return False

    bot_username = bot_info.get("username", "")
    application_id = bot_info.get("id", "")

    # Step 2: Invite bot to server
    invite_url = (
        f"https://discord.com/oauth2/authorize"
        f"?client_id={application_id}"
        f"&permissions=2048"  # Send Messages
        f"&scope=bot"
    )
    console.print()
    console.print(f"  [bold]Step 2:[/bold] Add the bot to your Discord server\n")
    console.print(f"    Open this URL in your browser:\n")
    console.print(f"    [bold blue]{invite_url}[/bold blue]")
    console.print()
    console.print("    Select a server → Authorize → Complete the captcha")
    console.print()

    Prompt.ask("  [dim]Press Enter when done[/dim]", default="")

    # Auto-name after bot username if no explicit name
    if not bot_name:
        _bot_key = bot_username or "discord"

    # Save config
    save_bot(_bot_key, {
        "channel_type": "discord",
        "bot_token": token,
        "bot_username": bot_username,
        "application_id": application_id,
        "verified": True,
    })

    # Register with API
    _register_channel_with_api("discord", token, bot_username, agent_name)

    console.print()
    console.print(f"  [green]Done![/green] Mention [bold]@{bot_username}[/bold] in any server channel to trigger your agent.")
    console.print(f"  [dim]Bot name: {_bot_key} — Config saved to ~/.agentfile/channels.yaml[/dim]")
    console.print()

    return True


def _register_channel_with_api(
    channel_type: str,
    token: str,
    bot_username: str,
    agent_name: str | None,
) -> bool:
    """Register any channel type with the Ninetrix API."""
    from agentfile.core.config import resolve_api_url
    from agentfile.core.auth import auth_headers

    api_url = resolve_api_url()
    headers = auth_headers(api_url)
    if not headers:
        return False

    try:
        resp = httpx.post(
            f"{api_url}/v1/channels",
            headers=headers,
            json={
                "channel_type": channel_type,
                "name": f"@{bot_username}",
                "config": {"bot_token": token},
                "session_mode": "per_chat",
                "routing_mode": "single",
            },
            timeout=15,
        )
        if resp.status_code not in (200, 201):
            return False

        channel_data = resp.json()
        channel_id = channel_data.get("id")

        # Auto-verify (Discord doesn't need 6-digit code flow)
        httpx.post(
            f"{api_url}/v1/channels/{channel_id}/verify",
            headers=headers,
            json={"code": channel_data.get("config", {}).get("verification_code", "000000")},
            timeout=10,
        )

        if agent_name:
            httpx.post(
                f"{api_url}/v1/channels/{channel_id}/agents",
                headers=headers,
                json={"agent_name": agent_name, "is_default": True},
                timeout=10,
            )

        console.print(f"  [dim]Registered with API[/dim]")
        return True
    except httpx.ConnectError:
        return False
    except Exception:
        return False


def _render_qr_terminal(data: str) -> None:
    """Render a QR code in the terminal using Unicode half-blocks.

    Uses the 'qrcode' library if available, otherwise falls back to
    a minimal pure-Python implementation.
    """
    try:
        import qrcode
        qr = qrcode.QRCode(border=2, box_size=1)
        qr.add_data(data)
        qr.make(fit=True)
        matrix = qr.modules

        # Use Unicode half-block chars: upper=black+white, etc.
        # Each printed row represents 2 QR rows using ▀ ▄ █ and space
        WHITE = "\033[47m"  # white background
        BLACK = "\033[40m"  # black background
        RESET = "\033[0m"

        lines = []
        for y in range(0, len(matrix), 2):
            row = "  "
            for x in range(len(matrix[0])):
                top = matrix[y][x] if y < len(matrix) else False
                bot = matrix[y + 1][x] if y + 1 < len(matrix) else False
                if top and bot:
                    row += "█"
                elif top and not bot:
                    row += "▀"
                elif not top and bot:
                    row += "▄"
                else:
                    row += " "
            lines.append(row)

        # Print with white background padding
        print()
        for line in lines:
            print(line)
        print()

    except ImportError:
        # Fallback: install qrcode
        console.print("  [yellow]Installing qrcode for QR rendering...[/yellow]")
        import subprocess as _sp
        _sp.run(
            [sys.executable, "-m", "pip", "install", "qrcode"],
            capture_output=True,
        )
        try:
            import qrcode  # noqa: F811
            _render_qr_terminal(data)  # retry
        except ImportError:
            console.print(f"  [dim]QR data:[/dim] {data[:80]}...")
    except Exception as exc:
        console.print(f"  [red]QR render failed:[/red] {exc}")
        console.print(f"  [dim]QR data:[/dim] {data[:80]}...")


# ── WhatsApp setup ───────────────────────────────────────────────────────────

_WA_AUTH_DIR = Path.home() / ".agentfile" / "whatsapp-auth"


def setup_whatsapp_interactive(agent_name: str | None = None) -> bool:
    """Interactive WhatsApp setup flow using Baileys QR pairing.

    Starts a temporary Baileys bridge, shows QR code, waits for scan,
    saves credentials to ~/.agentfile/whatsapp-auth/.
    Returns True if paired successfully.
    """
    import shutil
    import subprocess

    console.print()
    console.print(Panel(
        "[bold]Connect WhatsApp[/bold]\n\n"
        "This will pair your WhatsApp account so people can\n"
        "message your agent directly from WhatsApp.\n\n"
        "[yellow]Warning: Use a dedicated phone number (eSIM or spare SIM)\n"
        "— not your personal WhatsApp. The agent responds to ALL\n"
        "incoming messages, and unofficial API usage carries a\n"
        "small risk of account restrictions from Meta.[/yellow]",
        border_style="purple",
        width=60,
    ))
    console.print()

    # Check if already configured
    existing = get_channel("whatsapp")
    if existing and existing.get("verified"):
        console.print(f"  [green]✓[/green] WhatsApp already connected: [bold]{existing.get('phone_number', '?')}[/bold]")
        reconfig = Prompt.ask("  Reconfigure?", choices=["y", "n"], default="n")
        if reconfig == "n":
            return True
        console.print()

    # Check Node.js is available
    if not shutil.which("node"):
        console.print("  [red]Node.js is required for WhatsApp.[/red]")
        console.print("  Install it: [bold]brew install node[/bold] or [bold]https://nodejs.org[/bold]")
        return False

    # Find the baileys-bridge
    bridge_dir = _find_baileys_bridge()
    if not bridge_dir:
        console.print("  [red]baileys-bridge not found.[/red]")
        console.print("  [dim]Expected at packages/channels/baileys-bridge/[/dim]")
        return False

    # Install npm dependencies if needed
    if not (bridge_dir / "node_modules").exists():
        console.print("  [dim]Installing Baileys dependencies...[/dim]")
        result = subprocess.run(
            ["npm", "install", "--production"],
            cwd=str(bridge_dir),
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            console.print(f"  [red]npm install failed:[/red] {result.stderr[:200]}")
            return False
        console.print("  [green]✓[/green] Dependencies installed")

    # Ensure auth dir exists
    _WA_AUTH_DIR.mkdir(parents=True, exist_ok=True)

    console.print()
    console.print("  [bold]Scan the QR code with your WhatsApp app:[/bold]")
    console.print("    WhatsApp → Settings → Linked Devices → Link a Device")
    console.print()

    # Start the bridge temporarily for pairing
    import socket
    import tempfile

    sock_path = Path(tempfile.gettempdir()) / "ninetrix-wa-setup.sock"
    if sock_path.exists():
        sock_path.unlink()

    env = {
        **dict(__import__("os").environ),
        "BAILEYS_SOCKET_PATH": str(sock_path),
        "BAILEYS_AUTH_DIR": str(_WA_AUTH_DIR),
    }

    proc = subprocess.Popen(
        ["node", str(bridge_dir / "index.js")],
        env=env,
        stdout=sys.stderr,   # show bridge logs (loading, connecting, etc.)
        stderr=sys.stderr,
    )

    # Wait for socket
    for _ in range(15):
        if sock_path.exists():
            break
        if proc.poll() is not None:
            console.print(f"  [red]Bridge exited with code {proc.returncode}. Check logs above.[/red]")
            return False
        time.sleep(1)
    else:
        proc.terminate()
        console.print("  [red]Bridge failed to start.[/red]")
        return False

    # Connect and read QR / connected events
    import json

    uds = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    uds.connect(str(sock_path))
    uds.settimeout(120)  # 2 min to scan QR

    paired = False
    phone_number = ""
    buffer = ""

    try:
        while True:
            data = uds.recv(4096).decode()
            if not data:
                break
            buffer += data
            lines = buffer.split("\n")
            buffer = lines.pop()

            for line in lines:
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if msg.get("type") == "qr":
                    qr_data = msg.get("data", "")
                    _render_qr_terminal(qr_data)

                elif msg.get("type") == "connected":
                    wa_data = msg.get("data", {})
                    wa_id = wa_data.get("id", "")
                    wa_name = wa_data.get("name", "")
                    phone_number = wa_id.split("@")[0] if "@" in wa_id else wa_id
                    console.print(f"\n  [green]✓[/green] Paired! {wa_name} ({phone_number})")
                    paired = True
                    break

            if paired:
                break

    except socket.timeout:
        console.print("\n  [yellow]⏱[/yellow] Timed out waiting for QR scan.")
    except Exception as exc:
        console.print(f"\n  [red]Error:[/red] {exc}")
    finally:
        uds.close()
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        sock_path.unlink(missing_ok=True)

    if not paired:
        return False

    # Save config
    save_channel("whatsapp", {
        "phone_number": phone_number,
        "auth_dir": str(_WA_AUTH_DIR),
        "verified": True,
    })

    # Register with API
    _register_channel_with_api("whatsapp", "", phone_number, agent_name)

    console.print()
    console.print(f"  [green]Done![/green] WhatsApp messages to [bold]{phone_number}[/bold] will trigger your agent.")
    console.print(f"  [dim]Auth saved to {_WA_AUTH_DIR}[/dim]")
    console.print()

    return True


def _find_baileys_bridge() -> Path | None:
    """Find the baileys-bridge directory."""
    # Check relative to this file (dev install)
    candidates = [
        Path(__file__).resolve().parent.parent.parent.parent / "channels" / "baileys-bridge",
        Path.home() / ".agentfile" / "baileys-bridge",
    ]
    for p in candidates:
        if (p / "index.js").exists():
            return p
    # Check if installed as package data
    try:
        import importlib.util
        spec = importlib.util.find_spec("ninetrix_channels")
        if spec and spec.submodule_search_locations:
            pkg_dir = Path(list(spec.submodule_search_locations)[0]).parent
            bridge = pkg_dir / "baileys-bridge"
            if (bridge / "index.js").exists():
                return bridge
    except Exception:
        pass
    return None


# ── CLI commands ─────────────────────────────────────────────────────────────

@click.group("channel")
def channel_cmd():
    """Connect messaging platforms (Telegram, Discord, WhatsApp) to your agents."""
    pass


@channel_cmd.command("connect")
@click.argument("platform", type=click.Choice(["telegram", "discord", "whatsapp"]))
@click.option("--agent", "-a", default=None, help="Agent name to bind to this channel")
@click.option("--bot", "-b", "bot_name", default=None, help="Bot name (for multiple bots of same type)")
def connect(platform: str, agent: str | None, bot_name: str | None):
    """Connect a messaging platform to your agents."""
    ok = False
    if platform == "telegram":
        ok = setup_telegram_interactive(agent_name=agent, bot_name=bot_name)
    elif platform == "discord":
        ok = setup_discord_interactive(agent_name=agent, bot_name=bot_name)
    elif platform == "whatsapp":
        ok = setup_whatsapp_interactive(agent_name=agent)
    # Immediately sync to API so dashboard sees it
    if ok:
        try:
            from agentfile.commands.run import _sync_bots_to_api
            _sync_bots_to_api()
        except Exception:
            pass


@channel_cmd.command("disconnect")
@click.argument("bot_name_arg", metavar="BOT_NAME")
def disconnect(bot_name_arg: str):
    """Remove a bot connection by name (e.g. 'support_bot', 'telegram')."""
    ch = get_bot(bot_name_arg)
    if not ch:
        console.print(f"  [dim]Bot '{bot_name_arg}' is not configured.[/dim]")
        console.print(f"  [dim]Run: ninetrix channel status[/dim]")
        return

    ch_type = ch.get("channel_type", "")

    # Delete webhook before removing config (Telegram only)
    if ch_type == "telegram" and ch.get("bot_token"):
        _delete_telegram_webhook(ch["bot_token"])

    # Delete WhatsApp auth state (credentials, session keys)
    if ch_type == "whatsapp":
        import shutil
        auth_dir = ch.get("auth_dir", str(_WA_AUTH_DIR))
        auth_path = Path(auth_dir)
        if auth_path.exists():
            shutil.rmtree(auth_path, ignore_errors=True)
            console.print(f"  [dim]Deleted auth state at {auth_path}[/dim]")

    remove_bot(bot_name_arg)
    console.print(f"  [green]✓[/green] {bot_name_arg} ({ch_type}) disconnected.")
    console.print("  [dim]Removed from ~/.agentfile/channels.yaml[/dim]")


@channel_cmd.command("rename")
@click.argument("old_name")
@click.argument("new_name")
def rename(old_name: str, new_name: str):
    """Rename a bot (e.g. 'telegram' → 'support_bot')."""
    bot = get_bot(old_name)
    if not bot:
        console.print(f"  [red]Bot '{old_name}' not found.[/red]")
        return
    if get_bot(new_name):
        console.print(f"  [red]Bot '{new_name}' already exists.[/red]")
        return
    save_bot(new_name, bot)
    remove_bot(old_name)
    console.print(f"  [green]✓[/green] Renamed [bold]{old_name}[/bold] → [bold]{new_name}[/bold]")
    console.print(f"  [dim]Use bot: {new_name} in your agentfile.yaml triggers[/dim]")
    # Sync to API dashboard
    try:
        from agentfile.commands.run import _sync_bots_to_api
        _sync_bots_to_api()
    except Exception:
        pass


@channel_cmd.command("status")
def status():
    """Show all connected bots."""
    # Bidirectional sync: push local → API first, then pull API → local
    try:
        from agentfile.commands.run import _sync_bots_to_api, _sync_api_to_bots
        _sync_bots_to_api()   # push new CLI bots to API (before API→local removes them)
        _sync_api_to_bots()   # pull dashboard bots + remove deleted ones
    except Exception:
        pass

    console.print()
    console.print("[bold]Channels[/bold]\n")

    bots = list_bots()
    if not bots:
        console.print("  [dim]No channels connected.[/dim]")
        console.print("  [dim]Run: ninetrix channel connect telegram --bot my_bot[/dim]")
        console.print("  [dim]      ninetrix channel connect discord --bot my_discord[/dim]")
        console.print()
        return

    console.print(f"  {'BOT NAME':20s}  {'TYPE':10s}  {'USERNAME':20s}  {'STATUS'}")
    console.print(f"  {'─' * 20}  {'─' * 10}  {'─' * 20}  {'─' * 12}")
    for name, cfg in sorted(bots.items()):
        if not isinstance(cfg, dict):
            continue
        ch_type = cfg.get("channel_type", "?")
        verified = cfg.get("verified", False)
        status_str = "[green]✓ verified[/green]" if verified else "[yellow]⚠ pending[/yellow]"
        username = cfg.get("bot_username") or cfg.get("phone_number") or "?"
        console.print(f"  {name:20s}  {ch_type:10s}  {username:20s}  {status_str}")

    console.print()
    console.print("  [dim]Usage in agentfile.yaml:[/dim]")
    console.print("  [dim]  triggers:[/dim]")
    console.print("  [dim]    - type: channel[/dim]")
    console.print("  [dim]      channels: [telegram][/dim]")
    console.print("  [dim]      bot: <bot_name>      # from table above[/dim]")
    console.print()
    console.print("  [dim]Commands: connect, disconnect, rename, status[/dim]")
    console.print()
