"""Tests for agentfile.core.models — data models, parsing, validation."""

from __future__ import annotations


import pytest
import yaml

from agentfile.core.models import (
    AgentDef,
    AgentFile,
    Execution,
    Governance,
    MCPGatewayConfig,
    Resources,
    ThinkingConfig,
    Tool,
    Trigger,
    VolumeSpec,
    _deep_merge,
    _parse_agent_def,
    _parse_execution,
    _parse_governance,
    _parse_memory,
    LATEST_SCHEMA_VERSION,
)


# ── _deep_merge ──────────────────────────────────────────────────────────────


class TestDeepMerge:
    def test_flat_override(self):
        base = {"a": 1, "b": 2}
        override = {"b": 3, "c": 4}
        result = _deep_merge(base, override)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_nested_dict_merge(self):
        base = {"a": {"x": 1, "y": 2}}
        override = {"a": {"y": 3, "z": 4}}
        result = _deep_merge(base, override)
        assert result == {"a": {"x": 1, "y": 3, "z": 4}}

    def test_does_not_mutate_base(self):
        base = {"a": {"x": 1}}
        override = {"a": {"y": 2}}
        _deep_merge(base, override)
        assert base == {"a": {"x": 1}}

    def test_list_replaces_not_appends(self):
        base = {"items": [1, 2]}
        override = {"items": [3]}
        result = _deep_merge(base, override)
        assert result == {"items": [3]}

    def test_empty_override(self):
        base = {"a": 1}
        assert _deep_merge(base, {}) == {"a": 1}

    def test_empty_base(self):
        assert _deep_merge({}, {"a": 1}) == {"a": 1}

    def test_deeply_nested(self):
        base = {"a": {"b": {"c": 1, "d": 2}}}
        override = {"a": {"b": {"c": 99}}}
        result = _deep_merge(base, override)
        assert result == {"a": {"b": {"c": 99, "d": 2}}}


# ── _parse_memory ────────────────────────────────────────────────────────────


class TestParseMemory:
    def test_gibibytes(self):
        assert _parse_memory("4Gi") == 4 * 1024**3

    def test_mebibytes(self):
        assert _parse_memory("512Mi") == 512 * 1024**2

    def test_kibibytes(self):
        assert _parse_memory("100Ki") == 100 * 1024

    def test_si_gigabytes(self):
        assert _parse_memory("4g") == 4 * 10**9

    def test_si_megabytes(self):
        assert _parse_memory("512m") == 512 * 10**6

    def test_si_kilobytes(self):
        assert _parse_memory("100k") == 100 * 10**3

    def test_plain_bytes(self):
        assert _parse_memory("1024") == 1024

    def test_whitespace_stripped(self):
        assert _parse_memory("  2Gi  ") == 2 * 1024**3

    def test_uppercase_G(self):
        assert _parse_memory("1G") == 1 * 10**9

    def test_fractional_gibibytes(self):
        assert _parse_memory("1.5Gi") == int(1.5 * 1024**3)


# ── Tool ─────────────────────────────────────────────────────────────────────


class TestTool:
    def test_is_mcp(self, mcp_tool):
        assert mcp_tool.is_mcp() is True
        assert mcp_tool.is_composio() is False
        assert mcp_tool.is_local() is False

    def test_is_composio(self, composio_tool):
        assert composio_tool.is_composio() is True
        assert composio_tool.is_mcp() is False

    def test_is_local_relative(self, local_tool):
        assert local_tool.is_local() is True

    def test_is_local_absolute(self):
        tool = Tool(name="abs", source="/usr/tools/foo.py")
        assert tool.is_local() is True

    def test_mcp_name(self, mcp_tool):
        assert mcp_tool.mcp_name == "brave-search"

    def test_mcp_name_none_for_non_mcp(self, composio_tool):
        assert composio_tool.mcp_name is None

    def test_composio_app(self, composio_tool):
        assert composio_tool.composio_app == "GITHUB"

    def test_composio_app_none_for_non_composio(self, mcp_tool):
        assert mcp_tool.composio_app is None

    def test_frozen(self, mcp_tool):
        with pytest.raises(Exception):
            mcp_tool.name = "changed"

    def test_default_actions_empty(self):
        tool = Tool(name="t", source="mcp://test")
        assert tool.actions == []


