from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

import yaml


@dataclass
class ServerConfig:
    name: str
    type: str  # npx | uvx | python | docker
    package: str = ""
    command: str = ""  # full command override (takes precedence over type+package)
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class WorkerConfig:
    gateway_url: str
    worker_id: str
    worker_name: str
    org_id: str
    token: str
    servers: list[ServerConfig]


def load_config(path: Optional[str] = None) -> WorkerConfig:
    data: dict = {}
    if path and os.path.exists(path):
        with open(path) as f:
            data = yaml.safe_load(f) or {}

    gateway_url = os.getenv("MCP_GATEWAY_URL", data.get("gateway_url", "ws://localhost:8080"))
    org_id = os.getenv("MCP_ORG_ID", data.get("org_id", data.get("workspace_id", "default")))
    worker_name = os.getenv("MCP_WORKER_NAME", data.get("worker_name", "worker-1"))
    worker_id = os.getenv("MCP_WORKER_ID", data.get("worker_id", worker_name))
    token = os.getenv("MCP_GATEWAY_TOKEN", data.get("token", "dev-secret"))

    servers: list[ServerConfig] = []
    for s in data.get("servers", []):
        # Resolve env vars in the server's env block
        resolved_env = {k: os.path.expandvars(str(v)) for k, v in s.get("env", {}).items()}
        servers.append(
            ServerConfig(
                name=s["name"],
                type=s.get("type", "npx"),
                package=s.get("package", ""),
                command=s.get("command", ""),
                args=s.get("args", []),
                env=resolved_env,
            )
        )

    return WorkerConfig(
        gateway_url=gateway_url,
        worker_id=worker_id,
        worker_name=worker_name,
        org_id=org_id,
        token=token,
        servers=servers,
    )


def server_to_command(server: ServerConfig) -> tuple[str, list[str]]:
    """Convert a ServerConfig to (executable, args) for subprocess launch."""
    if server.command:
        parts = server.command.split()
        return parts[0], parts[1:] + server.args

    if server.type == "npx":
        return "npx", ["-y", server.package] + server.args
    elif server.type == "uvx":
        return "uvx", [server.package] + server.args
    elif server.type == "python":
        return "python", ["-m", server.package] + server.args
    elif server.type == "docker":
        return "docker", ["run", "--rm", "-i", server.package] + server.args
    else:
        raise ValueError(f"Unknown server type: {server.type!r}")
