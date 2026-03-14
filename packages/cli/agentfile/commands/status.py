"""ninetrix status — show warm pool container status."""

from __future__ import annotations

import datetime
import json
import os
import time
from pathlib import Path

import click
import docker
import httpx
from docker.errors import DockerException
from rich.console import Console
from rich.table import Table

from agentfile.core.models import AgentFile

console = Console()

_STATE_DIR = Path.home() / ".agentfile" / "pools"


def _docker_client() -> docker.DockerClient:
    try:
        return docker.from_env()
    except DockerException as exc:
        console.print(f"[red]Docker is not running or not installed:[/red] {exc}")
        raise SystemExit(1)


def _uptime(started_at: float | None) -> str:
    if not started_at:
        return "?"
    secs = int(time.time() - started_at)
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m {secs % 60}s"
    return f"{secs // 3600}h {(secs % 3600) // 60}m"


def _uptime_from_iso(started_at_str: str) -> str:
    """Compute uptime from Docker's ISO-8601 StartedAt timestamp."""
    try:
        ts = started_at_str[:19]  # trim sub-seconds and Z
        dt = datetime.datetime.fromisoformat(ts + "+00:00")
        secs = int((datetime.datetime.now(datetime.timezone.utc) - dt).total_seconds())
        if secs < 0:
            return "?"
        if secs < 60:
            return f"{secs}s"
        if secs < 3600:
            return f"{secs // 60}m {secs % 60}s"
        return f"{secs // 3600}h {(secs % 3600) // 60}m"
    except Exception:
        return "?"


def _find_states() -> list[dict]:
    if not _STATE_DIR.exists():
        return []
    states = []
    for f in _STATE_DIR.glob("*.json"):
        try:
            states.append(json.loads(f.read_text()))
        except Exception:
            pass
    return states


def _rows_from_docker(
    client: docker.DockerClient,
    image_prefix: str | None = None,
) -> list[dict]:
    """Scan all Docker containers for ninetrix/* images (single-agent fallback).

    If *image_prefix* is given (e.g. ``"ninetrix/my-agent"``), only containers
    whose image starts with that prefix are returned.
    """
    rows = []
    try:
        for container in client.containers.list():
            tags = container.image.tags
            image_ref = tags[0] if tags else ""
            if not image_ref.startswith("ninetrix/"):
                continue
            if image_prefix:
                base = image_prefix.split(":")[0]
                if not image_ref.startswith(base + ":") and image_ref != image_prefix:
                    continue
            slug = image_ref.split("/", 1)[1].split(":")[0]
            started_at = container.attrs.get("State", {}).get("StartedAt", "")
            host_port = "—"
            for bindings in (container.ports or {}).values():
                if bindings:
                    host_port = bindings[0].get("HostPort", "—")
                    break
            rows.append({
                "swarm": "—",
                "agent": slug,
                "image": image_ref,
                "status": container.status,
                "port": host_port,
                "uptime": _uptime_from_iso(started_at),
            })
    except DockerException:
        pass
    return rows


def _print_gateway_status() -> None:
    """Print a single-line MCP Gateway health summary."""
    gw_url = os.environ.get("MCP_GATEWAY_URL", "http://localhost:8080")
    try:
        health = httpx.get(f"{gw_url}/health", timeout=2).json()
        worker_count = health.get("connected_workers", 0)
        try:
            tools = httpx.get(f"{gw_url}/admin/tools", timeout=2).json().get("tools", [])
            tool_count = len(tools)
            servers = {t["name"].split("__")[0] for t in tools if "__" in t["name"]}
            detail = f"{tool_count} tool(s) across {len(servers)} server(s)"
        except Exception:
            detail = f"{worker_count} worker(s)"
        console.print(
            f"  [dim]MCP Gateway:[/dim]  [green]✓ online[/green]  [dim]{detail}[/dim]  "
            f"[dim]({gw_url})[/dim]\n"
        )
    except Exception:
        console.print(
            "  [dim]MCP Gateway:[/dim]  [dim]offline[/dim]  "
            "[dim]— run [bold]ninetrix gateway start[/bold][/dim]\n"
        )


@click.command("status")
@click.option("--file", "-f", "agentfile_path", default=None,
              help="Path to agentfile.yaml (to filter by swarm or image)")
@click.option("--swarm", default=None, help="Swarm name filter")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
def status_cmd(agentfile_path: str | None, swarm: str | None, as_json: bool) -> None:
    """Show the status of running agents (warm pool or single-agent)."""
    console.print()
    console.print("[bold purple]ninetrix status[/bold purple]\n")

    # Derive swarm filter and entry-agent image from --file if supplied
    swarm_filter = swarm
    entry_image: str | None = None
    if agentfile_path:
        try:
            af = AgentFile.from_path(agentfile_path)
            if af.is_multi_agent and not swarm_filter:
                swarm_filter = f"agentfile-{af.entry_agent.name}-swarm"
            else:
                entry_image = af.entry_agent.image_name()  # "ninetrix/<slug>:latest"
        except (FileNotFoundError, ValueError):
            pass

    states = _find_states()
    if swarm_filter:
        states = [s for s in states if s.get("swarm") == swarm_filter]

    client = _docker_client()
    all_rows: list[dict] = []

    if states:
        # ── Warm-pool path ────────────────────────────────────────────────────
        for state in states:
            swarm_name = state.get("swarm", "?")
            started_at = state.get("started_at")
            for name, info in state.get("agents", {}).items():
                cid = info.get("container_id", "")
                image = info.get("image", "?")
                host_port = info.get("host_port", "?")

                container_status = "unknown"
                try:
                    container = client.containers.get(cid or f"agentfile-{name}")
                    container.reload()
                    container_status = container.status
                except DockerException:
                    container_status = "not found"

                all_rows.append({
                    "swarm": swarm_name,
                    "agent": name,
                    "image": image,
                    "status": container_status,
                    "port": str(host_port),
                    "uptime": _uptime(started_at),
                })
    else:
        # ── Single-agent fallback: scan Docker containers by image name ───────
        all_rows = _rows_from_docker(client, image_prefix=entry_image)
        if not all_rows:
            console.print("  No running agents found.")
            console.print(
                "  [dim]Start one with [bold]ninetrix run[/bold] "
                "or [bold]ninetrix up[/bold].[/dim]\n"
            )

    if as_json:
        print(json.dumps(all_rows, indent=2))
        return

    if not all_rows:
        _print_gateway_status()
        return

    table = Table(show_header=True, header_style="bold purple", box=None)
    table.add_column("Agent", style="bold")
    table.add_column("Image", style="dim")
    table.add_column("Status")
    table.add_column("Port")
    table.add_column("Uptime")
    table.add_column("Swarm", style="dim")

    for row in all_rows:
        status = row["status"]
        status_color = "green" if status == "running" else "red"
        table.add_row(
            row["agent"],
            row["image"],
            f"[{status_color}]{status}[/{status_color}]",
            row["port"],
            row["uptime"],
            row["swarm"],
        )

    console.print(table)
    console.print()
    _print_gateway_status()
