"""Tests for agentfile.core.template_context — Jinja2 context builder."""

from __future__ import annotations

import json

import pytest

from agentfile.core.models import (
    AgentDef,
    AgentFile,
    Execution,
    Governance,
    MCPGatewayConfig,
    ThinkingConfig,
    Tool,
    Trigger,
    VolumeSpec,
)
from agentfile.core.template_context import build_context


@pytest.fixture
def simple_af():
    """AgentFile with one simple agent."""
    agent = AgentDef(
        name="test-agent",
        provider="anthropic",
        model="claude-sonnet-4-6",
        temperature=0.2,
        tools=[Tool(name="search", source="mcp://brave-search")],
    )
    return AgentFile(
        agents={"test-agent": agent},
        governance=Governance(),
    )


class TestBuildContextBasic:
    def test_returns_context_with_agent(self, simple_af):
        agent = simple_af.entry_agent
        ctx = build_context(simple_af, agent)
        # Support both dict and Pydantic model
        if hasattr(ctx, "agent"):
            assert ctx.agent.name == "test-agent"
        else:
            assert ctx["agent"].name == "test-agent"

    def test_agent_name(self, simple_af):
        ctx = build_context(simple_af, simple_af.entry_agent)
        val = ctx.agent_name if hasattr(ctx, "agent_name") else ctx["agent_name"]
        assert val == "test-agent"

    def test_max_tokens(self, simple_af):
        ctx = build_context(simple_af, simple_af.entry_agent)
        val = ctx.max_tokens if hasattr(ctx, "max_tokens") else ctx["max_tokens"]
        assert val == 8192

    def test_base_image_default(self, simple_af):
        ctx = build_context(simple_af, simple_af.entry_agent)
        val = ctx.base_image if hasattr(ctx, "base_image") else ctx["base_image"]
        assert val == "python:3.12-slim"


class TestBuildContextMCPGateway:
    def test_use_mcp_gateway_with_explicit_config(self):
        agent = AgentDef(
            name="test",
            tools=[Tool(name="search", source="mcp://brave-search")],
        )
        af = AgentFile(
            agents={"test": agent},
            governance=Governance(),
            mcp_gateway=MCPGatewayConfig(url="http://gw:8080", token="tok", org_id="org1"),
        )
        ctx = build_context(af, agent)
        use_gw = ctx.use_mcp_gateway if hasattr(ctx, "use_mcp_gateway") else ctx["use_mcp_gateway"]
        gw_url = ctx.mcp_gateway_url if hasattr(ctx, "mcp_gateway_url") else ctx["mcp_gateway_url"]
        gw_org = ctx.mcp_gateway_org_id if hasattr(ctx, "mcp_gateway_org_id") else ctx["mcp_gateway_org_id"]
        assert use_gw is True
        assert gw_url == "http://gw:8080"
        assert gw_org == "org1"

    def test_localhost_rewritten_to_docker_internal(self):
        agent = AgentDef(
            name="test",
            tools=[Tool(name="search", source="mcp://brave-search")],
        )
        af = AgentFile(
            agents={"test": agent},
            governance=Governance(),
            mcp_gateway=MCPGatewayConfig(url="http://localhost:8080"),
        )
        ctx = build_context(af, agent)
        gw_url = ctx.mcp_gateway_url if hasattr(ctx, "mcp_gateway_url") else ctx["mcp_gateway_url"]
        assert "host.docker.internal" in gw_url
        assert "localhost" not in gw_url

    def test_mcp_tools_enable_gateway_even_without_config(self):
        agent = AgentDef(
            name="test",
            tools=[Tool(name="search", source="mcp://brave-search")],
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())
        ctx = build_context(af, agent)
        use_gw = ctx.use_mcp_gateway if hasattr(ctx, "use_mcp_gateway") else ctx["use_mcp_gateway"]
        assert use_gw is True

    def test_no_mcp_tools_no_gateway(self):
        agent = AgentDef(
            name="test",
            tools=[Tool(name="github", source="composio://GITHUB")],
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())
        ctx = build_context(af, agent)
        use_gw = ctx.use_mcp_gateway if hasattr(ctx, "use_mcp_gateway") else ctx["use_mcp_gateway"]
        assert use_gw is False


