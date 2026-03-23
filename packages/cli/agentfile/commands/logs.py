"""ninetrix logs — stream logs from warm pool agent containers."""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import click
import docker
from docker.errors import DockerException
from rich.console import Console

console = Console()

_STATE_DIR = Path.home() / ".agentfile" / "pools"

_AGENT_COLORS = [
    "cyan", "yellow", "green", "magenta", "blue",
    "bright_cyan", "bright_yellow", "bright_green",
]

# Docker log timestamp prefix: "2024-01-15T09:32:01.123456789Z "
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T(\d{2}:\d{2}:\d{2})\.\d+Z ")


def _strip_ts(line: str) -> tuple[str, str]:
    """Return (hh:mm:ss, message) — strips the verbose Docker ISO timestamp."""
    m = _TS_RE.match(line)
    if m:
        return m.group(1), line[m.end():]
    return "", line


def _docker_client() -> docker.DockerClient:
    try:
        return docker.from_env()
    except DockerException as exc:
        console.print(f"[red]Docker is not running or not installed:[/red] {exc}")
        raise SystemExit(1)


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


def _containers_from_docker(
    client: docker.DockerClient,
    image_prefix: str | None = None,
    agent_filter: str | None = None,
) -> list[tuple[str, object, str]]:
    """Scan running Docker containers for ninetrix/* images (single-agent fallback).

    Returns list of ``(agent_name, container, color)`` tuples.
    """
    results = []
    color_idx = 0
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
            if agent_filter and slug != agent_filter:
                continue
            color = _AGENT_COLORS[color_idx % len(_AGENT_COLORS)]
            color_idx += 1
            results.append((slug, container, color))
    except DockerException:
        pass
    return results


@click.command("logs")
@click.option("--file", "-f", "agentfile_path", default=None,
              help="Path to agentfile.yaml (used in single-agent mode to find the container)")
@click.option("--agent", "-a", "agent_filter", default=None,
              help="Show logs for only this agent")
@click.option("--follow", is_flag=True, default=False,
              help="Follow log output (like docker logs -f)")
@click.option("--tail", default=50, show_default=True,
              help="Number of recent lines to show from each container")
@click.option("--swarm", default=None, help="Swarm name filter")
def logs_cmd(
    agentfile_path: str | None,
    agent_filter: str | None,
    follow: bool,
    tail: int,
    swarm: str | None,
) -> None:
    """Stream logs from agent containers (warm pool or single-agent)."""
    console.print()
    console.print("[bold purple]ninetrix logs[/bold purple]\n")

    # Derive entry-agent image from --file for single-agent lookup
    entry_image: str | None = None
    if agentfile_path:
        try:
            from agentfile.core.models import AgentFile
            af = AgentFile.from_path(agentfile_path)
            entry_image = af.entry_agent.image_name()
        except (FileNotFoundError, ValueError):
            pass

    states = _find_states()
    if swarm:
        states = [s for s in states if s.get("swarm") == swarm]

    client = _docker_client()

    # Collect containers to stream
    containers: list[tuple[str, object, str]] = []
    color_idx = 0

    if states:
        # ── Warm-pool path ────────────────────────────────────────────────────
        for state in states:
            for name, info in state.get("agents", {}).items():
                if agent_filter and name != agent_filter:
                    continue
                cid = info.get("container_id", f"agentfile-{name}")
                color = _AGENT_COLORS[color_idx % len(_AGENT_COLORS)]
                color_idx += 1
                try:
                    container = client.containers.get(cid)
                    containers.append((name, container, color))
                except DockerException:
                    console.print(f"  [yellow]Warning:[/yellow] Container for '{name}' not found.")
    else:
        # ── Single-agent fallback: scan Docker by image name ─────────────────
        containers = _containers_from_docker(client, image_prefix=entry_image, agent_filter=agent_filter)

    if not containers:
        console.print("  No running agent containers found.\n")
        console.print(
            "  [dim]Start one with [bold]ninetrix run[/bold] "
            "or [bold]ninetrix up[/bold].[/dim]\n"
        )
        return

    # Pad agent names to the same width for aligned output
    pad = max(len(name) for name, _, _ in containers)

    def _fmt(name: str, color: str, line: str) -> str:
        ts, msg = _strip_ts(line)
        label = f"[{color}]\\[{name:<{pad}}][/{color}]"
        ts_part = f"[dim]{ts}[/dim] " if ts else ""
        safe_msg = msg.replace("[", "\\[")
        return f"{label} {ts_part}{safe_msg}"

    if not follow:
        for name, container, color in containers:
            try:
                raw = container.logs(tail=tail, timestamps=True).decode("utf-8", errors="replace")
                for line in raw.splitlines():
                    if line.strip():
                        console.print(_fmt(name, color, line))
            except DockerException as exc:
                console.print(f"  [red]Could not get logs for '{name}':[/red] {exc}")
        console.print()
        return

    # Follow mode: one thread per container, all print to the shared console
    def _stream(name: str, container, color: str) -> None:
        try:
            for chunk in container.logs(stream=True, follow=True, tail=tail, timestamps=True):
                line = chunk.decode("utf-8", errors="replace").rstrip()
                if line.strip():
                    console.print(_fmt(name, color, line))
        except (DockerException, Exception):
            pass

    agent_list = ", ".join(f"[{c}]{n}[/{c}]" for n, _, c in containers)
    console.print(f"  Following: {agent_list}  [dim](Ctrl+C to stop)[/dim]\n")

    threads = []
    for name, container, color in containers:
        t = threading.Thread(target=_stream, args=(name, container, color), daemon=True)
        t.start()
        threads.append(t)

    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        console.print("\n  Stopped.\n")
