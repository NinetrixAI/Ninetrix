"""AgentFile data model and YAML parser."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


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

# ── JSON Schema (loaded once at import time) ───────────────────────────────────
_SCHEMA: dict = json.loads((Path(__file__).parent / "schema.json").read_text())


def _schema_errors(data: dict) -> list[str]:
    """Validate *data* against the agentfile JSON Schema. Returns formatted error strings."""
    try:
        import jsonschema
    except ImportError:
        return []  # graceful degradation if package is missing

    validator = jsonschema.Draft7Validator(_SCHEMA)
    errors = []
    for err in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path)):
        parts: list[str] = []
        for segment in err.absolute_path:
            if isinstance(segment, int):
                parts.append(f"[{segment}]")
            else:
                parts.append(f".{segment}" if parts else segment)
        path = "".join(parts) or "(root)"
        errors.append(f"{path}: {err.message}")
    return errors


# ── Sub-models ────────────────────────────────────────────────────────────────

@dataclass
class Tool:
    name: str
    source: str
    actions: list[str] = field(default_factory=list)  # Composio: optional action filter

    def is_mcp(self) -> bool:
        return self.source.startswith("mcp://")

    def is_composio(self) -> bool:
        return self.source.startswith("composio://")

    def is_local(self) -> bool:
        return self.source.startswith("./") or self.source.startswith("/")

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


@dataclass
class HumanApproval:
    enabled: bool = True
    actions: list[str] = field(default_factory=list)
    notify_url: str = ""  # webhook POSTed when a tool needs human approval


@dataclass
class Governance:
    max_budget_per_run: float = 1.0
    budget_warning_usd: float = 0.0  # soft warning threshold — logs + event only, no exit
    human_approval: HumanApproval = field(default_factory=HumanApproval)
    rate_limit: str = "10_requests_per_minute"


@dataclass
class Trigger:
    type: str                       # "webhook" | "schedule"
    endpoint: Optional[str] = None
    cron: Optional[str] = None
    port: int = 9100                # webhook listen port
    message: str = ""               # schedule: message injected each fire
    target_agent: Optional[str] = None  # multi-agent: which agent gets this trigger


@dataclass
class Verifier:
    provider: str = ""   # defaults to agent's provider when empty
    model: str = ""      # defaults to agent's model when empty
    max_tokens: int = 128


@dataclass
class ThinkingConfig:
    enabled: bool = False
    model: str = ""       # defaults to agent's model when empty
    provider: str = ""    # defaults to agent's provider when empty
    max_tokens: int = 2048
    temperature: float = 0.1   # analytical reasoning works best at low temp
    min_input_length: int = 50  # skip thinking for inputs shorter than this
    prompt: str = ""      # optional custom thinking instruction


@dataclass
class Execution:
    mode: str = "direct"               # "direct" | "planned"
    verify_steps: bool = False
    max_steps: int = 10
    on_step_failure: str = "continue"  # "abort" | "continue" | "retry_once"
    verifier: Verifier = field(default_factory=Verifier)
    thinking: ThinkingConfig = field(default_factory=ThinkingConfig)
    durability: bool = True            # crash-safe: auto-restart + resume from last checkpoint


@dataclass
class Resources:
    cpu: Optional[float] = None        # --cpus for docker run
    memory: Optional[str] = None       # --memory (e.g. "4Gi", "512Mi")
    storage: Optional[str] = None      # label only — no runtime enforcement yet
    base_image: Optional[str] = None   # overrides FROM in Dockerfile
    warm_pool: bool = False            # if True: no --rm in ninetrix run


@dataclass
class VolumeSpec:
    name: str = ""
    provider: str = "local"            # "local" | "s3"
    host_path: Optional[str] = None    # local provider: host path to bind-mount
    bucket: Optional[str] = None       # s3 provider: bucket name
    prefix: str = ""                   # s3 provider: key prefix
    container_path: str = "/workspace"
    read_only: bool = False
    sync: str = "bidirectional"        # "download-only" | "upload-only" | "bidirectional"


@dataclass
class MCPGatewayConfig:
    """Points agents at a remote MCP Gateway instead of spawning local MCP servers."""
    url: str                          # HTTP(S) URL of the gateway (e.g. https://mcp.ninetrix.io)
    token: str = ""                   # Organization token — Bearer auth header
    org_id: str = "default"           # Organization to scope tool access


# ── Agent definition ──────────────────────────────────────────────────────────

@dataclass
class AgentDef:
    """One agent entry under agents: in agentfile.yaml."""
    name: str                                        # key from agents: dict
    description: str = ""
    model: str = "claude-sonnet-4-6"
    provider: str = "anthropic"
    temperature: float = 0.2
    tools: list[Tool] = field(default_factory=list)
    governance: Optional[Governance] = None          # overrides global if set
    triggers: list[Trigger] = field(default_factory=list)
    role: str = ""
    goal: str = ""
    instructions: str = ""
    constraints: list[str] = field(default_factory=list)
    execution: Optional[Execution] = None
    collaborators: list[str] = field(default_factory=list)
    resources: Resources = field(default_factory=Resources)
    volume_refs: list = field(default_factory=list)  # list[str | VolumeSpec]
    serve: bool = False   # keep running and accept /invoke HTTP requests (no triggers needed)

    def image_name(self, tag: str = "latest") -> str:
        slug = self.name.lower().replace(" ", "-")
        return f"ninetrix/{slug}:{tag}"

    def webhook_triggers(self) -> list[Trigger]:
        return [t for t in self.triggers if t.type == "webhook"]

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

def _parse_tool(t: dict) -> Tool:
    return Tool(
        name=t["name"],
        source=t["source"],
        actions=list(t.get("actions") or []),
    )


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


def _parse_trigger(t: dict) -> Trigger:
    return Trigger(
        type=t["type"],
        endpoint=t.get("endpoint"),
        cron=t.get("cron"),
        port=int(t.get("port", 9100)),
        message=str(t.get("message", "") or ""),
        target_agent=t.get("target_agent"),
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


def _parse_resources(raw: dict) -> Resources:
    return Resources(
        cpu=float(raw["cpu"]) if "cpu" in raw else None,
        memory=str(raw["memory"]) if "memory" in raw else None,
        storage=str(raw["storage"]) if "storage" in raw else None,
        base_image=str(raw["base_image"]) if "base_image" in raw else None,
        warm_pool=bool(raw.get("warm_pool", False)),
    )


def _parse_volume_spec(raw: dict, name: str = "") -> VolumeSpec:
    return VolumeSpec(
        name=str(raw.get("name", name) or name),
        provider=str(raw.get("provider", "local")),
        host_path=raw.get("host_path"),
        bucket=raw.get("bucket"),
        prefix=str(raw.get("prefix", "") or ""),
        container_path=str(raw.get("container_path", "/workspace")),
        read_only=bool(raw.get("read_only", False)),
        sync=str(raw.get("sync", "bidirectional")),
    )


def _parse_mcp_gateway(raw: dict | None) -> Optional[MCPGatewayConfig]:
    if not raw:
        return None
    return MCPGatewayConfig(
        url=str(raw["url"]),
        token=str(raw.get("token", "") or ""),
        org_id=str(raw.get("org_id", raw.get("workspace_id", "default")) or "default"),
    )


def _parse_agent_def(key: str, araw: dict) -> AgentDef:
    """Parse one entry under the agents: dict."""
    meta = araw.get("metadata") or {}
    runtime = araw.get("runtime") or {}

    volume_refs: list = []
    for v in (araw.get("volumes") or []):
        if isinstance(v, str):
            volume_refs.append(v)
        elif isinstance(v, dict):
            volume_refs.append(_parse_volume_spec(v, name=str(v.get("name", ""))))

    return AgentDef(
        name=key,
        description=str(meta.get("description", "") or ""),
        model=str(runtime.get("model", "claude-sonnet-4-6")),
        provider=str(runtime.get("provider", "anthropic")),
        temperature=float(runtime.get("temperature", 0.2)),
        tools=[_parse_tool(t) for t in (araw.get("tools") or [])],
        governance=_parse_governance(araw["governance"]) if araw.get("governance") else None,
        triggers=[_parse_trigger(t) for t in (araw.get("triggers") or [])],
        role=str(meta.get("role", "") or ""),
        goal=str(meta.get("goal", "") or ""),
        instructions=str(meta.get("instructions", "") or ""),
        constraints=list(meta.get("constraints") or []),
        execution=_parse_execution(araw["execution"]) if "execution" in araw else None,
        collaborators=list(araw.get("collaborators") or []),
        resources=_parse_resources(runtime.get("resources") or {}),
        volume_refs=volume_refs,
        serve=bool(araw.get("serve", False)),
    )


# ── Root model ─────────────────────────────────────────────────────────────────

@dataclass
class AgentFile:
    """Root model — always uses agents: map (single or multi)."""
    agents: dict[str, AgentDef]         # ordered; first declared = entry point
    governance: Governance              # global default
    triggers: list[Trigger]             # global triggers
    execution: Optional[Execution] = None                         # global default
    volumes: dict[str, VolumeSpec] = field(default_factory=dict)  # named shared volumes
    environments: dict = field(default_factory=dict)              # env overlay definitions
    mcp_gateway: Optional[MCPGatewayConfig] = None               # remote MCP Gateway config
    _raw: dict = field(default_factory=dict, repr=False, compare=False)  # original parsed dict

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
            raw = yaml.safe_load(fh)

        if not isinstance(raw, dict):
            raise ValueError("Agentfile must be a YAML mapping at the root level.")

        schema_errs = _schema_errors(raw)
        if schema_errs:
            lines = "\n".join(f"  • {e}" for e in schema_errs)
            raise ValueError(f"agentfile.yaml schema errors:\n{lines}")

        return cls._parse(raw)

    @classmethod
    def _parse(cls, data: dict) -> "AgentFile":
        agents_raw = data.get("agents") or {}
        agents: dict[str, AgentDef] = {}
        for key, araw in agents_raw.items():
            agents[key] = _parse_agent_def(key, araw or {})

        volumes: dict[str, VolumeSpec] = {
            k: _parse_volume_spec(v, name=k)
            for k, v in (data.get("volumes") or {}).items()
        }

        global_exec_raw = data.get("execution")
        return cls(
            agents=agents,
            governance=_parse_governance(data.get("governance") or {}),
            triggers=[_parse_trigger(t) for t in (data.get("triggers") or [])],
            execution=_parse_execution(global_exec_raw) if global_exec_raw is not None else None,
            volumes=volumes,
            environments=dict(data.get("environments") or {}),
            mcp_gateway=_parse_mcp_gateway(data.get("mcp_gateway")),
            _raw=data,
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
        merged = _deep_merge(self._raw, self.environments[env])
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

    def validate(self) -> list[str]:
        """Return a list of validation error strings (empty = valid)."""
        errors: list[str] = []

        if not self.agents:
            errors.append("agents: at least one agent is required")
            return errors

        known_providers = ("anthropic", "openai", "google", "mistral", "groq")

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

            if not agent.tools:
                errors.append(f"{prefix}: at least one tool is required")
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

        # Validate root-level triggers
        for i, trigger in enumerate(self.triggers):
            if trigger.type == "webhook" and not trigger.endpoint:
                errors.append(f"triggers[{i}]: webhook trigger requires an 'endpoint'")
            if trigger.type == "schedule" and not trigger.cron:
                errors.append(f"triggers[{i}]: schedule trigger requires a 'cron' expression")
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
