# Claude Science ← Codex Connector

English | [简体中文](README_zh.md)

A local proxy that lets you drive **Claude Science** with the **Codex quota included in your ChatGPT Pro / Plus subscription**. You don't need to buy an OpenAI API key — if you have a ChatGPT Pro or Plus plan, just sign in with a Codex device code and your Codex quota is bridged to Claude Science.

> **This project is a modified version of [Jyx0208/claude-science-api-bridge](https://github.com/Jyx0208/claude-science-api-bridge) (MIT license).**
> The original project lets Claude Science use DeepSeek / OpenAI / custom API keys. This project modifies it so that **the Codex quota bundled with a ChatGPT Pro / Plus subscription is enough to use Claude Science**, with no separate OpenAI API key required.

## How it works

A ChatGPT Pro/Plus login token **cannot** call `api.openai.com` directly. This tool runs a local Anthropic-compatible proxy (`http://127.0.0.1:9876`) that translates the Anthropic Messages requests sent by Claude Science into the OpenAI Responses protocol, forwards them to ChatGPT's official Codex backend (`https://chatgpt.com/backend-api/codex/responses`), and attaches the `chatgpt-account-id` header. Claude Science only ever sees a standard Anthropic interface, while the actual compute is billed against your ChatGPT subscription quota (streaming and tool calls included).

## Requirements

- A macOS computer
- Claude Science installed
- A ChatGPT Pro or Plus subscription
- Python 3.9+

## Quick start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Sign in with a Codex device code
#    (opens https://auth.openai.com/codex/device where you enter the code)
./setup-codex-device.py

# 3. Point Claude Science at the local proxy
export ANTHROPIC_BASE_URL=http://127.0.0.1:9876

# 4. Start the proxy
./start.sh
```

`setup-codex-device.py` will:

- Import your existing Codex CLI login automatically if you've already run `codex login` (i.e. `~/.codex/auth.json` exists), so no code entry is needed; otherwise
- Print the login URL `https://auth.openai.com/codex/device` and a one-time code for you to sign in with in the browser.

After a successful login, the token is stored locally in `codex-auth.json` (mode `0600`) and the backend is automatically switched to ChatGPT Codex:

```json
{
  "openai_auth_mode": "codex_device",
  "default_backend": "openai",
  "codex_backend_url": "https://chatgpt.com/backend-api/codex",
  "codex_model": "gpt-5-codex"
}
```

## Point Claude Science at the proxy

Once the proxy is running, make sure Claude Science uses the local proxy address:

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:9876
```

Then (re)start Claude Science. You can also open the management dashboard to check status:

```
http://127.0.0.1:9876/dashboard
```

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
| `codex_model` | Model to use | `gpt-5-codex` |
| `proxy_port` | Local proxy port | `9876` |

> If OpenAI changes the Codex backend URL or model name, override `codex_backend_url` / `codex_model` in `config.json`.

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
└── static/
    └── dashboard.html     # Management dashboard
```

## Security notes

- `config.json` and `codex-auth.json` are excluded via `.gitignore` and will **not** be committed.
- Your ChatGPT login token is stored only on your machine in `codex-auth.json` (mode `0600`).
- Never upload `codex-auth.json` or `config.json` to any repository.

## License

MIT. See `LICENSE`. This project is a modified version of [Jyx0208/claude-science-api-bridge](https://github.com/Jyx0208/claude-science-api-bridge) (MIT license); both the original and this project are released under the MIT license.
