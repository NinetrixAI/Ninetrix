"""agentfile build — validate + Dockerfile + docker build."""

from __future__ import annotations

import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import click
from jinja2 import Environment, PackageLoader
from rich.console import Console

from agentfile.core.models import AgentDef, AgentFile
from agentfile.core.docker import build_image
from agentfile.core.template_context import build_context

console = Console()


def _render_templates(agent_def: AgentDef, af: AgentFile, context_dir: Path) -> None:
    """Render Dockerfile and entrypoint.py into context_dir for a single agent."""
    env = Environment(
        loader=PackageLoader("agentfile", "templates"),
        keep_trailing_newline=True,
    )

    ctx = build_context(
        af,
        agent_def,
        is_saas_runner=False,
        has_invoke_server=agent_def.serve,
        _warn=lambda msg: console.print(f"  [yellow]Warning:[/yellow] {msg}"),
    )

    dockerfile = env.get_template("Dockerfile.j2").render(**ctx)
    (context_dir / "Dockerfile").write_text(dockerfile)

    entrypoint = env.get_template("entrypoint.py.j2").render(**ctx)
    (context_dir / "entrypoint.py").write_text(entrypoint)


def _build_one(
    agent_name: str, agent_def: AgentDef, af: AgentFile,
    agentfile_path: str, tag: str,
) -> tuple[bool, str, list[str]]:
    """Render templates and build one image in a worker thread.

    Returns (success, full_tag, log_lines). Never prints to console directly
    so it is safe to call from multiple threads simultaneously.
    """
    import docker as _docker
    from docker.errors import DockerException as _DE

    lines: list[str] = []
    with tempfile.TemporaryDirectory(prefix=f"agentfile-build-{agent_name}-") as tmp:
        ctx = Path(tmp)
        shutil.copy(agentfile_path, ctx / "agentfile.yaml")
        _render_templates(agent_def, af, ctx)
        full_tag = agent_def.image_name(tag)
        try:
            client = _docker.from_env()
            _img, logs = client.images.build(
                path=str(ctx), tag=full_tag, rm=True, forcerm=True,
            )
            for chunk in logs:
                line = chunk.get("stream", "").rstrip()
                if line:
                    lines.append(line)
            return True, full_tag, lines
        except _DE as exc:
            return False, full_tag, [str(exc)]


@click.command("build")
@click.option("--file", "-f", "agentfile_path", default="agentfile.yaml",
              show_default=True, help="Path to agentfile.yaml")
@click.option("--tag", "-t", default="latest", show_default=True,
              help="Docker image tag")
@click.option("--push", is_flag=True, default=False,
              help="Push the image(s) after building")
@click.option("--agent", "agent_filter", default=None,
              help="Build only this agent key (multi-agent files)")
@click.option("--environment", "environment", default=None, metavar="NAME",
              help="Apply environment overlay from agentfile.yaml (e.g. dev, prod)")
def build_cmd(agentfile_path: str, tag: str, push: bool, agent_filter: str | None,
              environment: str | None) -> None:
    """Validate agentfile.yaml and build Docker image(s)."""
    console.print()
    console.print("[bold purple]ninetrix build[/bold purple]\n")

    console.print(f"  Reading [bold]{agentfile_path}[/bold] …")
    try:
        af = AgentFile.from_path(agentfile_path)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)

    if environment:
        if environment not in af.environments:
            available = ", ".join(af.environments.keys()) or "none defined"
            console.print(f"[red]Environment '{environment}' not found.[/red] Available: {available}")
            raise SystemExit(1)
        af = af.for_env(environment)
        console.print(f"  [dim]Environment:[/dim] [bold]{environment}[/bold]")

    errors = af.validate()
    if errors:
        console.print("[red]Validation failed:[/red]")
        for e in errors:
            console.print(f"    • {e}")
        raise SystemExit(1)
    console.print("  [green]✓[/green] Agentfile is valid")

    if agent_filter:
        if agent_filter not in af.agents:
            console.print(f"[red]Agent '{agent_filter}' not found in agentfile.[/red]")
            console.print(f"  Available agents: {', '.join(af.agents.keys())}")
            raise SystemExit(1)
        agents_to_build = {agent_filter: af.agents[agent_filter]}
    else:
        agents_to_build = af.agents

    built_refs: list[str] = []

    if len(agents_to_build) == 1:
        # Single agent — stream docker output verbosely as before
        agent_name, agent_def = next(iter(agents_to_build.items()))
        with tempfile.TemporaryDirectory(prefix=f"agentfile-build-{agent_name}-") as tmp:
            ctx = Path(tmp)
            shutil.copy(agentfile_path, ctx / "agentfile.yaml")
            _render_templates(agent_def, af, ctx)
            image_ref = build_image(ctx, agent_def.image_name(), tag)
            built_refs.append(image_ref)
    else:
        # Multi-agent — build all images in parallel, show spinner + summary
        names = list(agents_to_build.keys())
        console.print(
            f"  [dim]Building {len(names)} image(s) in parallel: {', '.join(names)}[/dim]"
        )
        results: dict[str, tuple[bool, str, list[str]]] = {}
        with console.status("  Building images…", spinner="dots"):
            with ThreadPoolExecutor(max_workers=len(agents_to_build)) as pool:
                futures = {
                    pool.submit(_build_one, name, adef, af, agentfile_path, tag): name
                    for name, adef in agents_to_build.items()
                }
                for future in as_completed(futures):
                    results[futures[future]] = future.result()

        any_failed = False
        for name in names:          # print in declaration order
            ok, ref, lines = results[name]
            if ok:
                console.print(f"  [green]✓[/green] Built [bold]{ref}[/bold]")
                built_refs.append(ref)
            else:
                msg = lines[-1] if lines else "unknown error"
                console.print(f"  [red]✗[/red] Failed to build [bold]{name}[/bold]: {msg}")
                any_failed = True
        if any_failed:
            raise SystemExit(1)

    if push:
        from agentfile.core.docker import push_image
        for ref in built_refs:
            push_image(ref)

    if len(built_refs) == 1:
        console.print(f"\n  Run it with:\n    [bold]ninetrix run --image {built_refs[0]}[/bold]\n")
    else:
        console.print(f"\n  Start the warm pool with:\n    [bold]ninetrix up --file {agentfile_path}[/bold]\n")
