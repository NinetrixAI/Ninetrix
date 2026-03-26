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


def _sync_bots_to_api() -> None:
    """Push all bots from channels.yaml to the local API DB.

    This keeps the dashboard in sync with the CLI. Called on startup
    (ninetrix run / ninetrix up). Creates or updates channels in the API.
    """
    from agentfile.core.config import resolve_api_url
    from agentfile.core.channel_config import list_bots

    api_url = resolve_api_url()
    if not api_url:
        return

    # Use machine secret for local API (auth_headers may return stale SaaS token)
    _secret_file = Path.home() / ".agentfile" / ".api-secret"
    if _secret_file.exists():
        _secret = _secret_file.read_text().strip()
        headers = {"Authorization": f"Bearer {_secret}"}
    else:
        from agentfile.core.auth import auth_headers
        headers = auth_headers(api_url)
    if not headers:
        return

    bots = list_bots()
    if not bots:
        return

    synced = 0
    for bot_name, cfg in bots.items():
        if not isinstance(cfg, dict) or not cfg.get("verified"):
            continue
        ch_type = cfg.get("channel_type", "")
        if not ch_type:
            continue

        try:
            # Check if channel already exists in API by searching for matching bot_token
            resp = httpx.get(f"{api_url}/v1/channels", headers=headers, timeout=5)
            existing = None
            if resp.status_code == 200:
                for ch in resp.json():
                    if ch.get("config", {}).get("bot_token") == cfg.get("bot_token", ""):
                        existing = ch
                        break
                    # Match by name for channels without bot_token (whatsapp)
                    if ch.get("name") == bot_name and ch.get("channel_type") == ch_type:
                        existing = ch
                        break

            if existing:
                # Update name if changed
                if existing.get("name") != bot_name:
                    httpx.patch(
                        f"{api_url}/v1/channels/{existing['id']}",
                        headers=headers,
                        json={"name": bot_name},
                        timeout=5,
                    )
                    synced += 1
            else:
                # Create new channel in API
                config_payload = {"bot_token": cfg.get("bot_token", "")}
                if cfg.get("chat_id"):
                    config_payload["chat_id"] = str(cfg["chat_id"])
                if cfg.get("bot_username"):
                    config_payload["bot_username"] = cfg["bot_username"]

                resp = httpx.post(
                    f"{api_url}/v1/channels",
                    headers=headers,
                    json={
                        "channel_type": ch_type,
                        "name": bot_name,
                        "config": config_payload,
                        "session_mode": "per_chat",
                        "routing_mode": "single",
                    },
                    timeout=10,
                )
                if resp.status_code in (200, 201):
                    ch_data = resp.json()
                    # Auto-verify
                    code = ch_data.get("config", {}).get("verification_code", "000000")
                    httpx.post(
                        f"{api_url}/v1/channels/{ch_data['id']}/verify",
                        headers=headers,
                        json={"code": code},
                        timeout=5,
                    )
                    synced += 1
        except httpx.ConnectError:
            return  # API not running
        except Exception:
            pass

    if synced:
        console.print(f"  [dim]Synced {synced} channel(s) to dashboard[/dim]")