# ── AgentDef ─────────────────────────────────────────────────────────────────


class TestAgentDef:
    def test_image_name_default_tag(self, basic_agent):
        assert basic_agent.image_name() == "ninetrix/test-agent:latest"

    def test_image_name_custom_tag(self, basic_agent):
        assert basic_agent.image_name("v1.2.3") == "ninetrix/test-agent:v1.2.3"

    def test_image_name_spaces_replaced(self):
        agent = AgentDef(name="My Agent", tools=[])
        assert agent.image_name() == "ninetrix/my-agent:latest"

    def test_system_prompt_all_parts(self):
        agent = AgentDef(
            name="test",
            role="analyst",
            goal="find insights",
            instructions="Look carefully.",
            constraints=["Be accurate", "Be fast"],
        )
        prompt = agent.system_prompt
        assert "You are a analyst." in prompt
        assert "Goal: find insights" in prompt
        assert "Instructions:\nLook carefully." in prompt
        assert "- Be accurate" in prompt
        assert "- Be fast" in prompt

    def test_system_prompt_empty_when_no_fields(self):
        agent = AgentDef(name="test")
        assert agent.system_prompt == ""

    def test_system_prompt_partial(self):
        agent = AgentDef(name="test", role="helper")
        assert agent.system_prompt == "You are a helper."

    def test_webhook_triggers_filter(self):
        agent = AgentDef(
            name="test",
            triggers=[
                Trigger(type="webhook", endpoint="/run"),
                Trigger(type="schedule", cron="0 * * * *"),
                Trigger(type="webhook", endpoint="/health"),
            ],
        )
        webhooks = agent.webhook_triggers()
        assert len(webhooks) == 2
        assert all(t.type == "webhook" for t in webhooks)

    def test_schedule_triggers_filter(self):
        agent = AgentDef(
            name="test",
            triggers=[
                Trigger(type="webhook", endpoint="/run"),
                Trigger(type="schedule", cron="0 * * * *"),
            ],
        )
        schedules = agent.schedule_triggers()
        assert len(schedules) == 1
        assert schedules[0].cron == "0 * * * *"

    def test_default_values(self):
        agent = AgentDef(name="test")
        assert agent.provider == "anthropic"
        assert agent.model == "claude-sonnet-4-6"
        assert agent.temperature == 0.2
        assert agent.max_tokens == 8192
        assert agent.max_turns == 20
        assert agent.tool_timeout == 30
        assert agent.history_window_tokens == 90_000
        assert agent.routing_mode == "agent"


# ── Sub-models defaults ──────────────────────────────────────────────────────


class TestSubModelDefaults:
    def test_governance_defaults(self):
        gov = Governance()
        assert gov.max_budget_per_run == 1.0
        assert gov.rate_limit == "10_requests_per_minute"
        assert gov.human_approval.enabled is True

    def test_execution_defaults(self):
        exe = Execution()
        assert exe.mode == "direct"
        assert exe.verify_steps is False
        assert exe.max_steps == 10
        assert exe.on_step_failure == "continue"
        assert exe.durability is True

    def test_resources_defaults(self):
        res = Resources()
        assert res.cpu is None
        assert res.memory is None
        assert res.base_image is None
        assert res.warm_pool is False

    def test_trigger_defaults(self):
        t = Trigger(type="webhook")
        assert t.port == 9100
        assert t.message == ""
        assert t.target_agent is None

    def test_thinking_config_defaults(self):
        tc = ThinkingConfig()
        assert tc.enabled is False
        assert tc.max_tokens == 2048

    def test_volume_spec_defaults(self):
        vs = VolumeSpec()
        assert vs.provider == "local"
        assert vs.container_path == "/workspace"
        assert vs.read_only is False
        assert vs.sync == "bidirectional"

    def test_mcp_gateway_config_alias(self):
        gw = MCPGatewayConfig(url="http://test:8080", workspace_id="ws-123")
        assert gw.org_id == "ws-123"


