#!/usr/bin/env bash
# Linux virtual environment provisioning for the shared /workspace inside the
# Paperclip server container (docker-server-1).
#
# Usage: provision-workspace-venv.sh <name> [project-dir]
#   - creates /workspace/.venvs/<name> (a Linux venv that works in the container)
#   - installs <project-dir>/requirements.txt into it (idempotent)
#
# The .venv/ directory inside a repository is a Windows venv (Scripts/) and
# cannot run on Linux. Do not use it inside the container.
#
# If the container image lacks python3-venv, run once as root first:
#   docker exec -u root docker-server-1 sh -c 'apt-get update -qq && apt-get install -y -qq --no-install-recommends python3-venv'
# (this container fs is ephemeral, so a recreated container needs this step again;
# the venv itself persists on the /workspace bind mount)
set -euo pipefail

NAME="${1:?usage: provision-workspace-venv.sh <name> [project-dir]}"
PROJECT="${2:-/workspace/$NAME}"
VENV="/workspace/.venvs/$NAME"

if ! python3 -m ensurepip --version >/dev/null 2>&1; then
  if [ "$(id -u)" -ne 0 ]; then
    echo "python3-venv missing: run the root apt-get command from the script header, then retry." >&2
    exit 1
  fi
  apt-get update -qq
  apt-get install -y -qq --no-install-recommends python3-venv >/dev/null
fi

mkdir -p "$(dirname "$VENV")"
if [ ! -x "$VENV/bin/python" ] || ! "$VENV/bin/python" -c 'import pip' >/dev/null 2>&1; then
  rm -rf "$VENV"
  python3 -m venv "$VENV"
fi

if [ -f "$PROJECT/requirements.txt" ]; then
  "$VENV/bin/pip" install -q -r "$PROJECT/requirements.txt"
fi

"$VENV/bin/python" - <<'EOF'
import sys
print(f"venv ready: {sys.prefix} (python {sys.version.split()[0]})")
EOF
