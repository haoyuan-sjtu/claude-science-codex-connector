#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

export PROXY_HOST="${PROXY_HOST:-127.0.0.1}"
export PROXY_PORT="${PROXY_PORT:-9876}"

if [ ! -f "$SCRIPT_DIR/config.json" ] && [ -f "$SCRIPT_DIR/config.example.json" ]; then
  cp "$SCRIPT_DIR/config.example.json" "$SCRIPT_DIR/config.json"
  chmod 600 "$SCRIPT_DIR/config.json"
fi

# Use a dedicated virtualenv so the proxy's dependencies (httpx/fastapi/uvicorn/cryptography)
# are available regardless of which system python3 is on PATH. Auto-create on first run.
VENV_PY="$SCRIPT_DIR/.venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
  echo "Creating virtualenv (.venv) — one-time setup ..."
  created=0
  for base_py in python3 /usr/local/bin/python3 /usr/bin/python3; do
    if command -v "$base_py" >/dev/null 2>&1 && "$base_py" -m venv "$SCRIPT_DIR/.venv" 2>/dev/null; then
      created=1; break
    fi
  done
  if [ "$created" -ne 1 ]; then
    echo "Error: could not create .venv (need python3 with the venv module)." >&2
    exit 1
  fi
  echo "Installing requirements.txt into .venv ..."
  "$SCRIPT_DIR/.venv/bin/pip" install --quiet --upgrade pip
  "$SCRIPT_DIR/.venv/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt" || {
    echo "Error: pip install -r requirements.txt failed." >&2; exit 1; }
  echo "Virtualenv ready"
fi

if [ -f "$HOME/.claude-science/encryption.key" ]; then
  "$VENV_PY" "$SCRIPT_DIR/setup-token.py"
else
  echo "Warning: ~/.claude-science/encryption.key does not exist yet."
  echo "Open Claude Science once, then rerun setup-token.py if login state is needed."
fi

echo "Dashboard: http://$PROXY_HOST:$PROXY_PORT/dashboard"
echo "Health:    http://$PROXY_HOST:$PROXY_PORT/health"
echo "Use:       export ANTHROPIC_BASE_URL=http://$PROXY_HOST:$PROXY_PORT"
echo

exec "$VENV_PY" "$SCRIPT_DIR/proxy.py"

