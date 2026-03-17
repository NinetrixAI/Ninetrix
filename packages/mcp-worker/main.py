"""
Ninetrix MCP Worker

Starts MCP server subprocesses and connects to the MCP Gateway.

In dev/enterprise mode (no MCP_SAAS_API_URL):
  - All servers in mcp-worker.yaml start eagerly with their static env blocks.

In SaaS mode (MCP_SAAS_API_URL + MCP_GATEWAY_TOKEN set):
  - Known managed integrations (github, slack, tavily, etc.) start eagerly at boot
    with credentials fetched from the saas-api vault — tools are registered with the
    gateway immediately so agents can discover them via tools/list.
  - Unknown/custom servers still start from their yaml env blocks as in dev mode.
"""
from __future__ import annotations

import asyncio
import logging
import os

from config import load_config
from gateway_client import GatewayClient
from runtime import ServerPool

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("mcp-worker")


async def main():
    config_path = os.getenv("MCP_WORKER_CONFIG", "mcp-worker.yaml")
    cfg = load_config(config_path)

    logger.info(
        "Starting worker %r (workspace=%s) → gateway %s",
        cfg.worker_name,
        cfg.workspace_id,
        cfg.gateway_url,
    )

    # ── Start static MCP servers from yaml ────────────────────────────────────
    pool = ServerPool(cfg)
    eager_tools = await pool.start_static_servers()

    logger.info(
        "Eager tools: %d across %d static server(s)",
        len(eager_tools), len(pool.server_names()),
    )
    if not eager_tools:
        logger.info("No static servers configured — lazy-start mode only.")

    # ── Tool-call handler — uses ServerPool for lazy server startup ───────────
    async def handle_tool_call(call_id: str, server_name: str, tool_name: str, args: dict) -> dict:
        return await pool.call_tool(server_name, tool_name, args)

    # ── Connect to gateway ────────────────────────────────────────────────────
    client = GatewayClient(
        gateway_url=cfg.gateway_url,
        worker_id=cfg.worker_id,
        workspace_id=cfg.workspace_id,
        worker_name=cfg.worker_name,
        token=cfg.token,
        on_tool_call=handle_tool_call,
    )
    client.set_tools(eager_tools, pool.server_names())

    try:
        await asyncio.gather(
            client.connect(),
            client.ping_loop(),
        )
    finally:
        logger.info("Shutting down MCP servers…")
        await pool.stop_all()


if __name__ == "__main__":
    asyncio.run(main())

