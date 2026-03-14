"""Shared Jinja2 template context builder.

Used by both:
  - cli/agentfile/commands/build.py  (CLI docker build)
  - runner/runner.py                 (SaaS runner boot)

When copied into runner/core/ by build.sh, the try/except import resolves
the correct mcp_registry location automatically.
"""

from __future__ import annotations

import dataclasses
from typing import Callable


def build_context(
    af,
    agent_def,
    *,
    is_saas_runner: bool = False,
    has_invoke_server: bool = False,
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
    try:
        from agentfile.core.mcp_registry import resolve
    except ImportError:
        from core.mcp_registry import resolve  # type: ignore[import]

    eff_governance  = af.effective_governance(agent_def)
    eff_persistence = af.effective_persistence(agent_def)
    eff_triggers    = af.effective_triggers(agent_def)

    agent = dataclasses.replace(
        agent_def,
        governance=eff_governance,
        persistence=eff_persistence,
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

    needs_node    = False
    needs_uv      = False
    has_mcp_tools = False
    mcp_tool_defs: list[dict] = []

    if use_mcp_gateway:
        # In gateway mode tools are discovered at runtime — no local server setup needed
        has_mcp_tools = False
    else:
        for tool in agent.tools:
            if not tool.is_mcp():
                continue
            has_mcp_tools = True
            sdef = resolve(tool.mcp_name)
            if sdef is None:
                if _warn:
                    _warn(
                        f"MCP server '{tool.mcp_name}' is not in the registry. "
                        f"Run: ninetrix mcp add {tool.mcp_name} ..."
                    )
            else:
                if sdef.type == "npx":
                    needs_node = True
                if sdef.type == "uvx":
                    needs_uv = True
            mcp_tool_defs.append({
                "alias":        tool.name,
                "registry_key": tool.mcp_name,
                "sdef":         sdef,
            })

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

    return {
        "agent":                      agent,
        "needs_node":                 needs_node,
        "needs_uv":                   needs_uv,
        "has_mcp_tools":              has_mcp_tools,
        "mcp_tool_defs":              mcp_tool_defs,
        "has_composio_tools":         has_composio_tools,
        "composio_tool_defs":         composio_tool_defs,
        "has_persistence":            eff_persistence is not None,
        "persistence_provider":       eff_persistence.provider if eff_persistence else "",
        "persistence_url_template":   eff_persistence.url if eff_persistence else "",
        "approval_notify_url":        eff_governance.human_approval.notify_url,
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
    }
