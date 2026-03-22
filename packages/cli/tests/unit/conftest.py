"""Unit test fixtures — mocks for external dependencies."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentfile.core.models import (
    AgentDef,
    AgentFile,
    Governance,
    HumanApproval,
    Tool,
)


@pytest.fixture
def mcp_tool() -> Tool:
    return Tool(name="web_search", source="mcp://brave-search")


@pytest.fixture
def composio_tool() -> Tool:
    return Tool(name="github", source="composio://GITHUB", actions=["GITHUB_LIST_REPOS"])


@pytest.fixture
def local_tool() -> Tool:
    return Tool(name="my_tool", source="./tools/my_tool.py")


@pytest.fixture
def basic_agent() -> AgentDef:
    return AgentDef(
        name="test-agent",
        description="A test agent",
        provider="anthropic",
        model="claude-sonnet-4-6",
        tools=[Tool(name="search", source="mcp://brave-search")],
    )


@pytest.fixture
def basic_governance() -> Governance:
    return Governance(
        max_budget_per_run=1.0,
        human_approval=HumanApproval(enabled=False),
    )


@pytest.fixture
def basic_agentfile(basic_agent: AgentDef, basic_governance: Governance) -> AgentFile:
    return AgentFile(
        schema_version="1.1",
        agents={"test-agent": basic_agent},
        governance=basic_governance,
    )


@pytest.fixture
def mock_docker_client():
    """Return a mocked Docker client."""
    with patch("docker.from_env") as mock_from_env:
        client = MagicMock()
        mock_from_env.return_value = client
        yield client
