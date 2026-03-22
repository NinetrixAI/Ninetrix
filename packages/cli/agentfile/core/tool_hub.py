"""Tool Hub — community tool registry client.

Fetches tool metadata from the Ninetrix Tool Hub (GitHub repo). Falls back to
a built-in copy of core tools when offline.

Used by:
  ninetrix tools search/info/list/add  — CLI commands
  ninetrix mcp add/catalog              — delegates here
  template_context.py                    — resolve tool metadata at build time

The Tool Hub replaces the hardcoded mcp_catalog.py as the single source of
truth for tool integrations.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_TOOLS_HUB_BASE = "https://raw.githubusercontent.com/Ninetrix-ai/tools-hub/main"
_TOOLS_HUB_URL = f"{_TOOLS_HUB_BASE}/registry.json"

# In-memory cache — one fetch per CLI session.
_registry_cache: dict | None = None


# ── Data class ────────────────────────────────────────────────────────────────

@dataclass
class ToolHubEntry:
    """Describes one tool from the Tool Hub registry."""

    name: str
    description: str
    source_type: str                        # "mcp" | "openapi" | "plugin" | "local"
    verified: bool = False
    tags: list[str] = field(default_factory=list)
    # MCP fields
    runner: str = ""                        # npx | uvx | python | docker
    package: str = ""
    args: list[str] = field(default_factory=list)
    # OpenAPI fields
    spec_url: str = ""
    base_url: str = ""
    # Plugin fields
    pip_package: str = ""
    # Local @Tool fields
    files: list[str] = field(default_factory=list)
    file_hashes: dict[str, str] = field(default_factory=dict)
    # Dependencies (pip/apt)
    pip_deps: list[str] = field(default_factory=list)
    apt_deps: list[str] = field(default_factory=list)
    # Companion skills (oven + baker pattern)
    skill_set: list[str] = field(default_factory=list)
    # Credentials
    credentials: dict[str, dict[str, Any]] = field(default_factory=dict)
    credential_aliases: dict[str, str] = field(default_factory=dict)
    # Version
    latest_version: str = "1.0.0"

    # ── Backwards-compat with CatalogEntry ────────────────────────────

    @property
    def type(self) -> str:
        """Runner type (npx/uvx/python/docker). Compat with CatalogEntry."""
        return self.runner

    @property
    def required_env(self) -> dict[str, str]:
        """Required env vars as {var: label}. Compat with CatalogEntry."""
        return {
            var: spec.get("label", var)
            for var, spec in self.credentials.items()
            if spec.get("required", False)
        }

    @property
    def env_aliases(self) -> dict[str, str]:
        """Env var aliases. Compat with CatalogEntry."""
        return dict(self.credential_aliases)

    def worker_yaml_block(self) -> dict:
        """Return the server block for mcp-worker.yaml. Compat with CatalogEntry."""
        block: dict = {"type": self.runner, "package": self.package}
        if self.args:
            block["args"] = self.args
        if self.required_env:
            block["env"] = {}
            for var in self.required_env:
                source = next(
                    (alias for alias, canon in self.credential_aliases.items() if canon == var),
                    var,
                )
                block["env"][var] = f"${{{source}}}"
        return block

    def missing_env(self) -> list[str]:
        """Return list of required env vars not set on the host."""
        missing = []
        for var in self.required_env:
            sources = [var] + [
                alias for alias, canon in self.credential_aliases.items() if canon == var
            ]
            if not any(os.environ.get(s) for s in sources):
                missing.append(var)
        return missing

    def resolve_env_value(self, var: str) -> str | None:
        """Return the value of var (or its alias) from the host environment."""
        sources = [var] + [
            alias for alias, canon in self.credential_aliases.items() if canon == var
        ]
        for s in sources:
            v = os.environ.get(s)
            if v:
                return v
        return None

    def agentfile_snippet(self) -> str:
        """Return the YAML snippet to add this tool to agentfile.yaml."""
        if self.source_type == "mcp":
            return f"- name: {self.name}\n  source: mcp://{self.name}"
        elif self.source_type == "openapi":
            return f"- name: {self.name}\n  source: openapi://{self.spec_url}"
        elif self.source_type == "plugin":
            return f"- name: {self.name}\n  source: {self.name}://default"
        elif self.source_type == "local":
            lines = [f"- name: {self.name}"]
            lines.append(f"  source: ./tools/{self.files[0]}" if self.files else f"  source: ./tools/{self.name}.py")
            if self.pip_deps or self.apt_deps:
                lines.append("  dependencies:")
                if self.pip_deps:
                    lines.append("    pip: [" + ", ".join(self.pip_deps) + "]")
                if self.apt_deps:
                    lines.append("    apt: [" + ", ".join(self.apt_deps) + "]")
            return "\n".join(lines)
        return f"- name: {self.name}\n  source: mcp://{self.name}"

    def fetch_files(self) -> dict[str, str]:
        """Fetch code files from GitHub and verify SHA256 hashes.

        Returns:
            Dict of {filename: content} for all verified files.

        Raises:
            RuntimeError: If a file can't be fetched or hash doesn't match.
        """
        import hashlib

        if self.source_type != "local" or not self.files:
            return {}

        result: dict[str, str] = {}
        for filename in self.files:
            url = f"{_TOOLS_HUB_BASE}/tools/{self.name}/{filename}"
            content = _fetch_url(url)
            if content is None:
                raise RuntimeError(f"Failed to fetch {filename} from Tool Hub")

            # Verify SHA256 hash
            expected_hash = self.file_hashes.get(filename)
            if expected_hash:
                actual_hash = hashlib.sha256(content.encode()).hexdigest()
                if actual_hash != expected_hash:
                    raise RuntimeError(
                        f"SHA256 mismatch for {filename}!\n"
                        f"  Expected: {expected_hash}\n"
                        f"  Actual:   {actual_hash}\n"
                        f"  The file may have been tampered with."
                    )

            result[filename] = content

        return result


# ── Registry fetch + cache ────────────────────────────────────────────────────

def _fetch_url(url: str) -> str | None:
    """Fetch a URL and return text content, or None on failure."""
    try:
        import httpx
        resp = httpx.get(url, timeout=15, follow_redirects=True)
        if resp.status_code == 200:
            return resp.text
        return None
    except Exception:
        return None


def _disk_cache_path() -> Path:
    """Return the path for the on-disk registry cache."""
    return Path.home() / ".agentfile" / "cache" / "tools-hub-registry.json"


def get_registry() -> dict:
    """Fetch and cache the Tool Hub registry.json.

    Resolution order:
    1. In-memory cache (per CLI session)
    2. HTTP fetch from GitHub
    3. On-disk cache (~/.agentfile/cache/tools-hub-registry.json)
    4. Built-in fallback (hardcoded core tools)
    """
    global _registry_cache
    if _registry_cache is not None:
        return _registry_cache

    # Try HTTP fetch
    raw = _fetch_url(_TOOLS_HUB_URL)
    if raw:
        try:
            _registry_cache = json.loads(raw)
            # Update disk cache
            try:
                cache_path = _disk_cache_path()
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(raw)
            except Exception:
                pass
            return _registry_cache
        except json.JSONDecodeError:
            pass

    # Try disk cache
    try:
        cache_path = _disk_cache_path()
        if cache_path.exists():
            _registry_cache = json.loads(cache_path.read_text())
            return _registry_cache
    except Exception:
        pass

    # Fallback to built-in
    _registry_cache = _BUILTIN_FALLBACK
    return _registry_cache


def _entry_from_raw(name: str, raw: dict) -> ToolHubEntry:
    """Convert a raw registry entry dict to a ToolHubEntry."""
    source = raw.get("source", {})
    deps = raw.get("dependencies", {})
    # Get file hashes from the latest version entry
    versions = raw.get("versions", {})
    latest_ver = raw.get("latest", "1.0.0")
    ver_entry = versions.get(latest_ver, {})

    return ToolHubEntry(
        name=name,
        description=raw.get("description", ""),
        source_type=source.get("type", "mcp"),
        verified=raw.get("verified", False),
        tags=raw.get("tags", []),
        runner=source.get("runner", ""),
        package=source.get("package", ""),
        args=source.get("args", []),
        spec_url=source.get("spec_url", ""),
        base_url=source.get("base_url", ""),
        pip_package=source.get("pip_package", ""),
        files=source.get("files", []),
        file_hashes=ver_entry.get("file_hashes", {}),
        pip_deps=deps.get("pip", []),
        apt_deps=deps.get("apt", []),
        credentials=raw.get("credentials", {}),
        credential_aliases=raw.get("credential_aliases", {}),
        skill_set=raw.get("skill_set", []),
        latest_version=latest_ver,
    )


# ── Public API ────────────────────────────────────────────────────────────────

def get(name: str) -> ToolHubEntry | None:
    """Look up a tool by name. Returns None if not found."""
    registry = get_registry()
    tools = registry.get("tools", {})
    raw = tools.get(name)
    if raw is None:
        return None
    return _entry_from_raw(name, raw)


def list_all(*, source_type: str | None = None, verified_only: bool = False) -> list[ToolHubEntry]:
    """Return all tools, optionally filtered."""
    registry = get_registry()
    tools = registry.get("tools", {})
    result = []
    for name, raw in sorted(tools.items()):
        entry = _entry_from_raw(name, raw)
        if source_type and entry.source_type != source_type:
            continue
        if verified_only and not entry.verified:
            continue
        result.append(entry)
    return result


def search(query: str) -> list[ToolHubEntry]:
    """Search tools by name, description, or tags."""
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
    """Clear the in-memory cache. Used by tests."""
    global _registry_cache
    _registry_cache = None


# ── Built-in fallback (offline resilience) ────────────────────────────────────
# This is a snapshot of the core tools. Updated periodically.

_BUILTIN_FALLBACK: dict = {
    "version": 2,
    "generated_at": "2026-03-22T00:00:00Z",
    "tools": {
        "github": {
            "latest": "1.0.0", "description": "GitHub — repos, issues, PRs, code search, file contents",
            "tags": ["developer", "git"], "verified": True,
            "source": {"type": "mcp", "runner": "npx", "package": "@modelcontextprotocol/server-github"},
            "credentials": {"GITHUB_PERSONAL_ACCESS_TOKEN": {"label": "GitHub personal access token", "required": True}},
            "credential_aliases": {"GITHUB_TOKEN": "GITHUB_PERSONAL_ACCESS_TOKEN"},
        },
        "slack": {
            "latest": "1.0.0", "description": "Slack — send messages, list channels, read threads",
            "tags": ["communication"], "verified": True,
            "source": {"type": "mcp", "runner": "npx", "package": "@modelcontextprotocol/server-slack"},
            "credentials": {"SLACK_BOT_TOKEN": {"label": "Slack bot token (xoxb-...)", "required": True}, "SLACK_TEAM_ID": {"label": "Slack workspace/team ID", "required": True}},
        },
        "notion": {
            "latest": "1.0.0", "description": "Notion — read/write pages, databases, blocks",
            "tags": ["productivity"], "verified": True,
            "source": {"type": "mcp", "runner": "npx", "package": "@notionhq/notion-mcp-server"},
            "credentials": {"NOTION_API_KEY": {"label": "Notion integration token (secret_...)", "required": True}},
        },
        "stripe": {
            "latest": "1.0.0", "description": "Stripe — payments, customers, invoices, subscriptions",
            "tags": ["payments", "fintech"], "verified": True,
            "source": {"type": "mcp", "runner": "npx", "package": "@stripe/agent-toolkit"},
            "credentials": {"STRIPE_SECRET_KEY": {"label": "Stripe secret key (sk_live_... or sk_test_...)", "required": True}},
        },
        "postgres": {
            "latest": "1.0.0", "description": "PostgreSQL — read/write SQL queries",
            "tags": ["database"], "verified": True,
            "source": {"type": "mcp", "runner": "npx", "package": "@modelcontextprotocol/server-postgres"},
            "credentials": {"POSTGRES_CONNECTION_STRING": {"label": "PostgreSQL connection string", "required": True}},
            "credential_aliases": {"DATABASE_URL": "POSTGRES_CONNECTION_STRING"},
        },
        "linear": {
            "latest": "1.0.0", "description": "Linear — issues, projects, teams, cycles",
            "tags": ["project-management", "developer"], "verified": True,
            "source": {"type": "mcp", "runner": "npx", "package": "@linear/linear-mcp-server"},
            "credentials": {"LINEAR_API_KEY": {"label": "Linear personal API key", "required": True}},
        },
        "brave-search": {
            "latest": "1.0.0", "description": "Brave Search — web search with privacy focus",
            "tags": ["search"], "verified": True,
            "source": {"type": "mcp", "runner": "npx", "package": "@modelcontextprotocol/server-brave-search"},
            "credentials": {"BRAVE_API_KEY": {"label": "Brave Search API key", "required": True}},
        },
        "tavily": {
            "latest": "1.0.0", "description": "Tavily Search — AI-optimised web search, free tier available",
            "tags": ["search"], "verified": True,
            "source": {"type": "mcp", "runner": "npx", "package": "tavily-mcp"},
            "credentials": {"TAVILY_API_KEY": {"label": "Tavily API key", "required": True}},
        },
        "filesystem": {
            "latest": "1.0.0", "description": "Local filesystem — read and write files in /workspace",
            "tags": ["filesystem"], "verified": True,
            "source": {"type": "mcp", "runner": "npx", "package": "@modelcontextprotocol/server-filesystem", "args": ["/workspace"]},
        },
        "fetch": {
            "latest": "1.0.0", "description": "HTTP fetch — retrieve any URL as text or HTML",
            "tags": ["http"], "verified": True,
            "source": {"type": "mcp", "runner": "uvx", "package": "mcp-server-fetch"},
        },
        "memory": {
            "latest": "1.0.0", "description": "In-process key-value memory store — persists across turns",
            "tags": ["storage"], "verified": True,
            "source": {"type": "mcp", "runner": "npx", "package": "@modelcontextprotocol/server-memory"},
        },
        "sqlite": {
            "latest": "1.0.0", "description": "SQLite — read/write SQL queries on a local .db file",
            "tags": ["database"], "verified": True,
            "source": {"type": "mcp", "runner": "uvx", "package": "mcp-server-sqlite", "args": ["--db-path", "/data/db.sqlite"]},
        },
        "puppeteer": {
            "latest": "1.0.0", "description": "Browser automation with Puppeteer (headless Chrome)",
            "tags": ["browser"], "verified": True,
            "source": {"type": "mcp", "runner": "npx", "package": "@modelcontextprotocol/server-puppeteer"},
        },
        "google-drive": {
            "latest": "1.0.0", "description": "Google Drive — list, read, search files and folders",
            "tags": ["google"], "verified": True,
            "source": {"type": "mcp", "runner": "npx", "package": "@modelcontextprotocol/server-gdrive"},
            "credentials": {"GOOGLE_DRIVE_ACCESS_TOKEN": {"label": "Google OAuth access token for Drive", "required": True}},
        },
        "duckduckgo": {
            "latest": "1.0.0", "description": "DuckDuckGo — privacy-focused web search",
            "tags": ["search"], "verified": True,
            "source": {"type": "mcp", "runner": "npx", "package": "@nicepkg/duckduckgo-mcp-server"},
        },
        "ocr": {
            "latest": "1.0.0", "description": "OCR — extract text from images and PDFs using Tesseract",
            "tags": ["ocr", "images", "pdf", "document"], "verified": True,
            "source": {"type": "local", "files": ["ocr_tools.py"]},
            "dependencies": {"pip": ["pytesseract>=0.3", "Pillow>=10.0", "pymupdf>=1.24"], "apt": ["tesseract-ocr", "tesseract-ocr-eng"]},
            "versions": {"1.0.0": {"sha256": "", "file_hashes": {"ocr_tools.py": ""}}},
        },
        "sendgrid": {
            "latest": "1.0.0", "description": "SendGrid — transactional email delivery",
            "tags": ["email", "communication"], "verified": True,
            "source": {"type": "openapi", "spec_url": "https://raw.githubusercontent.com/sendgrid/sendgrid-oai/main/oai.yaml", "base_url": "https://api.sendgrid.com"},
            "credentials": {"SENDGRID_API_KEY": {"label": "SendGrid API key", "required": True}},
        },
        "agent-browser": {
            "latest": "1.0.0", "description": "Agent Browser — headless browser automation CLI for AI agents",
            "tags": ["browser", "automation", "web", "scraping"], "verified": True,
            "source": {"type": "local", "files": ["agent_browser_tools.py"]},
            "dependencies": {"pip": [], "apt": [], "npm": ["agent-browser"]},
            "skill_set": ["hub://agent-browser@1.0.0"],
            "versions": {"1.0.0": {"sha256": "", "file_hashes": {"agent_browser_tools.py": ""}}},
        },
    },
}
