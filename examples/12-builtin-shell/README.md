# 12 — Bash & Filesystem

An agent with full shell access inside its Docker container. No MCP servers needed — `bash` and `filesystem` tools are embedded directly in the agent runtime.

## What it can do

- Run any shell command (`ls`, `curl`, `grep`, `apt-get install`, `python3`, etc.)
- Read, write, and list files
- Install packages, run scripts, manage processes
- Inspect system info, network, logs

## Run it

```bash
ninetrix build
ninetrix run
```

Then ask it things like:

```
> What OS is this container running? Show me the kernel version and installed packages.
> Write a Python script that fetches the top 5 Hacker News stories and save it to /app/hn.py
> Install jq and curl, then fetch the GitHub API and show my public repos
> Show me all running processes and disk usage
```

## Tools

| Tool | What it does |
|------|-------------|
| `bash` | Execute any shell command with configurable timeout |
| `filesystem` | `read_file`, `write_file`, `list_dir` |

## Notes

- The agent runs as **root** inside the container — it has full access.
- Commands run in `/bin/sh` with a default 60-second timeout.
- The container is isolated — nothing escapes to the host.
- Combine with `trigger: webhook` to create an always-on DevOps bot.