# ── _parse_governance ────────────────────────────────────────────────────────


class TestParseGovernance:
    def test_full(self):
        raw = {
            "max_budget_per_run": 5.0,
            "budget_warning_usd": 3.0,
            "human_approval": {"enabled": True, "actions": ["shell_exec"], "notify_url": "http://hook"},
            "rate_limit": "20_requests_per_minute",
        }
        gov = _parse_governance(raw)
        assert gov.max_budget_per_run == 5.0
        assert gov.budget_warning_usd == 3.0
        assert gov.human_approval.enabled is True
        assert gov.human_approval.actions == ["shell_exec"]
        assert gov.human_approval.notify_url == "http://hook"
        assert gov.rate_limit == "20_requests_per_minute"

    def test_empty_uses_defaults(self):
        gov = _parse_governance({})
        assert gov.max_budget_per_run == 1.0
        assert gov.human_approval.enabled is True
        assert gov.human_approval.actions == []

    def test_null_human_approval(self):
        gov = _parse_governance({"human_approval": None})
        assert gov.human_approval.actions == []


# ── _parse_execution ─────────────────────────────────────────────────────────


class TestParseExecution:
    def test_planned_mode_with_verifier(self):
        raw = {
            "mode": "planned",
            "verify_steps": True,
            "max_steps": 15,
            "on_step_failure": "retry_once",
            "verifier": {"provider": "anthropic", "model": "claude-haiku-4-5-20251001", "max_tokens": 256},
        }
        exe = _parse_execution(raw)
        assert exe.mode == "planned"
        assert exe.verify_steps is True
        assert exe.max_steps == 15
        assert exe.on_step_failure == "retry_once"
        assert exe.verifier.provider == "anthropic"
        assert exe.verifier.model == "claude-haiku-4-5-20251001"

    def test_thinking_as_bool(self):
        raw = {"thinking": True}
        exe = _parse_execution(raw)
        assert exe.thinking.enabled is True

    def test_thinking_as_dict(self):
        raw = {"thinking": {"enabled": True, "model": "gpt-4o", "max_tokens": 1024}}
        exe = _parse_execution(raw)
        assert exe.thinking.enabled is True
        assert exe.thinking.model == "gpt-4o"
        assert exe.thinking.max_tokens == 1024

    def test_thinking_absent(self):
        exe = _parse_execution({})
        assert exe.thinking.enabled is False

    def test_defaults(self):
        exe = _parse_execution({})
        assert exe.mode == "direct"
        assert exe.durability is True


# ── _parse_agent_def ─────────────────────────────────────────────────────────


class TestParseAgentDef:
    def test_basic_parsing(self):
        raw = {
            "metadata": {"description": "test", "role": "helper"},
            "runtime": {"provider": "openai", "model": "gpt-4o", "temperature": 0.5},
            "tools": [{"name": "search", "source": "mcp://test"}],
        }
        agent = _parse_agent_def("my-agent", raw)
        assert agent.name == "my-agent"
        assert agent.description == "test"
        assert agent.role == "helper"
        assert agent.provider == "openai"
        assert agent.model == "gpt-4o"
        assert agent.temperature == 0.5
        assert len(agent.tools) == 1

    def test_non_dict_raises(self):
        with pytest.raises(ValueError, match="expected a mapping"):
            _parse_agent_def("bad", "not a dict")

    def test_volumes_string_refs(self):
        raw = {
            "metadata": {},
            "runtime": {},
            "tools": [],
            "volumes": ["data-vol", "code-vol"],
        }
        agent = _parse_agent_def("test", raw)
        assert agent.volume_refs == ["data-vol", "code-vol"]

    def test_volumes_inline_dict(self):
        raw = {
            "metadata": {},
            "runtime": {},
            "tools": [],
            "volumes": [{"host_path": "/tmp/data", "container_path": "/data"}],
        }
        agent = _parse_agent_def("test", raw)
        assert len(agent.volume_refs) == 1
        vol = agent.volume_refs[0]
        assert isinstance(vol, VolumeSpec)
        assert vol.host_path == "/tmp/data"

    def test_collaborators(self):
        raw = {
            "metadata": {},
            "runtime": {},
            "tools": [],
            "collaborators": ["agent-a", "agent-b"],
        }
        agent = _parse_agent_def("test", raw)
        assert agent.collaborators == ["agent-a", "agent-b"]

    def test_routing(self):
        raw = {
            "metadata": {},
            "runtime": {},
            "tools": [],
            "routing": {"mode": "auto", "model": "haiku", "provider": "anthropic"},
        }
        agent = _parse_agent_def("test", raw)
        assert agent.routing_mode == "auto"
        assert agent.routing_model == "haiku"

    def test_output_type(self):
        raw = {
            "metadata": {},
            "runtime": {},
            "tools": [],
            "output_type": {"type": "object", "properties": {"x": {"type": "string"}}},
        }
        agent = _parse_agent_def("test", raw)
        assert agent.output_type == {"type": "object", "properties": {"x": {"type": "string"}}}


