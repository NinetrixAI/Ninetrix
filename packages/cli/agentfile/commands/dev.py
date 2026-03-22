"""ninetrix dev — start and health-check the full local stack via Docker Compose."""
from __future__ import annotations

import importlib.resources
import shutil
import subprocess
import sys
import time
from pathlib import Path

import click
import httpx
from rich.console import Console
from rich.table import Table

console = Console()

_CORE_STACK = [
    {
        "name": "postgres",
        "health_url": None,
        "display_port": "localhost:5432",
    },
    {
        "name": "api",
        "health_url": "http://localhost:8000/health",
        "display_port": "http://localhost:8000",
    },
]

_MCP_STACK = [
    {
        "name": "mcp-gateway",
        "health_url": "http://localhost:9090/health",
        "display_port": "http://localhost:9090",
    },
    {
        "name": "mcp-worker",
        "health_url": None,
        "display_port": "connected to gateway",
    },
]

_DASHBOARD_URL = "http://localhost:8000/dashboard"
_API_DOCS_URL  = "http://localhost:8000/docs"
_COMPOSE_FILE_REL = "compose/docker-compose.dev.yml"  # inside agentfile package_data
_DEFAULT_WORKER_CONFIG = "mcp-worker.default.yaml"    # inside mcp_worker package_data


def _get_compose_file() -> Path:
    """Return path to the bundled docker-compose.dev.yml."""
    import os

    # 0. Explicit override
    if override := os.getenv("NINETRIX_COMPOSE_FILE"):
        p = Path(override)
        if p.exists():
            return p
        raise click.ClickException(
            f"NINETRIX_COMPOSE_FILE={override!r} does not exist."
        )

    # 1. Try package_data (installed via pip/pipx/uv)
    try:
        ref = importlib.resources.files("agentfile.compose") / "docker-compose.dev.yml"
        # Use as_file but copy the path before the context manager closes
        with importlib.resources.as_file(ref) as p:
            resolved = Path(p).resolve()
        if resolved.exists():
            return resolved
    except Exception:
        pass

    # 2. Fallback for editable install — walk up to find infra/compose/
    here = Path(__file__).resolve().parent
    for _ in range(8):
        candidate = here / "infra" / "compose" / "docker-compose.dev.yml"
        if candidate.exists():
            return candidate
        here = here.parent

    raise click.ClickException(
        "Cannot locate docker-compose.dev.yml. Re-install ninetrix or "
        "set NINETRIX_COMPOSE_FILE to the file path."
    )


def _ensure_mcp_worker_config() -> None:
    """Write default mcp-worker.yaml to ~/.agentfile/ on first run."""
    dest = Path.home() / ".agentfile" / "mcp-worker.yaml"
    if dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Try package_data first (installed via pip/pipx/uv)
    try:
        ref = importlib.resources.files("agentfile.compose") / "mcp-worker.default.yaml"
        with importlib.resources.as_file(ref) as src:
            if Path(src).exists():
                shutil.copy(src, dest)
                console.print(f"[dim]Created {dest} — edit to enable MCP servers.[/dim]")
                return
    except Exception:
        pass

    # Fallback for editable install
    candidates = [
        Path(__file__).resolve().parent.parent.parent.parent.parent
        / "packages" / "mcp-worker" / "mcp-worker.default.yaml",
    ]
    for src in candidates:
        if src.exists():
            shutil.copy(src, dest)
            console.print(f"[dim]Created {dest} — edit to enable MCP servers.[/dim]")
            return

    # Write a minimal fallback inline
    dest.write_text(
        "gateway_url: ws://localhost:9090\n"
        "org_id: default\n"
        "worker_name: default\n"
        "servers: []\n"
    )
    console.print(f"[dim]Created minimal {dest}[/dim]")


_SECRET_FILE = Path.home() / ".agentfile" / ".api-secret"


def _ensure_host_secret() -> str:
    """Generate or load the machine secret on the host.

    This is the same file the API would write inside its container, but by
    generating it here first and passing it as AGENTFILE_RUNNER_TOKENS we ensure
    the host CLI and agent containers can authenticate against the Dockerised API
    with zero manual configuration.
    """
    import secrets as _secrets
    _SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    if _SECRET_FILE.exists():
        secret = _SECRET_FILE.read_text().strip()
        if secret:
            return secret
    secret = _secrets.token_urlsafe(32)
    _SECRET_FILE.write_text(secret)
    _SECRET_FILE.chmod(0o600)
    return secret


def _check_docker() -> None:
    result = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
    if result.returncode != 0:
        raise click.ClickException(
            "Docker is not running.\n"
            "  macOS/Windows: start Docker Desktop\n"
            "  Linux: sudo systemctl start docker"
        )


