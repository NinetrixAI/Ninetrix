"""ninetrix connect / disconnect / connections — manage OAuth integrations via Ninetrix Cloud.

These commands operate on cloud state only — no local file mutations.

Quick start:
  ninetrix connections                  list all available integrations and their status
  ninetrix connect github               authorize GitHub via OAuth (opens browser)
  ninetrix disconnect github            revoke the GitHub credential from the vault
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import click
import httpx
from rich.console import Console
from rich.table import Table

from agentfile.core.auth import TOKEN_FILE, read_token

console = Console()

_CLOUD_MCP_API = "https://mcp.ninetrix.io"

# Integration display names + icons (mirrors what mcp-api returns, kept here for
# rich offline rendering when the API is unreachable).
_KNOWN_ICONS: dict[str, str] = {
    "google-drive": "Google Drive",
    "gmail": "Gmail",
    "google-calendar": "Google Calendar",
    "google-sheets": "Google Sheets",
    "slack": "Slack",
    "notion": "Notion",
    "github": "GitHub",
    "supabase": "Supabase",
}


# ── URL + token helpers ───────────────────────────────────────────────────────

def _mcp_api_url() -> str | None:
    """Return the mcp-api base URL to use for these commands.

    Resolution order:
      1. NINETRIX_MCP_API_URL env var  — explicit override
      2. MCP_API_URL env var           — docker-compose / CI
      3. Auto-probe localhost:8010     — local dev stack
      4. Cloud default (mcp.ninetrix.io) if a JWT is stored
    """
    if url := os.environ.get("NINETRIX_MCP_API_URL"):
        return url
    if url := os.environ.get("MCP_API_URL"):
        return url
    # Auto-probe local dev instance (silent, 1s timeout)
    try:
        r = httpx.get("http://localhost:8010/health", timeout=1.0)
        if r.is_success:
            return "http://localhost:8010"
    except Exception:
        pass
    # Fall back to cloud if the user is logged in
    if TOKEN_FILE.exists():
        try:
            if json.loads(TOKEN_FILE.read_text()).get("token"):
                return _CLOUD_MCP_API
        except Exception:
            pass
    return None


def _auth_headers(api_url: str) -> dict[str, str]:
    token = read_token(api_url)
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def _require_api() -> tuple[str, dict[str, str]]:
    """Return (api_url, headers) or print an error and exit."""
    api_url = _mcp_api_url()
    if not api_url:
        console.print(
            "  [red]Not logged in.[/red]  Run: [bold]ninetrix auth login --token <token>[/bold]\n"
        )
        raise SystemExit(1)
    headers = _auth_headers(api_url)
    if not headers:
        console.print(
            "  [red]No token found.[/red]  Run: [bold]ninetrix auth login --token <token>[/bold]\n"
        )
        raise SystemExit(1)
    return api_url, headers


# ── ninetrix connections ──────────────────────────────────────────────────────

@click.command("connections")
def connections_cmd() -> None:
    """List all available integrations and their connected status.

    \b
    Examples:
      ninetrix connections
    """
    console.print()
    console.print("[bold purple]ninetrix connections[/bold purple]\n")

    api_url, headers = _require_api()

    try:
        resp = httpx.get(
            f"{api_url}/v1/integrations/connected",
            headers=headers,
            timeout=8,
        )
    except httpx.ConnectError:
        console.print(
            f"  [red]✗[/red] Cannot reach mcp-api at [bold]{api_url}[/bold]\n"
            "  Run [bold]ninetrix dev[/bold] to start the local stack, "
            "or check [dim]NINETRIX_MCP_API_URL[/dim].\n"
        )
        raise SystemExit(1)

    if resp.status_code == 401:
        console.print(
            "  [red]✗[/red] Token rejected — run [bold]ninetrix auth login --token <token>[/bold]\n"
        )
        raise SystemExit(1)

    resp.raise_for_status()
    integrations: list[dict] = resp.json()

    if not integrations:
        console.print("  [dim]No integrations found.[/dim]\n")
        return

    t = Table(show_header=True, header_style="bold purple")
    t.add_column("Integration", style="bold cyan")
    t.add_column("ID")
    t.add_column("Status")

    connected_count = 0
    for item in sorted(integrations, key=lambda x: x.get("name", x.get("id", ""))):
        name = item.get("name", item.get("id", "?"))
        iid = item.get("id", "")
        if item.get("connected"):
            status = "[green]connected[/green]"
            connected_count += 1
        else:
            status = "[dim]not connected[/dim]"
        t.add_row(name, iid, status)

    console.print(t)
    console.print()

    if connected_count:
        console.print(f"  [green]✓[/green] {connected_count} integration(s) connected")
    else:
        console.print("  [dim]No integrations connected.[/dim]")
    console.print(
        "  Connect one: [bold]ninetrix connect <id>[/bold]  "
        "(e.g. [bold]ninetrix connect github[/bold])\n"
    )


# ── ninetrix connect ──────────────────────────────────────────────────────────

@click.command("connect")
@click.argument("integration_id")
@click.option("--no-browser", is_flag=True, help="Print the OAuth URL without opening a browser")
def connect_cmd(integration_id: str, no_browser: bool) -> None:
    """Connect an integration via OAuth.

    Opens a browser to authorize the integration, then waits for you to confirm.

    \b
    Examples:
      ninetrix connect github
      ninetrix connect slack
      ninetrix connect google-drive --no-browser
    """
    console.print()
    console.print("[bold purple]ninetrix connect[/bold purple]\n")

    api_url, headers = _require_api()

    # 1. Get the OAuth authorization URL from mcp-api
    try:
        resp = httpx.get(
            f"{api_url}/v1/integrations/{integration_id}/authorize",
            headers=headers,
            timeout=8,
        )
    except httpx.ConnectError:
        console.print(
            f"  [red]✗[/red] Cannot reach mcp-api at [bold]{api_url}[/bold]\n"
            "  Run [bold]ninetrix dev[/bold] to start the local stack.\n"
        )
        raise SystemExit(1)

    if resp.status_code == 404:
        console.print(
            f"  [red]'{integration_id}' is not a known integration.[/red]\n"
            "  Run [bold]ninetrix connections[/bold] to see available integrations.\n"
        )
        raise SystemExit(1)
    if resp.status_code == 401:
        console.print(
            "  [red]✗[/red] Token rejected — run [bold]ninetrix auth login --token <token>[/bold]\n"
        )
        raise SystemExit(1)
    if resp.status_code == 503:
        console.print(
            f"  [yellow]⚠[/yellow]  {integration_id} OAuth is not configured on this server.\n"
            "  Contact your organization administrator.\n"
        )
        raise SystemExit(1)

    resp.raise_for_status()
    auth_url: str = resp.json()["url"]

    # 2. Show / open the URL
    display_name = _KNOWN_ICONS.get(integration_id, integration_id)
    console.print(
        f"  Authorize [bold]{display_name}[/bold] by completing the OAuth flow:\n"
        f"  [bold]{auth_url}[/bold]\n"
    )
    if not no_browser:
        click.launch(auth_url)

    # 3. Wait for user to complete the flow in the browser
    console.print(
        "  Complete the authorization in your browser, then press "
        "[bold]Enter[/bold] to verify…\n"
    )
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        console.print("\n  [yellow]Cancelled.[/yellow]\n")
        raise SystemExit(0)

    # 4. Verify connection by checking /integrations/connected
    try:
        check_resp = httpx.get(
            f"{api_url}/v1/integrations/connected",
            headers=headers,
            timeout=8,
        )
        if check_resp.is_success:
            items = check_resp.json()
            for item in items:
                if item.get("id") == integration_id and item.get("connected"):
                    console.print(
                        f"  [green]✓[/green] [bold]{display_name}[/bold] connected "
                        f"successfully.\n\n"
                        f"  Use in [bold]agentfile.yaml[/bold]:\n"
                        f"    [dim]tools:\n"
                        f"      - name: {integration_id}\n"
                        f"        source: mcp://{integration_id}[/dim]\n"
                    )
                    return
            # Not found connected yet
            console.print(
                f"  [yellow]⚠[/yellow]  [bold]{display_name}[/bold] does not appear "
                "connected yet.\n"
                f"  You can try again: [bold]ninetrix connect {integration_id}[/bold]\n"
                f"  Or authorize manually: [bold]{auth_url}[/bold]\n"
            )
        else:
            console.print(
                f"  [yellow]⚠[/yellow]  Could not verify connection status ({check_resp.status_code}).\n"
                f"  If you completed the OAuth flow, it should be active.\n"
            )
    except Exception:
        console.print(
            f"  [yellow]⚠[/yellow]  Could not verify connection status.\n"
            f"  If you completed the OAuth flow, it should be active.\n"
        )


# ── ninetrix disconnect ───────────────────────────────────────────────────────

@click.command("disconnect")
@click.argument("integration_id")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
def disconnect_cmd(integration_id: str, yes: bool) -> None:
    """Disconnect an integration and remove its credentials from the vault.

    \b
    Examples:
      ninetrix disconnect github
      ninetrix disconnect slack --yes
    """
    console.print()
    console.print("[bold purple]ninetrix disconnect[/bold purple]\n")

    api_url, headers = _require_api()

    display_name = _KNOWN_ICONS.get(integration_id, integration_id)

    if not yes:
        confirmed = click.confirm(
            f"  Remove credentials for [bold]{display_name}[/bold]? "
            "This cannot be undone.",
            default=False,
        )
        console.print()
        if not confirmed:
            console.print("  Aborted.\n")
            raise SystemExit(0)

    try:
        resp = httpx.delete(
            f"{api_url}/v1/integrations/{integration_id}",
            headers=headers,
            timeout=8,
        )
    except httpx.ConnectError:
        console.print(
            f"  [red]✗[/red] Cannot reach mcp-api at [bold]{api_url}[/bold]\n"
            "  Run [bold]ninetrix dev[/bold] to start the local stack.\n"
        )
        raise SystemExit(1)

    if resp.status_code == 404:
        console.print(
            f"  [yellow]{integration_id} is not a known integration.[/yellow]\n"
            "  Run [bold]ninetrix connections[/bold] to see available integrations.\n"
        )
        raise SystemExit(1)
    if resp.status_code == 401:
        console.print(
            "  [red]✗[/red] Token rejected — run [bold]ninetrix auth login --token <token>[/bold]\n"
        )
        raise SystemExit(1)
    if resp.status_code == 204:
        console.print(
            f"  [green]✓[/green] [bold]{display_name}[/bold] disconnected. "
            "Credentials removed from vault.\n"
        )
        return

    resp.raise_for_status()
