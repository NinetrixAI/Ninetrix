# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

```bash
# Install in editable mode (required for local development)
pip install -e .

# Run the CLI
agentfile --help

# Manual smoke tests (no automated test suite)
agentfile init --name test-agent --provider anthropic --yes
agentfile build --file agentfile.yaml
agentfile run --file agentfile.yaml
agentfile run --file agentfile.yaml --thread-id my-session-1   # resume persistent thread
agentfile mcp list --file agentfile.yaml
agentfile mcp test duckduckgo
```

There is no test suite, linting config, or CI setup in this repository.

---

## Full Local Dev Stack (`ninetrix dev`)

`ninetrix dev` starts the **complete local development stack** as Docker containers. It is the recommended way to develop with Ninetrix — run it once and everything (API, database, MCP gateway, MCP worker) comes up together.

### What gets started

| Service | Port | Description |
|---------|------|-------------|
| `postgres` | 5432 | Local PostgreSQL for agent checkpoints |
| `api` | 8000 | FastAPI checkpoint reader + dashboard backend |
| `mcp-gateway` | 8080 | MCP tool routing hub — agents call this via HTTP |
| `mcp-worker` | — | MCP subprocess bridge — connects outbound WS to gateway |

Compose file: `infra/compose/docker-compose.dev.yml`

### First Run (auto-setup)

On first run, `ninetrix dev` automatically:
1. Creates `~/.agentfile/mcp-worker.yaml` with an empty `servers: []` config
2. Generates `~/.agentfile/.api-secret` (machine auth token)
3. Pulls Docker images and starts all four services

### `.env` file — where to place it

Place a `.env` file in the **directory where you run `ninetrix dev`** (typically the project root). Docker Compose reads it natively.

```
# .env (project root — same directory you run `ninetrix dev` from)

# LLM provider keys
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...

# MCP server credentials — forwarded to the mcp-worker container
GITHUB_TOKEN=ghp_...
SLACK_BOT_TOKEN=xoxb-...
NOTION_API_KEY=secret_...
LINEAR_API_KEY=lin_...

# Telemetry (optional — connects agents to the local API)
AGENTFILE_API_URL=http://localhost:8000
AGENTFILE_RUNNER_TOKEN=<value from ~/.agentfile/.api-secret>
```

The docker-compose.dev.yml forwards all credential env vars to the mcp-worker container. The LLM keys are forwarded to agent containers by `ninetrix run` / `ninetrix up`.

### Configuring MCP Servers (`~/.agentfile/mcp-worker.yaml`)

This file is bind-mounted into the mcp-worker container (read-only). Edit it to enable MCP servers for the worker. After editing, run `ninetrix dev` again (or restart the worker container).

```yaml
# ~/.agentfile/mcp-worker.yaml
gateway_url: "ws://mcp-gateway:8080"   # internal Docker network hostname
workspace_id: "local"
worker_name: "default"
token: "local-dev-secret"              # matches MCP_GATEWAY_SECRET in compose

servers:
  - name: github
    type: npx
    package: "@modelcontextprotocol/server-github"
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "${GITHUB_TOKEN}"  # resolved from container env at startup

  - name: filesystem
    type: npx
    package: "@modelcontextprotocol/server-filesystem"
    args: ["/workspace"]
```

`${VAR}` syntax in `env:` values is resolved from the container's environment at worker startup. The container environment comes from the host env / `.env` file via docker-compose.

### Verifying the Stack is Running

```bash
# Check all services are healthy
ninetrix dev --detach    # start detached, no log streaming

# Gateway health + connected worker count
curl http://localhost:8080/health

# See all registered MCP tools (should show github__* after config)
curl http://localhost:8080/admin/tools

# See connected workers
curl http://localhost:8080/admin/workers
```

### Observability (Logs)

```bash
# Stream all service logs
docker compose -f infra/compose/docker-compose.dev.yml logs -f

# Just the mcp-worker (tool calls in/out)
docker compose -f infra/compose/docker-compose.dev.yml logs mcp-worker -f

# Just the mcp-gateway (routing, errors)
docker compose -f infra/compose/docker-compose.dev.yml logs mcp-gateway -f
```

