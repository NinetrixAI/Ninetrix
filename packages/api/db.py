"""Database connection pool (asyncpg)."""
from __future__ import annotations

import json
import os

import asyncpg
from dotenv import load_dotenv

load_dotenv()

_pool: asyncpg.Pool | None = None
CATALOG_SEED: list[dict] = [
    {
        "id": "github",
        "name": "GitHub",
        "description": "Access repositories, issues, pull requests, and webhooks.",
        "auth_type": "oauth2",
        "icon": "🐙",
        "oauth_authorize_url": "https://github.com/login/oauth/authorize",
        "oauth_token_url": "https://github.com/login/oauth/access_token",
        "oauth_scopes": ["repo", "user"],
        "tools": [
            {"name": "read_repo", "description": "Read repositories and their content", "permissions": ["read"]},
            {"name": "write_repo", "description": "Create commits, branches, and pull requests", "permissions": ["write"]},
            {"name": "manage_webhooks", "description": "Configure repository webhooks", "permissions": ["write"]},
        ],
    },
    {
        "id": "slack",
        "name": "Slack",
        "description": "Send messages, manage channels, upload files, search, and interact with your Slack workspace. 39 actions across messaging, channels, users, files, reactions, pins, DMs, user groups, bookmarks, and reminders.",
        "auth_type": "oauth2",
        "icon": "💬",
        "oauth_authorize_url": "https://slack.com/oauth/v2/authorize",
        "oauth_token_url": "https://slack.com/api/oauth.v2.access",
        "oauth_scopes": [
            "channels:history", "channels:read", "channels:manage", "channels:join",
            "chat:write", "chat:write.customize",
            "files:read", "files:write",
            "groups:history", "groups:read", "groups:write",
            "im:read", "im:write", "im:history",
            "mpim:history", "mpim:read", "mpim:write",
            "reactions:read", "reactions:write",
            "users:read", "users:read.email",
            "usergroups:read", "usergroups:write",
            "pins:read", "pins:write",
            "bookmarks:read", "bookmarks:write",
            "reminders:read", "reminders:write",
        ],
        "tools": [
            {"name": "post_message",    "description": "Send a message to a channel (supports text, Block Kit, thread replies)", "permissions": ["write"]},
            {"name": "update_message",  "description": "Edit an existing message by channel + timestamp", "permissions": ["write"]},
            {"name": "delete_message",  "description": "Delete a message from a channel", "permissions": ["write"]},
            {"name": "schedule_message","description": "Schedule a message for future delivery", "permissions": ["write"]},
            {"name": "list_channels",   "description": "List all channels in the workspace with pagination", "permissions": ["read"]},
            {"name": "get_channel",     "description": "Get details for a single channel by ID", "permissions": ["read"]},
            {"name": "create_channel",  "description": "Create a new public or private channel", "permissions": ["write"]},
            {"name": "archive_channel", "description": "Archive a channel", "permissions": ["write"]},
            {"name": "rename_channel",  "description": "Rename a channel", "permissions": ["write"]},
            {"name": "set_topic",       "description": "Set a channel's topic", "permissions": ["write"]},
            {"name": "set_purpose",     "description": "Set a channel's purpose/description", "permissions": ["write"]},
            {"name": "invite_users",    "description": "Invite one or more users to a channel", "permissions": ["write"]},
            {"name": "kick_user",       "description": "Remove a user from a channel", "permissions": ["write"]},
            {"name": "join_channel",    "description": "Make the bot join a channel", "permissions": ["write"]},
            {"name": "leave_channel",   "description": "Make the bot leave a channel", "permissions": ["write"]},
            {"name": "get_history",     "description": "Fetch message history for a channel", "permissions": ["read"]},
            {"name": "get_thread_replies","description": "Fetch all replies in a message thread", "permissions": ["read"]},
            {"name": "search_messages", "description": "Full-text search across the workspace (requires user token)", "permissions": ["read"]},
            {"name": "get_user",        "description": "Get a user's full profile by user ID", "permissions": ["read"]},
            {"name": "list_users",      "description": "Paginated list of all workspace users", "permissions": ["read"]},
            {"name": "lookup_by_email", "description": "Find a user by email address", "permissions": ["read"]},
            {"name": "get_user_presence","description": "Get a user's current presence status", "permissions": ["read"]},
            {"name": "upload_file",     "description": "Upload a file to one or more channels", "permissions": ["write"]},
            {"name": "list_files",      "description": "List files in the workspace with filters", "permissions": ["read"]},
            {"name": "get_file_info",   "description": "Get metadata and download URL for a file by ID", "permissions": ["read"]},
            {"name": "delete_file",     "description": "Permanently delete a file", "permissions": ["write"]},
            {"name": "share_file",      "description": "Share an existing file to additional channels", "permissions": ["write"]},
            {"name": "add_reaction",    "description": "Add an emoji reaction to a message", "permissions": ["write"]},
            {"name": "remove_reaction", "description": "Remove an emoji reaction from a message", "permissions": ["write"]},
            {"name": "list_reactions",  "description": "List all reactions on a message", "permissions": ["read"]},
            {"name": "add_pin",         "description": "Pin a message to a channel", "permissions": ["write"]},
            {"name": "remove_pin",      "description": "Unpin a message from a channel", "permissions": ["write"]},
            {"name": "list_pins",       "description": "List all pinned items in a channel", "permissions": ["read"]},
            {"name": "open_dm",         "description": "Open a DM channel with one or more users", "permissions": ["write"]},
            {"name": "open_group_dm",   "description": "Open a group DM with multiple users", "permissions": ["write"]},
            {"name": "post_dm",         "description": "Open a DM and immediately post a message (convenience)", "permissions": ["write"]},
            {"name": "list_usergroups", "description": "List all user groups in the workspace", "permissions": ["read"]},
            {"name": "create_usergroup","description": "Create a new user group", "permissions": ["write"]},
            {"name": "update_usergroup","description": "Update a user group's name, handle, or channels", "permissions": ["write"]},
            {"name": "set_usergroup_users","description": "Set the full membership list of a user group", "permissions": ["write"]},
            {"name": "list_bookmarks",  "description": "List bookmarks in a channel", "permissions": ["read"]},
            {"name": "add_bookmark",    "description": "Add a bookmark to a channel", "permissions": ["write"]},
            {"name": "edit_bookmark",   "description": "Edit an existing bookmark", "permissions": ["write"]},
            {"name": "remove_bookmark", "description": "Remove a bookmark from a channel", "permissions": ["write"]},
            {"name": "add_reminder",    "description": "Create a reminder (requires SLACK_USER_TOKEN)", "permissions": ["write"]},
            {"name": "list_reminders",  "description": "List reminders for the authed user (requires SLACK_USER_TOKEN)", "permissions": ["read"]},
            {"name": "delete_reminder", "description": "Delete a reminder by ID (requires SLACK_USER_TOKEN)", "permissions": ["write"]},
        ],
    },
    {
        "id": "notion",
        "name": "Notion",
        "description": "Read and write pages, databases, and search content.",
        "auth_type": "oauth2",
        "icon": "📝",
        "oauth_authorize_url": "https://api.notion.com/v1/oauth/authorize",
        "oauth_token_url": "https://api.notion.com/v1/oauth/token",
        "oauth_scopes": [],
        "tools": [
            {"name": "read_pages", "description": "Read pages and databases", "permissions": ["read"]},
            {"name": "write_pages", "description": "Create and update pages", "permissions": ["write"]},
            {"name": "search", "description": "Search across your workspace", "permissions": ["read"]},
        ],
    },
    {
        "id": "linear",
        "name": "Linear",
        "description": "Manage issues, projects, and team workflows.",
        "auth_type": "oauth2",
        "icon": "📐",
        "oauth_authorize_url": "https://linear.app/oauth/authorize",
        "oauth_token_url": "https://api.linear.app/oauth/token",
        "oauth_scopes": ["read", "issues:create", "issues:update"],
        "tools": [
            {"name": "read_issues", "description": "Read issues and their details", "permissions": ["read"]},
            {"name": "write_issues", "description": "Create and update issues", "permissions": ["write"]},
            {"name": "manage_projects", "description": "Create and update projects", "permissions": ["write"]},
        ],
    },
    {
        "id": "stripe",
        "name": "Stripe",
        "description": "Access payment data, customers, and process refunds.",
        "auth_type": "apikey",
        "icon": "💳",
        "oauth_authorize_url": None,
        "oauth_token_url": None,
        "oauth_scopes": [],
        "tools": [
            {"name": "read_payments", "description": "Read payment intents and charges", "permissions": ["read"]},
            {"name": "read_customers", "description": "Access customer profiles", "permissions": ["read"]},
            {"name": "create_refunds", "description": "Issue refunds on charges", "permissions": ["write"]},
        ],
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "description": "Pass-through API key for OpenAI inference.",
        "auth_type": "apikey",
        "icon": "🤖",
        "oauth_authorize_url": None,
        "oauth_token_url": None,
        "oauth_scopes": [],
        "tools": [
            {"name": "inference", "description": "Call OpenAI models via agents", "permissions": ["write"]},
        ],
    },
    {
        "id": "sendgrid",
        "name": "SendGrid",
        "description": "Send transactional and marketing emails.",
        "auth_type": "apikey",
        "icon": "📧",
        "oauth_authorize_url": None,
        "oauth_token_url": None,
        "oauth_scopes": [],
        "tools": [
            {"name": "send_email", "description": "Send emails to recipients", "permissions": ["write"]},
        ],
    },
]


