"""Integration tests for YAML → model → validate roundtrip."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agentfile.core.models import AgentFile, LATEST_SCHEMA_VERSION


class TestMinimalRoundtrip:
    def test_parse_and_validate(self, minimal_yaml):
        af = AgentFile.from_path(minimal_yaml)
        errors = af.validate_config()
        assert errors == []

    def test_agent_properties(self, minimal_yaml):
        af = AgentFile.from_path(minimal_yaml)
        agent = af.entry_agent
        assert agent.name == "test-agent"
        assert agent.provider == "anthropic"
        assert agent.model == "claude-sonnet-4-6"
        assert agent.temperature == 0.2
        assert agent.max_tokens == 4096
        assert agent.max_turns == 10
        assert agent.tool_timeout == 15
        assert agent.history_window_tokens == 50000

    def test_system_prompt_composed(self, minimal_yaml):
        af = AgentFile.from_path(minimal_yaml)
        prompt = af.entry_agent.system_prompt
        assert "test assistant" in prompt
        assert "answer questions" in prompt
        assert "Be helpful" in prompt
        assert "Be concise" in prompt


class TestMultiAgentRoundtrip:
    def test_both_agents_parsed(self, multi_agent_yaml):
        af = AgentFile.from_path(multi_agent_yaml)
        assert len(af.agents) == 2
        assert "researcher" in af.agents
        assert "writer" in af.agents

    def test_collaborators(self, multi_agent_yaml):
        af = AgentFile.from_path(multi_agent_yaml)
        assert "writer" in af.agents["researcher"].collaborators
        assert "researcher" in af.agents["writer"].collaborators

    def test_different_providers(self, multi_agent_yaml):
        af = AgentFile.from_path(multi_agent_yaml)
        assert af.agents["researcher"].provider == "anthropic"
        assert af.agents["writer"].provider == "openai"

    def test_root_triggers(self, multi_agent_yaml):
        af = AgentFile.from_path(multi_agent_yaml)
        assert len(af.triggers) == 1
        assert af.triggers[0].type == "webhook"
        assert af.triggers[0].endpoint == "/run"

    def test_entry_agent_gets_root_trigger(self, multi_agent_yaml):
        af = AgentFile.from_path(multi_agent_yaml)
        entry = af.entry_agent
        eff = af.effective_triggers(entry)
        assert any(t.endpoint == "/run" for t in eff)


class TestFullFeaturedRoundtrip:
    def test_all_sections_present(self, full_featured_yaml):
        af = AgentFile.from_path(full_featured_yaml)
        assert af.mcp_gateway is not None
        assert len(af.volumes) == 2
        assert len(af.environments) == 2

    def test_execution_chain(self, full_featured_yaml):
        af = AgentFile.from_path(full_featured_yaml)
        agent = af.agents["main-agent"]
        exe = af.effective_execution(agent)
        assert exe.mode == "planned"
        assert exe.verify_steps is True
        assert exe.verifier.provider == "anthropic"
        assert exe.thinking.enabled is True
        assert exe.thinking.max_tokens == 4096

    def test_governance_chain(self, full_featured_yaml):
        af = AgentFile.from_path(full_featured_yaml)
        agent = af.agents["main-agent"]
        gov = af.effective_governance(agent)
        assert gov.max_budget_per_run == 5.0
        assert "GITHUB_CREATE_ISSUE" in gov.human_approval.actions

    def test_environment_overlay(self, full_featured_yaml):
        af = AgentFile.from_path(full_featured_yaml)
        af_prod = af.for_env("prod")
        assert af_prod.agents["main-agent"].temperature == 0.1
        # Other properties should be preserved
        assert af_prod.agents["main-agent"].provider == "anthropic"

    def test_tool_types(self, full_featured_yaml):
        af = AgentFile.from_path(full_featured_yaml)
        tools = af.agents["main-agent"].tools
        mcp = [t for t in tools if t.is_mcp()]
        composio = [t for t in tools if t.is_composio()]
        assert len(mcp) == 1
        assert len(composio) == 1


class TestOldSchemaRoundtrip:
    def test_parses_with_deprecation_warning(self, old_schema_yaml, capsys):
        af = AgentFile.from_path(old_schema_yaml)
        assert af.schema_version == "1.0"
        captured = capsys.readouterr()
        assert "WARNING" in captured.err

    def test_workspace_id_migration(self, old_schema_yaml, capsys):
        af = AgentFile.from_path(old_schema_yaml)
        assert af.mcp_gateway.org_id == "old-workspace"


class TestDynamicYaml:
    """Test various YAML structures that might appear in the wild."""

    def test_agent_without_metadata(self, tmp_path):
        """Agents should work even without metadata block."""
        p = tmp_path / "test.yaml"
        p.write_text(yaml.dump({
            "schema_version": "1.1",
            "agents": {
                "bare": {
                    "runtime": {"provider": "anthropic", "model": "claude-sonnet-4-6"},
                    "tools": [{"name": "t", "source": "mcp://test"}],
                }
            },
        }))
        af = AgentFile.from_path(p)
        assert af.agents["bare"].provider == "anthropic"

    def test_agent_without_runtime(self, tmp_path):
        """Agent without runtime block should use defaults."""
        p = tmp_path / "test.yaml"
        p.write_text(yaml.dump({
            "schema_version": "1.1",
            "agents": {
                "default": {
                    "metadata": {"description": "test"},
                    "tools": [{"name": "t", "source": "mcp://test"}],
                }
            },
        }))
        af = AgentFile.from_path(p)
        agent = af.agents["default"]
        assert agent.provider == "anthropic"
        assert agent.model == "claude-sonnet-4-6"

    def test_empty_tools_list(self, tmp_path):
        p = tmp_path / "test.yaml"
        p.write_text(yaml.dump({
            "schema_version": "1.1",
            "agents": {
                "empty-tools": {
                    "metadata": {},
                    "runtime": {},
                    "tools": [],
                }
            },
        }))
        af = AgentFile.from_path(p)
        errors = af.validate_config()
        assert any("at least one tool" in e for e in errors)

    def test_multiple_tool_types(self, tmp_path):
        p = tmp_path / "test.yaml"
        p.write_text(yaml.dump({
            "schema_version": "1.1",
            "agents": {
                "multi-tool": {
                    "metadata": {},
                    "runtime": {},
                    "tools": [
                        {"name": "search", "source": "mcp://brave-search"},
                        {"name": "github", "source": "composio://GITHUB"},
                    ],
                }
            },
        }))
        af = AgentFile.from_path(p)
        tools = af.agents["multi-tool"].tools
        assert any(t.is_mcp() for t in tools)
        assert any(t.is_composio() for t in tools)

    def test_nested_execution_with_thinking_bool(self, tmp_path):
        p = tmp_path / "test.yaml"
        p.write_text(yaml.dump({
            "schema_version": "1.1",
            "agents": {
                "thinker": {
                    "metadata": {},
                    "runtime": {},
                    "tools": [{"name": "t", "source": "mcp://test"}],
                    "execution": {"mode": "direct", "thinking": True},
                }
            },
        }))
        af = AgentFile.from_path(p)
        exe = af.effective_execution(af.agents["thinker"])
        assert exe.thinking.enabled is True

    def test_volumes_mixed_refs(self, tmp_path):
        p = tmp_path / "test.yaml"
        p.write_text(yaml.dump({
            "schema_version": "1.1",
            "agents": {
                "vol-agent": {
                    "metadata": {},
                    "runtime": {},
                    "tools": [{"name": "t", "source": "mcp://t"}],
                    "volumes": [
                        "shared-vol",
                        {"host_path": "/tmp/inline", "container_path": "/inline"},
                    ],
                }
            },
            "volumes": {
                "shared-vol": {"provider": "local", "host_path": "/tmp/shared", "container_path": "/shared"},
            },
        }))
        af = AgentFile.from_path(p)
        vols = af.effective_volumes(af.agents["vol-agent"])
        assert len(vols) == 2
