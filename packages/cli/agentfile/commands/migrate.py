"""ninetrix migrate — upgrade agentfile.yaml to the latest schema version."""

from __future__ import annotations

import sys
from pathlib import Path

import click
import yaml
from rich.console import Console

console = Console()

# Current schema version
LATEST_VERSION = "1.1"


# ── Migration functions ───────────────────────────────────────────────────────
# Each takes a raw dict and returns a (dict, list[str]) of (migrated, changes).

def _migrate_1_0_to_1_1(data: dict) -> tuple[dict, list[str]]:
    """Migrate from 1.0 (or unversioned) to 1.1."""
    changes: list[str] = []

    # Rename mcp_gateway.workspace_id → org_id
    gw = data.get("mcp_gateway")
    if gw and "workspace_id" in gw:
        gw["org_id"] = gw.pop("workspace_id")
        changes.append("mcp_gateway: renamed workspace_id → org_id")

    # Set schema_version
    data["schema_version"] = "1.1"
    changes.append("set schema_version: '1.1'")

    return data, changes


# Ordered migration chain
_MIGRATIONS: list[tuple[str, str, callable]] = [
    ("1.0", "1.1", _migrate_1_0_to_1_1),
]


def _detect_version(data: dict) -> str:
    """Detect the schema version from the YAML data."""
    return str(data.get("schema_version") or data.get("version") or "1.0")


def _migrate(data: dict) -> tuple[dict, list[str]]:
    """Apply all pending migrations and return (migrated_data, all_changes)."""
    current = _detect_version(data)
    all_changes: list[str] = []

    for from_ver, to_ver, fn in _MIGRATIONS:
        if current == from_ver:
            data, changes = fn(data)
            all_changes.extend(changes)
            current = to_ver

    return data, all_changes


# ── CLI command ───────────────────────────────────────────────────────────────

@click.command("migrate")
@click.option("--file", "-f", "filepath", default="agentfile.yaml",
              help="Path to agentfile.yaml")
@click.option("--dry-run", is_flag=True, help="Show changes without writing")
def migrate_cmd(filepath: str, dry_run: bool) -> None:
    """Upgrade agentfile.yaml to the latest schema version."""
    path = Path(filepath)
    if not path.exists():
        console.print(f"  [red]✗[/red] File not found: {path}")
        sys.exit(1)

    with path.open() as fh:
        raw = yaml.safe_load(fh)

    if not isinstance(raw, dict):
        console.print("  [red]✗[/red] File is not a valid YAML mapping")
        sys.exit(1)

    current = _detect_version(raw)
    if current == LATEST_VERSION:
        console.print(f"  [green]✓[/green] Already at schema_version: '{LATEST_VERSION}' — nothing to do")
        return

    console.print(f"  Migrating from [yellow]{current}[/yellow] → [green]{LATEST_VERSION}[/green]\n")

    migrated, changes = _migrate(raw)

    for change in changes:
        console.print(f"  [cyan]→[/cyan] {change}")

    if not changes:
        console.print("  [dim]No changes needed[/dim]")
        return

    if dry_run:
        console.print(f"\n  [yellow]Dry run[/yellow] — no files modified")
        return

    # Write back
    with path.open("w") as fh:
        yaml.dump(migrated, fh, default_flow_style=False, sort_keys=False, allow_unicode=True)

    console.print(f"\n  [green]✓[/green] Wrote {path}")
