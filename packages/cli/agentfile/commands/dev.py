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

_STACK = [
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
    {
        "name": "mcp-gateway",
        "health_url": "http://localhost:8080/health",
        "display_port": "http://localhost:8080",
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
    # 1. Try package_data (installed via pip/pipx/uv)
    try:
        ref = importlib.resources.files("agentfile.compose") / "docker-compose.dev.yml"
        with importlib.resources.as_file(ref) as p:
            if Path(p).exists():
                return Path(p)
    except Exception:
        pass

    # 2. Fallback for editable install — walk up to repo root
    here = Path(__file__).resolve().parent
    for _ in range(6):
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
        "gateway_url: ws://localhost:8080\n"
        "workspace_id: local\n"
        "worker_name: default\n"
        "servers: []\n"
    )
    console.print(f"[dim]Created minimal {dest}[/dim]")


def _check_docker() -> None:
    result = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
    if result.returncode != 0:
        raise click.ClickException(
            "Docker is not running.\n"
            "  macOS/Windows: start Docker Desktop\n"
            "  Linux: sudo systemctl start docker"
        )


def _compose(compose_file: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", "-f", str(compose_file), *args],
        check=check,
    )


def _compose_up(compose_file: Path, pull: bool) -> None:
    if pull:
        console.print("[dim]Pulling latest images…[/dim]")
        result = subprocess.run(
            ["docker", "compose", "-f", str(compose_file), "pull"],
            capture_output=True,
        )
        if result.returncode != 0:
            console.print("[dim]Images not on registry yet — building locally…[/dim]")
            _compose(compose_file, "build")
    console.print("[dim]Starting services…[/dim]\n")
    result = subprocess.run(
        ["docker", "compose", "-f", str(compose_file), "up", "-d", "--remove-orphans"],
        capture_output=True,
        text=True,
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


def _poll_health(timeout: int = 60) -> dict[str, bool]:
    """Poll HTTP /health endpoints. Returns {service: is_healthy}."""
    status = {s["name"]: False for s in _STACK}
    deadline = time.time() + timeout

    while time.time() < deadline:
        for svc in _STACK:
            if status[svc["name"]]:
                continue
            url = svc["health_url"]
            if url is None:
                # postgres: healthy once api is healthy
                # mcp-worker: healthy once mcp-gateway is healthy
                if svc["name"] == "postgres" and status.get("api"):
                    status["postgres"] = True
                elif svc["name"] == "mcp-worker" and status.get("mcp-gateway"):
                    status["mcp-worker"] = True
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


def _status_table(status: dict[str, bool], final: bool = False) -> Table:
    table = Table(box=None, padding=(0, 2), show_header=False)
    table.add_column(style="bold")
    table.add_column()
    table.add_column(style="dim")

    for svc in _STACK:
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
def dev_command(pull: bool, reset: bool, detach: bool) -> None:
    """Start the full local Ninetrix stack.

    Starts PostgreSQL, the API server, MCP gateway, and MCP worker
    via Docker Compose, waits for each service to become healthy,
    then streams logs. Press Ctrl+C for a clean shutdown.

    \b
    Agents should set:
      AGENTFILE_API_URL=http://localhost:8000
      MCP_GATEWAY_URL=http://localhost:8080
    """
    console.rule("[bold]ninetrix dev[/bold]")

    _check_docker()
    compose_file = _get_compose_file()
    _ensure_mcp_worker_config()

    if reset:
        console.print("[yellow]Wiping volumes…[/yellow]")
        _compose(compose_file, "down", "-v", check=False)

    _compose_up(compose_file, pull=pull)

    console.print("[dim]Waiting for services…[/dim]\n")

    # Live-update the status while polling
    from rich.live import Live
    with Live(console=console, refresh_per_second=2) as live:
        for _ in range(60):
            status = _poll_health(timeout=2)
            live.update(_status_table(status, final=False))
            if all(status.values()):
                break

    console.print(_status_table(status, final=True))

    failed = [k for k, v in status.items() if not v]
    if failed:
        console.print(f"\n[red]Services did not become healthy: {', '.join(failed)}[/red]")
        console.print(f"[dim]Logs: docker compose -f {compose_file} logs[/dim]")
        raise click.ClickException("Stack did not start cleanly.")

    if detach:
        return

    # Stream logs until Ctrl+C
    log_proc = subprocess.Popen(
        ["docker", "compose", "-f", str(compose_file), "logs", "-f", "--no-log-prefix"],
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    try:
        log_proc.wait()
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopping services…[/yellow]")
        log_proc.terminate()
        _compose_down(compose_file)
        console.print("[green]All services stopped.[/green]")
