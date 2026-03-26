# 20 — Channel + Collaborators

One Telegram bot backed by three specialized agents. Users talk to one bot — routing happens behind the scenes via the collaborator system.

## Architecture

```
User → Telegram @support_bot → front-desk agent
                                    │
                                    ├── transfer_to_agent("researcher", "...")
                                    │       → researcher agent (invoke-only, no channel)
                                    │       ← result
                                    │
                                    └── transfer_to_agent("coder", "...")
                                            → coder agent (invoke-only, no channel)
                                            ← result
                                    │
                                    ← response sent back to Telegram
```

## Agents

| Agent | Role | Channel | Purpose |
|-------|------|---------|---------|
| `front-desk` | Router | Telegram `support_bot` | Receives all messages, delegates to specialists |
| `researcher` | Specialist | None (invoke-only) | Research, lookups, analysis |
| `coder` | Specialist | None (invoke-only) | Code writing, debugging |

## Setup

```bash
# 1. Connect a Telegram bot
ninetrix channel connect telegram --bot support_bot

# 2. Build all 3 agents
ninetrix build

# 3. Start the warm pool (all 3 containers)
ninetrix up

# 4. Message @support_bot on Telegram
#    "What's the current Node.js LTS version?"  → routed to researcher
#    "Write a Python fibonacci function"         → routed to coder
#    "Hey!"                                      → handled by front-desk directly
```

## How it works

- Only `front-desk` has a channel trigger — it's the only agent connected to Telegram
- `researcher` and `coder` have no triggers — they're only reachable via `/invoke` (HTTP calls on the Docker bridge network)
- When front-desk calls `transfer_to_agent("researcher", "...")`, the CLI's multi-agent system routes the request to the researcher container
- The result flows back through front-desk and is sent to the Telegram chat
