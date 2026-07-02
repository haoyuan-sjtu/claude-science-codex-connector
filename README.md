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
- Claude Science installed
- A ChatGPT Pro or Plus subscription
- Python 3.9+

## Quick start

```bash
# 0. Clone the repo and enter the directory
git clone https://github.com/haoyuan-sjtu/claude-science-codex-connector.git
cd claude-science-codex-connector

# 1. Install dependencies
pip install -r requirements.txt

# 2. Sign in with a Codex device code
#    (opens https://auth.openai.com/codex/device where you enter the code)
python3 setup-codex-device.py

# 3. Point Claude Science at the local proxy
export ANTHROPIC_BASE_URL=http://127.0.0.1:9876

# 4. Start the proxy
bash ./start.sh
```

> All commands must be run from inside the `claude-science-codex-connector`
> directory. If you see `no such file or directory: ./start.sh`, you are not in
> the repo folder — run `cd claude-science-codex-connector` first. If `./start.sh`
> reports permission denied, run `chmod +x start.sh setup-codex-device.py` or use
> the `bash start.sh` / `python3 setup-codex-device.py` forms shown above.

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
| `codex_model` | Default model when no per-model mapping matches | `gpt-5-codex` |
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
  "codex_model": "gpt-5.5-codex",
  "codex_model_map": {
    "claude-opus-4-8": "gpt-5.5-codex",
    "claude-sonnet-4-5": "gpt-5.5-codex",
    "claude-haiku-4-5": "gpt-5.4-codex"
  }
}
```

Any model not in the map falls back to `codex_model`.

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

- `config.json` and `codex-auth.json` are excluded via `.gitignore` and are not part of the repository files.
- Your ChatGPT login token is stored only on your machine in `codex-auth.json` (mode `0600`).
- Never upload `codex-auth.json` or `config.json` to the internet.

## License

MIT. See `LICENSE`. This project is a modified version of [Jyx0208/claude-science-api-bridge](https://github.com/Jyx0208/claude-science-api-bridge) (MIT license); both the original and this project are released under the MIT license.
