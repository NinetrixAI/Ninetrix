"""Client for the Ninetrix Cloud API (saas-api).

Used by `ninetrix deploy` to create/update agents and deployments on the cloud.
All methods are synchronous (httpx) — the CLI is not async.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
from rich.console import Console

from agentfile.core.auth import read_token
from agentfile.core.config import resolve_saas_url, _CLOUD_DEFAULT

console = Console()

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


# ── Auth resolution ───────────────────────────────────────────────────────────

def resolve_cloud_auth(token_override: str | None = None) -> tuple[str, str | None]:
    """Return (api_url, token) for Ninetrix Cloud.

    Token resolution: --token flag > AGENTFILE_API_TOKEN env > auth.json > secrets.
    """
    api_url = resolve_saas_url() or _CLOUD_DEFAULT
    token = token_override or read_token(api_url)
    return api_url, token


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class WhoAmI:
    """Identity returned by the API on token verification."""
    email: str | None = None
    org_slug: str | None = None
    org_id: str | None = None
    org_name: str | None = None


@dataclass
class DeployResult:
    """Result of deploying a single agent."""
    agent_name: str
    agent_id: str
    deployment_id: str | None
    action: str  # "created" | "updated"
    status: str = "pending"
    url: str | None = None
    dashboard_url: str | None = None
    region: str = ""
    cpus: float = 1
    memory_mb: int = 512
    error: str | None = None


# ── Cloud client ──────────────────────────────────────────────────────────────

class CloudClient:
    """Thin wrapper around the Ninetrix SaaS API."""

    def __init__(self, api_url: str, token: str):
        self.api_url = api_url.rstrip("/")
        self.token = token
        self._headers = {"Authorization": f"Bearer {token}"}

    def _url(self, path: str) -> str:
        return f"{self.api_url}/v1{path}"

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        resp = httpx.request(
            method, self._url(path),
            headers=self._headers, timeout=_TIMEOUT, **kwargs,
        )
        return resp

    # ── Identity ──────────────────────────────────────────────────────────

    def whoami(self) -> WhoAmI:
        """Verify the token and return identity info.

        Works with both JWT tokens (user context) and API tokens (org context).
        """
        # Try /users/me first (JWT)
        resp = self._request("GET", "/users/me")
        if resp.status_code == 200:
            data = resp.json()
            # Also fetch org info
            org_resp = self._request("GET", "/users/me/orgs")
            org = {}
            if org_resp.status_code == 200:
                orgs = org_resp.json()
                if orgs:
                    org = orgs[0]
            return WhoAmI(
                email=data.get("email"),
                org_slug=org.get("slug"),
                org_id=org.get("id"),
                org_name=org.get("name"),
            )

        # Fallback: API token — try /tokens to verify it's valid
        resp2 = self._request("GET", "/tokens")
        if resp2.status_code == 200:
            # API tokens are org-scoped — resolve org slug from agents list
            org_slug = None
            org_id = None
            try:
                agents = self.list_agents()
                if agents:
                    org_id = agents[0].get("org_id")
                if org_id:
                    org_resp = self._request("GET", f"/orgs/{org_id}")
                    if org_resp.status_code == 200:
                        org_data = org_resp.json()
                        org_slug = org_data.get("slug")
            except Exception:
                pass
            return WhoAmI(email="(API token)", org_slug=org_slug, org_id=org_id)

        if resp.status_code == 401 or resp2.status_code == 401:
            return WhoAmI()  # invalid token

        # Unexpected error
        return WhoAmI()

    # ── Agents ────────────────────────────────────────────────────────────

    def list_agents(self) -> list[dict]:
        resp = self._request("GET", "/agents")
        resp.raise_for_status()
        return resp.json()

    def find_agent_by_name(self, name: str) -> dict | None:
        """Find an existing agent by name (case-insensitive slug match)."""
        agents = self.list_agents()
        for a in agents:
            if a.get("name", "").lower() == name.lower() and a.get("status") == "active":
                return a
        return None

    def create_agent(self, payload: dict) -> dict:
        resp = self._request("POST", "/agents", json=payload)
        resp.raise_for_status()
        return resp.json()

    def update_agent(self, agent_id: str, payload: dict) -> dict:
        resp = self._request("PATCH", f"/agents/{agent_id}", json=payload)
        resp.raise_for_status()
        return resp.json()

    # ── Deployments ───────────────────────────────────────────────────────

    def get_deployment(self, deployment_id: str) -> dict:
        resp = self._request("GET", f"/deployments/{deployment_id}")
        resp.raise_for_status()
        return resp.json()

    def list_deployments(self) -> list[dict]:
        resp = self._request("GET", "/deployments")
        resp.raise_for_status()
        return resp.json()

    def find_deployment_for_agent(self, agent_id: str) -> dict | None:
        """Find the active deployment for a given agent."""
        deployments = self.list_deployments()
        for d in deployments:
            if d.get("agent_id") == agent_id and d.get("status") != "destroyed":
                return d
        return None

    def start_deployment(self, deployment_id: str) -> dict:
        resp = self._request("POST", f"/deployments/{deployment_id}/start")
        resp.raise_for_status()
        return resp.json()

    def update_deployment(self, deployment_id: str, payload: dict) -> dict:
        resp = self._request("PATCH", f"/deployments/{deployment_id}", json=payload)
        resp.raise_for_status()
        return resp.json()

    # ── Deploy (create-or-update) ─────────────────────────────────────────

    def deploy_agent(
        self,
        agent_name: str,
        yaml_content: str,
        description: str | None = None,
        region: str | None = None,
        cpus: int = 1,
        memory_mb: int = 512,
        env: dict[str, str] | None = None,
    ) -> DeployResult:
        """Idempotent deploy: create a new agent or update an existing one."""
        env = env or {}

        existing = self.find_agent_by_name(agent_name)

        if existing:
            # UPDATE — patch YAML + config, trigger hot-reload
            agent_id = existing["id"]
            self.update_agent(agent_id, {
                "yaml_content": yaml_content,
                "description": description,
            })

            # Find and update the deployment config if it exists
            dep = self.find_deployment_for_agent(agent_id)
            deployment_id = None
            if dep:
                deployment_id = dep["id"]
                update_payload: dict = {}
                if cpus != dep.get("cpus"):
                    update_payload["cpus"] = cpus
                if memory_mb != dep.get("memory_mb"):
                    update_payload["memory_mb"] = memory_mb
                if region and region != dep.get("region"):
                    update_payload["region"] = region
                if env:
                    update_payload["env"] = env
                if update_payload:
                    self.update_deployment(deployment_id, update_payload)

                # Restart if stopped
                if dep.get("status") == "stopped":
                    self.start_deployment(deployment_id)

            return DeployResult(
                agent_name=agent_name,
                agent_id=agent_id,
                deployment_id=deployment_id,
                action="updated",
                status=dep.get("status", "unknown") if dep else "no_deployment",
                region=dep.get("region", "") if dep else "",
                cpus=dep.get("cpus", cpus) if dep else cpus,
                memory_mb=dep.get("memory_mb", memory_mb) if dep else memory_mb,
            )
        else:
            # CREATE — new agent + auto-provision Fly machine
            resp = self.create_agent({
                "name": agent_name,
                "description": description or "",
                "yaml_content": yaml_content,
                "region": region,
                "cpus": cpus,
                "memory_mb": memory_mb,
                "env": env,
            })

            return DeployResult(
                agent_name=agent_name,
                agent_id=resp["id"],
                deployment_id=resp.get("deployment_id"),
                action="created",
                status="pending",
                region=region or "",
                cpus=cpus,
                memory_mb=memory_mb,
            )

    # ── Polling ───────────────────────────────────────────────────────────

    def wait_for_deployment(
        self, deployment_id: str, timeout: int = 120, interval: int = 3,
    ) -> str:
        """Poll until the deployment reaches 'running' (or error). Returns final status."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            dep = self.get_deployment(deployment_id)
            status = dep.get("status", "pending")
            if status in ("running", "error", "destroyed"):
                return status
            time.sleep(interval)
        return "timeout"
