"""Tests for agentfile.core.errors — error formatting."""

from __future__ import annotations

import pytest

from agentfile.core.errors import _parse_docker_explanation, fmt_docker_error


class TestParseDockerExplanation:
    def test_strips_http_preamble(self):
        exc = Exception(
            '500 Server Error for http+docker://localhost/v1.44/containers: "port 8080 is in use"'
        )
        result = _parse_docker_explanation(exc)
        assert result == "port 8080 is in use"

    def test_uses_explanation_attr(self):
        exc = Exception("fallback")
        exc.explanation = "the real error"
        result = _parse_docker_explanation(exc)
        assert result == "the real error"

    def test_fallback_to_str(self):
        exc = Exception("simple error")
        result = _parse_docker_explanation(exc)
        assert result == "simple error"


class TestFmtDockerError:
    def test_port_already_allocated(self):
        exc = Exception("Bind for 0.0.0.0:8080 failed: port is already allocated")
        msg, hint = fmt_docker_error(exc)
        assert "8080" in msg
        assert "already in use" in msg
        assert hint is not None
        assert "port" in hint.lower()

    def test_image_not_found(self):
        exc = Exception("No such image: ninetrix/test:latest")
        msg, hint = fmt_docker_error(exc)
        assert "not found" in msg.lower()
        assert "ninetrix build" in hint

    def test_image_not_found_exception_type(self):
        from docker.errors import ImageNotFound
        exc = ImageNotFound("ninetrix/test:latest")
        msg, hint = fmt_docker_error(exc)
        assert "not found" in msg.lower()

    def test_pull_access_denied(self):
        exc = Exception("pull access denied for ninetrix/test, unauthorized")
        msg, hint = fmt_docker_error(exc)
        assert "access denied" in msg.lower()
        assert "docker login" in hint

    def test_resolve_reference_failure(self):
        exc = Exception('failed to resolve reference "ghcr.io/org/image:v1"')
        msg, hint = fmt_docker_error(exc)
        assert "Cannot pull" in msg

    def test_out_of_memory(self):
        exc = Exception("cannot allocate memory")
        msg, hint = fmt_docker_error(exc)
        assert "memory" in msg.lower()
        assert hint is not None

    def test_generic_error(self):
        exc = Exception("some other docker error")
        msg, hint = fmt_docker_error(exc)
        assert msg == "some other docker error"
        assert hint is None

    def test_resolve_reference_with_403(self):
        exc = Exception('failed to resolve reference "ghcr.io/org/image:v1" 403')
        msg, hint = fmt_docker_error(exc)
        assert "403" in msg


class TestDockerFail:
    def test_exits_with_code_1(self):
        from agentfile.core.errors import docker_fail
        exc = Exception("test error")
        with pytest.raises(SystemExit) as exc_info:
            docker_fail(exc, "Building")
        assert exc_info.value.code == 1


class TestFail:
    def test_exits_with_code_1(self):
        from agentfile.core.errors import fail
        with pytest.raises(SystemExit) as exc_info:
            fail("something broke")
        assert exc_info.value.code == 1

    def test_exits_with_hint(self):
        from agentfile.core.errors import fail
        with pytest.raises(SystemExit):
            fail("broke", hint="try this")
