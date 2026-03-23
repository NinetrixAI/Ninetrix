"""Skill Hub — community skill registry client.

Fetches skill metadata from the Ninetrix Skills Hub (GitHub repo).
Used by: ninetrix hub search, ninetrix hub add (skills).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_SKILLS_HUB_URL = "https://raw.githubusercontent.com/Ninetrix-ai/skills-hub/main/registry.json"

_registry_cache: dict | None = None


@dataclass
class SkillHubEntry:
    """Describes one skill from the Skills Hub registry."""
    name: str
    description: str
    tags: list[str] = field(default_factory=list)
    author: str = ""
    latest_version: str = "1.0.0"
    requires_tools: list[str] = field(default_factory=list)
    companion_tool: str = ""
    tokens: int = 0

    def agentfile_snippet(self) -> str:
        ver = self.latest_version or "1.0.0"
        return f"- hub://{self.name}@{ver}"


def _fetch_url(url: str) -> str | None:
    try:
        import httpx
        resp = httpx.get(url, timeout=15, follow_redirects=True)
        if resp.status_code == 200:
            return resp.text
        return None
    except Exception:
        return None


def get_registry() -> dict:
    global _registry_cache
    if _registry_cache is not None:
        return _registry_cache

    raw = _fetch_url(_SKILLS_HUB_URL)
    if raw:
        try:
            _registry_cache = json.loads(raw)
            return _registry_cache
        except json.JSONDecodeError:
            pass

    _registry_cache = {}
    return _registry_cache


def _entry_from_raw(name: str, raw: dict) -> SkillHubEntry:
    latest = raw.get("latest", "1.0.0")
    ver_entry = raw.get("versions", {}).get(latest, {})
    return SkillHubEntry(
        name=name,
        description=raw.get("description", ""),
        tags=raw.get("tags", []),
        author=raw.get("author", ""),
        latest_version=latest,
        requires_tools=raw.get("requires", {}).get("tools", []),
        companion_tool=raw.get("companion_tool", ""),
        tokens=ver_entry.get("tokens", 0),
    )


def get(name: str) -> SkillHubEntry | None:
    registry = get_registry()
    skills = registry.get("skills", {})
    raw = skills.get(name)
    if raw is None:
        return None
    return _entry_from_raw(name, raw)


def list_all() -> list[SkillHubEntry]:
    registry = get_registry()
    skills = registry.get("skills", {})
    return [_entry_from_raw(name, raw) for name, raw in sorted(skills.items())]


def search(query: str) -> list[SkillHubEntry]:
    q = query.lower()
    results = []
    for entry in list_all():
        score = 0
        if q in entry.name.lower():
            score += 10
        if q in entry.description.lower():
            score += 5
        if any(q in tag.lower() for tag in entry.tags):
            score += 3
        if score > 0:
            results.append((score, entry))
    results.sort(key=lambda x: -x[0])
    return [entry for _, entry in results]


def reset_cache() -> None:
    global _registry_cache
    _registry_cache = None
