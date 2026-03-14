"""ninetrix mcp — inspect, add, and test MCP tool integrations."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.table import Table

from agentfile.core.models import AgentFile
from agentfile.core.mcp_registry import (
    MCPServerDef,
    _user_config_path,
    get_merged_registry,
    resolve,
)

console = Console()


# ── mcp group ─────────────────────────────────────────────────────────────────

@click.group("mcp")
def mcp_cmd() -> None:
    """Manage MCP (Model Context Protocol) tool integrations."""
    pass


# ── mcp list ──────────────────────────────────────────────────────────────────

@mcp_cmd.command("list")
@click.option("--file", "-f", "agentfile_path", default="agentfile.yaml",
              show_default=True, help="Path to agentfile.yaml")
@click.option("--all", "show_all", is_flag=True,
              help="Show full built-in + user registry instead of agentfile tools")
def mcp_list(agentfile_path: str, show_all: bool) -> None:
    """List MCP tools declared in agentfile.yaml with their resolution status."""
    console.print()
    console.print("[bold purple]ninetrix mcp list[/bold purple]\n")

    if show_all:
        _show_full_registry()
        return

    try:
        af = AgentFile.from_path(agentfile_path)
    except FileNotFoundError:
        console.print(f"[red]File not found:[/red] {agentfile_path}")
        raise SystemExit(1)

    mcp_tools = [
        t
        for agent_def in af.agents.values()
        for t in agent_def.tools
        if t.is_mcp()
    ]

    if not mcp_tools:
        console.print("  No MCP tools declared in this agentfile.\n")
        console.print(
            "  Add tools in [bold]agentfile.yaml[/bold] like:\n"
            "    [dim]tools:\n"
            "      - name: web_search\n"
            "        source: mcp://brave-search[/dim]\n"
        )
        return

    table = Table(show_header=True, header_style="bold purple")
    table.add_column("Alias")
    table.add_column("Registry Key")
    table.add_column("Type")
    table.add_column("Package")
    table.add_column("Status")
    table.add_column("Required Env Vars")

    for tool in mcp_tools:
        key = tool.mcp_name or ""
        sdef = resolve(key)

        if sdef:
            status = "[green]found[/green]"
            stype = sdef.type
            pkg = sdef.package
            if sdef.env_keys:
                env_parts = []
                for k in sdef.env_keys:
                    if os.environ.get(k):
                        env_parts.append(f"[green]{k}[/green]")
                    else:
                        env_parts.append(f"[red]{k}[/red]")
                env_display = ", ".join(env_parts)
            else:
                env_display = "[dim]none[/dim]"
        else:
            status = "[yellow]unknown[/yellow]"
            stype = "?"
            pkg = "[dim]not in registry[/dim]"
            env_display = "[dim]—[/dim]"

        table.add_row(tool.name, key, stype, pkg, status, env_display)

    console.print(table)
    console.print(
        "\n  [dim]Env vars shown in[/dim] [green]green[/green] = set, "
        "[red]red[/red] = missing.\n"
        "  Run [bold]ninetrix mcp add <name> ...[/bold] to register a custom server.\n"
    )


def _show_full_registry() -> None:
    registry = get_merged_registry()
    table = Table(show_header=True, header_style="bold purple")
    table.add_column("Name")
    table.add_column("Type")
    table.add_column("Package")
    table.add_column("Env Keys")
    table.add_column("Description")

    for name, sdef in sorted(registry.items()):
        env_str = ", ".join(sdef.env_keys) if sdef.env_keys else "[dim]none[/dim]"
        table.add_row(name, sdef.type, sdef.package, env_str, sdef.description)

    console.print(table)
    console.print()


# ── mcp add ───────────────────────────────────────────────────────────────────

@mcp_cmd.command("add")
@click.argument("name")
@click.option("--type", "server_type", required=True,
              type=click.Choice(["npx", "uvx", "docker", "python"], case_sensitive=False),
              help="How the server is launched")
@click.option("--package", "-p", required=True,
              help="Package name / script path to run")
@click.option("--args", "-a", "extra_args", multiple=True, metavar="ARG",
              help="Extra CLI args passed to the server (repeatable)")
@click.option("--env-key", "-e", "env_keys", multiple=True, metavar="KEY",
              help="Environment variable required by this server (repeatable)")
@click.option("--description", "-d", default="",
              help="Human-readable description")
@click.option("--yes", "-y", is_flag=True,
              help="Overwrite existing entry without prompting")
def mcp_add(
    name: str,
    server_type: str,
    package: str,
    extra_args: tuple[str, ...],
    env_keys: tuple[str, ...],
    description: str,
    yes: bool,
) -> None:
    """Add or update a custom MCP server in ~/.agentfile/mcp.yaml."""
    console.print()
    console.print("[bold purple]ninetrix mcp add[/bold purple]\n")

    config_path = _user_config_path()

    existing: dict = {}
    if config_path.exists():
        try:
            existing = yaml.safe_load(config_path.read_text()) or {}
        except yaml.YAMLError as exc:
            console.print(f"[red]Cannot parse existing config:[/red] {exc}")
            raise SystemExit(1)

    if name in existing and not yes:
        overwrite = click.confirm(
            f"  '{name}' already exists in ~/.agentfile/mcp.yaml. Overwrite?",
            default=False,
        )
        if not overwrite:
            console.print("  Aborted.")
            return

    entry: dict = {"type": server_type, "package": package}
    if extra_args:
        entry["args"] = list(extra_args)
    if env_keys:
        entry["env_keys"] = list(env_keys)
    if description:
        entry["description"] = description

    existing[name] = entry
    config_path.write_text(yaml.dump(existing, default_flow_style=False, sort_keys=True))

    console.print(f"  [green]✓[/green] Saved '[bold]{name}[/bold]' → {config_path}")
    console.print(
        f"\n  Use it in agentfile.yaml:\n"
        f"    [dim]tools:\n"
        f"      - name: my_tool\n"
        f"        source: mcp://{name}[/dim]\n"
    )


# ── mcp test ──────────────────────────────────────────────────────────────────

@mcp_cmd.command("test")
@click.argument("name")
@click.option("--timeout", default=15, show_default=True,
              help="Seconds to wait for server startup")
def mcp_test(name: str, timeout: int) -> None:
    """Start an MCP server, call initialize, and print its tool schemas."""
    console.print()
    console.print("[bold purple]ninetrix mcp test[/bold purple]\n")

    sdef = resolve(name)
    if sdef is None:
        console.print(f"  [red]Unknown MCP server:[/red] '{name}'")
        console.print(
            "  Use [bold]ninetrix mcp add[/bold] to register a custom server, "
            "or [bold]ninetrix mcp list --all[/bold] for known names.\n"
        )
        raise SystemExit(1)

    missing_env = [k for k in sdef.env_keys if not os.environ.get(k)]
    if missing_env:
        console.print("  [yellow]Warning:[/yellow] Missing required environment variables:")
        for k in missing_env:
            console.print(f"    [red]{k}[/red] is not set")
        console.print()
        if not click.confirm("  Continue anyway?", default=False):
            raise SystemExit(0)

    console.print(f"  Starting [bold]{name}[/bold] ({sdef.type}: {sdef.package}) …")

    try:
        asyncio.run(_test_server(name, sdef, timeout))
    except KeyboardInterrupt:
        console.print("\n  Interrupted.")
    except Exception as exc:
        console.print(f"\n  [red]Error:[/red] {exc}")
        raise SystemExit(1)


async def _test_server(name: str, sdef: MCPServerDef, timeout: int) -> None:
    """Connect to an MCP server and print its tool list."""
    from contextlib import AsyncExitStack
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = _build_server_params(sdef)

    async with AsyncExitStack() as stack:
        transport = await asyncio.wait_for(
            stack.enter_async_context(stdio_client(params)),
            timeout=timeout,
        )
        read_stream, write_stream = transport
        session: ClientSession = await stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await session.initialize()

        response = await session.list_tools()
        tools = response.tools

        if not tools:
            console.print(f"  [yellow]Server '{name}' returned no tools.[/yellow]\n")
            return

        console.print(f"\n  [green]✓[/green] Connected. {len(tools)} tool(s) available:\n")
        for tool in tools:
            console.print(f"  [bold]{tool.name}[/bold]")
            if tool.description:
                console.print(f"    {tool.description}")
            schema = tool.inputSchema or {}
            if schema.get("properties"):
                pretty = json.dumps(schema, indent=2)
                indented = "\n      ".join(pretty.splitlines())
                console.print(f"    Schema:\n      {indented}")
            console.print()


def _build_server_params(sdef: MCPServerDef) -> "StdioServerParameters":
    """Translate an MCPServerDef into StdioServerParameters for the MCP SDK."""
    from mcp import StdioServerParameters

    env = {k: os.environ[k] for k in sdef.env_keys if k in os.environ} or None

    if sdef.type == "npx":
        return StdioServerParameters(
            command="npx", args=["-y", sdef.package] + sdef.args, env=env
        )
    elif sdef.type == "uvx":
        return StdioServerParameters(
            command="uvx", args=[sdef.package] + sdef.args, env=env
        )
    elif sdef.type == "docker":
        return StdioServerParameters(
            command="docker", args=["run", "--rm", "-i", sdef.package] + sdef.args, env=env
        )
    elif sdef.type == "python":
        return StdioServerParameters(
            command="python", args=[sdef.package] + sdef.args, env=env
        )
    else:
        raise ValueError(f"Unknown server type: {sdef.type!r}")
