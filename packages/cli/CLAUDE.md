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

## Architecture Overview

The CLI packages AI agents as Docker containers. The workflow is:
`**agentfile.yaml**` → validate → render Jinja2 templates → `docker build` → `docker run`

### Command Flow

Each command lives in `agentfile/commands/`:

- `init.py` — scaffolds a new `agentfile.yaml` from `templates/agentfile.yaml.j2`
- `build.py` — validates the config, resolves MCP tool specs from the registry, renders `Dockerfile.j2` + `entrypoint.py.j2` into a temp dir, then calls `docker build`
- `run.py` — runs the built image via `subprocess` with interactive TTY; always injects `AGENTFILE_PROVIDER`, `AGENTFILE_MODEL`, `AGENTFILE_TEMPERATURE` as env vars to override baked-in values; also forwards API keys, Composio keys, DB URL, and `AGENTFILE_THREAD_ID`
- `deploy.py` — wraps build + `docker push` + prints the resulting `docker run` command
- `mcp.py` — manages MCP tool servers: `list`, `add` (writes to `~/.agentfile/mcp.yaml`), `test` (connects via MCP SDK and prints tool schemas)

### Core Models (`agentfile/core/models.py`)

`AgentFile` is the root dataclass, parsed from YAML via `AgentFile.from_path()`. Key properties:

- `system_prompt` — assembles the agent persona from `role`, `goal`, `instructions`, `constraints` fields
- `image_name(tag)` — returns `agentfile/<slug>:<tag>`
- `validate()` — returns a list of error strings (empty = valid); requires at least one tool

Sub-models:


| Dataclass       | Fields                                                                                        |
| --------------- | --------------------------------------------------------------------------------------------- |
| `Tool`          | `name`, `source`, `actions`; methods `is_mcp()`, `is_composio()`, `mcp_name`, `composio_app`  |
| `Governance`    | `max_budget_per_run`, `human_approval` (`HumanApproval`), `rate_limit`                        |
| `HumanApproval` | `enabled`, `actions`                                                                          |
| `Persistence`   | `provider` (`"postgres"`), `url` (supports `${ENV_VAR}` syntax)                               |
| `Execution`     | `mode` (`"direct"` | `"planned"`), `verify_steps`, `max_steps`, `on_step_failure`, `verifier` |
| `Verifier`      | `provider`, `model`, `max_tokens` — the LLM used for step verification                        |


### MCP Registry (`agentfile/core/mcp_registry.py`)

Two-layer registry: built-in servers + user overrides in `~/.agentfile/mcp.yaml`. Tools declared as `source: mcp://brave-search` in `agentfile.yaml` are resolved here at build time into subprocess specs `(type, package, args, env_keys)`.

### Templates (`agentfile/templates/`)

All three templates are Jinja2 (`.j2`):

- `Dockerfile.j2` — installs provider SDKs, MCP package, Composio SDK (provider-specific), and psycopg3; conditionally adds Node.js or `uv` based on MCP server types
- `entrypoint.py.j2` — the generated agent runtime (see below)
- `agentfile.yaml.j2` — initial scaffold template

### Multi-Provider Support

The `entrypoint.py.j2` template handles three providers at runtime:

- **Anthropic**: `stop_reason == "tool_use"` branch; tool format uses `input_schema`
- **OpenAI**: `finish_reason == "tool_calls"` branch; tool format uses `parameters`
- **Google Gemini**: `function_call` parts; schema must be sanitized via `_sanitize_schema_for_gemini()` which strips `additionalProperties`, `$schema`, `$defs`, etc.

The provider/model/temperature can always be overridden at runtime via environment variables, even if different values were baked into the image at build time.

### Tool Sources

Tools are declared in `agentfile.yaml` with a `source:` field:


| Source prefix | Protocol                                           | Example             |
| ------------- | -------------------------------------------------- | ------------------- |
| `mcp://`      | MCP stdio subprocess (npx / uvx / docker / python) | `mcp://duckduckgo`  |
| `composio://` | Composio cloud action registry                     | `composio://GITHUB` |


