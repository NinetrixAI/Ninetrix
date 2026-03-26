"""Tests for agentfile.core.channel_bridge — Telegram token redaction."""

from __future__ import annotations

import pytest

from agentfile.core.channel_bridge import _redact_token, _tg_url


class TestRedactToken:
    """Ensure Telegram bot tokens are redacted from URLs and error messages."""

    def test_redacts_full_url(self):
        url = "https://api.telegram.org/bot123456:ABCdef_GHIjkl/getUpdates"
        result = _redact_token(url)
        assert "123456:ABCdef_GHIjkl" not in result
        assert "/bot<REDACTED>/getUpdates" in result

    def test_redacts_in_error_message(self):
        msg = (
            "HTTP 409 for url https://api.telegram.org/bot999:XYZ-abc_123/getUpdates "
            "- Conflict: terminated by other getUpdates request"
        )
        result = _redact_token(msg)
        assert "999:XYZ-abc_123" not in result
        assert "bot<REDACTED>" in result

    def test_preserves_non_token_content(self):
        msg = "Connection timeout after 30s"
        assert _redact_token(msg) == msg

    def test_redacts_multiple_occurrences(self):
        msg = (
            "Failed: /bot111:AAA then retried /bot222:BBB"
        )
        result = _redact_token(msg)
        assert "111:AAA" not in result
        assert "222:BBB" not in result

    def test_redacts_long_token(self):
        token = "7654321098:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        result = _redact_token(url)
        assert token not in result


class TestTgUrl:
    """Verify _tg_url builds correct Telegram API URLs."""

    def test_builds_get_updates_url(self):
        url = _tg_url("123:ABC", "getUpdates")
        assert url == "https://api.telegram.org/bot123:ABC/getUpdates"

    def test_builds_send_message_url(self):
        url = _tg_url("123:ABC", "sendMessage")
        assert url == "https://api.telegram.org/bot123:ABC/sendMessage"

    def test_builds_delete_webhook_url(self):
        url = _tg_url("123:ABC", "deleteWebhook")
        assert url == "https://api.telegram.org/bot123:ABC/deleteWebhook"
