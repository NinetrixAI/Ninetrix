# MCP Gateway — Architecture & Implementation Plan

## Overview

The MCP Gateway is a central hub that aggregates tools from multiple workers and exposes
them to agents via a single HTTP endpoint. It solves two key problems:

1. **Cloud agents** can't spawn local MCP server processes (no filesystem, no Node.js)
2. **Enterprise customers** need tools to run on-prem without data leaving their network

The WebSocket architecture is key: workers connect **outbound** to the gateway, so they
work behind NAT/firewalls with no inbound ports required.

---

## Architecture: Three Layers

```
┌─────────────────────────────────────────────────────────────┐
│  CONTROL PLANE (saas-api)                                    │
│  Integration Registry · Credential Vault · Policy Engine    │
└─────────────────────────┬───────────────────────────────────┘
                          │ token validation / tool policies
┌─────────────────────────▼───────────────────────────────────┐
│  MCP GATEWAY  (mcp-gateway/)                                 │
│  JSON-RPC API  ·  Worker Registry  ·  WebSocket Tunnel       │
│                                                              │
│  POST /v1/mcp/{workspace_id}    ←── agents call this        │
│  WS   /ws/workers/{worker_id}   ←── workers connect here    │
└──────────┬──────────────────────────────────────────────────┘
           │ WebSocket (outbound from worker)
┌──────────▼──────────────────────────────────────────────────┐
│  MCP WORKERS  (mcp-worker/)                                  │
│  SaaS managed  ·  Enterprise on-prem  ·  Local dev          │
│                                                              │
│  Runs MCP server subprocesses (npx/uvx/python/docker)       │
│  Bridges stdio MCP protocol ↔ WebSocket JSON messages       │
└─────────────────────────────────────────────────────────────┘
```

---

## Protocol

### Agent → Gateway (JSON-RPC 2.0 over HTTP)

```
POST /v1/mcp/{workspace_id}
Authorization: Bearer <workspace_token>
Content-Type: application/json

{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
{"jsonrpc": "2.0", "id": 2, "method": "tools/list",  "params": {}}
{"jsonrpc": "2.0", "id": 3, "method": "tools/call",  "params": {"name": "slack__send_message", "arguments": {...}}}
```

### Worker → Gateway (WebSocket JSON messages)

```
# Worker registers on connect
{"type": "worker.register", "worker_id": "...", "workspace_id": "...", "tools": [...]}

# Gateway sends tool call to worker
{"type": "tool.call", "call_id": "uuid", "tool": "slack__send_message", "arguments": {...}}

# Worker returns result
{"type": "tool.result", "call_id": "uuid", "result": {...}}

# Keepalive
{"type": "ping"} / {"type": "pong"}
```

### Tool Namespacing

Tools are prefixed: `{server_name}__{tool_name}` (double underscore).
Example: `filesystem__read_file`, `slack__send_message`, `github__create_pr`.

This prevents collisions when multiple workers expose different servers.

---

## File Structure

```
mcp-gateway/
  main.py           FastAPI app, CORS, mounts all routers
  models.py         Pydantic: ToolSchema, WorkerStatus, MCPRequest/Response
  pyproject.toml    deps: fastapi, uvicorn[standard], websockets>=13, pydantic>=2
  Dockerfile        FROM python:3.12-slim, pip install -e ., uvicorn port 8080
  core/
    __init__.py
    registry.py     WorkerRegistry singleton — manages WebSocket connections,
                    tool index, pending call futures, send_call() with 60s timeout
    auth.py         Token verification; Phase 1: env secret; Phase 3: saas-api lookup
  routers/
    __init__.py
    workers.py      WS /ws/workers/{worker_id} — register, tool.result, ping handlers
    mcp.py          POST /v1/mcp/{workspace_id} — JSON-RPC dispatcher
    admin.py        GET /health, /admin/workers, /admin/tools

mcp-worker/
  main.py           Startup: load config, start MCP servers, connect to gateway
  config.py         ServerConfig/WorkerConfig dataclasses, load_config() merges YAML+env
  mcp_bridge.py     MCPServer class wrapping stdio subprocess; prefixes tools {server}__
  gateway_client.py GatewayClient — persistent WS, auto-reconnect with backoff, ping loop
  pyproject.toml    deps: mcp>=1.0, websockets>=13, pydantic>=2, pyyaml>=6
  Dockerfile        FROM python:3.12-slim + Node.js 20 (for npx MCP servers)
  mcp-worker.yaml.example  Example config with filesystem + GitHub servers
```

---

## agentfile.yaml Integration

Add `mcp_gateway:` block to use the gateway instead of spawning local MCP processes:

```yaml
mcp_gateway:
  url: "ws://localhost:8080"      # rewritten to host.docker.internal inside Docker
  token: "dev-secret"
  workspace_id: "default"

agents:
  my-agent:
    runtime:
      provider: anthropic
      model: claude-sonnet-4-6
    tools:
      - name: filesystem
        source: mcp://filesystem   # still declared here for intent; resolved via gateway
      - name: slack
        source: mcp://slack
```

