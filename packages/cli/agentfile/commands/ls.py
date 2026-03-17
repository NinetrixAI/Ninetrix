"""ninetrix ls — inventory view of agents defined in agentfile.yaml."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import click
import docker
from docker.errors import DockerException, ImageNotFound
from rich.console import Console
from rich.table import Table

from agentfile.core.models import AgentFile, AgentDef

console = Console()

_STATE_DIR = Path.home() / ".agentfile" / "pools"


def _docker_client() -> Optional[docker.DockerClient]:
    try:
        return docker.from_env()
    except DockerException:
        return None


def _load_pool_states() -> list[dict]:
    if not _STATE_DIR.exists():
        return []
    states = []
    for f in _STATE_DIR.glob("*.json"):
        try:
            states.append(json.loads(f.read_text()))
        except Exception:
            pass
    return states


def _build_running_map(client: Optional[docker.DockerClient]) -> dict[str, dict]:
    """Return a dict keyed by agent name/slug:
      {name: {"mode": "pool" | "solo", "swarm": str | None, "port": str}}
    """
    if not client:
        return {}

    running: dict[str, dict] = {}

    # ── Pool path ──────────────────────────────────────────────────────────────
    for state in _load_pool_states():
        swarm = state.get("swarm", "")
        for name, info in state.get("agents", {}).items():
            cid = info.get("container_id", "")
            try:
                c = client.containers.get(cid or f"agentfile-{name}")
                c.reload()
                if c.status == "running":
                    running[name] = {
                        "mode": "pool",
                        "swarm": swarm,
                        "port": str(info.get("host_port", "?")),
                    }
            except DockerException:
                pass

    # ── Solo path: scan running containers for ninetrix/* images ──────────────
    try:
        for container in client.containers.list():
            tags = container.image.tags
            image_ref = tags[0] if tags else ""
            if not image_ref.startswith("ninetrix/"):
                continue
            slug = image_ref.split("/", 1)[1].split(":")[0]
            if slug not in running:
                host_port = "—"
                for bindings in (container.ports or {}).values():
                    if bindings:
                        host_port = bindings[0].get("HostPort", "—")
                        break
                running[slug] = {"mode": "solo", "swarm": None, "port": host_port}
    except DockerException:
        pass

    return running


def _image_exists(client: Optional[docker.DockerClient], image_name: str) -> bool:
    if not client:
        return False
    try:
        client.images.get(image_name)
        return True
    except (ImageNotFound, DockerException):
        return False


def _tool_summary(agent: AgentDef) -> str:
    """E.g. '3  (mcp, local)'"""
    if not agent.tools:
        return "—"
    types = []
    if any(t.is_mcp() for t in agent.tools):
        types.append("mcp")
    if any(t.is_composio() for t in agent.tools):
        types.append("composio")
    if any(t.is_local() for t in agent.tools):
        types.append("local")
    n = len(agent.tools)
    if types:
        return f"{n}  ({', '.join(types)})"
    return str(n)


def _truncate(s: str, max_len: int = 30) -> str:
    if len(s) <= max_len:
        return s
    return s[:max_len - 1] + "…"


# ── Main command ──────────────────────────────────────────────────────────────

@click.command("ls")
@click.option("--file", "-f", "agentfile_path", default="agentfile.yaml",
              help="Path to agentfile.yaml", show_default=True)
@click.option("--tools", "show_tools", is_flag=True, help="Show tool inventory")
@click.option("--triggers", "show_triggers", is_flag=True, help="Show trigger definitions")
@click.option("--no-docker", is_flag=True, help="Skip Docker checks (faster, YAML-only)")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
def ls_cmd(
    agentfile_path: str,
    show_tools: bool,
    show_triggers: bool,
    no_docker: bool,
    as_json: bool,
) -> None:
    """List agents, tools, and triggers defined in agentfile.yaml."""
    console.print()

    try:
        af = AgentFile.from_path(agentfile_path)
    except FileNotFoundError:
        console.print(f"  [red]✗[/red] File not found: {agentfile_path}")
        console.print("  [dim]Run [bold]ninetrix init[/bold] to scaffold one.[/dim]\n")
        raise SystemExit(1)
    except ValueError as exc:
        console.print(f"  [red]✗[/red] Invalid agentfile: {exc}\n")
        raise SystemExit(1)

    client = None if no_docker else _docker_client()

    if show_tools:
        _print_tools(af, as_json)
    elif show_triggers:
        _print_triggers(af, agentfile_path, as_json)
    else:
        _print_agents(af, agentfile_path, client, as_json)


# ── Sub-views ─────────────────────────────────────────────────────────────────

def _print_agents(
    af: AgentFile,
    source_path: str,
    client: Optional[docker.DockerClient],
    as_json: bool,
) -> None:
    running_map = _build_running_map(client)

    rows = []
    for agent in af.agents.values():
        slug = agent.name.lower().replace(" ", "-")
        image = agent.image_name()
        built = _image_exists(client, image)

        # Match by original key OR by slug (image name normalisation)
        run_info = running_map.get(agent.name) or running_map.get(slug)

        if run_info:
            status = "running"
            mode = run_info["mode"]
            swarm = run_info.get("swarm") or "—"
        elif built:
            status = "stopped"
            mode = "—"
            swarm = "—"
        else:
            status = "—"
            mode = "—"
            swarm = "—"

        rows.append({
            "agent": agent.name,
            "role": agent.role or agent.description or "—",
            "model": f"{agent.provider} / {agent.model}",
            "tools": _tool_summary(agent),
            "built": built,
            "status": status,
            "mode": mode,
            "swarm": swarm,
        })

    if as_json:
        out = [{k: v for k, v in r.items()} for r in rows]
        print(json.dumps(out, indent=2))
        return

    n = len(af.agents)
    n_running = sum(1 for r in rows if r["status"] == "running")
    fname = Path(source_path).name
    console.print(
        f"[bold purple]ninetrix ls[/bold purple]  "
        f"[dim]{fname}  ·  {n} agent{'s' if n != 1 else ''}  ·  {n_running} running[/dim]\n"
    )

    table = Table(show_header=True, header_style="bold purple", box=None, padding=(0, 1))
    table.add_column("Agent", style="bold")
    table.add_column("Role", style="dim", max_width=28)
    table.add_column("Model", style="dim")
    table.add_column("Tools")
    table.add_column("Image")
    table.add_column("Status")
    table.add_column("Mode", style="dim")

    for row in rows:
        status = row["status"]
        if status == "running":
            status_str = "[green]running[/green]"
        elif status == "stopped":
            status_str = "[yellow]stopped[/yellow]"
        else:
            status_str = "[dim]—[/dim]"

        image_str = (
            "[green]✓ built[/green]" if row["built"]
            else "[dim]✗ no image[/dim]"
        )

        mode = row["mode"]
        if mode == "pool":
            # Shorten "agentfile-foo-swarm" → "foo"
            swarm_label = (
                row["swarm"]
                .removeprefix("agentfile-")
                .removesuffix("-swarm")
            )
            mode_str = f"pool  [dim]({swarm_label})[/dim]"
        elif mode == "solo":
            mode_str = "solo"
        else:
            mode_str = "[dim]—[/dim]"

        table.add_row(
            row["agent"],
            _truncate(row["role"], 28),
            row["model"],
            row["tools"],
            image_str,
            status_str,
            mode_str,
        )

    console.print(table)
    console.print()

    if client is None:
        console.print(
            "  [dim](Docker checks skipped — remove --no-docker for image/run status)[/dim]\n"
        )


def _print_tools(af: AgentFile, as_json: bool) -> None:
    rows = []
    for agent in af.agents.values():
        for tool in agent.tools:
            if tool.is_mcp():
                tool_type = "mcp"
            elif tool.is_composio():
                tool_type = "composio"
            elif tool.is_local():
                tool_type = "local"
            else:
                tool_type = "unknown"

            rows.append({
                "agent": agent.name,
                "tool": tool.name,
                "source": tool.source,
                "type": tool_type,
                "actions": tool.actions or [],
            })

        if agent.collaborators:
            rows.append({
                "agent": agent.name,
                "tool": "transfer_to_agent",
                "source": f"built-in — {', '.join(agent.collaborators)}",
                "type": "built-in",
                "actions": [],
            })

    if as_json:
        print(json.dumps(rows, indent=2))
        return

    real_count = sum(1 for r in rows if r["type"] != "built-in")
    console.print(
        f"[bold purple]ninetrix ls --tools[/bold purple]  "
        f"[dim]{real_count} tool(s) across {len(af.agents)} agent(s)[/dim]\n"
    )

    table = Table(show_header=True, header_style="bold purple", box=None, padding=(0, 1))
    table.add_column("Agent", style="bold")
    table.add_column("Tool")
    table.add_column("Source", style="dim")
    table.add_column("Type")

    type_colors = {
        "mcp": "cyan",
        "composio": "blue",
        "local": "green",
        "built-in": "dim",
        "unknown": "red",
    }

    for row in rows:
        c = type_colors.get(row["type"], "white")
        table.add_row(
            row["agent"],
            row["tool"],
            row["source"],
            f"[{c}]{row['type']}[/{c}]",
        )

    console.print(table)
    console.print()


def _print_triggers(af: AgentFile, source_path: str, as_json: bool) -> None:
    rows = []

    # Per-agent triggers
    for agent in af.agents.values():
        for t in agent.triggers:
            rows.append({
                "agent": agent.name,
                "type": t.type,
                "detail": t.endpoint or t.cron or "—",
                "port": str(t.port) if t.type == "webhook" else "—",
                "target": agent.name,
            })

    # Root-level triggers
    for t in af.triggers:
        rows.append({
            "agent": "(root)",
            "type": t.type,
            "detail": t.endpoint or t.cron or "—",
            "port": str(t.port) if t.type == "webhook" else "—",
            "target": t.target_agent or af.entry_agent.name,
        })

    if as_json:
        print(json.dumps(rows, indent=2))
        return

    console.print(
        f"[bold purple]ninetrix ls --triggers[/bold purple]  "
        f"[dim]{len(rows)} trigger(s)[/dim]\n"
    )

    if not rows:
        console.print(
            "  [dim]No triggers defined. Add a [bold]triggers:[/bold] "
            "block to your agentfile.yaml.[/dim]\n"
        )
        return

    table = Table(show_header=True, header_style="bold purple", box=None, padding=(0, 1))
    table.add_column("Agent", style="bold")
    table.add_column("Type")
    table.add_column("Schedule / Endpoint")
    table.add_column("Port", style="dim")
    table.add_column("Target", style="dim")

    type_colors = {"webhook": "cyan", "schedule": "yellow"}

    for row in rows:
        c = type_colors.get(row["type"], "white")
        agent_label = "[dim](root)[/dim]" if row["agent"] == "(root)" else row["agent"]
        table.add_row(
            agent_label,
            f"[{c}]{row['type']}[/{c}]",
            row["detail"],
            row["port"],
            row["target"],
        )

    console.print(table)
    console.print()
