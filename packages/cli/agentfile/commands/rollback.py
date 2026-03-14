"""ninetrix rollback — switch one agent to a previous image tag without rebuilding."""

from __future__ import annotations

import json
from pathlib import Path

import click
import docker
from docker.errors import DockerException, ImageNotFound
from rich.console import Console

from agentfile.core.models import AgentFile
from agentfile.commands.up import _build_agent_env, INVOKE_PORT

console = Console()

_STATE_DIR = Path.home() / ".agentfile" / "pools"


def _docker_client() -> docker.DockerClient:
    try:
        return docker.from_env()
    except DockerException as exc:
        console.print(f"[red]Docker is not running or not installed:[/red] {exc}")
        raise SystemExit(1)


@click.command("rollback")
@click.option("--agent", "-a", "agent_name", required=True,
              help="Agent key to roll back (must match a key in agentfile.yaml)")
@click.option("--tag", "-t", required=True,
              help="Image tag to roll back to (e.g. 'v1', 'stable', 'latest')")
@click.option("--file", "-f", "agentfile_path", default="agentfile.yaml",
              show_default=True, help="Path to agentfile.yaml")
def rollback_cmd(agent_name: str, tag: str, agentfile_path: str) -> None:
    """Switch one agent to a previous image tag — no rebuild required."""
    console.print()
    console.print("[bold purple]ninetrix rollback[/bold purple]\n")

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
    target_image = agent_def.image_name(tag)

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
    current_image = agent_info.get("image", "unknown")

    # 3. Confirm the target image exists locally
    client = _docker_client()
    try:
        with console.status(f"  Checking image [bold]{target_image}[/bold]…", spinner="dots"):
            client.images.get(target_image)
        console.print(f"  [green]✓[/green] Image [bold]{target_image}[/bold] found")
    except ImageNotFound:
        console.print(f"  [red]✗[/red] Image [bold]{target_image}[/bold] not found locally.")
        console.print(f"  Run [bold]ninetrix build --tag {tag} --agent {agent_name}[/bold] first.")
        raise SystemExit(1)
    except DockerException as exc:
        console.print(f"  [red]Docker error:[/red] {exc}")
        raise SystemExit(1)

    console.print(f"  Rolling back [bold]{agent_name}[/bold]:")
    console.print(f"    {current_image}  →  {target_image}")
    console.print()

    # 4. Stop and remove the current container
    try:
        with console.status(f"  Stopping [bold]{agent_name}[/bold]…", spinner="dots"):
            c = client.containers.get(agent_info.get("container_id") or container_name)
            c.stop(timeout=5)
            c.remove()
        console.print("  [green]✓[/green] Stopped current container")
    except DockerException:
        try:
            with console.status(f"  Stopping [bold]{agent_name}[/bold]…", spinner="dots"):
                c = client.containers.get(container_name)
                c.stop(timeout=5)
                c.remove()
            console.print("  [green]✓[/green] Stopped current container")
        except DockerException:
            console.print(f"  [dim]{agent_name} was already stopped[/dim]")

    # 5. Reconstruct peer URLs and start new container with target image
    all_agent_names = list(state["agents"].keys())
    peer_urls = {n: f"http://{n}:{INVOKE_PORT}" for n in all_agent_names}
    env = _build_agent_env(af, agent_def, agent_name, peer_urls, warn=False)

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
        with console.status(f"  Starting [bold]{agent_name}[/bold] ({tag})…", spinner="dots"):
            container = client.containers.run(target_image, **run_kwargs)
        console.print(
            f"  [green]✓[/green] Started [bold]{agent_name}[/bold] ({tag}) → localhost:{host_port}"
        )
    except DockerException as exc:
        console.print(f"  [red]Failed to start '{agent_name}':[/red] {exc}")
        raise SystemExit(1)

    # 6. Update state file
    state["agents"][agent_name]["container_id"] = container.id
    state["agents"][agent_name]["image"] = target_image
    state_file_path.write_text(json.dumps(state, indent=2))

    console.print(f"\n  [bold]{agent_name} rolled back to [green]{tag}[/green].[/bold]\n")
