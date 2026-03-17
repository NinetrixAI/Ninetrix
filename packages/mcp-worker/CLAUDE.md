# CLAUDE.md — mcp-worker

## What is this?

The MCP Worker is a bridge process that **runs MCP server subprocesses and connects outbound to the MCP Gateway**. Agents never talk to the worker directly — they call the gateway, which routes tool calls to the right worker over a persistent WebSocket.

The key insight: the WebSocket connection is **worker → gateway** (outbound), not inbound. This lets workers run behind NAT, firewalls, or inside private customer networks without any port-forwarding.

```
Agent container
  POST /v1/mcp/{workspace_id}
        │
        ▼
  mcp-gateway (public endpoint)
        │  WebSocket (worker initiates)
        ▼
  mcp-worker  ← THIS REPO
    spawns: npx/uvx/python MCP server subprocesses
    communicates via stdio (MCP protocol)
```

## Two Operating Modes

### Dev / Enterprise Mode (default)
No `MCP_SAAS_API_URL` set. All MCP servers are declared in `mcp-worker.yaml` with their credentials in `env:` blocks. Servers start **eagerly** at boot.

```yaml
servers:
  - name: github
    type: npx
    package: "@modelcontextprotocol/server-github"
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "${GITHUB_TOKEN}"
```

The `${VAR}` syntax is resolved at boot from the container's environment. Credentials come from host env vars forwarded via docker-compose.

### SaaS Mode (prod)
When `MCP_SAAS_API_URL` + `MCP_GATEWAY_TOKEN` are both set:
- Servers in `mcp-worker.yaml` that are **known managed integrations** (see `_MANAGED_PACKAGES` in `runtime.py`) start **eagerly at boot** using vault credentials — `start_static_servers()` calls `_start_managed_server()` instead of `_start_server_from_config()` for these. This ensures their tools are registered with the gateway immediately so agents can discover them via `tools/list`.
- Unknown or custom servers (not in `_MANAGED_PACKAGES`) still start from their yaml `env:` block as in dev mode.
- `_start_managed_server()` calls `saas-api /internal/v1/gateway/tool-credential` with the worker token to fetch the real credential, then merges it with `os.environ` (preserving `PATH`, `HOME`, etc.) before spawning the subprocess.
- Credentials are never stored in worker memory beyond the subprocess spawn.
- Google token expiry (401) on a tool call triggers an automatic `refresh_credential()` + subprocess restart.

**Managed integrations** (`_MANAGED_PACKAGES` in `runtime.py`):
`github`, `slack`, `notion`, `google-drive`, `google-sheets`, `google-docs`, `tavily`, `brave-search`, `filesystem`

> Add new integrations here when they are added to `saas-api/_CRED_ENV_MAP` and `mcp_catalog`.

## File Structure

```
main.py          Entry point — wires config → ServerPool → GatewayClient → run
config.py        WorkerConfig + ServerConfig dataclasses; load_config() parses mcp-worker.yaml
                   Env vars always override yaml fields
                   server_to_command() converts ServerConfig → (executable, args)
mcp_bridge.py    MCPServer class — wraps one MCP server subprocess:
                   start()     — spawns subprocess, runs MCP init, lists tools (prefixed name__tool)
                               — if startup fails after stdio_cm is entered, stop() is called to
                                 prevent leaked anyio context managers from crashing the worker
                   call_tool() — invokes tool with 30s timeout
                   stop()      — tears down stdio + session context managers
                   tools       — list[dict] with {name, description, inputSchema}
gateway_client.py GatewayClient — persistent WebSocket to the gateway:
                   connect()   — connects, sends worker.register, handles tool.call messages
                   ping_loop() — sends keepalive pings every 30s
                   Reconnects with exponential back-off (5s → 60s cap) on disconnect
runtime.py       ServerPool — manages all MCPServer instances:
                   start_static_servers() — eager start at boot; uses _start_managed_server for
                                            entries in _MANAGED_PACKAGES when is_saas_mode() is true
                   get_server(name)       — returns running server or starts lazily (for non-yaml servers)
                   call_tool(...)         — executes tool, handles Google 401 refresh
                   _start_managed_server() — fetches creds from saas-api vault, merges with os.environ,
                                             starts subprocess; used both at boot and on lazy call
                   _restart_server()      — stops + restarts with refreshed credentials
                   _MANAGED_PACKAGES      — dict of server_name → npx_package for known integrations
saas_client.py   HTTP client for worker → saas-api credential fetching (SaaS mode only):
                   get_tool_credential(integration_id) → env_vars dict
                   refresh_credential(integration_id) → re-fetches after Google token refresh
                   is_saas_mode()         — returns True only when SAAS_API_URL + TOKEN are set
mcp-worker.yaml.example  Reference config with examples for all supported server types
```

