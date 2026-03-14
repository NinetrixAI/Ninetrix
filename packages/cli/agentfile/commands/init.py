"""agentfile init — scaffold a new agentfile.yaml."""

from __future__ import annotations

from pathlib import Path

import click
from jinja2 import Environment, PackageLoader
from rich.console import Console
from rich.prompt import Prompt

console = Console()

_PROVIDERS = ["anthropic", "openai", "google", "mistral", "groq"]
_DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-4o",
    "google": "gemini-2.5-flash-lite",
    "mistral": "mistral-large-latest",
    "groq": "llama-3.1-70b-versatile",
}


@click.command("init")
@click.option("--name", "-n", default=None, help="Agent name (used as the agent key)")
@click.option("--provider", "-p", default=None,
              type=click.Choice(_PROVIDERS, case_sensitive=False),
              help="LLM provider")
@click.option("--yes", "-y", is_flag=True, help="Skip interactive prompts, use defaults")
@click.argument("output", default="agentfile.yaml")
def init_cmd(name: str | None, provider: str | None, yes: bool, output: str) -> None:
    """Scaffold a new agentfile.yaml in the current directory."""
    out_path = Path(output)

    if out_path.exists() and not yes:
        overwrite = click.confirm(f"  {out_path} already exists. Overwrite?", default=False)
        if not overwrite:
            console.print("  Aborted.")
            return

    console.print()
    console.print("[bold purple]ninetrix init[/bold purple]\n")

    if yes:
        agent_name = name or "my-agent"
        agent_provider = provider or "anthropic"
        agent_description = "An AI agent built with Agentfile"
        agent_temperature = "0.2"
    else:
        agent_name = name or Prompt.ask("  Agent name", default="my-agent")
        agent_description = Prompt.ask(
            "  Description", default="An AI agent built with Agentfile"
        )
        agent_provider = provider or Prompt.ask(
            "  Provider",
            choices=_PROVIDERS,
            default="anthropic",
        )
        agent_temperature = Prompt.ask("  Temperature (0.0 – 2.0)", default="0.2")

    agent_model = _DEFAULT_MODELS.get(agent_provider, "claude-sonnet-4-6")

    # Sanitize agent name to a valid YAML key (no spaces)
    agent_key = agent_name.lower().replace(" ", "-")

    env = Environment(
        loader=PackageLoader("agentfile", "templates"),
        keep_trailing_newline=True,
    )
    template = env.get_template("agentfile.yaml.j2")
    rendered = template.render(
        name=agent_name,
        key=agent_key,
        description=agent_description,
        provider=agent_provider,
        model=agent_model,
        temperature=agent_temperature,
    )

    out_path.write_text(rendered)
    console.print(f"\n  [green]✓[/green] Created [bold]{out_path}[/bold]")
    console.print("  Edit it, then run [bold]ninetrix build[/bold] to package your agent.\n")
