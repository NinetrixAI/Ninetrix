"""Fernet encryption helpers for integration credentials."""
from __future__ import annotations

import base64
import os

from cryptography.fernet import Fernet


def _key() -> bytes:
    raw = os.environ.get("AGENTFILE_ENCRYPTION_KEY", "")
    if not raw:
        # Dev fallback: deterministic key (NOT secure for production)
        raw = "agentfile-dev-encryption-key-32b!"
    padded = raw.encode()[:32].ljust(32, b"=")
    return base64.urlsafe_b64encode(padded)


def encrypt(plaintext: str) -> str:
    return Fernet(_key()).encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    return Fernet(_key()).decrypt(ciphertext.encode()).decode()