Key log lines to watch for:
- `Worker connected: id=default workspace=local` — worker registered with gateway
- `Started MCP server 'github' — 30 tool(s)` — GitHub MCP server started in worker
- `Routing github__list_repos → worker=default` — tool call routed by gateway
- Any `ERROR` lines indicate failures (missing env vars, npx not found, etc.)

### Stop the Stack

```bash
ninetrix dev --detach    # start
Ctrl+C                   # stop (when running with log streaming)

# Manual stop
docker compose -f infra/compose/docker-compose.dev.yml down
```

---

## Architecture Overview

The CLI packages AI agents as Docker containers. The workflow is:
`**agentfile.yaml**` → validate → render Jinja2 templates → `docker build` → `docker run`

### Command Flow

Each command lives in `agentfile/commands/`:

- `init.py` — scaffolds a new `agentfile.yaml` from `templates/agentfile.yaml.j2`
- `build.py` — validates the config, renders `Dockerfile.j2` + `entrypoint.py.j2` into a temp dir, then calls `docker build`; MCP tool specs are no longer resolved at build time (gateway handles discovery at runtime)
- `run.py` — runs the built image via `subprocess` with interactive TTY; always injects `AGENTFILE_PROVIDER`, `AGENTFILE_MODEL`, `AGENTFILE_TEMPERATURE` as env vars to override baked-in values; also forwards API keys, Composio keys, DB URL, and `AGENTFILE_THREAD_ID`
- `deploy.py` — wraps build + `docker push` + prints the resulting `docker run` command
- `mcp.py` — manages MCP tool servers: `list`, `add` (writes to `~/.agentfile/mcp.yaml`), `test` (connects via MCP SDK and prints tool schemas)
- `migrate.py` — upgrades `agentfile.yaml` to the latest schema version; supports `--dry-run`

### Core Models (`agentfile/core/models.py`)

All models use **Pydantic v2 BaseModel** (migrated from dataclasses). `AgentFile` is the root model, parsed from YAML via `AgentFile.from_path()`. Key properties:

- `system_prompt` — assembles the agent persona from `role`, `goal`, `instructions`, `constraints` fields
- `image_name(tag)` — returns `ninetrix/<slug>:<tag>`
- `validate()` — returns a list of error strings (empty = valid); requires at least one tool
- `schema_version` — tracks YAML schema version (current: `"1.1"`); prints deprecation warning if old/missing

Sub-models:


| Model           | Fields                                                                                        |
| --------------- | --------------------------------------------------------------------------------------------- |
| `Tool`          | `name`, `source`, `actions`; methods `is_mcp()`, `is_composio()`, `mcp_name`, `composio_app`  |
| `Governance`    | `max_budget_per_run`, `human_approval` (`HumanApproval`), `rate_limit`                        |
| `HumanApproval` | `enabled`, `actions`, `notify_url`                                                            |
| `Execution`     | `mode` (`"direct"` | `"planned"`), `verify_steps`, `max_steps`, `on_step_failure`, `verifier`, `thinking`, `durability` |
| `Verifier`      | `provider`, `model`, `max_tokens` — the LLM used for step verification                        |
| `ThinkingConfig`| `enabled`, `model`, `provider`, `max_tokens`, `temperature`, `min_input_length`, `prompt`      |
| `Resources`     | `cpu`, `memory`, `storage`, `base_image`, `warm_pool`                                         |
| `VolumeSpec`    | `name`, `provider`, `host_path`, `bucket`, `prefix`, `container_path`, `read_only`, `sync`    |
| `MCPGatewayConfig` | `url`, `token`, `org_id` (alias: `workspace_id` — deprecated)                             |

Sub-models use `frozen=True` for immutability. `Literal` types enforce valid values for enums (`mode`, `type`, `sync`, etc.). `MCPGatewayConfig` uses `populate_by_name=True` for backward-compatible `workspace_id` alias.


### MCP Registry (`agentfile/core/mcp_registry.py`)

