#!/bin/sh
# Ninetrix installer
# Usage: curl -fsSL https://install.ninetrix.io | sh

set -e

PACKAGE="ninetrix"
CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
RESET='\033[0m'

say()     { printf "${CYAN}[ninetrix]${RESET} %s\n" "$1"; }
ok()      { printf "${GREEN}[ninetrix]${RESET} %s\n" "$1"; }
warn()    { printf "${YELLOW}[ninetrix]${RESET} %s\n" "$1"; }
err()     { printf "${RED}[ninetrix]${RESET} %s\n" "$1" >&2; exit 1; }

say "Installing ninetrix..."

# ── Try pipx (best for CLI tools) ─────────────────────────────────────────────
if command -v pipx >/dev/null 2>&1; then
    say "Using pipx..."
    pipx install "$PACKAGE" --force
    ok "ninetrix installed via pipx"
    _installed_via="pipx"

# ── Try uv ────────────────────────────────────────────────────────────────────
elif command -v uv >/dev/null 2>&1; then
    say "Using uv..."
    uv tool install "$PACKAGE"
    ok "ninetrix installed via uv"
    _installed_via="uv"

# ── Try pip3 ──────────────────────────────────────────────────────────────────
elif command -v pip3 >/dev/null 2>&1; then
    say "Using pip3..."
    pip3 install --user "$PACKAGE"
    ok "ninetrix installed via pip3"
    _installed_via="pip3"

# ── Install pipx then retry ───────────────────────────────────────────────────
else
    warn "Neither pipx, uv, nor pip3 found. Attempting to install pipx..."

    if command -v brew >/dev/null 2>&1; then
        brew install pipx
        pipx ensurepath
    elif command -v apt-get >/dev/null 2>&1; then
        sudo apt-get install -y pipx
    elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y pipx
    else
        err "Could not find a package manager to install pipx.\nInstall pipx manually: https://pipx.pypa.io\nThen run: pipx install ninetrix"
    fi

    pipx install "$PACKAGE"
    ok "ninetrix installed via pipx"
    _installed_via="pipx"
fi

# ── PATH check ────────────────────────────────────────────────────────────────
if ! command -v ninetrix >/dev/null 2>&1; then
    warn "ninetrix is not on your PATH yet."

    if [ "$_installed_via" = "pipx" ]; then
        warn "Run:  pipx ensurepath"
        warn "Then open a new terminal."
    elif [ "$_installed_via" = "uv" ]; then
        warn "Run:  uv tool update-shell"
        warn "Or add \$(uv tool dir)/bin to your PATH."
    else
        warn "Add ~/.local/bin to your PATH."
    fi
else
    VERSION=$(ninetrix --version 2>/dev/null | awk '{print $NF}')
    printf "\n"
    ok "ninetrix $VERSION is ready."
    printf "\n"
    printf "  Get started:\n"
    printf "    ${CYAN}ninetrix dev${RESET}   — start local stack\n"
    printf "    ${CYAN}ninetrix init${RESET}  — scaffold a new agent\n"
    printf "    ${CYAN}ninetrix build${RESET} — build Docker image\n"
    printf "    ${CYAN}ninetrix run${RESET}   — run your agent\n"
    printf "\n"
    printf "  Docs: https://docs.ninetrix.io\n\n"
fi
