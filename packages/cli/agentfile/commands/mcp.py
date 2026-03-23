"""ninetrix mcp — manage MCP tool servers on the gateway worker.

After `ninetrix dev` is running, use these commands to add, remove, list,
test, and inspect MCP tools — no YAML editing required.

Quick start:
  ninetrix mcp status               what's running in the gateway right now
  ninetrix mcp list                 cross-ref gateway tools vs agentfile.yaml
  ninetrix mcp add github           add GitHub to the worker (restarts worker)
  ninetrix mcp remove github        remove GitHub from the worker
  ninetrix mcp test github          live-test GitHub tools via the gateway
  ninetrix mcp catalog              show all available servers in the built-in catalog
"""

from __future__ import annotations

import os
import time

import click
from rich.console import Console
from rich.table import Table

from agentfile.core import mcp_catalog, worker_config
from agentfile.core.auth import auth_headers, read_token
from agentfile.core.config import resolve_saas_url

console = Console()

# ── Gateway helpers ─────────────────────────────────────────────────────────────

def _gw_url() -> str:
    """HTTP base URL of the gateway (converts ws:// → http://)."""
    raw = os.environ.get("MCP_GATEWAY_URL", "http://localhost:9090")
    return raw.replace("ws://", "http://").replace("wss://", "https://")


def _gw_org_id() -> str:
    return os.environ.get("MCP_GATEWAY_ORG_ID", "local")


def _gw_token() -> str | None:
    return os.environ.get("MCP_GATEWAY_TOKEN")


def _gw_headers() -> dict:
    tok = _gw_token()
    return {"Authorization": f"Bearer {tok}"} if tok else {}


def _gateway_tools() -> list[dict]:
    """Fetch tools from the gateway.  Returns [] if gateway is unreachable."""
    try:
        import httpx
        resp = httpx.get(f"{_gw_url()}/admin/tools", headers=_gw_headers(), timeout=3)
        return resp.json().get("tools", []) if resp.is_success else []
    except Exception:
        return []


def _gateway_online() -> bool:
    try:
        import httpx
        r = httpx.get(f"{_gw_url()}/health", headers=_gw_headers(), timeout=3)
        return r.is_success
    except Exception:
        return False


def _fmt_relative(iso: str) -> str:
    """Format an ISO timestamp as a human-readable relative string."""
    from datetime import datetime, timezone
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        s = int(delta.total_seconds())
        if s < 60:
            return "just now"
        if s < 3600:
            return f"{s // 60}m ago"
        if s < 86400:
            return f"{s // 3600}h ago"
        return f"{s // 86400}d ago"
    except Exception:
        return iso


