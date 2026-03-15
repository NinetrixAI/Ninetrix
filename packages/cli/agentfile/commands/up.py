"""ninetrix up — start the full multi-agent warm pool on a Docker bridge network."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

import click
import docker
import httpx
from docker.errors import DockerException
from rich.console import Console

from agentfile.core.models import AgentFile, AgentDef, _parse_memory

console = Console()


def _is_gateway_running() -> bool:
    """Return True if the local MCP Gateway is reachable on localhost:8080."""
    try:
        r = httpx.get("http://localhost:8080/health", timeout=1.0)
        return r.status_code < 500
    except Exception:
        return False


_KEY_ENV_VARS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai":    "OPENAI_API_KEY",
    "google":    "GEMINI_API_KEY",
    "mistral":   "MISTRAL_API_KEY",
    "groq":      "GROQ_API_KEY",
}

_STATE_DIR = Path.home() / ".agentfile" / "pools"
INVOKE_PORT = 9000  # internal port each agent listens on for /invoke


def _load_dotenv_key(key: str) -> str | None:
    env_file = Path(".env")
    if not env_file.exists():
        return None
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _fetch_integration_credentials() -> dict[str, str]:
    """Return a flat dict of env vars from connected integrations. Empty on failure."""
    from agentfile.core.auth import auth_headers
    api_url = os.environ.get("AGENTFILE_API_URL", "http://localhost:8000")
    try:
        resp = httpx.get(
            f"{api_url}/integrations/credentials",
            headers=auth_headers(api_url),
            timeout=3,
        )
        if resp.status_code == 200:
            creds: dict[str, str] = {}
            for _integration_id, pairs in resp.json().items():
                creds.update(pairs)
            return creds
    except Exception:
        pass
    return {}


def _swarm_name(af: AgentFile) -> str:
    return f"agentfile-{af.entry_agent.name}-swarm"


def _state_file(swarm: str) -> Path:
    return _STATE_DIR / f"{swarm}.json"


def _build_agent_env(
    af: AgentFile,
    agent_def: AgentDef,
    agent_name: str,
    peer_urls: dict[str, str],
    warn: bool = True,
) -> dict[str, str]:
    """Build the full env dict for one agent container."""
    env: dict[str, str] = {
        "AGENTFILE_PROVIDER":      agent_def.provider,
        "AGENTFILE_MODEL":         agent_def.model,
        "AGENTFILE_TEMPERATURE":   str(agent_def.temperature),
        "AGENTFILE_INVOKE_PORT":   str(INVOKE_PORT),
        "AGENTFILE_SYSTEM_PROMPT": agent_def.system_prompt,
    }

    key_var = _KEY_ENV_VARS.get(agent_def.provider)
    if key_var:
        val = os.environ.get(key_var) or _load_dotenv_key(key_var) or ""
        if val:
            env[key_var] = val
        elif warn:
            console.print(
                f"  [yellow]Warning:[/yellow] {key_var} not set — "
                f"agent '{agent_name}' may fail at runtime."
            )

    # Forward verifier API key if it uses a different provider than the main agent
    eff_exec = af.effective_execution(agent_def)
    verifier_provider = eff_exec.verifier.provider or agent_def.provider
    verifier_key_var = _KEY_ENV_VARS.get(verifier_provider)
    if eff_exec.verify_steps and verifier_key_var and verifier_key_var != key_var:
        val = os.environ.get(verifier_key_var) or _load_dotenv_key(verifier_key_var) or ""
        if val:
            env[verifier_key_var] = val
        elif warn:
            console.print(
                f"  [yellow]Warning:[/yellow] {verifier_key_var} not set — "
                f"verifier for '{agent_name}' (provider: {verifier_provider}) may fail at runtime."
            )

    if any(t.is_composio() for t in agent_def.tools):
        for cvar in ("COMPOSIO_API_KEY", "COMPOSIO_ENTITY_ID"):
            val = os.environ.get(cvar) or _load_dotenv_key(cvar) or ""
            if val:
                env[cvar] = val

    eff_pers = af.effective_persistence(agent_def)
    if eff_pers:
        m = re.search(r'\$\{([^}]+)\}', eff_pers.url)
        if m:
            var_name = m.group(1)
            val = os.environ.get(var_name) or _load_dotenv_key(var_name) or ""
            if val:
                env[var_name] = val

    # Forward SaaS credentials so agents can phone home with thread events.
    # Rewrite localhost → host.docker.internal so containers can reach the host API.
    for _var in ("AGENTFILE_RUNNER_TOKEN", "AGENTFILE_API_URL"):
        _val = os.environ.get(_var) or _load_dotenv_key(_var)
        if _val:
            if _var == "AGENTFILE_API_URL":
                _val = _val.replace("localhost", "host.docker.internal") \
                           .replace("127.0.0.1", "host.docker.internal")
            env[_var] = _val

    # Forward MCP gateway connection vars — rewrite localhost so containers can reach
    # a gateway running on the host (e.g. started by `ninetrix dev`).
    # Auto-detect: if the local gateway is up, wire agents to it automatically.
    _gw_running = _is_gateway_running()
    for _var in ("MCP_GATEWAY_URL", "MCP_GATEWAY_TOKEN", "MCP_GATEWAY_WORKSPACE"):
        _val = os.environ.get(_var) or _load_dotenv_key(_var)
        if _val:
            if _var == "MCP_GATEWAY_URL":
                _val = _val.replace("localhost", "host.docker.internal") \
                           .replace("127.0.0.1", "host.docker.internal")
            env.setdefault(_var, _val)
    if _gw_running:
        env.setdefault("MCP_GATEWAY_URL", "http://host.docker.internal:8080")
        env.setdefault("MCP_GATEWAY_WORKSPACE", "default")

    for peer_name, peer_url in peer_urls.items():
        if peer_name != agent_name:
            env[f"AGENTFILE_PEER_{peer_name.upper()}_URL"] = peer_url

    # Inject S3 volume env vars
    for v in af.effective_volumes(agent_def):
        if v.provider != "s3":
            continue
        key = v.name.upper().replace("-", "_")
        env[f"AGENTFILE_VOL_{key}_BUCKET"] = os.path.expandvars(v.bucket or "")
        env[f"AGENTFILE_VOL_{key}_PREFIX"] = os.path.expandvars(v.prefix or "")
        env[f"AGENTFILE_VOL_{key}_PATH"] = v.container_path
        for aws_var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION"):
            val = os.environ.get(aws_var) or _load_dotenv_key(aws_var) or ""
            if val:
                env[aws_var] = val

    # Forward any AGENTFILE_* runtime overrides from the host env (don't overwrite
    # values already set above — e.g. AGENTFILE_PROVIDER always comes from the yaml).
    for _k, _v in os.environ.items():
        if _k.startswith("AGENTFILE_"):
            env.setdefault(_k, _v)

    return env


def _docker_client() -> docker.DockerClient:
    try:
        return docker.from_env()
    except DockerException as exc:
        console.print(f"[red]Docker is not running or not installed:[/red] {exc}")
        raise SystemExit(1)


@click.command("up")
@click.option("--file", "-f", "agentfile_path", default="agentfile.yaml",
              show_default=True, help="Path to agentfile.yaml")
@click.option("--tag", "-t", default="latest", show_default=True, help="Image tag")
@click.option("--detach", "-d", is_flag=True, default=True, show_default=True,
              help="Run in background (default: true)")
def up_cmd(agentfile_path: str, tag: str, detach: bool) -> None:
    """Start all agents in a Docker bridge network (multi-agent warm pool)."""
    console.print()
    console.print("[bold purple]ninetrix up[/bold purple]\n")

    try:
        af = AgentFile.from_path(agentfile_path)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)

    if not af.is_multi_agent:
        console.print(
            "[yellow]Single-agent file.[/yellow] "
            "Use [bold]ninetrix run[/bold] to run it directly.\n"
        )
        raise SystemExit(0)

    errors = af.validate()
    if errors:
        console.print("[red]Validation failed:[/red]")
        for e in errors:
            console.print(f"    • {e}")
        raise SystemExit(1)

    client = _docker_client()
    swarm = _swarm_name(af)
    agent_names = list(af.agents.keys())

    # Compute host port assignments: entry agent gets INVOKE_PORT, others get +1, +2, …
    host_ports = {name: INVOKE_PORT + i for i, name in enumerate(agent_names)}

    # Build peer URL map (container-to-container, using Docker hostnames)
    peer_urls: dict[str, str] = {
        name: f"http://{name}:{INVOKE_PORT}" for name in agent_names
    }

    # 1. Create Docker bridge network
    try:
        with console.status(f"  Creating network [bold]{swarm}[/bold]…", spinner="dots"):
            client.networks.create(swarm, driver="bridge")
        console.print(f"  [green]✓[/green] Created network [bold]{swarm}[/bold]")
    except DockerException:
        client.networks.get(swarm)
        console.print(f"  [dim]Reusing existing network [bold]{swarm}[/bold][/dim]")

    # Fetch integration credentials once and inject into all agents
    integration_creds = _fetch_integration_credentials()

    # 2. Start each agent container
    container_ids: dict[str, str] = {}
    for name, agent_def in af.agents.items():
        image_ref = agent_def.image_name(tag)
        host_port = host_ports[name]

        env = _build_agent_env(af, agent_def, name, peer_urls)
        # Inject integration hub credentials (don't overwrite already-set vars)
        for k, v in integration_creds.items():
            env.setdefault(k, v)

        # Remove any existing container with this name
        try:
            old = client.containers.get(f"agentfile-{name}")
            old.remove(force=True)
            console.print(f"  [dim]Removed stale container for '{name}'[/dim]")
        except DockerException:
            pass

        res = agent_def.resources
        nano_cpus = int(res.cpu * 1e9) if res.cpu is not None else None
        mem_limit = _parse_memory(res.memory) if res.memory else None

        # Build bind-mount volumes for local providers
        local_vols = af.effective_volumes(agent_def)
        bind_mounts: dict[str, dict] = {}
        for v in local_vols:
            if v.provider == "local" and v.host_path:
                host_path = os.path.expandvars(v.host_path)
                host_path = str(Path(host_path).resolve())
                mode = "ro" if v.read_only else "rw"
                bind_mounts[host_path] = {"bind": v.container_path, "mode": mode}

        run_kwargs: dict = dict(
            name=f"agentfile-{name}",
            hostname=name,
            network=swarm,
            ports={f"{INVOKE_PORT}/tcp": host_port},
            environment=env,
            extra_hosts={"host.docker.internal": "host-gateway"},
            detach=True,
            remove=False,
        )
        if nano_cpus is not None:
            run_kwargs["nano_cpus"] = nano_cpus
        if mem_limit is not None:
            run_kwargs["mem_limit"] = mem_limit
        if bind_mounts:
            run_kwargs["volumes"] = bind_mounts

        try:
            with console.status(f"  Starting [bold]{name}[/bold]…", spinner="dots"):
                container = client.containers.run(image_ref, **run_kwargs)
            container_ids[name] = container.id
            console.print(
                f"  [green]✓[/green] Started [bold]{name}[/bold] "
                f"({image_ref}) → localhost:{host_port}"
            )
        except DockerException as exc:
            console.print(f"  [red]Failed to start '{name}':[/red] {exc}")

    # 3. Save pool state (include resource limits for rollback/restart)
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    agents_state: dict[str, Any] = {}
    for name, cid in container_ids.items():
        res = af.agents[name].resources
        entry: dict[str, Any] = {
            "container_id": cid,
            "host_port": host_ports[name],
            "image": af.agents[name].image_name(tag),
        }
        if res.cpu is not None:
            entry["nano_cpus"] = int(res.cpu * 1e9)
        if res.memory:
            entry["mem_limit"] = _parse_memory(res.memory)
        agents_state[name] = entry

    state = {
        "swarm": swarm,
        "tag": tag,
        "agentfile": str(Path(agentfile_path).resolve()),
        "agents": agents_state,
        "started_at": time.time(),
    }
    _state_file(swarm).write_text(json.dumps(state, indent=2))

    console.print()
    console.print(f"  [bold green]Warm pool ready.[/bold green] Swarm: [bold]{swarm}[/bold]")
    console.print(f"  Entry agent: [bold]{af.entry_agent.name}[/bold] → localhost:{host_ports[af.entry_agent.name]}")
    console.print()
    console.print("  Commands:")
    console.print("    ninetrix status")
    console.print(f"    ninetrix invoke --agent {af.entry_agent.name} -m \"your task\"")
    console.print("    ninetrix logs --follow")
    console.print("    ninetrix down\n")
