"""Shared Jinja2 template context builder.

Used by both:
  - cli/agentfile/commands/build.py  (CLI docker build)
  - runner/runner.py                 (SaaS runner boot)
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any, Callable

try:
    from pydantic import BaseModel, ConfigDict
    _PYDANTIC_AVAILABLE = True
except ImportError:
    _PYDANTIC_AVAILABLE = False

if _PYDANTIC_AVAILABLE:
    class TemplateContext(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True)

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
        has_schedule_triggers: bool = False
        has_any_triggers: bool = False
        webhook_trigger_defs: list = []
        schedule_trigger_defs: list = []
        webhook_port: int = 8000
        is_multi_agent: bool = False
        agent_name: str = ""
        collaborators: list = []
        has_collaborators: bool = False
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
        mcp_gateway_workspace: str = "default"
        has_local_tools: bool = False
        local_tool_files: list = []
        local_tool_manifests: list = []
        local_source_paths: list = []


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

    agent = dataclasses.replace(
        agent_def,
        governance=eff_governance,
        triggers=eff_triggers,
    )

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
    mcp_gateway_workspace = mcp_gateway.workspace_id if mcp_gateway else "default"

    # Local MCP subprocess mode has been removed.
    # All MCP tools now route through the MCP gateway/worker infrastructure.
    # use_mcp_gateway is True whenever the agent has any mcp:// tools.
    has_any_mcp_tools = any(t.is_mcp() for t in agent.tools)
    if has_any_mcp_tools and not use_mcp_gateway:
        # No explicit mcp_gateway: block in yaml — gateway URL/token/workspace
        # will be read purely from env vars (MCP_GATEWAY_URL, MCP_GATEWAY_TOKEN,
        # MCP_GATEWAY_WORKSPACE) at runtime.
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

    has_webhook_triggers  = bool(agent.webhook_triggers())
    has_schedule_triggers = bool(agent.schedule_triggers())
    has_any_triggers      = has_webhook_triggers or has_schedule_triggers

    webhook_trigger_defs  = [
        {"endpoint": t.endpoint, "port": t.port}
        for t in agent.webhook_triggers()
    ]
    schedule_trigger_defs = [
        {"cron": t.cron, "message": t.message or "Run your scheduled task."}
        for t in agent.schedule_triggers()
    ]
    webhook_port = agent.webhook_triggers()[0].port if agent.webhook_triggers() else 8000

    is_multi_agent    = af.is_multi_agent
    collaborators     = agent_def.collaborators
    has_collaborators = bool(collaborators)
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

    result = {
        "agent":                      agent,
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
        "has_schedule_triggers":      has_schedule_triggers,
        "has_any_triggers":           has_any_triggers,
        "webhook_trigger_defs":       webhook_trigger_defs,
        "schedule_trigger_defs":      schedule_trigger_defs,
        "webhook_port":               webhook_port,
        "is_multi_agent":             is_multi_agent,
        "agent_name":                 agent_def.name,
        "collaborators":              collaborators,
        "has_collaborators":          has_collaborators,
        "invoke_port":                9000,
        "transfer_timeout":           300,
        "base_image":                 base_image,
        "has_s3_volumes":             has_s3_volumes,
        "volume_defs":                volume_defs,
        "is_saas_runner":             is_saas_runner,
        "has_invoke_server":          has_invoke_server,
        "use_mcp_gateway":            use_mcp_gateway,
        "mcp_gateway_url":            mcp_gateway_url,
        "mcp_gateway_token":          mcp_gateway_token,
        "mcp_gateway_workspace":      mcp_gateway_workspace,
        "has_local_tools":            has_local_tools,
        "local_tool_files":           local_tool_files,
        "local_tool_manifests":       local_tool_manifests,
        "local_source_paths":         local_source_paths,
    }
    if _PYDANTIC_AVAILABLE:
        return TemplateContext(**result)
    return result