**MCP tools** are resolved at build time via `agentfile/core/mcp_registry.py`. The entrypoint starts each MCP server as a subprocess and communicates via stdio using the MCP SDK.

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
version: "1.0"

metadata:
  name: my-agent          # used for Docker image tag slug
  description: ...
  role: ...               # composed into system_prompt
  goal: ...
  instructions: |
    ...
  constraints:
    - "..."

runtime:
  provider: anthropic     # anthropic | openai | google | mistral | groq
  model: claude-sonnet-4-6
  temperature: 0.2

tools:
  - name: web_search
    source: mcp://duckduckgo        # MCP tool — registry key after mcp://
  - name: github
    source: composio://GITHUB       # Composio tool — app name after composio://
  - name: gmail_send
    source: composio://GMAIL
    actions:                        # optional: limit to specific Composio actions
      - GMAIL_SEND_EMAIL

governance:
  max_budget_per_run: 1.00
  human_approval:
    enabled: true
    actions: [file_write, shell_exec]   # tool names that require human approval
  rate_limit: 10_requests_per_minute

execution:
  mode: planned                     # "direct" (default) | "planned"
  verify_steps: true                # call verifier LLM after each tool call
  max_steps: 10                     # cap on plan size
  on_step_failure: continue         # "abort" | "continue" | "retry_once"
  verifier:
    provider: anthropic             # defaults to agent's provider
    model: claude-haiku-4-5-20251001   # small/fast model recommended
    max_tokens: 128

persistence:
  provider: "postgres"
  url: "${DATABASE_URL}"            # ${VAR} resolved from env at runtime

triggers:
  - type: webhook
    endpoint: /run
```

### Key Constants in Generated `entrypoint.py`

- `MAX_TURNS = 20` — safety cap on the agentic tool-use loop
- `TOOL_TIMEOUT = 30` — seconds before a hanging MCP tool call is aborted
- `MAX_TOKENS = 8192` — max output tokens per LLM call
- `HISTORY_WINDOW_CHARS = 100_000` — sliding-window budget (~25k tokens); older messages trimmed before each LLM call
- `APPROVAL_POLL_INTERVAL = 5` — seconds between human-approval DB polls
- `APPROVAL_TIMEOUT = 3600` — 1 hour hard timeout for human approval

### Jinja2 Template Context Variables (passed from `build.py`)


| Variable                   | Type        | Purpose                                   |
| -------------------------- | ----------- | ----------------------------------------- |
| `agent`                    | `AgentFile` | Full agent config object                  |
| `needs_node`               | bool        | Install Node.js in Dockerfile             |
| `needs_uv`                 | bool        | Install uv in Dockerfile                  |
| `has_mcp_tools`            | bool        | Enable MCP async branch                   |
| `mcp_tool_defs`            | list        | Resolved MCP server specs                 |
| `has_composio_tools`       | bool        | Enable Composio integration               |
| `composio_tool_defs`       | list        | `[{app, actions}]`                        |
| `has_persistence`          | bool        | Enable StateStore / Checkpointer          |
| `persistence_provider`     | str         | e.g. `"postgres"`                         |
| `persistence_url_template` | str         | Raw URL with `${VAR}` placeholders        |
| `has_planned_execution`    | bool        | Enable plan-then-execute mode             |
| `verify_steps`             | bool        | Enable per-tool verification              |
| `max_plan_steps`           | int         | Cap on number of plan steps               |
| `on_step_failure`          | str         | `"abort"` | `"continue"` | `"retry_once"` |
| `has_verifier`             | bool        | Initialize separate verifier LLM client   |
| `verifier_provider`        | str         | Provider for verifier LLM                 |
| `verifier_model`           | str         | Model for verifier LLM                    |
| `verifier_max_tokens`      | int         | Max tokens for verifier response          |


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
| `AGENTFILE_MAX_TURNS` | `20` | Safety cap on the agentic tool-use loop |
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

