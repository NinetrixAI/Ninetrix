"""ninetrix restart — stop, rebuild, and restart a single agent in the warm pool."""

from __future__ import annotations

import json
from pathlib import Path

import click
import docker
from docker.errors import DockerException
from rich.console import Console

from agentfile.core.models import AgentFile
from agentfile.commands.build import _build_one
from agentfile.commands.up import _build_agent_env, INVOKE_PORT

console = Console()

_STATE_DIR = Path.home() / ".agentfile" / "pools"


def _docker_client() -> docker.DockerClient:
    try:
        return docker.from_env()
    except DockerException as exc:
        console.print(f"[red]Docker is not running or not installed:[/red] {exc}")
        raise SystemExit(1)


@click.command("restart")
@click.option("--agent", "-a", "agent_name", required=True,
              help="Agent key to restart (must match a key in agentfile.yaml)")
@click.option("--file", "-f", "agentfile_path", default="agentfile.yaml",
              show_default=True, help="Path to agentfile.yaml")
@click.option("--tag", "-t", default="latest", show_default=True,
              help="Image tag to rebuild and restart")
def restart_cmd(agent_name: str, agentfile_path: str, tag: str) -> None:
    """Stop, rebuild, and restart one agent without taking the whole swarm down."""
    console.print()
    console.print("[bold purple]ninetrix restart[/bold purple]\n")

    # 1. Load agentfile
    try:
        af = AgentFile.from_path(agentfile_path)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)

    if agent_name not in af.agents:
        console.print(f"[red]Agent '{agent_name}' not found in agentfile.[/red]")
        console.print(f"  Available: {', '.join(af.agents.keys())}")
        raise SystemExit(1)

    agent_def = af.agents[agent_name]

    # 2. Load pool state
    state_files = list(_STATE_DIR.glob("*.json")) if _STATE_DIR.exists() else []
    state = None
    state_file_path = None
    for sf in state_files:
        s = json.loads(sf.read_text())
        if agent_name in s.get("agents", {}):
            state = s
            state_file_path = sf
            break

    if state is None:
        console.print(f"[red]No running pool found containing agent '{agent_name}'.[/red]")
        console.print("  Run [bold]ninetrix up[/bold] first.")
        raise SystemExit(1)

    swarm = state["swarm"]
    agent_info = state["agents"][agent_name]
    host_port = agent_info["host_port"]
    container_name = f"agentfile-{agent_name}"

    # Reconstruct peer URLs (container-to-container, same as up.py)
    all_agent_names = list(state["agents"].keys())
    peer_urls = {n: f"http://{n}:{INVOKE_PORT}" for n in all_agent_names}

    client = _docker_client()

    # 3. Stop and remove existing container
    try:
        with console.status(f"  Stopping [bold]{agent_name}[/bold]…", spinner="dots"):
            c = client.containers.get(agent_info.get("container_id") or container_name)
            c.stop(timeout=5)
            c.remove()
        console.print(f"  [green]✓[/green] Stopped [bold]{agent_name}[/bold]")
    except DockerException:
        try:
            with console.status(f"  Stopping [bold]{agent_name}[/bold]…", spinner="dots"):
                c = client.containers.get(container_name)
                c.stop(timeout=5)
                c.remove()
            console.print(f"  [green]✓[/green] Stopped [bold]{agent_name}[/bold]")
        except DockerException:
            console.print(f"  [dim]{agent_name} was already stopped[/dim]")

    # 4. Rebuild image
    with console.status(
        f"  Rebuilding [bold]{agent_def.image_name(tag)}[/bold]…", spinner="dots"
    ):
        ok, full_tag, lines = _build_one(agent_name, agent_def, af, agentfile_path, tag)

    if not ok:
        msg = lines[-1] if lines else "unknown error"
        console.print(f"  [red]✗[/red] Build failed: {msg}")
        raise SystemExit(1)
    console.print(f"  [green]✓[/green] Built [bold]{full_tag}[/bold]")

    # 5. Start new container on the same network with the same config
    env = _build_agent_env(af, agent_def, agent_name, peer_urls)
    run_kwargs: dict = dict(
        name=container_name,
        hostname=agent_name,
        network=swarm,
        ports={f"{INVOKE_PORT}/tcp": host_port},
        environment=env,
        extra_hosts={"host.docker.internal": "host-gateway"},
        detach=True,
        remove=False,
    )
    if agent_info.get("nano_cpus"):
        run_kwargs["nano_cpus"] = agent_info["nano_cpus"]
    if agent_info.get("mem_limit"):
        run_kwargs["mem_limit"] = agent_info["mem_limit"]

    try:
        with console.status(f"  Starting [bold]{agent_name}[/bold]…", spinner="dots"):
            container = client.containers.run(full_tag, **run_kwargs)
        console.print(
            f"  [green]✓[/green] Restarted [bold]{agent_name}[/bold] → localhost:{host_port}"
        )
    except DockerException as exc:
        console.print(f"  [red]Failed to start '{agent_name}':[/red] {exc}")
        raise SystemExit(1)

    # 6. Update state file with new container ID
    state["agents"][agent_name]["container_id"] = container.id
    state["agents"][agent_name]["image"] = full_tag
    state_file_path.write_text(json.dumps(state, indent=2))

    console.print(f"\n  [bold]{agent_name} is back online.[/bold]\n")
