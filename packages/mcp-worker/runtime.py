"""Lazy MCP server pool with per-integration credential injection.

Instead of starting all MCP servers at boot, ServerPool starts them on-demand
when the first tool call for that integration arrives. Credentials are fetched
from saas-api just-in-time for each integration, so the worker never holds
credentials it isn't actively using.

In dev/enterprise mode (no MCP_SAAS_API_URL), servers defined in mcp-worker.yaml
start eagerly with their static env blocks — existing behaviour unchanged.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from config import ServerConfig, WorkerConfig, server_to_command
from mcp_bridge import MCPServer
import saas_client

log = logging.getLogger(__name__)

# Integrations whose MCP package is known — maps server name → npx package
# Add entries here as new managed integrations are supported.
_MANAGED_PACKAGES: dict[str, str] = {
    "github":        "@modelcontextprotocol/server-github",
    "slack":         "@modelcontextprotocol/server-slack",
    "notion":        "@notionhq/notion-mcp-server",
    "google-drive":  "@modelcontextprotocol/server-google-drive",
    "google-sheets": "@modelcontextprotocol/server-google-drive",  # uses same package
    "google-docs":   "@modelcontextprotocol/server-google-drive",
    "tavily":        "tavily-mcp",
    "brave-search":  "@modelcontextprotocol/server-brave-search",
    "filesystem":    "@modelcontextprotocol/server-filesystem",
}


class ServerPool:
    """
    Manages a set of MCPServer instances.

    Servers defined in mcp-worker.yaml are started eagerly (pre-configured).
    Servers for SaaS-managed integrations are started lazily on first use
    with credentials fetched from saas-api just-in-time.
    """

    def __init__(self, cfg: WorkerConfig):
        self._cfg = cfg
        # server_name → MCPServer (running)
        self._servers: dict[str, MCPServer] = {}
        # server_name → ServerConfig (from yaml, for eager servers)
        self._static_configs: dict[str, ServerConfig] = {
            sc.name: sc for sc in cfg.servers
        }

    async def start_static_servers(self) -> list[dict]:
        """Start all servers declared in mcp-worker.yaml. Called at boot."""
        tools_out: list[dict] = []
        for sc in self._cfg.servers:
            # In SaaS mode, use vault credentials for known managed integrations
            if saas_client.is_saas_mode() and sc.name in _MANAGED_PACKAGES:
                srv = await self._start_managed_server(sc.name)
            else:
                srv = await self._start_server_from_config(sc)
            if srv:
                tools_out.extend(srv.tools)
        return tools_out

    async def get_server(self, server_name: str) -> Optional[MCPServer]:
        """
        Return a running MCPServer for the given server_name.
        If not running yet, attempt to start it (fetching credentials if in SaaS mode).
        Returns None if the server can't be started.
        """
        if server_name in self._servers:
            return self._servers[server_name]

        # In SaaS mode, managed integrations always use vault credentials — even on
        # lazy restart (e.g. after a crash). Check this BEFORE static config so that
        # a yaml env block with ${UNRESOLVED_VAR} never shadows the real credential.
        if saas_client.is_saas_mode() and server_name in _MANAGED_PACKAGES:
            return await self._start_managed_server(server_name)

        # Dev/enterprise mode: start from yaml config
        if server_name in self._static_configs:
            srv = await self._start_server_from_config(self._static_configs[server_name])
            return srv

        return None

    async def call_tool(self, server_name: str, tool_name: str, args: dict) -> dict:
        """
        Execute a tool call, starting the server lazily if needed.
        On 401-like errors from Google integrations, attempts a credential refresh.
        """
        srv = await self.get_server(server_name)
        if not srv:
            raise ValueError(f"Server '{server_name}' not available")

        try:
            return await srv.call_tool(tool_name, args)
        except Exception as exc:
            err_str = str(exc).lower()
            # Detect Google token expiry and attempt transparent refresh
            if "401" in err_str and server_name.startswith("google-") and saas_client.is_saas_mode():
                log.info("Google 401 detected for '%s' — attempting token refresh", server_name)
                new_creds = await saas_client.refresh_credential(server_name)
                if new_creds:
                    await self._restart_server(server_name, new_creds)
                    srv = self._servers.get(server_name)
                    if srv:
                        return await srv.call_tool(tool_name, args)
            raise

    def all_tools(self) -> list[dict]:
        """Return tools from all currently-running servers."""
        return [tool for srv in self._servers.values() for tool in srv.tools]

    def server_names(self) -> list[str]:
        return list(self._servers.keys())

    async def stop_all(self):
        for name, srv in list(self._servers.items()):
            try:
                await srv.stop()
            except Exception:
                pass
        self._servers.clear()

    # ── Private helpers ────────────────────────────────────────────────────────

    async def _start_server_from_config(self, sc: ServerConfig) -> Optional[MCPServer]:
        command, args = server_to_command(sc)
        srv = MCPServer(name=sc.name, command=command, args=args, env=sc.env)
        try:
            log.info("Starting MCP server: %s  (%s %s)", sc.name, command, " ".join(args))
            await srv.start()
            self._servers[sc.name] = srv
            return srv
        except Exception as exc:
            log.error("Failed to start server %s: %s — skipping", sc.name, exc)
            return None

    async def _start_managed_server(self, server_name: str) -> Optional[MCPServer]:
        """Fetch credentials from saas-api and start the MCP server subprocess."""
        log.info("Starting managed server '%s' — fetching credentials from vault…", server_name)
        creds = await saas_client.get_tool_credential(server_name)
        if not creds:
            log.warning(
                "No credentials for '%s' — integration may not be connected. "
                "Run: ninetrix mcp connect %s",
                server_name, server_name,
            )
            return None
        # Log which env vars were fetched (never log values)
        log.info("Fetched credential vars for '%s': %s", server_name, list(creds.keys()))

        package = _MANAGED_PACKAGES[server_name]
        # Merge fetched creds with current process env so PATH/NODE_PATH are inherited
        env = {**os.environ, **creds}

        srv = MCPServer(name=server_name, command="npx", args=["-y", package], env=env)
        try:
            await srv.start()
            self._servers[server_name] = srv
            log.info("Managed server '%s' started with %d tool(s)", server_name, len(srv.tools))
            return srv
        except Exception as exc:
            log.error("Failed to start managed server '%s': %s", server_name, exc)
            return None

    async def _restart_server(self, server_name: str, new_creds: dict):
        """Stop and restart a server with refreshed credentials."""
        old = self._servers.pop(server_name, None)
        if old:
            try:
                await old.stop()
            except Exception:
                pass

        package = _MANAGED_PACKAGES.get(server_name)
        if not package:
            # Static server — rebuild from yaml config
            sc = self._static_configs.get(server_name)
            if sc:
                merged_env = {**sc.env, **new_creds}
                sc_new = ServerConfig(
                    name=sc.name, type=sc.type, package=sc.package,
                    command=sc.command, args=sc.args, env=merged_env,
                )
                await self._start_server_from_config(sc_new)
            return

        env = {**os.environ, **new_creds}
        srv = MCPServer(name=server_name, command="npx", args=["-y", package], env=env)
        try:
            await srv.start()
            self._servers[server_name] = srv
            log.info("Restarted '%s' with refreshed credentials", server_name)
        except Exception as exc:
            log.error("Failed to restart '%s': %s", server_name, exc)
