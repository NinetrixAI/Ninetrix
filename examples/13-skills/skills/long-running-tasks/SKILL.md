---
name: long-running-tasks
version: 1.0.0
description: Never block the agent loop — use background execution for slow commands
author: ninetrix
tags: [development, operations, performance]
requires:
  tools: [builtin://shell, builtin://process_manager]
---

# Long-Running Tasks

Never block the conversation on a slow command. If a task might take more than 30 seconds, run it in the background.

## How to decide

- **Use `shell`** for commands that finish in seconds: `ls`, `grep`, `cat`, `git status`, `pip install`
- **Use `process_start`** for anything that might take longer: deploys, builds, test suites, data processing, downloads, migrations, servers

When in doubt, use `process_start`. Returning instantly is always better than blocking for minutes.

## Process

1. **Start** — use `process_start(command="...", name="descriptive-name")`
2. **Inform** — tell the user: "Started [task] in the background. I'll check on it."
3. **Check** — use `process_output(name="...")` to read progress
4. **Report** — when done, summarize the result to the user
5. **Clean up** — use `process_stop` if the task needs to be cancelled

## Rules

- Never use `shell` for: `docker build`, `npm run build`, `pytest`, `make`, deploy scripts, database migrations, `wget`/`curl` for large files, `git clone` of large repos
- Always give the background process a descriptive name (not "proc_1234")
- Check output every 30-60 seconds for long tasks — don't forget about them
- If a process fails (exit code != 0), read the full output and diagnose before retrying
- If the user asks about a running task, check `process_output` before answering — don't guess

## Anti-patterns

- Do NOT run `shell("./deploy.sh")` and block for 10 minutes — the user can't interact with you
- Do NOT start a background process and never check on it
- Do NOT start multiple instances of the same long task without stopping the first one
- Do NOT assume a process succeeded without checking its output and exit code