Two-layer registry: built-in servers + user overrides in `~/.agentfile/mcp.yaml`. Used by the `ninetrix mcp list/add/test` CLI commands only. **Not used at build time** — the agent Docker no longer spawns local MCP servers.

### Templates (`agentfile/templates/`)

All three templates are Jinja2 (`.j2`):

- `Dockerfile.j2` — installs provider SDKs, httpx (for MCP gateway), Composio SDK (provider-specific), and psycopg3. No Node.js or uv — MCP servers are never run inside the agent container.
- `entrypoint.py.j2` — the generated agent runtime (see below)
- `agentfile.yaml.j2` — initial scaffold template

### Multi-Provider Support

The `entrypoint.py.j2` template handles three providers at runtime:

- **Anthropic**: `stop_reason == "tool_use"` branch; tool format uses `input_schema`
- **OpenAI**: `finish_reason == "tool_calls"` branch; tool format uses `parameters`
- **Google Gemini**: `function_call` parts; schema must be sanitized via `_sanitize_schema_for_gemini()` which strips `additionalProperties`, `$schema`, `$defs`, etc.

The provider/model/temperature can always be overridden at runtime via environment variables, even if different values were baked into the image at build time.

### Tool Sources

Tools are declared in `agentfile.yaml` with a `source:` field. The schema allows any `prefix://` URI — built-in and community sources are pluggable via the SDK's `ToolSource` ABC.

| Source prefix | Protocol | Example |
| ------------- | -------- | ------- |
| `mcp://` | MCP Gateway HTTP proxy — routed via mcp-gateway + mcp-worker | `mcp://duckduckgo` |
| `composio://` | Composio cloud action registry | `composio://GITHUB` |
| `openapi://` | Any REST API with an OpenAPI 3.x spec — auto-generates tools from operationIds | `openapi://https://petstore.swagger.io/v3/openapi.json` |
| `builtin://` | Built-in tools (bash, filesystem, memory, etc.) | `builtin://bash` |
| `./` or `/` | Local @Tool Python files | `./tools/my_tool.py` |
| `<custom>://` | Community plugins via `pip install ninetrix-source-X` (entry_points discovery) | `jira://my-instance` |

Tools can also declare `auth:`, `dependencies:`, and `base_url:` fields:

```yaml
tools:
  - name: stripe
    source: openapi://https://stripe.com/spec.json
    actions: [createPaymentIntent, listCustomers]
    auth:
      type: bearer
      token: ${STRIPE_API_KEY}
    base_url: https://api.stripe.com
  - name: ocr
    source: ./tools/ocr.py
    dependencies:
      pip: [pytesseract]
      apt: [tesseract-ocr]
```


**MCP tools** — the agent Docker never spawns MCP server subprocesses. All `mcp://` tools are proxied at runtime through the MCP gateway (`POST /v1/mcp/{workspace_id}`). The gateway routes each call to a connected mcp-worker, which runs the actual MCP server subprocess. Gateway connection is configured via `mcp_gateway:` in the yaml OR env vars at runtime:

```yaml
# in agentfile.yaml (optional — can use env vars instead)
mcp_gateway:
  url: "http://localhost:8080"       # or MCP_GATEWAY_URL env var
  token: "${MCP_GATEWAY_TOKEN}"      # or MCP_GATEWAY_TOKEN env var
  workspace_id: "default"            # or MCP_GATEWAY_WORKSPACE env var
```

If no `mcp_gateway:` section is present but the agent has `mcp://` tools, `use_mcp_gateway` is still set to `True` — the agent reads connection info purely from env vars at runtime.

**Composio tools** use `client.tools.get_raw_composio_tools()` at runtime to fetch schemas, then `client.tools.execute(slug=..., arguments=..., user_id=..., dangerously_skip_version_check=True)` to invoke them. Tool schemas are formatted for the active provider at build time (Jinja2 conditional). Provider-specific Composio packages are installed in the Dockerfile:

- Anthropic: `composio composio-claude-agent-sdk claude-agent-sdk`
- OpenAI: `composio composio-openai-agents openai-agents`
- Other: `composio`