class TestBuildContextComposio:
    def test_has_composio_tools(self):
        agent = AgentDef(
            name="test",
            tools=[
                Tool(name="github", source="composio://GITHUB", actions=["LIST_REPOS"]),
                Tool(name="search", source="mcp://brave-search"),
            ],
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())
        ctx = build_context(af, agent)
        has = ctx.has_composio_tools if hasattr(ctx, "has_composio_tools") else ctx["has_composio_tools"]
        defs = ctx.composio_tool_defs if hasattr(ctx, "composio_tool_defs") else ctx["composio_tool_defs"]
        assert has is True
        assert len(defs) == 1
        assert defs[0]["app"] == "GITHUB"
        assert defs[0]["actions"] == ["LIST_REPOS"]


class TestBuildContextExecution:
    def test_planned_execution(self):
        agent = AgentDef(
            name="test",
            tools=[Tool(name="t", source="mcp://t")],
            execution=Execution(mode="planned", verify_steps=True, max_steps=5),
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())
        ctx = build_context(af, agent)
        planned = ctx.has_planned_execution if hasattr(ctx, "has_planned_execution") else ctx["has_planned_execution"]
        verify = ctx.verify_steps if hasattr(ctx, "verify_steps") else ctx["verify_steps"]
        steps = ctx.max_plan_steps if hasattr(ctx, "max_plan_steps") else ctx["max_plan_steps"]
        assert planned is True
        assert verify is True
        assert steps == 5

    def test_thinking_enabled(self):
        agent = AgentDef(
            name="test",
            tools=[Tool(name="t", source="mcp://t")],
            execution=Execution(
                thinking=ThinkingConfig(enabled=True, model="gpt-4o", max_tokens=1024),
            ),
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())
        ctx = build_context(af, agent)
        has_thinking = ctx.has_thinking_step if hasattr(ctx, "has_thinking_step") else ctx["has_thinking_step"]
        model = ctx.thinking_model if hasattr(ctx, "thinking_model") else ctx["thinking_model"]
        assert has_thinking is True
        assert model == "gpt-4o"

    def test_verifier_defaults_to_agent_provider(self):
        agent = AgentDef(
            name="test",
            provider="openai",
            model="gpt-4o",
            tools=[Tool(name="t", source="mcp://t")],
            execution=Execution(verify_steps=True),
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())
        ctx = build_context(af, agent)
        vp = ctx.verifier_provider if hasattr(ctx, "verifier_provider") else ctx["verifier_provider"]
        vm = ctx.verifier_model if hasattr(ctx, "verifier_model") else ctx["verifier_model"]
        assert vp == "openai"
        assert vm == "gpt-4o"


class TestBuildContextTriggers:
    def test_webhook_triggers(self):
        agent = AgentDef(
            name="test",
            tools=[Tool(name="t", source="mcp://t")],
            triggers=[Trigger(type="webhook", endpoint="/run", port=9100)],
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())
        ctx = build_context(af, agent)
        has_wh = ctx.has_webhook_triggers if hasattr(ctx, "has_webhook_triggers") else ctx["has_webhook_triggers"]
        defs = ctx.webhook_trigger_defs if hasattr(ctx, "webhook_trigger_defs") else ctx["webhook_trigger_defs"]
        port = ctx.webhook_port if hasattr(ctx, "webhook_port") else ctx["webhook_port"]
        assert has_wh is True
        assert len(defs) == 1
        assert defs[0]["endpoint"] == "/run"
        assert port == 9100

    def test_schedule_triggers(self):
        agent = AgentDef(
            name="test",
            tools=[Tool(name="t", source="mcp://t")],
            triggers=[Trigger(type="schedule", cron="0 * * * *", message="do it")],
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())
        ctx = build_context(af, agent)
        has_sched = ctx.has_schedule_triggers if hasattr(ctx, "has_schedule_triggers") else ctx["has_schedule_triggers"]
        defs = ctx.schedule_trigger_defs if hasattr(ctx, "schedule_trigger_defs") else ctx["schedule_trigger_defs"]
        assert has_sched is True
        assert defs[0]["cron"] == "0 * * * *"
        assert defs[0]["message"] == "do it"

    def test_no_triggers(self, simple_af):
        ctx = build_context(simple_af, simple_af.entry_agent)
        has_any = ctx.has_any_triggers if hasattr(ctx, "has_any_triggers") else ctx["has_any_triggers"]
        assert has_any is False


