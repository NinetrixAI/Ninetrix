"""ninetrix init — scaffold a new agentfile.yaml with an interactive setup wizard."""

from __future__ import annotations

import os
from pathlib import Path

import click
from jinja2 import Environment, PackageLoader
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

console = Console()

# ── Provider registry ─────────────────────────────────────────────────────────
# Each entry: (provider_key, display_name, default_model, env_var_for_api_key)

PROVIDERS = [
    ("anthropic",    "Anthropic",     "claude-sonnet-4-6",          "ANTHROPIC_API_KEY"),
    ("openai",       "OpenAI",        "gpt-4o",                     "OPENAI_API_KEY"),
    ("google",       "Google Gemini", "gemini-2.5-flash-preview-05-20", "GEMINI_API_KEY"),
    ("deepseek",     "DeepSeek",      "deepseek-chat",              "DEEPSEEK_API_KEY"),
    ("mistral",      "Mistral",       "mistral-large-latest",       "MISTRAL_API_KEY"),
    ("groq",         "Groq",          "llama-3.3-70b-versatile",    "GROQ_API_KEY"),
    ("together_ai",  "Together AI",   "together_ai/meta-llama/Llama-3.3-70B-Instruct-Turbo", "TOGETHERAI_API_KEY"),
    ("openrouter",   "OpenRouter",    "openrouter/auto",            "OPENROUTER_API_KEY"),
    ("cerebras",     "Cerebras",      "cerebras/llama-3.3-70b",     "CEREBRAS_API_KEY"),
    ("fireworks_ai", "Fireworks",     "fireworks_ai/accounts/fireworks/models/llama-v3p3-70b-instruct", "FIREWORKS_API_KEY"),
    ("bedrock",      "AWS Bedrock",   "bedrock/anthropic.claude-sonnet-4-6-20250514-v1:0", "AWS_ACCESS_KEY_ID"),
    ("azure",        "Azure OpenAI",  "azure/gpt-4o",               "AZURE_API_KEY"),
]

_PROVIDER_KEYS = [p[0] for p in PROVIDERS]
_PROVIDER_NAMES = {p[0]: p[1] for p in PROVIDERS}
_DEFAULT_MODELS = {p[0]: p[2] for p in PROVIDERS}
_KEY_ENV_VARS = {p[0]: p[3] for p in PROVIDERS}


def _save_api_key_to_dotenv(env_var: str, value: str) -> None:
    """Append or update an API key in .env file."""
    env_path = Path(".env")
    lines: list[str] = []
    found = False

    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.strip().startswith(f"{env_var}="):
                lines.append(f"{env_var}={value}")
                found = True
            else:
                lines.append(line)

    if not found:
        lines.append(f"{env_var}={value}")

    env_path.write_text("\n".join(lines) + "\n")


def _select_provider_interactive() -> str:
    """Arrow-key provider selector. Falls back to numbered list if InquirerPy unavailable."""
    try:
        from InquirerPy import inquirer
        choices = [{"name": p[1], "value": p[0]} for p in PROVIDERS]
        return inquirer.select(
            message="Pick your LLM provider:",
            choices=choices,
            default="anthropic",
            pointer="›",
        ).execute()
    except ImportError:
        # Fallback: numbered list
        console.print("  Pick your LLM provider:\n")
        for i, (key, name, _, _) in enumerate(PROVIDERS, 1):
            console.print(f"    {i:2d}. {name}")
        console.print()
        while True:
            choice = Prompt.ask("  Enter number", default="1")
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(PROVIDERS):
                    return PROVIDERS[idx][0]
            except ValueError:
                pass
            console.print("  [red]Invalid choice[/red]")


@click.command("init")
@click.option("--name", "-n", default=None, help="Agent name")
@click.option("--provider", "-p", default=None,
              type=click.Choice(_PROVIDER_KEYS, case_sensitive=False),
              help="LLM provider")
@click.option("--yes", "-y", is_flag=True, help="Skip prompts, use defaults")
@click.argument("output", default="agentfile.yaml")
def init_cmd(name: str | None, provider: str | None, yes: bool, output: str) -> None:
    """Create a new agentfile.yaml — the starting point for your AI agent."""
    out_path = Path(output)

    if out_path.exists() and not yes:
        overwrite = click.confirm(f"  {out_path} already exists. Overwrite?", default=False)
        if not overwrite:
            console.print("  Aborted.")
            return

    # ── Non-interactive mode ─────────────────────────────────────────────
    if yes:
        agent_name = name or "my-agent"
        agent_provider = provider or "anthropic"
        agent_description = "An AI agent built with Ninetrix"
        _write_yaml(out_path, agent_name, agent_description, agent_provider)
        return

    # ── Interactive wizard ───────────────────────────────────────────────
    console.print()
    console.print(Panel(
        "[bold]Welcome to Ninetrix[/bold]\n\n"
        "Let's build your first AI agent.\n"
        "This will create an agentfile.yaml in the current directory.",
        border_style="purple",
        width=56,
    ))
    console.print()

    # 1. Agent name
    agent_name = name or Prompt.ask("  What should we call your agent?", default="my-agent")
    console.print()

    # 2. Provider selection
    if provider:
        agent_provider = provider
    else:
        agent_provider = _select_provider_interactive()

    provider_display = _PROVIDER_NAMES.get(agent_provider, agent_provider)
    console.print(f"\n  [green]✓[/green] {provider_display}\n")

    # 3. API key
    env_var = _KEY_ENV_VARS.get(agent_provider, "")
    if env_var:
        existing_key = os.environ.get(env_var) or _read_dotenv_key(env_var)
        if existing_key:
            masked = existing_key[:8] + "..." + existing_key[-4:] if len(existing_key) > 16 else "***"
            console.print(f"  [green]✓[/green] {env_var} found ({masked})\n")
        else:
            api_key = Prompt.ask(f"  Enter your {provider_display} API key")
            if api_key.strip():
                _save_api_key_to_dotenv(env_var, api_key.strip())
                console.print(f"  [green]✓[/green] Saved to .env\n")
            else:
                console.print(f"  [dim]Skipped. Set {env_var} in .env or your shell before running.[/dim]\n")

    # 4. What does your agent do?
    agent_description = Prompt.ask(
        "  What will your agent do?",
        default="Help users accomplish tasks",
    )
    console.print()

    # ── Write file ───────────────────────────────────────────────────────
    _write_yaml(out_path, agent_name, agent_description, agent_provider)


def _read_dotenv_key(key: str) -> str | None:
    env_file = Path(".env")
    if not env_file.exists():
        return None
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _write_yaml(out_path: Path, name: str, description: str, provider: str) -> None:
    agent_key = name.lower().replace(" ", "-")
    model = _DEFAULT_MODELS.get(provider, "claude-sonnet-4-6")

    env = Environment(
        loader=PackageLoader("agentfile", "templates"),
        keep_trailing_newline=True,
    )
    template = env.get_template("agentfile.yaml.j2")
    rendered = template.render(
        name=name,
        key=agent_key,
        description=description,
        provider=provider,
        model=model,
        temperature="0.2",
    )
    out_path.write_text(rendered)

    provider_display = _PROVIDER_NAMES.get(provider, provider)
    console.print(f"  [green]✓[/green] Created [bold]{out_path}[/bold]")
    console.print(f"  [dim]{provider_display} · {model}[/dim]")
    console.print()
    console.print("  [bold]Next steps:[/bold]")
    console.print("    ninetrix build     build your agent")
    console.print("    ninetrix run       run it locally")
    console.print()
    console.print("  [dim]Docs: https://docs.ninetrix.io/quickstart[/dim]")
    console.print()
