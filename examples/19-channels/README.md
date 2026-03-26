# 19 — Multi-Bot Channels

Two agents connected to different messaging platforms via named bots.

## Agents

| Agent | Platform | Bot | Purpose |
|-------|----------|-----|---------|
| `support-agent` | Telegram | `support_bot` | Customer support |
| `community-agent` | Discord | `community_bot` | Community engagement |

## Setup

```bash
# 1. Connect your Telegram bot
ninetrix channel connect telegram --bot support_bot

# 2. Connect your Discord bot
ninetrix channel connect discord --bot community_bot

# 3. Verify both are connected
ninetrix channel status

# 4. Build and run
ninetrix build
ninetrix run
```

## How it works

Each agent has its own `triggers` block with a `bot:` field pointing to a named bot in `~/.agentfile/channels.yaml`. Messages to each bot are routed to the corresponding agent.

### Access control

Use `allowed_ids` to restrict who can message each bot:

```yaml
triggers:
  - type: channel
    channels: [telegram]
    bot: support_bot
    allowed_ids:
      - "236242721"       # Telegram chat ID
```

Run `ninetrix channel status` to see all configured bots.
