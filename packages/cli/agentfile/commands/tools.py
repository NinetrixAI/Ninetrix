"""ninetrix tools — discover, search, and add tools from the Tool Hub."""

from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

console = Console()


@click.group("tools")
def tools_cmd() -> None:
    """Discover and manage tools from the Ninetrix Tool Hub."""


@tools_cmd.command("search")
@click.argument("query")
def search_cmd(query: str) -> None:
    """Search tools by name, description, or tag."""
    from agentfile.core.tool_hub import search

    results = search(query)
    if not results:
        console.print(f"  No tools found for [bold]{query}[/bold]\n")
        return

    table = Table(show_header=True, header_style="bold", pad_edge=False, box=None)
    table.add_column("NAME", style="bold")
    table.add_column("TYPE", style="dim")
    table.add_column("VERIFIED")
    table.add_column("DESCRIPTION")

    for entry in results:
        verified = "[green]✓[/green]" if entry.verified else " "
        table.add_row(entry.name, entry.source_type, verified, entry.description[:60])

    console.print()
    console.print(table)
    console.print()


@tools_cmd.command("list")
@click.option("--type", "source_type", default=None, help="Filter by type (mcp, openapi, plugin)")
@click.option("--verified", is_flag=True, help="Show only verified tools")
def list_cmd(source_type: str | None, verified: bool) -> None:
    """List all tools in the Tool Hub."""
    from agentfile.core.tool_hub import list_all

    entries = list_all(source_type=source_type, verified_only=verified)
    if not entries:
        console.print("  No tools found.\n")
        return

    table = Table(show_header=True, header_style="bold", pad_edge=False, box=None)
    table.add_column("NAME", style="bold")
    table.add_column("TYPE", style="dim")
    table.add_column("VERIFIED")
    table.add_column("TAGS", style="dim")
    table.add_column("DESCRIPTION")

    for entry in entries:
        verified_mark = "[green]✓[/green]" if entry.verified else " "
        tags = ", ".join(entry.tags[:3])
        table.add_row(entry.name, entry.source_type, verified_mark, tags, entry.description[:50])

    console.print()
    console.print(table)
    console.print(f"\n  [dim]{len(entries)} tool(s)[/dim]\n")


@tools_cmd.command("info")
@click.argument("name")
def info_cmd(name: str) -> None:
    """Show details about a tool."""
    from agentfile.core.tool_hub import get

    entry = get(name)
    if entry is None:
        console.print(f"  [red]Tool '{name}' not found in the Tool Hub.[/red]\n")
        console.print("  [dim]Run 'ninetrix tools search <query>' to find tools.[/dim]\n")
        raise SystemExit(1)

    verified = "[green]✓ verified[/green]" if entry.verified else "[dim]community[/dim]"
    console.print()
    console.print(f"  [bold]{entry.name}[/bold] (v{entry.latest_version}) {verified}")
    console.print(f"  {entry.description}\n")

    if entry.source_type == "mcp":
        console.print(f"  [dim]Source:[/dim]  mcp ({entry.runner} {entry.package})")
    elif entry.source_type == "openapi":
        console.print(f"  [dim]Source:[/dim]  openapi ({entry.spec_url})")
    elif entry.source_type == "plugin":
        console.print(f"  [dim]Source:[/dim]  plugin (pip install {entry.pip_package})")
    elif entry.source_type == "local":
        console.print(f"  [dim]Source:[/dim]  local @Tool ({', '.join(entry.files)})")
        if entry.pip_deps:
            console.print(f"  [dim]Pip:[/dim]     {', '.join(entry.pip_deps)}")
        if entry.apt_deps:
            console.print(f"  [dim]Apt:[/dim]     {', '.join(entry.apt_deps)}")

    if entry.tags:
        console.print(f"  [dim]Tags:[/dim]    {', '.join(entry.tags)}")

    if entry.required_env:
        console.print(f"\n  [bold]Required credentials:[/bold]")
        for var, label in entry.required_env.items():
            status = "[green]✓[/green]" if entry.resolve_env_value(var) else "[yellow]✗[/yellow]"
            console.print(f"    {status} {var}  [dim]{label}[/dim]")

    if entry.skill_set:
        console.print(f"\n  [bold]Recommended skills:[/bold] [dim](oven + baker pattern)[/dim]")
        for skill in entry.skill_set:
            console.print(f"    [green]→[/green] {skill}")
        console.print(f"    [dim]Skills teach the agent HOW to use this tool effectively.[/dim]")

    console.print(f"\n  [bold]Add to agentfile.yaml:[/bold]")
    console.print(f"    [dim]tools:[/dim]")
    for line in entry.agentfile_snippet().splitlines():
        console.print(f"      {line}")
    if entry.skill_set:
        console.print(f"    [dim]skills:[/dim]")
        for skill in entry.skill_set:
            console.print(f"      [dim]- {skill}[/dim]")

    console.print()


