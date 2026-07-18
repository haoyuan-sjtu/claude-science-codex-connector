#!/usr/bin/env python3
"""
Local proxy that lets Claude Science use DeepSeek and ChatGPT APIs.

Features:
  - Anthropic ↔ OpenAI format translation (streaming + non-streaming)
  - Model-based routing to DeepSeek / OpenAI
  - Fake OAuth token generation
  - Web management dashboard at http://127.0.0.1:9876/dashboard
  - Persistent config via ~/.claude-science/proxy/config.json
  - Request logging and health monitoring

Quick start:
  ./start.sh
  Then open http://127.0.0.1:9876/dashboard
"""

from __future__ import annotations

import json
import os
import re
import base64
import hashlib
import secrets
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
APP_DIR = Path(__file__).resolve().parent
PROXY_DIR = Path(os.environ.get("CLAUDE_SCIENCE_PROXY_DIR", str(APP_DIR))).expanduser()
CONFIG_FILE = PROXY_DIR / "config.json"
CODEX_AUTH_FILE = PROXY_DIR / "codex-auth.json"
CODEX_CLI_AUTH_FILE = Path.home() / ".codex" / "auth.json"
STATIC_DIR = PROXY_DIR / "static"
TOKEN_DIR = Path.home() / ".claude-science" / ".oauth-tokens"
ENC_KEY_FILE = Path.home() / ".claude-science" / "encryption.key"


# ---------------------------------------------------------------------------
# Config persistence
# ---------------------------------------------------------------------------
class Config:
    """Persistent config backed by config.json."""

    DEFAULTS = {
        "deepseek_api_key": "",
        "openai_api_key": "",
        "custom_api_key": "",
        "openai_auth_mode": "api_key",
        "deepseek_base_url": "https://api.deepseek.com",
        "openai_base_url": "https://api.openai.com",
        "custom_base_url": "",
        "codex_auth_base_url": "https://auth.openai.com",
        "codex_device_url": "https://auth.openai.com/codex/device",
        "codex_client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
        "codex_backend_url": "https://chatgpt.com/backend-api/codex",
        "codex_model": "gpt-5.6-sol",
        "codex_model_map": {},
        "default_backend": "deepseek",
        "force_model": "",
        "deepseek_model_map": {},
        "openai_model_map": {},
        "custom_model_map": {},
        "deepseek_model_pattern": r"deepseek|deep-seek",
        "openai_model_pattern": r"^(gpt-|o1|o3|o4|chatgpt)",
        "custom_model_pattern": "",
        "reasoning_content_policy": "fallback",
        "inline_image_policy": "auto",
        "proxy_host": "127.0.0.1",
        "proxy_port": 9876,
    }

    ENV_KEYS = {
        "deepseek_api_key": "DEEPSEEK_API_KEY",
        "openai_api_key": "OPENAI_API_KEY",
        "custom_api_key": "CUSTOM_API_KEY",
        "openai_auth_mode": "OPENAI_AUTH_MODE",
        "deepseek_base_url": "DEEPSEEK_BASE_URL",
        "openai_base_url": "OPENAI_BASE_URL",
        "custom_base_url": "CUSTOM_BASE_URL",
        "codex_auth_base_url": "CODEX_AUTH_BASE_URL",
        "codex_device_url": "CODEX_DEVICE_URL",
        "codex_client_id": "CODEX_CLIENT_ID",
        "codex_backend_url": "CODEX_BACKEND_URL",
        "codex_model": "CODEX_MODEL",
        "codex_model_map": "CODEX_MODEL_MAP",
        "default_backend": "DEFAULT_BACKEND",
        "force_model": "FORCE_MODEL",
        "deepseek_model_map": "DEEPSEEK_MODEL_MAP",
        "openai_model_map": "OPENAI_MODEL_MAP",
        "custom_model_map": "CUSTOM_MODEL_MAP",
        "deepseek_model_pattern": "DEEPSEEK_MODEL_PATTERN",
        "openai_model_pattern": "OPENAI_MODEL_PATTERN",
        "custom_model_pattern": "CUSTOM_MODEL_PATTERN",
        "reasoning_content_policy": "REASONING_CONTENT_POLICY",
        "inline_image_policy": "INLINE_IMAGE_POLICY",
        "proxy_host": "PROXY_HOST",
        "proxy_port": "PROXY_PORT",
    }
    JSON_KEYS = {"deepseek_model_map", "openai_model_map", "custom_model_map", "codex_model_map"}

    def __init__(self):
        self._data = dict(self.DEFAULTS)
        self._load()
        self._load_env()

    def _load(self):
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE) as f:
                    stored = json.load(f)
                self._data.update(stored)
            except Exception:
                pass

    def _load_env(self):
        for key, env_key in self.ENV_KEYS.items():
            value = os.environ.get(env_key)
            if value in (None, ""):
                continue
            try:
                if key in self.JSON_KEYS:
                    value = json.loads(value)
                elif key == "proxy_port":
                    value = int(value)
            except Exception:
                continue
            self._data[key] = value

    def save(self):
        PROXY_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            json.dump(self._data, f, indent=2)
        os.chmod(CONFIG_FILE, 0o600)

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def update(self, d: dict):
        self._data.update(d)
        self.save()

    def public_dict(self) -> dict:
        """Return config with API keys masked."""
        d = dict(self._data)
        for k in ("deepseek_api_key", "openai_api_key", "custom_api_key"):
            val = d.get(k, "")
            if val and len(val) > 8:
                d[k] = val[:4] + "•" * (len(val) - 8) + val[-4:]
        d["codex_device_configured"] = codex_auth_store.is_configured()
        d["codex_device_email"] = codex_auth_store.public_email()
        d["codex_device_expires_at"] = codex_auth_store.public_expires_at()
        return d

    @property
    def deepseek_api_key(self) -> str: return self._data["deepseek_api_key"]
    @property
    def openai_api_key(self) -> str: return self._data["openai_api_key"]
    @property
    def custom_api_key(self) -> str: return self._data["custom_api_key"]
    @property
    def openai_auth_mode(self) -> str: return self._data["openai_auth_mode"]
    @property
    def deepseek_base_url(self) -> str: return self._data["deepseek_base_url"]
    @property
    def openai_base_url(self) -> str: return self._data["openai_base_url"]
    @property
    def custom_base_url(self) -> str: return self._data["custom_base_url"]
    @property
    def codex_auth_base_url(self) -> str: return self._data["codex_auth_base_url"]
    @property
    def codex_device_url(self) -> str: return self._data["codex_device_url"]
    @property
    def codex_client_id(self) -> str: return self._data["codex_client_id"]
    @property
    def codex_backend_url(self) -> str: return self._data["codex_backend_url"]
    @property
    def codex_model(self) -> str: return self._data["codex_model"]
    @property
    def codex_model_map(self) -> dict: return self._data["codex_model_map"]
    @property
    def default_backend(self) -> str: return self._data["default_backend"]
    @property
    def force_model(self) -> str: return self._data["force_model"]
    @property
    def deepseek_model_map(self) -> dict: return self._data["deepseek_model_map"]
    @property
    def openai_model_map(self) -> dict: return self._data["openai_model_map"]
    @property
    def custom_model_map(self) -> dict: return self._data["custom_model_map"]
    @property
    def deepseek_model_pattern(self) -> str: return self._data["deepseek_model_pattern"]
    @property
    def openai_model_pattern(self) -> str: return self._data["openai_model_pattern"]
    @property
    def custom_model_pattern(self) -> str: return self._data["custom_model_pattern"]
    @property
    def reasoning_content_policy(self) -> str: return self._data["reasoning_content_policy"]
    @property
    def inline_image_policy(self) -> str: return self._data["inline_image_policy"]
    @property
    def proxy_host(self) -> str: return self._data["proxy_host"]
    @property
    def proxy_port(self) -> int: return self._data["proxy_port"]

    def resolve_backend(self, model: str) -> dict:
        """Determine which backend to use and what model name to send."""
        backend = self.default_backend
        try:
            ds_pat = re.compile(self.deepseek_model_pattern, re.IGNORECASE)
            oa_pat = re.compile(self.openai_model_pattern, re.IGNORECASE)
            custom_pat = re.compile(self.custom_model_pattern, re.IGNORECASE) if self.custom_model_pattern else None
        except re.error:
            ds_pat = re.compile(r"deepseek|deep-seek", re.IGNORECASE)
            oa_pat = re.compile(r"^(gpt-|o1|o3|o4|chatgpt)", re.IGNORECASE)
            custom_pat = None

        if ds_pat.search(model):
            backend = "deepseek"
        elif oa_pat.search(model):
            backend = "openai"
        elif custom_pat and custom_pat.search(model):
            backend = "custom"

        if backend == "deepseek":
            base_url = normalize_openai_base_url(self.deepseek_base_url)
            mapped_model = self.force_model or self.deepseek_model_map.get(model, model)
            auth_header = bearer_auth_header(self.deepseek_api_key, "deepseek")
        elif backend == "openai":
            if self.openai_auth_mode == "codex_device":
                base_url = self.codex_backend_url.rstrip("/")
                mapped_model = (
                    self.force_model
                    or self.codex_model_map.get(model)
                    or self.codex_model
                    or "gpt-5.6-sol"
                )
                auth_header = codex_auth_store.authorization_header()
                return {
                    "backend": "codex",
                    "model": mapped_model,
                    "auth_header": auth_header,
                    "base_url": base_url,
                    "account_id": codex_auth_store.account_id(),
                }
            base_url = normalize_openai_base_url(self.openai_base_url)
            mapped_model = self.force_model or self.openai_model_map.get(model, model)
            auth_header = bearer_auth_header(self.openai_api_key, "openai")
        elif backend == "custom":
            base_url = normalize_openai_base_url(self.custom_base_url)
            mapped_model = self.force_model or self.custom_model_map.get(model, model)
            auth_header = bearer_auth_header(self.custom_api_key, "custom")
        else:
            raise ValueError(f"Unsupported backend '{backend}'. Use deepseek, openai, or custom.")

        return {
            "backend": backend,
            "model": mapped_model,
            "auth_header": auth_header,
            "base_url": base_url,
        }