# ── AgentFile ────────────────────────────────────────────────────────────────


class TestAgentFile:
    def test_from_path_minimal(self, minimal_yaml):
        af = AgentFile.from_path(minimal_yaml)
        assert len(af.agents) == 1
        assert "test-agent" in af.agents
        assert af.schema_version == "1.1"

    def test_from_path_multi_agent(self, multi_agent_yaml):
        af = AgentFile.from_path(multi_agent_yaml)
        assert len(af.agents) == 2
        assert af.is_multi_agent is True

    def test_from_path_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="not found"):
            AgentFile.from_path(tmp_path / "missing.yaml")

    def test_from_path_non_yaml(self, tmp_path):
        p = tmp_path / "file.txt"
        p.write_text("hello")
        with pytest.raises(ValueError, match=".yaml"):
            AgentFile.from_path(p)

    def test_from_path_empty_file(self, tmp_path):
        p = tmp_path / "empty.yaml"
        p.write_text("")
        with pytest.raises(ValueError, match="mapping"):
            AgentFile.from_path(p)

    def test_from_path_missing_agents_key(self, invalid_no_agents_yaml):
        with pytest.raises(ValueError, match="agents"):
            AgentFile.from_path(invalid_no_agents_yaml)

    def test_is_multi_agent_false_for_single(self, minimal_yaml):
        af = AgentFile.from_path(minimal_yaml)
        assert af.is_multi_agent is False

    def test_entry_agent_is_first(self, multi_agent_yaml):
        af = AgentFile.from_path(multi_agent_yaml)
        assert af.entry_agent.name == "researcher"

    def test_effective_execution_agent_level(self, basic_agentfile, basic_agent):
        agent_exec = Execution(mode="planned")
        basic_agent.execution = agent_exec
        result = basic_agentfile.effective_execution(basic_agent)
        assert result.mode == "planned"

    def test_effective_execution_global_fallback(self, basic_agentfile, basic_agent):
        basic_agentfile.execution = Execution(mode="planned")
        result = basic_agentfile.effective_execution(basic_agent)
        assert result.mode == "planned"

    def test_effective_execution_default(self, basic_agentfile, basic_agent):
        result = basic_agentfile.effective_execution(basic_agent)
        assert result.mode == "direct"

    def test_effective_governance_agent_level(self, basic_agentfile, basic_agent):
        agent_gov = Governance(max_budget_per_run=10.0)
        basic_agent.governance = agent_gov
        result = basic_agentfile.effective_governance(basic_agent)
        assert result.max_budget_per_run == 10.0

    def test_effective_governance_global_fallback(self, basic_agentfile, basic_agent):
        result = basic_agentfile.effective_governance(basic_agent)
        assert result.max_budget_per_run == 1.0

    def test_effective_triggers_combines_agent_and_root(self):
        agent = AgentDef(
            name="entry",
            triggers=[Trigger(type="webhook", endpoint="/agent-hook")],
        )
        af = AgentFile(
            agents={"entry": agent},
            governance=Governance(),
            triggers=[
                Trigger(type="webhook", endpoint="/root-hook"),
                Trigger(type="webhook", endpoint="/targeted", target_agent="entry"),
            ],
        )
        triggers = af.effective_triggers(agent)
        endpoints = [t.endpoint for t in triggers]
        assert "/agent-hook" in endpoints
        assert "/root-hook" in endpoints  # no target_agent, entry agent gets it
        assert "/targeted" in endpoints

    def test_effective_triggers_root_targeted_at_non_entry(self):
        entry = AgentDef(name="entry")
        other = AgentDef(name="other")
        af = AgentFile(
            agents={"entry": entry, "other": other},
            governance=Governance(),
            triggers=[Trigger(type="schedule", cron="0 * * * *", target_agent="other")],
        )
        # Entry agent should NOT get the trigger targeted at "other"
        entry_triggers = af.effective_triggers(entry)
        assert len(entry_triggers) == 0
        # "other" should get it
        other_triggers = af.effective_triggers(other)
        assert len(other_triggers) == 1

    def test_effective_volumes_deduplication(self):
        vol = VolumeSpec(name="data", host_path="/tmp/data")
        agent = AgentDef(name="test", volume_refs=["data", "data"])
        af = AgentFile(
            agents={"test": agent},
            governance=Governance(),
            volumes={"data": vol},
        )
        result = af.effective_volumes(agent)
        assert len(result) == 1

    def test_effective_volumes_inline_and_ref(self):
        global_vol = VolumeSpec(name="shared", host_path="/tmp/shared")
        inline_vol = VolumeSpec(name="local", host_path="/tmp/local")
        agent = AgentDef(name="test", volume_refs=["shared", inline_vol])
        af = AgentFile(
            agents={"test": agent},
            governance=Governance(),
            volumes={"shared": global_vol},
        )
        result = af.effective_volumes(agent)
        assert len(result) == 2

    def test_effective_volumes_missing_ref_skipped(self):
        agent = AgentDef(name="test", volume_refs=["nonexistent"])
        af = AgentFile(agents={"test": agent}, governance=Governance())
        result = af.effective_volumes(agent)
        assert len(result) == 0


