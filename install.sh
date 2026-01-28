#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if command -v pipx >/dev/null 2>&1; then
  echo "Installing Autopilot via pipx (editable)..."
  pipx install -e "$ROOT_DIR"
else
  echo "pipx not found. Installing Autopilot with pip --user (editable)..."
  python3 -m pip install --user -e "$ROOT_DIR"
  USER_BIN="$(python3 -m site --user-base)/bin"
  if [[ ":$PATH:" != *":$USER_BIN:"* ]]; then
    echo ""
    echo "Add this to your shell profile to use 'autopilot' everywhere:"
    echo "  export PATH=\"$USER_BIN:\$PATH\""
  fi
fi

echo ""
echo "Done. Try: autopilot -h"