When `mcp_gateway:` is present:
- Build: skips local MCP server setup (no `needs_node`, no subprocess specs baked in)
- Runtime: `_init_mcp_gateway()` calls `tools/list` at startup to discover all tools
- `_MCPGatewaySession` is a drop-in for `mcp.ClientSession` — same `.call_tool()` interface

### Environment Variables Forwarded by `ninetrix run`

| Var | Source | Purpose |
|-----|--------|---------|
| `MCP_GATEWAY_URL` | env / .env / yaml | Gateway WebSocket URL (localhost rewritten to host.docker.internal) |
| `MCP_GATEWAY_TOKEN` | env / .env / yaml | Auth token for the gateway |
| `MCP_GATEWAY_ORG_ID` | env / .env / yaml | Workspace ID for tool scoping |

---

## CLI Commands

```bash
ninetrix gateway start    # docker compose up (mcp-gateway + mcp-worker)
ninetrix gateway stop     # docker compose down
ninetrix gateway status   # shows connected workers, tool count, available tool names
```

Compose file: `cli/docker-compose.gateway.yml`
- Starts `ninetrix/mcp-gateway:dev` on port 8080
- Starts `ninetrix/mcp-worker:dev` using `mcp-worker.yaml.example` config
- Worker depends on gateway health check before connecting

---

## Implementation Status

| Phase | Status | Description |
|-------|--------|-------------|
| Phase 1 | ✅ Done | Core gateway + worker services |
| Phase 2 | ✅ Done | CLI integration (agentfile.yaml `mcp_gateway:` block, `ninetrix gateway` commands) |
| Phase 3 | ✅ Done | Multi-tenant auth + workspace isolation + per-tool credential broker |
| Phase 4 | ⬜ Todo | Managed SaaS workers |
| Phase 5 | ⬜ Todo | Enterprise self-hosted worker (`ninetrix worker install`) |

---

## Phase 3 — Multi-tenant Auth & Workspace Isolation

**Goal:** Each tenant's tools are invisible to other tenants. Workers authenticate with
per-workspace tokens instead of a shared secret.

**Changes needed:**

- `saas-api/`: Add `worker_tokens` table (same pattern as `workspace_tokens`).
  New endpoint: `POST /internal/workers/verify-token` → validates token, returns workspace_id.
- `mcp-gateway/core/auth.py`: In SaaS mode, call saas-api to verify token instead of
  comparing against `MCP_GATEWAY_SECRET` env var.
- `mcp-gateway/core/registry.py`: Index `WorkerConnection` by `workspace_id`.
  `tools/list` and `tools/call` filter by the calling agent's workspace.
- Token format: `nxt_<urlsafe32>` (same as existing workspace tokens).

---

## Phase 4 — Managed SaaS Workers

**Goal:** SaaS provides pre-warmed managed workers for common tools. Users toggle them
on in the dashboard — no worker infra to manage.

**Changes needed:**

- `saas-api/`: New managed-workers service. On workspace tool enable:
  1. Pull credentials from Credential Vault (Fernet-encrypted, api/crypto.py pattern)
  2. Start mcp-worker container via Docker SDK with injected credentials
  3. Worker auto-connects to gateway; tools appear in workspace
- `app/dashboard/integrations`: Wire the existing integrations page to gateway worker
  status — show connected/disconnected per tool.
- Warm pool: keep N containers pre-started per tier; assign on first use.

---

## Phase 5 — Enterprise Self-Hosted Worker

**Goal:** Enterprise customers run MCP tools entirely on-prem. Worker connects outbound
to Ninetrix cloud gateway — no data leaves their network.

**Changes needed:**

- `cli/agentfile/commands/worker.py`: New `ninetrix worker` command group:
  - `ninetrix worker install` — generates systemd unit or `docker-compose.worker.yml`
  - `ninetrix worker status` — checks if local worker is connected to gateway
- `mcp-worker/mcp-worker.yaml.example`: Expand with all supported server types and
  credential injection patterns.
- `docs-v2/`: Enterprise self-hosted guide — install → configure → connect → verify.

**Key selling point vs Composio:** The WebSocket outbound-only architecture means the
worker works behind corporate firewalls with zero network config. Customer data (files,
DB contents, API responses) never transits Ninetrix infrastructure.

---

## Known Issues & Fixes Applied

| Issue | Fix |
|-------|-----|
| Health check used `curl` (not in python:3.12-slim) | Use Python `urllib.request` in healthcheck |
| Worker compose volume path wrong | Changed to relative `../mcp-worker/mcp-worker.yaml.example` |
| Agent container hit `localhost:8080` (itself) | `template_context.py` rewrites localhost→host.docker.internal at build time; `run.py` rewrites at runtime |
| `MCP_GATEWAY_URL` not injected when `--image` flag used | `run.py` now always forwards from env/dotenv/yaml regardless of af.mcp_gateway |
| `AGENTFILE_API_URL` not injected → silent 401 on runner events | `run.py` now reads from env/.env and applies `_docker_url()` rewrite |
| `setuptools.backends.legacy:build` BackendUnavailable | Fixed both pyproject.toml files to use `setuptools.build_meta` |
