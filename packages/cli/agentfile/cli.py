"""Ninetrix CLI entry point."""

from __future__ import annotations

import os
import sys

import click
from rich.console import Console

from agentfile import __version__
from agentfile.commands.init import init_cmd
from agentfile.commands.build import build_cmd
from agentfile.commands.run import run_cmd
from agentfile.commands.deploy import deploy_cmd
from agentfile.commands.mcp import mcp_cmd
from agentfile.commands.up import up_cmd
from agentfile.commands.down import down_cmd
from agentfile.commands.status import status_cmd
from agentfile.commands.logs import logs_cmd
from agentfile.commands.invoke import invoke_cmd
from agentfile.commands.trace import trace_cmd
from agentfile.commands.restart import restart_cmd
from agentfile.commands.rollback import rollback_cmd
from agentfile.commands.doctor import doctor_cmd
from agentfile.commands.validate import validate_cmd
from agentfile.commands.auth import auth_cmd
from agentfile.commands.config import config_cmd
from agentfile.commands.compose import compose_cmd
from agentfile.commands.gateway import gateway_cmd
from agentfile.commands.dev import dev_command
from agentfile.commands.env import env_cmd
from agentfile.commands.ls import ls_cmd
from agentfile.commands.connect import connect_cmd, disconnect_cmd, connections_cmd
from agentfile.commands.migrate import migrate_cmd
from agentfile.commands.schema import schema_cmd
from agentfile.commands.channel import channel_cmd
from agentfile.commands.tools import tools_cmd
from agentfile.commands.hub import hub_cmd
from agentfile.commands.completion import completion_cmd

console = Console()

LOGO = "[bold purple] Ninetrix [/bold purple]"

# Commands that need Docker — pre-flight check runs before any work.
# Commands that need Docker — pre-flight check runs before any work.
# Note: "deploy" is NOT here — it uploads YAML to the cloud, no local Docker needed.
_DOCKER_COMMANDS = frozenset({
    "build", "run", "up", "down", "status", "logs",
    "invoke", "restart", "rollback", "compose", "env", "dev",
    "gateway",
})


