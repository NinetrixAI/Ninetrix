---
name: error-handling-playbook
version: 1.0.0
description: Systematic recovery when tool calls fail — retry, fallback, or explain
author: ninetrix
tags: [development, reliability, error-handling]
requires:
  tools: []
---

# Error Handling Playbook

When a tool call fails, do NOT just apologize. Follow this recovery ladder:

## Step 1 — Retry (if transient)

If the error looks transient (timeout, rate limit, 5xx, network error):
- Wait a moment, then retry the same call once
- If it succeeds on retry, continue normally — no need to mention the hiccup

## Step 2 — Fallback (if retry fails)

If retry also fails, look for an alternative path:
- Can a different tool achieve the same result? Use it
- Can you answer from context or prior knowledge without the tool? Do that
- Can you complete a partial version of the task? Deliver what you can

## Step 3 — Explain (if no fallback exists)

Only when you've exhausted retries and fallbacks:
- Tell the user what you tried and what failed (in plain language, no stack traces)
- Explain what the user can do: "Try again in a few minutes" or "You can do this manually by..."
- Offer to continue with the rest of the task if only one step failed

## Rules

- Never silently swallow errors — always make progress or tell the user
- Never dump raw error messages or JSON to the user
- Never give up after a single failure without trying alternatives
- Log the error details internally (they help with debugging) but present a clean summary to the user