def _compose_env(secret: str) -> dict:
    """Build env for docker compose — injects host secret + MCP credentials."""
    from agentfile.commands.gateway import _build_proc_env
    env = _build_proc_env()
    env["AGENTFILE_RUNNER_TOKENS"] = secret
    return env


def _profile_args(profiles: list[str] | None) -> list[str]:
    """Build --profile flags for docker compose CLI."""
    if not profiles:
        return []
    args = []
    for p in profiles:
        args.extend(["--profile", p])
    return args


def _compose(
    compose_file: Path, *args: str,
    secret: str = "", profiles: list[str] | None = None, check: bool = True,
) -> subprocess.CompletedProcess:
    cmd = ["docker", "compose", "-f", str(compose_file), *_profile_args(profiles), *args]
    return subprocess.run(
        cmd,
        check=check,
        env=_compose_env(secret) if secret else None,
    )


def _compose_up(compose_file: Path, pull: bool, secret: str, profiles: list[str] | None = None) -> None:
    from rich.live import Live
    from rich.spinner import Spinner

    env = _compose_env(secret)
    pargs = _profile_args(profiles)

    if pull:
        with Live(
            Spinner("dots", text="  Pulling latest images…"),
            console=console,
            refresh_per_second=12,
            transient=True,
        ):
            pull_result = subprocess.run(
                ["docker", "compose", "-f", str(compose_file), *pargs, "pull"],
                capture_output=True,
                env=env,
            )
        if pull_result.returncode != 0:
            console.print("  [yellow]Registry unavailable — building locally…[/yellow]")
            with Live(
                Spinner("dots", text="  Building images…"),
                console=console,
                refresh_per_second=12,
                transient=True,
            ):
                subprocess.run(
                    ["docker", "compose", "-f", str(compose_file), *pargs, "build"],
                    capture_output=True,
                    env=env,
                )
        else:
            console.print("  [green]✓[/green] Images up to date")

    with Live(
        Spinner("dots", text="  Starting services…"),
        console=console,
        refresh_per_second=12,
        transient=True,
    ):
        result = subprocess.run(
            ["docker", "compose", "-f", str(compose_file), *pargs, "up", "-d", "--remove-orphans"],
            capture_output=True,
            text=True,
            env=env,
        )

    if result.returncode != 0:
        if "not found" in result.stderr or "denied" in result.stderr:
            raise click.ClickException(
                "Could not pull Ninetrix images from GHCR.\n\n"
                "  Images are published automatically on every push to main.\n"
                "  If this is a fresh install, wait a few minutes for CI to finish,\n"
                "  then run:  ninetrix dev --pull\n\n"
                "  To check image status:\n"
                "  https://github.com/Ninetrix-ai/Ninetrix/pkgs/container/ninetrix-api"
            )
        raise click.ClickException(
            f"Failed to start services:\n{result.stderr.strip()}"
        )


def _compose_down(compose_file: Path) -> None:
    _compose(compose_file, "down", check=False)