### Persistence Layer (`persistence:` in agentfile.yaml)

Optional PostgreSQL-backed checkpoint store. Activated by adding a `persistence:` block.

**What gets saved** — every meaningful state transition writes a row to `agentfile_checkpoints`:

- Full message history (JSON)
- Tool call inputs and results
- Token usage (input + output)
- Step verifier results (if `verify_steps: true`)

**Resume** — `agentfile run --thread-id <id>` resumes a prior session; the container restores history from the DB and continues from where it left off.

**Human approval polling** — when `governance.human_approval.enabled: true` and a tool matches the `actions` list, the agent pauses and polls the DB every 5 seconds for an external `UPDATE ... SET status='approved'` (or `rejected`). Timeout is 1 hour.

**DB schema** (`agentfile_checkpoints` table):

```
id, trace_id, thread_id, agent_id, step_index, timestamp, status, checkpoint (JSONB), metadata (JSONB)
```

Status values: `in_progress`, `waiting_for_approval`, `approved`, `rejected`, `completed`, `error`

**Docker networking** — `run.py` adds `--add-host=host.docker.internal:host-gateway` so containers can reach a PostgreSQL instance running on the host.

**Key implementation detail** — psycopg3 connection uses `autocommit=True` and each DDL statement is executed separately (no multi-statement batches).

### Plan-Then-Execute (`execution:` in agentfile.yaml)

Optional two-phase execution mode. Activated by setting `execution.mode: planned`.

**Phase 1 — Plan**: Before any tools run, the main agent LLM is asked (with no tools available) to output a structured JSON plan:

```json
{"goal": "...", "steps": [{"id": 1, "description": "...", "tool": "tool_name"}]}
```

The plan is printed to the terminal before execution begins. If planning fails, falls back to direct mode automatically.

**Phase 2 — Execute**: The existing agentic tool-use loop runs normally, but each tool result is optionally verified by a separate verifier LLM.

**Step verification** (`verify_steps: true`) — after each tool call, the verifier receives a compressed 3-part context (goal + tool called + tool result, capped at ~800 tokens total) and returns `{"ok": true/false, "reason": "..."}`. The verifier uses its own LLM client (can be a different provider/model from the main agent) configured via `execution.verifier`.

**Failure policy** (`on_step_failure`):

- `continue` — log the failure and keep going (default)
- `abort` — stop the turn immediately, write `status=completed` to DB
- `retry_once` — inject an error tool_result asking the LLM to retry

**Verifier token accounting** — tracked separately from main agent tokens in `variables.verifier_input_tokens` / `variables.verifier_output_tokens` in the checkpoint. Verifier results are stored in `variables.verifications[]` per tool call.

**Runtime override** — `AGENTFILE_VERIFIER_MODEL` env var overrides the baked-in verifier model without rebuilding.

### `agentfile.yaml` Full Schema

```yaml
schema_version: "1.1"              # run `ninetrix migrate` to upgrade old files

agents:
  my-agent:
    metadata:
      name: my-agent               # used for Docker image tag slug
      description: ...
      role: ...                    # composed into system_prompt
      goal: ...
      instructions: |
        ...
      constraints:
        - "..."

    runtime:
      provider: anthropic          # anthropic | openai | google | mistral | groq
      model: claude-sonnet-4-6
      temperature: 0.2
      max_tokens: 8192             # max output tokens per LLM call
      max_turns: 20                # safety cap on the agentic loop
      tool_timeout: 30             # seconds before a tool call is aborted
      history_window_tokens: 90000 # sliding-window token budget

    tools:
      - name: web_search
        source: mcp://duckduckgo        # MCP tool — registry key after mcp://
      - name: github
        source: composio://GITHUB       # Composio tool — app name after composio://
      - name: gmail_send
        source: composio://GMAIL
        actions:                        # optional: limit to specific Composio actions
          - GMAIL_SEND_EMAIL

    # Structured output — agent must respond with JSON matching this schema
    output_type:                        # optional
      type: object
      required: [summary, confidence]
      properties:
        summary: {type: string}
        confidence: {type: number, minimum: 0, maximum: 1}

    governance:
      max_budget_per_run: 1.00
      human_approval:
        enabled: true
        actions: [file_write, shell_exec]
      rate_limit: 10_requests_per_minute

    execution:
      mode: planned                     # "direct" (default) | "planned"
      verify_steps: true
      max_steps: 10
      on_step_failure: continue         # "abort" | "continue" | "retry_once"
      verifier:
        provider: anthropic
        model: claude-haiku-4-5-20251001
        max_tokens: 128

    persistence:
      provider: "postgres"
      url: "${DATABASE_URL}"            # ${VAR} resolved from env at runtime

    # Multi-agent collaboration
    collaborators: [researcher, writer] # names of other agents in this file
    transfer_timeout: 300               # seconds before a transfer times out
    routing:                            # optional — how to pick a collaborator
      mode: auto                        # "agent" (default, LLM decides via tool) | "auto" (cheap router picks)
      model: claude-haiku-4-5-20251001  # fast/cheap model for routing
      provider: anthropic

triggers:
  - type: webhook
    endpoint: /run
```

