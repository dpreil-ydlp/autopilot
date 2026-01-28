#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

SYSTEM_PYTHON="/usr/bin/python3"

ensure_pipx() {
  if command -v pipx >/dev/null 2>&1; then
    return 0
  fi
  if [[ -x "$SYSTEM_PYTHON" ]]; then
    "$SYSTEM_PYTHON" -m pip install --user --upgrade pipx >/dev/null 2>&1 || return 1
    USER_BIN="$("$SYSTEM_PYTHON" -m site --user-base)/bin"
    export PATH="$USER_BIN:$PATH"
    command -v pipx >/dev/null 2>&1 && return 0
  fi
  return 1
}

if ensure_pipx; then
  echo "Installing Autopilot via pipx (editable)..."
  pipx install -e "$ROOT_DIR"
else
  echo "pipx not available. Installing Autopilot with system pip --user (editable)..."
  if [[ -x "$SYSTEM_PYTHON" ]]; then
    "$SYSTEM_PYTHON" -m pip install --user -e "$ROOT_DIR"
    USER_BIN="$("$SYSTEM_PYTHON" -m site --user-base)/bin"
  else
    python3 -m pip install --user -e "$ROOT_DIR"
    USER_BIN="$(python3 -m site --user-base)/bin"
  fi
  if [[ ":$PATH:" != *":$USER_BIN:"* ]]; then
    echo ""
    echo "Add this to your shell profile to use 'autopilot' everywhere:"
    echo "  export PATH=\"$USER_BIN:\$PATH\""
  fi
fi

echo ""
echo "Done. Try: autopilot -h"
