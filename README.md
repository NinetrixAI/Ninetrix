<div align="center">

<img src="https://raw.githubusercontent.com/Ninetrix-ai/ninetrix/main/.github/assets/logo.png" alt="Ninetrix" width="64" />

# Ninetrix

**`agentfile.yaml` is to AI agents what `Dockerfile` is to containers.**

Build, ship, and observe AI agents as portable Docker containers — multi-provider, self-hostable, and production-ready in minutes.

[![PyPI](https://img.shields.io/pypi/v/ninetrix?color=blue&label=pip%20install%20ninetrix)](https://pypi.org/project/ninetrix/)
[![License](https://img.shields.io/badge/license-Apache%202.0-green)](./LICENSE)
[![Discord](https://img.shields.io/badge/Discord-Join-5865F2)](https://discord.gg/ninetrix)

<!-- DEMO GIF -->
<!-- ![ninetrix demo](https://raw.githubusercontent.com/Ninetrix-ai/ninetrix/main/.github/assets/demo.gif) -->

</div>

---

## The Problem

Building AI agents today means choosing between:
- **Python frameworks** — hard to deploy, hard to observe, framework-locked
- **Hosted platforms** — no self-hosting, no auditability, vendor lock-in
- **Rolling your own** — glue code, no standards, reinventing the wheel every time

**Ninetrix gives you a third path:** define your agent in a 10-line YAML, ship it as a Docker container, observe it in a dashboard — and own every byte of it.

---

## Install

```bash
pip install ninetrix
# or
brew install Ninetrix-ai/tap/ninetrix
# or
uv tool install ninetrix
```

---

## 5-Minute Quickstart

```bash
# 1. Start the local stack (Postgres, API, MCP gateway, dashboard)
ninetrix dev

# 2. Scaffold a new agent
ninetrix init --name my-agent --provider anthropic

# 3. Build and run
ninetrix build
ninetrix run
```

Open **http://localhost:8000/dashboard** — watch your agent run, see every tool call, inspect the full trace.

---

## Your Agent is a YAML File

```yaml
agents:
  researcher:
    metadata:
      role: Research assistant
      goal: Answer questions accurately using web search
      instructions: Search the web, synthesize results, cite your sources.

    runtime:
      provider: anthropic          # or openai, google, mistral, groq
      model: claude-sonnet-4-6
      temperature: 0.3

    tools:
      - name: web_search
        source: mcp://brave-search
      - name: github
        source: mcp://github
```

```bash
ninetrix build
ninetrix run
ninetrix run --thread-id my-session  # resume where you left off
```

---

## Multi-Agent in One File

```yaml
agents:
  orchestrator:
    metadata:
      role: Task coordinator
      goal: Break down tasks and delegate to specialists
    runtime: { provider: anthropic, model: claude-sonnet-4-6 }
    collaborators: [researcher, writer]   # can hand off to either

  researcher:
    metadata:
      role: Web researcher
    runtime: { provider: anthropic, model: claude-haiku-4-5-20251001 }
    tools:
      - { name: web_search, source: mcp://brave-search }

  writer:
    metadata:
      role: Content writer
    runtime: { provider: openai, model: gpt-4o }    # mix providers freely
```

```bash
ninetrix up      # starts all agents on a shared Docker bridge network
ninetrix invoke  # send a message to any running agent
ninetrix trace   # render the full multi-agent call tree
ninetrix down    # clean shutdown
```

---

## What's in the Box

| Feature | Description |
|---------|-------------|
| **Any LLM provider** | Anthropic, OpenAI, Google, Mistral, Groq — switch without code changes |
| **MCP-native tooling** | Connect any MCP server (filesystem, GitHub, Slack, Notion, Brave, ...) |
| **Multi-agent orchestration** | Agents hand off to each other via `collaborators` on a Docker bridge |
| **Persistent memory** | PostgreSQL checkpoints — resume any session with `--thread-id` |
| **Human-in-the-loop** | Gate specific tool calls on human approval before executing |
| **Planned execution** | Agent generates a plan first, executes step-by-step with verification |
| **Webhook + schedule triggers** | Agents that wake up on HTTP calls or cron schedules |
| **Governance & budgets** | Set `max_budget_per_run`, approval gates, rate limits per agent |
| **Observability dashboard** | Traces, timelines, token usage — every run fully inspectable |
| **Self-hostable** | One `docker compose up` — no vendor calls, no telemetry, air-gap ready |

---

## Human-in-the-Loop

```yaml
governance:
  human_approval:
    enabled: true
    actions: [file_write, shell_exec, send_email]   # gate these tool calls
```

When the agent hits an approved action, it pauses and waits. Approve or reject from the dashboard or the CLI. Works locally and in production.

---

## MCP Tools

Any MCP server works. Add it to `~/.agentfile/mcp-worker.yaml`:

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

  - name: slack
    type: npx
    package: "@modelcontextprotocol/server-slack"
    env:
      SLACK_BOT_TOKEN: "${SLACK_BOT_TOKEN}"
```

Then reference in `agentfile.yaml`:

```yaml
tools:
  - { name: github, source: mcp://github }
  - { name: filesystem, source: mcp://filesystem }
```

---

## Self-Hosting (Enterprise)

Full stack on your infra in 60 seconds:

```bash
curl -O https://raw.githubusercontent.com/Ninetrix-ai/ninetrix/main/infra/compose/docker-compose.self-host.yml
curl -O https://raw.githubusercontent.com/Ninetrix-ai/ninetrix/main/infra/compose/.env.example
cp .env.example .env   # set your domain + API keys
docker compose -f docker-compose.self-host.yml up -d
```

- Caddy handles automatic HTTPS for your domain
- All images are public on GHCR — no build step required
- Credentials never leave your network
- Works air-gapped (no outbound calls except to LLM providers you choose)

---

## vs. Alternatives

|  | **Ninetrix** | Python frameworks | Hosted platforms | Roll your own |
|--|--|--|--|--|
| Portable YAML spec | ✅ | ❌ code only | ❌ | ❌ |
| Docker-native deploy | ✅ | ❌ | ❌ | manual |
| Self-hostable | ✅ | ❌ | ❌ | manual |
| Built-in observability | ✅ | ❌ plugin | ❌ | manual |
| Multi-provider | ✅ | partial | partial | manual |
| MCP-native | ✅ | partial | ❌ | manual |
| Human-in-the-loop | ✅ | ❌ | ❌ | manual |
| Resume sessions | ✅ | ❌ | ❌ | manual |
| Open source | ✅ | ✅ | ❌ | — |

---

## Examples

| Example | What it demonstrates |
|---------|---------------------|
| [`01-hello-world`](./examples/01-hello-world) | Single agent with web search |
| [`02-multi-agent`](./examples/02-multi-agent) | Orchestrator → researcher handoff |
| [`03-with-mcp`](./examples/03-with-mcp) | MCP tools via local gateway |
| [`04-research-crew`](./examples/04-research-crew) | 3-agent crew: researcher + writer + reviewer |
| [`05-scheduled-agent`](./examples/05-scheduled-agent) | Cron-triggered agent |
| [`06-self-hosted`](./examples/06-self-hosted) | Full self-hosted stack |

---

## Repo Structure

```
packages/cli/          pip install ninetrix  — CLI tool
packages/api/          Local API + dashboard backend
packages/mcp-gateway/  Routes tool calls to MCP workers
packages/mcp-worker/   Spawns MCP server subprocesses
packages/dashboard/    Local observability dashboard (Next.js)
infra/compose/         Docker Compose (dev + self-host)
examples/              6 ready-to-run agentfile.yaml examples
schema/v1/             JSON Schema for agentfile.yaml
```

---

## Contributing

See [CONTRIBUTING.md](./.github/CONTRIBUTING.md).

Schema proposals, new examples, provider additions, and CLI improvements are all welcome. The `agentfile.yaml` spec is the most important surface — open an issue before a PR for anything that changes it.

---

## License

Apache 2.0 — use it, fork it, build on it.

---

<div align="center">
  <a href="https://ninetrix.io/docs">Docs</a> · <a href="https://discord.gg/ninetrix">Discord</a> · <a href="https://twitter.com/ninetrix_ai">Twitter</a>
</div>