def _tools_by_server(tools: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for t in tools:
        prefix = t["name"].split("__")[0] if "__" in t["name"] else t["name"]
        counts[prefix] = counts.get(prefix, 0) + 1
    return counts


# ── Restart helper ──────────────────────────────────────────────────────────────

def _restart_worker_with_feedback(verbose: bool = True) -> bool:
    """Restart the mcp-worker and poll until tools re-register (or timeout)."""
    from agentfile.commands.gateway import restart_worker

    if verbose:
        with console.status("  Restarting mcp-worker…", spinner="dots"):
            ok = restart_worker()
    else:
        ok = restart_worker()

    if not ok:
        console.print(
            "  [yellow]⚠[/yellow]  Could not restart worker automatically.\n"
            "  Run manually: [bold]docker compose restart mcp-worker[/bold]"
        )
        return False
    return True


def _wait_for_server(server_name: str, timeout: int = 30) -> int:
    """Poll gateway until server_name has tools.  Returns tool count (0 = timeout)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        counts = _tools_by_server(_gateway_tools())
        if counts.get(server_name, 0) > 0:
            return counts[server_name]
        time.sleep(2)
    return 0


# ── mcp group ─────────────────────────────────────────────────────────────────

@click.group("mcp")
def mcp_cmd() -> None:
    """Manage MCP tool servers on the local gateway worker."""


# ── SaaS integration helpers ─────────────────────────────────────────────────

def _saas_connected_integrations() -> list[dict]:
    """Return integrations connected via the Ninetrix vault.

    Each entry is a dict with keys: id, name, mcp_source, account_label.
    Returns [] if not in SaaS mode or API is unreachable.
    """
    api_url = resolve_saas_url()
    if not api_url:
        return []
    try:
        import httpx
        from agentfile.core.auth import auth_headers
        resp = httpx.get(
            f"{api_url}/v1/integrations",
            headers=auth_headers(api_url),
            timeout=5,
        )
        if not resp.is_success:
            return []
        return [r for r in resp.json() if r.get("connected")]
    except Exception:
        return []


# ── mcp status ────────────────────────────────────────────────────────────────

@mcp_cmd.command("status")
def mcp_status() -> None:
    """Show per-server health, tool counts, and credential status."""
    console.print()
    console.print("[bold purple]ninetrix mcp status[/bold purple]\n")

    # ── Gather data ───────────────────────────────────────────────────────────
    gw_online = _gateway_online()
    tools = _gateway_tools() if gw_online else []
    by_server = _tools_by_server(tools)
    configured = worker_config.list_servers()
    cloud = _saas_connected_integrations() if _is_saas_mode() else []

    if gw_online:
        console.print(f"  [green]✓[/green] Gateway  [bold]{_gw_url()}[/bold]  "
                      f"({len(tools)} tool(s) available)\n")
    else:
        console.print(
            f"  [yellow]⚠[/yellow]  Gateway not reachable at [bold]{_gw_url()}[/bold]  "
            "[dim](run [bold]ninetrix dev[/bold] to start)[/dim]\n"
        )

    # ── Build unified rows ────────────────────────────────────────────────────
    # Each row: (name, source, status, tool_count_str, last_used_str)
    rows: list[tuple[str, str, str, str, str]] = []
    any_issue = False

    # Local: union of worker config + live gateway tools
    local_names = sorted(set(configured) | set(by_server.keys()))
    for name in local_names:
        tool_count = by_server.get(name, 0)
        in_config = name in configured
        entry = mcp_catalog.get(name)

        if tool_count > 0:
            status = "[green]running[/green]"
        elif in_config:
            status = "[red]not loaded[/red]"
            any_issue = True
            if entry and entry.required_env:
                for var in entry.required_env:
                    if not entry.resolve_env_value(var):
                        any_issue = True
        else:
            status = "[dim]dynamic[/dim]"

        rows.append((name, "[dim]local[/dim]", status, str(tool_count) if tool_count else "—", "[dim]—[/dim]"))

    # Cloud: connected integrations from SaaS vault
    cloud_local_names = {r[0] for r in rows}
    for item in sorted(cloud, key=lambda x: x.get("mcp_source") or x.get("id", "")):
        name = item.get("mcp_source") or item.get("id", "?")
        display = item.get("name", name)
        if name in cloud_local_names:
            continue  # already shown as local
        connected_at = item.get("connected_at")
        last_used = _fmt_relative(connected_at) if connected_at else "[dim]—[/dim]"
        rows.append((display, "[blue]cloud[/blue]", "[green]connected[/green]", "—", last_used))

    # ── Render single table ───────────────────────────────────────────────────
    if rows:
        t = Table(show_header=True, header_style="bold")
        t.add_column("Server", style="bold cyan")
        t.add_column("Source")
        t.add_column("Status")
        t.add_column("Tools", justify="right")
        t.add_column("Last used")
        for row in rows:
            t.add_row(*row)
        console.print(t)
    else:
        console.print("  [dim]No servers configured. Run [bold]ninetrix mcp add <server>[/bold] or [bold]ninetrix mcp connect <server>[/bold][/dim]")

    console.print()

    if any_issue:
        console.print("  [yellow]Some local servers have issues.[/yellow]")
        console.print("  Run [bold]ninetrix mcp add <server>[/bold] to fix missing servers.")
        console.print("  Run [bold]ninetrix gateway doctor[/bold] for a full diagnostic.\n")
    elif rows:
        console.print(f"  [dim]Worker config:[/dim] {worker_config.find_config_path()}\n")


# ── mcp list ──────────────────────────────────────────────────────────────────

@mcp_cmd.command("list")
@click.option("--file", "-f", "agentfile_path", default="agentfile.yaml",
              show_default=True, help="Path to agentfile.yaml")
def mcp_list(agentfile_path: str) -> None:
    """Cross-reference gateway tools with what agents declare in agentfile.yaml."""
    console.print()
    console.print("[bold purple]ninetrix mcp list[/bold purple]\n")

    # ── Gateway side ──────────────────────────────────────────────────────────
    gw_online = _gateway_online()
    tools = _gateway_tools() if gw_online else []
    by_server = _tools_by_server(tools)

    if gw_online:
        console.print(
            f"  [green]✓[/green] Gateway [bold]{_gw_url()}[/bold]  "
            f"org=[bold]{_gw_org_id()}[/bold]  "
            f"{len(tools)} tool(s)\n"
        )
    else:
        console.print(
            "  [yellow]⚠[/yellow]  Gateway not reachable — "
            "run [bold]ninetrix dev[/bold] first\n"
        )

    # ── Agentfile side ────────────────────────────────────────────────────────
    declared: dict[str, list[str]] = {}   # mcp_name → [agent_names]
    try:
        from agentfile.core.models import AgentFile
        af = AgentFile.from_path(agentfile_path)
        for agent_name, agent_def in af.agents.items():
            for tool in agent_def.tools:
                if tool.is_mcp():
                    key = tool.mcp_name or tool.name
                    declared.setdefault(key, []).append(agent_name)
    except FileNotFoundError:
        pass  # no agentfile in cwd — that's ok, just show gateway state

    # ── Combined table ────────────────────────────────────────────────────────
    all_names = sorted(set(by_server.keys()) | set(declared.keys()))

    if not all_names:
        console.print("  No MCP tools found.\n")
        console.print("  Add a server:  [bold]ninetrix mcp add github[/bold]")
        console.print("  See catalog:   [bold]ninetrix mcp catalog[/bold]\n")
        return

    t = Table(show_header=True, header_style="bold purple")
    t.add_column("Server", style="bold cyan")
    t.add_column("Tools", justify="right")
    t.add_column("Gateway")
    t.add_column("Used by agents")

    gaps: list[str] = []
    for name in all_names:
        count = by_server.get(name, 0)
        in_gw = "[green]running[/green]" if count > 0 else "[red]not running[/red]"
        agents = ", ".join(declared.get(name, [])) or "[dim]not declared[/dim]"
        t.add_row(name, str(count) if count else "—", in_gw, agents)
        if name in declared and count == 0:
            gaps.append(name)

    console.print(t)
    console.print()

    if gaps:
        console.print(
            f"  [yellow]⚠[/yellow]  {len(gaps)} server(s) declared in agentfile.yaml "
            "but not running in gateway:"
        )
        for g in gaps:
            console.print(f"    [bold]ninetrix mcp add {g}[/bold]")
        console.print()


# ── mcp add ───────────────────────────────────────────────────────────────────

@mcp_cmd.command("add")
@click.argument("name")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompts")
@click.option("--no-restart", is_flag=True, help="Write config but do not restart worker")
@click.option("--custom", "is_custom", is_flag=True, help="Add a custom server not in the catalog")
@click.option("--local", "local_only", is_flag=True,
              help="Configure via local mcp-worker.yaml instead of the Ninetrix vault")
@click.option("--no-browser", is_flag=True, help="(SaaS) Print connect URL without opening browser")
@click.option("--type", "server_type",
              type=click.Choice(["npx", "uvx", "docker", "python"], case_sensitive=False),
              default=None, help="(--custom) Server launch type")
@click.option("--package", "-p", default=None, help="(--custom) Package name or script path")
@click.option("--args", "-a", "extra_args", multiple=True, metavar="ARG",
              help="(--custom) Extra CLI args (repeatable)")
@click.option("--env", "-e", "env_pairs", multiple=True, metavar="VAR=SOURCE",
              help="(--custom) Env var mappings e.g. API_KEY=${MY_KEY} (repeatable)")
@click.pass_context
def mcp_add(
    ctx: click.Context,
    name: str,
    yes: bool,
    no_restart: bool,
    is_custom: bool,
    local_only: bool,
    no_browser: bool,
    server_type: str | None,
    package: str | None,
    extra_args: tuple[str, ...],
    env_pairs: tuple[str, ...],
) -> None:
    """Add an MCP server to the worker and restart it.

    \b
    Examples:
      ninetrix mcp add github
      ninetrix mcp add slack
      ninetrix mcp add --custom --type npx --package @acme/my-mcp-server my-server
    """
    console.print()
    console.print("[bold purple]ninetrix mcp add[/bold purple]\n")

    # In SaaS mode, credentials live in the vault — redirect to `mcp connect`
    # unless the user explicitly wants local worker config (--local).
    if not local_only and not is_custom and _is_saas_mode():
        console.print("  [dim]SaaS mode detected — connecting via Ninetrix vault.[/dim]\n")
        ctx.invoke(mcp_connect, name=name, no_browser=no_browser, no_wait=False)
        return

    already = worker_config.has_server(name)

    if is_custom:
        # ── Custom server (not in catalog) ────────────────────────────────────
        if not server_type:
            server_type = click.prompt("  Server type", type=click.Choice(["npx", "uvx", "python", "docker"]))
        if not package:
            package = click.prompt("  Package / script path")
        block: dict = {"type": server_type, "package": package}
        if extra_args:
            block["args"] = list(extra_args)
        if env_pairs:
            env_dict = {}
            for pair in env_pairs:
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    env_dict[k.strip()] = v.strip()
            if env_dict:
                block["env"] = env_dict
        _write_and_restart(name, block, already, yes, no_restart)
        return

    # ── Catalog server ────────────────────────────────────────────────────────
    entry = mcp_catalog.get(name)
    if entry is None:
        console.print(f"  [red]'{name}' is not in the built-in catalog.[/red]")
        console.print("  Run [bold]ninetrix mcp catalog[/bold] to see available servers.")
        console.print("  Use [bold]--custom[/bold] to add a non-catalog server.\n")
        raise SystemExit(1)

    console.print(f"  [bold]{name}[/bold] — {entry.description}")
    console.print(f"  Package: [dim]{entry.package}[/dim]\n")

    # Check / prompt for required env vars
    missing = entry.missing_env()
    if missing:
        console.print("  [yellow]Required environment variables:[/yellow]")
        dotenv_path = _nearest_dotenv()
        for var in missing:
            label = entry.required_env.get(var, var)
            aliases = [a for a, c in entry.env_aliases.items() if c == var]
            if aliases:
                hint = f"  ({' or '.join(aliases)} also accepted)"
            else:
                hint = ""
            console.print(f"  [red]✗[/red]  [bold]{var}[/bold]  {label}{hint}")

        if not yes:
            console.print()
            save_to_env = click.confirm(
                f"  Enter values now and save to {dotenv_path}?", default=True
            )
            if save_to_env:
                for var in missing:
                    label = entry.required_env.get(var, var)
                    value = click.prompt(f"  {var}", hide_input=True)
                    if value:
                        _append_dotenv(dotenv_path, var, value)
                        os.environ[var] = value
                        console.print(f"  [green]✓[/green] Saved {var} to {dotenv_path}")
                missing = entry.missing_env()  # re-check
        console.print()

    if missing and not yes:
        warn = click.confirm(
            f"  {len(missing)} env var(s) still missing — add anyway?", default=False
        )
        if not warn:
            console.print("  Aborted.\n")
            raise SystemExit(0)

    block = entry.worker_yaml_block()
    _write_and_restart(name, block, already, yes, no_restart)


def _write_and_restart(
    name: str,
    block: dict,
    already: bool,
    yes: bool,
    no_restart: bool,
) -> None:
    if already and not yes:
        if not click.confirm(f"  '{name}' is already configured. Overwrite?", default=False):
            console.print("  Aborted.\n")
            raise SystemExit(0)

    path = worker_config.add_server(name, block)
    verb = "Updated" if already else "Added"
    console.print(f"  [green]✓[/green] {verb} [bold]{name}[/bold] → {path}")

    if no_restart:
        console.print(
            "\n  [dim]Worker not restarted (--no-restart).[/dim]"
            "\n  Run [bold]ninetrix mcp restart[/bold] when ready.\n"
        )
        return

    console.print()
    ok = _restart_worker_with_feedback()
    if not ok:
        return

    # Poll until tools appear
    console.print(f"  Waiting for [bold]{name}[/bold] tools to register…", end="")
    count = _wait_for_server(name, timeout=30)
    if count:
        console.print(f"\r  [green]✓[/green] [bold]{name}[/bold] connected — {count} tool(s) available\n")
        _print_usage_hint(name)
    else:
        console.print(
            f"\r  [yellow]⚠[/yellow]  [bold]{name}[/bold] registered 0 tools after 30s.\n"
            "  Check worker logs: [bold]ninetrix gateway logs[/bold]\n"
        )


def _print_usage_hint(name: str) -> None:
    console.print(
        "  Use in [bold]agentfile.yaml[/bold]:\n"
        f"    [dim]tools:\n"
        f"      - name: {name}\n"
        f"        source: mcp://{name}[/dim]\n"
    )


# ── mcp remove ────────────────────────────────────────────────────────────────

@mcp_cmd.command("remove")
@click.argument("name")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
@click.option("--no-restart", is_flag=True, help="Update config but do not restart worker")
def mcp_remove(name: str, yes: bool, no_restart: bool) -> None:
    """Remove an MCP server from the worker and restart it."""
    console.print()
    console.print("[bold purple]ninetrix mcp remove[/bold purple]\n")

    if not worker_config.has_server(name):
        console.print(f"  [yellow]'{name}' is not in the worker config.[/yellow]")
        console.print("  Run [bold]ninetrix mcp status[/bold] to see what's configured.\n")
        raise SystemExit(0)

    # Warn if server is declared in agentfile.yaml
    try:
        from agentfile.core.models import AgentFile
        af = AgentFile.from_path("agentfile.yaml")
        users = [
            agent_name for agent_name, adef in af.agents.items()
            for t in adef.tools if t.is_mcp() and (t.mcp_name or t.name) == name
        ]
        if users:
            console.print(
                f"  [yellow]⚠[/yellow]  '{name}' is declared in agentfile.yaml "
                f"(used by: {', '.join(users)}).\n"
                "  Removing it will cause those agents to fail on mcp tool calls.\n"
            )
    except Exception:
        pass

    if not yes:
        if not click.confirm(f"  Remove '{name}' from worker config?", default=False):
            console.print("  Aborted.\n")
            raise SystemExit(0)

    removed = worker_config.remove_server(name)
    if removed:
        console.print(f"  [green]✓[/green] Removed [bold]{name}[/bold] from {worker_config.find_config_path()}")
    else:
        console.print(f"  [yellow]'{name}' was not found in config.[/yellow]")
        raise SystemExit(0)

    if no_restart:
        console.print("\n  [dim]Worker not restarted (--no-restart).[/dim]\n")
        return

    console.print()
    ok = _restart_worker_with_feedback()
    if ok:
        console.print(f"  [green]✓[/green] Done. [bold]{name}[/bold] tools removed.\n")


# ── mcp test ──────────────────────────────────────────────────────────────────

@mcp_cmd.command("test")
@click.argument("server_name")
@click.argument("tool_name", required=False)
@click.option("--arg", "-a", "args_pairs", multiple=True, metavar="KEY=VALUE",
              help="Arguments for the tool call (repeatable)")
@click.option("--org-id", default=None,
              help="Gateway organization (default: from MCP_GATEWAY_ORG_ID or 'local')")
def mcp_test(
    server_name: str,
    tool_name: str | None,
    args_pairs: tuple[str, ...],
    org_id: str | None,
) -> None:
    """Test MCP tools via the live gateway.

    \b
    Examples:
      ninetrix mcp test github                            list all github tools
      ninetrix mcp test github github__list_user_repos    call a specific tool
      ninetrix mcp test github github__create_issue -a owner=myorg -a repo=myrepo -a title="Test"
    """
    import json
    try:
        import httpx
    except ImportError:
        console.print("[red]httpx is required: pip install httpx[/red]")
        raise SystemExit(1)

    console.print()
    console.print("[bold purple]ninetrix mcp test[/bold purple]\n")

    ws = org_id or _gw_org_id()
    endpoint = f"{_gw_url()}/v1/mcp/{ws}"
    headers = {**_gw_headers(), "Content-Type": "application/json"}

    # ── 1. tools/list ─────────────────────────────────────────────────────────
    try:
        t0 = time.perf_counter()
        resp = httpx.post(
            endpoint,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers=headers,
            timeout=10,
        )
        ms = int((time.perf_counter() - t0) * 1000)

        if not resp.is_success:
            console.print(f"  [red]✗[/red] tools/list failed ({resp.status_code}): {resp.text}\n")
            raise SystemExit(1)

        result = resp.json()
        if "error" in result:
            console.print(f"  [red]✗[/red] Gateway error: {result['error']}\n")
            raise SystemExit(1)

        all_tools = result.get("result", {}).get("tools", [])
        server_tools = [t for t in all_tools if t["name"].startswith(f"{server_name}__")]

        console.print(
            f"  [green]✓[/green] tools/list — "
            f"{len(server_tools)} [bold]{server_name}[/bold] tool(s) returned in {ms}ms\n"
        )

        if not server_tools:
            console.print(f"  [yellow]No tools found for server '{server_name}'.[/yellow]")
            console.print(f"  Run [bold]ninetrix mcp add {server_name}[/bold] if it's not yet configured.\n")
            if all_tools:
                available = sorted({t["name"].split("__")[0] for t in all_tools if "__" in t["name"]})
                console.print(f"  Available servers: {', '.join(available)}\n")
            raise SystemExit(1)

    except httpx.ConnectError:
        console.print(
            f"  [red]✗[/red] Cannot connect to gateway at {_gw_url()}\n"
            "  Run [bold]ninetrix dev[/bold] to start the stack.\n"
        )
        raise SystemExit(1)

    if tool_name is None:
        # List mode — show all tools for this server
        t = Table(show_header=True, header_style="bold purple")
        t.add_column("Tool")
        t.add_column("Description")
        for tool in server_tools:
            t.add_row(tool["name"], (tool.get("description") or "")[:80])
        console.print(t)
        console.print()
        return

    # ── 2. tools/call ─────────────────────────────────────────────────────────
    call_args: dict = {}
    for pair in args_pairs:
        if "=" in pair:
            k, v = pair.split("=", 1)
            call_args[k.strip()] = v.strip()

    full_tool_name = tool_name if "__" in tool_name else f"{server_name}__{tool_name}"
    console.print(f"  Calling [bold]{full_tool_name}[/bold]…")
    if call_args:
        console.print(f"  Args: {call_args}\n")

    try:
        t0 = time.perf_counter()
        resp = httpx.post(
            endpoint,
            json={
                "jsonrpc": "2.0", "id": 2,
                "method": "tools/call",
                "params": {"name": full_tool_name, "arguments": call_args},
            },
            headers=headers,
            timeout=60,
        )
        ms = int((time.perf_counter() - t0) * 1000)
    except httpx.ConnectError:
        console.print("  [red]✗[/red] Connection lost during tool call.\n")
        raise SystemExit(1)

    if not resp.is_success:
        console.print(f"  [red]✗[/red] HTTP {resp.status_code}: {resp.text}\n")
        raise SystemExit(1)

    data = resp.json()
    if "error" in data:
        console.print(f"  [red]✗[/red] Error: {data['error']}\n")
        raise SystemExit(1)

    call_result = data.get("result", {})
    is_error = call_result.get("isError", False)
    content = call_result.get("content", [])

    if is_error:
        console.print(f"  [red]✗[/red] Tool returned error in {ms}ms:\n")
    else:
        console.print(f"  [green]✓[/green] Success in {ms}ms:\n")

    for item in content:
        if item.get("type") == "text":
            console.print(item["text"])
        else:
            console.print(json.dumps(item, indent=2))
    console.print()


# ── mcp catalog ───────────────────────────────────────────────────────────────

@mcp_cmd.command("catalog")
@click.argument("name", required=False)
def mcp_catalog_cmd(name: str | None) -> None:
    """List all built-in MCP servers available to add.

    Pass a server name to see its full details and required env vars.
    """
    console.print()
    console.print("[bold purple]ninetrix mcp catalog[/bold purple]\n")

    if name:
        entry = mcp_catalog.get(name)
        if entry is None:
            console.print(f"  [red]'{name}' is not in the catalog.[/red]\n")
            raise SystemExit(1)
        _print_catalog_detail(name, entry)
        return

    t = Table(show_header=True, header_style="bold purple")
    t.add_column("Name", style="bold cyan")
    t.add_column("Type")
    t.add_column("Description")
    t.add_column("Requires")
    t.add_column("In worker", justify="center")

    configured = set(worker_config.list_servers())

    for sname, entry in sorted(mcp_catalog.list_all().items()):
        requires = ", ".join(entry.required_env.keys()) or "[dim]nothing[/dim]"
        in_worker = "[green]✓[/green]" if sname in configured else ""
        t.add_row(sname, entry.type, entry.description[:60], requires, in_worker)

    console.print(t)
    console.print()
    console.print("  Add a server:    [bold]ninetrix mcp add <name>[/bold]")
    console.print("  See details:     [bold]ninetrix mcp catalog <name>[/bold]\n")


def _print_catalog_detail(name: str, entry: mcp_catalog.CatalogEntry) -> None:
    console.print(f"  [bold]{name}[/bold]")
    console.print(f"  {entry.description}\n")
    console.print(f"  Type:    {entry.type}")
    console.print(f"  Package: {entry.package}")
    if entry.args:
        console.print(f"  Args:    {entry.args}")
    if entry.required_env:
        console.print("\n  Required env vars:")
        for var, label in entry.required_env.items():
            val = entry.resolve_env_value(var)
            status = "[green]set[/green]" if val else "[red]missing[/red]"
            console.print(f"    {status}  [bold]{var}[/bold]  — {label}")
        if entry.env_aliases:
            console.print("\n  Accepted aliases:")
            for alias, canon in entry.env_aliases.items():
                console.print(f"    {alias} → {canon}")
    console.print()
    console.print("  Add this server:")
    console.print(f"    [bold]ninetrix mcp add {name}[/bold]\n")


# ── post-connect local wiring ─────────────────────────────────────────────────

def _post_connect_local_setup(name: str, api_url: str, headers: dict) -> None:
    """After a successful SaaS connect, wire the server into the local mcp-worker.

    Steps:
      1. Add the server to mcp-worker.yaml (tells the worker which package to run).
      2. Restart the mcp-worker — on first tool call it will fetch credentials
         JIT from the vault via the gateway tool-credential endpoint.
    """
    entry = mcp_catalog.get(name)
    if entry is None:
        console.print("  Run [bold]ninetrix mcp status[/bold] to verify tools are available.\n")
        return

    # 1. Add to mcp-worker.yaml (no credentials — worker fetches them JIT)
    block = entry.worker_yaml_block()
    worker_config.add_server(name, block)
    console.print(
        f"  [green]✓[/green] Added [bold]{name}[/bold] to "
        f"[dim]{worker_config.find_config_path()}[/dim]\n"
    )

    # 2. Restart worker so it picks up the new server entry
    if _gateway_online():
        console.print("  Restarting mcp-worker…\n")
        ok = _restart_worker_with_feedback(verbose=False)
        if ok:
            count = _wait_for_server(name, timeout=30)
            if count:
                console.print(
                    f"  [green]✓[/green] [bold]{name}[/bold] ready — "
                    f"{count} tool(s) available\n"
                )
            else:
                console.print(
                    f"  [yellow]⚠[/yellow]  Worker restarted but [bold]{name}[/bold] "
                    "registered 0 tools — credentials will be fetched on first tool call.\n"
                )
    else:
        console.print(
            "  [dim]Gateway not running — start with [bold]ninetrix dev[/bold] "
            "to activate the new server.[/dim]\n"
        )


# ── mcp connect ───────────────────────────────────────────────────────────────

@mcp_cmd.command("connect")
@click.argument("name")
@click.option("--no-browser", is_flag=True, help="Print URL only, don't open browser")
@click.option("--no-wait", is_flag=True, help="Print URL and exit without waiting (for CI)")
@click.pass_context
def mcp_connect(ctx: click.Context, name: str, no_browser: bool, no_wait: bool) -> None:
    """Connect an MCP server via the Ninetrix credential vault.

    \b
    Opens a browser to authorize the integration, then waits for confirmation.
    Requires: ninetrix auth login

    \b
    Examples:
      ninetrix mcp connect github
      ninetrix mcp connect tavily --no-browser
    """
    try:
        import httpx
    except ImportError:
        console.print("[red]httpx is required: pip install httpx[/red]")
        raise SystemExit(1)

    console.print()
    console.print("[bold purple]ninetrix mcp connect[/bold purple]\n")

    api_url = resolve_saas_url()
    if not api_url:
        console.print("  [red]Not logged in.[/red]  Run: [bold]ninetrix auth login --token <token>[/bold]\n")
        raise SystemExit(1)

    token = read_token(api_url)
    if not token:
        console.print("  [red]Not logged in.[/red]  Run: [bold]ninetrix auth login --token <token>[/bold]\n")
        raise SystemExit(1)

    headers = auth_headers(api_url)

    # 1. Request a magic link from the API
    try:
        resp = httpx.post(
            f"{api_url}/v1/integrations/{name}/connect-link",
            headers=headers,
            timeout=10,
        )
    except httpx.ConnectError:
        console.print(f"  [red]✗[/red] Cannot reach API at [bold]{api_url}[/bold]\n")
        raise SystemExit(1)

    if resp.status_code == 404:
        console.print(f"  [red]'{name}' is not a known integration.[/red]")
        console.print("  Run [bold]ninetrix mcp catalog[/bold] to see available servers.\n")
        raise SystemExit(1)
    if resp.status_code == 429:
        console.print("  [yellow]Too many recent connect attempts.[/yellow] Wait a moment and retry.\n")
        raise SystemExit(1)
    resp.raise_for_status()

    url = resp.json()["url"]

    # 2. Open browser (unless suppressed)
    console.print(f"  Opening browser: [bold]{url}[/bold]\n")
    if not no_browser:
        click.launch(url)

    if no_wait:
        return

    # 3. Single long-poll — server blocks up to 90s, so one request is enough
    console.print("  Waiting for authorization… [dim](Ctrl+C to cancel)[/dim]\n")
    try:
        r = httpx.get(
            f"{api_url}/v1/integrations/{name}/wait-connected",
            headers=headers,
            timeout=95,  # slightly longer than server-side 90s
        )
        if r.is_success and r.json().get("connected"):
            label = r.json().get("account_label") or f"{name}: connected"
            console.print(f"  [green]✓[/green] {label}\n")
            _post_connect_local_setup(name, api_url, headers)
            return
    except httpx.TimeoutException:
        pass
    except httpx.ConnectError:
        pass

    console.print("  [yellow]Timed out waiting for authorization.[/yellow]")
    console.print(f"  Complete manually: {url}\n")


# ── mcp restart ───────────────────────────────────────────────────────────────

@mcp_cmd.command("restart")
def mcp_restart() -> None:
    """Restart the mcp-worker to pick up config or credential changes."""
    console.print()
    console.print("[bold purple]ninetrix mcp restart[/bold purple]\n")

    ok = _restart_worker_with_feedback()
    if ok:
        console.print(
            "  [green]✓[/green] Worker restarting.\n"
            "  Run [bold]ninetrix mcp status[/bold] in a few seconds to verify.\n"
        )


# ── .env helpers ───────────────────────────────────────────────────────────────

def _is_saas_mode() -> bool:
    """Return True when a saas-api is reachable and the user has a token."""
    api_url = resolve_saas_url()
    if not api_url:
        return False
    return bool(read_token(api_url))


def _nearest_dotenv() -> str:
    """Return the path of the .env file to use for saving credentials."""
    return ".env"


def _append_dotenv(path: str, key: str, value: str) -> None:
    """Append or update KEY=VALUE in a .env file."""
    from pathlib import Path
    env_path = Path(path)
    lines = env_path.read_text().splitlines() if env_path.exists() else []

    new_lines = []
    found = False
    for line in lines:
        if line.strip().startswith(f"{key}="):
            new_lines.append(f"{key}={value}")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"{key}={value}")

    env_path.write_text("\n".join(new_lines) + "\n")
