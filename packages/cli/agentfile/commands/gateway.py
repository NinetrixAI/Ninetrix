"""agentfile gateway — start/stop/restart/status/doctor the local MCP Gateway stack."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

console = Console()


# ── Known credential env vars per MCP server name ─────────────────────────────
# Used by auto-forwarding (start/restart) and doctor (missing var detection).

_SERVER_CRED_VARS: dict[str, list[str]] = {
    "github":        ["GITHUB_TOKEN", "GITHUB_PERSONAL_ACCESS_TOKEN"],
    "slack":         ["SLACK_BOT_TOKEN", "SLACK_TEAM_ID"],
    "notion":        ["NOTION_API_KEY"],
    "linear":        ["LINEAR_API_KEY"],
    "google-drive":  ["GOOGLE_DRIVE_ACCESS_TOKEN"],
    "google-sheets": ["GOOGLE_SHEETS_ACCESS_TOKEN"],
    "google-docs":   ["GOOGLE_DOCS_ACCESS_TOKEN"],
    "brave-search":  ["BRAVE_API_KEY"],
    "stripe":        ["STRIPE_SECRET_KEY"],
    "postgres":      ["POSTGRES_CONNECTION_STRING", "DATABASE_URL"],
    "filesystem":    [],   # no credentials needed
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _find_compose_file() -> Path | None:
    candidates = [
        Path(__file__).parent.parent.parent / "docker-compose.gateway.yml",
        Path(__file__).parent.parent.parent.parent / "docker-compose.gateway.yml",
        Path.cwd() / "docker-compose.gateway.yml",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def _gateway_url() -> str:
    return os.environ.get("MCP_GATEWAY_URL", "http://localhost:8000")


def _find_worker_config(compose: Path) -> Path | None:
    """Find mcp-worker.yaml or .example relative to the compose file."""
    worker_dir = compose.parent.parent / "mcp-worker"
    for name in ("mcp-worker.yaml", "mcp-worker.yaml.example"):
        p = worker_dir / name
        if p.exists():
            return p
    return None


def _parse_server_names(worker_config: Path) -> list[str]:
    """Extract server names from mcp-worker.yaml."""
    try:
        import yaml
        data = yaml.safe_load(worker_config.read_text()) or {}
        return [s["name"] for s in data.get("servers", []) if "name" in s]
    except Exception:
        return []


def _load_dotenv() -> dict[str, str]:
    """Read KEY=VALUE pairs from .env in the current directory."""
    env_file = Path(".env")
    result: dict[str, str] = {}
    if not env_file.exists():
        return result
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            result[k.strip()] = v.strip().strip('"').strip("'")
    return result


def _collect_creds(server_names: list[str], dotenv: dict[str, str]) -> dict[str, str]:
    """Collect credential env vars for the given servers from host env + .env."""
    result: dict[str, str] = {}
    for name in server_names:
        for var in _SERVER_CRED_VARS.get(name, []):
            val = os.environ.get(var) or dotenv.get(var)
            if val:
                result[var] = val
    return result


def _build_proc_env(compose: Path | None) -> dict[str, str]:
    """Build subprocess env with auto-collected credentials injected."""
    dotenv = _load_dotenv()
    if compose:
        worker_cfg = _find_worker_config(compose)
        if worker_cfg:
            server_names = _parse_server_names(worker_cfg)
            creds = _collect_creds(server_names, dotenv)
            if creds:
                return {**os.environ, **creds}
    return dict(os.environ)


# ── CLI group ──────────────────────────────────────────────────────────────────

@click.group("gateway")
def gateway_cmd() -> None:
    """Manage the local MCP Gateway (start/stop/restart/status/doctor).

    \b
    The MCP Gateway lets agents use tools from remote workers instead of
    spawning local MCP server processes.  Workers can run anywhere — on
    this machine, in your cloud, or inside a customer's private network.

    \b
    Quick start:
      ninetrix gateway start     start gateway + worker (auto-forwards credentials)
      ninetrix gateway doctor    check health and diagnose missing credentials
      ninetrix gateway restart   restart worker with fresh credentials (no rebuild)
      ninetrix gateway status    show connected workers and tools
      ninetrix gateway stop      tear down the gateway stack
    """


# ── start ──────────────────────────────────────────────────────────────────────

@gateway_cmd.command("start")
@click.option("--detach/--no-detach", default=True, show_default=True,
              help="Run in the background (detached mode)")
@click.option("--build/--no-build", "rebuild", default=False,
              help="Rebuild Docker images before starting")
def gateway_start(detach: bool, rebuild: bool) -> None:
    """Start the MCP Gateway and default worker.

    Automatically forwards credentials from your environment / .env
    to the worker container — no manual docker-compose editing needed.
    """
    compose = _find_compose_file()
    if compose is None:
        console.print("[red]docker-compose.gateway.yml not found.[/red]")
        console.print("  Expected at the repo root or current directory.")
        raise SystemExit(1)

    console.print()
    console.print("[bold purple]ninetrix gateway start[/bold purple]\n")
    console.print(f"  [dim]Compose file:[/dim] {compose}\n")

    # Auto-collect and report credentials
    dotenv = _load_dotenv()
    worker_cfg = _find_worker_config(compose)
    creds: dict[str, str] = {}
    if worker_cfg:
        server_names = _parse_server_names(worker_cfg)
        creds = _collect_creds(server_names, dotenv)
        if creds:
            console.print(f"  [dim]Auto-forwarding {len(creds)} credential(s) to worker:[/dim]")
            for k in sorted(creds):
                console.print(f"    {k}=[dim]****[/dim]")
            console.print()
        else:
            console.print("  [dim]No credentials found in env/.env — worker starts without them.[/dim]")
            console.print("  [dim]Run [bold]ninetrix gateway doctor[/bold] after start to check status.[/dim]\n")

    proc_env = {**os.environ, **creds}

    cmd = ["docker", "compose", "-f", str(compose), "up"]
    if detach:
        cmd.append("-d")
    if rebuild:
        cmd.append("--build")

    result = subprocess.run(cmd, env=proc_env)
    if result.returncode != 0:
        raise SystemExit(result.returncode)

    if detach:
        console.print()
        console.print("  [green]✓[/green] Gateway started.")
        console.print(f"  Admin:  [bold]{_gateway_url()}/admin/workers[/bold]")
        console.print(f"  Health: [bold]{_gateway_url()}/health[/bold]")
        console.print()
        console.print("  [dim]Run [bold]ninetrix gateway doctor[/bold] to verify tools are loaded.[/dim]")
        console.print()


# ── stop ───────────────────────────────────────────────────────────────────────

@gateway_cmd.command("stop")
def gateway_stop() -> None:
    """Stop the MCP Gateway and all workers."""
    compose = _find_compose_file()
    if compose is None:
        console.print("[red]docker-compose.gateway.yml not found.[/red]")
        raise SystemExit(1)

    console.print()
    console.print("[bold purple]ninetrix gateway stop[/bold purple]\n")

    result = subprocess.run(["docker", "compose", "-f", str(compose), "down"])
    if result.returncode != 0:
        raise SystemExit(result.returncode)

    console.print("  [green]✓[/green] Gateway stopped.\n")


# ── restart ────────────────────────────────────────────────────────────────────

@gateway_cmd.command("restart")
@click.option("--all", "restart_all", is_flag=True,
              help="Restart gateway and worker (default: worker only)")
def gateway_restart(restart_all: bool) -> None:
    """Restart the worker with fresh credentials — gateway stays up.

    Use this after setting or changing env vars / .env entries.
    Credentials are re-read from your environment automatically.
    Use --all to restart the full stack including the gateway.
    """
    compose = _find_compose_file()
    if compose is None:
        console.print("[red]docker-compose.gateway.yml not found.[/red]")
        raise SystemExit(1)

    console.print()
    console.print("[bold purple]ninetrix gateway restart[/bold purple]\n")

    proc_env = _build_proc_env(compose)

    if restart_all:
        console.print("  Restarting full stack…\n")
        services: list[str] = []
    else:
        console.print("  Restarting worker (gateway stays up)…\n")
        services = ["mcp-worker"]

    # force-recreate picks up new env vars; plain `restart` does not
    cmd = ["docker", "compose", "-f", str(compose), "up", "-d", "--force-recreate"] + services
    result = subprocess.run(cmd, env=proc_env)
    if result.returncode != 0:
        raise SystemExit(result.returncode)

    console.print()
    console.print("  [green]✓[/green] Restarted.")
    console.print("  [dim]Worker reconnects in a few seconds. Run [bold]ninetrix gateway doctor[/bold] to verify.[/dim]\n")


# ── status ─────────────────────────────────────────────────────────────────────

@gateway_cmd.command("status")
def gateway_status() -> None:
    """Show connected workers and available tools."""
    import httpx as _httpx

    url = _gateway_url()
    console.print()
    console.print("[bold purple]ninetrix gateway status[/bold purple]\n")

    try:
        resp = _httpx.get(f"{url}/health", timeout=3)
        resp.raise_for_status()
        health = resp.json()
    except Exception as exc:
        console.print(f"  [red]✗[/red] Gateway not reachable at [bold]{url}[/bold]: {exc}\n")
        console.print("  [dim]Run [bold]ninetrix gateway start[/bold] to start the gateway.[/dim]\n")
        raise SystemExit(1)

    console.print(
        f"  [green]✓[/green] Gateway online — "
        f"{health.get('connected_workers', 0)} worker(s) connected\n"
    )

    try:
        workers_resp = _httpx.get(f"{url}/admin/workers", timeout=3)
        workers = workers_resp.json().get("workers", [])
    except Exception:
        workers = []

    if workers:
        t = Table(show_header=True, header_style="bold")
        t.add_column("Worker", style="bold cyan")
        t.add_column("Workspace")
        t.add_column("Servers")
        t.add_column("Tools", justify="right")
        t.add_column("Connected")
        for w in workers:
            t.add_row(
                w.get("worker_name", w.get("worker_id", "?")),
                w.get("workspace_id", ""),
                ", ".join(w.get("servers", [])) or "—",
                str(w.get("tool_count", 0)),
                w.get("connected_at", "")[:19],
            )
        console.print(t)
        console.print()
    else:
        console.print("  [yellow]No workers connected.[/yellow]")
        console.print("  [dim]Workers connect automatically when you run docker-compose.gateway.yml[/dim]\n")

    try:
        tools_resp = _httpx.get(f"{url}/admin/tools", timeout=3)
        tools = tools_resp.json().get("tools", [])
        if tools:
            names = [t["name"] for t in tools]
            console.print(f"  [dim]Available tools ({len(names)}):[/dim] {', '.join(names)}\n")
    except Exception:
        pass


# ── doctor ─────────────────────────────────────────────────────────────────────

@gateway_cmd.command("doctor")
def gateway_doctor() -> None:
    """Diagnose gateway health, worker connections, and missing credentials.

    Checks every configured MCP server against what's actually running,
    detects missing env vars, and prints actionable fix commands.
    """
    import httpx as _httpx

    url = _gateway_url()
    console.print()
    console.print("[bold purple]ninetrix gateway doctor[/bold purple]\n")

    # ── 1. Gateway reachability ────────────────────────────────────────────────
    try:
        resp = _httpx.get(f"{url}/health", timeout=3)
        resp.raise_for_status()
        health = resp.json()
        console.print(
            f"  [green]✓[/green] Gateway  [bold]{url}[/bold]  "
            f"({health.get('connected_workers', 0)} worker(s) connected)"
        )
    except Exception as exc:
        console.print(f"  [red]✗[/red] Gateway not reachable at [bold]{url}[/bold]")
        console.print(f"    {exc}")
        console.print("\n  Fix: [bold]ninetrix gateway start[/bold]\n")
        raise SystemExit(1)

    # ── 2. Worker connections ──────────────────────────────────────────────────
    workers: list[dict] = []
    try:
        workers = _httpx.get(f"{url}/admin/workers", timeout=3).json().get("workers", [])
    except Exception:
        pass

    if not workers:
        console.print("  [red]✗[/red] Worker    not connected\n")
        console.print("  Fix: [bold]ninetrix gateway restart[/bold]\n")
        raise SystemExit(1)

    for w in workers:
        console.print(
            f"  [green]✓[/green] Worker    [bold]{w.get('worker_name', w.get('worker_id', '?'))}[/bold]"
            f"  (workspace: {w.get('workspace_id', '?')})"
        )

    # ── 3. Tool counts per server prefix ──────────────────────────────────────
    tools_by_server: dict[str, int] = {}
    try:
        for t in _httpx.get(f"{url}/admin/tools", timeout=3).json().get("tools", []):
            prefix = t["name"].split("__")[0] if "__" in t["name"] else t["name"]
            tools_by_server[prefix] = tools_by_server.get(prefix, 0) + 1
    except Exception:
        pass

    total_tools = sum(tools_by_server.values())
    console.print(f"\n  [bold]Servers[/bold]  ({total_tools} total tool(s))\n")

    # ── 4. Cross-reference with mcp-worker.yaml ────────────────────────────────
    compose = _find_compose_file()
    configured_servers: list[str] = []
    if compose:
        worker_cfg = _find_worker_config(compose)
        if worker_cfg:
            configured_servers = _parse_server_names(worker_cfg)
            console.print(f"  [dim]Config: {worker_cfg}[/dim]\n")

    dotenv = _load_dotenv()
    fixes: list[str] = []
    all_ok = True

    for server_name in configured_servers:
        tool_count = tools_by_server.get(server_name, 0)
        required = _SERVER_CRED_VARS.get(server_name, [])

        if tool_count > 0:
            console.print(f"    [green]✓[/green]  {server_name:<22} {tool_count} tool(s)")
        else:
            # Identify missing env vars
            missing = [
                v for v in required
                if not os.environ.get(v) and not dotenv.get(v)
            ]
            if missing:
                all_ok = False
                console.print(
                    f"    [red]✗[/red]  {server_name:<22} "
                    f"[red]FAILED[/red] — missing: [bold]{', '.join(missing)}[/bold]"
                )
                for var in missing:
                    fixes.append(f"export {var}=your-value-here")
            elif not required:
                # Server with no required credentials — likely startup failure
                all_ok = False
                console.print(
                    f"    [yellow]?[/yellow]  {server_name:<22} "
                    f"[yellow]0 tools[/yellow] — check worker logs"
                )
                fixes.append("docker compose -f docker-compose.gateway.yml logs mcp-worker")
            else:
                all_ok = False
                console.print(
                    f"    [yellow]?[/yellow]  {server_name:<22} "
                    f"[yellow]0 tools[/yellow] — credentials set but server may have failed"
                )
                fixes.append("docker compose -f docker-compose.gateway.yml logs mcp-worker")

    # Show tools from servers not declared in yaml (e.g. dynamically added)
    for server_name, count in tools_by_server.items():
        if server_name not in configured_servers:
            console.print(f"    [dim]~[/dim]  {server_name:<22} {count} tool(s) [dim](not in yaml)[/dim]")

    if not configured_servers and not tools_by_server:
        console.print("    [dim]No servers configured.[/dim]")

    console.print()

    # ── 5. Print fix commands ──────────────────────────────────────────────────
    if fixes:
        seen: list[str] = []
        for f in fixes:
            if f not in seen:
                seen.append(f)
        console.print("  [bold]Fix:[/bold]")
        for f in seen:
            console.print(f"    [dim]{f}[/dim]")
        console.print()
        console.print("  Then run: [bold]ninetrix gateway restart[/bold]\n")
    elif all_ok and total_tools > 0:
        console.print("  [green]Everything looks good![/green]\n")
    elif total_tools == 0:
        console.print("  [yellow]No tools available — worker may still be starting.[/yellow]")
        console.print("  [dim]Wait a few seconds and run [bold]ninetrix gateway doctor[/bold] again.[/dim]\n")
