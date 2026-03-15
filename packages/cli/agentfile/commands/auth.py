"""ninetrix auth — manage authentication with the Ninetrix API."""
from __future__ import annotations

import json
import os

import click
import httpx
from rich.console import Console

from agentfile.core.auth import (
    SECRET_FILE,
    TOKEN_FILE,
    auth_headers,
    clear_token,
    save_token,
)
from agentfile.core.config import (
    CONFIG_FILE,
    _CLOUD_DEFAULT,
    api_url_source,
    get_api_url,
    resolve_api_url,
    set_api_url,
)

console = Console()


@click.group("auth")
def auth_cmd() -> None:
    """Manage authentication with the Ninetrix API hub."""
    pass


@auth_cmd.command("login")
@click.option("--token", "-t", required=True, metavar="TOKEN",
              help="Personal access token from the Ninetrix dashboard (Settings → API Keys)")
@click.option("--api-url", default=None, metavar="URL",
              help=f"API endpoint to connect to (saved to {CONFIG_FILE}). "
                   f"Defaults to {_CLOUD_DEFAULT}.")
def auth_login(token: str, api_url: str | None) -> None:
    """Authenticate with the Ninetrix API.

    Saves the token to ~/.agentfile/auth.json and the API URL to
    ~/.agentfile/config.json so every subsequent `ninetrix run/up`
    works without any env vars or .env files.

    \b
    Examples:
      ninetrix auth login --token nxt_xxxxx
      ninetrix auth login --token nxt_xxxxx --api-url https://api.ninetrix.io
    """
    console.print()

    # Resolve URL: flag > env var > existing config > cloud default
    url = (
        api_url
        or os.environ.get("AGENTFILE_API_URL")
        or get_api_url()
        or _CLOUD_DEFAULT
    )

    # Verify the token against the live API before saving
    try:
        resp = httpx.get(
            f"{url}/integrations/credentials",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
        if resp.status_code == 401:
            console.print("  [red]✗[/red] Token rejected — double-check it's correct.\n")
            raise SystemExit(1)
        if resp.status_code not in (200,):
            console.print(
                f"  [yellow]⚠[/yellow]  API returned {resp.status_code}. "
                "Saving credentials anyway."
            )
    except httpx.ConnectError:
        console.print(
            f"  [yellow]⚠[/yellow]  Could not reach [dim]{url}[/dim] — "
            "saving credentials anyway (check the URL if this persists)."
        )

    save_token(token)
    set_api_url(url)

    console.print(f"  [green]✓[/green] Token saved    → [dim]{TOKEN_FILE}[/dim]")
    console.print(f"  [green]✓[/green] API URL saved  → [dim]{CONFIG_FILE}[/dim]")
    console.print(f"\n  [dim]API:[/dim] {url}")
    console.print(
        "\n  All [bold]ninetrix run/up[/bold] commands will now send telemetry "
        "to this API automatically — no env vars or .env files needed.\n"
    )


@auth_cmd.command("logout")
def auth_logout() -> None:
    """Remove the stored API token."""
    console.print()
    if not TOKEN_FILE.exists():
        console.print("  [dim]No token stored — nothing to remove.[/dim]\n")
        return
    clear_token()
    console.print("  [green]✓[/green] Token removed.\n")


@auth_cmd.command("status")
@click.option("--api-url", default=None, metavar="URL", help="Override API URL to check")
def auth_status(api_url: str | None) -> None:
    """Show which auth method is active and whether the API is reachable."""
    from agentfile.core.config import resolve_api_url, api_url_source
    url = api_url or resolve_api_url()
    url_source = api_url_source() if not api_url else "flag"
    console.print()
    console.print("[bold]ninetrix auth status[/bold]\n")

    # Token source
    if os.environ.get("AGENTFILE_API_TOKEN"):
        token_method = "env var [dim](AGENTFILE_API_TOKEN)[/dim]"
    elif TOKEN_FILE.exists():
        try:
            data = json.loads(TOKEN_FILE.read_text())
            if data.get("token"):
                token_method = f"token file [dim]({TOKEN_FILE})[/dim]"
            else:
                token_method = "[dim]token file (empty)[/dim]"
        except Exception:
            token_method = "[dim]token file (unreadable)[/dim]"
    elif SECRET_FILE.exists():
        token_method = f"machine secret [dim]({SECRET_FILE})[/dim]"
    else:
        token_method = "[yellow]none[/yellow]"

    console.print(f"  [dim]API URL:    [/dim]  {url}  [dim]({url_source})[/dim]")
    console.print(f"  [dim]Token:      [/dim]  {token_method}")

    # Connectivity check
    try:
        resp = httpx.get(
            f"{url}/integrations/credentials",
            headers=auth_headers(url),
            timeout=3,
        )
        if resp.status_code == 200:
            console.print("  [dim]Status:    [/dim]  [green]✓ connected[/green]")
        elif resp.status_code == 401:
            console.print(
                "  [dim]Status:    [/dim]  [red]✗ auth failed[/red]  "
                "[dim]— run [bold]ninetrix auth login --token <token>[/bold][/dim]"
            )
        else:
            console.print(f"  [dim]Status:    [/dim]  [yellow]HTTP {resp.status_code}[/yellow]")
    except httpx.ConnectError:
        console.print("  [dim]Status:    [/dim]  [dim]API not reachable[/dim]")
    console.print()