### Key Constants in Generated `entrypoint.py`

All four runtime limits are now **configurable in `agentfile.yaml`** under `runtime:` and overridable via env vars at runtime:

- `MAX_TOKENS` — max output tokens per LLM call (default: 8192, yaml: `runtime.max_tokens`, env: `AGENTFILE_MAX_TOKENS`)
- `MAX_TURNS` — safety cap on the agentic tool-use loop (default: 20, yaml: `runtime.max_turns`, env: `AGENTFILE_MAX_TURNS`)
- `TOOL_TIMEOUT` — seconds before a hanging MCP tool call is aborted (default: 30, yaml: `runtime.tool_timeout`, env: `AGENTFILE_TOOL_TIMEOUT`)
- `HISTORY_WINDOW_TOKENS` — sliding-window token budget for history (default: 90000, yaml: `runtime.history_window_tokens`, env: `AGENTFILE_HISTORY_WINDOW_TOKENS`)
- `APPROVAL_POLL_INTERVAL = 5` — seconds between human-approval DB polls (not configurable)
- `APPROVAL_TIMEOUT = 3600` — 1 hour hard timeout for human approval (not configurable)

### Jinja2 Template Context Variables (passed from `build.py`)


| Variable                   | Type        | Purpose                                        |
| -------------------------- | ----------- | ---------------------------------------------- |
| `agent`                    | `AgentDef`  | Agent config (with effective governance/triggers merged) |
| `use_mcp_gateway`          | bool        | True if agent has any `mcp://` tools           |
| `mcp_gateway_url`          | str         | Gateway base URL (baked in from yaml or empty) |
| `mcp_gateway_token`        | str         | Bearer token (baked in from yaml or empty)     |
| `mcp_gateway_org_id`       | str         | Organization ID (default: `"default"`)         |
| `has_composio_tools`       | bool        | Enable Composio integration                    |
| `composio_tool_defs`       | list        | `[{app, actions}]`                             |
| `has_output_type`          | bool        | Enable structured JSON output validation       |
| `output_type_schema`       | str         | JSON string of the output schema               |
| `max_tokens`               | int         | Max output tokens per LLM call (from yaml)     |
| `max_turns`                | int         | Agentic loop safety cap (from yaml)            |
| `tool_timeout`             | int         | MCP tool call timeout in seconds (from yaml)   |
| `history_window_tokens`    | int         | Sliding-window token budget (from yaml)        |
| `has_persistence`          | bool        | Enable StateStore / Checkpointer               |
| `persistence_provider`     | str         | e.g. `"postgres"`                              |
| `persistence_url_template` | str         | Raw URL with `${VAR}` placeholders             |
| `has_planned_execution`    | bool        | Enable plan-then-execute mode                  |
| `verify_steps`             | bool        | Enable per-tool verification                   |
| `max_plan_steps`           | int         | Cap on number of plan steps                    |
| `on_step_failure`          | str         | `"abort"` \| `"continue"` \| `"retry_once"`   |
| `has_verifier`             | bool        | Initialize separate verifier LLM client        |
| `verifier_provider`        | str         | Provider for verifier LLM                      |
| `verifier_model`           | str         | Model for verifier LLM                         |
| `verifier_max_tokens`      | int         | Max tokens for verifier response               |
| `has_collaborators`        | bool        | Agent has collaborator agents                  |
| `collaborators`            | list[str]   | Names of collaborator agents                   |
| `has_auto_routing`         | bool        | Use cheap LLM router instead of agent tool     |
| `routing_model`            | str         | Model for auto-router (default: haiku)         |
| `routing_provider`         | str         | Provider for auto-router                       |
| `transfer_timeout`         | int         | Collaborator transfer timeout in seconds       |


