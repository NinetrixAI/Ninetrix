"""Shared Jinja2 template context builder.

Used by both:
  - cli/agentfile/commands/build.py  (CLI docker build)
  - runner/runner.py                 (SaaS runner boot)
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any, Callable

try:
    from pydantic import BaseModel, ConfigDict
    _PYDANTIC_AVAILABLE = True
except ImportError:
    _PYDANTIC_AVAILABLE = False

if _PYDANTIC_AVAILABLE:
    class TemplateContext(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

        agent: Any
        has_composio_tools: bool = False
        composio_tool_defs: list = []
        has_planned_execution: bool = False
        verify_steps: bool = False
        max_plan_steps: int = 10
        on_step_failure: str = "continue"
        has_verifier: bool = False
        verifier_provider: str = ""
        verifier_model: str = ""
        verifier_max_tokens: int = 128
        has_thinking_step: bool = False
        thinking_provider: str = ""
        thinking_model: str = ""
        thinking_max_tokens: int = 2048
        thinking_temperature: float = 0.5
        thinking_min_input_length: int = 50
        thinking_prompt: str = ""
        has_webhook_triggers: bool = False
        has_channel_triggers: bool = False
        channel_types: list = []
        has_schedule_triggers: bool = False
        has_any_triggers: bool = False
        webhook_trigger_defs: list = []
        schedule_trigger_defs: list = []
        webhook_port: int = 8000
        is_multi_agent: bool = False
        agent_name: str = ""
        collaborators: list = []
        has_collaborators: bool = False
        has_auto_routing: bool = False
        routing_model: str = ""
        routing_provider: str = ""
        has_output_type: bool = False
        output_type_schema: str = ""
        max_tokens: int = 8192
        max_turns: int = 20
        tool_timeout: int = 30
        history_window_tokens: int = 90_000
        invoke_port: int = 9000
        transfer_timeout: int = 300
        base_image: str = "python:3.12-slim"
        has_s3_volumes: bool = False
        volume_defs: list = []
        is_saas_runner: bool = False
        has_invoke_server: bool = False
        use_mcp_gateway: bool = False
        mcp_gateway_url: str = ""
        mcp_gateway_token: str = ""
        mcp_gateway_org_id: str = "default"
        has_local_tools: bool = False
        local_tool_files: list = []
        local_tool_manifests: list = []
        local_source_paths: list = []
        has_builtin_shell: bool = False
        has_builtin_bash: bool = False
        has_builtin_filesystem: bool = False
        has_builtin_memory: bool = False
        has_builtin_scheduler: bool = False
        has_builtin_web_search: bool = False
        has_builtin_web_browse: bool = False
        has_builtin_notify: bool = False
        has_builtin_ask_user: bool = False
        has_builtin_sub_agent: bool = False
        has_builtin_code_interpreter: bool = False
        has_any_builtin: bool = False
        apt_packages: list = []
        npm_packages: list = []
        pip_packages: list = []
        has_apt_packages: bool = False
        has_npm_packages: bool = False
        has_pip_packages: bool = False
        has_skills: bool = False
        skill_instructions: str = ""
        skill_source_paths: list = []
        # Modular provider system — data-driven deps
        collected_deps: dict = {}
        builtin_names: set = set()
        tool_schemes: set = set()


_SKILLS_HUB_BASE = "https://raw.githubusercontent.com/Ninetrix-ai/skills-hub/main"

# In-memory cache for registry.json during a single build session.
_hub_registry_cache: dict | None = None


def _fetch_url(url: str) -> str | None:
    """Fetch a URL and return text content, or None on failure."""
    try:
        import httpx
        resp = httpx.get(url, timeout=15, follow_redirects=True)
        if resp.status_code == 200:
            return resp.text
        return None
    except Exception:
        return None


def _get_hub_registry() -> dict:
    """Fetch and cache registry.json from the Skills Hub."""
    global _hub_registry_cache
    if _hub_registry_cache is not None:
        return _hub_registry_cache

    import json
    raw = _fetch_url(f"{_SKILLS_HUB_BASE}/registry.json")
    if raw:
        try:
            _hub_registry_cache = json.loads(raw)
            return _hub_registry_cache
        except json.JSONDecodeError:
            pass
    _hub_registry_cache = {}
    return _hub_registry_cache


def _resolve_hub_skill(slug: str, version: str | None, _warn: Callable | None = None) -> str | None:
    """Fetch a hub:// skill from GitHub and verify integrity.

    Returns the instruction body (frontmatter stripped) or None on failure.
    """
    registry = _get_hub_registry()
    skills = registry.get("skills", {})

    # Resolve version
    resolved_version = version
    if resolved_version is None:
        entry = skills.get(slug)
        if entry and "latest" in entry:
            resolved_version = entry["latest"]
            print(
                f"  ℹ  hub://{slug} resolved to {resolved_version} "
                f"(pin with hub://{slug}@{resolved_version})",
                file=sys.stderr,
            )
        # If no registry or no entry, we'll still try to fetch directly

    # Fetch SKILL.md
    url = f"{_SKILLS_HUB_BASE}/skills/{slug}/SKILL.md"
    raw = _fetch_url(url)
    if raw is None:
        if _warn:
            _warn(
                f"hub://{slug}: could not fetch from Skills Hub. "
                "Check your internet connection or verify the skill exists at "
                f"https://github.com/Ninetrix-ai/skills-hub/tree/main/skills/{slug}"
            )
        return None

    body = _strip_frontmatter(raw)

    # Verify SHA256 if registry has hash info
    if resolved_version and slug in skills:
        versions = skills[slug].get("versions", {})
        version_info = versions.get(resolved_version, {})
        expected_hash = version_info.get("sha256")
        if expected_hash:
            actual_hash = hashlib.sha256(body.encode()).hexdigest()
            if actual_hash != expected_hash:
                if _warn:
                    _warn(
                        f"hub://{slug}@{resolved_version}: SHA256 mismatch! "
                        f"Expected {expected_hash[:16]}..., got {actual_hash[:16]}... "
                        "The skill may have been tampered with. Build rejected."
                    )
                return None

    if not body.strip():
        if _warn:
            _warn(f"hub://{slug}: SKILL.md is empty")
        return None

    return body


def _strip_frontmatter(text: str) -> str:
    """Strip YAML frontmatter from a SKILL.md file, returning only the body."""
    stripped = text.strip()
    if not stripped.startswith("---"):
        return stripped
    # Find closing ---
    end = stripped.find("---", 3)
    if end == -1:
        return stripped
    return stripped[end + 3:].strip()


def _resolve_local_skill(path: Path) -> str | None:
    """Resolve a local skill path to its instruction body.

    Supports two formats:
      1. New: path points to a directory containing SKILL.md (single file with frontmatter)
      2. New: path points directly to a SKILL.md file
      3. Legacy: path points to a directory containing instructions.md + skill.yaml
    """
    if path.is_file() and path.name == "SKILL.md":
        return _strip_frontmatter(path.read_text())

    if path.is_dir():
        # New format: SKILL.md
        skill_md = path / "SKILL.md"
        if skill_md.exists():
            return _strip_frontmatter(skill_md.read_text())
        # Legacy format: instructions.md
        inst = path / "instructions.md"
        if inst.exists():
            return inst.read_text().strip()

    return None


def build_context(
    af,
    agent_def,
    *,
    is_saas_runner: bool = False,
    has_invoke_server: bool = False,
    agentfile_dir: str | Path | None = None,
    _warn: Callable[[str], None] | None = None,
) -> dict:
    """Build the Jinja2 template context for a single agent.

    Args:
        af:               AgentFile root object.
        agent_def:        AgentDef for the agent being rendered.
        is_saas_runner:   True when called from the SaaS runner boot script.
        has_invoke_server: True when agent.serve is enabled (CLI path).
        _warn:            Optional callable for MCP registry warnings.
    """
    eff_governance  = af.effective_governance(agent_def)
    eff_triggers    = af.effective_triggers(agent_def)

    agent = agent_def.model_copy(update={
        "governance": eff_governance,
        "triggers": eff_triggers,
    })

    # Gateway mode: agents call one HTTP endpoint instead of spawning local MCP processes
    mcp_gateway    = getattr(af, "mcp_gateway", None)
    use_mcp_gateway = mcp_gateway is not None
    # Replace localhost with host.docker.internal so the baked-in default works
    # inside any Docker container — regardless of whether run.py injects the URL.
    _raw_gw_url = mcp_gateway.url if mcp_gateway else ""
    mcp_gateway_url = (
        _raw_gw_url
        .replace("localhost", "host.docker.internal")
        .replace("127.0.0.1", "host.docker.internal")
    )
    mcp_gateway_token     = mcp_gateway.token        if mcp_gateway else ""
    mcp_gateway_org_id = mcp_gateway.org_id if mcp_gateway else "default"

    # Local MCP subprocess mode has been removed.
    # All MCP tools now route through the MCP gateway/worker infrastructure.
    # use_mcp_gateway is True whenever the agent has any mcp:// tools.
    has_any_mcp_tools = any(t.is_mcp() for t in agent.tools)
    if has_any_mcp_tools and not use_mcp_gateway:
        # No explicit mcp_gateway: block in yaml — gateway URL/token/org_id
        # will be read purely from env vars (MCP_GATEWAY_URL, MCP_GATEWAY_TOKEN,
        # MCP_GATEWAY_ORG_ID) at runtime.
        use_mcp_gateway = True

    composio_tool_defs = [
        {"app": t.composio_app, "actions": t.actions}
        for t in agent.tools if t.is_composio()
    ]
    has_composio_tools = bool(composio_tool_defs)

    exec_obj          = af.effective_execution(agent_def)
    ver_obj           = exec_obj.verifier
    verifier_provider = ver_obj.provider or agent.provider
    verifier_model    = ver_obj.model    or agent.model
    thinking_cfg      = exec_obj.thinking
    thinking_provider = thinking_cfg.provider or agent.provider
    thinking_model    = thinking_cfg.model    or agent.model

    # webhook_triggers() includes channel triggers for port binding purposes,
    # but has_webhook_triggers should only be True for actual webhook triggers
    # (not channel triggers) — channel messages flow through ChannelManager → /chat,
    # not through the webhook trigger queue.
    _pure_webhook_triggers = [t for t in eff_triggers if t.type == "webhook"]
    has_webhook_triggers  = bool(_pure_webhook_triggers)
    _channel_triggers     = [t for t in eff_triggers if t.type == "channel"]
    has_channel_triggers  = bool(_channel_triggers)
    # Collect unique channel types across all channel triggers
    _channel_types_set: set[str] = set()
    for _ct in _channel_triggers:
        _channel_types_set.update(_ct.channels)
    channel_types = sorted(_channel_types_set)
    has_schedule_triggers = bool(agent.schedule_triggers())
    has_any_triggers      = has_webhook_triggers or has_schedule_triggers or has_channel_triggers

    webhook_trigger_defs  = [
        {"endpoint": t.endpoint or "/run", "port": t.port}
        for t in _pure_webhook_triggers
    ]
    schedule_trigger_defs = [
        {"cron": t.cron, "message": t.message or "Run your scheduled task."}
        for t in agent.schedule_triggers()
    ]
    # Port for the webhook/channel server — channel triggers also need a port
    # because the ChannelManager calls /chat on the internal FastAPI server.
    _all_trigger_ports = _pure_webhook_triggers + _channel_triggers
    webhook_port = _all_trigger_ports[0].port if _all_trigger_ports else 8000

    is_multi_agent    = af.is_multi_agent
    collaborators     = agent_def.collaborators
    has_collaborators = bool(collaborators)
    has_auto_routing  = has_collaborators and agent_def.routing_mode == "auto"
    routing_model     = agent_def.routing_model or "claude-haiku-4-5-20251001"
    routing_provider  = agent_def.routing_provider or agent_def.provider
    volume_defs       = af.effective_volumes(agent_def)
    has_s3_volumes    = any(v.provider == "s3" for v in volume_defs)
    base_image        = agent_def.resources.base_image or "python:3.12-slim"

    # ── Local @Tool discovery ──────────────────────────────────────────────────
    has_local_tools = False
    local_tool_files: list[str] = []         # container paths: /app/tools/foo.py
    local_tool_manifests: list[dict] = []    # tool schema dicts (name/description/parameters)
    local_source_paths: list[str] = []       # host absolute paths (for build.py to copy)

    if agentfile_dir is not None:
        _local_sources = [t for t in agent_def.tools if t.is_local()]
        if _local_sources:
            _af_dir = Path(agentfile_dir)
            _seen: set[str] = set()
            # Collect source paths first — these don't require the SDK.
            for _t in _local_sources:
                _src = (_af_dir / _t.source).resolve()
                if str(_src) not in _seen:
                    _seen.add(str(_src))
                    local_tool_files.append(f"/app/tools/{_src.name}")
                    local_source_paths.append(str(_src))
            # has_local_tools drives the async entrypoint path (with telemetry).
            # Set it whenever local tool sources are declared, regardless of SDK.
            has_local_tools = bool(local_source_paths)
            # Attempt manifest discovery — requires ninetrix-sdk.
            try:
                from ninetrix.discover import discover_tools_in_file as _dtif  # type: ignore[import]
                for _src_str in local_source_paths:
                    local_tool_manifests.extend(_dtif(Path(_src_str)))
            except ImportError:
                if _warn:
                    _warn(
                        "ninetrix-sdk is not installed — local @Tool discovery skipped. "
                        "Run: pip install -e /path/to/sdk  (or: pip install ninetrix-sdk)"
                    )
            except Exception as _exc:
                if _warn:
                    _warn(f"Local tool discovery failed: {_exc}")

    # ── Builtin tools ────────────────────────────────────────────────────────────
    _ALL_BUILTIN_NAMES = {
        "bash", "filesystem", "memory", "scheduler",
        "web_search", "web_browse", "notify", "ask_user",
        "sub_agent", "code_interpreter",
    }
    _builtin_tools = [t for t in agent_def.tools if t.is_builtin()]
    _builtin_names = {t.builtin_name for t in _builtin_tools}
    has_builtin_shell = "shell" in _builtin_names
    # Backward compat: "shell" → "bash"
    if "shell" in _builtin_names:
        _builtin_names.discard("shell")
        _builtin_names.add("bash")
    # tools: all → enable all
    if getattr(agent_def, "tools_all", False):
        _builtin_names = set(_ALL_BUILTIN_NAMES)
    has_builtin_bash = "bash" in _builtin_names
    has_builtin_filesystem = "filesystem" in _builtin_names
    has_builtin_memory = "memory" in _builtin_names
    has_builtin_scheduler = "scheduler" in _builtin_names
    has_builtin_web_search = "web_search" in _builtin_names
    has_builtin_web_browse = "web_browse" in _builtin_names
    has_builtin_notify = "notify" in _builtin_names
    has_builtin_ask_user = "ask_user" in _builtin_names
    has_builtin_sub_agent = "sub_agent" in _builtin_names
    has_builtin_code_interpreter = "code_interpreter" in _builtin_names
    has_any_builtin = bool(_builtin_names)

    # ── Skill discovery ──────────────────────────────────────────────────────
    has_skills = False
    skill_instructions_parts: list[str] = []
    skill_source_paths: list[str] = []

    for _s in agent_def.skills:
        if _s.is_hub():
            _body = _resolve_hub_skill(_s.hub_slug, _s.hub_version, _warn)
            if _body is not None:
                skill_instructions_parts.append(_body)
                skill_source_paths.append(_s.source)
            elif _warn:
                _warn(f"Skill '{_s.source}': could not resolve from Skills Hub")
        elif _s.is_local() and agentfile_dir is not None:
            _skill_path = (Path(agentfile_dir) / _s.source).resolve()
            _body = _resolve_local_skill(_skill_path)
            if _body is not None:
                skill_instructions_parts.append(_body)
                skill_source_paths.append(str(_skill_path))
            elif _warn:
                _warn(f"Skill '{_s.source}': no SKILL.md or instructions.md found at {_skill_path}")
    has_skills = bool(skill_instructions_parts)

    # Token budget warning for skills
    if has_skills:
        _total_skill_chars = sum(len(p) for p in skill_instructions_parts)
        _est_tokens = _total_skill_chars // 4  # rough estimate: 4 chars per token
        _window = getattr(agent_def, "history_window_tokens", 90000) or 90000
        _pct = (_est_tokens / _window) * 100
        if _pct > 15:
            print(
                f"  ⚠  Skills use ~{_est_tokens:,} tokens ({_pct:.0f}% of "
                f"history_window_tokens: {_window:,}). Consider reducing skills "
                f"or increasing runtime.history_window_tokens.",
                file=sys.stderr,
            )

    import json as _json
    has_output_type = agent_def.output_type is not None
    output_type_schema = _json.dumps(agent_def.output_type) if has_output_type else ""

    # ── Resolve hub:// tools (fetch install/deps from Tool Hub) ─────────────────
    from agentfile.core.tool_hub import get as _hub_get
    for _t in agent_def.tools:
        if _t.is_hub() and _t.hub_name:
            _hub_entry = _hub_get(_t.hub_name)
            if _hub_entry and _hub_entry.install:
                print(f"  ℹ  hub://{_t.hub_name} → CLI install resolved from Tool Hub", file=sys.stderr)

    # ── Collected dependencies (data-driven Dockerfile) ───────────────────────
    _collected_pip: set[str] = set()
    _collected_apt: set[str] = set()
    _collected_npm: set[str] = set()
    _collected_apt_repos: list[dict] = []  # [{keyring_url, repo}]
    _collected_install: list[str] = []  # legacy raw install commands
    for _t in agent_def.tools:
        if _t.is_composio():
            _collected_pip.add("composio")
        if hasattr(_t, 'scheme') and _t.scheme == "openapi":
            _collected_pip.add("httpx>=0.27")
        if hasattr(_t, 'dependencies') and _t.dependencies:
            _collected_pip.update(_t.dependencies.pip)
            _collected_apt.update(_t.dependencies.apt)
            if _t.dependencies.install:
                _collected_install.append(_t.dependencies.install)
        # Resolve hub:// tools — pull deps from Tool Hub registry
        if _t.is_hub() and _t.hub_name:
            _hub_entry = _hub_get(_t.hub_name)
            if _hub_entry:
                if _hub_entry.pip_deps:
                    _collected_pip.update(_hub_entry.pip_deps)
                if _hub_entry.apt_deps:
                    _collected_apt.update(_hub_entry.apt_deps)
                if _hub_entry.npm_deps:
                    _collected_npm.update(_hub_entry.npm_deps)
                if _hub_entry.apt_repo and _hub_entry.apt_repo.get("repo"):
                    _collected_apt_repos.append(_hub_entry.apt_repo)
                if _hub_entry.install:
                    _collected_install.append(_hub_entry.install)
    # Feature-level deps
    if has_channel_triggers:
        # ninetrix-channels is copied into the build context (not from PyPI)
        _collected_pip.add("httpx>=0.27")
    if has_any_triggers or is_multi_agent or has_invoke_server:
        _collected_pip.update(["fastapi>=0.104", "uvicorn[standard]>=0.24"])
    if has_schedule_triggers:
        _collected_pip.add("apscheduler>=3.10")
    if has_collaborators:
        _collected_pip.add("aiohttp>=3.9")
    if has_s3_volumes:
        _collected_pip.add("awscli")
    if use_mcp_gateway:
        _collected_pip.add("httpx>=0.27")
    # User-declared packages
    for _p in agent_def.packages:
        if _p.startswith("pip:"):
            _collected_pip.add(_p[4:])
        elif _p.startswith("npm:"):
            pass  # npm handled separately
        else:
            _collected_apt.add(_p)

    # Also collect npm from user packages
    for _p in agent_def.packages:
        if _p.startswith("npm:"):
            _collected_npm.add(_p[4:])

    collected_deps = {
        "pip": sorted(_collected_pip),
        "apt": sorted(_collected_apt),
        "npm": sorted(_collected_npm),
        "apt_repos": _collected_apt_repos,
        "install": _collected_install,
    }
    tool_schemes = {_t.scheme for _t in agent_def.tools if hasattr(_t, 'scheme')}

    result = {
        "agent":                      agent,
        "has_output_type":            has_output_type,
        "output_type_schema":         output_type_schema,
        "has_composio_tools":         has_composio_tools,
        "composio_tool_defs":         composio_tool_defs,
        "has_planned_execution":      exec_obj.mode == "planned",
        "verify_steps":               exec_obj.verify_steps,
        "max_plan_steps":             exec_obj.max_steps,
        "on_step_failure":            exec_obj.on_step_failure,
        "has_verifier":               exec_obj.verify_steps,
        "verifier_provider":          verifier_provider,
        "verifier_model":             verifier_model,
        "verifier_max_tokens":        ver_obj.max_tokens,
        "has_thinking_step":          thinking_cfg.enabled,
        "thinking_provider":          thinking_provider,
        "thinking_model":             thinking_model,
        "thinking_max_tokens":        thinking_cfg.max_tokens,
        "thinking_temperature":       thinking_cfg.temperature,
        "thinking_min_input_length":  thinking_cfg.min_input_length,
        "thinking_prompt":            thinking_cfg.prompt,
        "has_webhook_triggers":       has_webhook_triggers,
        "has_channel_triggers":       has_channel_triggers,
        "channel_types":              channel_types,
        "has_schedule_triggers":      has_schedule_triggers,
        "has_any_triggers":           has_any_triggers,
        "webhook_trigger_defs":       webhook_trigger_defs,
        "schedule_trigger_defs":      schedule_trigger_defs,
        "webhook_port":               webhook_port,
        "is_multi_agent":             is_multi_agent,
        "agent_name":                 agent_def.name,
        "collaborators":              collaborators,
        "has_collaborators":          has_collaborators,
        "has_auto_routing":           has_auto_routing,
        "routing_model":              routing_model,
        "routing_provider":           routing_provider,
        "max_tokens":                 agent_def.max_tokens,
        "max_turns":                  agent_def.max_turns,
        "tool_timeout":               agent_def.tool_timeout,
        "history_window_tokens":      agent_def.history_window_tokens,
        "invoke_port":                9000,
        "transfer_timeout":           agent_def.transfer_timeout,
        "base_image":                 base_image,
        "has_s3_volumes":             has_s3_volumes,
        "volume_defs":                volume_defs,
        "is_saas_runner":             is_saas_runner,
        "has_invoke_server":          has_invoke_server,
        "use_mcp_gateway":            use_mcp_gateway,
        "mcp_gateway_url":            mcp_gateway_url,
        "mcp_gateway_token":          mcp_gateway_token,
        "mcp_gateway_org_id":      mcp_gateway_org_id,
        "has_local_tools":            has_local_tools,
        "local_tool_files":           local_tool_files,
        "local_tool_manifests":       local_tool_manifests,
        "local_source_paths":         local_source_paths,
        "has_builtin_shell":          has_builtin_shell,
        "has_builtin_bash":           has_builtin_bash,
        "has_builtin_filesystem":     has_builtin_filesystem,
        "has_builtin_memory":         has_builtin_memory,
        "has_builtin_scheduler":      has_builtin_scheduler,
        "has_builtin_web_search":     has_builtin_web_search,
        "has_builtin_web_browse":     has_builtin_web_browse,
        "has_builtin_notify":         has_builtin_notify,
        "has_builtin_ask_user":       has_builtin_ask_user,
        "has_builtin_sub_agent":      has_builtin_sub_agent,
        "has_builtin_code_interpreter": has_builtin_code_interpreter,
        "has_any_builtin":            has_any_builtin,
        "apt_packages":               [p for p in agent_def.packages if ":" not in p],
        "npm_packages":               [p[4:] for p in agent_def.packages if p.startswith("npm:")],
        "pip_packages":               [p[4:] for p in agent_def.packages if p.startswith("pip:")],
        "has_apt_packages":           any(":" not in p for p in agent_def.packages),
        "has_npm_packages":           any(p.startswith("npm:") for p in agent_def.packages),
        "has_pip_packages":           any(p.startswith("pip:") for p in agent_def.packages),
        "has_skills":                 has_skills,
        "skill_instructions":         "\n\n---\n\n".join(skill_instructions_parts),
        "skill_source_paths":         skill_source_paths,
        # Modular provider system — data-driven deps
        "collected_deps":             collected_deps,
        "builtin_names":              _builtin_names,
        "tool_schemes":               tool_schemes,
    }
    if _PYDANTIC_AVAILABLE:
        return TemplateContext(**result)
    return result