def normalize_openai_base_url(base_url: str) -> str:
    """Return the OpenAI-compatible /v1 base URL without duplicating /v1."""
    cleaned = (base_url or "").rstrip("/")
    if not cleaned:
        return ""
    return cleaned if cleaned.endswith("/v1") else cleaned + "/v1"


def bearer_auth_header(token: str, backend: str) -> str:
    if not token:
        raise ValueError(
            f"No API key configured for backend '{backend}'. "
            f"Set it in the dashboard: http://{config.proxy_host}:{config.proxy_port}/dashboard"
        )
    return f"Bearer {token}"


class CodexAuthStore:
    """Local token cache for OpenAI Codex device-code authentication."""

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            with open(self.path) as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def save(self, data: dict):
        PROXY_DIR.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(data, f, indent=2)
        os.chmod(self.path, 0o600)

    def clear(self):
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def is_configured(self) -> bool:
        data = self.load()
        return bool(data.get("access_token") or data.get("refresh_token"))

    def public_email(self) -> str:
        return str(self.load().get("email") or "")

    def public_expires_at(self) -> str:
        return str(self.load().get("expires_at") or "")

    def account_id(self) -> str:
        data = self.load()
        account_id = data.get("chatgpt_account_id")
        if account_id:
            return str(account_id)
        return _account_id_from_id_token(data.get("id_token", ""))

    def authorization_header(self) -> str:
        data = self.load()
        token = data.get("access_token")
        if token and not self._is_expiring(data):
            return f"Bearer {token}"
        refreshed = self.refresh(data)
        token = refreshed.get("access_token")
        if not token:
            raise ValueError(
                "OpenAI Codex device auth is not configured. "
                f"Open {config.codex_device_url}, enter a code from the dashboard or "
                "run ./setup-codex-device.py, then retry."
            )
        return f"Bearer {token}"

    def _is_expiring(self, data: dict) -> bool:
        expires_at = data.get("expires_at")
        if not expires_at:
            exp = _exp_from_access_token(data.get("access_token", ""))
            if exp is None:
                return False
            return exp <= time.time() + 300
        try:
            if isinstance(expires_at, (int, float)):
                expires_ts = float(expires_at)
            else:
                expires_ts = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00")).timestamp()
            return expires_ts <= time.time() + 300
        except Exception:
            return False

    def refresh(self, data: dict) -> dict:
        refresh_token = data.get("refresh_token")
        if not refresh_token:
            return self._maybe_import_from_cli(data)
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": data.get("client_id") or config.codex_client_id,
        }
        url = f"{config.codex_auth_base_url.rstrip('/')}/oauth/token"
        try:
            resp = httpx.post(
                url,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=20,
                trust_env=False,
            )
            if resp.status_code != 200:
                return self._maybe_import_from_cli(data)
            token_data = resp.json()
            merged = dict(data)
            merged.update(_normalize_codex_token_response(token_data, payload["client_id"]))
            self.save(merged)
            return merged
        except Exception:
            return self._maybe_import_from_cli(data)

    def _maybe_import_from_cli(self, data: dict) -> dict:
        """Fallback: re-import a fresh token from the Codex CLI auth file.

        The Codex CLI and this bridge share one OAuth credential. OpenAI refresh
        tokens are one-time-use, so if the CLI refreshes first it consumes the
        refresh_token and the bridge's own refresh returns 401
        refresh_token_reused. Re-importing from the CLI keeps the bridge working
        without forcing the user to re-run setup-codex-device.py manually.
        """
        if not CODEX_CLI_AUTH_FILE.exists():
            return data
        try:
            with open(CODEX_CLI_AUTH_FILE) as f:
                cli_data = json.load(f)
        except Exception:
            return data
        tokens = cli_data.get("tokens") if isinstance(cli_data, dict) else None
        if not isinstance(tokens, dict) or not tokens.get("access_token"):
            return data
        new_exp = _exp_from_access_token(tokens.get("access_token", ""))
        old_exp = _exp_from_access_token(data.get("access_token", ""))
        if new_exp and old_exp and new_exp <= old_exp:
            return data
        imported = _normalize_codex_token_response(
            {
                "access_token": tokens.get("access_token"),
                "refresh_token": tokens.get("refresh_token"),
                "id_token": tokens.get("id_token"),
                "chatgpt_account_id": tokens.get("account_id"),
            },
            data.get("client_id") or config.codex_client_id,
        )
        if not imported.get("access_token"):
            return data
        merged = dict(data)
        merged.update(imported)
        self.save(merged)
        print("[proxy] re-imported fresh Codex token from ~/.codex/auth.json (refresh_token was reused)", flush=True)
        return merged


