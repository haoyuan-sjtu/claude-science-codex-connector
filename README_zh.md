# Claude Science_Codex Connector

[English](README.md) | 简体中文

用 **ChatGPT Pro / Plus 订阅自带的 Codex 额度** 直接驱动 Claude Science 的本地代理工具。你不需要额外购买 OpenAI API key，只要有 ChatGPT Pro 或 Plus 订阅，通过 Codex device code 登录即可把 Codex 额度桥接给 Claude Science 使用。

> **本项目基于 [Jyx0208/claude-science-api-bridge](https://github.com/Jyx0208/claude-science-api-bridge)（MIT 协议）修改。**
> 原项目让 Claude Science 使用 DeepSeek / OpenAI / 自定义 API key。
> 本项目在此基础上修改为：**用 ChatGPT Pro / Plus 附带的 Codex 额度即可使用 Claude Science**，无需额外的 OpenAI API key。

## 原理

ChatGPT Pro/Plus 的登录 token **不能**直接调用 `api.openai.com`。本工具在本机启动一个 Anthropic 兼容代理（`http://127.0.0.1:9876`），把 Claude Science 发出的 Anthropic Messages 请求翻译成 OpenAI Responses 协议，转发到 ChatGPT 官方的 Codex 后端（`https://chatgpt.com/backend-api/codex/responses`），并带上 `chatgpt-account-id` 头。这样 Claude Science 只看到标准 Anthropic 接口，而实际算力来自你的 ChatGPT 订阅额度（含流式与工具调用）。

## 使用要求

- macOS设备上测试（Windows/Linux待确认）
- 已安装 Claude Science **并至少打开过一次**（首次启动会生成 `~/.claude-science/encryption.key`，桥接需要它来生成本地 OAuth token，见[快速开始](#快速开始)）
- ChatGPT Pro 或 Plus 订阅
- Python 3.9+（配置阶段仅需标准库；代理的第三方依赖会由启动脚本自动装入本地 `.venv`）

## 快速开始

```bash
# 0. 克隆仓库并进入目录
git clone https://github.com/haoyuan-sjtu/claude-science-codex-connector.git
cd claude-science-codex-connector

# 1.（可选）预装依赖。启动脚本会在首次运行时自动创建本地 .venv 并安装，
#    所以这一步只是用来确认你的 Python 能装上这些包。
pip install -r requirements.txt

# 2. 用 Codex device code 登录（浏览器打开 https://auth.openai.com/codex/device 输入 code）
python3 setup-codex-device.py

# 3. 启动代理（首次会自动创建 .venv、用 ~/.claude-science/encryption.key 生成本地
#    OAuth token，然后前台运行代理）
bash ./start.sh
```

> **首次使用前置条件：** 第 3 步之前，请确保 Claude Science app 已**至少打开过一次**。首次打开会生成 `~/.claude-science/encryption.key`；若该文件不存在，`start.sh` 会打印警告并跳过 token 生成，Claude Science 将不会走桥接。

> 所有命令都需在 `claude-science-codex-connector` 目录内执行。若提示 `no such file or directory: ./start.sh`，说明没在仓库目录里——先 `cd claude-science-codex-connector`。若 `./start.sh` 提示权限不足，执行 `chmod +x start.sh setup-codex-device.py`，或直接用上面的 `bash start.sh` / `python3 setup-codex-device.py` 形式。

> 代理启动后，仍需**通过桥接**启动 Claude Science（从 Dock 直接打开不会走代理）。请看[每次开机后的启动步骤](#每次开机后的启动步骤)。

`setup-codex-device.py` 会：

- 如果本机已用 `codex login` 登录过 Codex CLI（存在 `~/.codex/auth.json`），直接导入，无需再输入 code；
- 否则打印登录地址 `https://auth.openai.com/codex/device` 和一次性 code，你在浏览器登录并输入 code 即可。

登录成功后，token 保存在本机 `codex-auth.json`（权限 `0600`），并自动把后端切换为 ChatGPT Codex：

```json
{
  "openai_auth_mode": "codex_device",
  "default_backend": "openai",
  "codex_backend_url": "https://chatgpt.com/backend-api/codex",
  "codex_model": "gpt-5.5"
}
```

## 让 Claude Science 使用代理

代理启动后，Claude Science 必须在带有本地代理地址的环境下启动：

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:9876
```

从 Dock / Finder / Spotlight 启动 Claude Science **不会**继承该变量，因此会直接调用真实的 `api.anthropic.com` 并返回 `401`。请改用[每次开机后的启动步骤](#每次开机后的启动步骤)里的一键脚本或手动启动方式。也可以打开管理面板确认状态：

```
http://127.0.0.1:9876/dashboard
```

## 每次开机后的启动步骤

上面的首次配置只需做一次。每次开机后，用一键脚本（推荐）或下面的手动步骤把整套服务拉起来即可。

> 下文路径写作 `.../start-mac.sh`、`.../start.sh` 等，`...` 表示**本项目在本机的实际安装目录**（以本地实际路径为准，例如 `~/.claude-science/claude-science-api-bridge-main`），请按实际路径替换。

> ⚠️ **macOS 注意：** 不要从 Dock / Finder / Spotlight 启动 Claude Science。GUI 启动方式**不会**继承 `ANTHROPIC_BASE_URL` 环境变量，Claude Science 会直接用已过期的真实会话调用 `api.anthropic.com`，再次出现 `401` / `"Your Claude session is no longer valid"`。必须从终端启动（或用一键脚本）以继承环境变量。

### 一键启动（推荐）

**macOS**（任意目录均可）：
```bash
bash .../start-mac.sh
```
- 在 `127.0.0.1:9876` 后台启动桥接代理（日志：`~/.claude-science/logs/bridge-proxy.log`）
- 以 `ANTHROPIC_BASE_URL=http://127.0.0.1:9876` 重新启动 Claude Science
- 验证环境变量已注入 Claude Science 进程
- `bash start-mac.sh --stop` 全部停止；`--proxy-only` 只启动代理

**Windows（PowerShell）：**
```powershell
powershell -ExecutionPolicy Bypass -File .\start-windows.ps1
```
- `-Stop` 全部停止；`-ProxyOnly` 只启动代理
- 若 Claude Science 不在标准安装目录，编辑脚本顶部 `$candidates` 列表
- Windows 支持未经测试，见[使用要求](#使用要求)

### 手动步骤（macOS）

每次开机后，按顺序执行。

**第 1 步 —— 启动桥接代理**（前台运行，保留此终端窗口）：
```bash
bash .../start.sh
```
看到 `Dashboard: http://127.0.0.1:9876/dashboard` 即启动成功。**这个终端不要关**（关了代理就停了）。

**第 2 步 —— 新开一个终端窗口，启动 Claude Science：**
```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:9876
"/Applications/Claude Science.app/Contents/MacOS/ClaudeScience" &
```

**第 3 步 —— 验证：**
```bash
ps eww -p $(pgrep -f "ClaudeScience" | head -1) | tr ' ' '\n' | grep ANTHROPIC_BASE_URL
```
输出 `ANTHROPIC_BASE_URL=http://127.0.0.1:9876` 即可正常使用。

### 关键提醒

- 不要从 Dock / Finder / Spotlight 启动 Claude Science —— 这样不会继承环境变量，会再次出现 `401` 错误。必须用第 2 步的命令启动。
- 关闭 Claude Science 后想重新打开，只需重跑第 2 步（代理若还活着就不用重跑第 1 步）。
- 代理进程崩了就重跑第 1 步。

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
| `codex_model` | 无匹配映射时的默认模型 | `gpt-5.5` |
| `codex_model_map` | 把每个 Claude 模型映射到对应 Codex 模型（见下） | `{}` |
| `proxy_port` | 本地代理端口 | `9876` |

> 如果 OpenAI 调整了 Codex 后端地址或模型名，可在 `config.json` 中覆盖 `codex_backend_url` / `codex_model`。

### 按模型映射

Claude Science 会请求不同的 Claude 模型（Opus / Sonnet / Haiku）。你可以用 `codex_model_map` 把每个模型映射到指定的 Codex 模型。注意：`force_model` 必须留空，否则它会对所有请求覆盖映射。

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

映射中未列出的模型会回退到 `codex_model`。

> **模型 ID 说明：** 使用 ChatGPT 账号时，Codex 后端只接受其支持的模型 ID（例如
> `gpt-5.5`、`gpt-5.4`）。带后缀的 `gpt-5.5-codex`，或 `gpt-5`/`gpt-5-codex`
> 等都会返回 400 错误。后端同时拒绝 `temperature`、`top_p`、`max_output_tokens`
> 参数，因此代理在 Codex 路径下会自动省略这些参数。

## 项目结构

```text
.
├── README.md              # 英文文档
├── README_zh.md           # 中文文档
├── LICENSE
├── requirements.txt
├── config.example.json
├── proxy.py               # 本地 Anthropic <-> Codex Responses 代理
├── setup-codex-device.py  # Codex device code 登录
├── setup-token.py         # 生成 Claude Science 可接受的本地 OAuth token
├── start.sh               # 启动代理
├── start-mac.sh           # 一键启动：代理 + Claude Science（macOS）
├── start-windows.ps1      # 一键启动：代理 + Claude Science（Windows）
└── static/
    └── dashboard.html     # 管理面板
```

## 安全说明

- `config.json` 和 `codex-auth.json` 已在 `.gitignore` 中排除，不在仓库文件中。
- ChatGPT 登录 token 只保存在本机 `codex-auth.json`（权限 `0600`）。
- 请勿把 `codex-auth.json` 或 `config.json` 上传到网络上。

## 许可证

MIT。见 `LICENSE`。本项目是 [Jyx0208/claude-science-api-bridge](https://github.com/Jyx0208/claude-science-api-bridge)（MIT 协议）的修改版，原项目与本项目均遵循 MIT 协议。
