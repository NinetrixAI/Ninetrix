"""ninetrix config — view and set persistent CLI configuration."""
from __future__ import annotations

import json
import os

import click
from rich.console import Console
from rich.table import Table

from agentfile.core.config import (
    CONFIG_FILE,
    _CLOUD_DEFAULT,
    api_url_source,
    get_api_url,
    read_config,
    resolve_api_url,
    set_api_url,
)
from agentfile.core.auth import TOKEN_FILE, SECRET_FILE

console = Console()

_LOCAL_ALIAS = "local"
_LOCAL_URL = "http://localhost:8000"


@click.group("config")
def config_cmd() -> None:
    """View and set persistent CLI configuration."""
    pass


@config_cmd.command("show")
def config_show() -> None:
    """Print the current configuration and auth status."""
    console.print()
    console.print("[bold]ninetrix config[/bold]\n")

    api_url = resolve_api_url()
    source = api_url_source()

    # Auth token source
    if os.environ.get("AGENTFILE_API_TOKEN"):
        token_source = "env var [dim](AGENTFILE_API_TOKEN)[/dim]"
        token_display = "[green]set[/green]"
    elif TOKEN_FILE.exists():
        try:
            data = json.loads(TOKEN_FILE.read_text())
            if data.get("token"):
                token_source = f"[dim]{TOKEN_FILE}[/dim]"
                token_display = "[green]set[/green]"
            else:
                token_source = f"[dim]{TOKEN_FILE}[/dim]"
                token_display = "[yellow]empty[/yellow]"
        except Exception:
            token_source = f"[dim]{TOKEN_FILE}[/dim]"
            token_display = "[red]unreadable[/red]"
    elif SECRET_FILE.exists():
        token_source = f"machine secret [dim]({SECRET_FILE})[/dim]"
        token_display = "[green]set[/green] [dim](localhost only)[/dim]"
    else:
        token_source = "[dim]none[/dim]"
        token_display = "[yellow]not set[/yellow]"

    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    table.add_column(style="dim", min_width=16)
    table.add_column()
    table.add_column(style="dim")

    table.add_row("API URL", f"[bold]{api_url}[/bold]", source)
    table.add_row("Token", token_display, token_source)
    table.add_row(
        "Config file",
        str(CONFIG_FILE),
        "[green]exists[/green]" if CONFIG_FILE.exists() else "[dim]not created yet[/dim]",
    )

    console.print(table)
    console.print()

    # Hint when nothing is configured
    if not get_api_url() and not os.environ.get("AGENTFILE_API_URL"):
        console.print(
            "  [dim]Tip: run [bold]ninetrix auth login --token <token>[/bold] "
            "to configure API access for all projects.[/dim]\n"
        )


@config_cmd.command("set")
@click.argument("key")
@click.argument("value")
def config_set(key: str, value: str) -> None:
    """Set a config value.

    \b
    Keys:
      api-url   The Ninetrix API endpoint.
                Use "local" as a shorthand for http://localhost:8000.

    \b
    Examples:
      ninetrix config set api-url https://api.ninetrix.io
      ninetrix config set api-url local
    """
    console.print()
    key = key.lower().replace("_", "-")

    if key == "api-url":
        resolved = _LOCAL_URL if value.lower() == _LOCAL_ALIAS else value
        set_api_url(resolved)
        console.print(f"  [green]✓[/green] api-url → [bold]{resolved}[/bold]")
        console.print(f"  [dim]Saved to {CONFIG_FILE}[/dim]\n")
    else:
        console.print(f"  [red]✗[/red] Unknown config key: [bold]{key}[/bold]")
        console.print("  [dim]Available keys: api-url[/dim]\n")
        raise SystemExit(1)


@config_cmd.command("get")
@click.argument("key")
def config_get(key: str) -> None:
    """Print a single config value."""
    key = key.lower().replace("_", "-")
    if key == "api-url":
        console.print(resolve_api_url())
    else:
        console.print(f"[red]Unknown key:[/red] {key}")
        raise SystemExit(1)


@config_cmd.command("unset")
@click.argument("key")
def config_unset(key: str) -> None:
    """Remove a config value (revert to default)."""
    console.print()
    key = key.lower().replace("_", "-")
    cfg = read_config()

    field_map = {"api-url": "api_url"}
    field = field_map.get(key)
    if not field:
        console.print(f"  [red]✗[/red] Unknown config key: [bold]{key}[/bold]")
        raise SystemExit(1)

    if field not in cfg:
        console.print(f"  [dim]{key} was not set — nothing to remove.[/dim]\n")
        return

    del cfg[field]
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    console.print(f"  [green]✓[/green] {key} removed from config.\n")
