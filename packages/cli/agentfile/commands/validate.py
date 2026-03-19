"""ninetrix validate — static analysis of agentfile.yaml without building."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Literal

import click
from rich.console import Console
from rich.table import Table

console = Console()

_KEY_ENV_VARS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai":    "OPENAI_API_KEY",
    "google":    "GEMINI_API_KEY",
    "mistral":   "MISTRAL_API_KEY",
    "groq":      "GROQ_API_KEY",
}

Level = Literal["ok", "warn", "error"]


def _r(level: Level, category: str, message: str) -> dict:
    return {"level": level, "category": category, "message": message}


def _load_dotenv_key(key: str) -> str | None:
    env_file = Path(".env")
    if not env_file.exists():
        return None
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _check_schema(af_path: str, environment: str | None) -> tuple[list[dict], object | None]:
    """Parse + validate the agentfile. Returns (results, AgentFile | None)."""
    results: list[dict] = []
    p = Path(af_path)

    if not p.exists():
        results.append(_r("error", "file", f"{af_path} not found"))
        return results, None

    try:
        from agentfile.core.models import AgentFile
        af = AgentFile.from_path(af_path)
    except Exception as exc:
        results.append(_r("error", "parse", str(exc)))
        return results, None

    results.append(_r("ok", "parse", f"Parsed successfully ({len(af.agents)} agent(s))"))

    if environment:
        if environment not in af.environments:
            available = ", ".join(af.environments.keys()) or "none defined"
            results.append(_r("error", "environment", f"'{environment}' not found — available: {available}"))
            return results, None
        af = af.for_env(environment)
        results.append(_r("ok", "environment", f"Environment overlay '{environment}' applied"))

    errors = af.validate()
    if errors:
        for e in errors:
            results.append(_r("error", "schema", e))
    else:
        results.append(_r("ok", "schema", "Schema valid"))

    return results, af


def _check_agents(af) -> list[dict]:
    results: list[dict] = []

    for agent_name, agent_def in af.agents.items():
        prefix = agent_name

        # Provider API key
        key_var = _KEY_ENV_VARS.get(agent_def.provider)
        if key_var:
            val = os.environ.get(key_var) or _load_dotenv_key(key_var)
            if val:
                masked = val[:4] + "…" + val[-4:] if len(val) > 8 else "***"
                results.append(_r("ok", prefix, f"{key_var} set ({masked})"))
            else:
                results.append(_r("warn", prefix, f"{key_var} not set — agent won't run without it"))
        else:
            results.append(_r("warn", prefix, f"Unknown provider '{agent_def.provider}' — no API key check"))

        # MCP tools
        from agentfile.core.mcp_catalog import get as _mcp_catalog_get
        for tool in agent_def.tools:
            if tool.is_mcp():
                entry = _mcp_catalog_get(tool.mcp_name)
                if entry is None:
                    results.append(_r("warn", prefix,
                        f"MCP tool '{tool.mcp_name}' not in catalog — run: ninetrix mcp add {tool.mcp_name}"))
                else:
                    results.append(_r("ok", prefix, f"MCP tool '{tool.mcp_name}' → {entry.type}:{entry.package}"))
            elif tool.is_composio():
                results.append(_r("ok", prefix, f"Composio tool '{tool.composio_app}'" +
                    (f" (actions: {', '.join(tool.actions)})" if tool.actions else "")))

        # Triggers
        eff_triggers = af.effective_triggers(agent_def)
        for t in eff_triggers:
            if t.type == "webhook":
                results.append(_r("ok", prefix, f"Webhook trigger: {t.endpoint} port {t.port}"))
            elif t.type == "schedule":
                results.append(_r("ok", prefix, f"Schedule trigger: cron='{t.cron}'"))

        # Execution mode
        exec_obj = af.effective_execution(agent_def)
        if exec_obj.mode == "planned":
            results.append(_r("ok", prefix,
                f"Planned execution (max_steps={exec_obj.max_steps}, verify_steps={exec_obj.verify_steps})"))

        # Thinking
        thinking = exec_obj.thinking
        if thinking.enabled:
            results.append(_r("ok", prefix,
                f"Thinking enabled: {thinking.provider}/{thinking.model} "
                f"(max_tokens={thinking.max_tokens}, min_input_len={thinking.min_input_length})"))

        # Collaborators
        if agent_def.collaborators:
            missing = [c for c in agent_def.collaborators if c not in af.agents]
            if missing:
                results.append(_r("error", prefix,
                    f"Collaborators not found in agentfile: {', '.join(missing)}"))
            else:
                results.append(_r("ok", prefix,
                    f"Collaborators: {', '.join(agent_def.collaborators)}"))
            if agent_def.routing_mode == "auto":
                results.append(_r("ok", prefix,
                    f"Auto-routing enabled (router: {agent_def.routing_provider or agent_def.provider}"
                    f"/{agent_def.routing_model or 'claude-haiku-4-5-20251001'})"))

        # Structured output
        if agent_def.output_type is not None:
            ot = agent_def.output_type
            if not isinstance(ot, dict):
                results.append(_r("error", prefix,
                    "output_type must be a JSON Schema object"))
            elif "properties" not in ot and "type" not in ot:
                results.append(_r("warn", prefix,
                    "output_type has no 'properties' or 'type' — schema may be incomplete"))
            else:
                n_props = len(ot.get("properties", {}))
                n_req = len(ot.get("required", []))
                results.append(_r("ok", prefix,
                    f"Structured output: {n_props} field(s), {n_req} required"))

        # Budget without persistence
        eff_gov = af.effective_governance(agent_def)
        if eff_gov.max_budget_per_run < 1.0 and not hasattr(agent_def, "persistence"):
            # Check if persistence is configured via raw YAML
            raw_agents = getattr(af, "raw", {}).get("agents", {})
            agent_raw = raw_agents.get(agent_name, {})
            if not agent_raw.get("persistence"):
                results.append(_r("warn", prefix,
                    f"Budget ${eff_gov.max_budget_per_run:.2f} set but no persistence configured — "
                    "budget cannot be tracked across restarts"))

        # Volumes
        eff_vols = af.effective_volumes(agent_def)
        for vol in eff_vols:
            if vol.provider == "local" and vol.host_path:
                resolved = os.path.expandvars(vol.host_path)
                if not Path(resolved).exists():
                    results.append(_r("warn", prefix,
                        f"Volume '{vol.name}': host_path '{vol.host_path}' does not exist"))
            elif vol.provider == "s3":
                bucket = os.path.expandvars(vol.bucket or "")
                if not bucket or bucket.startswith("$"):
                    results.append(_r("warn", prefix,
                        f"Volume '{vol.name}' (s3): bucket '{vol.bucket}' not resolved"))

    return results


def _check_template_render(af_path: str, af) -> list[dict]:
    """Dry-run Jinja2 template rendering for each agent without Docker build."""
    results: list[dict] = []

    try:
        from agentfile.commands.build import _render_templates
    except ImportError as exc:
        results.append(_r("warn", "templates", f"Cannot import _render_templates: {exc}"))
        return results

    for agent_name, agent_def in af.agents.items():
        with tempfile.TemporaryDirectory(prefix=f"agentfile-validate-{agent_name}-") as tmp:
            ctx = Path(tmp)
            import shutil
            shutil.copy(af_path, ctx / "agentfile.yaml")
            try:
                _render_templates(agent_def, af, ctx)
                entrypoint_lines = len((ctx / "entrypoint.py").read_text().splitlines())
                dockerfile_lines = len((ctx / "Dockerfile").read_text().splitlines())
                results.append(_r("ok", agent_name,
                    f"Templates render OK (entrypoint.py: {entrypoint_lines} lines, "
                    f"Dockerfile: {dockerfile_lines} lines)"))
            except Exception as exc:
                results.append(_r("error", agent_name, f"Template render failed: {exc}"))

    return results


def _print_table(results: list[dict]) -> bool:
    """Print results table. Returns True if any errors found."""
    table = Table(box=None, padding=(0, 1), show_header=False)
    table.add_column("icon",     style="bold", no_wrap=True)
    table.add_column("category", style="dim",  no_wrap=True)
    table.add_column("message")

    any_error = False
    for r in results:
        level = r["level"]
        if level == "ok":
            icon, color = "✓", "green"
        elif level == "warn":
            icon, color = "!", "yellow"
        else:
            icon, color = "✗", "red"
            any_error = True

        table.add_row(
            f"[{color}]{icon}[/{color}]",
            f"[dim]{r['category']}[/dim]",
            f"[{color}]{r['message']}[/{color}]",
        )

    console.print(table)
    return any_error


@click.command("validate")
@click.option("--file", "-f", "agentfile_path", default="agentfile.yaml",
              show_default=True, help="Path to agentfile.yaml")
@click.option("--environment", "environment", default=None, metavar="NAME",
              help="Apply environment overlay before validating (e.g. prod)")
@click.option("--no-render", is_flag=True, default=False,
              help="Skip Jinja2 template dry-run (faster)")
@click.option("--json", "output_json", is_flag=True, default=False,
              help="Output results as JSON (useful for CI)")
def validate_cmd(agentfile_path: str, environment: str | None,
                 no_render: bool, output_json: bool) -> None:
    """Validate agentfile.yaml without building — schema, tools, env vars, templates."""
    if not output_json:
        console.print()
        console.print("[bold purple]ninetrix validate[/bold purple]\n")

    all_results: list[dict] = []

    # 1. Parse + schema
    schema_results, af = _check_schema(agentfile_path, environment)
    all_results.extend(schema_results)

    if af is not None:
        # 2. Per-agent checks
        all_results.extend(_check_agents(af))

        # 3. Template dry-run
        if not no_render:
            all_results.extend(_check_template_render(agentfile_path, af))

    if output_json:
        print(json.dumps(all_results, indent=2))
        any_error = any(r["level"] == "error" for r in all_results)
        sys.exit(1 if any_error else 0)

    any_error = _print_table(all_results)
    console.print()

    errors = [r for r in all_results if r["level"] == "error"]
    warns  = [r for r in all_results if r["level"] == "warn"]

    if any_error:
        console.print(f"  [red]Validation failed[/red] — {len(errors)} error(s), {len(warns)} warning(s)\n")
        raise SystemExit(1)
    elif warns:
        console.print(f"  [yellow]Valid with warnings[/yellow] — {len(warns)} warning(s)\n")
    else:
        console.print("  [green]All checks passed.[/green]\n")
