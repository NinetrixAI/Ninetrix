"""Extended tests for agentfile.core.template_context — builtin tools, skills, local tools."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from agentfile.core.models import (
    AgentDef,
    AgentFile,
    Execution,
    Governance,
    Resources,
    Skill,
    ThinkingConfig,
    Tool,
    Trigger,
    Verifier,
    VolumeSpec,
)
from agentfile.core.template_context import build_context


# ── Helper ────────────────────────────────────────────────────────────────────


def _ctx(af: AgentFile, agent: AgentDef | None = None, **kwargs):
    """Build context and return it — supports both dict and Pydantic model."""
    ag = agent or af.entry_agent
    return build_context(af, ag, **kwargs)


def _get(ctx, key):
    """Access a context value regardless of whether it is a Pydantic model or dict."""
    if hasattr(ctx, key):
        return getattr(ctx, key)
    return ctx[key]


# ── Builtin tools ─────────────────────────────────────────────────────────────


class TestBuildContextBuiltinTools:
    """Tests for has_builtin_shell and has_builtin_filesystem context vars."""

    def test_builtin_shell_detected(self):
        agent = AgentDef(
            name="test",
            tools=[Tool(name="shell", source="builtin://shell")],
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())
        ctx = _ctx(af, agent)
        assert _get(ctx, "has_builtin_shell") is True
        assert _get(ctx, "has_builtin_filesystem") is False

    def test_builtin_filesystem_detected(self):
        agent = AgentDef(
            name="test",
            tools=[Tool(name="fs", source="builtin://filesystem")],
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())
        ctx = _ctx(af, agent)
        assert _get(ctx, "has_builtin_filesystem") is True
        assert _get(ctx, "has_builtin_shell") is False

    def test_both_builtins_detected(self):
        agent = AgentDef(
            name="test",
            tools=[
                Tool(name="shell", source="builtin://shell"),
                Tool(name="fs", source="builtin://filesystem"),
            ],
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())
        ctx = _ctx(af, agent)
        assert _get(ctx, "has_builtin_shell") is True
        assert _get(ctx, "has_builtin_filesystem") is True

    def test_no_builtin_tools_both_false(self):
        agent = AgentDef(
            name="test",
            tools=[Tool(name="search", source="mcp://brave-search")],
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())
        ctx = _ctx(af, agent)
        assert _get(ctx, "has_builtin_shell") is False
        assert _get(ctx, "has_builtin_filesystem") is False

    def test_mcp_tool_alongside_builtin(self):
        """Builtin tools coexist with MCP tools correctly."""
        agent = AgentDef(
            name="test",
            tools=[
                Tool(name="shell", source="builtin://shell"),
                Tool(name="search", source="mcp://brave-search"),
            ],
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())
        ctx = _ctx(af, agent)
        assert _get(ctx, "has_builtin_shell") is True
        assert _get(ctx, "use_mcp_gateway") is True


# ── Local tools ───────────────────────────────────────────────────────────────


class TestBuildContextLocalTools:
    """Tests for has_local_tools, local_tool_files, local_source_paths."""

    def test_no_local_tools_when_agentfile_dir_none(self, tmp_path: Path):
        """Without agentfile_dir, local tools cannot be discovered."""
        agent = AgentDef(
            name="test",
            tools=[Tool(name="my_tool", source="./tools/my_tool.py")],
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())
        ctx = build_context(af, agent, agentfile_dir=None)
        assert _get(ctx, "has_local_tools") is False
        assert _get(ctx, "local_source_paths") == []

    def test_local_tools_detected_with_agentfile_dir(self, tmp_path: Path):
        """When agentfile_dir is given and file exists, has_local_tools is True."""
        # Create a fake tool file
        tool_file = tmp_path / "tools" / "my_tool.py"
        tool_file.parent.mkdir(parents=True)
        tool_file.write_text("def my_tool(): pass\n")

        agent = AgentDef(
            name="test",
            tools=[Tool(name="my_tool", source="./tools/my_tool.py")],
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())

        # ninetrix-sdk not installed → just sources, no manifest discovery
        with patch("agentfile.core.template_context.Path") as _:
            ctx = build_context(af, agent, agentfile_dir=tmp_path)

        # has_local_tools should be True since the source was declared
        assert _get(ctx, "has_local_tools") is True

    def test_local_source_paths_contains_absolute_path(self, tmp_path: Path):
        """local_source_paths entries are resolved absolute paths."""
        tool_file = tmp_path / "tools" / "my_tool.py"
        tool_file.parent.mkdir(parents=True)
        tool_file.write_text("def my_tool(): pass\n")

        agent = AgentDef(
            name="test",
            tools=[Tool(name="my_tool", source="./tools/my_tool.py")],
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())
        ctx = build_context(af, agent, agentfile_dir=tmp_path)

        paths = _get(ctx, "local_source_paths")
        assert len(paths) == 1
        assert str(tool_file.resolve()) == paths[0]

    def test_local_tool_files_have_container_path(self, tmp_path: Path):
        """local_tool_files contains /app/tools/<filename> paths."""
        tool_file = tmp_path / "tools" / "my_tool.py"
        tool_file.parent.mkdir(parents=True)
        tool_file.write_text("def my_tool(): pass\n")

        agent = AgentDef(
            name="test",
            tools=[Tool(name="my_tool", source="./tools/my_tool.py")],
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())
        ctx = build_context(af, agent, agentfile_dir=tmp_path)

        files = _get(ctx, "local_tool_files")
        assert len(files) == 1
        assert files[0] == "/app/tools/my_tool.py"

    def test_duplicate_source_deduped(self, tmp_path: Path):
        """Two tools pointing to the same source file are deduplicated."""
        tool_file = tmp_path / "tools" / "shared.py"
        tool_file.parent.mkdir(parents=True)
        tool_file.write_text("def shared(): pass\n")

        agent = AgentDef(
            name="test",
            tools=[
                Tool(name="tool_a", source="./tools/shared.py"),
                Tool(name="tool_b", source="./tools/shared.py"),
            ],
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())
        ctx = build_context(af, agent, agentfile_dir=tmp_path)

        paths = _get(ctx, "local_source_paths")
        assert len(paths) == 1


# ── Skills ────────────────────────────────────────────────────────────────────


class TestBuildContextSkills:
    """Tests for has_skills and skill_instructions context vars."""

    def test_no_skills_false(self):
        agent = AgentDef(
            name="test",
            tools=[Tool(name="search", source="mcp://brave-search")],
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())
        ctx = build_context(af, agent, agentfile_dir=None)
        assert _get(ctx, "has_skills") is False
        assert _get(ctx, "skill_instructions") == ""

    def test_skill_instructions_loaded_from_file(self, tmp_path: Path):
        """When agentfile_dir is set and instructions.md exists, it is loaded."""
        skill_dir = tmp_path / "skills" / "research"
        skill_dir.mkdir(parents=True)
        (skill_dir / "instructions.md").write_text("# Research\nAlways cite sources.")

        agent = AgentDef(
            name="test",
            tools=[Tool(name="search", source="mcp://brave-search")],
            skills=[Skill(source="./skills/research")],
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())
        ctx = build_context(af, agent, agentfile_dir=tmp_path)

        assert _get(ctx, "has_skills") is True
        assert "cite sources" in _get(ctx, "skill_instructions")

    def test_multiple_skills_joined(self, tmp_path: Path):
        """Multiple skills have their instructions joined with a separator."""
        for name, content in [("skill-a", "Skill A content"), ("skill-b", "Skill B content")]:
            d = tmp_path / "skills" / name
            d.mkdir(parents=True)
            (d / "instructions.md").write_text(content)

        agent = AgentDef(
            name="test",
            tools=[Tool(name="search", source="mcp://brave-search")],
            skills=[
                Skill(source="./skills/skill-a"),
                Skill(source="./skills/skill-b"),
            ],
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())
        ctx = build_context(af, agent, agentfile_dir=tmp_path)

        instructions = _get(ctx, "skill_instructions")
        assert "Skill A content" in instructions
        assert "Skill B content" in instructions
        # Skills are joined with a separator
        assert "---" in instructions

    def test_missing_instructions_md_warns(self, tmp_path: Path):
        """If instructions.md is absent, the _warn callback is called."""
        skill_dir = tmp_path / "skills" / "empty-skill"
        skill_dir.mkdir(parents=True)
        # No instructions.md

        warnings = []

        agent = AgentDef(
            name="test",
            tools=[Tool(name="search", source="mcp://brave-search")],
            skills=[Skill(source="./skills/empty-skill")],
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())
        build_context(af, agent, agentfile_dir=tmp_path, _warn=warnings.append)

        assert len(warnings) == 1
        assert "instructions.md" in warnings[0]

    def test_skills_without_agentfile_dir(self):
        """Without agentfile_dir, skills cannot be loaded."""
        agent = AgentDef(
            name="test",
            tools=[Tool(name="search", source="mcp://brave-search")],
            skills=[Skill(source="./skills/research")],
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())
        ctx = build_context(af, agent, agentfile_dir=None)
        assert _get(ctx, "has_skills") is False


# ── Runtime limits ────────────────────────────────────────────────────────────


class TestBuildContextRuntimeLimits:
    """Tests for max_tokens, max_turns, tool_timeout, history_window_tokens."""

    def test_custom_max_tokens(self):
        agent = AgentDef(
            name="test",
            tools=[Tool(name="t", source="mcp://t")],
            max_tokens=4096,
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())
        ctx = build_context(af, agent)
        assert _get(ctx, "max_tokens") == 4096

    def test_custom_max_turns(self):
        agent = AgentDef(
            name="test",
            tools=[Tool(name="t", source="mcp://t")],
            max_turns=50,
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())
        ctx = build_context(af, agent)
        assert _get(ctx, "max_turns") == 50

    def test_custom_tool_timeout(self):
        agent = AgentDef(
            name="test",
            tools=[Tool(name="t", source="mcp://t")],
            tool_timeout=60,
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())
        ctx = build_context(af, agent)
        assert _get(ctx, "tool_timeout") == 60

    def test_custom_history_window(self):
        agent = AgentDef(
            name="test",
            tools=[Tool(name="t", source="mcp://t")],
            history_window_tokens=50_000,
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())
        ctx = build_context(af, agent)
        assert _get(ctx, "history_window_tokens") == 50_000


# ── SaaS runner / invoke server flags ────────────────────────────────────────


class TestBuildContextFlags:
    """Tests for is_saas_runner and has_invoke_server flags."""

    def test_is_saas_runner_false_by_default(self):
        agent = AgentDef(name="test", tools=[Tool(name="t", source="mcp://t")])
        af = AgentFile(agents={"test": agent}, governance=Governance())
        ctx = build_context(af, agent)
        assert _get(ctx, "is_saas_runner") is False

    def test_is_saas_runner_true_when_set(self):
        agent = AgentDef(name="test", tools=[Tool(name="t", source="mcp://t")])
        af = AgentFile(agents={"test": agent}, governance=Governance())
        ctx = build_context(af, agent, is_saas_runner=True)
        assert _get(ctx, "is_saas_runner") is True

    def test_has_invoke_server_false_by_default(self):
        agent = AgentDef(name="test", tools=[Tool(name="t", source="mcp://t")])
        af = AgentFile(agents={"test": agent}, governance=Governance())
        ctx = build_context(af, agent)
        assert _get(ctx, "has_invoke_server") is False

    def test_has_invoke_server_true_when_set(self):
        agent = AgentDef(name="test", tools=[Tool(name="t", source="mcp://t")])
        af = AgentFile(agents={"test": agent}, governance=Governance())
        ctx = build_context(af, agent, has_invoke_server=True)
        assert _get(ctx, "has_invoke_server") is True


# ── Custom base image ────────────────────────────────────────────────────────


class TestBuildContextBaseImage:
    """Tests for base_image context variable."""

    def test_custom_base_image(self):
        agent = AgentDef(
            name="test",
            tools=[Tool(name="t", source="mcp://t")],
            resources=Resources(base_image="python:3.11-slim"),
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())
        ctx = build_context(af, agent)
        assert _get(ctx, "base_image") == "python:3.11-slim"

    def test_default_base_image_is_python_312(self):
        agent = AgentDef(name="test", tools=[Tool(name="t", source="mcp://t")])
        af = AgentFile(agents={"test": agent}, governance=Governance())
        ctx = build_context(af, agent)
        assert _get(ctx, "base_image") == "python:3.12-slim"


# ── Thinking config extra fields ──────────────────────────────────────────────


class TestBuildContextThinkingExtended:
    """Extended thinking config coverage not in test_template_context.py."""

    def test_thinking_temperature_forwarded(self):
        agent = AgentDef(
            name="test",
            tools=[Tool(name="t", source="mcp://t")],
            execution=Execution(
                thinking=ThinkingConfig(enabled=True, temperature=0.7),
            ),
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())
        ctx = build_context(af, agent)
        assert _get(ctx, "thinking_temperature") == 0.7

    def test_thinking_min_input_length_forwarded(self):
        agent = AgentDef(
            name="test",
            tools=[Tool(name="t", source="mcp://t")],
            execution=Execution(
                thinking=ThinkingConfig(enabled=True, min_input_length=200),
            ),
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())
        ctx = build_context(af, agent)
        assert _get(ctx, "thinking_min_input_length") == 200

    def test_thinking_defaults_to_agent_provider(self):
        agent = AgentDef(
            name="test",
            provider="google",
            model="gemini-2.5-flash",
            tools=[Tool(name="t", source="mcp://t")],
            execution=Execution(thinking=ThinkingConfig(enabled=True)),
        )
        af = AgentFile(agents={"test": agent}, governance=Governance())
        ctx = build_context(af, agent)
        assert _get(ctx, "thinking_provider") == "google"
        assert _get(ctx, "thinking_model") == "gemini-2.5-flash"
