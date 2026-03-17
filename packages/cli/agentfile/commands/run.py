"""agentfile run — run the agent container locally."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path

import click
import httpx
from rich.console import Console

from agentfile.core.models import AgentFile, AgentDef
from agentfile.core.docker import build_image, run_container

console = Console()


def _image_exists(image_ref: str) -> bool:
    """Return True if *image_ref* is present in the local Docker image store."""
    import docker
    from docker.errors import ImageNotFound, DockerException
    try:
        docker.from_env().images.get(image_ref)
        return True
    except ImageNotFound:
        return False
    except DockerException:
        # Docker not reachable — let run_container() surface the real error.
        return True


def _auto_build(agent: AgentDef, af: AgentFile, agentfile_path: str, tag: str) -> None:
    """Validate + render templates + docker build for *agent*."""
    from agentfile.commands.build import _render_templates

    errors = af.validate()
    if errors:
        console.print("[red]Cannot auto-build — agentfile validation failed:[/red]")
        for e in errors:
            console.print(f"    • {e}")
        raise SystemExit(1)

    with tempfile.TemporaryDirectory(prefix=f"agentfile-build-{agent.name}-") as tmp:
        ctx = Path(tmp)
        shutil.copy(agentfile_path, ctx / "agentfile.yaml")
        _render_templates(agent, af, ctx)
        build_image(ctx, agent.image_name(), tag)
    console.print()

def _load_dotenv_key(key: str) -> str | None:
    """Try to read a key from a .env file in the current directory."""
    env_file = Path(".env")
    if not env_file.exists():
        return None
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


_KEY_ENV_VARS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai":    "OPENAI_API_KEY",
    "google":    "GEMINI_API_KEY",
    "mistral":   "MISTRAL_API_KEY",
    "groq":      "GROQ_API_KEY",
}


def _docker_url(url: str) -> str:
    """Rewrite localhost/127.0.0.1 → host.docker.internal so containers can reach host services."""
    return (
        url
        .replace("localhost", "host.docker.internal")
        .replace("127.0.0.1", "host.docker.internal")
    )


_LOCAL_API_URL = "http://localhost:8000"
_SECRET_FILE = Path.home() / ".agentfile" / ".api-secret"


def _is_local_api_running() -> bool:
    """Return True if the local Ninetrix API is reachable on localhost:8000."""
    try:
        r = httpx.get(f"{_LOCAL_API_URL}/health", timeout=1.0)
        return r.status_code < 500
    except Exception:
        return False


def _is_gateway_running() -> bool:
    """Return True if the local MCP Gateway is reachable on localhost:8080."""
    try:
        r = httpx.get("http://localhost:8080/health", timeout=1.0)
        return r.status_code < 500
    except Exception:
        return False


def _read_machine_secret() -> str | None:
    """Return the machine secret written by the local API on startup."""
    if _SECRET_FILE.exists():
        return _SECRET_FILE.read_text().strip() or None
    return None


def _inject_integration_credentials(env: dict[str, str]) -> None:
    """Inject non-MCP integration credentials into the agent container env.

    MCP tool credentials (TAVILY_API_KEY, GITHUB_TOKEN, etc.) are handled by the
    mcp-worker JIT on first tool call — the agent container never needs them.
    This function only fetches direct-API credentials (LLM keys, Composio, etc.)
    from the local API if it is running.
    """
    from agentfile.core.auth import auth_headers
    from agentfile.core.config import resolve_api_url
    api_url = resolve_api_url()
    if not api_url:
        return
    try:
        resp = httpx.get(
            f"{api_url}/integrations/credentials",
            headers=auth_headers(api_url),
            timeout=3,
        )
        if resp.status_code == 200:
            for _integration_id, creds in resp.json().items():
                for key, value in creds.items():
                    env.setdefault(key, value)
    except Exception:
        pass


@click.command("run")
@click.option("--file", "-f", "agentfile_path", default="agentfile.yaml",
              show_default=True, help="Path to agentfile.yaml")
@click.option("--image", default=None, help="Override image name:tag")
@click.option("--tag", "-t", default="latest", show_default=True, help="Image tag to run")
@click.option("--env", "-e", "extra_env", multiple=True, metavar="KEY=VALUE",
              help="Extra environment variables (repeatable)")
@click.option("--thread-id", default=None,
              help="Thread ID for resuming a prior run (auto-generated UUID if omitted)")
@click.option("--environment", "environment", default=None, metavar="NAME",
              help="Apply environment overlay from agentfile.yaml (e.g. dev, prod)")
def run_cmd(agentfile_path: str, image: str | None, tag: str, extra_env: tuple[str, ...],
            thread_id: str | None, environment: str | None) -> None:
    """Run the agent Docker image locally."""
    console.print()
    console.print("[bold purple]ninetrix run[/bold purple]\n")

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
        console.print(f"  [dim]Environment:[/dim] [bold]{environment}[/bold]\n")

    # For multi-agent files, only run the entry agent interactively
    agent = af.entry_agent
    if af.is_multi_agent:
        console.print(
            f"  [yellow]Multi-agent file detected.[/yellow] "
            f"Running entry agent [bold]{agent.name}[/bold] only."
        )
        console.print(
            "  [dim]Use 'ninetrix up' to start the full warm pool.[/dim]\n"
        )

    image_ref = image or agent.image_name(tag)

    # Auto-build if the derived image doesn't exist yet (skip when --image was
    # explicitly given, since the user is pointing at an image we didn't build).
    if image is None and not _image_exists(image_ref):
        console.print(
            f"  [yellow]Image [bold]{image_ref}[/bold] not found locally.[/yellow]\n"
            f"  Auto-building from [bold]{agentfile_path}[/bold] …\n"
        )
        _auto_build(agent, af, agentfile_path, tag)

    # Resolve effective governance for the entry agent
    eff_governance  = af.effective_governance(agent)

    # Always sync runtime config from the live agentfile, overriding baked-in Docker ENV.
    env: dict[str, str] = {
        "AGENTFILE_PROVIDER":       agent.provider,
        "AGENTFILE_MODEL":          agent.model,
        "AGENTFILE_TEMPERATURE":    str(agent.temperature),
        "AGENTFILE_SYSTEM_PROMPT":  agent.system_prompt,
    }

    key_var = _KEY_ENV_VARS.get(agent.provider)
    if key_var:
        value = os.environ.get(key_var) or _load_dotenv_key(key_var)
        if value:
            env[key_var] = value
        else:
            console.print(f"  [yellow]{key_var}[/yellow] is not set.\n")
            console.print("  You can set it permanently by adding this to your shell profile:\n")
            console.print(f"    [dim]export {key_var}=your-key-here[/dim]\n")
            value = click.prompt(f"  Enter {key_var} for this session", hide_input=True).strip()
            if not value:
                console.print("[red]No API key provided. Aborting.[/red]")
                raise SystemExit(1)
            env[key_var] = value

    # Forward verifier API key if it uses a different provider than the main agent
    eff_execution = af.effective_execution(agent)
    verifier_provider = eff_execution.verifier.provider or agent.provider
    verifier_key_var = _KEY_ENV_VARS.get(verifier_provider)
    if eff_execution.verify_steps and verifier_key_var and verifier_key_var != key_var:
        val = os.environ.get(verifier_key_var) or _load_dotenv_key(verifier_key_var)
        if val:
            env[verifier_key_var] = val
        else:
            console.print(
                f"  [yellow]Warning:[/yellow] {verifier_key_var} not set — "
                f"verifier (provider: {verifier_provider}) may fail at runtime."
            )

    if any(t.is_composio() for t in agent.tools):
        for var in ("COMPOSIO_API_KEY", "COMPOSIO_ENTITY_ID"):
            val = os.environ.get(var) or _load_dotenv_key(var)
            if val:
                env[var] = val
            elif var == "COMPOSIO_API_KEY":
                console.print("  [yellow]Warning:[/yellow] COMPOSIO_API_KEY is not set — "
                              "Composio tools will fail at runtime.")

    use_durability = eff_execution.durability
    if use_durability:
        env["AGENTFILE_THREAD_ID"] = thread_id or uuid.uuid4().hex
        console.print(f"  [dim]Thread ID:[/dim] {env['AGENTFILE_THREAD_ID']}")

    notify_url = eff_governance.human_approval.notify_url
    if notify_url:
        m = re.search(r'\$\{([^}]+)\}', notify_url)
        if m:
            var_name = m.group(1)
            val = os.environ.get(var_name) or _load_dotenv_key(var_name) or ""
            if val:
                env["AGENTFILE_APPROVAL_NOTIFY_URL"] = notify_url.replace(f"${{{var_name}}}", val)
            else:
                console.print(f"  [yellow]Warning:[/yellow] env var [bold]{var_name}[/bold] "
                              f"(referenced in human_approval.notify_url) is not set.")

    # Forward SaaS API credentials so the agent can phone home with thread events.
    # Resolution order for AGENTFILE_API_URL:
    #   env var → .env file → ~/.agentfile/config.json → auto-detect local
    # Resolution order for AGENTFILE_RUNNER_TOKEN:
    #   machine secret (when local API running — always authoritative for local dev)
    #   → env var / .env file → auth.json token
    # NOTE: machine secret takes priority over env/.env because env/.env often holds a
    # stale SaaS token that the local API won't accept. The machine secret is the only
    # token guaranteed to work with `ninetrix dev`.
    from agentfile.core.config import get_api_url as _get_api_url
    _api_url = (
        os.environ.get("AGENTFILE_API_URL")
        or _load_dotenv_key("AGENTFILE_API_URL")
        or _get_api_url()
    )
    if not _api_url and _is_local_api_running():
        _api_url = "http://localhost:8000"

    _token_source: str | None = None
    if _api_url:
        env["AGENTFILE_API_URL"] = _docker_url(_api_url)

        # 1. Machine secret — only valid for the local API (port 8000), not SaaS
        _is_local_target = _api_url and any(
            h in _api_url for h in ("localhost:8000", "127.0.0.1:8000", "host.docker.internal:8000")
        )
        if _is_local_target and _is_local_api_running():
            _secret = _read_machine_secret()
            if _secret:
                env["AGENTFILE_RUNNER_TOKEN"] = _secret
                _token_source = "machine secret"

        # 2. Explicit env var / .env file — used when not local dev (e.g. remote SaaS)
        if not env.get("AGENTFILE_RUNNER_TOKEN"):
            _runner_token = os.environ.get("AGENTFILE_RUNNER_TOKEN") or _load_dotenv_key("AGENTFILE_RUNNER_TOKEN")
            if _runner_token:
                env["AGENTFILE_RUNNER_TOKEN"] = _runner_token
                _token_source = "env / .env"

        # 3. Token saved by `ninetrix auth login` (remote SaaS fallback)
        if not env.get("AGENTFILE_RUNNER_TOKEN"):
            from agentfile.core.auth import read_token as _read_token
            _stored_token = _read_token(_api_url)
            if _stored_token:
                env["AGENTFILE_RUNNER_TOKEN"] = _stored_token
                _token_source = "auth.json"

        if env.get("AGENTFILE_RUNNER_TOKEN"):
            console.print(
                f"  [dim]Telemetry → {_api_url}  "
                f"[green]✓[/green] token: {_token_source}[/dim]\n"
            )
        else:
            console.print(
                f"  [yellow]⚠[/yellow]  Telemetry → {_api_url}  "
                f"[yellow]no token[/yellow] — events will not be sent.\n"
                "  [dim]Run [bold]ninetrix dev[/bold] or "
                "[bold]ninetrix auth login --token <token>[/bold] to fix.[/dim]\n"
            )

    # MCP Gateway — always forward gateway vars so `ninetrix run --image` works
    # from any directory, even if the local agentfile.yaml doesn't have mcp_gateway:.
    # Auto-detect: if `ninetrix dev` is running, wire up the gateway automatically.
    _gw_url_src = (
        os.environ.get("MCP_GATEWAY_URL")
        or _load_dotenv_key("MCP_GATEWAY_URL")
        or (af.mcp_gateway.url if af.mcp_gateway else None)
    )
    _gw_running = _is_gateway_running()
    if _gw_url_src:
        env["MCP_GATEWAY_URL"] = _docker_url(_gw_url_src)
    elif _gw_running:
        env.setdefault("MCP_GATEWAY_URL", "http://host.docker.internal:8080")

    _gw_token_src = (
        os.environ.get("MCP_GATEWAY_TOKEN")
        or _load_dotenv_key("MCP_GATEWAY_TOKEN")
        or (af.mcp_gateway.token if af.mcp_gateway else None)
    )
    if _gw_token_src:
        env["MCP_GATEWAY_TOKEN"] = _gw_token_src

    _gw_ws_src = (
        os.environ.get("MCP_GATEWAY_WORKSPACE")
        or _load_dotenv_key("MCP_GATEWAY_WORKSPACE")
        or (af.mcp_gateway.workspace_id if af.mcp_gateway else None)
    )
    if _gw_ws_src:
        env["MCP_GATEWAY_WORKSPACE"] = _gw_ws_src
    elif _gw_running:
        # Dev stack always resolves REQUIRE_AUTH=false tokens to workspace "default"
        env.setdefault("MCP_GATEWAY_WORKSPACE", "default")

    for pair in extra_env:
        if "=" in pair:
            k, v = pair.split("=", 1)
            env[k] = v
        else:
            console.print(f"  [yellow]Skipping malformed env var:[/yellow] {pair!r}")

    # Effective triggers for the entry agent
    eff_triggers = af.effective_triggers(agent)
    webhook_triggers = [t for t in eff_triggers if t.type == "webhook"]
    port_bindings: list[str] = []
    interactive = True
    if webhook_triggers:
        ports = sorted({t.port for t in webhook_triggers})
        for port in ports:
            port_bindings.append(f"{port}:{port}")
        env["AGENTFILE_WEBHOOK_PORT"] = str(ports[0])
        interactive = False
        console.print(f"  [dim]Trigger mode:[/dim] webhook server on port(s) {ports}\n")

    # Resolve volumes for the entry agent
    volume_defs = af.effective_volumes(agent)
    local_volumes = [v for v in volume_defs if v.provider == "local"]
    s3_volumes = [v for v in volume_defs if v.provider == "s3"]

    # Inject S3 volume env vars (bucket/prefix/path resolved from host env)
    for v in s3_volumes:
        key = v.name.upper().replace("-", "_")
        bucket_val = os.path.expandvars(v.bucket or "")
        prefix_val = os.path.expandvars(v.prefix or "")
        env[f"AGENTFILE_VOL_{key}_BUCKET"] = bucket_val
        env[f"AGENTFILE_VOL_{key}_PREFIX"] = prefix_val
        env[f"AGENTFILE_VOL_{key}_PATH"] = v.container_path
        # Forward AWS credentials if present on host
        for aws_var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION"):
            val = os.environ.get(aws_var) or _load_dotenv_key(aws_var)
            if val:
                env[aws_var] = val

    # Inject credentials from Integration Hub (silent no-op if API not running)
    _inject_integration_credentials(env)

    # Forward any AGENTFILE_* runtime overrides from the host env (don't overwrite
    # values already set above — e.g. AGENTFILE_PROVIDER always comes from the yaml).
    for _k, _v in os.environ.items():
        if _k.startswith("AGENTFILE_"):
            env.setdefault(_k, _v)

    res = agent.resources
    run_container(
        image_ref,
        env,
        port_bindings=port_bindings,
        interactive=interactive,
        cpu=res.cpu,
        memory=res.memory,
        warm_pool=res.warm_pool,
        volumes=local_volumes,
        restart_policy="on-failure:3" if use_durability else None,
    )
    console.print()
