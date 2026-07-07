#!/usr/bin/env bash
#
# start-mac.sh — One-click startup for Claude Science + Codex bridge (macOS)
#
# What it does:
#   1. Starts the local bridge proxy (port 9876) in the background
#   2. Waits until the proxy is healthy
#   3. Quits any running Claude Science, then relaunches it with
#      ANTHROPIC_BASE_URL pointed at the bridge
#   4. Verifies the env var was injected into the Claude Science process
#
# Usage:
#   bash start-mac.sh          # start everything
#   bash start-mac.sh --proxy-only   # start only the proxy
#   bash start-mac.sh --stop         # stop proxy + Claude Science
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BRIDGE_DIR="$SCRIPT_DIR"
DATA_DIR="${HOME}/.claude-science"
LOG_DIR="${DATA_DIR}/logs"
PROXY_LOG="${LOG_DIR}/bridge-proxy.log"
PID_FILE="${LOG_DIR}/bridge-proxy.pid"

PROXY_HOST="127.0.0.1"
PROXY_PORT="9876"
BASE_URL="http://${PROXY_HOST}:${PROXY_PORT}"
CLAUDE_APP="/Applications/Claude Science.app/Contents/MacOS/ClaudeScience"

mkdir -p "$LOG_DIR"

c_ok()   { printf "\033[32m[ok]\033[0m   %s\n" "$1"; }
c_info() { printf "\033[36m[start]\033[0m %s\n" "$1"; }
c_warn() { printf "\033[33m[warn]\033[0m %s\n" "$1"; }
c_err()  { printf "\033[31m[error]\033[0m %s\n" "$1"; }

VENV_PY="$BRIDGE_DIR/.venv/bin/python"

# Ensure a dedicated virtualenv with the proxy's dependencies exists.
# Falls back to auto-creating it from any available python3 (one-time).
ensure_venv() {
  if [ -x "$VENV_PY" ]; then return 0; fi
  c_info "Creating virtualenv (.venv) — one-time setup ..."
  local created=0 base_py
  for base_py in python3 /usr/local/bin/python3 /usr/bin/python3; do
    if command -v "$base_py" >/dev/null 2>&1 && "$base_py" -m venv "$BRIDGE_DIR/.venv" 2>/dev/null; then
      created=1; break
    fi
  done
  if [ "$created" -ne 1 ]; then
    c_err "Could not create .venv (need python3 with the venv module)."
    exit 1
  fi
  c_info "Installing requirements.txt into .venv ..."
  "$BRIDGE_DIR/.venv/bin/pip" install --quiet --upgrade pip
  if ! "$BRIDGE_DIR/.venv/bin/pip" install --quiet -r "$BRIDGE_DIR/requirements.txt"; then
    c_err "pip install -r requirements.txt failed."
    exit 1
  fi
  c_ok "Virtualenv ready"
}

proxy_listening() {
  lsof -nP -iTCP:"$PROXY_PORT" -sTCP:LISTEN >/dev/null 2>&1
}

proxy_healthy() {
  curl -sS --max-time 2 "$BASE_URL/health" >/dev/null 2>&1
}

stop_all() {
  c_info "Stopping Claude Science..."
  pkill -f "Claude Science.app" 2>/dev/null || true
  c_info "Stopping bridge proxy..."
  if [ -f "$PID_FILE" ]; then
    kill "$(cat "$PID_FILE")" 2>/dev/null || true
    rm -f "$PID_FILE"
  fi
  pkill -f "proxy.py" 2>/dev/null || true
  sleep 1
  c_ok "Stopped."
  exit 0
}

case "${1:-}" in
  --stop) stop_all ;;
  --proxy-only) PROXY_ONLY=1 ;;
  "") PROXY_ONLY=0 ;;
  *) echo "Unknown option: $1"; echo "Usage: bash start-mac.sh [--proxy-only|--stop]"; exit 1 ;;
esac

# --- 1. Start bridge proxy -------------------------------------------------
if proxy_listening; then
  c_ok "Bridge proxy already running on :${PROXY_PORT}"
else
  c_info "Launching bridge proxy..."
  cd "$BRIDGE_DIR"

  # Ensure a local config.json exists (start.sh does the same)
  if [ ! -f "$BRIDGE_DIR/config.json" ] && [ -f "$BRIDGE_DIR/config.example.json" ]; then
    cp "$BRIDGE_DIR/config.example.json" "$BRIDGE_DIR/config.json"
    chmod 600 "$BRIDGE_DIR/config.json"
  fi

  # Refresh the BYOK OAuth token if the encryption key is present
  ensure_venv
  if [ -f "${DATA_DIR}/encryption.key" ]; then
    "$VENV_PY" "$BRIDGE_DIR/setup-token.py" >/dev/null 2>&1 || true
  fi

  nohup "$VENV_PY" "$BRIDGE_DIR/proxy.py" >>"$PROXY_LOG" 2>&1 &
  echo $! > "$PID_FILE"

  # Wait for health (up to ~15s)
  healthy=0
  for i in $(seq 1 30); do
    if proxy_healthy; then healthy=1; break; fi
    sleep 0.5
  done
  if [ "$healthy" -ne 1 ]; then
    c_err "Bridge proxy did not become healthy. Check: $PROXY_LOG"
    exit 1
  fi
  c_ok "Bridge proxy is healthy"
fi

echo "        Dashboard: ${BASE_URL}/dashboard"
echo "        Health:    ${BASE_URL}/health"
echo "        Proxy log: ${PROXY_LOG}"

if [ "$PROXY_ONLY" -eq 1 ]; then
  c_ok "Proxy-only mode. Claude Science not launched."
  exit 0
fi

# --- 2. Quit existing Claude Science --------------------------------------
c_info "Quitting any running Claude Science..."
pkill -f "Claude Science.app" 2>/dev/null || true
sleep 2

if [ ! -x "$CLAUDE_APP" ]; then
  c_err "Claude Science not found at: $CLAUDE_APP"
  c_warn "Start the proxy above, then launch Claude Science manually with:"
  c_warn "  ANTHROPIC_BASE_URL=${BASE_URL} open -a 'Claude Science'"
  exit 1
fi

# --- 3. Launch Claude Science with env var --------------------------------
c_info "Launching Claude Science with ANTHROPIC_BASE_URL=${BASE_URL} ..."
export ANTHROPIC_BASE_URL="$BASE_URL"
"$CLAUDE_APP" &
disown 2>/dev/null || true

# --- 4. Verify env var was injected ---------------------------------------
sleep 3
CLAUDE_PID="$(pgrep -f "ClaudeScience" | head -1 || true)"
if [ -n "$CLAUDE_PID" ] && ps eww -p "$CLAUDE_PID" 2>/dev/null | tr ' ' '\n' | grep -q "ANTHROPIC_BASE_URL=${BASE_URL}"; then
  c_ok "ANTHROPIC_BASE_URL injected: ${BASE_URL} (pid ${CLAUDE_PID})"
else
  c_warn "Env var not detected. Verify manually:"
  c_warn "  ps eww -p \$(pgrep -f ClaudeScience | head -1) | tr ' ' '\n' | grep ANTHROPIC_BASE_URL"
fi

echo
c_ok "Done. Claude Science is running through the bridge."
echo "        To stop: bash start-mac.sh --stop"