class TestBuildContextCollaborators:
    def test_has_collaborators(self):
        agent_a = AgentDef(
            name="a",
            tools=[Tool(name="t", source="mcp://t")],
            collaborators=["b"],
        )
        agent_b = AgentDef(
            name="b",
            tools=[Tool(name="t", source="mcp://t")],
        )
        af = AgentFile(
            agents={"a": agent_a, "b": agent_b},
            governance=Governance(),
        )
        ctx = build_context(af, agent_a)
        has = ctx.has_collaborators if hasattr(ctx, "has_collaborators") else ctx["has_collaborators"]
        collab = ctx.collaborators if hasattr(ctx, "collaborators") else ctx["collaborators"]
        assert has is True
        assert collab == ["b"]

    def test_auto_routing(self):
        agent = AgentDef(
            name="a",
            tools=[Tool(name="t", source="mcp://t")],
            collaborators=["b"],
            routing_mode="auto",
            routing_model="claude-haiku-4-5-20251001",
        )
        af = AgentFile(
            agents={"a": agent, "b": AgentDef(name="b", tools=[Tool(name="t", source="mcp://t")])},
            governance=Governance(),
        )
        ctx = build_context(af, agent)
        has_auto = ctx.has_auto_routing if hasattr(ctx, "has_auto_routing") else ctx["has_auto_routing"]
        rm = ctx.routing_model if hasattr(ctx, "routing_model") else ctx["routing_model"]
        assert has_auto is True
        assert rm == "claude-haiku-4-5-20251001"


class TestBuildContextOutputType:
    def test_output_type_set(self):
        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        agent = AgentDef(
            name="test",
            tools=[Tool(name="t", source="mcp://t")],
            output_type=schema,
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())
        ctx = build_context(af, agent)
        has_ot = ctx.has_output_type if hasattr(ctx, "has_output_type") else ctx["has_output_type"]
        ot_schema = ctx.output_type_schema if hasattr(ctx, "output_type_schema") else ctx["output_type_schema"]
        assert has_ot is True
        assert json.loads(ot_schema) == schema

    def test_no_output_type(self, simple_af):
        ctx = build_context(simple_af, simple_af.entry_agent)
        has_ot = ctx.has_output_type if hasattr(ctx, "has_output_type") else ctx["has_output_type"]
        assert has_ot is False


class TestBuildContextVolumes:
    def test_s3_volumes_detected(self):
        vol = VolumeSpec(name="out", provider="s3", bucket="my-bucket")
        agent = AgentDef(
            name="test",
            tools=[Tool(name="t", source="mcp://t")],
            volume_refs=[vol],
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())
        ctx = build_context(af, agent)
        has_s3 = ctx.has_s3_volumes if hasattr(ctx, "has_s3_volumes") else ctx["has_s3_volumes"]
        assert has_s3 is True


# ---------------------------------------------------------------------------
# _validate_local_path — path traversal prevention
# ---------------------------------------------------------------------------

from pathlib import Path
import tempfile
import os

from agentfile.core.template_context import _validate_local_path


