"""Docker SDK wrapper for building, running, and pushing agent images."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import docker
from docker.errors import DockerException
from rich.console import Console
from rich.panel import Panel

if TYPE_CHECKING:
    from agentfile.core.models import VolumeSpec

console = Console()


def _docker_install_hint() -> str:
    """Return a platform-specific install hint for Docker."""
    system = platform.system()
    if system == "Darwin":
        return "Install Docker Desktop: brew install --cask docker\n    or download from https://docs.docker.com/desktop/install/mac-install/"
    elif system == "Linux":
        return "Install Docker Engine: curl -fsSL https://get.docker.com | sh\n    Then start it: sudo systemctl start docker"
    elif system == "Windows":
        return "Install Docker Desktop: https://docs.docker.com/desktop/install/windows-install/"
    return "Install Docker: https://docs.docker.com/get-docker/"


def require_docker() -> None:
    """Pre-flight check: ensure Docker is installed and the daemon is running.

    Call this at the top of any command that needs Docker. It gives the user
    a clear, actionable error immediately — before any YAML parsing, template
    rendering, or image building takes place.
    """
    # 1. Is the `docker` CLI binary on PATH?
    if not shutil.which("docker"):
        console.print()
        console.print(Panel(
            "[red bold]Docker is not installed[/red bold]\n\n"
            "Ninetrix packages agents as Docker containers.\n"
            "You need Docker installed to build and run agents.\n\n"
            f"[dim]{_docker_install_hint()}[/dim]",
            title="[red]Missing dependency[/red]",
            border_style="red",
        ))
        sys.exit(1)

    # 2. Can we connect to the Docker daemon?
    try:
        client = docker.from_env()
        client.ping()
    except DockerException:
        console.print()
        console.print(Panel(
            "[red bold]Docker daemon is not running[/red bold]\n\n"
            "The Docker CLI is installed but the daemon isn't responding.\n"
            "Start Docker Desktop or the Docker service and try again.\n\n"
            + (
                "[dim]macOS: open /Applications/Docker.app\n"
                "Linux: sudo systemctl start docker[/dim]"
                if platform.system() in ("Darwin", "Linux")
                else "[dim]Start Docker Desktop from the Start menu.[/dim]"
            ),
            title="[red]Docker not running[/red]",
            border_style="red",
        ))
        sys.exit(1)


def _client() -> docker.DockerClient:
    try:
        return docker.from_env()
    except DockerException as exc:
        console.print(f"[red]Docker is not running or not installed:[/red] {exc}")
        sys.exit(1)


def build_image(context_dir: Path, image_name: str, tag: str = "latest") -> str:
    """Build a Docker image from context_dir. Returns the full image tag."""
    client = _client()
    full_tag = f"{image_name}:{tag}" if ":" not in image_name else image_name

    console.print(f"  Building [bold]{full_tag}[/bold] …")
    try:
        _image, logs = client.images.build(
            path=str(context_dir),
            tag=full_tag,
            rm=True,
            forcerm=True,
        )
        for chunk in logs:
            line = chunk.get("stream", "").rstrip()
            if line:
                console.print(f"    [dim]{line}[/dim]")
        console.print(f"  [green]✓[/green] Image built: [bold]{full_tag}[/bold]")
        return full_tag
    except DockerException as exc:
        console.print(f"[red]Build failed:[/red] {exc}")
        sys.exit(1)


def run_container(
    image_name: str,
    env: dict[str, str] | None = None,
    port_bindings: list[str] | None = None,
    interactive: bool = True,
    cpu: float | None = None,
    memory: str | None = None,
    warm_pool: bool = False,
    volumes: list["VolumeSpec"] | None = None,
    restart_policy: str | None = None,
) -> None:
    """Run an agent container, optionally with TTY, port bindings, and resource limits."""
    env = env or {}

    # --restart and --rm are mutually exclusive in Docker
    no_rm = warm_pool or bool(restart_policy)
    # Build the equivalent manual command and print it so the user can reuse it
    rm_flag = "" if no_rm else "--rm "
    tty_flag = "-it " if interactive else ""
    port_flags = " ".join(f"-p {p}" for p in (port_bindings or []))
    port_display = f" {port_flags}" if port_flags else ""
    env_flags = " ".join(f"-e {k}=..." for k in env)
    console.print(
        f"  [dim]Equivalent:[/dim] docker run {rm_flag}{tty_flag}{env_flags}{port_display} {image_name}\n"
    )

    # Use subprocess + docker CLI so we get a real TTY (stdin/stdout pass-through).
    # The Docker SDK cannot attach a TTY when the Python process itself is a TTY.
    # --add-host ensures host.docker.internal resolves to the host on Linux too
    # (macOS Docker Desktop provides it automatically; this is a no-op there).
    cmd = ["docker", "run"]
    if not no_rm:
        cmd.append("--rm")
    if restart_policy:
        cmd += ["--restart", restart_policy]
    if interactive:
        cmd += ["-it"]
    cmd.append("--add-host=host.docker.internal:host-gateway")
    if cpu is not None:
        cmd += ["--cpus", str(cpu)]
    if memory is not None:
        from agentfile.core.models import _parse_memory
        cmd += ["--memory", str(_parse_memory(memory))]
    for port in (port_bindings or []):
        cmd += ["-p", port]
    for k, v in env.items():
        cmd += ["-e", f"{k}={v}"]
    for vol in (volumes or []):
        if vol.provider == "local" and vol.host_path:
            host_path = os.path.expandvars(vol.host_path)
            host_path = str(Path(host_path).resolve())
            mount = f"{host_path}:{vol.container_path}"
            if vol.read_only:
                mount += ":ro"
            cmd += ["-v", mount]
    cmd.append(image_name)

    try:
        subprocess.run(cmd, check=False)
    except FileNotFoundError:
        console.print("[red]`docker` CLI not found.[/red] Make sure Docker Desktop is installed.")
        sys.exit(1)
    except KeyboardInterrupt:
        pass  # user pressed Ctrl+C — clean exit


def push_image(image_name: str) -> None:
    """Push image to a registry (uses existing `docker login` credentials)."""
    client = _client()
    console.print(f"  Pushing [bold]{image_name}[/bold] …")
    try:
        for chunk in client.images.push(image_name, stream=True, decode=True):
            status = chunk.get("status", "")
            progress = chunk.get("progressDetail", {})
            if "error" in chunk:
                console.print(f"[red]Push error:[/red] {chunk['error']}")
                sys.exit(1)
            if status and not progress:
                console.print(f"    [dim]{status}[/dim]")
        console.print(f"  [green]✓[/green] Pushed: [bold]{image_name}[/bold]")
    except DockerException as exc:
        console.print(f"[red]Push failed:[/red] {exc}")
        sys.exit(1)
