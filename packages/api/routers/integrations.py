"""Integration Hub router."""
from __future__ import annotations

import base64
import json
import os
import secrets
import urllib.parse

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse

import db
from auth import verify_token
from crypto import decrypt, encrypt
from models import ApiKeyPayload, IntegrationCatalogItem, IntegrationTool

router = APIRouter(dependencies=[Depends(verify_token)])

# Maps (integration_id, key_name) → env var injected into agent containers.
# Hardcoded fallback — the Tool Hub registry is the primary source of truth.
_CRED_ENV_MAP: dict[str, dict[str, str]] = {
    "github":   {"access_token": "GITHUB_TOKEN"},
    "slack":    {"access_token": "SLACK_BOT_TOKEN", "team_id": "SLACK_TEAM_ID"},
    "notion":   {"access_token": "NOTION_TOKEN"},
    "linear":   {"access_token": "LINEAR_API_KEY"},
    "stripe":   {"api_key": "STRIPE_SECRET_KEY"},
    "openai":   {"api_key": "OPENAI_API_KEY"},
    "sendgrid": {"api_key": "SENDGRID_API_KEY"},
}

_OAUTH_CLIENT_ID_VARS = {
    "github": "GITHUB_CLIENT_ID",
    "slack":  "SLACK_CLIENT_ID",
    "notion": "NOTION_CLIENT_ID",
    "linear": "LINEAR_CLIENT_ID",
}

_OAUTH_CLIENT_SECRET_VARS = {
    "github": "GITHUB_CLIENT_SECRET",
    "slack":  "SLACK_CLIENT_SECRET",
    "notion": "NOTION_CLIENT_SECRET",
    "linear": "LINEAR_CLIENT_SECRET",
}


def _api_url() -> str:
    return os.environ.get("AGENTFILE_API_URL", "http://localhost:8000")


def _app_url() -> str:
    return os.environ.get("AGENTFILE_APP_URL", "http://localhost:3000")


def _row_to_catalog_item(row: dict, ui_row: dict | None) -> IntegrationCatalogItem:
    raw_tools = row.get("tools") or []
    if isinstance(raw_tools, str):
        raw_tools = json.loads(raw_tools)
    tools = [
        IntegrationTool(
            name=t["name"],
            description=t["description"],
            permissions=t["permissions"],
        )
        for t in raw_tools
    ]
    connected = ui_row is not None and ui_row.get("status") == "connected"
    status = ui_row["status"] if ui_row else "disconnected"
    account_label = ui_row.get("account_label") if ui_row else None
    return IntegrationCatalogItem(
        id=row["id"],
        name=row["name"],
        description=row.get("description") or "",
        auth_type=row["auth_type"],
        icon=row.get("icon") or "",
        tools=tools,
        connected=connected,
        status=status,
        account_label=account_label,
    )