def _sync_api_to_bots() -> None:
    """Pull channels from the API DB into channels.yaml.

    This keeps the CLI in sync with the dashboard. Called on startup
    alongside _sync_bots_to_api() for bidirectional sync.
    Also removes bots from channels.yaml that were deleted from the API.
    """
    from agentfile.core.config import resolve_api_url
    from agentfile.core.channel_config import get_bot, save_bot, list_bots, remove_bot

    api_url = resolve_api_url()
    if not api_url:
        return

    _secret_file = Path.home() / ".agentfile" / ".api-secret"
    if _secret_file.exists():
        _secret = _secret_file.read_text().strip()
        headers = {"Authorization": f"Bearer {_secret}"}
    else:
        from agentfile.core.auth import auth_headers
        headers = auth_headers(api_url)
    if not headers:
        return

    try:
        resp = httpx.get(f"{api_url}/v1/channels", headers=headers, timeout=5)
        if resp.status_code != 200:
            return
        channels = resp.json()
    except Exception:
        return

    # Build set of bot names that exist in the API
    api_bot_names = {ch.get("name", "") for ch in channels if ch.get("name")}

    # Remove bots from channels.yaml that were deleted from the API
    local_bots = list_bots()
    removed = 0
    for local_name in list(local_bots.keys()):
        if local_name not in api_bot_names and isinstance(local_bots[local_name], dict):
            remove_bot(local_name)
            removed += 1
    if removed:
        console.print(f"  [dim]Removed {removed} deleted channel(s) from local config[/dim]")

    synced = 0
    for ch in channels:
        if not ch.get("verified"):
            continue
        bot_name = ch.get("name", "")
        ch_type = ch.get("channel_type", "")
        config = ch.get("config", {})
        if not bot_name or not ch_type:
            continue

        # Skip if already exists in channels.yaml with same token
        existing = get_bot(bot_name)
        if existing and existing.get("bot_token") == config.get("bot_token", ""):
            continue

        # Save to channels.yaml
        save_bot(bot_name, {
            "channel_type": ch_type,
            "bot_token": config.get("bot_token", ""),
            "bot_username": config.get("bot_username", ""),
            "chat_id": config.get("chat_id", ""),
            "verified": True,
        })
        synced += 1

    if synced:
        console.print(f"  [dim]Synced {synced} channel(s) from dashboard[/dim]")


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

    # Bidirectional sync: CLI ↔ Dashboard (single source of truth)
    _sync_api_to_bots()   # API → channels.yaml (dashboard-created channels)
    _sync_bots_to_api()   # channels.yaml → API (CLI-created channels)

    # Auto-prompt channel setup if agentfile has channel triggers
    channel_triggers = [t for t in eff_triggers if t.type == "channel"]
    if channel_triggers:
        from agentfile.core.channel_config import is_bot_verified, get_bot as _get_bot_cfg, find_bots_by_type
        for ct in channel_triggers:
            if ct.bot:
                # Explicit bot name — check if it's configured
                if not is_bot_verified(ct.bot):
                    ch_type = ct.channels[0] if ct.channels else "telegram"
                    console.print(f"  [yellow]📱 Bot '{ct.bot}' not configured.[/yellow]")
                    console.print(f"  [dim]Run: ninetrix channel connect {ch_type} --bot {ct.bot}[/dim]\n")
                else:
                    _bcfg = _get_bot_cfg(ct.bot)
                    _label = (_bcfg.get("bot_username") or _bcfg.get("phone_number") or "?") if _bcfg else "?"
                    console.print(f"  [green]✓[/green] {ct.bot} connected: [bold]{_label}[/bold]\n")
            else:
                # No explicit bot — check each channel type has at least one verified bot
                for ch_type in ct.channels:
                    bots = find_bots_by_type(ch_type)
                    verified = {n: c for n, c in bots.items() if c.get("verified")}
                    if not verified:
                        console.print(f"  [yellow]📱 No {ch_type} bot configured.[/yellow]")
                        console.print(f"  [dim]Run: ninetrix channel connect {ch_type}[/dim]\n")
                    else:
                        for bname, bcfg in verified.items():
                            _label = bcfg.get("bot_username") or bcfg.get("phone_number") or "?"
                            console.print(f"  [green]✓[/green] {bname} ({ch_type}): [bold]{_label}[/bold]")

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

    # Inject channel bot configs into container as JSON env var.
    # The in-container ChannelManager reads AGENTFILE_CHANNEL_BOTS to spawn adapters.
    _bridge = None
    if channel_triggers:
        import json as _json
        from agentfile.core.channel_config import get_bot as _get_bot, list_bots as _list_bots

        # Collect trigger-level settings
        for ct in channel_triggers:
            env.setdefault("AGENTFILE_CHANNEL_SESSION_MODE", ct.session_mode)
            env.setdefault("AGENTFILE_CHANNEL_VERBOSE", "true" if ct.verbose else "false")
            if ct.allowed_ids:
                env.setdefault("AGENTFILE_CHANNEL_ALLOWED_IDS", ",".join(ct.allowed_ids))
            if ct.reject_message:
                env.setdefault("AGENTFILE_CHANNEL_REJECT_MESSAGE", ct.reject_message)

        # Resolve which bots to inject.
        # If trigger has bot: field, use that specific bot.
        # Otherwise, find all verified bots matching the channel types.
        _bots_to_inject: dict[str, dict] = {}
        _wa_volume: str | None = None

        for ct in channel_triggers:
            if ct.bot:
                # Explicit bot name from agentfile.yaml trigger
                _cfg = _get_bot(ct.bot)
                if _cfg and _cfg.get("verified"):
                    _bots_to_inject[ct.bot] = _cfg
                else:
                    console.print(f"  [yellow]Bot '{ct.bot}' not found or not verified.[/yellow]")
                    console.print(f"  [dim]Run: ninetrix channel connect {ct.channels[0] if ct.channels else 'telegram'} --bot {ct.bot}[/dim]\n")
            else:
                # No explicit bot — find all verified bots for each channel type
                all_bots = _list_bots()
                for ch_type in ct.channels:
                    for bname, bcfg in all_bots.items():
                        if isinstance(bcfg, dict) and bcfg.get("channel_type") == ch_type and bcfg.get("verified"):
                            _bots_to_inject[bname] = bcfg

        # Build the JSON config and inject env vars
        _bots_json: list[dict] = []
        for bname, bcfg in sorted(_bots_to_inject.items()):
            ch_type = bcfg.get("channel_type", "")
            bot_entry = {
                "name": bname,
                "channel_type": ch_type,
                "bot_token": bcfg.get("bot_token", ""),
                "chat_id": str(bcfg.get("chat_id", "")),
            }

            # WhatsApp: mount auth dir as volume
            if ch_type == "whatsapp":
                _wa_auth = bcfg.get("auth_dir", str(Path.home() / ".agentfile" / "whatsapp-auth"))
                bot_entry["auth_dir"] = "/data/whatsapp"
                bot_entry["enabled"] = True
                _wa_volume = f"{_wa_auth}:/data/whatsapp"

            _bots_json.append(bot_entry)
            _ch_label = bcfg.get("bot_username") or bcfg.get("phone_number") or bname
            console.print(
                f"  [green]📱 {bname}[/green] ({ch_type}) → @{_ch_label}"
            )

        if _bots_json:
            env["AGENTFILE_CHANNEL_BOTS"] = _json.dumps(_bots_json)

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
