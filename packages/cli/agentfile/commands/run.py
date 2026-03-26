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


def _load_agentfile_from_image(image_ref: str) -> AgentFile:
    """Extract /app/agentfile.yaml from a Docker image and parse it."""
    import subprocess
    result = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "cat", image_ref, "/app/agentfile.yaml"],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"docker run failed (exit {result.returncode})")
    return AgentFile.from_string(result.stdout)


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
    "anthropic":    "ANTHROPIC_API_KEY",
    "openai":       "OPENAI_API_KEY",
    "google":       "GEMINI_API_KEY",
    "mistral":      "MISTRAL_API_KEY",
    "groq":         "GROQ_API_KEY",
    "deepseek":     "DEEPSEEK_API_KEY",
    "together_ai":  "TOGETHERAI_API_KEY",
    "openrouter":   "OPENROUTER_API_KEY",
    "cerebras":     "CEREBRAS_API_KEY",
    "fireworks_ai": "FIREWORKS_API_KEY",
    "bedrock":      "AWS_ACCESS_KEY_ID",
    "azure":        "AZURE_API_KEY",
    "minimax":      "MINIMAX_API_KEY",
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
    """Return True if the local MCP Gateway is reachable on localhost:9090."""
    try:
        r = httpx.get("http://localhost:9090/health", timeout=1.0)
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
            f"{api_url}/v1/integrations/credentials",
            headers=auth_headers(api_url),
            timeout=3,
        )
        if resp.status_code == 200:
            for _integration_id, creds in resp.json().items():
                for key, value in creds.items():
                    env.setdefault(key, value)
    except Exception:
        pass