# ── for_env ──────────────────────────────────────────────────────────────────


class TestForEnv:
    def test_applies_environment_overlay(self, full_featured_yaml):
        af = AgentFile.from_path(full_featured_yaml)
        af_prod = af.for_env("prod")
        assert af_prod.agents["main-agent"].temperature == 0.1

    def test_staging_overlay(self, full_featured_yaml):
        af = AgentFile.from_path(full_featured_yaml)
        af_staging = af.for_env("staging")
        assert af_staging.agents["main-agent"].temperature == 0.3

    def test_returns_self_when_env_is_none(self, full_featured_yaml):
        af = AgentFile.from_path(full_featured_yaml)
        result = af.for_env(None)
        assert result is af

    def test_returns_self_when_env_not_found(self, full_featured_yaml):
        af = AgentFile.from_path(full_featured_yaml)
        result = af.for_env("nonexistent")
        assert result is af


# ── validate_config ──────────────────────────────────────────────────────────


class TestValidateConfig:
    def test_valid_minimal(self, minimal_yaml):
        af = AgentFile.from_path(minimal_yaml)
        errors = af.validate_config()
        assert errors == []

    def test_valid_multi_agent(self, multi_agent_yaml):
        af = AgentFile.from_path(multi_agent_yaml)
        errors = af.validate_config()
        assert errors == []

    def test_no_agents(self):
        af = AgentFile(agents={}, governance=Governance())
        errors = af.validate_config()
        assert any("at least one agent" in e for e in errors)

    def test_unknown_provider(self):
        agent = AgentDef(
            name="test",
            provider="unknown-provider",
            tools=[Tool(name="t", source="mcp://test")],
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())
        errors = af.validate_config()
        assert any("not a known provider" in e for e in errors)

    def test_temperature_out_of_range(self):
        agent = AgentDef(
            name="test",
            temperature=3.0,
            tools=[Tool(name="t", source="mcp://test")],
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())
        errors = af.validate_config()
        assert any("temperature" in e for e in errors)

    def test_no_tools(self):
        agent = AgentDef(name="test", tools=[])
        af = AgentFile(agents={"test": agent}, governance=Governance())
        errors = af.validate_config()
        assert any("at least one tool" in e for e in errors)

    def test_tool_missing_name(self):
        agent = AgentDef(name="test", tools=[Tool(name="", source="mcp://test")])
        af = AgentFile(agents={"test": agent}, governance=Governance())
        errors = af.validate_config()
        assert any("name is required" in e for e in errors)

    def test_tool_missing_source(self):
        agent = AgentDef(name="test", tools=[Tool(name="t", source="")])
        af = AgentFile(agents={"test": agent}, governance=Governance())
        errors = af.validate_config()
        assert any("source is required" in e for e in errors)

    def test_composio_empty_app(self):
        agent = AgentDef(name="test", tools=[Tool(name="t", source="composio://")])
        af = AgentFile(agents={"test": agent}, governance=Governance())
        errors = af.validate_config()
        assert any("app name is missing" in e for e in errors)

    def test_webhook_trigger_missing_endpoint(self):
        agent = AgentDef(
            name="test",
            tools=[Tool(name="t", source="mcp://test")],
            triggers=[Trigger(type="webhook")],
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())
        errors = af.validate_config()
        assert any("endpoint" in e for e in errors)

    def test_schedule_trigger_missing_cron(self):
        agent = AgentDef(
            name="test",
            tools=[Tool(name="t", source="mcp://test")],
            triggers=[Trigger(type="schedule")],
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())
        errors = af.validate_config()
        assert any("cron" in e for e in errors)

    def test_self_reference_collaborator(self):
        agent = AgentDef(
            name="test",
            tools=[Tool(name="t", source="mcp://test")],
            collaborators=["test"],
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())
        errors = af.validate_config()
        assert any("self-reference" in e for e in errors)

    def test_unknown_collaborator(self):
        agent = AgentDef(
            name="test",
            tools=[Tool(name="t", source="mcp://test")],
            collaborators=["nonexistent"],
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())
        errors = af.validate_config()
        assert any("does not reference a known agent" in e for e in errors)

    def test_zero_budget(self):
        agent = AgentDef(
            name="test",
            tools=[Tool(name="t", source="mcp://test")],
        )
        gov = Governance(max_budget_per_run=0.0)
        af = AgentFile(agents={"test": agent}, governance=gov)
        errors = af.validate_config()
        assert any("max_budget_per_run" in e for e in errors)

    def test_root_trigger_invalid_target(self):
        agent = AgentDef(
            name="test",
            tools=[Tool(name="t", source="mcp://test")],
        )
        af = AgentFile(
            agents={"test": agent},
            governance=Governance(),
            triggers=[Trigger(type="webhook", endpoint="/run", target_agent="ghost")],
        )
        errors = af.validate_config()
        assert any("does not reference a known agent" in e for e in errors)

    def test_validate_alias(self, minimal_yaml):
        af = AgentFile.from_path(minimal_yaml)
        assert af.validate() == af.validate_config()


