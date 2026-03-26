"""WhatsApp channel adapter via Baileys Node.js sidecar.

Uses @whiskeysockets/baileys running as a subprocess, communicating
over a Unix Domain Socket with newline-delimited JSON.

The baileys-bridge.js process is spawned by connect() and manages
the WhatsApp Web connection, QR codes, and Signal protocol state.
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Literal

from ninetrix_channels.base import ChannelAdapter, InboundMessage, MessageCallback

logger = logging.getLogger(__name__)

# Default paths inside the container
_DEFAULT_SOCKET_PATH = "/var/run/whatsapp.sock"
_DEFAULT_AUTH_DIR = "/data/whatsapp"
_BRIDGE_SCRIPT = "/opt/channels/baileys-bridge/index.js"


class WhatsAppAdapter(ChannelAdapter):
    channel_type = "whatsapp"

    def __init__(self) -> None:
        self._running = False
        self._process: subprocess.Popen | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._socket_path = _DEFAULT_SOCKET_PATH
        self._config: dict = {}

    @property
    def connection_mode(self) -> Literal["webhook", "persistent"]:
        return "persistent"

    # ── Required methods ──────────────────────────────────────────────────

    async def validate_config(self, config: dict) -> tuple[bool, str]:
        # WhatsApp uses QR pairing, not a token — just check auth dir exists
        auth_dir = config.get("auth_dir", _DEFAULT_AUTH_DIR)
        creds = Path(auth_dir) / "creds.json"
        if creds.exists():
            return True, ""
        return False, f"No WhatsApp credentials found at {auth_dir}. Run: ninetrix channel connect whatsapp"

    async def send_message(self, config: dict, chat_id: str, text: str) -> bool:
        """Send a message via the Baileys bridge."""
        if self._writer is None:
            print("[whatsapp] send_message: bridge not connected (writer is None)", flush=True)
            return False
        try:
            cmd = json.dumps({"type": "send", "chat_id": chat_id, "text": text})
            self._writer.write((cmd + "\n").encode())
            await self._writer.drain()
            print(f"[whatsapp] send_message → {chat_id} ({len(text)} chars)", flush=True)
            return True
        except Exception as exc:
            print(f"[whatsapp] send_message failed: {exc}", flush=True)
            return False

    async def get_bot_info(self, config: dict) -> dict:
        phone = config.get("phone_number", "")
        return {"phone_number": phone, "platform": "whatsapp"}

    # ── Persistent mode ───────────────────────────────────────────────────

    async def connect(
        self, config: dict, on_message: MessageCallback,
    ) -> None:
        """Start the Baileys bridge subprocess and listen for messages.

        Spawns baileys-bridge.js as a child process, connects via UDS,
        and forwards incoming messages to the callback.
        """
        self._config = config
        self._running = True
        self._socket_path = config.get("socket_path", _DEFAULT_SOCKET_PATH)
        auth_dir = config.get("auth_dir", _DEFAULT_AUTH_DIR)
        bridge_script = config.get("bridge_script", _BRIDGE_SCRIPT)

        # Ensure auth dir exists
        Path(auth_dir).mkdir(parents=True, exist_ok=True)

        # Start the Node.js bridge process
        env = {
            "BAILEYS_SOCKET_PATH": self._socket_path,
            "BAILEYS_AUTH_DIR": auth_dir,
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "NODE_PATH": "/usr/local/lib/node_modules",
        }

        self._process = subprocess.Popen(
            ["node", bridge_script],
            env=env,
            stdout=sys.stderr,  # bridge logs go to stderr
            stderr=sys.stderr,
        )
        print(f"[whatsapp] Bridge process started (PID {self._process.pid})", flush=True)

        # Wait for the UDS socket to appear
        socket_path = Path(self._socket_path)
        for _ in range(30):  # 30 seconds max
            if socket_path.exists():
                break
            if self._process.poll() is not None:
                raise RuntimeError(
                    f"Baileys bridge exited with code {self._process.returncode}"
                )
            await asyncio.sleep(1)
        else:
            raise RuntimeError(
                f"Baileys bridge did not create socket at {self._socket_path}"
            )

        # Connect to the UDS
        self._reader, self._writer = await asyncio.open_unix_connection(
            self._socket_path
        )
        print("[whatsapp] Connected to bridge via UDS", flush=True)

        # Read messages from the bridge
        try:
            while self._running:
                line = await self._reader.readline()
                if not line:
                    # Bridge closed the connection
                    if self._running:
                        print("[whatsapp] Bridge connection lost — will reconnect", flush=True)
                    break

                line_str = line.decode().strip()
                if not line_str:
                    continue

                try:
                    msg = json.loads(line_str)
                except json.JSONDecodeError:
                    continue

                msg_type = msg.get("type", "")

                if msg_type == "qr":
                    # Print QR code to terminal for scanning
                    qr_data = msg.get("data", "")
                    print(f"[whatsapp] Scan QR code in your WhatsApp app:", flush=True)
                    print(f"[whatsapp] QR data: {qr_data[:50]}...", flush=True)
                    # Try to render QR in terminal
                    try:
                        import qrcode  # type: ignore
                        qr = qrcode.QRCode(box_size=1, border=1)
                        qr.add_data(qr_data)
                        qr.print_ascii(out=sys.stderr)
                    except ImportError:
                        print(
                            "[whatsapp] Install 'qrcode' package for terminal QR rendering",
                            flush=True,
                        )

                elif msg_type == "connected":
                    data = msg.get("data", {})
                    name = data.get("name", "")
                    wa_id = data.get("id", "")
                    wa_phone = data.get("phone", wa_id.split("@")[0].split(":")[0] if "@" in wa_id else "")
                    # Store connected phone in config so ChannelManager can use it
                    config["connected_phone"] = wa_phone
                    print(f"[whatsapp] Connected: {name} ({wa_id}) phone={wa_phone}", flush=True)

                elif msg_type == "disconnected":
                    reason = msg.get("reason", "unknown")
                    print(f"[whatsapp] Disconnected: {reason}", flush=True)

                elif msg_type == "message":
                    data = msg.get("data", {})
                    inbound = InboundMessage(
                        channel_id=config.get("channel_id", ""),
                        chat_id=data.get("chat_id", ""),
                        channel_type="whatsapp",
                        user_id=data.get("user_id"),
                        username=data.get("username"),
                        text=data.get("text", ""),
                        raw=data,
                    )
                    try:
                        await on_message(inbound)
                    except Exception:
                        logger.exception("Error in WhatsApp message callback")

                elif msg_type == "error":
                    err_msg = msg.get("message", "unknown error")
                    print(f"[whatsapp] Bridge error: {err_msg}", flush=True)

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("WhatsApp adapter read loop error")

    async def disconnect(self) -> None:
        self._running = False

        # Tell the bridge to quit
        if self._writer and not self._writer.is_closing():
            try:
                self._writer.write(json.dumps({"type": "quit"}).encode() + b"\n")
                await self._writer.drain()
            except Exception:
                pass
            try:
                self._writer.close()
            except Exception:
                pass

        self._reader = None
        self._writer = None

        # Kill the bridge process
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            print("[whatsapp] Bridge process stopped", flush=True)

        self._process = None

        # Clean up socket file
        try:
            Path(self._socket_path).unlink(missing_ok=True)
        except Exception:
            pass
