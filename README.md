# Claude Science ← Codex Connector

用 **ChatGPT Pro / Plus 订阅自带的 Codex 额度** 直接驱动 Claude Science 的本地代理工具。你不需要购买 OpenAI API key，只要有 ChatGPT Pro 或 Plus 订阅，通过 Codex device code 登录即可把 Codex 额度桥接给 Claude Science 使用。

> **本项目基于 [Jyx0208/claude-science-api-bridge](https://github.com/Jyx0208/claude-science-api-bridge)（MIT 协议）修改。**
> 原项目让 Claude Science 使用 DeepSeek / OpenAI / 自定义 API key。本项目在此基础上修改为：**用 ChatGPT Pro / Plus 附带的 Codex 额度即可使用 Claude Science**，无需额外的 OpenAI API key。

## 原理

ChatGPT Pro/Plus 的登录 token **不能**直接调用 `api.openai.com`。本工具在本机启动一个 Anthropic 兼容代理（`http://127.0.0.1:9876`），把 Claude Science 发出的 Anthropic Messages 请求翻译成 OpenAI Responses 协议，转发到 ChatGPT 官方的 Codex 后端（`https://chatgpt.com/backend-api/codex/responses`），并带上 `chatgpt-account-id` 头。这样 Claude Science 只看到标准 Anthropic 接口，而实际算力来自你的 ChatGPT 订阅额度（含流式与工具调用）。

## 使用要求

- 一台 macOS 电脑
- 已安装 Claude Science
- 一个 ChatGPT Pro 或 Plus 订阅
- Python 3.9+

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 用 Codex device code 登录（浏览器打开 https://auth.openai.com/codex/device 输入 code）
./setup-codex-device.py

# 3. 让 Claude Science 走本地代理
export ANTHROPIC_BASE_URL=http://127.0.0.1:9876

# 4. 启动代理
./start.sh
```

`setup-codex-device.py` 会：

- 如果本机已用 `codex login` 登录过 Codex CLI（存在 `~/.codex/auth.json`），直接导入，无需再输入 code；
- 否则打印登录地址 `https://auth.openai.com/codex/device` 和一次性 code，你在浏览器登录并输入 code 即可。

登录成功后，token 保存在本机 `codex-auth.json`（权限 `0600`），并自动把后端切换为 ChatGPT Codex：

```json
{
  "openai_auth_mode": "codex_device",
  "default_backend": "openai",
  "codex_backend_url": "https://chatgpt.com/backend-api/codex",
  "codex_model": "gpt-5-codex"
}
```

## 让 Claude Science 使用代理

代理启动后，确保 Claude Science 使用本地代理地址：

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:9876
```

然后（重新）启动 Claude Science。也可以打开管理面板确认状态：

```
http://127.0.0.1:9876/dashboard
```

## 验证

```bash
curl -sS http://127.0.0.1:9876/health
curl -sS http://127.0.0.1:9876/v1/messages \
  -H 'Content-Type: application/json' \
  -d '{"model":"claude-sonnet-4-5","max_tokens":32,"messages":[{"role":"user","content":"Reply OK"}]}'
```

`/health` 返回 `"status":"ok"`，`/v1/messages` 返回 Anthropic 格式的消息即成功。

## 配置说明

默认配置见 `config.example.json`，首次运行 `start.sh` 会自动复制成本机 `config.json`。常用项：

| 字段 | 说明 | 默认 |
| --- | --- | --- |
| `openai_auth_mode` | `codex_device` 时使用 ChatGPT 订阅额度 | `api_key` |
| `codex_backend_url` | ChatGPT Codex 后端地址 | `https://chatgpt.com/backend-api/codex` |
| `codex_model` | 使用的模型 | `gpt-5-codex` |
| `proxy_port` | 本地代理端口 | `9876` |

> 如果 OpenAI 调整了 Codex 后端地址或模型名，可在 `config.json` 中覆盖 `codex_backend_url` / `codex_model`。

## 项目结构

```text
.
├── README.md
├── LICENSE
├── requirements.txt
├── config.example.json
├── proxy.py               # 本地 Anthropic <-> Codex Responses 代理
├── setup-codex-device.py  # Codex device code 登录
├── setup-token.py         # 生成 Claude Science 可接受的本地 OAuth token
├── start.sh               # 启动代理
└── static/
    └── dashboard.html     # 管理面板
```

## 安全说明

- `config.json` 和 `codex-auth.json` 已在 `.gitignore` 中排除，**不会**被提交。
- 你的 ChatGPT 登录 token 只保存在本机 `codex-auth.json`（权限 `0600`）。
- 请勿把 `codex-auth.json` 或 `config.json` 上传到任何仓库。

## 许可证

MIT。见 `LICENSE`。本项目是 [Jyx0208/claude-science-api-bridge](https://github.com/Jyx0208/claude-science-api-bridge)（MIT 协议）的修改版，原项目与本项目均遵循 MIT 协议。
