"""ninetrix compose — start agents + postgres + observability API in one command.

Like `ninetrix up` but also starts a local PostgreSQL database and the
agentfile observability API so the full stack runs with a single command.
Use `ninetrix down` to tear everything down.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any

import click
import docker
from docker.errors import DockerException
from rich.console import Console

from agentfile.core.errors import docker_fail, fail, fmt_docker_error
from agentfile.core.models import AgentDef, AgentFile, _parse_memory

console = Console()

INVOKE_PORT = 9000
_STATE_DIR = Path.home() / ".agentfile" / "pools"

_KEY_ENV_VARS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai":    "OPENAI_API_KEY",
    "google":    "GEMINI_API_KEY",
    "mistral":   "MISTRAL_API_KEY",
    "groq":      "GROQ_API_KEY",
}

_POSTGRES_USER = "postgres"
_POSTGRES_PASS = "postgres"
_POSTGRES_DB   = "agentfile"


def _load_dotenv_key(key: str) -> str | None:
    env_file = Path(".env")
    if not env_file.exists():
        return None
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _docker_client() -> docker.DockerClient:
    try:
        return docker.from_env()
    except DockerException as exc:
        fail(f"Docker is not running or not installed: {exc}")


def _swarm_name(af: AgentFile) -> str:
    return f"agentfile-{af.entry_agent.name}-swarm"


def _state_file(swarm: str) -> Path:
    return _STATE_DIR / f"{swarm}.json"


def _db_url(hostname: str = "db", port: int = 5432) -> str:
    return f"postgresql://{_POSTGRES_USER}:{_POSTGRES_PASS}@{hostname}:{port}/{_POSTGRES_DB}"


def _wait_for_postgres(container: Any, timeout: int = 30) -> bool:
    for _ in range(timeout):
        try:
            result = container.exec_run(
                f"pg_isready -U {_POSTGRES_USER} -d {_POSTGRES_DB}",
                demux=False,
            )
            if result.exit_code == 0:
                return True
        except DockerException:
            pass
        time.sleep(1)
    return False


def _wait_for_api(host_port: int, timeout: int = 20) -> bool:
    try:
        import httpx
    except ImportError:
        time.sleep(3)
        return True
    for _ in range(timeout):
        try:
            r = httpx.get(f"http://localhost:{host_port}/health", timeout=1)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def _cleanup_containers(client: docker.DockerClient, containers: list[dict]) -> None:
    """Best-effort removal of containers that were started before a failure."""
    for entry in reversed(containers):
        name = entry.get("name", "")
        cid  = entry.get("container_id", "")
        for lookup in filter(None, [cid, name]):
            try:
                client.containers.get(lookup).remove(force=True)
                break
            except DockerException:
                continue


def _remove_network(client: docker.DockerClient, swarm: str) -> None:
    try:
        client.networks.get(swarm).remove()
    except DockerException:
        pass


def _remove_stale(client: docker.DockerClient, name: str) -> None:
    try:
        client.containers.get(name).remove(force=True)
        console.print(f"  [dim]Removed stale container '{name}'[/dim]")
    except DockerException:
        pass


def _build_agent_env(
    af: AgentFile,
    agent_name: str,
    agent_def: AgentDef,
    peer_urls: dict[str, str],
    runner_token: str,
    api_hostname: str,
    db_hostname: str,
    db_port: int,
) -> dict[str, str]:
    env: dict[str, str] = {
        "AGENTFILE_PROVIDER":      agent_def.provider,
        "AGENTFILE_MODEL":         agent_def.model,
        "AGENTFILE_TEMPERATURE":   str(agent_def.temperature),
        "AGENTFILE_INVOKE_PORT":   str(INVOKE_PORT),
        "AGENTFILE_SYSTEM_PROMPT": agent_def.system_prompt,
        "AGENTFILE_API_URL":       f"http://{api_hostname}:8000",
        "AGENTFILE_RUNNER_TOKEN":  runner_token,
    }

    key_var = _KEY_ENV_VARS.get(agent_def.provider)
    if key_var:
        val = os.environ.get(key_var) or _load_dotenv_key(key_var) or ""
        if val:
            env[key_var] = val
        else:
            console.print(
                f"  [yellow]⚠[/yellow]  {key_var} not set — "
                f"agent '{agent_name}' may fail at runtime."
            )

    eff_exec = af.effective_execution(agent_def)
    verifier_provider = eff_exec.verifier.provider or agent_def.provider
    verifier_key_var = _KEY_ENV_VARS.get(verifier_provider)
    if eff_exec.verify_steps and verifier_key_var and verifier_key_var != key_var:
        val = os.environ.get(verifier_key_var) or _load_dotenv_key(verifier_key_var) or ""
        if val:
            env[verifier_key_var] = val

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
            if var_name == "DATABASE_URL":
                env["DATABASE_URL"] = _db_url(db_hostname, db_port)
            else:
                val = os.environ.get(var_name) or _load_dotenv_key(var_name) or ""
                if val:
                    env[var_name] = val

    for peer_name, peer_url in peer_urls.items():
        if peer_name != agent_name:
            env_key = f"AGENTFILE_PEER_{peer_name.upper().replace('-', '_')}_URL"
            env[env_key] = peer_url

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

    return env


def _find_api_dir(agentfile_path: str) -> Path | None:
    """Try to locate the api/ directory automatically."""
    candidates = [
        Path("api"),                                          # ./api (CWD)
        Path(agentfile_path).parent / "api",                  # next to agentfile
        Path(agentfile_path).parent.parent / "api",           # one level up
    ]
    for p in candidates:
        if (p / "Dockerfile").exists() and (p / "main.py").exists():
            return p.resolve()
    return None


def _build_api_image(client: docker.DockerClient, api_dir: Path, tag: str = "latest") -> str:
    """Build the agentfile API image locally. Returns the image ref."""
    image_ref = f"ninetrix-api:{tag}"
    console.print(f"  Building [bold]api[/bold] image from {api_dir} …")
    try:
        _image, logs = client.images.build(
            path=str(api_dir),
            tag=image_ref,
            rm=True,
            forcerm=True,
        )
        for chunk in logs:
            line = chunk.get("stream", "").rstrip()
            if line and not line.startswith("Step "):
                console.print(f"    [dim]{line}[/dim]")
        console.print(f"  [green]✓[/green] Built api image → [bold]{image_ref}[/bold]")
        return image_ref
    except DockerException as exc:
        docker_fail(exc, "Failed to build api image")


@click.command("compose")
@click.option("--file", "-f", "agentfile_path", default="agentfile.yaml",
              show_default=True, help="Path to agentfile.yaml")
@click.option("--tag", "-t", default="latest", show_default=True, help="Image tag")
@click.option("--api-image", default="ghcr.io/ninetrix-ai/ninetrix-api:latest",
              show_default=True, help="Docker image for the observability API")
@click.option("--build-api", is_flag=True, default=False,
              help="Build the API image locally before starting (requires --api-dir or auto-detect)")
@click.option("--api-dir", default=None, metavar="PATH",
              help="Path to the api/ source directory for --build-api (auto-detected if omitted)")
@click.option("--db-port", default=5432, show_default=True,
              help="Host port to bind PostgreSQL on")
@click.option("--api-port", default=8000, show_default=True,
              help="Host port to bind the observability API on")
@click.option("--runner-token", default=None,
              help="Shared secret for agent→API auth (auto-generated if omitted)")
def compose_cmd(
    agentfile_path: str,
    tag: str,
    api_image: str,
    build_api: bool,
    api_dir: str | None,
    db_port: int,
    api_port: int,
    runner_token: str | None,
) -> None:
    """Start agents + PostgreSQL + observability API as Docker containers.

    \b
    Starts the full stack on a Docker bridge network:
      db    — PostgreSQL 16 (shared persistence for all agents)
      api   — Agentfile observability API (traces, approvals, dashboard)
      <agents> — all agents from agentfile.yaml, wired together

    \b
    Use `ninetrix down` to stop everything.
    """
    console.print()
    console.print("[bold purple]ninetrix compose[/bold purple]\n")

    try:
        af = AgentFile.from_path(agentfile_path)
    except FileNotFoundError:
        fail(
            f"agentfile.yaml not found: {agentfile_path}",
            "Run 'ninetrix init' to scaffold a new agentfile.yaml.",
        )
    except ValueError as exc:
        fail(f"Invalid agentfile: {exc}")

    errors = af.validate()
    if errors:
        console.print("  [red]✗[/red] Validation failed:")
        for e in errors:
            console.print(f"      • {e}")
        raise SystemExit(1)

    client = _docker_client()

    # ── Build API image locally if requested ─────────────────────────────────
    if build_api:
        resolved_api_dir = Path(api_dir) if api_dir else _find_api_dir(agentfile_path)
        if resolved_api_dir is None:
            fail(
                "Cannot find the api/ source directory.",
                "Specify it with: ninetrix compose --build-api --api-dir path/to/api",
            )
        api_image = _build_api_image(client, resolved_api_dir, tag)

    swarm = _swarm_name(af)
    agent_names = list(af.agents.keys())

    runner_token = runner_token or (
        os.environ.get("AGENTFILE_RUNNER_TOKEN")
        or _load_dotenv_key("AGENTFILE_RUNNER_TOKEN")
        or f"nxt_{secrets.token_urlsafe(24)}"
    )

    host_ports = {name: INVOKE_PORT + i for i, name in enumerate(agent_names)}
    db_hostname  = "db"
    api_hostname = "api"
    peer_urls = {name: f"http://{name}:{INVOKE_PORT}" for name in agent_names}

    # Track every container we start so we can clean up on partial failure
    started: list[dict] = []

    def _abort(message: str, hint: str | None = None) -> None:
        """Clean up everything we started so far, then exit."""
        console.print(f"\n  [red]✗[/red] {message}")
        if hint:
            console.print(f"    [dim]Hint: {hint}[/dim]")
        if started:
            console.print("\n  Cleaning up started containers…")
            _cleanup_containers(client, started)
        _remove_network(client, swarm)
        raise SystemExit(1)

    # ── 1. Docker network ────────────────────────────────────────────────────
    try:
        network = client.networks.create(swarm, driver="bridge")
        console.print(f"  [green]✓[/green] Created network [bold]{swarm}[/bold]")
    except DockerException:
        try:
            network = client.networks.get(swarm)
            console.print(f"  [dim]Reusing existing network [bold]{swarm}[/bold][/dim]")
        except DockerException as exc:
            docker_fail(exc, "Failed to create Docker network")

    # ── 2. PostgreSQL ─────────────────────────────────────────────────────────
    _remove_stale(client, "agentfile-db")
    try:
        with console.status("  Starting [bold]db[/bold] (postgres:16-alpine)…", spinner="dots"):
            db_container = client.containers.run(
                "postgres:16-alpine",
                name="agentfile-db",
                hostname=db_hostname,
                network=swarm,
                ports={"5432/tcp": db_port},
                environment={
                    "POSTGRES_USER":     _POSTGRES_USER,
                    "POSTGRES_PASSWORD": _POSTGRES_PASS,
                    "POSTGRES_DB":       _POSTGRES_DB,
                },
                detach=True,
                remove=False,
            )
    except DockerException as exc:
        msg, hint = fmt_docker_error(exc)
        # Enrich the hint for port conflict
        if "already in use" in msg or "already allocated" in msg:
            hint = (
                f"Stop the existing PostgreSQL, or run: "
                f"ninetrix compose --db-port {db_port + 1}"
            )
        _abort(f"Failed to start db: {msg}", hint)

    started.append({"name": "agentfile-db", "container_id": db_container.id})

    with console.status("  Waiting for [bold]db[/bold] to be ready…", spinner="dots"):
        ready = _wait_for_postgres(db_container)

    if ready:
        console.print(f"  [green]✓[/green] db (postgres:16-alpine) → localhost:{db_port}")
    else:
        _abort(
            "PostgreSQL did not become ready in time",
            "Check 'docker logs agentfile-db' for details.",
        )

    # ── 3. Observability API ──────────────────────────────────────────────────
    _remove_stale(client, "ninetrix-api")
    try:
        with console.status("  Starting [bold]api[/bold]…", spinner="dots"):
            api_container = client.containers.run(
                api_image,
                name="ninetrix-api",
                hostname=api_hostname,
                network=swarm,
                ports={"8000/tcp": api_port},
                environment={
                    "DATABASE_URL":            _db_url(db_hostname),
                    "AGENTFILE_RUNNER_TOKENS": runner_token,
                },
                extra_hosts={"host.docker.internal": "host-gateway"},
                detach=True,
                remove=False,
            )
    except DockerException as exc:
        msg, hint = fmt_docker_error(exc)
        if "already in use" in msg or "already allocated" in msg:
            hint = (
                f"Stop the service on port {api_port}, or run: "
                f"ninetrix compose --api-port {api_port + 1}"
            )
        elif "Cannot pull" in msg or "not found" in msg.lower() or "403" in msg:
            hint = "Build the API image locally: ninetrix compose --build-api"
        _abort(f"Failed to start api: {msg}", hint)

    started.append({"name": "ninetrix-api", "container_id": api_container.id})

    with console.status("  Waiting for [bold]api[/bold] to be ready…", spinner="dots"):
        api_ready = _wait_for_api(api_port)

    if api_ready:
        console.print(f"  [green]✓[/green] api → localhost:{api_port}")
    else:
        console.print(
            "  [yellow]⚠[/yellow]  api health check timed out — "
            "run 'docker logs ninetrix-api' if it fails"
        )

    # ── 4. Agent containers ───────────────────────────────────────────────────
    container_ids: dict[str, str] = {}

    for name, agent_def in af.agents.items():
        image_ref = agent_def.image_name(tag)
        host_port = host_ports[name]

        env = _build_agent_env(
            af=af,
            agent_name=name,
            agent_def=agent_def,
            peer_urls=peer_urls,
            runner_token=runner_token,
            api_hostname=api_hostname,
            db_hostname=db_hostname,
            db_port=5432,  # internal port, always 5432 inside the network
        )

        _remove_stale(client, f"agentfile-{name}")

        res = agent_def.resources
        nano_cpus = int(res.cpu * 1e9) if res.cpu is not None else None
        mem_limit = _parse_memory(res.memory) if res.memory else None

        eff_triggers = af.effective_triggers(agent_def)
        webhook_triggers = [t for t in eff_triggers if t.type == "webhook"]
        port_map: dict[str, int] = {f"{INVOKE_PORT}/tcp": host_port}
        if webhook_triggers:
            trigger_ports = sorted({t.port for t in webhook_triggers})
            for p in trigger_ports:
                port_map[f"{p}/tcp"] = p
            env["AGENTFILE_WEBHOOK_PORT"] = str(trigger_ports[0])

        bind_mounts: dict[str, dict] = {}
        for v in af.effective_volumes(agent_def):
            if v.provider == "local" and v.host_path:
                host_path = str(Path(os.path.expandvars(v.host_path)).resolve())
                mode = "ro" if v.read_only else "rw"
                bind_mounts[host_path] = {"bind": v.container_path, "mode": mode}

        run_kwargs: dict[str, Any] = dict(
            name=f"agentfile-{name}",
            hostname=name,
            network=swarm,
            ports=port_map,
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
        except DockerException as exc:
            msg, hint = fmt_docker_error(exc)
            if "not found" in msg.lower() or "No such image" in msg:
                hint = f"Run 'ninetrix build --file {agentfile_path}' to build the image first."
            elif "already in use" in msg or "already allocated" in msg:
                hint = f"Run 'ninetrix down' to stop existing containers, then retry."
            _abort(f"Failed to start '{name}': {msg}", hint)

        container_ids[name] = container.id
        started.append({"name": f"agentfile-{name}", "container_id": container.id})
        console.print(
            f"  [green]✓[/green] {name} ({image_ref}) → localhost:{host_port}"
        )

    # ── 5. Save state ─────────────────────────────────────────────────────────
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

    # infra in start order so down.py reverses them correctly (api → db)
    infra_containers = [
        {"name": "agentfile-db",  "container_id": db_container.id},
        {"name": "ninetrix-api", "container_id": api_container.id},
    ]

    state = {
        "swarm": swarm,
        "tag": tag,
        "agentfile": str(Path(agentfile_path).resolve()),
        "agents": agents_state,
        "infra_containers": infra_containers,
        "started_at": time.time(),
    }
    _state_file(swarm).write_text(json.dumps(state, indent=2))

    # ── Summary ───────────────────────────────────────────────────────────────
    console.print()
    console.print(f"  [bold green]Stack ready.[/bold green]  Swarm: [bold]{swarm}[/bold]")
    console.print()
    console.print(f"  [dim]Dashboard API:[/dim]  http://localhost:{api_port}")
    console.print(f"  [dim]PostgreSQL:[/dim]      localhost:{db_port}")
    console.print(
        f"  [dim]Entry agent:[/dim]     {af.entry_agent.name} "
        f"→ localhost:{host_ports[af.entry_agent.name]}"
    )
    console.print()
    console.print("  Commands:")
    console.print(f"    ninetrix status")
    console.print(f"    ninetrix invoke --agent {af.entry_agent.name} -m \"your task\"")
    console.print(f"    ninetrix logs --follow")
    console.print(f"    ninetrix down\n")