def _container_state(compose_file: Path, service: str) -> str:
    """Return docker compose container state: running, exited, starting, missing."""
    result = subprocess.run(
        ["docker", "compose", "-f", str(compose_file), "ps", "--format", "json", service],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return "missing"
    import json as _json
    try:
        rows = [_json.loads(line) for line in result.stdout.strip().splitlines() if line.strip()]
        if not rows:
            return "missing"
        state = rows[0].get("State", rows[0].get("Status", "")).lower()
        if "running" in state:
            return "running"
        if "exit" in state or "dead" in state:
            return "exited"
        return "starting"
    except Exception:
        return "unknown"


def _poll_health(compose_file: Path, stack: list[dict], timeout: int = 60) -> dict[str, bool]:
    """Poll HTTP health endpoints and container states."""
    status = {s["name"]: False for s in stack}
    deadline = time.time() + timeout

    while time.time() < deadline:
        for svc in stack:
            if status[svc["name"]]:
                continue
            url = svc["health_url"]
            if url is None:
                # Check container is running via docker compose ps
                state = _container_state(compose_file, svc["name"])
                if state == "running":
                    status[svc["name"]] = True
                continue
            try:
                r = httpx.get(url, timeout=2)
                if r.status_code < 500:
                    status[svc["name"]] = True
            except Exception:
                pass

        if all(status.values()):
            break
        time.sleep(1)

    return status


def _failed_logs(compose_file: Path, status: dict[str, bool]) -> str:
    """Return last 10 log lines for any failed service."""
    lines = []
    for name, ok in status.items():
        if ok:
            continue
        result = subprocess.run(
            ["docker", "compose", "-f", str(compose_file), "logs", "--tail=10", name],
            capture_output=True, text=True,
        )
        if result.stdout.strip():
            lines.append(f"\n[{name}]\n{result.stdout.strip()}")
    return "\n".join(lines)


def _status_table(status: dict[str, bool], stack: list[dict], final: bool = False) -> Table:
    table = Table(box=None, padding=(0, 2), show_header=False)
    table.add_column(style="bold")
    table.add_column()
    table.add_column(style="dim")

    for svc in stack:
        name = svc["name"]
        ok = status.get(name, False)
        if ok:
            icon = "[green]✓[/green]"
        elif final:
            icon = "[red]✗[/red]"
        else:
            icon = "[yellow]…[/yellow]"
        table.add_row(icon, f"[bold]{name}[/bold]", svc["display_port"])

    if final and all(status.values()):
        table.add_row("", "", "")
        table.add_row("", f"[bold cyan]Dashboard[/bold cyan]  →  {_DASHBOARD_URL}", "")
        table.add_row("", f"[dim]API docs   →  {_API_DOCS_URL}[/dim]", "")
        table.add_row("", "", "")
        table.add_row("", "[dim]Ctrl+C to stop all services[/dim]", "")

    return table


@click.command("dev")
@click.option("--pull", is_flag=True, default=False, help="Pull latest images before starting.")
@click.option("--reset", is_flag=True, default=False, help="Wipe all volumes and restart from scratch.")
@click.option("--detach", "-d", is_flag=True, default=False, help="Start stack and exit without streaming logs.")
@click.option("--mcp/--no-mcp", default=None, help="Include MCP gateway + worker (prompts if omitted).")
def dev_command(pull: bool, reset: bool, detach: bool, mcp: bool | None) -> None:
    """Start the local Ninetrix stack.

    Starts PostgreSQL and the API server. Optionally includes the MCP
    gateway and worker (for agents that use mcp:// tools).

    \b
    Agents should set:
      AGENTFILE_API_URL=http://localhost:8000
      MCP_GATEWAY_URL=http://localhost:9090   (only if --mcp)
    """
    console.print()
    console.rule("[bold cyan]  Ninetrix Dev  [/bold cyan]")
    console.print()

    _check_docker()
    compose_file = _get_compose_file()
    secret = _ensure_host_secret()

    # Prompt for MCP if not specified
    if mcp is None:
        from rich.prompt import Confirm
        mcp = Confirm.ask("  Include MCP tools stack (gateway + worker)?", default=False)

    profiles: list[str] = []
    stack = list(_CORE_STACK)
    if mcp:
        profiles.append("mcp")
        stack.extend(_MCP_STACK)
        _ensure_mcp_worker_config()
        console.print("  [dim]MCP stack enabled[/dim]\n")
    else:
        # Stop any leftover MCP containers from a previous --mcp run
        subprocess.run(
            ["docker", "compose", "-f", str(compose_file), "--profile", "mcp",
             "rm", "-f", "-s", "mcp-gateway", "mcp-worker"],
            capture_output=True, env=_compose_env(secret),
        )
        console.print("  [dim]Core only (no MCP). Use --mcp to include tools.[/dim]\n")

    if reset:
        console.print("[yellow]Wiping volumes…[/yellow]")
        _compose(compose_file, "down", "-v", secret=secret, profiles=["mcp"], check=False)

    _compose_up(compose_file, pull=pull, secret=secret, profiles=profiles)

    # Live-update the status while polling — spinner + table, no duplicate print
    from rich.live import Live
    from rich.console import Group
    from rich.spinner import Spinner

    spin = Spinner("dots", text="  Waiting for services…")
    init_status = {s["name"]: False for s in stack}
    with Live(Group(spin, _status_table(init_status, stack)), console=console, refresh_per_second=8) as live:
        for _ in range(60):
            status = _poll_health(compose_file, stack, timeout=2)
            if all(status.values()):
                live.update(_status_table(status, stack, final=True))
                break
            live.update(Group(spin, _status_table(status, stack, final=False)))
        else:
            live.update(_status_table(status, stack, final=True))

    failed = [k for k, v in status.items() if not v]
    if failed:
        console.print(f"\n[red]Services did not become healthy: {', '.join(failed)}[/red]")
        logs = _failed_logs(compose_file, status)
        if logs:
            console.print(f"[dim]{logs}[/dim]")
        raise click.ClickException("Stack did not start cleanly.")

    if detach:
        return

    # Stream logs until Ctrl+C
    log_proc = subprocess.Popen(
        ["docker", "compose", "-f", str(compose_file), *_profile_args(profiles), "logs", "-f", "--no-log-prefix"],
        stdout=sys.stdout,
        stderr=sys.stderr,
        env=_compose_env(secret),
    )
    try:
        log_proc.wait()
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopping services…[/yellow]")
        log_proc.terminate()
        _compose(compose_file, "down", secret=secret, profiles=profiles, check=False)
        console.print("[green]All services stopped.[/green]")
