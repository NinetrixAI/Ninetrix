"""ninetrix schema — dump, document, and validate the agentfile.yaml schema."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.markdown import Markdown

console = Console()

_SCHEMA_PATH = Path(__file__).parent.parent / "core" / "schema.json"


def _load_schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text())


@click.group("schema")
def schema_cmd() -> None:
    """Inspect the agentfile.yaml JSON schema."""
    pass


@schema_cmd.command("dump")
def schema_dump() -> None:
    """Print the full JSON schema to stdout."""
    print(_SCHEMA_PATH.read_text())


@schema_cmd.command("docs")
def schema_docs() -> None:
    """Print a human-readable Markdown reference of the schema."""
    schema = _load_schema()
    lines: list[str] = []
    lines.append("# agentfile.yaml Schema Reference\n")
    lines.append(f"**{schema.get('title', 'Agentfile')}** — {schema.get('description', '')}\n")

    # Root properties
    props = schema.get("properties", {})
    lines.append("## Root Fields\n")
    lines.append("| Field | Type | Required | Description |")
    lines.append("|-------|------|----------|-------------|")
    required = set(schema.get("required", []))
    for name, prop in props.items():
        typ = prop.get("type", prop.get("oneOf", "object"))
        if isinstance(typ, list):
            typ = " | ".join(str(t) for t in typ)
        req = "yes" if name in required else ""
        desc = prop.get("description", "")[:80]
        lines.append(f"| `{name}` | {typ} | {req} | {desc} |")

    # Agent definition
    agent_schema = (props.get("agents", {})
                    .get("additionalProperties", {}))
    agent_props = agent_schema.get("properties", {})
    if agent_props:
        lines.append("\n## Agent Fields\n")
        lines.append("| Field | Type | Required | Description |")
        lines.append("|-------|------|----------|-------------|")
        agent_required = set(agent_schema.get("required", []))
        for name, prop in agent_props.items():
            typ = prop.get("type", "object")
            req = "yes" if name in agent_required else ""
            desc = prop.get("description", "")[:80]
            lines.append(f"| `{name}` | {typ} | {req} | {desc} |")

        # Sub-objects within agent
        for name, prop in agent_props.items():
            sub_props = prop.get("properties", {})
            if sub_props and prop.get("type") == "object":
                lines.append(f"\n### `{name}` Fields\n")
                lines.append("| Field | Type | Description |")
                lines.append("|-------|------|-------------|")
                for sname, sprop in sub_props.items():
                    styp = sprop.get("type", "any")
                    sdesc = sprop.get("description", "")[:80]
                    deprecated = " **(deprecated)**" if sprop.get("deprecated") else ""
                    lines.append(f"| `{sname}` | {styp} | {sdesc}{deprecated} |")

    md = "\n".join(lines)
    console.print(Markdown(md))


@schema_cmd.command("validate")
@click.argument("files", nargs=-1, required=True)
def schema_validate(files: tuple[str, ...]) -> None:
    """Validate one or more agentfile.yaml files (alias for 'ninetrix validate').

    Shows field-level errors pointing to the bad YAML field, not raw stack traces.

    \b
    For full validation including env vars and template rendering use:
      ninetrix validate --file <path>

    \b
    Examples:
      ninetrix schema validate agentfile.yaml
      ninetrix schema validate *.yaml
    """
    from agentfile.commands.validate import _check_schema, _print_table

    all_results: list[dict] = []
    for filepath in files:
        results, _ = _check_schema(filepath, None)
        all_results.extend(results)

    any_error = _print_table(all_results)
    console.print()
    if any_error:
        console.print("  [dim]For full validation: ninetrix validate --file <path>[/dim]\n")
        sys.exit(1)
