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
from agentfile.commands.compose import compose_cmd
from agentfile.commands.gateway import gateway_cmd
from agentfile.commands.dev import dev_command

console = Console()

LOGO = "[bold purple] Ninetrix [/bold purple]"


@click.group()
@click.version_option(__version__, "--version", "-V")
def cli() -> None:
    """[bold]Ninetrix[/bold] — build and deploy AI agents as containers.

    \b
    Quick start:
      ninetrix init          scaffold agentfile.yaml
      ninetrix build         build Docker image(s)
      ninetrix run           run entry agent locally
      ninetrix deploy        push & deploy to a registry
      ninetrix mcp list      inspect MCP tool integrations

    \b
    Multi-agent warm pool:
      ninetrix up            start all agents on a Docker network
      ninetrix status        show agent container status
      ninetrix invoke        send a message to a running agent
      ninetrix logs          stream agent container logs
      ninetrix trace         visualize a multi-agent run
      ninetrix restart       rebuild and restart one agent
      ninetrix rollback      switch one agent to a previous image tag
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
    Utilities:
      ninetrix validate      lint agentfile.yaml without building
      ninetrix doctor        check Docker, API, pool, and env vars
      ninetrix auth          manage API authentication
    """
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
cli.add_command(compose_cmd,  name="compose")
cli.add_command(gateway_cmd,  name="gateway")
cli.add_command(dev_command,   name="dev")


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
        if isinstance(exc, DockerException):
            msg, hint = fmt_docker_error(exc)
            console.print(f"\n  [red]✗[/red] Docker error: {msg}")
            if hint:
                console.print(f"    [dim]Hint: {hint}[/dim]")
        else:
            console.print(f"\n  [red]✗[/red] {exc}")
        console.print("    [dim]Set NINETRIX_DEBUG=1 for the full traceback.[/dim]")
        sys.exit(1)


if __name__ == "__main__":
    main()