def _try_sync_channel_from_api(channel_type: str) -> bool:
    """Check the local API for a verified channel and sync to channels.yaml.

    Returns True if a verified channel was found and synced.
    This enables channels set up via the dashboard to work with `ninetrix run`.
    Uses the internal endpoint that includes bot_token (localhost only).
    """
    from agentfile.core.config import resolve_api_url
    from agentfile.core.channel_config import save_channel

    api_url = resolve_api_url()
    if not api_url:
        return False
    try:
        resp = httpx.get(f"{api_url}/internal/v1/channels/config", timeout=5)
        if resp.status_code != 200:
            return False
        configs = resp.json()
        ch_cfg = configs.get(channel_type)
        if ch_cfg and ch_cfg.get("verified") and ch_cfg.get("bot_token"):
            save_channel(channel_type, ch_cfg)
            console.print(f"  [green]✓[/green] Synced {channel_type} channel from dashboard\n")
            return True
    except Exception:
        pass
    return False


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

    # When --image is explicit and no --file was given, read the agentfile
    # baked into the image instead of requiring a local file.
    _file_was_explicit = agentfile_path != "agentfile.yaml" or image is None
    if image and not _file_was_explicit:
        try:
            af = _load_agentfile_from_image(image)
        except Exception as exc:
            console.print(f"[yellow]Could not read agentfile from image:[/yellow] {exc}")
            console.print("  [dim]Falling back to local agentfile.yaml …[/dim]\n")
            try:
                af = AgentFile.from_path(agentfile_path)
            except (FileNotFoundError, ValueError) as exc2:
                console.print(f"[red]{exc2}[/red]")
                raise SystemExit(1)
    else:
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
            console.print(
                f"\n[red]Error:[/red] {key_var} is not set.\n"
                f"Set it with:  [dim]export {key_var}=your-key-here[/dim]\n"
            )
            raise SystemExit(1)

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
        os.environ.get("MCP_GATEWAY_ORG_ID")
        or _load_dotenv_key("MCP_GATEWAY_ORG_ID")
        or (af.mcp_gateway.org_id if af.mcp_gateway else None)
    )
    if _gw_ws_src:
        env["MCP_GATEWAY_ORG_ID"] = _gw_ws_src
    elif _gw_running:
        # Dev stack always resolves REQUIRE_AUTH=false tokens to org "default"
        env.setdefault("MCP_GATEWAY_ORG_ID", "default")

    for pair in extra_env:
        if "=" in pair:
            k, v = pair.split("=", 1)
            env[k] = v
        else:
            console.print(f"  [yellow]Skipping malformed env var:[/yellow] {pair!r}")

    # Effective triggers for the entry agent
    eff_triggers = af.effective_triggers(agent)

    # Auto-prompt channel setup if agentfile has channel triggers
    channel_triggers = [t for t in eff_triggers if t.type == "channel"]
    if channel_triggers:
        from agentfile.core.channel_config import is_verified, get_channel as _get_ch_cfg
        for ct in channel_triggers:
            for ch_type in ct.channels:
                if not is_verified(ch_type):
                    # Check if the API has a verified channel (e.g. set up via dashboard)
                    _try_sync_channel_from_api(ch_type)
                if not is_verified(ch_type):
                    console.print(
                        f"  [yellow]📱 {ch_type.title()} channel detected but not configured.[/yellow]\n"
                    )
                    if ch_type == "telegram":
                        from agentfile.commands.channel import setup_telegram_interactive
                        ok = setup_telegram_interactive(agent_name=agent.name)
                    elif ch_type == "discord":
                        from agentfile.commands.channel import setup_discord_interactive
                        ok = setup_discord_interactive(agent_name=agent.name)
                    elif ch_type == "whatsapp":
                        from agentfile.commands.channel import setup_whatsapp_interactive
                        ok = setup_whatsapp_interactive(agent_name=agent.name)
                    else:
                        ok = False
                    if not ok:
                        console.print(f"  [dim]Skipping {ch_type} setup. Run later:[/dim] [bold]ninetrix channel connect {ch_type}[/bold]\n")
                else:
                    _ch_cfg = _get_ch_cfg(ch_type)
                    _bot = _ch_cfg.get("bot_username", "?") if _ch_cfg else "?"
                    console.print(f"  [green]✓[/green] {ch_type.title()} connected: [bold]@{_bot}[/bold]\n")

    webhook_triggers = [t for t in eff_triggers if t.type in ("webhook", "channel")]
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

    # Forward tool credentials from host env based on Tool Hub metadata.
    # For each tool in the agentfile, look up its credentials in the hub
    # and forward the matching env vars (+ aliases) into the container.
    from agentfile.core.tool_hub import get as _hub_get
    for _t in agent.tools:
        # For hub:// tools, look up by hub_name; otherwise by tool name
        _lookup = _t.hub_name if _t.is_hub() else _t.name
        _hub_entry = _hub_get(_lookup) if _lookup else None
        if _hub_entry:
            # Forward required credential env vars
            for _cred_var in _hub_entry.credentials:
                _val = os.environ.get(_cred_var) or _load_dotenv_key(_cred_var)
                if _val:
                    env.setdefault(_cred_var, _val)
            # Forward aliases (e.g. GITHUB_TOKEN → GH_TOKEN)
            for _alias, _canonical in _hub_entry.credential_aliases.items():
                _val = os.environ.get(_alias) or _load_dotenv_key(_alias)
                if _val:
                    env.setdefault(_canonical, _val)
                    env.setdefault(_alias, _val)

    # Forward known AGENTFILE_* runtime overrides from the host env.
    # Uses an allowlist to avoid leaking unrelated host env vars into containers.
    _AGENTFILE_ALLOWLIST = {
        "AGENTFILE_PROVIDER", "AGENTFILE_MODEL", "AGENTFILE_TEMPERATURE",
        "AGENTFILE_MAX_TOKENS", "AGENTFILE_MAX_TURNS", "AGENTFILE_TOOL_TIMEOUT",
        "AGENTFILE_HISTORY_WINDOW_TOKENS", "AGENTFILE_MAX_PLAN_STEPS",
        "AGENTFILE_VERIFY_STEPS", "AGENTFILE_ON_STEP_FAILURE",
        "AGENTFILE_THINKING_ENABLED", "AGENTFILE_THINKING_PROVIDER",
        "AGENTFILE_THINKING_MODEL", "AGENTFILE_THINKING_MAX_TOKENS",
        "AGENTFILE_THINKING_TEMPERATURE", "AGENTFILE_THINKING_MIN_LENGTH",
        "AGENTFILE_THINKING_PROMPT",
        "AGENTFILE_VERIFIER_MODEL",
        "AGENTFILE_APPROVAL_ENABLED",
        "AGENTFILE_API_URL", "AGENTFILE_RUNNER_TOKEN",
        "AGENTFILE_THREAD_ID", "AGENTFILE_SYSTEM_PROMPT",
        "AGENTFILE_WEBHOOK_PORT",
        "AGENTFILE_CHANNEL_SESSION_MODE", "AGENTFILE_CHANNEL_VERBOSE",
        "AGENTFILE_CHANNEL_ALLOWED_IDS", "AGENTFILE_CHANNEL_REJECT_MESSAGE",
        "AGENTFILE_CHANNEL_LEGACY_BRIDGE",
        "AGENTFILE_APPROVAL_NOTIFY_URL",
    }
    # Also forward any AGENTFILE_CHANNEL_<TYPE>_* and AGENTFILE_VOL_* vars.
    for _k, _v in os.environ.items():
        if not _k.startswith("AGENTFILE_"):
            continue
        if _k in _AGENTFILE_ALLOWLIST or _k.startswith("AGENTFILE_CHANNEL_") or _k.startswith("AGENTFILE_VOL_") or _k.startswith("AGENTFILE_PEER_"):
            env.setdefault(_k, _v)

    # Inject channel credentials into container env vars.
    # The in-container ChannelManager reads these to connect to platforms.
    _bridge = None
    if channel_triggers:
        from agentfile.core.channel_config import is_verified as _ch_verified, get_channel as _get_ch

        _all_channel_types: set[str] = set()
        for ct in channel_triggers:
            _all_channel_types.update(ct.channels)
            # Inject session_mode, verbose, and access control from the trigger config
            env.setdefault("AGENTFILE_CHANNEL_SESSION_MODE", ct.session_mode)
            env.setdefault("AGENTFILE_CHANNEL_VERBOSE", "true" if ct.verbose else "false")
            if ct.allowed_ids:
                env.setdefault("AGENTFILE_CHANNEL_ALLOWED_IDS", ",".join(ct.allowed_ids))
            if ct.reject_message:
                env.setdefault("AGENTFILE_CHANNEL_REJECT_MESSAGE", ct.reject_message)

        _wa_volume: str | None = None
        for _ch_type in sorted(_all_channel_types):
            _ch_cfg = _get_ch(_ch_type)
            if _ch_cfg and _ch_cfg.get("verified"):
                _prefix = f"AGENTFILE_CHANNEL_{_ch_type.upper()}"
                if _ch_cfg.get("bot_token"):
                    env[f"{_prefix}_BOT_TOKEN"] = _ch_cfg["bot_token"]
                if _ch_cfg.get("chat_id"):
                    env[f"{_prefix}_CHAT_ID"] = str(_ch_cfg["chat_id"])

                # WhatsApp: mount auth dir as volume + set env vars
                if _ch_type == "whatsapp":
                    _wa_auth = _ch_cfg.get("auth_dir", str(Path.home() / ".agentfile" / "whatsapp-auth"))
                    env[f"{_prefix}_AUTH_DIR"] = "/data/whatsapp"
                    env[f"{_prefix}_ENABLED"] = "true"
                    _wa_volume = f"{_wa_auth}:/data/whatsapp"

                _ch_label = _ch_cfg.get("bot_username") or _ch_cfg.get("phone_number") or _ch_type
                console.print(
                    f"  [green]📱 {_ch_type.title()} channel:[/green] "
                    f"credentials injected into container (@{_ch_label})"
                )

        # Fallback: start external ChannelBridge for Telegram if the
        # container doesn't have ninetrix-channels installed (e.g. old image).
        # New images use the in-container ChannelManager instead.
        _use_legacy_bridge = os.environ.get("AGENTFILE_CHANNEL_LEGACY_BRIDGE", "").lower() in ("1", "true")
        if _use_legacy_bridge:
            for ct in channel_triggers:
                if "telegram" in ct.channels and _ch_verified("telegram"):
                    from agentfile.core.channel_bridge import ChannelBridge
                    _bridge_port = ct.port or 9100
                    _bridge_endpoint = ct.endpoint or "/run"
                    _bridge = ChannelBridge(agent_port=_bridge_port, agent_name=agent.name, endpoint=_bridge_endpoint)
                    if _bridge.start():
                        tg_cfg = _get_ch("telegram")
                        bot_name = tg_cfg.get("bot_username", "?") if tg_cfg else "?"
                        console.print(f"  [green]📱 Telegram bridge active (legacy):[/green] @{bot_name} → localhost:{_bridge_port}/run\n")
                    break

    # Mount WhatsApp auth volume if configured
    if channel_triggers and _wa_volume:
        from agentfile.core.models import VolumeSpec
        _wa_parts = _wa_volume.split(":")
        local_volumes.append(VolumeSpec(
            name="whatsapp-auth",
            provider="local",
            host_path=_wa_parts[0],
            container_path=_wa_parts[1],
        ))

    res = agent.resources
    try:
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
    finally:
        if _bridge:
            _bridge.stop()
    console.print()
