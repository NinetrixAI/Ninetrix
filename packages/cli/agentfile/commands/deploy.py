"""ninetrix deploy — deploy agents to Ninetrix Cloud.

No Docker build required. The YAML is uploaded to the cloud and the
runner base image renders the agent in-container.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import click
import httpx
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agentfile.core.models import AgentFile

console = Console()

_APP_URL = "https://app.ninetrix.io"


def _parse_env_pairs(env_pairs: tuple[str, ...]) -> dict[str, str]:
    """Parse KEY=VALUE pairs from --env flags."""
    result = {}
    for pair in env_pairs:
        if "=" not in pair:
            console.print(f"  [red]✗[/red] Invalid --env format: '{pair}' (expected KEY=VALUE)")
            raise SystemExit(1)
        k, v = pair.split("=", 1)
        result[k.strip()] = v.strip()
    return result


@click.command("deploy")
@click.option("--file", "-f", "agentfile_path", default="agentfile.yaml",
              show_default=True, help="Path to agentfile.yaml")
@click.option("--agent", "-a", "agent_filter", default=None,
              help="Deploy only this agent (default: all agents)")
@click.option("--env", "-e", "env_pairs", multiple=True, metavar="KEY=VALUE",
              help="Extra env vars for the deployment (repeatable)")
@click.option("--region", default=None, help="Fly.io region (default: server picks closest)")
@click.option("--cpus", type=float, default=1, show_default=True, help="CPU cores")
@click.option("--memory", "memory_mb", type=int, default=512, show_default=True,
              help="Memory in MB")
@click.option("--token", "token_override", default=None, envvar="AGENTFILE_API_TOKEN",
              help="API token (overrides auth.json; for CI)")
@click.option("--wait/--no-wait", default=True, show_default=True,
              help="Wait for agents to come online")
@click.option("--json", "json_output", is_flag=True, help="Machine-readable JSON output (for CI)")
@click.option("--dry-run", is_flag=True, help="Show what would be deployed without deploying")
def deploy_cmd(
    agentfile_path: str,
    agent_filter: str | None,
    env_pairs: tuple[str, ...],
    region: str | None,
    cpus: float,
    memory_mb: int,
    token_override: str | None,
    wait: bool,
    json_output: bool,
    dry_run: bool,
) -> None:
    """Deploy agents to Ninetrix Cloud.

    \b
    Uploads your agentfile.yaml to Ninetrix Cloud where it runs on
    managed infrastructure. No Docker build or registry push needed.

    \b
    Quick start:
      ninetrix auth login --token nxt_xxxxx   (one-time)
      ninetrix deploy                         (that's it)

    \b
    CI usage:
      ninetrix deploy --token $NXT_TOKEN --json
    """
    from agentfile.core.cloud import CloudClient, DeployResult, resolve_cloud_auth

    if not json_output:
        console.print()
        console.print("[bold purple]ninetrix deploy[/bold purple]")
        console.print()

    # ── 1. Validate YAML locally (fast, no Docker) ────────────────────────
    path = Path(agentfile_path)
    if not path.exists():
        _fail(json_output, f"File not found: {agentfile_path}")

    af = AgentFile.from_path(agentfile_path)
    errors = af.validate()
    if errors:
        if json_output:
            print(json.dumps({"error": "validation_failed", "details": errors}))
            raise SystemExit(1)
        console.print("  [red]✗[/red] Validation failed:")
        for e in errors:
            console.print(f"    [red]•[/red] {e}")
        raise SystemExit(1)

    if not json_output:
        console.print(f"  [green]✓[/green] Validated {agentfile_path} — {len(af.agents)} agent(s)")

    # ── 2. Resolve auth ───────────────────────────────────────────────────
    api_url, token = resolve_cloud_auth(token_override)
    if not token:
        if json_output:
            print(json.dumps({"error": "not_authenticated"}))
            raise SystemExit(1)
        console.print()
        console.print(Panel(
            "[red bold]Not authenticated[/red bold]\n\n"
            "Run [bold]ninetrix auth login --token <token>[/bold] to connect.\n"
            f"Get your token at [bold]{_APP_URL}/settings/tokens[/bold]",
            title="[red]Authentication required[/red]",
            border_style="red",
        ))
        raise SystemExit(1)

    client = CloudClient(api_url, token)

    # ── 3. Verify identity ────────────────────────────────────────────────
    try:
        identity = client.whoami()
    except httpx.ConnectError:
        _fail(json_output, f"Cannot reach API at {api_url}")
    except httpx.HTTPStatusError as exc:
        _fail(json_output, f"API error: {exc.response.status_code}")

    if not identity.email:
        _fail(json_output, "Invalid or expired token. Run 'ninetrix auth login' to re-authenticate.")

    if not json_output:
        org_display = f" (org: {identity.org_slug})" if identity.org_slug else ""
        console.print(f"  [green]✓[/green] Authenticated as {identity.email}{org_display}")

    # ── 4. Filter agents ──────────────────────────────────────────────────
    if agent_filter:
        if agent_filter not in af.agents:
            _fail(json_output, f"Agent '{agent_filter}' not found in {agentfile_path}")
        agents_to_deploy = {agent_filter: af.agents[agent_filter]}
    else:
        agents_to_deploy = af.agents

    extra_env = _parse_env_pairs(env_pairs)
    yaml_content = path.read_text()

    # ── 5. Dry run ────────────────────────────────────────────────────────
    if dry_run:
        if json_output:
            items = [
                {"agent": name, "region": region or "auto", "cpus": cpus, "memory_mb": memory_mb}
                for name in agents_to_deploy
            ]
            print(json.dumps({"dry_run": True, "agents": items}))
        else:
            _print_dry_run(agents_to_deploy, client, region, cpus, memory_mb)
        return

    # ── 6. Deploy each agent ─────────────────────────────────────────────
    results: list[DeployResult] = []

    for name, agent_def in agents_to_deploy.items():
        if not json_output:
            console.print(f"  [bold]⠋[/bold] Deploying [bold]{name}[/bold]…", end="")

        try:
            result = client.deploy_agent(
                agent_name=name,
                yaml_content=yaml_content,
                description=agent_def.description,
                region=region,
                cpus=int(cpus),
                memory_mb=memory_mb,
                env=extra_env,
            )

            # Build URLs
            if identity.org_slug:
                result.url = f"https://{identity.org_slug}.ninetrix.app/{name}"
            if result.agent_id:
                result.dashboard_url = f"{_APP_URL}/agents/{result.agent_id}"

            results.append(result)

            if not json_output:
                action_label = "created" if result.action == "created" else "updated"
                console.print(f"\r  [green]✓[/green] {name} — {action_label}")

        except httpx.HTTPStatusError as exc:
            error_detail = _extract_error(exc)
            result = DeployResult(
                agent_name=name,
                agent_id="",
                deployment_id=None,
                action="error",
                error=error_detail,
            )
            results.append(result)
            if not json_output:
                console.print(f"\r  [red]✗[/red] {name} — {error_detail}")

    # ── 7. Wait for deployments to come online ───────────────────────────
    if wait and not json_output:
        _wait_for_results(client, results)

    # ── 8. Print summary ─────────────────────────────────────────────────
    if json_output:
        print(json.dumps([_result_to_dict(r) for r in results]))
    else:
        _print_summary(results)

    # Exit non-zero if any failed
    if any(r.error for r in results):
        raise SystemExit(1)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fail(json_output: bool, message: str) -> None:
    if json_output:
        print(json.dumps({"error": message}))
    else:
        console.print(f"\n  [red]✗[/red] {message}\n")
    raise SystemExit(1)


def _extract_error(exc: httpx.HTTPStatusError) -> str:
    try:
        body = exc.response.json()
        detail = body.get("detail", str(exc))
    except Exception:
        detail = f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"

    # Friendly hints for common errors
    if exc.response.status_code == 403:
        if "role" in detail.lower() or "admin" in detail.lower() or "owner" in detail.lower():
            detail += (
                "\n        Your API token has read-only permissions. "
                "Generate a new token with 'admin' scope at Settings → API Keys."
            )
    elif exc.response.status_code == 409:
        if "fly app" in detail.lower():
            detail += "\n        Contact support or check your organization settings."

    return detail


def _result_to_dict(r) -> dict:
    return {
        "agent": r.agent_name,
        "agent_id": r.agent_id,
        "deployment_id": r.deployment_id,
        "action": r.action,
        "status": r.status,
        "url": r.url,
        "dashboard": r.dashboard_url,
        "region": r.region,
        "cpus": r.cpus,
        "memory_mb": r.memory_mb,
        "error": r.error,
    }


def _print_dry_run(agents, client, region, cpus, memory_mb) -> None:
    console.print()
    console.print("  [bold]Deploy Preview[/bold] [dim](dry run — nothing will be deployed)[/dim]")
    console.print()

    table = Table(box=None, padding=(0, 2), show_header=True)
    table.add_column("Agent", style="bold")
    table.add_column("Action")
    table.add_column("Region")
    table.add_column("CPUs")
    table.add_column("RAM")

    for name in agents:
        existing = client.find_agent_by_name(name)
        action = "[yellow]update[/yellow]" if existing else "[green]create[/green]"
        table.add_row(
            name, action,
            region or "auto", str(cpus), f"{memory_mb}MB",
        )

    console.print(table)
    console.print()
    console.print("  [dim]Run without --dry-run to deploy.[/dim]")
    console.print()


def _wait_for_results(client, results) -> None:
    """Poll deployments until they're running or failed."""
    pending = [r for r in results if r.deployment_id and r.action == "created"]
    if not pending:
        return

    console.print(f"  [bold]⠋[/bold] Waiting for {len(pending)} deployment(s) to come online…")

    for r in pending:
        try:
            final_status = client.wait_for_deployment(r.deployment_id, timeout=120)
            r.status = final_status
            if final_status == "running":
                console.print(f"  [green]✓[/green] {r.agent_name} is live!")
            elif final_status == "error":
                dep = client.get_deployment(r.deployment_id)
                r.error = dep.get("last_error", "Unknown error")
                console.print(f"  [red]✗[/red] {r.agent_name} failed: {r.error}")
            elif final_status == "timeout":
                console.print(
                    f"  [yellow]![/yellow] {r.agent_name} still starting — "
                    f"check dashboard: {r.dashboard_url}"
                )
        except Exception as exc:
            console.print(f"  [yellow]![/yellow] {r.agent_name} — could not check status: {exc}")


def _print_summary(results) -> None:
    """Print the final deploy summary with URLs."""
    console.print()

    for r in results:
        if r.error:
            continue

        lines = [f"  [bold]{r.agent_name}[/bold]"]
        if r.url:
            lines.append(f"  URL:       [bold]{r.url}[/bold]")
        if r.dashboard_url:
            lines.append(f"  Dashboard: [dim]{r.dashboard_url}[/dim]")
        status_color = "green" if r.status == "running" else "yellow"
        resource_info = f"{r.memory_mb} MB, {r.cpus} CPU"
        lines.append(f"  Status:    [{status_color}]{r.status}[/{status_color}] ({resource_info})")
        lines.append(f"  Action:    {r.action}")

        console.print(Panel(
            "\n".join(lines),
            border_style="green" if not r.error else "red",
        ))

    # Quick summary line
    ok = sum(1 for r in results if not r.error)
    fail = sum(1 for r in results if r.error)
    if fail:
        console.print(f"  [green]{ok} deployed[/green], [red]{fail} failed[/red]\n")
    else:
        console.print(f"  [green]✓ {ok} agent(s) deployed successfully[/green]\n")
