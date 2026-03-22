"""Built-in catalog of well-known MCP servers — thin wrapper around tool_hub.

This module now delegates to :mod:`agentfile.core.tool_hub` which fetches from
the Ninetrix Tool Hub (GitHub registry). The ``CatalogEntry`` class and
``get()``/``list_all()`` API are preserved for backwards compatibility.

For new code, prefer using ``tool_hub`` directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agentfile.core.tool_hub import (
    ToolHubEntry,
    get as _hub_get,
    list_all as _hub_list_all,
)


@dataclass
class CatalogEntry:
    """Describes one MCP server. Backwards-compatible wrapper around ToolHubEntry."""

    description: str
    type: str                              # npx | uvx | python | docker
    package: str
    args: list[str] = field(default_factory=list)
    required_env: dict[str, str] = field(default_factory=dict)
    env_aliases: dict[str, str] = field(default_factory=dict)

    def worker_yaml_block(self) -> dict:
        block: dict = {"type": self.type, "package": self.package}
        if self.args:
            block["args"] = self.args
        if self.required_env:
            block["env"] = {}
            for var in self.required_env:
                source = next(
                    (alias for alias, canon in self.env_aliases.items() if canon == var),
                    var,
                )
                block["env"][var] = f"${{{source}}}"
        return block

    def missing_env(self) -> list[str]:
        import os
        missing = []
        for var in self.required_env:
            sources = [var] + [
                alias for alias, canon in self.env_aliases.items() if canon == var
            ]
            if not any(os.environ.get(s) for s in sources):
                missing.append(var)
        return missing

    def resolve_env_value(self, var: str) -> str | None:
        import os
        sources = [var] + [
            alias for alias, canon in self.env_aliases.items() if canon == var
        ]
        for s in sources:
            v = os.environ.get(s)
            if v:
                return v
        return None

    @classmethod
    def from_hub_entry(cls, entry: ToolHubEntry) -> "CatalogEntry":
        """Convert a ToolHubEntry to a CatalogEntry."""
        return cls(
            description=entry.description,
            type=entry.runner,
            package=entry.package,
            args=entry.args,
            required_env=entry.required_env,
            env_aliases=entry.env_aliases,
        )


# Backwards-compat: CATALOG dict (lazy-populated from hub)
class _CatalogProxy(dict):
    """Dict-like proxy that populates from the Tool Hub on first access."""
    _loaded = False
    def _ensure(self):
        if not self._loaded:
            self.update(list_all())
            self._loaded = True
    def __contains__(self, key):
        self._ensure()
        return super().__contains__(key)
    def __getitem__(self, key):
        self._ensure()
        return super().__getitem__(key)
    def __len__(self):
        self._ensure()
        return super().__len__()
    def __iter__(self):
        self._ensure()
        return super().__iter__()
    def items(self):
        self._ensure()
        return super().items()
    def values(self):
        self._ensure()
        return super().values()
    def keys(self):
        self._ensure()
        return super().keys()

CATALOG = _CatalogProxy()


def get(name: str) -> CatalogEntry | None:
    """Look up a catalog entry by name. Returns None if not found."""
    entry = _hub_get(name)
    if entry is None or entry.source_type != "mcp":
        return None
    return CatalogEntry.from_hub_entry(entry)


def list_all() -> dict[str, CatalogEntry]:
    """Return all MCP catalog entries."""
    return {
        e.name: CatalogEntry.from_hub_entry(e)
        for e in _hub_list_all(source_type="mcp")
    }
