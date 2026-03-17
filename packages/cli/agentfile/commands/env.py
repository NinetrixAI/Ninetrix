"""ninetrix env — set or list env vars in running warm-pool containers."""

from __future__ import annotations

import json
from pathlib import Path

import click
import docker
from docker.errors import DockerException
from rich.console import Console
from rich.table import Table

from agentfile.core.models import AgentFile
from agentfile.commands.up import _build_agent_env, INVOKE_PORT

console = Console()

_STATE_DIR = Path.home() / ".agentfile" / "pools"
_SENSITIVE_PATTERNS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASS", "CREDENTIAL")


def _is_sensitive(key: str) -> bool:
    k = key.upper()
    return any(pat in k for pat in _SENSITIVE_PATTERNS)


def _docker_client() -> docker.DockerClient:
    try:
        return docker.from_env()
    except DockerException as exc:
        console.print(f"[red]Docker is not running or not installed:[/red] {exc}")
        raise SystemExit(1)


def _find_pool_state(agent_name: str | None) -> tuple[dict, Path] | tuple[None, None]:
    """Find pool state file containing agent_name (or most recent if None)."""
    if not _STATE_DIR.exists():
        return None, None
    state_files = list(_STATE_DIR.glob("*.json"))
    if not state_files:
        return None, None
    if agent_name is None:
        sf = max(state_files, key=lambda p: p.stat().st_mtime)
        try:
            return json.loads(sf.read_text()), sf
        except Exception:
            return None, None
    for sf in state_files:
        try:
            s = json.loads(sf.read_text())
            if agent_name in s.get("agents", {}):
                return s, sf
        except Exception:
            continue
    return None, None


def _resolve_agent_names(state: dict, agent_name: str | None) -> list[str]:
    agents = list(state.get("agents", {}).keys())
    if agent_name:
        if agent_name not in agents:
            console.print(f"[red]Agent '{agent_name}' not found in pool state.[/red]")
            console.print(f"  Available: {', '.join(agents)}")
            raise SystemExit(1)
        return [agent_name]
    return agents


def _restart_with_env(
    client: docker.DockerClient,
    agent_name: str,
    new_env: dict[str, str],
    state: dict,
    state_file: Path,
    image: str,
    host_port: int,
    swarm: str,
) -> None:
    """Stop a container and restart it with an updated env (no image rebuild)."""
    container_name = f"agentfile-{agent_name}"
    agent_info = state["agents"][agent_name]

    try:
        with console.status(f"  Stopping [bold]{agent_name}[/bold]…", spinner="dots"):
            c = client.containers.get(agent_info.get("container_id") or container_name)
            c.stop(timeout=5)
            c.remove()
        console.print(f"  [green]✓[/green] Stopped [bold]{agent_name}[/bold]")
    except DockerException:
        console.print(f"  [dim]{agent_name} was already stopped[/dim]")

    run_kwargs: dict = dict(
        name=container_name,
        hostname=agent_name,
        network=swarm,
        ports={f"{INVOKE_PORT}/tcp": host_port},
        environment=new_env,
        extra_hosts={"host.docker.internal": "host-gateway"},
        detach=True,
        remove=False,
    )
    if agent_info.get("nano_cpus"):
        run_kwargs["nano_cpus"] = agent_info["nano_cpus"]
    if agent_info.get("mem_limit"):
        run_kwargs["mem_limit"] = agent_info["mem_limit"]

    try:
        with console.status(f"  Restarting [bold]{agent_name}[/bold]…", spinner="dots"):
            container = client.containers.run(image, **run_kwargs)
        agent_info["container_id"] = container.id
        state_file.write_text(json.dumps(state, indent=2))
        console.print(
            f"  [green]✓[/green] Restarted [bold]{agent_name}[/bold] → localhost:{host_port}"
        )
    except DockerException as exc:
        console.print(f"  [red]Failed to restart '{agent_name}':[/red] {exc}")
        raise SystemExit(1)


@click.group("env")
def env_cmd() -> None:
    """Manage environment variables in running warm-pool containers."""


@env_cmd.command("set")
@click.argument("assignments", nargs=-1, required=True, metavar="KEY=VALUE ...")
@click.option("--agent", "-a", "agent_name", default=None,
              help="Agent key to target (default: all agents in the pool).")
@click.option("--no-restart", is_flag=True, default=False,
              help="Save override to pool state only; do not restart the container.")
