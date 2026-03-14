"""Friendly error formatting for Docker and CLI failures."""

from __future__ import annotations

import re
import sys
from typing import NoReturn

from rich.console import Console

console = Console()


def _parse_docker_explanation(exc: Exception) -> str:
    """Extract the human-readable explanation from a Docker SDK exception."""
    # APIError stores it in .explanation
    explanation = getattr(exc, "explanation", None) or str(exc)
    # Strip long HTTP preamble: "500 Server Error for http+docker://...: <real msg>"
    clean = re.sub(r"^\d{3} \w[\w ]+ Error for http\+docker://\S+:\s*", "", explanation)
    return clean.strip('"').strip()


def fmt_docker_error(exc: Exception) -> tuple[str, str | None]:
    """Return (short_message, hint_or_None) from a Docker exception.

    Covers the most common failure modes with actionable hints.
    """
    explanation = _parse_docker_explanation(exc)

    # ── Port already allocated ────────────────────────────────────────────────
    m = re.search(r"Bind for [^:]+:(\d+) failed: port is already allocated", explanation)
    if m:
        port = m.group(1)
        return (
            f"Port {port} is already in use",
            f"Stop the service occupying port {port}, or pass a different host port "
            f"(e.g. --db-port / --api-port).",
        )

    # ── Image not found ───────────────────────────────────────────────────────
    m2 = re.search(r"No such image:\s*(.+)", explanation)
    if m2:
        img = m2.group(1).strip()
        return (
            f"Image not found: {img}",
            "Run 'ninetrix build' to build the agent image first.",
        )

    from docker.errors import ImageNotFound  # noqa: PLC0415
    if isinstance(exc, ImageNotFound):
        return (
            f"Image not found: {explanation}",
            "Run 'ninetrix build' to build the agent image first.",
        )

    # ── Registry / pull failures ──────────────────────────────────────────────
    if "failed to resolve reference" in explanation:
        m3 = re.search(r'"([^"]+)"', explanation)
        img = m3.group(1) if m3 else "the image"
        extra = ""
        if "403" in explanation:
            extra = " (registry returned 403 — image may not exist or is private)"
        return (
            f"Cannot pull {img}{extra}",
            "Use --build-api --api-dir <path/to/api> to build the image locally.",
        )

    if "pull access denied" in explanation or "unauthorized" in explanation.lower():
        return (
            "Cannot pull image — access denied",
            "Run 'docker login' or verify the image name and tag.",
        )

    # ── Out of memory ─────────────────────────────────────────────────────────
    if "cannot allocate memory" in explanation.lower() or "out of memory" in explanation.lower():
        return (
            "Not enough memory to start the container",
            "Reduce the memory limit or free up system RAM.",
        )

    # ── Generic ──────────────────────────────────────────────────────────────
    return explanation or str(exc), None


def docker_fail(exc: Exception, context: str) -> NoReturn:
    """Print a clean Docker error with optional hint, then exit(1)."""
    msg, hint = fmt_docker_error(exc)
    console.print(f"\n  [red]✗[/red] {context}: {msg}")
    if hint:
        console.print(f"    [dim]Hint: {hint}[/dim]")
    sys.exit(1)


def fail(message: str, hint: str | None = None) -> NoReturn:
    """Print a plain error with optional hint, then exit(1)."""
    console.print(f"\n  [red]✗[/red] {message}")
    if hint:
        console.print(f"    [dim]Hint: {hint}[/dim]")
    sys.exit(1)
