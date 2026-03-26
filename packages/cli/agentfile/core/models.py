"""AgentFile data model and YAML parser.

All models use Pydantic v2 BaseModel for validation, serialisation, and
JSON Schema generation.  The YAML → model reshaping (metadata/runtime nesting)
is handled by classmethod factories (_parse / _parse_agent_def), not by Pydantic
validators, so that consumers see a flat, ergonomic API.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Literal, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field


# ── Deep merge helper ─────────────────────────────────────────────────────────

def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*. Dicts are merged; all other types replace."""
    import copy
    result = copy.deepcopy(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = copy.deepcopy(val)
    return result


# ── Memory helpers ─────────────────────────────────────────────────────────────

def _parse_memory(s: str) -> int:
    """Convert a Docker-style memory string to bytes.

    Examples: '4Gi' → 4*1024³, '512Mi' → 512*1024², '4g' → 4*10⁹ (SI).
    """
    s = s.strip()
    if s.endswith("Gi"):
        return int(float(s[:-2]) * 1024 ** 3)
    if s.endswith("Mi"):
        return int(float(s[:-2]) * 1024 ** 2)
    if s.endswith("Ki"):
        return int(float(s[:-2]) * 1024)
    if s.lower().endswith("g"):
        return int(float(s[:-1]) * 10 ** 9)
    if s.lower().endswith("m"):
        return int(float(s[:-1]) * 10 ** 6)
    if s.lower().endswith("k"):
        return int(float(s[:-1]) * 10 ** 3)
    return int(s)


# ── Sub-models ────────────────────────────────────────────────────────────────

class ToolAuth(BaseModel):
    """Authentication config for a tool source."""
    model_config = ConfigDict(frozen=True)

    type: str = "bearer"
    token: str = ""
    header_name: str = ""
    username: str = ""
    password: str = ""
    query_param: str = ""


class ToolDependencies(BaseModel):
    """Extra packages to install for a tool source."""
    model_config = ConfigDict(frozen=True)

    pip: list[str] = Field(default_factory=list)
    apt: list[str] = Field(default_factory=list)
    install: str = ""  # raw shell command to install a CLI tool (from Tool Hub)


class Tool(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    source: str
    actions: list[str] = Field(default_factory=list)
    auth: Optional[ToolAuth] = None
    dependencies: Optional[ToolDependencies] = None
    base_url: str = ""

    @property
    def scheme(self) -> str:
        """Extract URI scheme: 'mcp', 'composio', 'openapi', 'builtin', etc."""
        if "://" in self.source:
            return self.source.split("://", 1)[0]
        if self.source.startswith("./") or self.source.startswith("/"):
            return "local"
        return "unknown"

    @property
    def source_path(self) -> str:
        """Extract the part after '://' or the full path for local sources."""
        if "://" in self.source:
            return self.source.split("://", 1)[1]
        return self.source

    def is_mcp(self) -> bool:
        return self.scheme == "mcp"

    def is_composio(self) -> bool:
        return self.scheme == "composio"

    def is_local(self) -> bool:
        return self.scheme == "local"

    def is_builtin(self) -> bool:
        return self.scheme == "builtin"

    def is_openapi(self) -> bool:
        return self.scheme == "openapi"

    def is_hub(self) -> bool:
        return self.scheme == "hub"

    @property
    def hub_name(self) -> str | None:
        """Extract tool name from 'hub://gh' or 'hub://gh@1.0.0'."""
        if not self.is_hub():
            return None
        return self.source_path.split("@")[0]

    @property
    def builtin_name(self) -> Optional[str]:
        """Return the builtin tool name after 'builtin://', e.g. 'shell'. None if not builtin."""
        if not self.is_builtin():
            return None
        return self.source[len("builtin://"):]

    @property
    def mcp_name(self) -> Optional[str]:
        """Return the registry key after 'mcp://', e.g. 'brave-search'. None if not MCP."""
        if not self.is_mcp():
            return None
        return self.source[len("mcp://"):]

    @property
    def composio_app(self) -> Optional[str]:
        """Return the Composio app name after 'composio://', e.g. 'GITHUB'. None if not Composio."""
        if not self.is_composio():
            return None
        return self.source[len("composio://"):]


class Skill(BaseModel):
    model_config = ConfigDict(frozen=True)
    source: str

    def is_local(self) -> bool:
        return self.source.startswith("./") or self.source.startswith("/")

    def is_hub(self) -> bool:
        return self.source.startswith("hub://")

    @property
    def hub_slug(self) -> str | None:
        """Return the skill slug from 'hub://code-review@1.0.0' → 'code-review'. None if not hub."""
        if not self.is_hub():
            return None
        raw = self.source[len("hub://"):]
        return raw.split("@")[0] if "@" in raw else raw

    @property
    def hub_version(self) -> str | None:
        """Return the pinned version from 'hub://code-review@1.0.0' → '1.0.0'. None if unpinned."""
        if not self.is_hub():
            return None
        raw = self.source[len("hub://"):]
        return raw.split("@")[1] if "@" in raw else None


class HumanApproval(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    actions: list[str] = Field(default_factory=list)
    notify_url: str = ""


class Governance(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_budget_per_run: float = 1.0
    budget_warning_usd: float = 0.0
    human_approval: HumanApproval = Field(default_factory=HumanApproval)
    rate_limit: str = "10_requests_per_minute"


class Trigger(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: Literal["webhook", "schedule", "channel"]
    endpoint: Optional[str] = None
    cron: Optional[str] = None
    port: int = 9100
    message: str = ""
    target_agent: Optional[str] = None
    channels: list[str] = Field(default_factory=list)   # channel types: ["telegram", "whatsapp"]
    session_mode: str = "per_chat"     # per_message | per_chat
    verbose: bool = True               # print channel I/O to agent console
    allowed_ids: list[str] = Field(default_factory=list)  # allowlist: only these user/chat IDs get responses
    reject_message: str = ""           # optional message sent to blocked users (empty = silent)


class Verifier(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str = ""
    model: str = ""
    max_tokens: int = 128


class ThinkingConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    model: str = ""
    provider: str = ""
    max_tokens: int = 2048
    temperature: float = 0.1
    min_input_length: int = 50
    prompt: str = ""


class Execution(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: Literal["direct", "planned"] = "direct"
    verify_steps: bool = False
    max_steps: int = 10
    on_step_failure: Literal["abort", "continue", "retry_once"] = "continue"
    verifier: Verifier = Field(default_factory=Verifier)
    thinking: ThinkingConfig = Field(default_factory=ThinkingConfig)
    durability: bool = True


class Resources(BaseModel):
    model_config = ConfigDict(frozen=True)

    cpu: Optional[float] = None
    memory: Optional[str] = None
    storage: Optional[str] = None
    base_image: Optional[str] = None
    warm_pool: bool = False


class VolumeSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = ""
    provider: Literal["local", "s3"] = "local"
    host_path: Optional[str] = None
    bucket: Optional[str] = None
    prefix: str = ""
    container_path: str = "/workspace"
    read_only: bool = False
    sync: Literal["bidirectional", "download-only", "upload-only"] = "bidirectional"


class MCPGatewayConfig(BaseModel):
    """Points agents at a remote MCP Gateway instead of spawning local MCP servers."""
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    url: str
    token: str = ""
    org_id: str = Field(default="default", alias="workspace_id")


# ── Agent definition ──────────────────────────────────────────────────────────

class AgentDef(BaseModel):
    """One agent entry under agents: in agentfile.yaml."""
    model_config = ConfigDict(frozen=False)

    name: str
    description: str = ""
    model: str = "claude-sonnet-4-6"
    provider: str = "anthropic"
    temperature: float = 0.2
    max_tokens: int = 8192
    max_turns: int = 20
    tool_timeout: int = 30
    history_window_tokens: int = 90_000
    output_type: Optional[dict[str, Any]] = None
    tools_all: bool = False  # True when tools: all
    tools: list[Tool] = Field(default_factory=list)
    skills: list[Skill] = Field(default_factory=list)
    governance: Optional[Governance] = None
    triggers: list[Trigger] = Field(default_factory=list)
    role: str = ""
    goal: str = ""
    instructions: str = ""
    constraints: list[str] = Field(default_factory=list)
    execution: Optional[Execution] = None
    collaborators: list[str] = Field(default_factory=list)
    routing_mode: Literal["agent", "auto"] = "agent"
    routing_model: str = ""
    routing_provider: str = ""
    transfer_timeout: int = 300
    packages: list[str] = Field(default_factory=list)
    resources: Resources = Field(default_factory=Resources)
    volume_refs: list[Union[str, VolumeSpec]] = Field(default_factory=list)
    serve: bool = False

    def image_name(self, tag: str = "latest") -> str:
        slug = self.name.lower().replace(" ", "-")
        return f"ninetrix/{slug}:{tag}"

    def webhook_triggers(self) -> list[Trigger]:
        return [t for t in self.triggers if t.type in ("webhook", "channel")]

    def schedule_triggers(self) -> list[Trigger]:
        return [t for t in self.triggers if t.type == "schedule"]

    @property
    def system_prompt(self) -> str:
        """Compose the agent system prompt from role, goal, instructions, and constraints."""
        parts: list[str] = []
        if self.role:
            parts.append(f"You are a {self.role}.")
        if self.goal:
            parts.append(f"Goal: {self.goal}")
        if self.instructions:
            parts.append(f"Instructions:\n{self.instructions.strip()}")
        if self.constraints:
            lines = "\n".join(f"- {c}" for c in self.constraints)
            parts.append(f"Constraints:\n{lines}")
        return "\n\n".join(parts)


# ── Parsing helpers ───────────────────────────────────────────────────────────
# These reshape the YAML's nested structure (metadata/runtime) into the flat
# model fields.  Pydantic handles type coercion and validation from here.

def _parse_governance(gov_raw: dict) -> Governance:
    ha_raw = gov_raw.get("human_approval") or {}
    return Governance(
        max_budget_per_run=float(gov_raw.get("max_budget_per_run", 1.0)),
        budget_warning_usd=float(gov_raw.get("budget_warning_usd", 0.0)),
        human_approval=HumanApproval(
            enabled=ha_raw.get("enabled", True),
            actions=list(ha_raw.get("actions") or []),
            notify_url=str(ha_raw.get("notify_url", "") or ""),
        ),
        rate_limit=str(gov_raw.get("rate_limit", "10_requests_per_minute")),
    )


def _parse_execution(exec_raw: dict) -> Execution:
    ver_raw = exec_raw.get("verifier") or {}
    thinking_raw = exec_raw.get("thinking")
    if isinstance(thinking_raw, bool):
        thinking = ThinkingConfig(enabled=thinking_raw)
    elif isinstance(thinking_raw, dict):
        thinking = ThinkingConfig(
            enabled=bool(thinking_raw.get("enabled", True)),
            model=str(thinking_raw.get("model", "") or ""),
            provider=str(thinking_raw.get("provider", "") or ""),
            max_tokens=int(thinking_raw.get("max_tokens", 2048)),
            temperature=float(thinking_raw.get("temperature", 0.1)),
            min_input_length=int(thinking_raw.get("min_input_length", 50)),
            prompt=str(thinking_raw.get("prompt", "") or ""),
        )
    else:
        thinking = ThinkingConfig()
    return Execution(
        mode=str(exec_raw.get("mode", "direct")),
        verify_steps=bool(exec_raw.get("verify_steps", False)),
        max_steps=int(exec_raw.get("max_steps", 10)),
        on_step_failure=str(exec_raw.get("on_step_failure", "continue")),
        verifier=Verifier(
            provider=str(ver_raw.get("provider", "") or ""),
            model=str(ver_raw.get("model", "") or ""),
            max_tokens=int(ver_raw.get("max_tokens", 128)),
        ),
        thinking=thinking,
        durability=bool(exec_raw.get("durability", True)),
    )


def _parse_agent_def(key: str, araw: dict) -> AgentDef:
    """Parse one entry under the agents: dict.

    Reshapes metadata/runtime nesting into flat AgentDef fields.
    """
    if not isinstance(araw, dict):
        raise ValueError(
            f"agents.{key}: expected a mapping, got {type(araw).__name__}.\n"
            f"  Fix: ensure 'agents.{key}:' has metadata/runtime/tools sub-keys."
        )
    meta = araw.get("metadata") or {}
    runtime = araw.get("runtime") or {}

    volume_refs: list[Union[str, VolumeSpec]] = []
    for v in (araw.get("volumes") or []):
        if isinstance(v, str):
            volume_refs.append(v)
        elif isinstance(v, dict):
            volume_refs.append(VolumeSpec(
                name=str(v.get("name", "") or ""),
                provider=str(v.get("provider", "local")),
                host_path=v.get("host_path"),
                bucket=v.get("bucket"),
                prefix=str(v.get("prefix", "") or ""),
                container_path=str(v.get("container_path", "/workspace")),
                read_only=bool(v.get("read_only", False)),
                sync=str(v.get("sync", "bidirectional")),
            ))

    # ── Parse tools: supports "all", plain strings, hub:// strings, and objects ──
    _ALL_BUILTIN_NAMES = {
        "bash", "shell", "filesystem", "memory", "scheduler",
        "web_search", "web_browse", "notify", "ask_user",
        "sub_agent", "code_interpreter",
    }
    raw_tools = araw.get("tools")
    tools_all = False
    parsed_tools: list[Tool] = []
    if raw_tools == "all" or (isinstance(raw_tools, str) and raw_tools.strip().lower() == "all"):
        tools_all = True
    elif isinstance(raw_tools, list):
        for t in raw_tools:
            if isinstance(t, dict):
                parsed_tools.append(Tool(**t))
            elif isinstance(t, str):
                # Known builtin name → convert to builtin:// source
                if t in _ALL_BUILTIN_NAMES:
                    parsed_tools.append(Tool(name=t, source=f"builtin://{t}"))
                else:
                    # hub:// or other URI shorthand
                    parsed_tools.append(Tool(name=t.split("://")[-1].split("@")[0], source=t))
    # Backward compat: builtin: true → tools_all
    if not tools_all and bool(araw.get("builtin", False)):
        tools_all = True

    return AgentDef(
        name=key,
        tools_all=tools_all,
        description=str(meta.get("description", "") or ""),
        model=str(runtime.get("model", "claude-sonnet-4-6")),
        provider=str(runtime.get("provider", "anthropic")),
        temperature=float(runtime.get("temperature", 0.2)),
        max_tokens=int(runtime.get("max_tokens", 8192)),
        max_turns=int(runtime.get("max_turns", 20)),
        tool_timeout=int(runtime.get("tool_timeout", 30)),
        history_window_tokens=int(runtime.get("history_window_tokens", 90_000)),
        output_type=araw.get("output_type"),
        tools=parsed_tools,
        skills=[Skill(**s) if isinstance(s, dict) else Skill(source=s) for s in (araw.get("skills") or [])],
        governance=_parse_governance(araw["governance"]) if araw.get("governance") else None,
        triggers=[Trigger(**t) for t in (araw.get("triggers") or [])],
        role=str(meta.get("role", "") or ""),
        goal=str(meta.get("goal", "") or ""),
        instructions=str(meta.get("instructions", "") or ""),
        constraints=list(meta.get("constraints") or []),
        execution=_parse_execution(araw["execution"]) if "execution" in araw else None,
        collaborators=list(araw.get("collaborators") or []),
        routing_mode=str((araw.get("routing") or {}).get("mode", "agent")),
        routing_model=str((araw.get("routing") or {}).get("model", "") or ""),
        routing_provider=str((araw.get("routing") or {}).get("provider", "") or ""),
        transfer_timeout=int(araw.get("transfer_timeout", 300)),
        packages=list(araw.get("packages") or []),
        resources=Resources(**(runtime.get("resources") or {})),
        volume_refs=volume_refs,
        serve=bool(araw.get("serve", False)),
    )


# ── Root model ─────────────────────────────────────────────────────────────────

LATEST_SCHEMA_VERSION = "1.1"


class AgentFile(BaseModel):
    """Root model — always uses agents: map (single or multi)."""
    model_config = ConfigDict(frozen=False, arbitrary_types_allowed=True)

    schema_version: str = "1.0"
    agents: dict[str, AgentDef]
    governance: Governance
    triggers: list[Trigger] = Field(default_factory=list)
    execution: Optional[Execution] = None
    volumes: dict[str, VolumeSpec] = Field(default_factory=dict)
    environments: dict[str, Any] = Field(default_factory=dict)
    mcp_gateway: Optional[MCPGatewayConfig] = None
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True, repr=False)

    # ── Factory ──────────────────────────────────────────────────────────────

    @classmethod
    def from_path(cls, path: str | Path) -> "AgentFile":
        """Parse and validate an agentfile.yaml file."""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Agentfile not found: {p}")
        if p.suffix not in (".yaml", ".yml"):
            raise ValueError(f"Agentfile must be a .yaml file, got: {p.suffix}")

        with p.open() as fh:
            data = yaml.safe_load(fh)

        if not isinstance(data, dict):
            raise ValueError(
                "Agentfile must be a YAML mapping at the root level.\n"
                "  Why: the file is empty or contains a scalar/list instead of a mapping.\n"
                "  Fix: ensure the file starts with 'agents:' or 'schema_version:'."
            )

        if "agents" not in data:
            raise ValueError(
                "Missing required 'agents:' key in agentfile.yaml.\n"
                "  Why: every agentfile must define at least one agent.\n"
                "  Fix: add an 'agents:' block. Run 'ninetrix init' for a template."
            )

        # Deprecation warning for old/missing schema_version
        version = str(data.get("schema_version") or data.get("version") or "1.0")
        if version != LATEST_SCHEMA_VERSION:
            print(
                f"WARNING: agentfile.yaml schema_version is '{version}' "
                f"(latest: '{LATEST_SCHEMA_VERSION}'). "
                f"Run 'ninetrix migrate' to upgrade.",
                file=sys.stderr,
            )

        return cls._parse(data)

    @classmethod
    def from_string(cls, yaml_text: str) -> "AgentFile":
        """Parse and validate an agentfile.yaml from a YAML string."""
        data = yaml.safe_load(yaml_text)
        if not isinstance(data, dict):
            raise ValueError("Agentfile YAML must be a mapping at the root level.")
        if "agents" not in data:
            raise ValueError("Missing required 'agents:' key.")
        return cls._parse(data)

    @classmethod
    def _parse(cls, data: dict) -> "AgentFile":
        agents_raw = data.get("agents") or {}
        agents: dict[str, AgentDef] = {}
        for key, araw in agents_raw.items():
            agents[key] = _parse_agent_def(key, araw or {})

        volumes: dict[str, VolumeSpec] = {}
        for k, v in (data.get("volumes") or {}).items():
            vol_data = dict(v)
            vol_data.setdefault("name", k)
            volumes[k] = VolumeSpec(**vol_data)

        mcp_gw_raw = data.get("mcp_gateway")
        mcp_gateway = None
        if mcp_gw_raw:
            # Support both org_id and deprecated workspace_id
            gw_data = dict(mcp_gw_raw)
            if "workspace_id" in gw_data and "org_id" not in gw_data:
                print(
                    "WARNING: mcp_gateway.workspace_id is deprecated, "
                    "use mcp_gateway.org_id instead.",
                    file=sys.stderr,
                )
                gw_data["org_id"] = gw_data.pop("workspace_id")
            mcp_gateway = MCPGatewayConfig(**gw_data)

        global_exec_raw = data.get("execution")
        schema_version = str(data.get("schema_version") or data.get("version") or "1.0")
        return cls(
            schema_version=schema_version,
            agents=agents,
            governance=_parse_governance(data.get("governance") or {}),
            triggers=[Trigger(**t) for t in (data.get("triggers") or [])],
            execution=_parse_execution(global_exec_raw) if global_exec_raw is not None else None,
            volumes=volumes,
            environments=dict(data.get("environments") or {}),
            mcp_gateway=mcp_gateway,
            raw=data,
        )

    def for_env(self, env: str | None) -> "AgentFile":
        """Return a new AgentFile with the named environment's overrides deep-merged in.

        Only the fields declared under ``environments.<env>`` are changed; everything
        else keeps the base value. Lists (e.g. ``tools``) are fully replaced by the
        override, not appended to.

        Returns *self* unchanged if *env* is None or not present in ``environments``.
        """
        if not env or env not in self.environments:
            return self
        merged = _deep_merge(self.raw, self.environments[env])
        merged.pop("environments", None)  # prevent recursive application
        return AgentFile._parse(merged)

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def is_multi_agent(self) -> bool:
        return len(self.agents) > 1

    @property
    def entry_agent(self) -> AgentDef:
        """First declared agent is the entry point."""
        return next(iter(self.agents.values()))

    # ── Effective value resolution ────────────────────────────────────────────

    def effective_execution(self, agent: AgentDef) -> Execution:
        """Agent-level execution overrides global; falls back to global; falls back to defaults."""
        return agent.execution or self.execution or Execution()

    def effective_governance(self, agent: AgentDef) -> Governance:
        """Agent-level governance overrides global; falls back to global."""
        return agent.governance or self.governance

    def effective_triggers(self, agent: AgentDef) -> list[Trigger]:
        """Collect triggers for a given agent:
        - Agent's own triggers (from agents.<key>.triggers)
        - Root triggers targeting this agent (target_agent == agent.name)
        - Root triggers with no target_agent, if this is the entry agent
        """
        result: list[Trigger] = list(agent.triggers)
        is_entry = agent is self.entry_agent
        for t in self.triggers:
            if t.target_agent == agent.name:
                result.append(t)
            elif t.target_agent is None and is_entry:
                result.append(t)
        return result

    def effective_volumes(self, agent: AgentDef) -> list[VolumeSpec]:
        """Resolve volume refs for an agent into ordered, deduplicated VolumeSpec list.

        Strings are looked up in the global volumes dict; dicts are inline definitions.
        """
        result: list[VolumeSpec] = []
        seen: set[str] = set()
        for ref in agent.volume_refs:
            if isinstance(ref, str):
                vol = self.volumes.get(ref)
                if vol is not None and ref not in seen:
                    seen.add(ref)
                    result.append(vol)
            elif isinstance(ref, VolumeSpec):
                key = ref.name or ref.container_path
                if key not in seen:
                    seen.add(key)
                    result.append(ref)
        return result

    # ── Validation ────────────────────────────────────────────────────────────

    def validate_config(self) -> list[str]:
        """Return a list of validation error strings (empty = valid).

        Named validate_config() to avoid shadowing Pydantic's validate().
        """
        errors: list[str] = []

        if not self.agents:
            errors.append("agents: at least one agent is required")
            return errors

        known_providers = (
            "anthropic", "openai", "google", "mistral", "groq",
            "deepseek", "together_ai", "openrouter", "cerebras",
            "fireworks_ai", "bedrock", "azure", "minimax",
        )

        for key, agent in self.agents.items():
            prefix = f"agents.{key}"

            if not agent.model:
                errors.append(f"{prefix}.runtime.model is required")
            if agent.provider not in known_providers:
                errors.append(
                    f"{prefix}.runtime.provider '{agent.provider}' is not a known provider"
                )
            if not (0.0 <= agent.temperature <= 2.0):
                errors.append(
                    f"{prefix}.runtime.temperature must be between 0.0 and 2.0 "
                    f"(got {agent.temperature})"
                )

            eff_gov = self.effective_governance(agent)
            if eff_gov.max_budget_per_run <= 0:
                errors.append(f"{prefix}: governance.max_budget_per_run must be > 0")

            if not agent.tools and not agent.tools_all:
                errors.append(f"{prefix}: at least one tool is required (e.g. tools: [bash] or tools: all)")
            _ALL_BUILTINS = {
                "bash", "filesystem", "memory", "scheduler",
                "web_search", "web_browse", "notify", "ask_user",
                "sub_agent", "code_interpreter",
            }
            # Backward compat: accept old name "shell" as alias for "bash"
            _known_builtins = _ALL_BUILTINS | {"shell"}
            for i, tool in enumerate(agent.tools):
                if not tool.name:
                    errors.append(f"{prefix}.tools[{i}].name is required")
                if not tool.source:
                    errors.append(f"{prefix}.tools[{i}].source is required")
                if tool.is_composio() and not tool.composio_app:
                    errors.append(
                        f"{prefix}.tools[{i}].source: invalid composio:// URI — "
                        "app name is missing"
                    )
                if tool.is_builtin() and tool.builtin_name not in _known_builtins:
                    errors.append(
                        f"{prefix}.tools[{i}].source: unknown builtin '{tool.builtin_name}' — "
                        f"valid options: {', '.join(sorted(_known_builtins))}"
                    )
                if tool.is_hub() and not tool.hub_name:
                    errors.append(
                        f"{prefix}.tools[{i}].source: hub:// tool missing name (e.g. hub://gh)"
                    )

            for i, skill in enumerate(agent.skills):
                if skill.is_hub() and not skill.hub_slug:
                    errors.append(f"{prefix}.skills[{i}]: hub:// skill missing slug (e.g. hub://code-review@1.0.0)")

            eff_exec = self.effective_execution(agent)
            if eff_exec.mode not in ("direct", "planned"):
                errors.append(
                    f"{prefix}.execution.mode '{eff_exec.mode}' "
                    "must be 'direct' or 'planned'"
                )
            if eff_exec.on_step_failure not in ("abort", "continue", "retry_once"):
                errors.append(
                    f"{prefix}.execution.on_step_failure "
                    f"'{eff_exec.on_step_failure}' "
                    "must be 'abort', 'continue', or 'retry_once'"
                )
            if eff_exec.verify_steps and eff_exec.verifier.provider:
                if eff_exec.verifier.provider not in known_providers:
                    errors.append(
                        f"{prefix}.execution.verifier.provider "
                        f"'{eff_exec.verifier.provider}' is not a known provider"
                    )

            for i, trigger in enumerate(agent.triggers):
                if trigger.type == "webhook" and not trigger.endpoint:
                    errors.append(
                        f"{prefix}.triggers[{i}]: webhook trigger requires an 'endpoint'"
                    )
                if trigger.type == "schedule" and not trigger.cron:
                    errors.append(
                        f"{prefix}.triggers[{i}]: schedule trigger requires a 'cron' expression"
                    )
                if trigger.type == "channel" and not trigger.channels:
                    errors.append(
                        f"{prefix}.triggers[{i}]: channel trigger requires a 'channels' list (e.g. ['telegram'])"
                    )

        # Validate root-level triggers
        for i, trigger in enumerate(self.triggers):
            if trigger.type == "webhook" and not trigger.endpoint:
                errors.append(f"triggers[{i}]: webhook trigger requires an 'endpoint'")
            if trigger.type == "schedule" and not trigger.cron:
                errors.append(f"triggers[{i}]: schedule trigger requires a 'cron' expression")
            if trigger.type == "channel" and not trigger.channels:
                errors.append(f"triggers[{i}]: channel trigger requires a 'channels' list (e.g. ['telegram'])")
            if trigger.target_agent and trigger.target_agent not in self.agents:
                errors.append(
                    f"triggers[{i}].target_agent '{trigger.target_agent}' "
                    "does not reference a known agent"
                )

        # Collaborator reference checks — always run (regardless of agent count)
        agent_keys = set(self.agents.keys())
        for key, agent in self.agents.items():
            prefix = f"agents.{key}"
            for cname in agent.collaborators:
                if cname == key:
                    errors.append(
                        f"{prefix}.collaborators: self-reference '{cname}' is not allowed"
                    )
                elif cname not in agent_keys:
                    errors.append(
                        f"{prefix}.collaborators: '{cname}' does not reference a known agent"
                    )

        return errors

    # Keep backward-compatible alias
    def validate(self) -> list[str]:  # type: ignore[override]
        """Backward-compatible alias for validate_config()."""
        return self.validate_config()