# ── MCP Gateway parsing ─────────────────────────────────────────────────────


class TestMCPGatewayParsing:
    def test_gateway_parsed(self, full_featured_yaml):
        af = AgentFile.from_path(full_featured_yaml)
        assert af.mcp_gateway is not None
        assert af.mcp_gateway.url == "http://localhost:8080"
        assert af.mcp_gateway.token == "test-token"
        assert af.mcp_gateway.org_id == "test-org"

    def test_no_gateway(self, minimal_yaml):
        af = AgentFile.from_path(minimal_yaml)
        assert af.mcp_gateway is None

    def test_workspace_id_migrated(self, old_schema_yaml, capsys):
        af = AgentFile.from_path(old_schema_yaml)
        assert af.mcp_gateway is not None
        assert af.mcp_gateway.org_id == "old-workspace"
        captured = capsys.readouterr()
        assert "deprecated" in captured.err


# ── Schema version handling ──────────────────────────────────────────────────


class TestSchemaVersion:
    def test_latest_version(self, minimal_yaml):
        af = AgentFile.from_path(minimal_yaml)
        assert af.schema_version == LATEST_SCHEMA_VERSION

    def test_old_version_warning(self, old_schema_yaml, capsys):
        AgentFile.from_path(old_schema_yaml)
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "ninetrix migrate" in captured.err

    def test_version_field_alias(self, tmp_path):
        """The parser accepts 'version' as an alias for 'schema_version'."""
        p = tmp_path / "test.yaml"
        p.write_text(yaml.dump({
            "version": "1.0",
            "agents": {
                "a": {
                    "metadata": {},
                    "runtime": {},
                    "tools": [{"name": "t", "source": "mcp://t"}],
                }
            },
        }))
        af = AgentFile.from_path(p)
        assert af.schema_version == "1.0"


