"""ninetrix auth — manage authentication with the Ninetrix API."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import webbrowser

import click
import httpx
from rich.console import Console

from agentfile.core.auth import (
    CLOUD_SECRET_FILE,
    SECRET_FILE,
    TOKEN_FILE,
    auth_headers,
    clear_token,
    save_auth,
    save_token,
)
from agentfile.core.config import (
    CONFIG_FILE,
    _CLOUD_DEFAULT,
    clear_api_url,
    get_api_url,
    set_api_url,
)

console = Console()


# ── Browser auth flow (PKCE) ────────────────────────────────────────────────

def cli_auth_flow(api_url: str | None = None) -> bool:
    """Run the browser-based OAuth flow. Returns True if authenticated.

    1. Generate PKCE code_verifier + code_challenge
    2. POST /v1/auth/cli/start → get session_id, auth_url, confirm_code
    3. Open browser
    4. Poll until completed
    5. Save tokens
    """
    url = api_url or get_api_url() or _CLOUD_DEFAULT

    # Generate PKCE
    code_verifier = secrets.token_urlsafe(43)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b"=").decode()

    # Start session
    try:
        resp = httpx.post(
            f"{url}/v1/auth/cli/start",
            json={"code_challenge": code_challenge},
            timeout=10,
        )
        if resp.status_code != 200:
            console.print(f"  [red]Failed to start auth session ({resp.status_code})[/red]")
            return False
        data = resp.json()
    except httpx.ConnectError:
        console.print(f"  [red]Cannot reach {url}[/red]")
        console.print("  [dim]Is the Ninetrix Cloud API running?[/dim]")
        return False

    session_id = data["session_id"]
    auth_url = data["auth_url"]
    confirm_code = data["confirm_code"]

    # Show link + confirm code
    console.print()
    console.print(f"  [bold]→ {auth_url}[/bold]")
    console.print()
    console.print(f"  Confirm code: [bold yellow]{confirm_code}[/bold yellow]")
    console.print()

    # Open browser
    try:
        webbrowser.open(auth_url)
        console.print("  [dim]Browser opened. Complete sign-in there.[/dim]")
    except Exception:
        console.print("  [dim]Open the link above in your browser.[/dim]")

    console.print()

    # Poll for completion
    poll_interval = 2
    max_wait = 600  # 10 minutes
    elapsed = 0

    with console.status("  Waiting for sign in...", spinner="dots"):
        while elapsed < max_wait:
            time.sleep(poll_interval)
            elapsed += poll_interval

            try:
                resp = httpx.get(
                    f"{url}/v1/auth/cli/{session_id}",
                    params={"code_verifier": code_verifier},
                    timeout=5,
                )
                if resp.status_code != 200:
                    continue

                poll_data = resp.json()
                status = poll_data.get("status", "")

                if status == "completed":
                    access_token = poll_data.get("access_token", "")
                    refresh_token = poll_data.get("refresh_token", "")
                    user_name = poll_data.get("user_name", "")
                    user_email = poll_data.get("user_email", "")
                    org_id = poll_data.get("org_id", "")

                    if not access_token:
                        # Completed but no PKCE proof — shouldn't happen
                        continue

                    # Save everything
                    save_auth(
                        token=access_token,
                        refresh_token=refresh_token,
                        user_name=user_name,
                        user_email=user_email,
                        org_id=org_id,
                        api_url=url,
                    )
                    set_api_url(url)

                    console.print(
                        f"\n  [green]✓[/green] Authenticated as "
                        f"[bold]{user_name or user_email}[/bold]"
                    )
                    # Track successful auth + link anonymous ID to user
                    try:
                        from agentfile.core.telemetry import track, identify
                        track("cli_auth_completed", {"method": "browser"})
                        identify(user_email=user_email, org_id=org_id)
                    except Exception:
                        pass
                    return True

                elif status == "expired":
                    console.print("\n  [red]Session expired.[/red] Run again to retry.")
                    return False

            except Exception:
                continue

    console.print("\n  [red]Timed out waiting for sign in.[/red]")
    return False


@click.group("auth")
def auth_cmd() -> None:
    """Manage authentication with the Ninetrix API hub."""
    pass


@auth_cmd.command("login")
@click.option("--token", "-t", default=None, metavar="TOKEN",
              help="Personal access token (for CI/CD). Omit to sign in via browser.")
@click.option("--api-url", default=None, metavar="URL",
              help=f"API endpoint (default: {_CLOUD_DEFAULT})")
def auth_login(token: str | None, api_url: str | None) -> None:
    """Authenticate with the Ninetrix Cloud.

    Without --token: opens your browser for GitHub/Google/email sign-in.
    With --token: saves the API token directly (for CI/CD pipelines).

    \b
    Examples:
      ninetrix auth login                        # browser OAuth
      ninetrix auth login --token nxt_xxxxx      # API token (CI/CD)
    """
    console.print()

    if not token:
        # Browser OAuth flow
        console.print("  [bold]Sign in to Ninetrix Cloud[/bold]\n")
        ok = cli_auth_flow(api_url=api_url)
        if not ok:
            raise SystemExit(1)
        console.print()
        return

    # Manual token flow (existing behavior for CI/CD)
    url = (
        api_url
        or os.environ.get("AGENTFILE_API_URL")
        or get_api_url()
        or _CLOUD_DEFAULT
    )

    try:
        resp = httpx.get(
            f"{url}/v1/tokens",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
        if resp.status_code == 401:
            console.print("  [red]✗[/red] Token rejected — double-check it's correct.\n")
            raise SystemExit(1)
        if resp.status_code == 403:
            console.print("  [red]✗[/red] Token has insufficient permissions.\n")
            raise SystemExit(1)
        if resp.status_code not in (200,):
            console.print(
                f"  [red]✗[/red] API returned {resp.status_code} — token not saved.\n"
            )
            raise SystemExit(1)
    except httpx.ConnectError:
        console.print(
            f"  [yellow]⚠[/yellow]  Could not reach [dim]{url}[/dim] — "
            "saving credentials anyway (check the URL if this persists)."
        )

    save_token(token)
    set_api_url(url)

    console.print(f"  [green]✓[/green] Token saved    → [dim]{TOKEN_FILE}[/dim]")
    console.print(f"  [green]✓[/green] API URL saved  → [dim]{CONFIG_FILE}[/dim]")
    console.print(f"\n  [dim]API:[/dim] {url}\n")


@auth_cmd.command("logout")
def auth_logout() -> None:
    """Remove the stored API token and saved API URL."""
    console.print()
    if not TOKEN_FILE.exists() and not get_api_url():
        console.print("  [dim]No token stored — nothing to remove.[/dim]\n")
        return
    clear_token()
    # Also clear the saved API URL so subsequent commands don't silently target
    # the old SaaS endpoint without a token.
    if get_api_url():
        clear_api_url()
        console.print(f"  [green]✓[/green] API URL cleared  → [dim]{CONFIG_FILE}[/dim]")
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
    elif CLOUD_SECRET_FILE.exists():
        token_method = f"cloud secret [dim]({CLOUD_SECRET_FILE})[/dim]"
    elif SECRET_FILE.exists():
        token_method = f"machine secret [dim]({SECRET_FILE})[/dim]"
    else:
        token_method = "[yellow]none[/yellow]"

    console.print(f"  [dim]API URL:    [/dim]  {url}  [dim]({url_source})[/dim]")
    console.print(f"  [dim]Token:      [/dim]  {token_method}")

    # Connectivity check
    try:
        resp = httpx.get(
            f"{url}/v1/tokens",
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
