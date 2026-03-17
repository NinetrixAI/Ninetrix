"""DEPRECATED — use agentfile.core.mcp_catalog instead.

mcp_registry.py has been superseded by mcp_catalog.py, which is the single
source of truth for MCP server definitions used by all `ninetrix mcp` commands.
This file is kept only to avoid breaking any external code that may import it.
"""

from agentfile.core.mcp_catalog import CatalogEntry as _CatalogEntry, get, list_all  # noqa: F401
