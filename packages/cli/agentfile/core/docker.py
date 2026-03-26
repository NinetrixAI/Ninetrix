"""Docker SDK wrapper for building, running, and pushing agent images."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tempfile
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
    """Build a Docker image via `docker buildx build` (BuildKit).

    Uses subprocess so BuildKit cache mounts work and output streams in
    real-time.  Returns the full image tag.
    """
    import re
    import threading
    import time

    full_tag = f"{image_name}:{tag}" if ":" not in image_name else image_name

    console.print(f"  Building [bold]{full_tag}[/bold]")
    start_time = time.monotonic()

    # BuildKit progress line: "#6 [3/8] RUN pip install ..."
    step_re = re.compile(r"#\d+\s+\[(?:\w+\s+)?(\d+)/(\d+)\]\s+(.+)")

    cmd = [
        "docker", "buildx", "build",
        "--tag", full_tag,
        "--load",              # load into local docker images
        "--progress=plain",    # machine-readable output
        str(context_dir),
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,             # line-buffered
        text=True,
        env={**os.environ, "DOCKER_BUILDKIT": "1"},
    )

    from rich.live import Live
    from rich.text import Text

    build_log: list[str] = []
    current_label = "  [dim]Starting build…[/dim]"
    stop_timer = threading.Event()

    def _timer_loop(live_obj):
        while not stop_timer.wait(1.0):
            elapsed = time.monotonic() - start_time
            live_obj.update(
                Text.from_markup(f"{current_label}  [dim]({elapsed:.0f}s)[/dim]")
            )

    with Live(
        Text.from_markup(current_label),
        console=console,
        refresh_per_second=4,
    ) as live:
        timer_thread = threading.Thread(target=_timer_loop, args=(live,), daemon=True)
        timer_thread.start()
        while True:
            raw_line = proc.stdout.readline()
            if not raw_line and proc.poll() is not None:
                break
            line = raw_line.rstrip()
            if line:
                build_log.append(line)
                m = step_re.search(line)
                if m:
                    step_num, total, instruction = m.group(1), m.group(2), m.group(3)
                    label = instruction.strip()
                    if len(label) > 60:
                        label = label[:57] + "…"
                    current_label = f"  [dim]Step {step_num}/{total}:[/dim] {label}"
                    elapsed = time.monotonic() - start_time
                    live.update(
                        Text.from_markup(f"{current_label}  [dim]({elapsed:.0f}s)[/dim]")
                    )
        stop_timer.set()

    rc = proc.wait()
    elapsed = time.monotonic() - start_time

    if rc == 0:
        console.print(f"  [green]✓[/green] Image built: [bold]{full_tag}[/bold] [dim]({elapsed:.0f}s)[/dim]")
        return full_tag

    # Build failed — try to extract useful error info
    full_output = "\n".join(build_log)
    apt_matches = re.findall(r"Unable to locate package\s+(\S+)", full_output)
    npm_matches = re.findall(r"npm error 404\s.*'([^']+)'", full_output)
    pip_matches = re.findall(r"No matching distribution found for\s+(\S+)", full_output)

    if apt_matches or npm_matches or pip_matches:
        all_bad = apt_matches + npm_matches + pip_matches
        console.print(f"\n  [red]✗[/red] Package not found: [bold]{', '.join(all_bad)}[/bold]")
        console.print("    [dim]Hint: Check the spelling in your agentfile.yaml 'packages' list.[/dim]")
    else:
        # Show last few meaningful lines from the build log
        error_lines = [line for line in build_log[-20:] if "ERROR" in line or "error" in line.lower()]
        msg = error_lines[-1] if error_lines else "see output above"
        console.print(f"\n  [red]✗[/red] Build failed: {msg}")
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
    # Write env vars to a temp file instead of passing via command line.
    # This prevents secrets (API keys, tokens) from appearing in `ps aux`.
    env_file = tempfile.NamedTemporaryFile(
        mode="w", prefix="ninetrix-env-", suffix=".env",
        delete=False,
    )
    try:
        for k, v in env.items():
            # Docker --env-file format: KEY=VALUE (no quoting, newlines not supported).
            # Replace newlines to avoid breaking the format.
            env_file.write(f"{k}={v.replace(chr(10), ' ')}\n")
        env_file.close()
        os.chmod(env_file.name, 0o600)
        cmd += ["--env-file", env_file.name]
    except Exception:
        # If env-file creation fails, clean up and fall back to inline args.
        env_file.close()
        os.unlink(env_file.name)
        env_file = None  # type: ignore[assignment]
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
        result = subprocess.run(cmd, check=False, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0 and result.stderr:
            stderr_lower = result.stderr.lower()
            if "port is already allocated" in stderr_lower or "address already in use" in stderr_lower:
                # Extract the port number from the error message if possible
                import re as _re
                port_match = _re.search(r"0\.0\.0\.0:(\d+)", result.stderr)
                port_hint = port_match.group(1) if port_match else "the requested port"
                console.print(
                    f"\n[red]Error:[/red] Port {port_hint} is already in use.\n"
                    "  Stop the existing container with: [bold]docker ps[/bold] then [bold]docker stop <container>[/bold]\n"
                    "  Or change the port in your agentfile.yaml triggers section.\n"
                )
                sys.exit(1)
            else:
                # Let other Docker errors print through as-is
                console.print(result.stderr.rstrip())
    except FileNotFoundError:
        console.print("[red]`docker` CLI not found.[/red] Make sure Docker Desktop is installed.")
        sys.exit(1)
    except KeyboardInterrupt:
        pass  # user pressed Ctrl+C — clean exit
    finally:
        # Clean up the temporary env file so secrets don't persist on disk.
        if env_file is not None:
            try:
                os.unlink(env_file.name)
            except OSError:
                pass


def push_image(image_name: str) -> None:
    """Push image to a registry (uses existing `docker login` credentials)."""
    import time

    client = _client()
    console.print(f"  Pushing [bold]{image_name}[/bold]")
    start_time = time.monotonic()
    try:
        with console.status("  [dim]Uploading layers…[/dim]", spinner="dots") as spinner:
            for chunk in client.images.push(image_name, stream=True, decode=True):
                status = chunk.get("status", "")
                _ = chunk.get("progressDetail", {})
                if "error" in chunk:
                    console.print(f"[red]Push error:[/red] {chunk['error']}")
                    sys.exit(1)
                if status:
                    layer_id = chunk.get("id", "")
                    label = f"  [dim]{layer_id}: {status}[/dim]" if layer_id else f"  [dim]{status}[/dim]"
                    elapsed = time.monotonic() - start_time
                    spinner.update(f"{label}  [dim]({elapsed:.0f}s)[/dim]")
        elapsed = time.monotonic() - start_time
        console.print(f"  [green]✓[/green] Pushed: [bold]{image_name}[/bold] [dim]({elapsed:.0f}s)[/dim]")
    except DockerException as exc:
        console.print(f"[red]Push failed:[/red] {exc}")
        sys.exit(1)
