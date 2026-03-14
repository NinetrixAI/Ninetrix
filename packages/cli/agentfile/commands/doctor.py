"""ninetrix doctor — check that all required dependencies and config are healthy."""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

console = Console()

_STATE_DIR  = Path.home() / ".agentfile" / "pools"
_API_HOST   = "localhost"
_API_PORT   = 8000

_KEY_ENV_VARS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai":    "OPENAI_API_KEY",
    "google":    "GEMINI_API_KEY",
    "mistral":   "MISTRAL_API_KEY",
    "groq":      "GROQ_API_KEY",
}


def _ok(msg: str) -> tuple[str, str, str]:
    return ("✓", "green", msg)

def _warn(msg: str) -> tuple[str, str, str]:
    return ("!", "yellow", msg)

def _fail(msg: str) -> tuple[str, str, str]:
    return ("✗", "red", msg)


def _check_docker() -> tuple[str, str, str]:
    try:
        import docker
        from docker.errors import DockerException
        client = docker.from_env()
        info = client.info()
        return _ok(f"Docker {info.get('ServerVersion', 'running')}")
    except Exception as exc:
        return _fail(f"Docker not reachable — {exc}")


def _check_api() -> tuple[str, str, str]:
    try:
        sock = socket.create_connection((_API_HOST, _API_PORT), timeout=2)
        sock.close()
        return _ok(f"API server reachable on port {_API_PORT}")
    except OSError:
        return _warn(f"API server not running on port {_API_PORT} (optional)")


def _check_pool() -> list[tuple[str, str, str]]:
    results = []
    if not _STATE_DIR.exists() or not list(_STATE_DIR.glob("*.json")):
        results.append(_warn("No warm pool running (run 'ninetrix up' to start one)"))
        return results

    import docker
    from docker.errors import DockerException

    try:
        client = docker.from_env()
    except Exception:
        results.append(_warn("Cannot check pool containers — Docker unavailable"))
        return results

    for sf in sorted(_STATE_DIR.glob("*.json")):
        state = json.loads(sf.read_text())
        swarm = state.get("swarm", sf.stem)
        agents = state.get("agents", {})
        for name, info in agents.items():
            cid = info.get("container_id", "")
            container_name = f"agentfile-{name}"
            running = False
            for lookup in filter(None, [cid, container_name]):
                try:
                    c = client.containers.get(lookup)
                    if c.status == "running":
                        running = True
                    break
                except DockerException:
                    continue
            if running:
                results.append(_ok(f"Container [{swarm}] {name} — running"))
            else:
                results.append(_fail(f"Container [{swarm}] {name} — stopped or missing"))

    return results