async def connect() -> None:
    global _pool
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy .env.example to .env and fill in your PostgreSQL URL."
        )
    _pool = await asyncpg.create_pool(url, min_size=1, max_size=10)


async def close() -> None:
    if _pool:
        await _pool.close()


def pool() -> asyncpg.Pool:
    assert _pool is not None, "DB pool not initialised — call connect() first"
    return _pool


async def create_runner_events_table() -> None:
    """Create/migrate runner_events and agentfile_checkpoints tables. Idempotent."""
    await pool().execute("""
        CREATE TABLE IF NOT EXISTS runner_events (
            id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            event_type TEXT NOT NULL,
            thread_id  TEXT NOT NULL,
            trace_id   TEXT,
            agent_id   TEXT,
            payload    JSONB NOT NULL DEFAULT '{}',
            received_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    # Create agentfile_checkpoints if it doesn't exist yet
    await pool().execute("""
        CREATE TABLE IF NOT EXISTS agentfile_checkpoints (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            trace_id    TEXT NOT NULL,
            thread_id   TEXT NOT NULL,
            agent_id    TEXT,
            step_index  INTEGER NOT NULL DEFAULT 0,
            timestamp   TIMESTAMPTZ DEFAULT NOW(),
            updated_at  TIMESTAMPTZ DEFAULT NOW(),
            status      TEXT NOT NULL DEFAULT 'in_progress',
            checkpoint  JSONB NOT NULL DEFAULT '{}',
            metadata    JSONB NOT NULL DEFAULT '{}',
            parent_trace_id TEXT
        )
    """)
    await pool().execute("""
        CREATE INDEX IF NOT EXISTS checkpoints_thread_id_idx
        ON agentfile_checkpoints (thread_id)
    """)
    # Ensure parent_trace_id column exists (migration for older schemas)
    await pool().execute("""
        ALTER TABLE agentfile_checkpoints
            ADD COLUMN IF NOT EXISTS parent_trace_id TEXT
    """)
    # Migrate: add columns that may be missing if the table was created by an older schema
    await pool().execute("""
        ALTER TABLE runner_events
            ADD COLUMN IF NOT EXISTS thread_id TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS trace_id TEXT,
            ADD COLUMN IF NOT EXISTS agent_id TEXT,
            ADD COLUMN IF NOT EXISTS payload JSONB NOT NULL DEFAULT '{}'
    """)
    await pool().execute("""
        CREATE INDEX IF NOT EXISTS runner_events_thread_id_idx
        ON runner_events (thread_id)
    """)


async def create_integration_tables() -> None:
    """Create integration tables and seed catalog data. Idempotent."""
    p = pool()
    async with p.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS integration_catalog (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                auth_type TEXT NOT NULL,
                icon TEXT,
                oauth_authorize_url TEXT,
                oauth_token_url TEXT,
                oauth_scopes TEXT[],
                tools JSONB NOT NULL DEFAULT '[]'
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_integrations (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                integration_id TEXT NOT NULL REFERENCES integration_catalog(id),
                org_id TEXT NOT NULL DEFAULT 'default',
                status TEXT NOT NULL DEFAULT 'pending',
                account_label TEXT,
                oauth_state TEXT,
                connected_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (integration_id, org_id)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS integration_credentials (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_integration_id UUID NOT NULL REFERENCES user_integrations(id) ON DELETE CASCADE,
                key_name TEXT NOT NULL,
                encrypted_value TEXT NOT NULL,
                expires_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (user_integration_id, key_name)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS org_tokens (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                org_id TEXT NOT NULL DEFAULT 'default',
                token_hash TEXT NOT NULL UNIQUE,
                label TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                last_used_at TIMESTAMPTZ
            )
        """)
        # Seed / refresh catalog entries
        for item in CATALOG_SEED:
            await conn.execute(
                """
                INSERT INTO integration_catalog
                    (id, name, description, auth_type, icon,
                     oauth_authorize_url, oauth_token_url, oauth_scopes, tools)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    auth_type = EXCLUDED.auth_type,
                    icon = EXCLUDED.icon,
                    oauth_authorize_url = EXCLUDED.oauth_authorize_url,
                    oauth_token_url = EXCLUDED.oauth_token_url,
                    oauth_scopes = EXCLUDED.oauth_scopes,
                    tools = EXCLUDED.tools
                """,
                item["id"], item["name"], item["description"], item["auth_type"], item["icon"],
                item["oauth_authorize_url"], item["oauth_token_url"],
                item["oauth_scopes"],
                json.dumps(item["tools"]),
            )
