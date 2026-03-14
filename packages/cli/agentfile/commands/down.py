"""ninetrix down — stop and remove the warm pool containers and network."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import click
import docker
from docker.errors import DockerException
from rich.console import Console

from agentfile.core.models import AgentFile

console = Console()

_STATE_DIR = Path.home() / ".agentfile" / "pools"


def _docker_client() -> docker.DockerClient:
    try:
        return docker.from_env()
    except DockerException as exc:
        console.print(f"[red]Docker is not running or not installed:[/red] {exc}")
        raise SystemExit(1)


def _find_swarm(af: AgentFile | None, swarm_name: str | None) -> tuple[str, dict]:
    """Resolve swarm name → state dict from the pool state file."""
    if swarm_name is None and af is not None:
        swarm_name = f"agentfile-{af.entry_agent.name}-swarm"

    if swarm_name is None:
        if not _STATE_DIR.exists():
            console.print("[red]No pool state found. Is a warm pool running?[/red]")
            raise SystemExit(1)
        state_files = list(_STATE_DIR.glob("*.json"))
        if not state_files:
            console.print("[red]No pool state found. Is a warm pool running?[/red]")
            raise SystemExit(1)
        state_file = state_files[0]
    else:
        state_file = _STATE_DIR / f"{swarm_name}.json"

    if not state_file.exists():
        console.print(f"[red]No pool state for swarm '{swarm_name}'.[/red]")
        raise SystemExit(1)

    state = json.loads(state_file.read_text())
    return state["swarm"], state


def _stop_container(client: docker.DockerClient, name: str, info: dict) -> tuple[bool, str]:
    """Stop and remove one container. Returns (success, message)."""
    cid = info.get("container_id", "")
    container_name = f"agentfile-{name}"
    for lookup in filter(None, [cid, container_name]):
        try:
            c = client.containers.get(lookup)
            c.stop(timeout=5)
            c.remove()
            return True, ""
        except DockerException:
            continue
    return False, "not found (already stopped?)"


def _stop_infra_container(client: docker.DockerClient, entry: dict) -> None:
    """Stop and remove an infra container (db, api) recorded in state."""
    name = entry.get("name", "")
    cid  = entry.get("container_id", "")
    for lookup in filter(None, [cid, name]):
        try:
            c = client.containers.get(lookup)
            c.stop(timeout=5)
            c.remove()
            console.print(f"  [green]✓[/green] Stopped and removed [bold]{name}[/bold]")
            return
        except DockerException:
            continue
    console.print(f"  [dim]Infra container '{name}' not found (already stopped?)[/dim]")


@click.command("down")
@click.option("--file", "-f", "agentfile_path", default=None,
              help="Path to agentfile.yaml (used to identify the swarm)")
@click.option("--swarm", default=None, help="Swarm name to stop (alternative to --file)")
def down_cmd(agentfile_path: str | None, swarm: str | None) -> None:
    """Stop the warm pool containers and remove the Docker network."""
    console.print()
    console.print("[bold purple]ninetrix down[/bold purple]\n")

    af = None
    if agentfile_path:
        try:
            af = AgentFile.from_path(agentfile_path)
        except (FileNotFoundError, ValueError) as exc:
            console.print(f"[yellow]Warning:[/yellow] Could not parse agentfile: {exc}")

    swarm_name, state = _find_swarm(af, swarm)
    client = _docker_client()
    agents = state.get("agents", {})
    infra = state.get("infra_containers", [])  # [{name, container_id}, ...] in start order

    # 1. Stop all agent containers in parallel
    results: dict[str, tuple[bool, str]] = {}
    with console.status(
        f"  Stopping [bold]{', '.join(agents)}[/bold]…", spinner="dots"
    ):
        with ThreadPoolExecutor(max_workers=max(len(agents), 1)) as pool:
            futures = {
                pool.submit(_stop_container, client, name, info): name
                for name, info in agents.items()
            }
            for future in as_completed(futures):
                name = futures[future]
                results[name] = future.result()

    for name, (ok, msg) in results.items():
        if ok:
            console.print(f"  [green]✓[/green] Stopped and removed [bold]{name}[/bold]")
        else:
            console.print(f"  [dim]Container '{name}' {msg}[/dim]")

    # 2. Stop infra containers in reverse start order (api → db)
    for entry in reversed(infra):
        _stop_infra_container(client, entry)

    # 3. Remove network (must come after all containers are detached)
    try:
        with console.status(f"  Removing network [bold]{swarm_name}[/bold]…", spinner="dots"):
            network = client.networks.get(swarm_name)
            network.remove()
        console.print(f"  [green]✓[/green] Removed network [bold]{swarm_name}[/bold]")
    except DockerException:
        console.print(f"  [dim]Network '{swarm_name}' not found (already removed?)[/dim]")

    # 4. Delete state file
    state_file = _STATE_DIR / f"{swarm_name}.json"
    if state_file.exists():
        state_file.unlink()

    console.print(f"\n  [bold]Stack [green]down[/green].[/bold]\n")
