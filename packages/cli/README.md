# ninetrix

Build and deploy AI agents as Docker containers. Define your agent in YAML, ship it anywhere Docker runs.

```bash
pip install ninetrix
```

---

## Quickstart

```bash
# Scaffold a new agent
ninetrix init --name my-agent --provider anthropic

# Build the container image
ninetrix build --file ninetrix.yaml

# Run it interactively
ninetrix run --file ninetrix.yaml
```

## Multi-agent crews

```bash
# Start all agents on a shared Docker network
ninetrix up --file ninetrix.yaml

# Trigger the orchestrator
ninetrix invoke --agent orchestrator -m "Research Python history and write a summary"

# Stream logs from all agents
ninetrix logs --file ninetrix.yaml

# Visualize the execution trace
ninetrix trace --thread-id <id>

# Tear down
ninetrix down --file ninetrix.yaml
```

## ninetrix.yaml

```yaml
agents:
  orchestrator:
    metadata:
      role: "Research Orchestrator"
      goal: "Coordinate search and synthesis"
    runtime:
      provider: anthropic
      model: claude-sonnet-4-6
    tools:
      - { name: search, source: mcp://duckduckgo }
    collaborators: [researcher, writer]
    governance:
      max_budget_per_run: 1.00
      human_approval: true
    triggers:
      - type: webhook
        endpoint: /run

  researcher:
    runtime: { model: claude-haiku-4-5-20251001 }
    tools:
      - { name: search, source: mcp://duckduckgo }
      - { name: files,  source: mcp://filesystem }

  writer:
    runtime: { model: claude-sonnet-4-6, temperature: 0.7 }
    tools:
      - { name: files, source: mcp://filesystem }
```

## Commands

| Command | Description |
|---|---|
| `ninetrix init` | Scaffold a new `ninetrix.yaml` |
| `ninetrix build` | Build container images |
| `ninetrix run` | Run a single agent interactively |
| `ninetrix up` | Start all agents on a Docker bridge network |
| `ninetrix down` | Stop and remove all crew containers |
| `ninetrix status` | Show running agent containers |
| `ninetrix logs` | Stream logs from all agents |
| `ninetrix invoke` | POST a message to a running agent |
| `ninetrix trace` | Render a multi-agent execution tree |
| `ninetrix mcp list` | List available MCP tool servers |

## Environment variables

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `OPENAI_API_KEY` | OpenAI API key |
| `DATABASE_URL` | PostgreSQL URL for persistence |
| `ninetrix_PROVIDER` | Override model provider at runtime |
| `ninetrix_MODEL` | Override model at runtime |

## Requirements

- Python 3.10+
- Docker

## License

MIT