## mcp-worker.yaml Format

```yaml
# Gateway connection (all fields override-able via env vars)
gateway_url: "ws://localhost:8080"   # env: MCP_GATEWAY_URL
workspace_id: "default"             # env: MCP_WORKSPACE_ID
worker_name: "my-worker"            # env: MCP_WORKER_NAME
worker_id: "my-worker"              # env: MCP_WORKER_ID (defaults to worker_name)
token: "dev-secret"                 # env: MCP_GATEWAY_TOKEN

servers:
  - name: filesystem                # must be unique; used as tool prefix
    type: npx                       # npx | uvx | python | docker
    package: "@modelcontextprotocol/server-filesystem"
    args: ["/data"]                 # passed to the server process

  - name: github
    type: npx
    package: "@modelcontextprotocol/server-github"
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "${GITHUB_TOKEN}"  # resolved from container env

  - name: internal-api
    command: "python /opt/my_mcp_server.py"  # full command override (bypasses type+package)
    env:
      INTERNAL_API_KEY: "${INTERNAL_API_KEY}"
```

**Server types:**
| type | Runs |
|------|------|
| `npx` | `npx -y <package> [args...]` |
| `uvx` | `uvx <package> [args...]` |
| `python` | `python -m <package> [args...]` |
| `docker` | `docker run --rm -i <package> [args...]` |
| (command) | splits on spaces, appends args |

## Tool Namespacing

Every tool exposed by a server is prefixed with `{server_name}__`:

```
github__create_issue
github__list_repos
filesystem__read_file
slack__send_message
```

This prevents collisions when multiple servers expose tools with the same short name. The gateway indexes all tools under this namespace.

## WebSocket Protocol (Worker → Gateway)

```
// Connect:
ws://gateway:8080/ws/workers/{worker_id}?token=...&workspace_id=...&worker_name=...

// On connect, worker immediately sends:
{"type": "worker.register", "tools": [...], "servers": ["github", "filesystem"]}

// Gateway confirms:
{"type": "worker.registered", "tool_count": 42}

// Gateway sends tool calls:
{"type": "tool.call", "call_id": "uuid", "server": "github", "tool": "list_repos", "args": {...}}

// Worker responds:
{"type": "tool.result", "call_id": "uuid", "result": {"content": [...], "isError": false}}
// or on error:
{"type": "tool.result", "call_id": "uuid", "error": "some error message"}

// Keepalive:
{"type": "ping"} → {"type": "pong"}
```

## Credential Flow (SaaS Mode)

### At boot (eager startup for managed servers)
```
1. start_static_servers() iterates mcp-worker.yaml servers
2. For each server in _MANAGED_PACKAGES → _start_managed_server(name)
3. saas_client.get_tool_credential(name)
   POST saas-api/internal/v1/gateway/tool-credential
   headers: X-Gateway-Secret: <MCP_GATEWAY_SERVICE_SECRET>
   body:    { worker_token: "nxt_...", integration_id: "tavily" }
4. saas-api validates token → returns { "TAVILY_API_KEY": "tvly-abc123" }
5. env = {**os.environ, **creds}  ← merges with full process env (preserves PATH etc.)
6. Worker spawns: npx -y tavily-mcp  with env
7. list_tools() → registers "tavily__search", etc.
8. gateway_client.set_tools(eager_tools) → sends worker.register to gateway
   → agents can now discover tools via tools/list
```

