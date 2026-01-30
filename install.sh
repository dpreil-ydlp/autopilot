#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

SYSTEM_PYTHON="/usr/bin/python3"

is_py_ge_311() {
  local py="$1"
  "$py" - <<'PY' >/dev/null 2>&1
import sys
sys.exit(0 if sys.version_info >= (3,11) else 1)
PY
}

find_python_311() {
  local candidates=(
    "python3.14"
    "python3.13"
    "python3.12"
    "python3.11"
    "/opt/homebrew/bin/python3"
    "/usr/local/bin/python3"
    "python3"
    "$SYSTEM_PYTHON"
  )
  for py in "${candidates[@]}"; do
    if command -v "$py" >/dev/null 2>&1; then
      local resolved
      resolved="$(command -v "$py")"
      if is_py_ge_311 "$resolved"; then
        echo "$resolved"
        return 0
      fi
    elif [[ -x "$py" ]]; then
      if is_py_ge_311 "$py"; then
        echo "$py"
        return 0
      fi
    fi
  done
  return 1
}

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

PYTHON_BIN="$(find_python_311 || true)"
if [[ -z "${PYTHON_BIN:-}" ]]; then
  echo "ERROR: Python 3.11+ not found. Please install Python 3.11+ and re-run."
  exit 1
fi

if ensure_pipx; then
  echo "Installing Autopilot via pipx (editable)..."
  # Use --force so re-running the installer upgrades an existing pipx install.
  pipx install -e "$ROOT_DIR" --python "$PYTHON_BIN" --force
else
  echo "pipx not available. Installing Autopilot with system pip --user (editable)..."
  "$PYTHON_BIN" -m pip install --user --upgrade -e "$ROOT_DIR"
  USER_BIN="$("$PYTHON_BIN" -m site --user-base)/bin"
  if [[ ":$PATH:" != *":$USER_BIN:"* ]]; then
    echo ""
    echo "Add this to your shell profile to use 'autopilot' everywhere:"
    echo "  export PATH=\"$USER_BIN:\$PATH\""
  fi
fi

echo ""
echo "Done. Try: autopilot -h"

# Ensure global shim if possible
PIPX_BIN="${HOME}/.local/bin"
if [[ -x "${PIPX_BIN}/autopilot" && -w "/usr/local/bin" ]]; then
  ln -sf "${PIPX_BIN}/autopilot" /usr/local/bin/autopilot
fi
