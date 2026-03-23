"""ninetrix invoke — POST a message to a running warm pool agent."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

import click
import requests
from rich.console import Console

from agentfile.core.models import AgentFile

console = Console()

_STATE_DIR = Path.home() / ".agentfile" / "pools"
INVOKE_PORT = 9000


def _find_agent_url(agent_name: str, af: AgentFile | None) -> str | None:
    """Look up the host:port for a named agent from pool state files."""
    if not _STATE_DIR.exists():
        return None
    for state_file in _STATE_DIR.glob("*.json"):
        try:
            state = json.loads(state_file.read_text())
            for name, info in state.get("agents", {}).items():
                if name == agent_name:
                    port = info.get("host_port", INVOKE_PORT)
                    return f"http://localhost:{port}"
        except Exception:
            pass
    return None


@click.command("invoke")
@click.option("--file", "-f", "agentfile_path", default=None,
              help="Path to agentfile.yaml (to identify the swarm)")
@click.option("--agent", "agent_name", default=None,
              help="Agent key to invoke (defaults to entry agent)")
@click.option("--message", "-m", default=None,
              help="Message / task to send to the agent")
@click.option("--thread-id", default=None,
              help="Thread ID for session continuity (auto-generated if omitted)")
@click.option("--timeout", default=300, show_default=True,
              help="Request timeout in seconds")
@click.option("--wait", default=60, show_default=True,
              help="Seconds to wait for the agent to become ready before giving up")
@click.option("--chat", is_flag=True, default=False,
              help="Interactive chat mode — keep sending messages in a loop")
def invoke_cmd(
    agentfile_path: str | None,
    agent_name: str | None,
    message: str | None,
    thread_id: str | None,
    timeout: int,
    wait: int,
    chat: bool,
) -> None:
    """Send a message to a running warm pool agent and print the result."""
    if not chat and not message:
        console.print("[red]Error:[/red] Either --message or --chat is required.")
        raise SystemExit(1)

    console.print()
    console.print("[bold purple]ninetrix invoke[/bold purple]\n")

    af = None
    if agentfile_path:
        try:
            af = AgentFile.from_path(agentfile_path)
        except (FileNotFoundError, ValueError) as exc:
            console.print(f"[yellow]Warning:[/yellow] Could not parse agentfile: {exc}")

    # Determine target agent
    if agent_name is None:
        if af is not None:
            agent_name = af.entry_agent.name
        else:
            # Try to find any running pool and use its entry agent
            if _STATE_DIR.exists():
                for state_file in _STATE_DIR.glob("*.json"):
                    try:
                        state = json.loads(state_file.read_text())
                        agents = list(state.get("agents", {}).keys())
                        if agents:
                            agent_name = agents[0]
                            break
                    except Exception:
                        pass
            if agent_name is None:
                console.print("[red]Could not determine target agent. Use --agent or --file.[/red]")
                raise SystemExit(1)

    # Find the agent's URL
    url = _find_agent_url(agent_name, af)
    if url is None:
        console.print(
            f"[red]Agent '{agent_name}' is not running.[/red] "
            "Use [bold]ninetrix up[/bold] to start the warm pool."
        )
        raise SystemExit(1)

    tid = thread_id or uuid.uuid4().hex

    # ── Chat mode ─────────────────────────────────────────────────────────────
    if chat:
        console.print(f"  Chatting with [bold]{agent_name}[/bold] at {url}/invoke")
        console.print(f"  Thread ID: [dim]{tid}[/dim]")
        console.print("  [dim]Type 'exit' or Ctrl+C to quit.[/dim]\n")
        while True:
            try:
                user_input = console.input("[bold green]You:[/bold green] ")
            except (EOFError, KeyboardInterrupt):
                console.print("\n  [dim]Goodbye.[/dim]")
                break
            user_input = user_input.strip()
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit"):
                console.print("  [dim]Goodbye.[/dim]")
                break
            data = _send_message(url, user_input, tid, timeout, wait, agent_name)
            if data is None:
                break
            _print_response(data, tid)
        return

    # ── Single message mode ───────────────────────────────────────────────────
    console.print(f"  Invoking [bold]{agent_name}[/bold] at {url}/invoke")
    console.print(f"  Thread ID: [dim]{tid}[/dim]")
    console.print(f"  Message: {message[:100]}\n")

    data = _send_message(url, message, tid, timeout, wait, agent_name)
    if data is None:
        raise SystemExit(1)
    _print_response(data, tid)


def _send_message(
    url: str, message: str, tid: str, timeout: int, wait: int, agent_name: str
) -> dict | None:
    """Send a message to the agent with retry. Returns response dict or None on failure."""
    payload = {"message": message, "thread_id": tid}
    deadline = time.time() + wait
    attempt = 0

    while True:
        attempt += 1
        try:
            resp = requests.post(
                f"{url}/invoke",
                json=payload,
                timeout=(10, timeout),
            )
            if not resp.ok:
                try:
                    body = resp.json()
                    agent_error = body.get("error") or body.get("detail") or resp.text
                except Exception:
                    agent_error = resp.text
                console.print(f"  [red]✗[/red] Agent returned {resp.status_code}: {agent_error}")
                return None
            return resp.json()

        except requests.exceptions.ConnectionError:
            remaining = deadline - time.time()
            if remaining <= 0:
                console.print(
                    f"\n  [red]✗[/red] Could not connect to agent '{agent_name}' at {url} "
                    f"after {wait}s.\n"
                    f"    [dim]Hint: Run 'ninetrix logs --follow' to see what's happening.[/dim]"
                )
                return None
            if attempt == 1:
                console.print(
                    f"  [yellow]⚠[/yellow]  Agent not ready yet — "
                    f"retrying for up to {int(remaining)}s…"
                )
            time.sleep(2)

        except requests.exceptions.Timeout:
            console.print(f"  [red]✗[/red] Request timed out after {timeout}s.")
            return None
        except Exception as exc:
            console.print(f"  [red]✗[/red] {exc}")
            return None


def _print_response(data: dict, tid: str) -> None:
    """Print the agent's response."""
    status = data.get("status", "")
    result_tid = data.get("thread_id", tid)

    if status == "completed":
        console.print(data.get("result", "(no output)"))
        console.print(f"\n  [dim]thread_id: {result_tid}[/dim]\n")

    elif status in ("queued", "accepted", "in_progress"):
        run_id = data.get("run_id", result_tid)
        console.print(f"  [green]✓[/green] Message queued  [dim](run_id: {run_id})[/dim]")
        console.print("  [dim]The agent is processing in the background.[/dim]")
        console.print("  [dim]Follow progress:  ninetrix logs --follow[/dim]\n")

    elif status == "error":
        console.print(f"  [red]✗[/red] Agent error: {data.get('error', 'unknown')}")

    else:
        console.print(data)