async def _exchange_code(
    integration_id: str,
    token_url: str,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
) -> tuple[str, dict]:
    """Exchange authorization code for access token. Returns (access_token, extra_info)."""
    async with httpx.AsyncClient(timeout=10) as client:
        if integration_id == "notion":
            creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
            r = await client.post(
                token_url,
                json={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
                headers={"Authorization": f"Basic {creds}", "Content-Type": "application/json"},
            )
            r.raise_for_status()
            data = r.json()
            label = f"notion: {data.get('workspace_name', 'unknown')}"
            return data["access_token"], {"account_label": label}

        elif integration_id == "slack":
            r = await client.post(
                token_url,
                data={
                    "client_id": client_id, "client_secret": client_secret,
                    "code": code, "redirect_uri": redirect_uri,
                },
            )
            r.raise_for_status()
            data = r.json()
            if not data.get("ok"):
                raise ValueError(data.get("error", "Slack OAuth failed"))
            team_name = data.get("team", {}).get("name", "unknown")
            return data["access_token"], {"account_label": f"slack: {team_name}"}

        elif integration_id == "github":
            r = await client.post(
                token_url,
                data={
                    "client_id": client_id, "client_secret": client_secret,
                    "code": code, "redirect_uri": redirect_uri,
                },
                headers={"Accept": "application/json"},
            )
            r.raise_for_status()
            data = r.json()
            return data["access_token"], {}

        else:
            # linear and any future providers
            r = await client.post(
                token_url,
                data={
                    "client_id": client_id, "client_secret": client_secret,
                    "code": code, "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            r.raise_for_status()
            data = r.json()
            return data["access_token"], {}


async def _get_account_label(integration_id: str, access_token: str) -> str:
    """Fetch a human-readable account label from the provider's API."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            if integration_id == "github":
                r = await client.get(
                    "https://api.github.com/user",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if r.status_code == 200:
                    return f"github: {r.json().get('login', 'unknown')}"
            elif integration_id == "linear":
                r = await client.post(
                    "https://api.linear.app/graphql",
                    json={"query": "{ organization { name } }"},
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if r.status_code == 200:
                    org = r.json().get("data", {}).get("organization", {}).get("name", "unknown")
                    return f"linear: {org}"
    except Exception:
        pass
    return f"{integration_id}: connected"


# NOTE: /credentials must be defined BEFORE /{id} so the static path wins.

@router.get("/credentials")
async def get_credentials() -> dict:
    """Return all decrypted credentials as env-var maps keyed by integration_id. Used by CLI."""
    p = db.pool()
    async with p.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT ui.integration_id, ic.key_name, ic.encrypted_value
            FROM user_integrations ui
            JOIN integration_credentials ic ON ic.user_integration_id = ui.id
            WHERE ui.org_id = 'default' AND ui.status = 'connected'
            """
        )
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        int_id = row["integration_id"]
        key_name = row["key_name"]
        try:
            value = decrypt(row["encrypted_value"])
        except Exception:
            continue
        env_var = _CRED_ENV_MAP.get(int_id, {}).get(key_name)
        if env_var:
            result.setdefault(int_id, {})[env_var] = value
    return result


@router.get("")
async def list_integrations() -> list[IntegrationCatalogItem]:
    """List all integrations from catalog with connection status."""
    p = db.pool()
    async with p.acquire() as conn:
        catalog_rows = await conn.fetch("SELECT * FROM integration_catalog ORDER BY id")
        ui_rows = await conn.fetch(
            "SELECT * FROM user_integrations WHERE org_id = 'default'"
        )
    ui_map = {row["integration_id"]: dict(row) for row in ui_rows}
    return [
        _row_to_catalog_item(dict(row), ui_map.get(row["id"]))
        for row in catalog_rows
    ]


@router.get("/{id}")
async def get_integration(id: str) -> IntegrationCatalogItem:
    """Get single integration detail with tools."""
    p = db.pool()
    async with p.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM integration_catalog WHERE id = $1", id)
        if not row:
            raise HTTPException(status_code=404, detail=f"Integration '{id}' not found")
        ui_row = await conn.fetchrow(
            "SELECT * FROM user_integrations WHERE integration_id = $1 AND org_id = 'default'",
            id,
        )
    return _row_to_catalog_item(dict(row), dict(ui_row) if ui_row else None)


@router.get("/{id}/authorize")
async def authorize(id: str):
    """Start OAuth2 flow — redirect to provider authorization page."""
    p = db.pool()
    async with p.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM integration_catalog WHERE id = $1", id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Integration '{id}' not found")
    if row["auth_type"] != "oauth2":
        raise HTTPException(status_code=400, detail=f"'{id}' is not an OAuth2 integration")

    client_id_var = _OAUTH_CLIENT_ID_VARS.get(id, "")
    client_id = os.environ.get(client_id_var, "")
    if not client_id:
        raise HTTPException(status_code=500, detail=f"{client_id_var} env var is not set")

    state = secrets.token_urlsafe(32)
    redirect_uri = f"{_api_url()}/integrations/{id}/callback"

    async with p.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO user_integrations (integration_id, org_id, status, oauth_state)
            VALUES ($1, 'default', 'pending', $2)
            ON CONFLICT (integration_id, org_id) DO UPDATE
            SET status = 'pending', oauth_state = $2
            """,
            id, state,
        )

    scopes = row["oauth_scopes"] or []
    params: dict[str, str] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    if scopes:
        params["scope"] = " ".join(scopes)
    if id == "notion":
        params["response_type"] = "code"
        params["owner"] = "user"

    authorize_url = row["oauth_authorize_url"] + "?" + urllib.parse.urlencode(params)
    return RedirectResponse(url=authorize_url, status_code=302)


@router.get("/{id}/callback")
async def callback(id: str, code: str = "", state: str = "", error: str = ""):
    """OAuth2 callback — exchange code for token and save credential."""
    app_url = _app_url()

    if error:
        return RedirectResponse(
            url=f"{app_url}/dashboard/integrations?error={urllib.parse.quote(error)}",
            status_code=302,
        )

    p = db.pool()
    async with p.acquire() as conn:
        ui_row = await conn.fetchrow(
            "SELECT * FROM user_integrations WHERE integration_id = $1 AND org_id = 'default'",
            id,
        )

    if not ui_row or ui_row["oauth_state"] != state:
        raise HTTPException(status_code=400, detail="Invalid OAuth2 state parameter")

    async with p.acquire() as conn:
        cat_row = await conn.fetchrow("SELECT * FROM integration_catalog WHERE id = $1", id)

    client_id = os.environ.get(_OAUTH_CLIENT_ID_VARS.get(id, ""), "")
    client_secret = os.environ.get(_OAUTH_CLIENT_SECRET_VARS.get(id, ""), "")
    redirect_uri = f"{_api_url()}/integrations/{id}/callback"
    token_url = cat_row["oauth_token_url"]

    try:
        access_token, extra = await _exchange_code(
            id, token_url, client_id, client_secret, code, redirect_uri
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Token exchange failed: {exc}")

    account_label = extra.get("account_label") or await _get_account_label(id, access_token)
    encrypted = encrypt(access_token)

    async with p.acquire() as conn:
        await conn.execute(
            """
            UPDATE user_integrations
            SET status = 'connected', account_label = $1,
                connected_at = NOW(), oauth_state = NULL
            WHERE integration_id = $2 AND org_id = 'default'
            """,
            account_label, id,
        )
        ui_id = await conn.fetchval(
            "SELECT id FROM user_integrations WHERE integration_id = $1 AND org_id = 'default'",
            id,
        )
        await conn.execute(
            """
            INSERT INTO integration_credentials (user_integration_id, key_name, encrypted_value)
            VALUES ($1, 'access_token', $2)
            ON CONFLICT (user_integration_id, key_name) DO UPDATE
            SET encrypted_value = EXCLUDED.encrypted_value, created_at = NOW()
            """,
            ui_id, encrypted,
        )

    return RedirectResponse(
        url=f"{app_url}/dashboard/integrations?connected={id}",
        status_code=302,
    )


@router.post("/{id}/apikey")
async def save_api_key(id: str, payload: ApiKeyPayload) -> dict:
    """Save an API key for an API-key-based integration."""
    p = db.pool()
    async with p.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM integration_catalog WHERE id = $1", id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Integration '{id}' not found")
    if row["auth_type"] != "apikey":
        raise HTTPException(status_code=400, detail=f"'{id}' uses OAuth2, not an API key")

    encrypted = encrypt(payload.key)
    async with p.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO user_integrations
                (integration_id, org_id, status, account_label, connected_at)
            VALUES ($1, 'default', 'connected', $2, NOW())
            ON CONFLICT (integration_id, org_id) DO UPDATE
            SET status = 'connected', account_label = $2, connected_at = NOW()
            """,
            id, f"{id}: connected",
        )
        ui_id = await conn.fetchval(
            "SELECT id FROM user_integrations WHERE integration_id = $1 AND org_id = 'default'",
            id,
        )
        await conn.execute(
            """
            INSERT INTO integration_credentials (user_integration_id, key_name, encrypted_value)
            VALUES ($1, 'api_key', $2)
            ON CONFLICT (user_integration_id, key_name) DO UPDATE
            SET encrypted_value = EXCLUDED.encrypted_value, created_at = NOW()
            """,
            ui_id, encrypted,
        )

    return {"status": "connected"}


@router.delete("/{id}")
async def disconnect(id: str) -> dict:
    """Disconnect an integration (cascades to credentials)."""
    p = db.pool()
    async with p.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM user_integrations WHERE integration_id = $1 AND org_id = 'default'",
            id,
        )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail=f"'{id}' is not connected")
    return {"status": "disconnected"}