### On tool call (lazy startup for non-yaml managed servers)
```
1. Tool call arrives: server="github", tool="list_repos"
2. get_server("github") → not in self._servers, not in static_configs
3. is_saas_mode() and "github" in _MANAGED_PACKAGES → _start_managed_server("github")
4. Same credential fetch + subprocess spawn as above
5. Tool executes; result returned
```

### On Google 401
```
saas_client.refresh_credential() → re-fetches from vault (which calls Google /token internally)
→ _restart_server() stops old subprocess, starts fresh one with new access_token
```

Credentials are **never stored** beyond the subprocess spawn. The worker process itself holds no secrets at rest.

> **Important:** yaml `env:` blocks containing `${VAR}` are only useful in dev mode where the var is in the container's environment. In SaaS mode, managed servers ignore the yaml env block entirely — credentials come from the vault.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_GATEWAY_URL` | `ws://localhost:8080` | WebSocket URL of the gateway |
| `MCP_WORKSPACE_ID` | `default` | Workspace this worker belongs to |
| `MCP_WORKER_NAME` | `worker-1` | Human-readable worker name |
| `MCP_WORKER_ID` | `{worker_name}` | Unique worker identifier |
| `MCP_GATEWAY_TOKEN` | `dev-secret` | Auth token for gateway connection |
| `MCP_WORKER_CONFIG` | `mcp-worker.yaml` | Path to yaml config file |
| `MCP_SAAS_API_URL` | `` | Enables SaaS mode; saas-api URL for credential fetching |
| `MCP_GATEWAY_SERVICE_SECRET` | `dev-gateway-secret` | Shared secret for worker → saas-api internal calls |

Credential env vars (forwarded automatically by `ninetrix gateway start`):
`GITHUB_TOKEN`, `SLACK_BOT_TOKEN`, `SLACK_TEAM_ID`, `NOTION_API_KEY`, `LINEAR_API_KEY`, `BRAVE_API_KEY`, `STRIPE_SECRET_KEY`, `GOOGLE_*_ACCESS_TOKEN`, `POSTGRES_CONNECTION_STRING`

## Running Locally

```bash
# Via docker-compose (recommended — starts gateway + worker together)
cd cli/
ninetrix gateway start

# Direct (for development)
cd mcp-worker/
pip install -e .
MCP_GATEWAY_URL=ws://localhost:8080 \
MCP_GATEWAY_TOKEN=dev-secret \
MCP_WORKER_CONFIG=mcp-worker.yaml.example \
python main.py
```

## Key Invariants

- **Outbound only** — workers connect to the gateway; the gateway never connects to workers.
- **Credential isolation** — in SaaS mode, credentials are fetched per-integration, passed to the subprocess env, and not retained in worker memory.
- **Managed servers start eagerly in SaaS mode** — tools must be registered with the gateway at boot so agents can discover them via `tools/list`. Lazy startup on first tool call is only a fallback for servers not in `mcp-worker.yaml`.
- **One bad server cannot crash the worker** — `_start_server_from_config` and `_start_managed_server` catch all exceptions; `mcp_bridge.start()` calls `stop()` on failure to prevent leaked anyio context managers from corrupting the async event loop.
- **Tool prefix is stable** — `{server_name}__` prefix is set at `MCPServer.start()` and never changes. Renaming a server in yaml changes all its tool names.
- **Reconnect is automatic** — `GatewayClient.connect()` loops forever with exponential back-off. Workers survive gateway restarts.
- **30s tool timeout** — `mcp_bridge.MCPServer.call_tool()` uses `asyncio.wait_for(..., timeout=30.0)`. Hanging subprocess tools are cancelled.
- **`_MANAGED_PACKAGES` is the source of truth** — must be kept in sync with `saas-api/_CRED_ENV_MAP` and `cli/mcp_catalog.py`. Any new integration added to those must also be added here.