### Human-in-the-Loop (HITL)

HITL is a **first-class feature independent of persistence**. It works with or without a `persistence:` block.

**Without persistence** — the container pauses and prompts the terminal (`_stdin_approve()`):

```
⏸ Approval required: GMAIL_SEND_EMAIL
  Approve? [y/N]
```

**With persistence** — the container saves a `waiting_for_approval` checkpoint and polls the DB every 5 seconds. An external system (e.g. the web dashboard `/approvals` page) updates the row to `approved` or `rejected`.

**Notification webhook** — `governance.human_approval.notify_url` accepts a `${ENV_VAR}` placeholder. `run.py` resolves the env var and injects `AGENTFILE_APPROVAL_NOTIFY_URL` into the container. On pause, the entrypoint POSTs a JSON payload to this URL.

**Resume re-polling** — if a container crashes mid-approval-wait, restarting with the same `--thread-id` detects the `waiting_for_approval` state, trims history back to `turn_start_history_len`, and re-enters the DB poll loop automatically.

**Gate condition** — approval is gated by `{% if agent.governance.human_approval.enabled and agent.governance.human_approval.actions %}`, NOT by `has_persistence`. Approval works without a DB.

### Memory Buffer / History Windowing

The generated entrypoint trims the message history before every LLM API call using a sliding window:

- `HISTORY_WINDOW_CHARS = 100_000` (≈25k tokens) is the budget
- **Pinned** (never trimmed): system messages + the first user message (original task)
- **Trimmed**: oldest non-pinned messages, walking backwards from the current turn
- Orphan prevention: never starts the trimmed window with a bare `tool_result` user message (would crash the API)
- Google Gemini uses a parallel `_trim_contents()` function operating on `Content` objects
- Prints `[memory] Trimmed N old message(s)` to stderr when trimming fires

### API Layer (`/Users/kobi/Code/agentfile/api/`)

A local FastAPI server that reads from `agentfile_checkpoints` and serves the web dashboard.

**Setup:**

```bash
cd /Users/kobi/Code/agentfile/api
cp .env.example .env   # fill in DATABASE_URL=postgresql://...localhost...
pip install -e .
uvicorn main:app --reload --port 8000
```

**Endpoints:**


| Method | Path                                         | Description                                                                               |
| ------ | -------------------------------------------- | ----------------------------------------------------------------------------------------- |
| `GET`  | `/threads`                                   | List all threads (latest checkpoint per thread). Supports `?sort=`, `?order=`, `?status=` |
| `GET`  | `/threads/{thread_id}`                       | Full thread detail + extracted logs                                                       |
| `GET`  | `/threads/{thread_id}/checkpoints`           | All checkpoints in step order                                                             |
| `GET`  | `/approvals`                                 | Pending HITL approvals (`waiting_for_approval`)                                           |
| `POST` | `/approvals/{trace_id}/{step_index}/approve` | Approve a tool call                                                                       |
| `POST` | `/approvals/{trace_id}/{step_index}/reject`  | Reject a tool call                                                                        |


Sort fields: `updated_at` (default desc), `step_index`, `tokens_used`, `agent_id`, `status`.

**Files:** `main.py` (FastAPI app + CORS for localhost:3000), `db.py` (asyncpg pool), `models.py` (Pydantic schemas), `routers/threads.py`, `routers/approvals.py`.

### Runtime Env Var Overrides