@tools_cmd.command("add")
@click.argument("name")
@click.option("--file", "-f", "agentfile_path", default="agentfile.yaml", help="Path to agentfile.yaml")
@click.option("--write", is_flag=True, help="Write the tool to agentfile.yaml and copy files")
def add_cmd(name: str, agentfile_path: str, write: bool) -> None:
    """Add a tool from the Hub to your agentfile.yaml."""
    from agentfile.core.tool_hub import get

    entry = get(name)
    if entry is None:
        console.print(f"  [red]Tool '{name}' not found.[/red] Run 'ninetrix tools search {name}'\n")
        raise SystemExit(1)

    snippet = entry.agentfile_snippet()

    # For local @Tool tools, show what will be installed and require consent
    if entry.source_type == "local" and entry.files:
        verified = "[green]✓ verified[/green]" if entry.verified else "[yellow]community (unverified)[/yellow]"
        console.print()
        console.print(f"  [bold]{entry.name}[/bold] (v{entry.latest_version}) — {verified}")
        console.print(f"  Files to copy:")
        for f in entry.files:
            console.print(f"    [bold]tools/{f}[/bold]")
        if entry.pip_deps:
            console.print(f"  Dependencies (pip): {', '.join(entry.pip_deps)}")
        if entry.apt_deps:
            console.print(f"  Dependencies (apt): {', '.join(entry.apt_deps)}")
        if not entry.verified:
            console.print(f"\n  [yellow]⚠[/yellow]  This is a community tool. Review the code first:")
            console.print(f"    [bold]ninetrix tools inspect {name}[/bold]")
        console.print()

        if write:
            if not click.confirm("  Install this tool?", default=False):
                console.print("  [dim]Cancelled.[/dim]\n")
                return
            _install_local_tool(entry, agentfile_path)
        else:
            console.print(f"  Add to agentfile.yaml:\n")
            for line in snippet.splitlines():
                console.print(f"    {line}")
            console.print(f"\n  [dim]Or run: ninetrix tools add {name} --write[/dim]\n")
        return

    # Non-local tools (mcp, openapi, plugin)
    if write:
        _write_tool_to_agentfile(entry, agentfile_path)
    else:
        console.print(f"\n  Add this to your agentfile.yaml tools section:\n")
        console.print(f"    [bold]{snippet}[/bold]\n")

    # Warn about missing credentials
    missing = entry.missing_env()
    if missing:
        for var in missing:
            label = entry.required_env.get(var, "")
            console.print(f"  [yellow]⚠[/yellow]  Set [bold]{var}[/bold] in your .env file{f' — {label}' if label else ''}")
        console.print()


