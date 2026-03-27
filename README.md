<div align="center">

<img src="https://raw.githubusercontent.com/NinetrixAI/Ninetrix/main/.github/assets/logo.png" alt="Ninetrix" width="64" />

# Ninetrix

**The open standard for AI agents.**

Define agents in YAML. Run them in containers. Own your infrastructure.

[![PyPI](https://img.shields.io/pypi/v/ninetrix?color=blue&label=pip%20install%20ninetrix)](https://pypi.org/project/ninetrix/)
[![License](https://img.shields.io/badge/license-Apache%202.0-green)](./LICENSE)
[![Discord](https://img.shields.io/badge/Discord-Join-5865F2)](https://discord.gg/V4yFxnptbk)
[![Docs](https://img.shields.io/badge/docs-ninetrix.io-8B5CF6)](https://docs.ninetrix.io)

<!-- ![ninetrix demo](https://raw.githubusercontent.com/NinetrixAI/Ninetrix/main/.github/assets/demo.gif) -->

</div>

---

## Why Ninetrix

Building AI agents today means choosing between Python frameworks that are hard to deploy, hosted platforms that lock you in, or rolling your own glue code with no standards.

Ninetrix gives you a third path: **one YAML file, one Docker container, full control.**

```yaml
# agentfile.yaml — that's your entire agent definition
agents:
  assistant:
    metadata:
      role: Research assistant
      goal: Answer questions using web search
      instructions: Search the web, synthesize, cite sources.
    runtime:
      provider: anthropic
      model: claude-sonnet-4-6
    tools:
      - name: web_search
        source: mcp://brave-search
```

```bash
curl -fsSL https://install.ninetrix.io | sh
ninetrix build
ninetrix run
```

That's it. Your agent runs in an isolated Docker container with full observability, checkpointing, and budget controls. No framework lock-in, no vendor dependency.

---

## Quick Start

```bash
# Install (auto-detects pipx, uv, or pip3)
curl -fsSL https://install.ninetrix.io | sh

# Or manually: pip install ninetrix
# Or:          uv tool install ninetrix

# Start the local stack (Postgres, API, dashboard, MCP gateway)
ninetrix dev

# Scaffold a new agent
ninetrix init --name my-agent --provider anthropic

# Build the Docker image and run it
ninetrix build
ninetrix run
```

Open **http://localhost:8000/dashboard** to watch your agent run — every tool call, every message, full trace timeline.

---

## What You Get

| Feature | How it works |
|---------|-------------|
| **13 LLM providers** | Anthropic, OpenAI, Google, Mistral, Groq, DeepSeek, Together AI, OpenRouter, Cerebras, Fireworks, AWS Bedrock, Azure, MiniMax — switch with one line |
| **MCP tools** | Any MCP server works out of the box (GitHub, Slack, Notion, filesystem, Brave Search, ...) |
| **Hub tools** | `hub://gh`, `hub://slack` — community tool registry with companion skills |
| **Built-in tools** | `bash`, `filesystem`, `web_search`, `web_browse`, `memory`, `code_interpreter`, `sub_agent` |
| **Multi-agent** | Agents hand off to each other via `collaborators` on a shared Docker network |
| **Channels** | Connect Telegram, Discord, or WhatsApp bots — your agent responds to real users |
| **Human-in-the-loop** | Gate tool calls on human approval. Dashboard or CLI. |
| **Planned execution** | Agent generates a plan first, executes step-by-step with verification |
| **Budget caps** | `max_budget_per_run: 0.50` — hard-stop when spend hits the limit |
| **Persistent sessions** | PostgreSQL checkpoints — `ninetrix run --thread-id my-session` resumes where you left off |
| **Triggers** | Webhook and cron schedule triggers — agents that wake up on demand |
| **Observability** | Traces, timelines, token usage, cost per run — every run fully inspectable |
| **Self-hostable** | One `docker compose up` — no vendor calls, no telemetry, air-gap ready |
| **Skills** | Reusable prompt playbooks injected at build time — teach agents HOW to use their tools |

---

## Channels — Connect Messaging Platforms

Your agent can talk to real users on Telegram, Discord, and WhatsApp:

```bash
# Connect a Telegram bot
ninetrix channel connect telegram

# Add the trigger to your agentfile.yaml
```

```yaml
triggers:
  - type: channel
    channels: ["telegram"]
    session_mode: per_chat      # same user = same conversation
```

```bash
ninetrix run   # your agent is now live on Telegram
```

Works with Discord (WebSocket gateway) and WhatsApp (QR code pairing) the same way. Multiple bots, access control, and session management built in.

---

## Multi-Agent

```yaml
agents:
  orchestrator:
    metadata:
      role: Task coordinator
      goal: Break down tasks and delegate to specialists
    runtime: { provider: anthropic, model: claude-sonnet-4-6 }
    collaborators: [researcher, writer]

  researcher:
    metadata: { role: Web researcher }
    runtime: { provider: anthropic, model: claude-haiku-4-5-20251001 }
    tools:
      - { name: web_search, source: mcp://brave-search }

  writer:
    metadata: { role: Content writer }
    runtime: { provider: openai, model: gpt-4o }
```

```bash
ninetrix up      # starts all agents on a shared Docker network
ninetrix invoke  # send a message to any running agent
ninetrix trace   # render the full multi-agent call tree
ninetrix down    # clean shutdown
```

Mix providers freely. Each agent runs in its own container.

---

## Human-in-the-Loop

```yaml
governance:
  human_approval:
    enabled: true
    actions: [file_write, shell_exec, send_email]
```

Agent hits an approved action → pauses → waits for you. Approve or reject from the dashboard or CLI.

---

## MCP Tools

Any MCP server works. Configure in `~/.agentfile/mcp-worker.yaml`:

```yaml
servers:
  - name: github
    type: npx
    package: "@modelcontextprotocol/server-github"
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "${GITHUB_TOKEN}"

  - name: filesystem
    type: npx
    package: "@modelcontextprotocol/server-filesystem"
    args: ["${HOME}/projects"]
```

Reference in your agent:

```yaml
tools:
  - { name: github, source: mcp://github }
  - { name: filesystem, source: mcp://filesystem }
```

Or use the Tool Hub for community-maintained tools with zero config:

```yaml
tools:
  - { name: gh, source: hub://gh }           # GitHub via hub
  - { name: slack, source: hub://slack }      # Slack via hub
```

```bash
ninetrix hub search github    # search the registry
ninetrix tools info gh        # see credentials, deps, companion skills
```

---

## Python SDK — Custom Tools

Write tools in Python and use them directly — no MCP server needed:

```python
from ninetrix import Tool

@Tool
def calculate_stats(numbers: list[float]) -> dict:
    """Return mean, min, max for a list of numbers."""
    return {"mean": sum(numbers) / len(numbers), "min": min(numbers), "max": max(numbers)}
```

```yaml
tools:
  - name: my_tools
    source: ./tools/my_tools.py
```

Install the SDK: `pip install ninetrix-sdk`

---

## Self-Hosting

Full stack on your infrastructure in 60 seconds:

```bash
curl -O https://raw.githubusercontent.com/NinetrixAI/Ninetrix/main/infra/compose/docker-compose.self-host.yml
curl -O https://raw.githubusercontent.com/NinetrixAI/Ninetrix/main/infra/compose/.env.example
cp .env.example .env
docker compose -f docker-compose.self-host.yml up -d
```

- Automatic HTTPS via Caddy
- All images public on GHCR — no build step
- Credentials never leave your network
- Air-gap ready — no outbound calls except to LLM providers you choose

---

## vs. Alternatives

|  | **Ninetrix** | Python frameworks | Hosted platforms |
|--|--|--|--|
| Portable YAML spec | Yes | No — code only | No |
| Container-isolated | Yes | No | No |
| Self-hostable | Yes | Partial | No |
| Built-in observability | Yes | Plugin required | Vendor-locked |
| 13 LLM providers | Yes | Partial | Partial |
| MCP-native tools | Yes | Partial | No |
| Human-in-the-loop | Yes | No | No |
| Messaging channels | Yes | No | No |
| Resume sessions | Yes | No | No |
| Budget caps | Yes | No | No |
| Open source | Yes | Yes | No |

---

## Examples

22 ready-to-run examples in [`examples/`](./examples/):

| Example | What it shows |
|---------|-------------|
| [`01-hello-world`](./examples/01-hello-world) | Single agent with web search |
| [`02-multi-agent`](./examples/02-multi-agent) | Orchestrator → researcher handoff |
| [`03-with-mcp`](./examples/03-with-mcp) | MCP tools via local gateway |
| [`04-research-crew`](./examples/04-research-crew) | 3-agent crew: researcher + writer + reviewer |
| [`05-scheduled-agent`](./examples/05-scheduled-agent) | Cron-triggered agent |
| [`06-self-hosted`](./examples/06-self-hosted) | Full self-hosted stack |
| [`07-local-tools`](./examples/07-local-tools) | Custom Python `@Tool` decorator |
| [`08-timing-benchmark`](./examples/08-timing-benchmark) | Performance benchmarking |
| [`09-budget-limit`](./examples/09-budget-limit) | Budget caps and cost tracking |
| [`10-rate-limit`](./examples/10-rate-limit) | Rate limiting |
| [`11-tavily-search`](./examples/11-tavily-search) | Tavily search integration |
| [`12-builtin-shell`](./examples/12-builtin-shell) | Built-in bash/shell tools |
| [`13-skills`](./examples/13-skills) | Prompt-layer skills |
| [`14-all-builtins`](./examples/14-all-builtins) | Every built-in tool |
| [`15-openapi-tools`](./examples/15-openapi-tools) | OpenAPI spec as tools |
| [`16-custom-deps`](./examples/16-custom-deps) | Custom pip/apt dependencies |
| [`17-handoff`](./examples/17-handoff) | Agent-to-agent handoff |
| [`18-sub-agents`](./examples/18-sub-agents) | Sub-agent spawning |
| [`19-channels`](./examples/19-channels) | Telegram/Discord/WhatsApp |
| [`20-channel-collab`](./examples/20-channel-collab) | Multi-agent via channels |
| [`21-volumes`](./examples/21-volumes) | Persistent volumes |
| [`22-ollama`](./examples/22-ollama) | Local models via Ollama |

---

## Repo Structure

```
packages/
  cli/           pip install ninetrix — the CLI
  api/           Local API + dashboard backend
  channels/      Telegram, Discord, WhatsApp adapters
  mcp-gateway/   Routes tool calls to MCP workers
  mcp-worker/    Spawns MCP server subprocesses
  dashboard/     Local observability dashboard (Next.js)
infra/compose/   Docker Compose (dev + self-host)
examples/        22 ready-to-run examples
schema/v1/       JSON Schema for agentfile.yaml
```

---

## Contributing

See [CONTRIBUTING.md](./.github/CONTRIBUTING.md).

Schema proposals, new examples, provider additions, tool hub manifests, and CLI improvements are all welcome. The `agentfile.yaml` spec is the most important surface — open an issue before a PR for anything that changes it.

---

## License

Apache 2.0 — use it, fork it, build on it.

---

<div align="center">
  <a href="https://docs.ninetrix.io">Docs</a> · <a href="https://pypi.org/project/ninetrix/">PyPI</a> · <a href="https://discord.gg/V4yFxnptbk">Discord</a> · <a href="https://x.com/NinetrixAI">Twitter</a> · <a href="https://ninetrix.io">Website</a>
</div>
