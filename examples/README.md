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
