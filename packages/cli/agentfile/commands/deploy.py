"""ninetrix deploy — build, push, and ship the agent."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import click
from rich.console import Console
from rich.prompt import Prompt

from agentfile.core.models import AgentFile
from agentfile.core.docker import build_image, push_image
from agentfile.commands.build import _render_templates

console = Console()


@click.command("deploy")
@click.option("--file", "-f", "agentfile_path", default="agentfile.yaml",
              show_default=True, help="Path to agentfile.yaml")
@click.option("--tag", "-t", default="latest", show_default=True,
              help="Docker image tag")
@click.option("--registry", "-r", default=None,
              help="Registry prefix, e.g. ghcr.io/myorg  (default: Docker Hub)")
@click.option("--agent", "agent_filter", default=None,
              help="Deploy only this agent key (multi-agent files)")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
def deploy_cmd(agentfile_path: str, tag: str, registry: str | None,
               agent_filter: str | None, yes: bool) -> None:
    """Build the agent image(s), push to a registry, and print run commands."""
    console.print()
    console.print("[bold purple]ninetrix deploy[/bold purple]\n")

    # 1. Parse + validate
    console.print(f"  Reading [bold]{agentfile_path}[/bold] …")
    af = AgentFile.from_path(agentfile_path)
    errors = af.validate()
    if errors:
        console.print("[red]Validation failed:[/red]")
        for e in errors:
            console.print(f"    • {e}")
        raise SystemExit(1)
    console.print("  [green]✓[/green] Config is valid")

    # 2. Determine agents to deploy
    if agent_filter:
        if agent_filter not in af.agents:
            console.print(f"[red]Agent '{agent_filter}' not found in agentfile.[/red]")
            raise SystemExit(1)
        agents_to_deploy = {agent_filter: af.agents[agent_filter]}
    else:
        agents_to_deploy = af.agents

    # 3. Compose image refs
    image_refs = {}
    for name, agent_def in agents_to_deploy.items():
        base_name = agent_def.image_name(tag)
        if registry:
            reg = registry.rstrip("/")
            slug = name.lower().replace(" ", "-")
            image_refs[name] = f"{reg}/{slug}:{tag}"
        else:
            image_refs[name] = base_name

    for name, ref in image_refs.items():
        console.print(f"  [{name}] will be tagged: [bold]{ref}[/bold]")

    if not yes:
        click.confirm("  Continue?", default=True, abort=True)

    # 4. Build + push each agent
    for name, agent_def in agents_to_deploy.items():
        image_ref = image_refs[name]
        with tempfile.TemporaryDirectory(prefix=f"agentfile-deploy-{name}-") as tmp:
            ctx = Path(tmp)
            shutil.copy(agentfile_path, ctx / "agentfile.yaml")
            _render_templates(agent_def, af, ctx)
            build_image(ctx, image_ref)
        push_image(image_ref)

    # 5. Summary
    console.print()
    console.print("[bold green]Deployed![/bold green]")
    for name, ref in image_refs.items():
        console.print(f"\n  [{name}] Image: [bold]{ref}[/bold]")
    console.print(
        f"\n  Run with:\n"
        f"    [bold]docker run --rm -e ANTHROPIC_API_KEY=... {list(image_refs.values())[0]}[/bold]\n"
    )