@click.group()
@click.version_option(f"v{__version__}", "--version", "-V", prog_name="Ninetrix")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Ninetrix — build and deploy AI agents as containers.

    \b
    Quick start:
      ninetrix init          scaffold agentfile.yaml
      ninetrix build         build Docker image(s)
      ninetrix run           run entry agent locally
      ninetrix deploy        push & deploy to a registry

    \b
    MCP tools (after `ninetrix dev`):
      ninetrix mcp status    what's running in the gateway right now
      ninetrix mcp add       add a tool server (e.g. github, slack, notion)
      ninetrix mcp remove    remove a tool server
      ninetrix mcp test      test tools via the live gateway
      ninetrix mcp list      cross-ref gateway tools vs agentfile.yaml
      ninetrix mcp catalog   browse available servers

    \b
    Multi-agent warm pool:
      ninetrix up            start all agents on a Docker network
      ninetrix status        show agent container status
      ninetrix invoke        send a message to a running agent
      ninetrix logs          stream agent container logs
      ninetrix trace         visualize a multi-agent run
      ninetrix restart       rebuild and restart one agent
      ninetrix rollback      switch one agent to a previous image tag
      ninetrix env           set or list env vars in running containers
      ninetrix down          stop the warm pool

    \b
    Compose deployment:
      ninetrix compose       generate docker-compose.yml for any cloud

    \b
    MCP Gateway:
      ninetrix gateway start   start local gateway + worker stack
      ninetrix gateway status  show connected workers and tools
      ninetrix gateway stop    tear down the gateway stack

    \b
    Local environment:
      ninetrix dev           start local server (API + MCP gateway + dashboard)

    \b
    Channels:
      ninetrix channel connect telegram   connect a Telegram bot interactively
      ninetrix channel disconnect telegram remove Telegram connection
      ninetrix channel status              show connected channels

    \b
    Integrations (Ninetrix Cloud):
      ninetrix connections   list available integrations and connected status
      ninetrix connect       authorize an integration via OAuth (e.g. github, slack)
      ninetrix disconnect    revoke an integration and remove vault credentials

    \b
    Utilities:
      ninetrix ls            list agents, tools, and triggers from agentfile.yaml
      ninetrix validate      lint agentfile.yaml without building
      ninetrix doctor        check Docker, API, pool, and env vars
      ninetrix auth          manage API authentication
      ninetrix config        view and set persistent CLI configuration
      ninetrix completion    set up shell tab-completion (bash, zsh, fish)
    """
    # Pre-flight Docker check for commands that need it.
    # Runs before any YAML parsing or template work — gives immediate feedback.
    invoked = ctx.invoked_subcommand
    if invoked in _DOCKER_COMMANDS:
        from agentfile.core.docker import require_docker
        require_docker()

    # Track command usage (anonymous, opt-out)
    if invoked:
        try:
            from agentfile.core.telemetry import track
            track(f"cli_{invoked}", {"command": invoked})
        except Exception:
            pass

    # Flush telemetry on exit
    import atexit
    try:
        from agentfile.core.telemetry import shutdown
        atexit.register(shutdown)
    except Exception:
        pass


# Register sub-commands
cli.add_command(init_cmd,   name="init")
cli.add_command(build_cmd,  name="build")
cli.add_command(run_cmd,    name="run")
cli.add_command(deploy_cmd, name="deploy")
cli.add_command(mcp_cmd,    name="mcp")
cli.add_command(up_cmd,     name="up")
cli.add_command(down_cmd,   name="down")
cli.add_command(status_cmd, name="status")
cli.add_command(logs_cmd,   name="logs")
cli.add_command(invoke_cmd,   name="invoke")
cli.add_command(trace_cmd,   name="trace")
cli.add_command(restart_cmd,  name="restart")
cli.add_command(rollback_cmd, name="rollback")
cli.add_command(doctor_cmd,   name="doctor")
cli.add_command(validate_cmd, name="validate")
cli.add_command(auth_cmd,     name="auth")
cli.add_command(config_cmd,   name="config")
cli.add_command(compose_cmd,  name="compose")
cli.add_command(gateway_cmd,  name="gateway")
cli.add_command(dev_command,   name="dev")
cli.add_command(env_cmd,       name="env")
cli.add_command(ls_cmd,        name="ls")
cli.add_command(connections_cmd, name="connections")
cli.add_command(connect_cmd,     name="connect")
cli.add_command(disconnect_cmd,  name="disconnect")
cli.add_command(migrate_cmd,     name="migrate")
cli.add_command(schema_cmd,      name="schema")
cli.add_command(channel_cmd,     name="channel")
cli.add_command(tools_cmd,       name="tools")
cli.add_command(hub_cmd,         name="hub")
cli.add_command(completion_cmd,  name="completion")

from agentfile.commands.telemetry_cmd import telemetry_cmd
cli.add_command(telemetry_cmd, name="telemetry")


def main() -> None:
    debug = os.environ.get("NINETRIX_DEBUG") == "1"
    try:
        cli(standalone_mode=False)
    except click.exceptions.Exit as exc:
        sys.exit(exc.exit_code)
    except click.exceptions.Abort:
        console.print("\n[yellow]Aborted.[/yellow]")
        sys.exit(1)
    except SystemExit:
        raise
    except Exception as exc:
        if debug:
            raise
        # Friendly last-resort handler — individual commands should catch their
        # own exceptions and give better messages, but this prevents raw tracebacks.
        from docker.errors import DockerException
        from agentfile.core.errors import fmt_docker_error
        from rich.panel import Panel
        if isinstance(exc, DockerException):
            msg, hint = fmt_docker_error(exc)
            console.print()
            console.print(Panel(
                f"[red bold]{msg}[/red bold]"
                + (f"\n\n[dim]Hint: {hint}[/dim]" if hint else ""),
                title="[red]Docker error[/red]",
                border_style="red",
            ))
        else:
            console.print()
            console.print(Panel(
                f"[red bold]{exc}[/red bold]\n\n"
                "[dim]Set NINETRIX_DEBUG=1 for the full traceback.[/dim]",
                title="[red]Error[/red]",
                border_style="red",
            ))
        sys.exit(1)


if __name__ == "__main__":
    main()