All values below are read at container startup — **no rebuild needed**. Set them on the host before `ninetrix run` / `ninetrix up`; both commands forward all `AGENTFILE_*` vars via `setdefault` (yaml-derived values like `AGENTFILE_PROVIDER` always win).

| Env var | Default | What it controls |
|---|---|---|
| `AGENTFILE_MAX_TOKENS` | from yaml (8192) | Max output tokens per LLM call |
| `AGENTFILE_MAX_TURNS` | from yaml (20) | Safety cap on the agentic tool-use loop |
| `AGENTFILE_TOOL_TIMEOUT` | from yaml (30) | Seconds before a hanging tool call is aborted |
| `AGENTFILE_HISTORY_WINDOW_TOKENS` | from yaml (90000) | Token budget for sliding history window |
| `AGENTFILE_MAX_PLAN_STEPS` | from yaml | Max steps in planned execution |
| `AGENTFILE_VERIFY_STEPS` | from yaml | `true`/`false` — enable step verification |
| `AGENTFILE_ON_STEP_FAILURE` | from yaml | `abort` / `continue` / `retry_once` |
| `AGENTFILE_THINKING_ENABLED` | `true` | Toggle the pre-run reasoning step on/off |
| `AGENTFILE_THINKING_PROVIDER` | from yaml | Provider for the thinking LLM |
| `AGENTFILE_THINKING_MODEL` | from yaml | Model for the thinking LLM |
| `AGENTFILE_THINKING_MAX_TOKENS` | from yaml | Token budget for thinking output |
| `AGENTFILE_THINKING_TEMPERATURE` | from yaml | Temperature for thinking call |
| `AGENTFILE_THINKING_MIN_LENGTH` | from yaml | Min input chars to trigger thinking |
| `AGENTFILE_THINKING_PROMPT` | from yaml | Custom instruction injected into thinking call |
| `AGENTFILE_APPROVAL_ENABLED` | `true` | Toggle HITL approval gate on/off |

Implementation: `run.py` and `up.py/_build_agent_env()` iterate `os.environ` and call `env.setdefault(k, v)` for every key starting with `AGENTFILE_`.

### Known Gotchas

- **Jinja2 + Python f-strings**: `{{` in Jinja2 templates is the expression delimiter. Use plain strings (no `f` prefix) for any Python string literals containing `{` / `}` with no template variables to interpolate.
- **Jinja2 booleans in Python code**: `{{ some_bool | tojson }}` renders `true`/`false` (JSON), not `True`/`False` (Python). Use `{{ some_bool }}` for booleans embedded in generated Python code.
- **psycopg3 multi-statement SQL**: Use `autocommit=True` and run each DDL statement in a separate `execute()` call. Multi-statement strings cause transaction errors.
- **Composio execute versioning**: Always pass `dangerously_skip_version_check=True` to `client.tools.execute()` for manual (non-agentic) execution.
- **Composio tool schemas**: `get_raw_composio_tools()` returns `Tool` objects with `.slug`, `.description`, `.input_parameters` (flat `{prop: schema}` dict — wrap into `{"type": "object", "properties": ...}`). Use `tools=` param for action slugs, `toolkits=` for app names.
- **Docker networking to host**: `run.py` adds `--add-host=host.docker.internal:host-gateway` so containers can reach host-side services (e.g. PostgreSQL). Use `host.docker.internal` in DATABASE_URL. The API server runs on the host and uses `localhost` instead.
- **History corruption on error**: The main loop snapshots `len(checkpointer.history)` before each turn and restores it on exception, preventing `tool_use` messages without matching `tool_result` from persisting across turns.
- **Plan JSON parsing**: LLMs sometimes append explanation text after the closing `}`. The plan parser uses `text[text.find("{") : text.rfind("}")+1]` to extract only the JSON object, ignoring trailing content.
- **HITL gate is not `has_persistence`**: The approval block is gated on `agent.governance.human_approval.enabled and agent.governance.human_approval.actions`, not on `has_persistence`. Without a DB, `_stdin_approve()` handles approval interactively via stdin.

