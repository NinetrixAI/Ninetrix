# Examples

Each folder contains a self-contained `agentfile.yaml` you can build and run immediately.

| Example | What it shows |
|---------|--------------|
| [01-hello-world](./01-hello-world/) | Minimal agent with a single web search tool |
| [02-multi-agent](./02-multi-agent/) | Orchestrator that delegates to a specialist |
| [03-with-mcp](./03-with-mcp/) | Agent using MCP tools via the local gateway |
| [04-research-crew](./04-research-crew/) | Three-agent crew: researcher, writer, reviewer |
| [05-scheduled-agent](./05-scheduled-agent/) | Agent triggered on a cron schedule |
| [06-self-hosted](./06-self-hosted/) | Full self-hosted stack with docker-compose |
| [07-local-tools](./07-local-tools/) | Custom tools for the data assistant agent |
| [08-timing-benchmark](./08-timing-benchmark/) | Measures tool latency across cache, network, CPU, and batch I/O |
| [09-budget-limit](./09-budget-limit/) | Hard cost cap with `max_budget_per_run` — agent is stopped when budget is exhausted |
| [10-rate-limit](./10-rate-limit/) | Throttle LLM calls with `rate_limit` — shows automatic delays between requests |

## Quick start

```bash
pipx install ninetrix
or 
curl -fsSL https://install.ninetrix.io | sh
# Start the local stack
ninetrix dev

# In a new terminal, build and run any example
cd examples/01-hello-world
ninetrix build
ninetrix run
```
