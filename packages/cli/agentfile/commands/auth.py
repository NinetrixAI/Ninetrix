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

console = Console()


@click.group("auth")
def auth_cmd() -> None:
    """Manage authentication with the Ninetrix API hub."""
    pass


@auth_cmd.command("login")
@click.option("--token", "-t", required=True, metavar="TOKEN",
              help="Personal access token from the Ninetrix dashboard (Settings → API Keys)")
@click.option("--api-url", default=None, metavar="URL",
              help="API URL (overrides AGENTFILE_API_URL, default: http://localhost:8000)")
def auth_login(token: str, api_url: str | None) -> None:
    """Save an API token — enables credential injection in ninetrix run/up."""
    url = api_url or os.environ.get("AGENTFILE_API_URL", "http://localhost:8000")
    console.print()

    # Verify the token against the live API before saving
    try:
        resp = httpx.get(
            f"{url}/integrations/credentials",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
        if resp.status_code == 401:
            console.print("[red]Token rejected — double-check it's correct.[/red]\n")
            raise SystemExit(1)
        if resp.status_code not in (200,):
            console.print(
                f"  [yellow]Warning:[/yellow] API returned {resp.status_code}. "
                "Saving anyway."
            )
    except httpx.ConnectError:
        console.print(
            f"  [yellow]Warning:[/yellow] Could not reach [dim]{url}[/dim] — saving token anyway."
        )

    save_token(token)
    console.print(f"  [green]✓[/green] Token saved → [dim]{TOKEN_FILE}[/dim]")
    console.print(f"  [dim]API:[/dim] {url}\n")


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
@click.option("--api-url", default=None, metavar="URL", help="API URL to check")
def auth_status(api_url: str | None) -> None:
    """Show which auth method is active and whether the API is reachable."""
    url = api_url or os.environ.get("AGENTFILE_API_URL", "http://localhost:8000")
    console.print()
    console.print("[bold]ninetrix auth status[/bold]\n")

    # Determine which source the token comes from
    if os.environ.get("AGENTFILE_API_TOKEN"):
        method = "env var [dim](AGENTFILE_API_TOKEN)[/dim]"
    elif TOKEN_FILE.exists():
        try:
            data = json.loads(TOKEN_FILE.read_text())
            if data.get("token"):
                method = f"token file [dim]({TOKEN_FILE})[/dim]"
            else:
                method = "[dim]token file (empty)[/dim]"
        except Exception:
            method = "[dim]token file (unreadable)[/dim]"
    elif SECRET_FILE.exists():
        method = f"machine secret [dim]({SECRET_FILE})[/dim]"
    else:
        method = "[yellow]none[/yellow]"

    console.print(f"  [dim]Auth method:[/dim]  {method}")
    console.print(f"  [dim]API URL:    [/dim]  {url}")

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