class TestValidateLocalPath:
    """Unit tests for _validate_local_path path-traversal guard."""

    def test_simple_relative_path(self, tmp_path):
        """A normal relative path inside the base dir should resolve fine."""
        child = tmp_path / "tools" / "my_tool.py"
        child.parent.mkdir(parents=True, exist_ok=True)
        child.touch()
        result = _validate_local_path(tmp_path, "tools/my_tool.py")
        assert result == child.resolve()

    def test_dot_slash_prefix(self, tmp_path):
        """./relative paths are standard in agentfile.yaml."""
        child = tmp_path / "my_tool.py"
        child.touch()
        result = _validate_local_path(tmp_path, "./my_tool.py")
        assert result == child.resolve()

    def test_traversal_parent_dir_rejected(self, tmp_path):
        """../../etc/passwd style traversal must be blocked."""
        with pytest.raises(ValueError, match="escapes the project directory"):
            _validate_local_path(tmp_path, "../../etc/passwd")

    def test_traversal_dotdot_then_back_allowed(self, tmp_path):
        """../base_name/file resolves back inside — should be allowed."""
        child = tmp_path / "tool.py"
        child.touch()
        # e.g. base=/a/b/project, path=../project/tool.py => resolves inside
        result = _validate_local_path(tmp_path, f"../{tmp_path.name}/tool.py")
        assert result == child.resolve()

    def test_traversal_absolute_outside_rejected(self, tmp_path):
        """/etc/passwd as source should be rejected."""
        with pytest.raises(ValueError, match="escapes the project directory"):
            _validate_local_path(tmp_path, "/etc/passwd")

    def test_traversal_deep_escape_rejected(self, tmp_path):
        """Many levels of ../ must still be caught."""
        with pytest.raises(ValueError, match="escapes the project directory"):
            _validate_local_path(tmp_path, "../" * 20 + "etc/shadow")

    def test_symlink_escape_rejected(self, tmp_path):
        """A symlink pointing outside the base dir must be caught."""
        link = tmp_path / "evil_link"
        link.symlink_to("/etc")
        with pytest.raises(ValueError, match="escapes the project directory"):
            _validate_local_path(tmp_path, "evil_link/passwd")

    def test_file_in_nested_subdir(self, tmp_path):
        """Deeply nested but valid paths work."""
        nested = tmp_path / "a" / "b" / "c" / "tool.py"
        nested.parent.mkdir(parents=True)
        nested.touch()
        result = _validate_local_path(tmp_path, "a/b/c/tool.py")
        assert result == nested.resolve()

    def test_base_dir_itself_is_valid(self, tmp_path):
        """Edge case: source='.' resolves to the base dir itself."""
        result = _validate_local_path(tmp_path, ".")
        assert result == tmp_path.resolve()

    def test_error_message_contains_path_info(self, tmp_path):
        """Error message should help the user identify the problem."""
        with pytest.raises(ValueError) as exc_info:
            _validate_local_path(tmp_path, "../../secret.py")
        msg = str(exc_info.value)
        assert "../../secret.py" in msg
        assert str(tmp_path.resolve()) in msg


class TestBuildContextPathTraversal:
    """Integration tests: build_context rejects path traversal in tools/skills."""

    def test_local_tool_traversal_rejected(self, tmp_path):
        """A local tool with ../ traversal must raise ValueError.

        Source must start with './' to be classified as local by Tool.is_local().
        """
        agent = AgentDef(
            name="bad-agent",
            tools=[Tool(name="evil", source="./../../etc/passwd")],
        )
        af = AgentFile(agents={"bad-agent": agent}, governance=Governance())
        with pytest.raises(ValueError, match="escapes the project directory"):
            build_context(af, agent, agentfile_dir=str(tmp_path))

    def test_local_tool_valid_path_accepted(self, tmp_path):
        """A local tool with a safe relative path should work."""
        tool_file = tmp_path / "my_tool.py"
        tool_file.write_text("# tool")
        agent = AgentDef(
            name="good-agent",
            tools=[Tool(name="my_tool", source="./my_tool.py")],
        )
        af = AgentFile(agents={"good-agent": agent}, governance=Governance())
        ctx = build_context(af, agent, agentfile_dir=str(tmp_path))
        paths = ctx.local_source_paths if hasattr(ctx, "local_source_paths") else ctx["local_source_paths"]
        assert len(paths) == 1
        assert paths[0] == str(tool_file.resolve())