def _expires_at_from_response(token_data: dict) -> str:
    expires_at = token_data.get("expires_at")
    if expires_at:
        return str(expires_at)
    expires_in = token_data.get("expires_in")
    if isinstance(expires_in, (int, float)):
        dt = datetime.fromtimestamp(time.time() + float(expires_in), tz=timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")
    exp = _exp_from_access_token(token_data.get("access_token", ""))
    if exp:
        dt = datetime.fromtimestamp(exp, tz=timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")
    return ""


def _normalize_codex_token_response(token_data: dict, client_id: str) -> dict:
    account_id = token_data.get("chatgpt_account_id", "") or _account_id_from_id_token(
        token_data.get("id_token", "")
    )
    normalized = {
        "access_token": token_data.get("access_token", ""),
        "refresh_token": token_data.get("refresh_token", ""),
        "id_token": token_data.get("id_token", ""),
        "token_type": token_data.get("token_type", "Bearer"),
        "client_id": client_id,
        "expires_at": _expires_at_from_response(token_data),
        "email": token_data.get("email", "") or _email_from_id_token(token_data.get("id_token", "")),
        "chatgpt_account_id": account_id,
    }
    return {k: v for k, v in normalized.items() if v}


def _decode_jwt_claims(token: str) -> dict:
    """Best-effort decode of a JWT payload without verifying the signature."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        payload = parts[1]
        padding = "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload + padding)
        data = json.loads(decoded)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _exp_from_access_token(access_token: str) -> Optional[float]:
    """Extract the `exp` claim from a JWT access token, or None."""
    if not access_token:
        return None
    claims = _decode_jwt_claims(access_token)
    exp = claims.get("exp")
    if isinstance(exp, (int, float)):
        return float(exp)
    return None


def _account_id_from_id_token(id_token: str) -> str:
    if not id_token:
        return ""
    claims = _decode_jwt_claims(id_token)
    auth = claims.get("https://api.openai.com/auth")
    if isinstance(auth, dict):
        account_id = auth.get("chatgpt_account_id") or auth.get("chatgpt_account", {})
        if isinstance(account_id, dict):
            account_id = account_id.get("id", "")
        if account_id:
            return str(account_id)
    return str(claims.get("chatgpt_account_id", "") or "")


def _email_from_id_token(id_token: str) -> str:
    if not id_token:
        return ""
    claims = _decode_jwt_claims(id_token)
    return str(claims.get("email", "") or "")


def _new_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


async def _post_codex_auth(path: str, payload: dict) -> httpx.Response:
    url = f"{config.codex_auth_base_url.rstrip('/')}{path}"
    async with httpx.AsyncClient(timeout=20, trust_env=False) as c:
        resp = await c.post(url, json=payload)
        if resp.status_code in {400, 415, 422}:
            return await c.post(
                url,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        return resp


async def _exchange_codex_authorization_code(code: str, code_verifier: str, client_id: str) -> dict:
    url = f"{config.codex_auth_base_url.rstrip('/')}/oauth/token"
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "code_verifier": code_verifier,
    }
    async with httpx.AsyncClient(timeout=20, trust_env=False) as c:
        resp = await c.post(
            url,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if resp.status_code != 200:
        raise ValueError(f"OAuth token exchange failed: HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json()


async def _complete_codex_device_token(data: dict, code_verifier: str, client_id: str) -> dict:
    if data.get("access_token"):
        return data
    authorization_code = data.get("authorization_code") or data.get("code")
    if authorization_code:
        return await _exchange_codex_authorization_code(authorization_code, code_verifier, client_id)
    return data


codex_auth_store = CodexAuthStore(CODEX_AUTH_FILE)


# Global config
config = Config()

# ---------------------------------------------------------------------------
# Request log (in-memory ring buffer)
# ---------------------------------------------------------------------------
MAX_LOG_ENTRIES = 200
request_log: list[dict] = []


def log_request(backend: str, model: str, stream: bool, status: str):
    entry = {
        "time": datetime.now().strftime("%H:%M:%S"),
        "backend": backend,
        "model": model,
        "stream": stream,
        "status": status,
    }
    request_log.append(entry)
    if len(request_log) > MAX_LOG_ENTRIES:
        request_log.pop(0)


def log_local_event(request: Request, status_code: int):
    path = request.url.path
    if path.startswith("/static") or path in {"/dashboard", "/favicon.ico"}:
        return
    host = request.headers.get("host", "")
    entry = {
        "time": datetime.now().strftime("%H:%M:%S"),
        "backend": "local",
        "model": f"{request.method} {host}{path}",
        "stream": False,
        "status": str(status_code),
    }
    request_log.append(entry)
    if len(request_log) > MAX_LOG_ENTRIES:
        request_log.pop(0)
    print(f"[proxy] <- {request.method} host={host} path={path} status={status_code}")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="Claude Science BYOK Proxy", version="2.0.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Path normalization middleware
class NormalizePathMiddleware(BaseHTTPMiddleware):
    PASSTHROUGH = {"/health", "/dashboard", "/docs", "/openapi.json", "/favicon.ico"}

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # Skip static files and dashboard
        if path.startswith("/static") or path in self.PASSTHROUGH or path.startswith("/api"):
            return await call_next(request)

        while "/v1/v1/" in path:
            path = path.replace("/v1/v1/", "/v1/", 1)
        if not path.startswith("/v1/") and path not in self.PASSTHROUGH and not path.startswith("/docs"):
            path = "/v1" + path

        request.scope["path"] = path
        request.scope["raw_path"] = path.encode()
        return await call_next(request)


app.add_middleware(NormalizePathMiddleware)


@app.middleware("http")
async def access_log_middleware(request: Request, call_next):
    response = await call_next(request)
    log_local_event(request, response.status_code)
    return response

# Static files for dashboard
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Shared HTTP client
_client: Optional[httpx.AsyncClient] = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0),
            limits=httpx.Limits(max_keepalive_connections=20),
            trust_env=True,
        )
    return _client


# ---------------------------------------------------------------------------
# Request/Response translation: Anthropic <-> OpenAI
# ---------------------------------------------------------------------------

TOOL_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")
JSON_SCHEMA_TYPES = {"string", "number", "integer", "boolean", "object", "array"}
SCHEMA_COMBINATORS = ("anyOf", "oneOf", "allOf")


def normalize_tool_name(name, fallback: str) -> str:
    """OpenAI-compatible function names are alphanumeric plus _ and -."""
    cleaned = TOOL_NAME_RE.sub("_", str(name or fallback)).strip("_")
    return (cleaned or fallback)[:64]


def _pick_schema_type(value):
    if isinstance(value, str) and value in JSON_SCHEMA_TYPES:
        return value
    if isinstance(value, list):
        candidates = [v for v in value if isinstance(v, str) and v in JSON_SCHEMA_TYPES]
        if "object" in candidates:
            return "object"
        if "array" in candidates:
            return "array"
        if candidates:
            return candidates[0]
    return None


def _infer_schema_type(schema: dict):
    if "properties" in schema:
        return "object"
    if "items" in schema:
        return "array"
    enum_values = schema.get("enum")
    if isinstance(enum_values, list):
        for value in enum_values:
            if value is None:
                continue
            if isinstance(value, bool):
                return "boolean"
            if isinstance(value, int):
                return "integer"
            if isinstance(value, float):
                return "number"
            if isinstance(value, str):
                return "string"
    return None


def sanitize_tool_schema(schema, *, force_object: bool = False) -> dict:
    """Normalize Claude tool schemas for OpenAI-compatible providers.

    Claude Science can send tool schemas with a missing or null root type.
    DeepSeek rejects those for function parameters, so the root must always be
    an object schema. Nested schemas are kept permissive but never keep
    `type: null`.
    """
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}} if force_object else {}

    cleaned = {}
    schema_type = _pick_schema_type(schema.get("type")) or _infer_schema_type(schema)
    if force_object:
        schema_type = "object"
    if schema_type:
        cleaned["type"] = schema_type

    for key, value in schema.items():
        if key == "type" or value is None:
            continue
        if key == "properties":
            if isinstance(value, dict):
                cleaned["properties"] = {
                    str(prop_name): sanitize_tool_schema(prop_schema)
                    for prop_name, prop_schema in value.items()
                }
            continue
        if key == "items":
            if isinstance(value, dict):
                cleaned["items"] = sanitize_tool_schema(value)
            elif isinstance(value, list):
                cleaned["items"] = [sanitize_tool_schema(item) for item in value if isinstance(item, dict)]
            continue
        if key in SCHEMA_COMBINATORS:
            if isinstance(value, list):
                variants = [sanitize_tool_schema(item) for item in value if isinstance(item, dict)]
                if variants:
                    cleaned[key] = variants
            continue
        if key == "required":
            if isinstance(value, list):
                required = [item for item in value if isinstance(item, str)]
                if required:
                    cleaned["required"] = required
            continue
        if key == "enum":
            if isinstance(value, list):
                enum_values = [item for item in value if item is not None]
                if enum_values:
                    cleaned["enum"] = enum_values
            continue
        if key == "additionalProperties":
            if isinstance(value, bool):
                cleaned[key] = value
            elif isinstance(value, dict):
                cleaned[key] = sanitize_tool_schema(value)
            continue
        if key in {
            "description", "title", "format", "pattern", "minimum", "maximum",
            "exclusiveMinimum", "exclusiveMaximum", "minLength", "maxLength",
            "minItems", "maxItems", "default", "const",
        }:
            cleaned[key] = value

    if force_object:
        cleaned["type"] = "object"
        if not isinstance(cleaned.get("properties"), dict):
            cleaned["properties"] = {}
    return cleaned


# ---------------------------------------------------------------------------
# Mutual-exclusivity enforcement for tool schemas
#
# The Codex model tends to pass every optional parameter it sees, including
# groups that are mutually exclusive (e.g. a read tool's offset/limit for text
# vs pages for PDF), which the downstream tool validator rejects with
# "pages and offset/limit are mutually exclusive". Pure instruction text does
# not reliably stop this. Restructuring the schema with `oneOf` DOES: the Codex
# backend accepts oneOf and the model picks exactly one variant.
#
# Each rule lists groups of param-name patterns. When a schema's properties
# match two or more groups in a rule, the matched (exclusive) props are split
# into oneOf variants; the remaining (common) props stay at the root and are
# shared by every variant. Extend MUTUAL_EXCLUSIVITY_RULES for other tools.
# ---------------------------------------------------------------------------
MUTUAL_EXCLUSIVITY_RULES = [
    {
        "groups": [
            {"patterns": [r"^(offset|limit|start_line|end_line|line_start|line_end|from_line|to_line|start|end)$"],
             "label": "text/line mode"},
            {"patterns": [r"^(pages|page|page_range|page_numbers|page_num|page_count)$"],
             "label": "pdf/page mode"},
        ],
    },
]


def _match_exclusivity_group(prop_name: str, group: dict) -> bool:
    for pat in group["patterns"]:
        if re.match(pat, prop_name, re.IGNORECASE):
            return True
    return False


def apply_mutual_exclusivity(schema: dict) -> dict:
    """Restructure a tool schema to express mutually-exclusive param groups via oneOf.

    If the schema's properties match two or more exclusive groups (per
    MUTUAL_EXCLUSIVITY_RULES), the exclusive props are moved into oneOf variants
    and the common props remain at the root. Idempotent: schemas that already
    carry oneOf/anyOf, or that match fewer than two groups, are returned as-is.
    """
    if not isinstance(schema, dict):
        return schema
    if "oneOf" in schema or "anyOf" in schema:
        return schema
    props = schema.get("properties")
    if not isinstance(props, dict) or not props:
        return schema

    for rule in MUTUAL_EXCLUSIVITY_RULES:
        groups = rule["groups"]
        matched = []  # list of (group, [prop_names])
        for g in groups:
            hits = [name for name in props if _match_exclusivity_group(name, g)]
            if hits:
                matched.append((g, hits))
        if len(matched) < 2:
            continue

        exclusive_names: set = set()
        variants = []
        for g, hits in matched:
            exclusive_names.update(hits)
            variants.append({
                "type": "object",
                "description": f"{g['label']}. Mutually exclusive with the other mode(s); choose exactly one and omit the others.",
                "properties": {name: props[name] for name in hits},
            })

        common_props = {name: p for name, p in props.items() if name not in exclusive_names}
        new_schema = dict(schema)
        new_schema["properties"] = common_props
        req = schema.get("required")
        if isinstance(req, list):
            new_schema["required"] = [r for r in req if r not in exclusive_names]
        desc = schema.get("description", "")
        note = "Parameters are split into mutually-exclusive modes via oneOf; select exactly one mode."
        new_schema["description"] = f"{desc} {note}".strip() if desc else note
        new_schema["oneOf"] = variants
        return new_schema

    return schema


def _is_inline_image_url(url: str) -> bool:
    return isinstance(url, str) and url.startswith("data:")


def _openai_image_url_from_anthropic(block: dict) -> Optional[str]:
    if "image_url" in block:
        image_url = block["image_url"]
        if isinstance(image_url, dict):
            return image_url.get("url")
        if isinstance(image_url, str):
            return image_url
    if "source" in block:
        src = block["source"]
        if isinstance(src, dict):
            mt = src.get("media_type", "image/png")
            d = src.get("data", "")
            if d:
                return f"data:{mt};base64,{d}"
    return None


def _image_policy_for_backend(backend_name: str, backend_base_url: str) -> str:
    policy = (config.inline_image_policy or "auto").lower()
    if policy in {"preserve", "omit", "omit_inline"}:
        return policy
    if backend_name == "deepseek":
        return "omit"
    if backend_name == "custom" and "siliconflow" in (backend_base_url or "").lower():
        return "omit_inline"
    return "preserve"


def anthropic_to_openai(
    anthropic_body: dict,
    backend_model: str,
    backend_name: str = "",
    backend_base_url: str = "",
) -> dict:
    """Convert Anthropic Messages API request → OpenAI Chat Completions format."""
    openai_messages = []
    backend_name = backend_name.lower()
    image_policy = _image_policy_for_backend(backend_name, backend_base_url)

    # System prompt
    system = anthropic_body.get("system")
    if system:
        if isinstance(system, str):
            openai_messages.append({"role": "system", "content": system})
        elif isinstance(system, list):
            parts = [b["text"] for b in system if isinstance(b, dict) and b.get("type") == "text"]
            if parts:
                openai_messages.append({"role": "system", "content": "\n".join(parts)})

    # Messages
    for msg in anthropic_body.get("messages", []):
        role = msg.get("role", "user")
        content = msg.get("content")

        if role == "user":
            tool_messages = []
            if isinstance(content, str):
                openai_content = content
            elif isinstance(content, list):
                text_parts, image_parts, omitted_images = [], [], 0
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    t = block.get("type", "")
                    if t == "tool_result":
                        tool_content = block.get("content", "")
                        if isinstance(tool_content, list):
                            result_parts = []
                            for item in tool_content:
                                if isinstance(item, dict) and item.get("type") == "text":
                                    result_parts.append(item.get("text", ""))
                                elif isinstance(item, str):
                                    result_parts.append(item)
                                else:
                                    result_parts.append(json.dumps(item, ensure_ascii=False))
                            tool_content = "\n".join(part for part in result_parts if part)
                        elif not isinstance(tool_content, str):
                            tool_content = json.dumps(tool_content, ensure_ascii=False)
                        tool_messages.append({
                            "role": "tool",
                            "tool_call_id": block.get("tool_use_id", ""),
                            "content": tool_content,
                        })
                    elif t == "text":
                        text_parts.append(block["text"])
                    elif t in ("image", "image_url"):
                        url = _openai_image_url_from_anthropic(block)
                        if not url:
                            omitted_images += 1
                            continue
                        if image_policy == "omit" or (image_policy == "omit_inline" and _is_inline_image_url(url)):
                            omitted_images += 1
                        else:
                            image_parts.append({"type": "image_url", "image_url": {"url": url}})
                if image_parts:
                    openai_parts = list(image_parts)
                    if text_parts:
                        openai_parts.insert(0, {"type": "text", "text": " ".join(text_parts)})
                    if omitted_images:
                        openai_parts.append({
                            "type": "text",
                            "text": f"[{omitted_images} inline image attachment(s) omitted for backend compatibility.]",
                        })
                    openai_content = openai_parts
                elif omitted_images:
                    image_note = f"[{omitted_images} inline image attachment(s) omitted for backend compatibility.]"
                    openai_content = " ".join([*text_parts, image_note]).strip()
                else:
                    openai_content = " ".join(text_parts)
            else:
                openai_content = str(content)

            openai_messages.extend(tool_messages)
            if openai_content:
                openai_messages.append({"role": "user", "content": openai_content})

        elif role == "assistant":
            if isinstance(content, str):
                openai_messages.append({"role": "assistant", "content": content})
            elif isinstance(content, list):
                text_parts, tool_calls = [], []
                for block in content:
                    if block.get("type") == "text":
                        text_parts.append(block["text"])
                    elif block.get("type") == "tool_use":
                        tool_calls.append({
                            "id": block.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": block.get("name", ""),
                                "arguments": json.dumps(block.get("input", {})),
                            },
                        })
                am = {"role": "assistant"}
                am["content"] = " ".join(text_parts) if text_parts else None
                if tool_calls:
                    am["tool_calls"] = tool_calls
                openai_messages.append(am)
            else:
                openai_messages.append({"role": "assistant", "content": str(content)})

    openai_body = {"model": backend_model, "messages": openai_messages}

    max_tokens = anthropic_body.get("max_tokens", 4096)
    openai_body["max_tokens"] = max_tokens

    if "temperature" in anthropic_body:
        openai_body["temperature"] = anthropic_body["temperature"]
    if "top_p" in anthropic_body:
        openai_body["top_p"] = anthropic_body["top_p"]

    stop_seq = anthropic_body.get("stop_sequences")
    if stop_seq:
        if isinstance(stop_seq, list) and len(stop_seq) == 1:
            openai_body["stop"] = stop_seq[0]
        elif isinstance(stop_seq, list):
            openai_body["stop"] = stop_seq

    openai_body["stream"] = anthropic_body.get("stream", False)

    # Tools
    tools = anthropic_body.get("tools")
    if tools:
        openai_tools = []
        tool_name_map = {}
        for idx, tool in enumerate(tools):
            if isinstance(tool, dict):
                original_name = str(tool.get("name", "") or f"tool_{idx}")
                safe_name = normalize_tool_name(original_name, f"tool_{idx}")
                tool_name_map[original_name] = safe_name
                parameters = sanitize_tool_schema(tool.get("input_schema", {}), force_object=True)
                openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": safe_name,
                        "description": tool.get("description", ""),
                        "parameters": parameters,
                    },
                })
        if openai_tools:
            openai_body["tools"] = openai_tools
            tool_choice = anthropic_body.get("tool_choice")
            if tool_choice and backend_name != "deepseek":
                if isinstance(tool_choice, dict) and tool_choice.get("type") == "tool":
                    choice_name = str(tool_choice.get("name", ""))
                    openai_body["tool_choice"] = {
                        "type": "function",
                        "function": {"name": tool_name_map.get(choice_name, normalize_tool_name(choice_name, "tool_0"))},
                    }
                elif tool_choice == "any":
                    openai_body["tool_choice"] = "required"
                elif tool_choice == "auto":
                    openai_body["tool_choice"] = "auto"

    return openai_body


def openai_to_anthropic_response(openai_resp: dict, original_model: str, request_id: str) -> dict:
    choice = openai_resp.get("choices", [{}])[0]
    message = choice.get("message", {})
    content_blocks = []

    normal_content = message.get("content", "") or ""
    reasoning_content = message.get("reasoning_content", "") or ""
    policy = config.reasoning_content_policy
    if policy == "always" and reasoning_content:
        text_content = reasoning_content + (f"\n\n{normal_content}" if normal_content else "")
    elif policy == "fallback":
        text_content = normal_content or reasoning_content
    else:
        text_content = normal_content
    if text_content:
        content_blocks.append({"type": "text", "text": text_content})

    for tc in message.get("tool_calls") or []:
        func = tc.get("function", {})
        try:
            arguments = json.loads(func.get("arguments", "{}"))
        except json.JSONDecodeError:
            arguments = {"_raw": func.get("arguments", "{}")}
        content_blocks.append({
            "type": "tool_use",
            "id": tc.get("id", f"toolu_{uuid.uuid4().hex[:12]}"),
            "name": func.get("name", ""),
            "input": arguments,
        })

    usage = openai_resp.get("usage", {})
    return {
        "id": request_id,
        "type": "message",
        "role": "assistant",
        "content": content_blocks,
        "model": original_model,
        "stop_reason": _map_finish_reason(choice.get("finish_reason", "stop")),
        "stop_sequence": None,
        "usage": {"input_tokens": usage.get("prompt_tokens", 0), "output_tokens": usage.get("completion_tokens", 0)},
    }


def _map_finish_reason(r: str) -> str:
    m = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use", "function_call": "tool_use", "content_filter": "end_turn"}
    return m.get(r, "end_turn")


# ---------------------------------------------------------------------------
# Streaming translation
# ---------------------------------------------------------------------------

async def translate_stream(openai_stream, original_model: str, request_id: str):
    tool_calls_map: dict[int, dict] = {}
    finish_reason = None
    output_tokens = 0
    message_started = False
    content_block_started = False

    def ev(t: str, d: dict) -> str:
        return f"event: {t}\ndata: {json.dumps(d)}\n\n"

    async for line in openai_stream.aiter_lines():
        if not line or not line.startswith("data: "):
            continue
        payload = line[6:]
        if payload.strip() == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue

        usage = chunk.get("usage") or {}
        if usage:
            output_tokens = usage.get("completion_tokens", output_tokens)

        choices = chunk.get("choices", [])
        if not choices:
            continue

        choice = choices[0]
        delta = choice.get("delta", {})
        finish_reason = choice.get("finish_reason") or finish_reason

        if not message_started:
            message_started = True
            yield ev("message_start", {
                "type": "message_start",
                "message": {
                    "id": request_id, "type": "message", "role": "assistant",
                    "content": [], "model": original_model,
                    "stop_reason": None, "stop_sequence": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            })

        text_delta = delta.get("content", "") or ""
        if not text_delta and config.reasoning_content_policy != "never":
            text_delta = delta.get("reasoning_content", "") or ""
        if text_delta:
            if not content_block_started:
                content_block_started = True
                yield ev("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
            yield ev("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text_delta}})

        for tc_delta in delta.get("tool_calls") or []:
            idx = tc_delta.get("index", 0)
            func_delta = tc_delta.get("function", {})
            if idx not in tool_calls_map:
                tool_calls_map[idx] = {"id": tc_delta.get("id", ""), "name": func_delta.get("name", ""), "arguments": ""}
                yield ev("content_block_start", {
                    "type": "content_block_start", "index": idx + 1,
                    "content_block": {"type": "tool_use", "id": tool_calls_map[idx]["id"], "name": tool_calls_map[idx]["name"], "input": {}},
                })
            if func_delta.get("name"):
                tool_calls_map[idx]["name"] = func_delta["name"]
            if tc_delta.get("id"):
                tool_calls_map[idx]["id"] = tc_delta["id"]
            if func_delta.get("arguments"):
                tool_calls_map[idx]["arguments"] += func_delta["arguments"]
                yield ev("content_block_delta", {"type": "content_block_delta", "index": idx + 1, "delta": {"type": "input_json_delta", "partial_json": func_delta["arguments"]}})

        if finish_reason:
            if content_block_started:
                yield ev("content_block_stop", {"type": "content_block_stop", "index": 0})
            for idx in sorted(tool_calls_map.keys()):
                yield ev("content_block_delta", {"type": "content_block_delta", "index": idx + 1, "delta": {"type": "input_json_delta", "partial_json": ""}})
                yield ev("content_block_stop", {"type": "content_block_stop", "index": idx + 1})
            yield ev("message_delta", {"type": "message_delta", "delta": {"stop_reason": _map_finish_reason(finish_reason), "stop_sequence": None}, "usage": {"output_tokens": output_tokens}})
            yield ev("message_stop", {"type": "message_stop"})
            break

    if message_started and not finish_reason:
        if content_block_started:
            yield ev("content_block_stop", {"type": "content_block_stop", "index": 0})
        yield ev("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": output_tokens}})
        yield ev("message_stop", {"type": "message_stop"})


# ---------------------------------------------------------------------------
# ChatGPT Codex backend (Responses API) translation
#
# ChatGPT Plus/Pro OAuth tokens do not work against the standard
# api.openai.com Chat Completions endpoint. They only work against the ChatGPT
# Codex backend, which speaks the OpenAI Responses API. These helpers translate
# Anthropic Messages <-> Responses API so Claude Science can spend the ChatGPT
# subscription's Codex quota.
# ---------------------------------------------------------------------------

def _anthropic_system_to_instructions(system) -> str:
    if not system:
        return ""
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        parts = [b.get("text", "") for b in system if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(p for p in parts if p)
    return str(system)


# Injected into Codex `instructions` whenever tools are present. The Codex model
# otherwise tends to pass mutually-exclusive parameters together (e.g. a read
# tool's offset/limit for text AND pages for PDF), which the downstream tool
# validator rejects. This guidance is streaming-safe (it shapes generation, so
# no post-processing of the SSE stream is needed).
TOOL_USAGE_GUIDANCE = (
    "[Tool call parameter rules — mandatory]\n"
    "When calling a tool, include ONLY the parameters that apply to the specific input. "
    "Never combine parameters that serve mutually-exclusive purposes in a single call.\n"
    "Specifically, for file/read tools:\n"
    "- `offset` and `limit` apply to TEXT files (line-based reading).\n"
    "- `pages` applies to PDF files (page-based reading).\n"
    "- These two groups are MUTUALLY EXCLUSIVE: choose ONE group based on the file extension "
    "(`.pdf` => use `pages`; other text extensions => use `offset`/`limit`) and OMIT the other group entirely. "
    "Never emit both groups, and never emit empty `{}` for a required parameter."
)


# __CODEX_TRANSLATION_ANCHOR__


def anthropic_to_codex_responses(anthropic_body: dict, backend_model: str) -> dict:
    """Convert an Anthropic Messages request into an OpenAI Responses request."""
    input_items: list[dict] = []

    for msg in anthropic_body.get("messages", []):
        role = msg.get("role", "user")
        content = msg.get("content")

        if role == "user":
            text_parts, image_parts, tool_outputs = [], [], []
            if isinstance(content, str):
                if content:
                    text_parts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    t = block.get("type", "")
                    if t == "text":
                        text_parts.append(block.get("text", ""))
                    elif t == "tool_result":
                        tool_content = block.get("content", "")
                        if isinstance(tool_content, list):
                            collected = []
                            for item in tool_content:
                                if isinstance(item, dict) and item.get("type") == "text":
                                    collected.append(item.get("text", ""))
                                elif isinstance(item, str):
                                    collected.append(item)
                                else:
                                    collected.append(json.dumps(item, ensure_ascii=False))
                            tool_content = "\n".join(p for p in collected if p)
                        elif not isinstance(tool_content, str):
                            tool_content = json.dumps(tool_content, ensure_ascii=False)
                        tool_outputs.append({
                            "type": "function_call_output",
                            "call_id": block.get("tool_use_id", ""),
                            "output": tool_content,
                        })
                    elif t in ("image", "image_url"):
                        url = _openai_image_url_from_anthropic(block)
                        if url:
                            image_parts.append({"type": "input_image", "image_url": url})
            else:
                text_parts.append(str(content))

            input_items.extend(tool_outputs)
            message_content = []
            if text_parts:
                message_content.append({"type": "input_text", "text": "\n".join(p for p in text_parts if p)})
            message_content.extend(image_parts)
            if message_content:
                input_items.append({"type": "message", "role": "user", "content": message_content})

        elif role == "assistant":
            text_parts, tool_calls = [], []
            if isinstance(content, str):
                if content:
                    text_parts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        tool_calls.append({
                            "type": "function_call",
                            "call_id": block.get("id", ""),
                            "name": block.get("name", ""),
                            "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                        })
            else:
                text_parts.append(str(content))

            if text_parts:
                joined = "\n".join(p for p in text_parts if p)
                if joined:
                    input_items.append({
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": joined}],
                    })
            input_items.extend(tool_calls)

    responses_body: dict = {
        "model": backend_model,
        "input": input_items,
        "stream": bool(anthropic_body.get("stream", False)),
        "store": False,
    }

    instructions = _anthropic_system_to_instructions(anthropic_body.get("system"))
    if anthropic_body.get("tools"):
        instructions = (instructions + "\n\n" + TOOL_USAGE_GUIDANCE) if instructions else TOOL_USAGE_GUIDANCE
    if instructions:
        responses_body["instructions"] = instructions

    # NOTE: The ChatGPT-account Codex backend rejects sampling/limit params
    # (temperature, top_p, max_output_tokens) with HTTP 400 "Unsupported
    # parameter". They are intentionally omitted here.

    tools = anthropic_body.get("tools")
    if tools:
        responses_tools = []
        tool_name_map = {}
        for idx, tool in enumerate(tools):
            if not isinstance(tool, dict):
                continue
            original_name = str(tool.get("name", "") or f"tool_{idx}")
            safe_name = normalize_tool_name(original_name, f"tool_{idx}")
            tool_name_map[original_name] = safe_name
            responses_tools.append({
                "type": "function",
                "name": safe_name,
                "description": tool.get("description", ""),
                "parameters": apply_mutual_exclusivity(sanitize_tool_schema(tool.get("input_schema", {}), force_object=True)),
            })
        if responses_tools:
            responses_body["tools"] = responses_tools
            tool_choice = anthropic_body.get("tool_choice")
            if isinstance(tool_choice, dict) and tool_choice.get("type") == "tool":
                choice_name = str(tool_choice.get("name", ""))
                responses_body["tool_choice"] = {
                    "type": "function",
                    "name": tool_name_map.get(choice_name, normalize_tool_name(choice_name, "tool_0")),
                }
            elif tool_choice == "any":
                responses_body["tool_choice"] = "required"
            elif tool_choice == "auto":
                responses_body["tool_choice"] = "auto"

    return responses_body


# __CODEX_RESPONSE_ANCHOR__


def codex_responses_to_anthropic(resp: dict, original_model: str, request_id: str) -> dict:
    """Convert a non-streaming Responses API result into an Anthropic message."""
    content_blocks = []
    output = resp.get("output")
    if not isinstance(output, list):
        output = []

    for item in output:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "message":
            for part in item.get("content", []) or []:
                if isinstance(part, dict) and part.get("type") in ("output_text", "text"):
                    text = part.get("text", "")
                    if text:
                        content_blocks.append({"type": "text", "text": text})
        elif item_type == "function_call":
            try:
                arguments = json.loads(item.get("arguments", "{}") or "{}")
            except json.JSONDecodeError:
                arguments = {"_raw": item.get("arguments", "")}
            content_blocks.append({
                "type": "tool_use",
                "id": item.get("call_id") or item.get("id") or f"toolu_{uuid.uuid4().hex[:12]}",
                "name": item.get("name", ""),
                "input": arguments,
            })

    if not content_blocks:
        text = resp.get("output_text", "")
        if text:
            content_blocks.append({"type": "text", "text": text})

    usage = resp.get("usage", {}) or {}
    stop_reason = "tool_use" if any(b["type"] == "tool_use" for b in content_blocks) else "end_turn"
    if resp.get("status") == "incomplete" and (resp.get("incomplete_details") or {}).get("reason") == "max_output_tokens":
        stop_reason = "max_tokens"
    return {
        "id": request_id,
        "type": "message",
        "role": "assistant",
        "content": content_blocks,
        "model": original_model,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
        },
    }


# __CODEX_STREAM_ANCHOR__


async def translate_codex_stream(codex_stream, original_model: str, request_id: str):
    """Convert an OpenAI Responses SSE stream into Anthropic SSE events."""
    def ev(t: str, d: dict) -> str:
        return f"event: {t}\ndata: {json.dumps(d)}\n\n"

    message_started = False
    text_block_open = False
    text_index: Optional[int] = None
    tool_blocks: dict[str, dict] = {}
    next_index = 0
    output_tokens = 0
    input_tokens = 0
    stop_reason = "end_turn"
    saw_tool_use = False

    def ensure_message_start():
        nonlocal message_started
        if message_started:
            return None
        message_started = True
        return ev("message_start", {
            "type": "message_start",
            "message": {
                "id": request_id, "type": "message", "role": "assistant",
                "content": [], "model": original_model,
                "stop_reason": None, "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        })

    async for line in codex_stream.aiter_lines():
        if not line or not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue

        etype = event.get("type", "")

        if etype in ("response.created", "response.in_progress"):
            start = ensure_message_start()
            if start:
                yield start

        elif etype == "response.output_text.delta":
            start = ensure_message_start()
            if start:
                yield start
            if not text_block_open:
                text_block_open = True
                text_index = next_index
                next_index += 1
                yield ev("content_block_start", {"type": "content_block_start", "index": text_index, "content_block": {"type": "text", "text": ""}})
            delta = event.get("delta", "")
            if delta:
                yield ev("content_block_delta", {"type": "content_block_delta", "index": text_index, "delta": {"type": "text_delta", "text": delta}})

        elif etype == "response.output_text.done":
            if text_block_open:
                text_block_open = False
                yield ev("content_block_stop", {"type": "content_block_stop", "index": text_index})

        elif etype == "response.output_item.added":
            item = event.get("item", {}) or {}
            if item.get("type") == "function_call":
                start = ensure_message_start()
                if start:
                    yield start
                if text_block_open:
                    text_block_open = False
                    yield ev("content_block_stop", {"type": "content_block_stop", "index": text_index})
                saw_tool_use = True
                idx = next_index
                next_index += 1
                call_id = item.get("call_id") or item.get("id") or f"toolu_{uuid.uuid4().hex[:12]}"
                tool_blocks[item.get("id") or call_id] = {"index": idx}
                yield ev("content_block_start", {
                    "type": "content_block_start", "index": idx,
                    "content_block": {"type": "tool_use", "id": call_id, "name": item.get("name", ""), "input": {}},
                })

        elif etype == "response.function_call_arguments.delta":
            item_id = event.get("item_id", "")
            block = tool_blocks.get(item_id)
            if block is not None:
                delta = event.get("delta", "")
                if delta:
                    yield ev("content_block_delta", {"type": "content_block_delta", "index": block["index"], "delta": {"type": "input_json_delta", "partial_json": delta}})

        elif etype == "response.output_item.done":
            item = event.get("item", {}) or {}
            if item.get("type") == "function_call":
                block = tool_blocks.get(item.get("id", "")) or tool_blocks.get(item.get("call_id", ""))
                if block is not None:
                    yield ev("content_block_stop", {"type": "content_block_stop", "index": block["index"]})

        elif etype in ("response.completed", "response.incomplete"):
            resp_obj = event.get("response", {}) or {}
            usage = resp_obj.get("usage", {}) or {}
            output_tokens = usage.get("output_tokens", output_tokens)
            input_tokens = usage.get("input_tokens", input_tokens)
            if etype == "response.incomplete" and (resp_obj.get("incomplete_details") or {}).get("reason") == "max_output_tokens":
                stop_reason = "max_tokens"
            elif saw_tool_use:
                stop_reason = "tool_use"

        elif etype in ("response.failed", "error"):
            err = event.get("response", {}).get("error") or event.get("error") or {}
            msg = err.get("message", "Codex backend error") if isinstance(err, dict) else str(err)
            safe_msg = msg.encode("ascii", errors="replace").decode("ascii")
            yield ev("error", {"type": "error", "error": {"type": "api_error", "message": safe_msg}})
            return

    if not message_started:
        yield ev("message_start", {
            "type": "message_start",
            "message": {
                "id": request_id, "type": "message", "role": "assistant",
                "content": [], "model": original_model,
                "stop_reason": None, "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        })
    if text_block_open:
        yield ev("content_block_stop", {"type": "content_block_stop", "index": text_index})
    yield ev("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None}, "usage": {"output_tokens": output_tokens}})
    yield ev("message_stop", {"type": "message_stop"})


# ---------------------------------------------------------------------------
# Anthropic API routes
# ---------------------------------------------------------------------------

def _codex_headers(backend: dict) -> dict:
    headers = {
        "Authorization": backend["auth_header"],
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "OpenAI-Beta": "responses=experimental",
        "originator": "codex_cli_rs",
        "User-Agent": "codex_cli_rs",
    }
    account_id = backend.get("account_id")
    if account_id:
        headers["chatgpt-account-id"] = account_id
    return headers


async def _handle_codex_messages(body: dict, backend: dict, original_model: str, request_id: str, stream: bool):
    """Route a request to the ChatGPT Codex backend (Responses API)."""
    responses_body = anthropic_to_codex_responses(body, backend["model"])
    responses_body["stream"] = True  # Codex backend only reliably supports streaming
    headers = _codex_headers(backend)
    client = get_client()
    url = f"{backend['base_url'].rstrip('/')}/responses"

    print(f"[proxy] → codex | model={backend['model']} | stream={stream} | original_model={original_model}")

    if stream:
        async def stream_gen():
            try:
                async with client.stream("POST", url, json=responses_body, headers=headers) as backend_resp:
                    if backend_resp.status_code != 200:
                        try:
                            error_text = (await backend_resp.aread()).decode("utf-8", errors="replace")[:500]
                        except Exception:
                            error_text = "(unreadable response)"
                        print(f"[proxy] codex error {backend_resp.status_code}: {error_text}", flush=True)
                        log_request("codex", backend["model"], True, f"error {backend_resp.status_code}")
                        err_msg = f"Codex backend error {backend_resp.status_code}: {error_text}"
                        safe_msg = err_msg.encode("ascii", errors="replace").decode("ascii")
                        yield f"event: error\ndata: {json.dumps({'type':'error','error':{'type':'api_error','message':safe_msg}})}\n\n"
                        return
                    log_request("codex", backend["model"], True, "success")
                    async for event in translate_codex_stream(backend_resp, original_model, request_id):
                        yield event
            except Exception as e:
                log_request("codex", backend["model"], True, "error")
                safe_msg = str(e).encode("ascii", errors="replace").decode("ascii")
                yield f"event: error\ndata: {json.dumps({'type':'error','error':{'type':'api_error','message':safe_msg}})}\n\n"

        return StreamingResponse(stream_gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})

    # Non-streaming: consume the Codex SSE stream and assemble a final message.
    try:
        aggregated = {"output": [], "usage": {}, "status": "completed"}
        text_acc = ""
        tool_items: dict[str, dict] = {}
        async with client.stream("POST", url, json=responses_body, headers=headers) as backend_resp:
            if backend_resp.status_code != 200:
                err_text = (await backend_resp.aread()).decode("utf-8", errors="replace")[:500]
                print(f"[proxy] codex error {backend_resp.status_code}: {err_text}", flush=True)
                log_request("codex", backend["model"], False, f"error {backend_resp.status_code}")
                safe_msg = f"Codex backend returned {backend_resp.status_code}: {err_text}".encode("ascii", errors="replace").decode("ascii")
                return JSONResponse({"type": "error", "error": {"type": "api_error", "message": safe_msg}}, status_code=backend_resp.status_code)
            async for line in backend_resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                etype = event.get("type", "")
                if etype == "response.output_text.delta":
                    text_acc += event.get("delta", "")
                elif etype == "response.output_item.done":
                    item = event.get("item", {}) or {}
                    if item.get("type") == "function_call":
                        tool_items[item.get("id") or item.get("call_id", "")] = item
                elif etype in ("response.completed", "response.incomplete"):
                    resp_obj = event.get("response", {}) or {}
                    aggregated["usage"] = resp_obj.get("usage", {}) or {}
                    aggregated["status"] = resp_obj.get("status", "completed")
                    aggregated["incomplete_details"] = resp_obj.get("incomplete_details", {})
                elif etype in ("response.failed", "error"):
                    err = event.get("response", {}).get("error") or event.get("error") or {}
                    msg = err.get("message", "Codex backend error") if isinstance(err, dict) else str(err)
                    log_request("codex", backend["model"], False, "error")
                    safe_msg = msg.encode("ascii", errors="replace").decode("ascii")
                    return JSONResponse({"type": "error", "error": {"type": "api_error", "message": safe_msg}}, status_code=502)

        if text_acc:
            aggregated["output"].append({"type": "message", "content": [{"type": "output_text", "text": text_acc}]})
        for item in tool_items.values():
            aggregated["output"].append(item)
        log_request("codex", backend["model"], False, "success")
        return JSONResponse(codex_responses_to_anthropic(aggregated, original_model, request_id))
    except Exception as e:
        log_request("codex", backend["model"], False, "error")
        safe_msg = str(e).encode("ascii", errors="replace").decode("ascii")
        return JSONResponse({"type": "error", "error": {"type": "api_error", "message": safe_msg}}, status_code=502)


@app.post("/v1/messages")
async def messages_api(request: Request):
    body = await request.json()
    original_model = body.get("model", "claude-sonnet-4-5")

    try:
        backend = config.resolve_backend(original_model)
    except ValueError as e:
        return JSONResponse({"type": "error", "error": {"type": "api_error", "message": str(e)}}, status_code=400)

    stream = body.get("stream", False)
    request_id = f"msg_{uuid.uuid4().hex[:16]}"

    if backend["backend"] == "codex":
        return await _handle_codex_messages(body, backend, original_model, request_id, stream)

    openai_body = anthropic_to_openai(body, backend["model"], backend["backend"], backend["base_url"])

    print(f"[proxy] → {backend['backend']} | model={backend['model']} | "
          f"stream={stream} | original_model={original_model}")

    headers = {"Authorization": backend["auth_header"], "Content-Type": "application/json"}
    client = get_client()
    url = f"{backend['base_url']}/chat/completions"

    if stream:
        async def stream_gen():
            try:
                async with client.stream("POST", url, json=openai_body, headers=headers) as backend_resp:
                    if backend_resp.status_code != 200:
                        try:
                            error_text = (await backend_resp.aread()).decode("utf-8", errors="replace")[:500]
                        except Exception:
                            error_text = "(unreadable response)"
                        print(f"[proxy] backend error {backend_resp.status_code}: {error_text}", flush=True)
                        log_request(backend["backend"], backend["model"], True, f"error {backend_resp.status_code}")
                        err_msg = f"Backend error {backend_resp.status_code}: {error_text}"
                        safe_msg = err_msg.encode("ascii", errors="replace").decode("ascii")
                        yield f"event: error\ndata: {json.dumps({'type':'error','error':{'type':'api_error','message':safe_msg}})}\n\n"
                        return
                    log_request(backend["backend"], backend["model"], True, "success")
                    async for event in translate_stream(backend_resp, original_model, request_id):
                        yield event
            except Exception as e:
                log_request(backend["backend"], backend["model"], True, "error")
                yield f"event: error\ndata: {json.dumps({'type':'error','error':{'type':'api_error','message':str(e)}})}\n\n"

        return StreamingResponse(stream_gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})
    else:
        try:
            resp = await client.post(url, json=openai_body, headers=headers)
            if resp.status_code != 200:
                err_text = resp.text[:500] if resp.text else "(empty)"
                print(f"[proxy] backend error {resp.status_code}: {err_text}", flush=True)
                log_request(backend["backend"], backend["model"], False, f"error {resp.status_code}")
                safe_msg = f"Backend returned {resp.status_code}: {err_text}".encode("ascii", errors="replace").decode("ascii")
                return JSONResponse({"type": "error", "error": {"type": "api_error", "message": safe_msg}}, status_code=resp.status_code)
            log_request(backend["backend"], backend["model"], False, "success")
            return JSONResponse(openai_to_anthropic_response(resp.json(), original_model, request_id))
        except Exception as e:
            log_request(backend["backend"], backend["model"], False, "error")
            safe_msg = str(e).encode("ascii", errors="replace").decode("ascii")
            return JSONResponse({"type": "error", "error": {"type": "api_error", "message": safe_msg}}, status_code=502)


@app.post("/v1/messages/count_tokens")
async def count_tokens(request: Request):
    body = await request.json()
    total_chars = 0
    for msg in body.get("messages", []):
        content = msg.get("content", "")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total_chars += len(json.dumps(block))
    system = body.get("system", "")
    if isinstance(system, str):
        total_chars += len(system)
    elif isinstance(system, list):
        total_chars += len(json.dumps(system))
    return JSONResponse({"input_tokens": max(1, total_chars // 4)})


# ---------------------------------------------------------------------------
# OAuth mocks
# ---------------------------------------------------------------------------

FAKE_ACCOUNT_UUID = "byok-user-000000000000000000"
FAKE_ORG_UUID = "org_byok_000000000000"
FAKE_ACCESS_TOKEN = "fake-bearer-token-for-proxy"


def fake_token_response() -> dict:
    return {
        "token_type": "bearer",
        "access_token": FAKE_ACCESS_TOKEN,
        "refresh_token": "fake-refresh-token",
        "expires_in": 999999999,
        "expires_at": "2099-12-31T23:59:59Z",
        "scope": "openid profile email",
    }


def fake_user_response() -> dict:
    return {
        "id": FAKE_ACCOUNT_UUID,
        "uuid": FAKE_ACCOUNT_UUID,
        "sub": FAKE_ACCOUNT_UUID,
        "email": "byok@localhost",
        "email_verified": True,
        "name": "BYOK User",
        "organization": fake_org_response(),
        "organization_uuid": FAKE_ORG_UUID,
        "org_uuid": FAKE_ORG_UUID,
        "subscription_type": "max",
        "rate_limit_tier": "tier_5",
        "seat_tier": "enterprise_usage_based",
        "billing_type": "api",
        "has_extra_usage_enabled": True,
    }


def fake_org_response() -> dict:
    return {
        "id": FAKE_ORG_UUID,
        "uuid": FAKE_ORG_UUID,
        "name": "BYOK Organization",
        "type": "organization",
        "status": "active",
        "default_role": "admin",
        "subscription": {"type": "max", "status": "active"},
        "rate_limit_tier": "tier_5",
        "billing_type": "api",
    }


def fake_org_list_response() -> dict:
    org = fake_org_response()
    return {
        **org,
        "data": [org],
        "organizations": [org],
        "has_more": False,
        "first_id": org["id"],
        "last_id": org["id"],
    }


@app.api_route("/v1/oauth/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def oauth_mock(request: Request, path: str):
    return JSONResponse(fake_token_response())


@app.get("/v1/userinfo")
@app.get("/v1/me")
@app.get("/v1/user")
@app.get("/v1/profile")
@app.get("/v1/account")
async def userinfo_mock(request: Request):
    return JSONResponse(fake_user_response())



@app.get("/v1/models")
async def list_models(request: Request):
    """Return compatible model list."""
    models = [
        {"id": "claude-sonnet-4-5", "type": "model", "display_name": "Claude Sonnet 4.5"},
        {"id": "claude-opus-4-8", "type": "model", "display_name": "Claude Opus 4.8"},
        {"id": "claude-haiku-4-5-20251001", "type": "model", "display_name": "Claude Haiku 4.5"},
        {"id": "deepseek-chat", "type": "model", "display_name": "DeepSeek Chat"},
        {"id": "deepseek-reasoner", "type": "model", "display_name": "DeepSeek Reasoner"},
        {"id": "gpt-4o", "type": "model", "display_name": "GPT-4o"},
    ]
    return JSONResponse({"data": models, "has_more": False, "first_id": models[0]["id"], "last_id": models[-1]["id"]})


# Add proper organization endpoint (not just catch-all)
@app.get("/v1/organizations")
async def orgs_mock(request: Request):
    """Mock organization list endpoint."""
    return JSONResponse(fake_org_list_response())


@app.get("/v1/organization")
@app.get("/v1/organizations/{org_id}")
async def org_mock(request: Request, org_id: str = FAKE_ORG_UUID):
    """Mock single organization endpoint."""
    return JSONResponse(fake_org_response())


@app.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def catch_all(request: Request, path: str):
    lowered = path.lower()
    if "oauth" in lowered or "token" in lowered:
        return JSONResponse(fake_token_response())
    if "organization" in lowered or lowered.startswith("org"):
        return JSONResponse(fake_org_list_response())
    if any(k in lowered for k in ("userinfo", "profile", "account", "user", "me")):
        return JSONResponse(fake_user_response())
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Dashboard & Management API
# ---------------------------------------------------------------------------

@app.get("/dashboard")
async def dashboard():
    return FileResponse(str(STATIC_DIR / "dashboard.html"))


@app.get("/api/config")
async def api_get_config():
    return config.public_dict()


@app.post("/api/config")
async def api_update_config(request: Request):
    body = await request.json()
    allowed_keys = {
        "deepseek_api_key", "openai_api_key", "custom_api_key",
        "openai_auth_mode",
        "deepseek_base_url", "openai_base_url", "custom_base_url",
        "codex_auth_base_url", "codex_device_url", "codex_client_id",
        "codex_backend_url", "codex_model",
        "default_backend", "force_model",
        "deepseek_model_map", "openai_model_map", "custom_model_map", "codex_model_map",
        "deepseek_model_pattern", "openai_model_pattern", "custom_model_pattern",
        "reasoning_content_policy", "inline_image_policy",
    }
    update_data = {k: v for k, v in body.items() if k in allowed_keys}
    # Reject masked API keys (bullet character U+2022)
    for key in ("deepseek_api_key", "openai_api_key", "custom_api_key"):
        if key in update_data and "•" in update_data[key]:
            del update_data[key]  # Skip masked placeholder
    if update_data:
        config.update(update_data)
        return {"ok": True}
    return {"ok": False, "error": "No valid config keys provided"}


@app.get("/api/codex-device/status")
async def api_codex_device_status():
    return {
        "ok": True,
        "configured": codex_auth_store.is_configured(),
        "email": codex_auth_store.public_email(),
        "expires_at": codex_auth_store.public_expires_at(),
        "device_url": config.codex_device_url,
    }


@app.post("/api/codex-device/start")
async def api_codex_device_start():
    code_verifier, code_challenge = _new_pkce_pair()
    payload = {
        "client_id": config.codex_client_id,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    try:
        resp = await _post_codex_auth("/deviceauth/usercode", payload)
        if resp.status_code != 200:
            return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:300]}"}
        data = resp.json()
        return {
            "ok": True,
            "device_auth_id": data.get("device_auth_id") or data.get("device_code"),
            "user_code": data.get("user_code"),
            "verification_uri": data.get("verification_uri") or config.codex_device_url,
            "verification_uri_complete": data.get("verification_uri_complete"),
            "expires_in": data.get("expires_in", 900),
            "interval": data.get("interval", 5),
            "code_verifier": code_verifier,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/codex-device/poll")
async def api_codex_device_poll(request: Request):
    body = await request.json()
    device_auth_id = body.get("device_auth_id") or body.get("device_code")
    user_code = body.get("user_code")
    code_verifier = body.get("code_verifier", "")
    if not device_auth_id:
        return {"ok": False, "error": "device_auth_id is required"}
    if not code_verifier:
        return {"ok": False, "error": "code_verifier is required"}

    payload = {
        "client_id": config.codex_client_id,
        "device_auth_id": device_auth_id,
    }
    if user_code:
        payload["user_code"] = user_code

    try:
        resp = await _post_codex_auth("/deviceauth/token", payload)
        data = {}
        try:
            data = resp.json()
        except Exception:
            pass
        if resp.status_code == 428 or data.get("error") in {"authorization_pending", "slow_down"}:
            return {"ok": False, "pending": True, "error": data.get("error", "authorization_pending")}
        if resp.status_code != 200:
            return {"ok": False, "error": data.get("error_description") or data.get("error") or f"HTTP {resp.status_code}: {resp.text[:300]}"}
        data = await _complete_codex_device_token(data, code_verifier, config.codex_client_id)
        token_data = _normalize_codex_token_response(data, config.codex_client_id)
        if not token_data.get("access_token"):
            return {"ok": False, "error": "Token response did not include an access token"}
        codex_auth_store.save(token_data)
        config.update({"openai_auth_mode": "codex_device", "default_backend": "openai"})
        return {
            "ok": True,
            "configured": True,
            "email": token_data.get("email", ""),
            "expires_at": token_data.get("expires_at", ""),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.delete("/api/codex-device")
async def api_codex_device_logout():
    codex_auth_store.clear()
    if config.openai_auth_mode == "codex_device":
        config.update({"openai_auth_mode": "api_key"})
    return {"ok": True}


@app.post("/api/test-backend")
async def api_test_backend(request: Request):
    """Test connectivity to a backend provider."""
    body = await request.json()
    provider = body.get("provider", "deepseek")
    api_key = body.get("api_key", "")
    base_url = body.get("base_url", "")
    auth_mode = body.get("auth_mode", "api_key")

    if provider == "openai" and auth_mode == "codex_device":
        try:
            auth_header = codex_auth_store.authorization_header()
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        # Verify the ChatGPT Codex backend accepts the token with a tiny request.
        backend = {
            "auth_header": auth_header,
            "account_id": codex_auth_store.account_id(),
        }
        probe_body = {
            "model": config.codex_model or "gpt-5.6-sol",
            "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "ping"}]}],
            "stream": True,
            "store": False,
        }
        url = f"{config.codex_backend_url.rstrip('/')}/responses"
        try:
            async with httpx.AsyncClient(timeout=20, trust_env=False) as c:
                async with c.stream("POST", url, json=probe_body, headers=_codex_headers(backend)) as resp:
                    if resp.status_code == 200:
                        await resp.aclose()
                        return {"ok": True, "models": [config.codex_model or "gpt-5.6-sol"]}
                    err_text = (await resp.aread()).decode("utf-8", errors="replace")[:300]
                    return {"ok": False, "error": f"HTTP {resp.status_code}: {err_text}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    elif not api_key:
        return {"ok": False, "error": "API key is required"}
    else:
        auth_header = f"Bearer {api_key}"

    if base_url:
        url = f"{normalize_openai_base_url(base_url)}/models"
    elif provider == "deepseek":
        url = "https://api.deepseek.com/v1/models"
    elif provider == "openai":
        url = "https://api.openai.com/v1/models"
    else:
        return {"ok": False, "error": "Custom provider requires an API Base URL"}

    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as c:
            resp = await c.get(url, headers={"Authorization": auth_header})
            if resp.status_code == 200:
                data = resp.json()
                models = [m.get("id", "") for m in data.get("data", [])[:10]]
                return {"ok": True, "models": models}
            else:
                return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:300]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/setup-global-env")
async def api_setup_global_env():
    """Set ANTHROPIC_BASE_URL globally on macOS via launchctl."""
    proxy_url = f"http://{config.proxy_host}:{config.proxy_port}"
    try:
        subprocess.run(
            ["launchctl", "setenv", "ANTHROPIC_BASE_URL", proxy_url],
            capture_output=True, text=True, timeout=5,
        )
        return {"ok": True, "proxy_url": proxy_url}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/install-service")
async def api_install_service():
    """Install proxy as a macOS LaunchAgent for auto-start on login."""
    plist_name = "com.byok.claude-science-proxy.plist"
    plist_dir = Path.home() / "Library" / "LaunchAgents"
    plist_path = plist_dir / plist_name

    proxy_url = f"http://{config.proxy_host}:{config.proxy_port}"

    python_dir = str(Path(sys.executable).parent)
    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.byok.claude-science-proxy</string>
    <key>ProgramArguments</key>
    <array>
        <string>{sys.executable}</string>
        <string>{Path(__file__).resolve()}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{PROXY_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>EnvironmentVariables</key>
    <dict>
        <key>ANTHROPIC_BASE_URL</key>
        <string>{proxy_url}</string>
        <key>PROXY_HOST</key>
        <string>{config.proxy_host}</string>
        <key>PROXY_PORT</key>
        <string>{config.proxy_port}</string>
        <key>PATH</key>
        <string>{python_dir}:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
    <key>StandardOutPath</key>
    <string>{Path.home() / ".claude-science" / "logs" / "proxy.log"}</string>
    <key>StandardErrorPath</key>
    <string>{Path.home() / ".claude-science" / "logs" / "proxy-error.log"}</string>
</dict>
</plist>"""

    try:
        plist_dir.mkdir(parents=True, exist_ok=True)
        with open(plist_path, "w") as f:
            f.write(plist_content)

        # Unload old, load new
        subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
        subprocess.run(["launchctl", "load", str(plist_path)], capture_output=True)

        # Also save a copy in the proxy dir
        copy_path = PROXY_DIR / plist_name
        with open(copy_path, "w") as f:
            f.write(plist_content)

        return {"ok": True, "plist_path": str(plist_path)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/refresh-token")
async def api_refresh_token():
    """Re-generate the fake OAuth token."""
    try:
        result = subprocess.run(
            [sys.executable, str(PROXY_DIR / "setup-token.py")],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return {"ok": True, "output": result.stdout.strip().split("\n")[-3:]}
        return {"ok": False, "error": result.stderr or result.stdout}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/recent-requests")
async def api_recent_requests():
    return {"requests": list(reversed(request_log[-50:]))}


@app.delete("/api/recent-requests")
async def api_clear_requests():
    request_log.clear()
    return {"ok": True}


@app.api_route("/api/oauth/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def api_oauth_mock(request: Request, path: str):
    lowered = path.lower()
    if any(k in lowered for k in ("profile", "account", "userinfo", "user", "me")):
        return JSONResponse(fake_user_response())
    if "organization" in lowered or lowered.startswith("org"):
        return JSONResponse(fake_org_list_response())
    if "usage" in lowered:
        return JSONResponse({
            "usage": {"used": 0, "limit": 999999999, "remaining": 999999999},
            "organization": fake_org_response(),
            "organizations": [fake_org_response()],
        })
    return JSONResponse(fake_token_response())


@app.api_route("/api/auth/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def api_auth_mock(request: Request, path: str):
    lowered = path.lower()
    if "organization" in lowered or lowered.startswith("org"):
        return JSONResponse(fake_org_list_response())
    return JSONResponse(fake_user_response())


@app.get("/api/userinfo")
@app.get("/api/me")
@app.get("/api/user")
@app.get("/api/profile")
@app.get("/api/account")
async def api_userinfo_mock(request: Request):
    return JSONResponse(fake_user_response())


@app.get("/api/organizations")
async def api_orgs_mock(request: Request):
    return JSONResponse(fake_org_list_response())


@app.get("/api/organization")
@app.get("/api/organizations/{org_id}")
async def api_org_mock(request: Request, org_id: str = FAKE_ORG_UUID):
    return JSONResponse(fake_org_response())


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def api_anthropic_catch_all(request: Request, path: str):
    lowered = path.lower()
    if "oauth" in lowered or "token" in lowered:
        return JSONResponse(fake_token_response())
    if "organization" in lowered or lowered.startswith("org"):
        return JSONResponse(fake_org_list_response())
    if any(k in lowered for k in ("userinfo", "profile", "account", "user", "me")):
        return JSONResponse(fake_user_response())
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "deepseek_configured": bool(config.deepseek_api_key),
        "openai_configured": bool(config.openai_api_key) or (
            config.openai_auth_mode == "codex_device" and codex_auth_store.is_configured()
        ),
        "openai_auth_mode": config.openai_auth_mode,
        "custom_configured": bool(config.custom_api_key and config.custom_base_url),
        "default_backend": config.default_backend,
        "force_model": config.force_model or "(none)",
        "inline_image_policy": config.inline_image_policy,
        "proxy_dir": str(PROXY_DIR),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import threading
    import uvicorn

    HTTPS_PORT = config.proxy_port + 1  # 9877 by default
    CERT_DIR = PROXY_DIR / "certs"
    SSL_CERT = str(CERT_DIR / "server-cert.pem")
    SSL_KEY = str(CERT_DIR / "server-key.pem")

    have_ssl = os.path.exists(SSL_CERT) and os.path.exists(SSL_KEY)

    print(f"\n{'='*60}")
    print(f"  Claude Science BYOK Proxy v2.1")
    print(f"  Dashboard → http://{config.proxy_host}:{config.proxy_port}/dashboard")
    if have_ssl:
        print(f"  HTTPS     → https://{config.proxy_host}:{HTTPS_PORT}")
        print(f"  Cert CN   → api.anthropic.com")
    print(f"  Health    → http://{config.proxy_host}:{config.proxy_port}/health")
    print(f"  {'-'*56}")
    print(f"  Codex link:")
    print(f"    backend → {config.codex_backend_url}")
    print(f"    model   → {config.codex_model}")
    if config.openai_auth_mode == "codex_device":
        if codex_auth_store.is_configured():
            _email = codex_auth_store.public_email() or "(unknown)"
            _exp = codex_auth_store.public_expires_at() or "(unknown)"
            print(f"    auth    → codex_device ({_email}, expires {_exp})")
        else:
            print(f"    auth    → codex_device (NOT configured — run ./setup-codex-device.py)")
    else:
        print(f"    auth    → {config.openai_auth_mode} (set openai_auth_mode=codex_device to use ChatGPT quota)")
    print(f"{'='*60}\n")

    if have_ssl:
        # Start HTTPS server in a background thread
        def run_https():
            uvicorn.run(
                app, host=config.proxy_host, port=HTTPS_PORT,
                ssl_keyfile=SSL_KEY, ssl_certfile=SSL_CERT,
                log_level="warning",
            )

        t = threading.Thread(target=run_https, daemon=True)
        t.start()
        print(f"[proxy] HTTPS server started on port {HTTPS_PORT}")

    # Start HTTP server (main thread)
    uvicorn.run(app, host=config.proxy_host, port=config.proxy_port, log_level="warning")