@tools_cmd.command("inspect")
@click.argument("name")
def inspect_cmd(name: str) -> None:
    """View the source code of a local @Tool before installing it."""
    from agentfile.core.tool_hub import get

    entry = get(name)
    if entry is None:
        console.print(f"  [red]Tool '{name}' not found.[/red]\n")
        raise SystemExit(1)

    if entry.source_type != "local" or not entry.files:
        console.print(f"  [dim]{name} is a {entry.source_type} tool — no source code to inspect.[/dim]\n")
        return

    console.print()
    try:
        files = entry.fetch_files()
    except RuntimeError as exc:
        console.print(f"  [red]{exc}[/red]\n")
        raise SystemExit(1)

    for filename, content in files.items():
        lines = content.splitlines()
        console.print(f"  [bold]── {filename} ({len(lines)} lines) ──[/bold]\n")
        for i, line in enumerate(lines, 1):
            console.print(f"  [dim]{i:4d}[/dim] {line}")
        console.print()

    # Show hash verification
    verified = "[green]✓ verified[/green]" if entry.verified else "[yellow]unverified[/yellow]"
    console.print(f"  SHA256 hashes verified: [green]✓[/green]  |  Status: {verified}\n")


def _install_local_tool(entry, agentfile_path: str) -> None:
    """Fetch code files from hub, verify hashes, copy to ./tools/, update agentfile.yaml."""
    from pathlib import Path

    # 1. Verify agentfile.yaml exists BEFORE fetching anything
    af_path = Path(agentfile_path)
    if not af_path.exists():
        console.print(f"  [red]{agentfile_path} not found.[/red]")
        console.print(f"  [dim]Use --file to specify the path: ninetrix tools add {entry.name} --write --file path/to/agentfile.yaml[/dim]\n")
        raise SystemExit(1)

    # 2. Fetch and verify files
    try:
        files = entry.fetch_files()
    except RuntimeError as exc:
        console.print(f"  [red]Failed: {exc}[/red]\n")
        raise SystemExit(1)

    # 3. Copy files to ./tools/
    tools_dir = Path("tools")
    tools_dir.mkdir(exist_ok=True)

    for filename, content in files.items():
        dest = tools_dir / filename
        if dest.exists():
            if not click.confirm(f"  {dest} already exists. Overwrite?", default=False):
                console.print(f"  [dim]Skipped {filename}[/dim]")
                continue
        dest.write_text(content)
        lines = content.splitlines()
        console.print(f"  [green]✓[/green] Copied [bold]tools/{filename}[/bold] ({len(lines)} lines)")

    # 4. Update agentfile.yaml
    _write_tool_to_agentfile(entry, agentfile_path)

    console.print()


def _write_tool_to_agentfile(entry, agentfile_path: str) -> None:
    """Add a tool entry to agentfile.yaml."""
    from pathlib import Path
    import yaml

    path = Path(agentfile_path)
    if not path.exists():
        console.print(f"  [red]{agentfile_path} not found.[/red]")
        console.print(f"  [dim]Use --file to specify the path: ninetrix tools add {entry.name} --write --file path/to/agentfile.yaml[/dim]\n")
        raise SystemExit(1)

    data = yaml.safe_load(path.read_text())
    agent_name = next(iter(data.get("agents", {})))
    agent = data["agents"][agent_name]
    tools_list = agent.setdefault("tools", [])

    if any(t.get("name") == entry.name for t in tools_list):
        console.print(f"  [yellow]{entry.name} is already in {agentfile_path}[/yellow]\n")
        return

    new_tool: dict = {"name": entry.name}
    if entry.source_type == "mcp":
        new_tool["source"] = f"mcp://{entry.name}"
    elif entry.source_type == "openapi":
        new_tool["source"] = f"openapi://{entry.spec_url}"
    elif entry.source_type == "local":
        new_tool["source"] = f"./tools/{entry.files[0]}" if entry.files else f"./tools/{entry.name}.py"
        if entry.pip_deps or entry.apt_deps:
            deps: dict = {}
            if entry.pip_deps:
                deps["pip"] = entry.pip_deps
            if entry.apt_deps:
                deps["apt"] = entry.apt_deps
            new_tool["dependencies"] = deps
    elif entry.source_type == "plugin":
        new_tool["source"] = f"{entry.name}://default"
    tools_list.append(new_tool)

    path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
    console.print(f"  [green]✓[/green] Added [bold]{entry.name}[/bold] to {agentfile_path}")
