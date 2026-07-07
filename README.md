# Claude Science_Codex Connector

English | [简体中文](README_zh.md)

A local proxy that lets you drive **Claude Science** with the **Codex quota included in your ChatGPT Pro / Plus subscription**. You don't need to buy a separate OpenAI API key — if you have a ChatGPT Pro or Plus plan, just sign in with a Codex device code and your Codex quota is bridged to Claude Science.

> **This project is a modified version of [Jyx0208/claude-science-api-bridge](https://github.com/Jyx0208/claude-science-api-bridge) (MIT license).**
> The original project lets Claude Science use DeepSeek / OpenAI / custom API keys.
> This project modifies it so that **the Codex quota bundled with a ChatGPT Pro / Plus subscription is enough to use Claude Science**, with no separate OpenAI API key required.

## How it works

A ChatGPT Pro/Plus login token **cannot** call `api.openai.com` directly. This tool runs a local Anthropic-compatible proxy (`http://127.0.0.1:9876`) that translates the Anthropic Messages requests sent by Claude Science into the OpenAI Responses protocol, forwards them to ChatGPT's official Codex backend (`https://chatgpt.com/backend-api/codex/responses`), and attaches the `chatgpt-account-id` header. Claude Science only ever sees a standard Anthropic interface, while the actual compute is billed against your ChatGPT subscription quota (streaming and tool calls included).

## Requirements

- Tested on macOS (Windows/Linux not yet confirmed)
- Claude Science installed **and opened at least once** (the first launch creates `~/.claude-science/encryption.key`, which the bridge needs to mint a local OAuth token — see [Quick start](#quick-start))
- A ChatGPT Pro or Plus subscription
- Python 3.9+ (only the standard library is needed for setup; the proxy's third-party deps are auto-installed into a local `.venv` by the start scripts)

## Quick start

```bash
# 0. Clone the repo and enter the directory
git clone https://github.com/haoyuan-sjtu/claude-science-codex-connector.git
cd claude-science-codex-connector

# 1. (Optional) Pre-install dependencies. The start scripts auto-create a
#    local .venv and install these on first run, so this is just a one-time
#    sanity check that your Python can build them.
pip install -r requirements.txt

# 2. Sign in with a Codex device code
#    (opens https://auth.openai.com/codex/device where you enter the code)
python3 setup-codex-device.py

# 3. Start the proxy (auto-creates .venv if missing, mints the local OAuth
#    token via ~/.claude-science/encryption.key, then runs in the foreground)
bash ./start.sh
```

> **First-time prerequisite:** make sure the Claude Science app has been opened **at least once** before step 3. That first launch creates `~/.claude-science/encryption.key`. If it's missing, `start.sh` prints a warning and skips the token step, and Claude Science won't route through the bridge.

> All commands must be run from inside the `claude-science-codex-connector`
> directory. If you see `no such file or directory: ./start.sh`, you are not in
> the repo folder — run `cd claude-science-codex-connector` first. If `./start.sh`
> reports permission denied, run `chmod +x start.sh setup-codex-device.py` or use
> the `bash start.sh` / `python3 setup-codex-device.py` forms shown above.

> Once the proxy is running, you still need to launch Claude Science **through the bridge** (it won't route through the proxy if opened from the Dock). Jump to [Daily startup](#daily-startup-after-each-reboot).

`setup-codex-device.py` will:

- Import your existing Codex CLI login automatically if you've already run `codex login` (i.e. `~/.codex/auth.json` exists), so no code entry is needed; otherwise
- Print the login URL `https://auth.openai.com/codex/device` and a one-time code for you to sign in with in the browser.

After a successful login, the token is stored locally in `codex-auth.json` (mode `0600`) and the backend is automatically switched to ChatGPT Codex:

```json
{
  "openai_auth_mode": "codex_device",
  "default_backend": "openai",
  "codex_backend_url": "https://chatgpt.com/backend-api/codex",
  "codex_model": "gpt-5.5"
}
```

## Point Claude Science at the proxy

Once the proxy is running, Claude Science must be launched with the local proxy address in its environment:

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:9876
```

Launching Claude Science from the Dock / Finder / Spotlight does **not** inherit this variable, so it will call the real `api.anthropic.com` and fail with `401`. Use the one-click script or the manual launch in [Daily startup](#daily-startup-after-each-reboot) instead. You can also open the management dashboard to check status:

```
http://127.0.0.1:9876/dashboard
```

## Daily startup (after each reboot)

The first-time setup above only needs to be done once. After each reboot, use one of the one-click scripts — or the manual steps below — to bring everything back up.

> Paths below are written as `.../start-mac.sh`, `.../start.sh`, etc. `...` stands for **this project's directory on your machine** (your local actual path, e.g. `~/.claude-science/claude-science-api-bridge-main`) — substitute it accordingly.

> ⚠️ **macOS caveat:** do **not** launch Claude Science from the Dock / Finder / Spotlight. A GUI launch does **not** inherit the `ANTHROPIC_BASE_URL` environment variable, so Claude Science will call the real `api.anthropic.com` with an expired session and you'll get `401` / `"Your Claude session is no longer valid"`. Always launch it from the shell (or via the one-click script) so the env var is inherited.

### One-click (recommended)

**macOS** — from anywhere:
```bash
bash .../start-mac.sh
```
- Starts the bridge proxy on `127.0.0.1:9876` (backgrounded; logs at `~/.claude-science/logs/bridge-proxy.log`)
- Relaunches Claude Science with `ANTHROPIC_BASE_URL=http://127.0.0.1:9876`
- Verifies the env var was injected into the Claude Science process
- `bash start-mac.sh --stop` stops everything; `--proxy-only` starts just the proxy

**Windows (PowerShell):**
```powershell
powershell -ExecutionPolicy Bypass -File .\start-windows.ps1
```
- `-Stop` stops everything; `-ProxyOnly` starts just the proxy
- If Claude Science isn't in a standard install folder, edit the `$candidates` list near the top of the script
- Windows support is untested — see [Requirements](#requirements)

### Manual steps (macOS)

Run these in order after each reboot.

**Step 1 — Start the bridge proxy** (foreground; keep this terminal open):
```bash
bash .../start.sh
```
You should see `Dashboard: http://127.0.0.1:9876/dashboard`. Do not close this terminal — closing it stops the proxy.

**Step 2 — In a new terminal, launch Claude Science with the env var:**
```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:9876
"/Applications/Claude Science.app/Contents/MacOS/ClaudeScience" &
```

**Step 3 — Verify:**
```bash
ps eww -p $(pgrep -f "ClaudeScience" | head -1) | tr ' ' '\n' | grep ANTHROPIC_BASE_URL
```
Output `ANTHROPIC_BASE_URL=http://127.0.0.1:9876` means you're good to go.

### Reminders

- Don't launch Claude Science from Dock / Finder / Spotlight — it won't inherit the env var and the `401` error will return.
- If you close Claude Science and want to reopen it, just rerun Step 2 (skip Step 1 if the proxy is still alive).
- If the proxy process died, rerun Step 1.

## Verify

```bash
curl -sS http://127.0.0.1:9876/health
curl -sS http://127.0.0.1:9876/v1/messages \
  -H 'Content-Type: application/json' \
  -d '{"model":"claude-sonnet-4-5","max_tokens":32,"messages":[{"role":"user","content":"Reply OK"}]}'
```

Success means `/health` returns `"status":"ok"` and `/v1/messages` returns an Anthropic-formatted message.

## Configuration

Default settings live in `config.example.json`. The first run of `start.sh` copies it to a local `config.json`. Common options:

| Field | Description | Default |
| --- | --- | --- |
| `openai_auth_mode` | `codex_device` uses your ChatGPT subscription quota | `api_key` |
| `codex_backend_url` | ChatGPT Codex backend URL | `https://chatgpt.com/backend-api/codex` |
| `codex_model` | Default model when no per-model mapping matches | `gpt-5.5` |
| `codex_model_map` | Maps each Claude model to a Codex model (see below) | `{}` |
| `proxy_port` | Local proxy port | `9876` |

> If OpenAI changes the Codex backend URL or model name, override `codex_backend_url` / `codex_model` in `config.json`.

### Per-model mapping

Claude Science requests different Claude models (Opus / Sonnet / Haiku). You can map each to a specific Codex model with `codex_model_map`. Keep `force_model` empty, otherwise it overrides the map for every request.

```json
{
  "openai_auth_mode": "codex_device",
  "default_backend": "openai",
  "force_model": "",
  "codex_model": "gpt-5.5",
  "codex_model_map": {
    "claude-opus-4-8": "gpt-5.5",
    "claude-sonnet-4-5": "gpt-5.5",
    "claude-haiku-4-5": "gpt-5.4"
  }
}
```

Any model not in the map falls back to `codex_model`.

> **Note on model IDs:** with a ChatGPT account, the Codex backend only accepts
> its supported model IDs (e.g. `gpt-5.5`, `gpt-5.4`). Suffixed variants like
> `gpt-5.5-codex` or `gpt-5`/`gpt-5-codex` are rejected with a 400 error.
> The backend also rejects the `temperature`, `top_p`, and `max_output_tokens`
> parameters, so the proxy omits them automatically for the Codex path.

## Project structure

```text
.
├── README.md              # English documentation
├── README_zh.md           # Chinese documentation
├── LICENSE
├── requirements.txt
├── config.example.json
├── proxy.py               # Local Anthropic <-> Codex Responses proxy
├── setup-codex-device.py  # Codex device-code login
├── setup-token.py         # Generates a local OAuth token Claude Science accepts
├── start.sh               # Starts the proxy
├── start-mac.sh           # One-click startup: proxy + Claude Science (macOS)
├── start-windows.ps1      # One-click startup: proxy + Claude Science (Windows)
└── static/
    └── dashboard.html     # Management dashboard
```

## Security notes

- `config.json` and `codex-auth.json` are excluded via `.gitignore` and are not part of the repository files.
- Your ChatGPT login token is stored only on your machine in `codex-auth.json` (mode `0600`).
- Never upload `codex-auth.json` or `config.json` to the internet.

## License

MIT. See `LICENSE`. This project is a modified version of [Jyx0208/claude-science-api-bridge](https://github.com/Jyx0208/claude-science-api-bridge) (MIT license); both the original and this project are released under the MIT license.