def _check_agentfile(path: str) -> list[tuple[str, str, str]]:
    results = []
    p = Path(path)
    if not p.exists():
        results.append(_warn(f"{path} not found in current directory"))
        return results

    try:
        from agentfile.core.models import AgentFile
        af = AgentFile.from_path(path)
    except Exception as exc:
        results.append(_fail(f"{path} parse error — {exc}"))
        return results

    errors = af.validate()
    if errors:
        for e in errors:
            results.append(_fail(f"agentfile validation: {e}"))
    else:
        results.append(_ok(f"{path} — valid ({len(af.agents)} agent(s))"))

    # Check API key availability per agent
    for name, agent_def in af.agents.items():
        key_var = _KEY_ENV_VARS.get(agent_def.provider)
        if not key_var:
            continue
        val = os.environ.get(key_var) or _load_dotenv_key(key_var)
        if val:
            masked = val[:4] + "…" + val[-4:] if len(val) > 8 else "***"
            results.append(_ok(f"{name}: {key_var} set ({masked})"))
        else:
            results.append(_fail(f"{name}: {key_var} not set (provider: {agent_def.provider})"))

    # Check resources per agent (non-default values only)
    for name, agent_def in af.agents.items():
        res = agent_def.resources
        parts: list[str] = []
        if res.base_image:
            parts.append(f"base_image={res.base_image}")
        if res.cpu is not None:
            parts.append(f"cpu={res.cpu}")
        if res.memory:
            parts.append(f"memory={res.memory}")
        if res.warm_pool:
            parts.append("warm_pool=true")
        if parts:
            results.append(_ok(f"{name}: resources — {', '.join(parts)}"))

    # Check global volumes (host_path existence for local; AWS creds for S3)
    has_s3 = False
    for vol_name, vol in af.volumes.items():
        if vol.provider == "local":
            if vol.host_path:
                resolved = os.path.expandvars(vol.host_path)
                if Path(resolved).exists():
                    results.append(_ok(f"volume '{vol_name}': {vol.host_path} → {vol.container_path}"))
                else:
                    results.append(_warn(
                        f"volume '{vol_name}': host_path '{vol.host_path}' does not exist "
                        "(will be created by Docker or fail at runtime)"
                    ))
            else:
                results.append(_warn(f"volume '{vol_name}' (local): missing host_path"))
        elif vol.provider == "s3":
            has_s3 = True
            bucket = os.path.expandvars(vol.bucket or "")
            if bucket and not bucket.startswith("$"):
                results.append(_ok(f"volume '{vol_name}' (s3): bucket={bucket}, sync={vol.sync}"))
            else:
                results.append(_warn(
                    f"volume '{vol_name}' (s3): bucket '{vol.bucket}' not resolved "
                    "(check env var before running)"
                ))

    # Check inline per-agent volumes (not string refs to globals)
    from agentfile.core.models import VolumeSpec
    for name, agent_def in af.agents.items():
        for ref in agent_def.volume_refs:
            if not isinstance(ref, VolumeSpec):
                continue  # string ref already covered by global volumes check
            if ref.provider == "local":
                if ref.host_path:
                    resolved = os.path.expandvars(ref.host_path)
                    if Path(resolved).exists():
                        results.append(_ok(f"{name}/volume '{ref.name}': {ref.host_path} → {ref.container_path}"))
                    else:
                        results.append(_warn(
                            f"{name}/volume '{ref.name}': host_path '{ref.host_path}' does not exist"
                        ))
            elif ref.provider == "s3":
                has_s3 = True

    # AWS credentials check (once, if any S3 volume is declared)
    if has_s3:
        for aws_var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
            val = os.environ.get(aws_var) or _load_dotenv_key(aws_var)
            if val:
                results.append(_ok(f"{aws_var} set"))
            else:
                results.append(_warn(f"{aws_var} not set — required for S3 volume sync"))

    return results


def _load_dotenv_key(key: str) -> str | None:
    env_file = Path(".env")
    if not env_file.exists():
        return None
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _check_database() -> tuple[str, str, str]:
    url = os.environ.get("DATABASE_URL") or _load_dotenv_key("DATABASE_URL")
    if not url:
        return _warn("DATABASE_URL not set (persistence and API disabled)")
    # Mask credentials in display
    try:
        from urllib.parse import urlparse
        p = urlparse(url)
        display = f"{p.scheme}://…@{p.hostname}{p.path}"
    except Exception:
        display = url[:30] + "…"
    return _ok(f"DATABASE_URL set → {display}")


@click.command("doctor")
@click.option("--file", "-f", "agentfile_path", default="agentfile.yaml",
              show_default=True, help="Path to agentfile.yaml to validate")
def doctor_cmd(agentfile_path: str) -> None:
    """Check Docker, API, pool containers, agentfile, and env vars."""
    console.print()
    console.print("[bold purple]ninetrix doctor[/bold purple]\n")

    checks: list[tuple[str, str, str, str]] = []  # (icon, color, category, message)

    def add(category: str, result: tuple[str, str, str]) -> None:
        icon, color, msg = result
        checks.append((icon, color, category, msg))

    def add_many(category: str, results: list[tuple[str, str, str]]) -> None:
        for r in results:
            add(category, r)

    add("Docker",    _check_docker())
    add("API",       _check_api())
    add("Database",  _check_database())
    add_many("Pool",      _check_pool())
    add_many("Agentfile", _check_agentfile(agentfile_path))

    # Render results
    table = Table(box=None, padding=(0, 1), show_header=False)
    table.add_column("icon",     style="bold", no_wrap=True)
    table.add_column("category", style="dim",  no_wrap=True)
    table.add_column("message")

    any_fail = False
    for icon, color, category, msg in checks:
        table.add_row(
            f"[{color}]{icon}[/{color}]",
            f"[dim]{category}[/dim]",
            f"[{color}]{msg}[/{color}]",
        )
        if color == "red":
            any_fail = True

    console.print(table)
    console.print()

    if any_fail:
        console.print("  [red]Some checks failed.[/red] Fix the issues above and re-run.\n")
        raise SystemExit(1)
    else:
        console.print("  [green]All checks passed.[/green]\n")
