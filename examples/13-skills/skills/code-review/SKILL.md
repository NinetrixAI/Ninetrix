---
name: code-review
version: 1.0.0
description: Systematic code review playbook
author: ninetrix
tags: [development, review, security]
requires:
  tools: [builtin://shell]
---

# Code Review

When reviewing code, follow this process:

1. **Read the diff** — understand what changed and why
2. **Check correctness** — look for logic errors, edge cases, off-by-one bugs
3. **Check security** — SQL injection, XSS, command injection, secrets in code
4. **Check style** — consistent naming, no dead code, clear variable names
5. **Check tests** — are new code paths covered? Are edge cases tested?

## Severity levels

- **Critical**: Security vulnerabilities, data loss risks, crashes
- **Major**: Logic errors, missing error handling, performance issues
- **Minor**: Style issues, naming, documentation gaps

## Rules

- Always explain *why* something is a problem, not just *what*
- Suggest a concrete fix for every issue found
- Acknowledge good patterns — don't only report negatives
- Never approve code with Critical issues
