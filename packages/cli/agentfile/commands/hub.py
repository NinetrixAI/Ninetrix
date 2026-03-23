"""ninetrix hub — unified search and install for tools + skills."""

from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

console = Console()


@click.group("hub")
def hub_cmd() -> None:
    """Search and install tools + skills from the Ninetrix Hub."""


@hub_cmd.command("search")
@click.argument("query")
def search_cmd(query: str) -> None:
    """Search both tools and skills by name, description, or tag."""
    from agentfile.core.tool_hub import search as tool_search
    from agentfile.core.skill_hub import search as skill_search

    tools = tool_search(query)
    skills = skill_search(query)

    if not tools and not skills:
        console.print(f"  No results for [bold]{query}[/bold]\n")
        return

    table = Table(show_header=True, header_style="bold", pad_edge=False, box=None)
    table.add_column("NAME", style="bold")
    table.add_column("KIND")
    table.add_column("TYPE", style="dim")
    table.add_column("DESCRIPTION")

    for entry in tools:
        verified = " [green]✓[/green]" if entry.verified else ""
        table.add_row(entry.name, f"[cyan]tool[/cyan]{verified}", entry.source_type, entry.description[:55])

    for entry in skills:
        table.add_row(entry.name, "[magenta]skill[/magenta]", "", entry.description[:55])

    console.print()
    console.print(table)
    console.print(f"\n  [dim]{len(tools)} tool(s), {len(skills)} skill(s)[/dim]\n")


@hub_cmd.command("add")
@click.argument("name")
@click.option("--file", "-f", "agentfile_path", default="agentfile.yaml", help="Path to agentfile.yaml")
def add_cmd(name: str, agentfile_path: str) -> None:
    """Add a tool or skill to your agentfile.yaml."""
    from agentfile.core.tool_hub import get as tool_get
    from agentfile.core.skill_hub import get as skill_get

    tool = tool_get(name)
    skill = skill_get(name)

    if tool and skill:
        # Both exist — ask what to add
        console.print(f"\n  [bold]{name}[/bold] exists as both a [cyan]tool[/cyan] and a [magenta]skill[/magenta].\n")
        choice = click.prompt("  Add as", type=click.Choice(["tool", "skill", "both"]), default="both")
        if choice in ("tool", "both"):
            _add_tool(tool, agentfile_path)
        if choice in ("skill", "both"):
            _add_skill(skill, agentfile_path)
        return

    if tool:
        _add_tool(tool, agentfile_path)
        # Suggest companion skills
        if tool.skill_set:
            console.print(f"  [dim]This tool has companion skills:[/dim]")
            for s in tool.skill_set:
                console.print(f"    [green]→[/green] {s}")
            if click.confirm("\n  Also add the companion skill(s)?", default=True):
                for skill_ref in tool.skill_set:
                    _add_skill_ref(skill_ref, agentfile_path)
        return

    if skill:
        _add_skill(skill, agentfile_path)
        return

    console.print(f"  [red]'{name}' not found in tools or skills hub.[/red]")
    console.print(f"  [dim]Run: ninetrix hub search {name}[/dim]\n")
    raise SystemExit(1)


def _add_tool(entry, agentfile_path: str) -> None:
    """Add a tool entry to agentfile.yaml."""
    from pathlib import Path
    import yaml

    path = Path(agentfile_path)
    if not path.exists():
        console.print(f"  [red]{agentfile_path} not found.[/red]")
        console.print(f"  [dim]Use --file to specify: ninetrix hub add {entry.name} --file path/to/agentfile.yaml[/dim]\n")
        raise SystemExit(1)

    data = yaml.safe_load(path.read_text())
    agent_name = next(iter(data.get("agents", {})))
    agent = data["agents"][agent_name]
    if agent.get("tools") is None:
        agent["tools"] = []
    tools_list = agent["tools"]

    # Check if already added (tools can be strings like "hub://gh" or dicts)
    for t in tools_list:
        if isinstance(t, str) and entry.name in t:
            console.print(f"  [yellow]{entry.name} tool already in {agentfile_path}[/yellow]\n")
            return
        if isinstance(t, dict) and t.get("name") == entry.name:
            console.print(f"  [yellow]{entry.name} tool already in {agentfile_path}[/yellow]\n")
            return

    # Add as clean hub:// reference — the CLI resolves everything at build time
    tools_list.append(f"hub://{entry.name}")

    path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
    console.print(f"  [green]✓[/green] Added [cyan]tool[/cyan] [bold]{entry.name}[/bold] to {agentfile_path}")


def _add_skill(entry, agentfile_path: str) -> None:
    """Add a skill entry to agentfile.yaml."""
    ref = f"hub://{entry.name}@{entry.latest_version}"
    _add_skill_ref(ref, agentfile_path)


def _add_skill_ref(ref: str, agentfile_path: str) -> None:
    """Add a skill reference (hub://name@version) to agentfile.yaml."""
    from pathlib import Path
    import yaml

    path = Path(agentfile_path)
    if not path.exists():
        console.print(f"  [red]{agentfile_path} not found.[/red]\n")
        raise SystemExit(1)

    data = yaml.safe_load(path.read_text())
    agent_name = next(iter(data.get("agents", {})))
    agent = data["agents"][agent_name]
    if agent.get("skills") is None:
        agent["skills"] = []
    skills_list = agent["skills"]

    # Check if already added
    existing_refs = set()
    for s in skills_list:
        if isinstance(s, str):
            existing_refs.add(s.split("@")[0])
        elif isinstance(s, dict) and "source" in s:
            existing_refs.add(s["source"].split("@")[0])

    ref_base = ref.split("@")[0]
    if ref_base in existing_refs:
        console.print(f"  [yellow]{ref} skill already in {agentfile_path}[/yellow]")
        return

    skills_list.append(ref)

    path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
    console.print(f"  [green]✓[/green] Added [magenta]skill[/magenta] [bold]{ref}[/bold] to {agentfile_path}")