def env_set_cmd(
    assignments: tuple[str, ...],
    agent_name: str | None,
    no_restart: bool,
) -> None:
    """Inject or rotate env vars in a running warm-pool container.

    \b
    Examples:
      ninetrix env set ANTHROPIC_API_KEY=sk-new-key
      ninetrix env set DEBUG=true --agent orchestrator
      ninetrix env set A=1 B=2 --agent worker
      ninetrix env set MY_VAR=value --no-restart
    """
    console.print()
    console.print("[bold purple]ninetrix env set[/bold purple]\n")

    overrides: dict[str, str] = {}
    for a in assignments:
        if "=" not in a:
            console.print(f"[red]Invalid assignment:[/red] {a!r}  (expected KEY=VALUE)")
            raise SystemExit(1)
        k, v = a.split("=", 1)
        overrides[k] = v

    state, state_file = _find_pool_state(agent_name)
    if state is None:
        console.print("[red]No running pool found.[/red]")
        console.print("  Run [bold]ninetrix up[/bold] first.")
        raise SystemExit(1)

    target_agents = _resolve_agent_names(state, agent_name)
    swarm = state["swarm"]
    agentfile_path = state.get("agentfile", "agentfile.yaml")
    tag = state.get("tag", "latest")

    try:
        af = AgentFile.from_path(agentfile_path)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]Could not load agentfile:[/red] {exc}")
        raise SystemExit(1)

    client = _docker_client()

    for name in target_agents:
        agent_def = af.agents.get(name)
        if agent_def is None:
            console.print(f"  [yellow]Warning:[/yellow] Agent '{name}' not in agentfile, skipping.")
            continue

        agent_info = state["agents"][name]
        existing: dict[str, str] = agent_info.get("env_overrides", {})
        existing.update(overrides)
        agent_info["env_overrides"] = existing

        for k, v in overrides.items():
            display = "***" if _is_sensitive(k) else v
            console.print(f"  [bold]{name}[/bold]: {k} = {display}")

        if no_restart:
            continue

        all_agent_names = list(state["agents"].keys())
        peer_urls = {n: f"http://{n}:{INVOKE_PORT}" for n in all_agent_names}
        env = _build_agent_env(af, agent_def, name, peer_urls, warn=False)
        env.update(existing)

        image = agent_info.get("image") or agent_def.image_name(tag)
        host_port = agent_info["host_port"]

        _restart_with_env(client, name, env, state, state_file, image, host_port, swarm)

    state_file.write_text(json.dumps(state, indent=2))

    if no_restart:
        console.print(
            "\n  [dim]Saved. The override will be applied on next restart.[/dim]\n"
        )
    else:
        console.print()


@env_cmd.command("list")
@click.option("--agent", "-a", "agent_name", default=None,
              help="Agent key to inspect (default: entry agent).")
@click.option("--show-values", is_flag=True, default=False,
              help="Show full values (disables redaction of sensitive vars).")
def env_list_cmd(agent_name: str | None, show_values: bool) -> None:
    """List environment variables in a running warm-pool container.

    Sensitive variables (names containing KEY, TOKEN, SECRET, PASSWORD) are
    redacted by default. Pass --show-values to reveal them.
    """
    console.print()
    console.print("[bold purple]ninetrix env list[/bold purple]\n")

    state, _sf = _find_pool_state(agent_name)
    if state is None:
        console.print("[red]No running pool found.[/red]")
        console.print("  Run [bold]ninetrix up[/bold] first.")
        raise SystemExit(1)

    all_agents = _resolve_agent_names(state, agent_name)
    # Default to entry agent (first in pool) when no --agent specified
    target_agents = [all_agents[0]] if agent_name is None else all_agents

    client = _docker_client()

    for name in target_agents:
        agent_info = state["agents"][name]
        container_name = f"agentfile-{name}"

        raw_env: list[str] = []
        try:
            container = client.containers.get(
                agent_info.get("container_id") or container_name
            )
            raw_env = container.attrs.get("Config", {}).get("Env") or []
        except DockerException:
            console.print(
                f"  [yellow]{name}[/yellow]: container not running — "
                "showing saved overrides only\n"
            )

        env_dict: dict[str, str] = {}
        for entry in raw_env:
            if "=" in entry:
                k, v = entry.split("=", 1)
                env_dict[k] = v

        # Pool-state overrides are authoritative for what was last set
        env_dict.update(agent_info.get("env_overrides", {}))

        table = Table(
            title=f"[bold]{name}[/bold]",
            show_header=True,
            header_style="bold dim",
        )
        table.add_column("Variable", style="cyan", no_wrap=True)
        table.add_column("Value")

        for k in sorted(env_dict):
            v = env_dict[k]
            if not show_values and _is_sensitive(k):
                suffix = v[-4:] if len(v) >= 4 else v
                v = f"***{suffix}"
            table.add_row(k, v)

        console.print(table)
        console.print()
