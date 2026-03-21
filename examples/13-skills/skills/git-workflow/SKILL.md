---
name: git-workflow
version: 1.0.0
description: Git branching and commit message conventions
author: ninetrix
tags: [development, git, workflow]
requires:
  tools: [builtin://shell]
---

# Git Workflow

## Branch naming

- Feature branches: `feat/<short-description>`
- Bug fixes: `fix/<short-description>`
- Chores: `chore/<short-description>`

## Commit messages

Use conventional commits: `type(scope): description`

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`

Examples:
- `feat(auth): add OAuth2 login flow`
- `fix(api): handle null response from upstream`

## Rules

- Never force-push to `main` or `master`
- Always create a branch for changes — never commit directly to main
- Keep commits atomic — one logical change per commit
- Write commit messages in imperative mood ("add feature" not "added feature")
- Rebase feature branches on main before merging