# ── Full-featured YAML ──────────────────────────────────────────────────────


class TestFullFeaturedYaml:
    def test_execution_planned(self, full_featured_yaml):
        af = AgentFile.from_path(full_featured_yaml)
        agent = af.agents["main-agent"]
        exe = af.effective_execution(agent)
        assert exe.mode == "planned"
        assert exe.verify_steps is True
        assert exe.thinking.enabled is True

    def test_governance_parsed(self, full_featured_yaml):
        af = AgentFile.from_path(full_featured_yaml)
        agent = af.agents["main-agent"]
        gov = af.effective_governance(agent)
        assert gov.max_budget_per_run == 5.0
        assert gov.human_approval.enabled is True
        assert "GITHUB_CREATE_ISSUE" in gov.human_approval.actions

    def test_resources_parsed(self, full_featured_yaml):
        af = AgentFile.from_path(full_featured_yaml)
        res = af.agents["main-agent"].resources
        assert res.cpu == 2.0
        assert res.memory == "4Gi"
        assert res.base_image == "python:3.12-slim"

    def test_volumes_parsed(self, full_featured_yaml):
        af = AgentFile.from_path(full_featured_yaml)
        assert "data-vol" in af.volumes
        assert af.volumes["data-vol"].read_only is True
        assert af.volumes["s3-vol"].provider == "s3"

    def test_triggers_parsed(self, full_featured_yaml):
        af = AgentFile.from_path(full_featured_yaml)
        agent = af.agents["main-agent"]
        assert len(agent.triggers) == 2
        webhooks = agent.webhook_triggers()
        schedules = agent.schedule_triggers()
        assert len(webhooks) == 1
        assert len(schedules) == 1

    def test_output_type_parsed(self, full_featured_yaml):
        af = AgentFile.from_path(full_featured_yaml)
        ot = af.agents["main-agent"].output_type
        assert ot is not None
        assert "summary" in ot["properties"]

    def test_environments_parsed(self, full_featured_yaml):
        af = AgentFile.from_path(full_featured_yaml)
        assert "prod" in af.environments
        assert "staging" in af.environments

    def test_composio_tool_parsed(self, full_featured_yaml):
        af = AgentFile.from_path(full_featured_yaml)
        tools = af.agents["main-agent"].tools
        composio = [t for t in tools if t.is_composio()]
        assert len(composio) == 1
        assert composio[0].composio_app == "GITHUB"
        assert "GITHUB_LIST_REPOS" in composio[0].actions


class TestTriggerDefaults:
    """Verify Trigger list fields use proper default_factory (no mutable default sharing)."""

    def test_channels_default_is_empty_list(self):
        t = Trigger(type="webhook")
        assert t.channels == []

    def test_allowed_ids_default_is_empty_list(self):
        t = Trigger(type="webhook")
        assert t.allowed_ids == []

    def test_separate_instances_have_independent_lists(self):
        """Two Trigger instances must not share the same list object."""
        t1 = Trigger(type="webhook")
        t2 = Trigger(type="webhook")
        # Pydantic frozen=True prevents mutation, but verify they're
        # separate objects at construction time.
        assert t1.channels is not t2.channels
        assert t1.allowed_ids is not t2.allowed_ids
