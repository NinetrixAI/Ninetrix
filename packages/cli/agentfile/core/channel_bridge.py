"""Channel bridge — polls Telegram and forwards messages to agent container.

Runs in a background thread alongside `run_container()`. Bridges the gap
between Telegram's API and the agent's webhook endpoint on localhost.

Usage:
    bridge = ChannelBridge(agent_port=9100)
    bridge.start()        # non-blocking, spawns background thread
    run_container(...)    # blocking
    bridge.stop()         # cleanup
"""
from __future__ import annotations

import logging
import threading
import time

import httpx
from rich.console import Console

from agentfile.core.channel_config import get_channel

logger = logging.getLogger(__name__)
_console = Console(stderr=True)  # print to stderr so it doesn't mix with container stdout

_TG_API = "https://api.telegram.org/bot{token}"


class ChannelBridge:
    """Polls Telegram getUpdates and POSTs messages to localhost:{port}/run."""

    def __init__(self, agent_port: int = 9100, agent_name: str = "", endpoint: str = "/run") -> None:
        self._port = agent_port
        self._agent_name = agent_name
        self._endpoint = endpoint
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        """Start the polling bridge in a background thread.

        Returns True if a Telegram channel is configured and the bridge started.
        """
        tg = get_channel("telegram")
        if not tg or not tg.get("verified") or not tg.get("bot_token"):
            return False

        self._running = True
        self._thread = threading.Thread(
            target=self._poll_loop,
            args=(tg["bot_token"], tg.get("chat_id", "")),
            daemon=True,
            name="telegram-bridge",
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _poll_loop(self, bot_token: str, chat_id: str) -> None:
        """Long-poll Telegram and forward messages to agent container."""
        offset = 0
        bot_username = get_channel("telegram").get("bot_username", "?")

        # Wait for the container to start (webhook server needs a moment)
        self._wait_for_container()

        _console.print(f"[dim][telegram] Bridge polling started — @{bot_username} → localhost:{self._port}{self._endpoint}[/dim]", highlight=False)

        while self._running:
            try:
                params: dict = {
                    "timeout": 30,
                    "allowed_updates": ["message"],
                }
                if offset:
                    params["offset"] = offset

                resp = httpx.get(
                    f"{_TG_API.format(token=bot_token)}/getUpdates",
                    params=params,
                    timeout=40,
                )

                if resp.status_code == 409:
                    # Webhook still set — delete it
                    httpx.post(
                        f"{_TG_API.format(token=bot_token)}/deleteWebhook",
                        json={"drop_pending_updates": False},
                        timeout=10,
                    )
                    time.sleep(2)
                    continue

                if resp.status_code != 200:
                    time.sleep(5)
                    continue

                updates = resp.json().get("result", [])
                for update in updates:
                    update_id = update.get("update_id", 0)
                    offset = update_id + 1

                    message = update.get("message")
                    if not message:
                        continue

                    text = (message.get("text") or "").strip()
                    if not text or text.startswith("/start"):
                        continue

                    from_user = message.get("from", {})
                    username = from_user.get("username") or from_user.get("first_name", "")

                    self._dispatch(text, bot_token, str(message["chat"]["id"]), username)

            except httpx.ReadTimeout:
                continue
            except Exception:
                logger.debug("Telegram bridge error", exc_info=True)
                time.sleep(5)

    def _wait_for_container(self) -> None:
        """Wait up to 30s for the agent container's webhook server to be reachable."""
        for _ in range(30):
            if not self._running:
                return
            try:
                resp = httpx.get(f"http://localhost:{self._port}/health", timeout=2)
                if resp.status_code in (200, 404, 405):
                    return
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadError, httpx.RemoteProtocolError):
                pass
            except Exception:
                pass
            time.sleep(1)

    def _dispatch(self, text: str, bot_token: str, chat_id: str, username: str) -> None:
        """Forward a message to the agent via /chat (synchronous) and send response to Telegram."""
        base = f"http://localhost:{self._port}"
        _console.print(f"[dim][telegram] @{username}: {text[:80]}[/dim]", highlight=False)

        # Use /chat endpoint — it's synchronous and returns the full response.
        # session_id = chat_id so per_chat conversations are maintained.
        try:
            resp = httpx.post(
                f"{base}/chat",
                json={"message": text, "session_id": chat_id},
                timeout=300,  # agent might take a while
            )
        except httpx.ConnectError:
            _send_tg(bot_token, chat_id, "⚠️ Agent is not running.")
            return
        except httpx.ReadTimeout:
            _send_tg(bot_token, chat_id, "⏱ Agent timed out. Try a shorter request.")
            return
        except Exception:
            logger.debug("Dispatch error", exc_info=True)
            _send_tg(bot_token, chat_id, "⚠️ Something went wrong.")
            return

        if resp.status_code == 503:
            _send_tg(bot_token, chat_id, "⏳ Agent is still starting up. Try again in a moment.")
            return

        if resp.status_code != 200:
            # Try to extract error details from response
            err_detail = ""
            try:
                err_data = resp.json()
                err_detail = err_data.get("error", "") or err_data.get("detail", "")
            except Exception:
                err_detail = resp.text[:200] if resp.text else ""
            _console.print(f"[red][telegram] Agent error {resp.status_code}: {err_detail[:200]}[/red]", highlight=False)
            # Send a user-friendly version to Telegram
            if "budget" in err_detail.lower():
                _send_tg(bot_token, chat_id, "💰 Agent budget exceeded. Increase max_budget_per_run in agentfile.yaml.")
            else:
                _send_tg(bot_token, chat_id, f"⚠️ Agent error: {err_detail[:500]}" if err_detail else f"⚠️ Agent error ({resp.status_code})")
            return

        # Extract the response text
        try:
            data = resp.json()
            answer = data.get("response", "")
        except Exception:
            answer = resp.text

        if not answer or answer == "(no response)":
            _send_tg(bot_token, chat_id, "🤷 Agent returned no response.")
            return

        # Telegram has a 4096 char limit per message — split if needed
        for chunk in _split_message(answer, max_len=4000):
            _send_tg(bot_token, chat_id, chunk)

        # Sync session to local API so dashboard shows it in Sessions view
        self._sync_session(chat_id, username)

    def _sync_session(self, chat_id: str, username: str) -> None:
        """Notify the local API about this channel session (fire-and-forget)."""
        import os
        api_url = os.environ.get("AGENTFILE_API_URL", "http://localhost:8000")
        try:
            httpx.post(
                f"{api_url}/v1/channels/sessions/sync",
                json={
                    "channel_type": "telegram",
                    "external_chat_id": chat_id,
                    "external_user_id": username,
                    "agent_name": self._agent_name,
                    "thread_id": f"chat-{chat_id}",
                },
                timeout=5,
            )
        except Exception:
            logger.debug("Session sync failed (non-critical)", exc_info=True)


def _split_message(text: str, max_len: int = 4000) -> list[str]:
    """Split a long message into chunks that fit Telegram's 4096 char limit."""
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        # Try to split at a newline
        cut = text.rfind("\n", 0, max_len)
        if cut < max_len // 2:
            cut = max_len  # no good newline, hard cut
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


def _send_tg(bot_token: str, chat_id: str, text: str) -> None:
    try:
        httpx.post(
            f"{_TG_API.format(token=bot_token)}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
    except Exception:
        pass
