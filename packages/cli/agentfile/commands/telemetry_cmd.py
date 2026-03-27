"""ninetrix telemetry — manage anonymous usage tracking."""
from __future__ import annotations

import click
from rich.console import Console

from agentfile.core.telemetry import _is_enabled, set_enabled

console = Console()


@click.group("telemetry")
def telemetry_cmd() -> None:
    """Manage anonymous usage analytics."""
    pass


@telemetry_cmd.command("on")
def telemetry_on() -> None:
    """Enable anonymous usage analytics."""
    set_enabled(True)
    console.print("  [green]✓[/green] Telemetry enabled.")
    console.print("  [dim]Anonymous usage data helps improve Ninetrix.[/dim]")


@telemetry_cmd.command("off")
def telemetry_off() -> None:
    """Disable anonymous usage analytics."""
    set_enabled(False)
    console.print("  [green]✓[/green] Telemetry disabled.")
    console.print("  [dim]No usage data will be sent.[/dim]")


@telemetry_cmd.command("status")
def telemetry_status() -> None:
    """Show current telemetry status."""
    enabled = _is_enabled()
    if enabled:
        console.print("  [green]●[/green] Telemetry is [bold]enabled[/bold]")
        console.print("  [dim]Disable with: ninetrix telemetry off[/dim]")
    else:
        console.print("  [dim]●[/dim] Telemetry is [bold]disabled[/bold]")
        console.print("  [dim]Enable with: ninetrix telemetry on[/dim]")
