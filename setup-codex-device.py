#!/usr/bin/env python3
"""Link this bridge to OpenAI Codex with device-code auth.

This script intentionally uses only Python's standard library so it can run
from the user's shell before the proxy virtualenv or FastAPI dependencies exist.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
CONFIG_FILE = APP_DIR / "config.json"
CODEX_AUTH_FILE = APP_DIR / "codex-auth.json"
CODEX_CLI_AUTH_FILE = Path.home() / ".codex" / "auth.json"

DEFAULTS = {
    "openai_auth_mode": "api_key",
    "openai_base_url": "https://api.openai.com",
    "codex_auth_base_url": "https://auth.openai.com",
    "codex_device_url": "https://auth.openai.com/codex/device",
    "codex_client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
    "codex_backend_url": "https://chatgpt.com/backend-api/codex",
    "codex_model": "gpt-5-codex",
    "default_backend": "deepseek",
}


def load_config() -> dict:
    data = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                stored = json.load(f)
            if isinstance(stored, dict):
                data.update(stored)
        except Exception:
            pass
    return data


def save_config(data: dict):
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(CONFIG_FILE, 0o600)


def save_codex_auth(data: dict):
    with open(CODEX_AUTH_FILE, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(CODEX_AUTH_FILE, 0o600)


def print_runtime():
    print("Script runtime:")
    print(f"- cwd: {Path.cwd()}")
    print(f"- script: {Path(__file__).resolve()}")
    print(f"- python: {sys.executable}")
    print(f"- python_version: {sys.version.split()[0]}")
    print(f"- bridge_auth_file: {CODEX_AUTH_FILE}")
    print(f"- codex_cli_auth_file: {CODEX_CLI_AUTH_FILE}")
    print("")


def import_codex_cli_auth(config: dict) -> bool:
    if not CODEX_CLI_AUTH_FILE.exists():
        return False
    try:
        with open(CODEX_CLI_AUTH_FILE) as f:
            data = json.load(f)
    except Exception as e:
        print(f"Could not read Codex CLI auth file: {e}", file=sys.stderr)
        return False

    tokens = data.get("tokens") if isinstance(data, dict) else None
    if not isinstance(tokens, dict) or not tokens.get("access_token"):
        return False

    imported = normalize_token_response(
        {
            "access_token": tokens.get("access_token"),
            "refresh_token": tokens.get("refresh_token"),
            "id_token": tokens.get("id_token"),
            "chatgpt_account_id": tokens.get("account_id"),
        },
        str(config.get("codex_client_id") or DEFAULTS["codex_client_id"]),
    )
    save_codex_auth(imported)
    config.update({"openai_auth_mode": "codex_device", "default_backend": "openai"})
    save_config(config)
    print("Imported existing Codex CLI ChatGPT login.")
    print("OpenAI backend is now set to use codex_device auth.")
    print("No token values were printed.")
    return True


def new_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def request_json(method: str, url: str, payload: dict, *, as_form: bool = False) -> tuple[int, dict, str]:
    if as_form:
        body = urllib.parse.urlencode(payload).encode()
        content_type = "application/x-www-form-urlencoded"
    else:
        body = json.dumps(payload).encode()
        content_type = "application/json"
    req = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": content_type, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            return resp.status, parse_json(text), text
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", errors="replace")
        return e.code, parse_json(text), text


def post_codex_auth(auth_base_url: str, path: str, payload: dict) -> tuple[int, dict, str]:
    url = auth_base_url.rstrip("/") + path
    status, data, text = request_json("POST", url, payload)
    if status in {400, 415, 422}:
        return request_json("POST", url, payload, as_form=True)
    return status, data, text


def parse_json(text: str) -> dict:
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def expires_at_from_response(token_data: dict) -> str:
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


def normalize_token_response(token_data: dict, client_id: str) -> dict:
    account_id = token_data.get("chatgpt_account_id", "") or _account_id_from_id_token(
        token_data.get("id_token", "")
    )
    normalized = {
        "access_token": token_data.get("access_token", ""),
        "refresh_token": token_data.get("refresh_token", ""),
        "id_token": token_data.get("id_token", ""),
        "token_type": token_data.get("token_type", "Bearer"),
        "client_id": client_id,
        "expires_at": expires_at_from_response(token_data),
        "email": token_data.get("email", "") or _email_from_id_token(token_data.get("id_token", "")),
        "chatgpt_account_id": account_id,
    }
    return {k: v for k, v in normalized.items() if v}


def _decode_jwt_claims(token: str) -> dict:
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


def _exp_from_access_token(access_token: str):
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


def exchange_authorization_code(auth_base_url: str, code: str, code_verifier: str, client_id: str) -> dict:
    status, data, text = request_json(
        "POST",
        auth_base_url.rstrip("/") + "/oauth/token",
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "code_verifier": code_verifier,
        },
        as_form=True,
    )
    if status != 200:
        raise RuntimeError(f"OAuth token exchange failed: HTTP {status}: {text[:300]}")
    return data


def complete_device_token(auth_base_url: str, data: dict, code_verifier: str, client_id: str) -> dict:
    if data.get("access_token"):
        return data
    authorization_code = data.get("authorization_code") or data.get("code")
    if authorization_code:
        return exchange_authorization_code(auth_base_url, authorization_code, code_verifier, client_id)
    return data


def main() -> int:
    print_runtime()
    config = load_config()
    auth_base_url = str(config.get("codex_auth_base_url") or DEFAULTS["codex_auth_base_url"])
    device_url = str(config.get("codex_device_url") or DEFAULTS["codex_device_url"])
    client_id = str(config.get("codex_client_id") or DEFAULTS["codex_client_id"])

    if import_codex_cli_auth(config):
        return 0

    print("No reusable Codex CLI ChatGPT token was found.")
    print("Recommended environment/action:")
    print("1. Run: codex login --device-auth")
    print("2. Complete the official Codex device-code login.")
    print("3. Re-run this script from:")
    print(f"   cd {APP_DIR}")
    print("   ./setup-codex-device.py")
    print("")

    code_verifier, code_challenge = new_pkce_pair()
    start_payload = {
        "client_id": client_id,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }

    try:
        status, data, text = post_codex_auth(auth_base_url, "/deviceauth/usercode", start_payload)
    except Exception as e:
        print(f"Failed to request device code: {e}", file=sys.stderr)
        return 1

    if status != 200:
        print(f"Device code request failed: HTTP {status}", file=sys.stderr)
        print(text[:500], file=sys.stderr)
        return 1

    device_auth_id = data.get("device_auth_id") or data.get("device_code")
    user_code = data.get("user_code")
    verification_uri = data.get("verification_uri") or device_url
    expires_in = int(data.get("expires_in") or 900)
    interval = max(2, int(data.get("interval") or 5))

    if not device_auth_id or not user_code:
        print("Device code response was missing device_auth_id or user_code.", file=sys.stderr)
        return 1

    print("Open this URL and sign in:")
    print(verification_uri)
    print("\nEnter this code:")
    print(user_code)
    print("\nWaiting for authorization...")

    deadline = time.time() + expires_in
    while time.time() < deadline:
        time.sleep(interval)
        poll_payload = {
            "client_id": client_id,
            "device_auth_id": device_auth_id,
            "user_code": user_code,
        }
        try:
            status, token_data, text = post_codex_auth(auth_base_url, "/deviceauth/token", poll_payload)
        except Exception as e:
            print(f"Token poll failed: {e}", file=sys.stderr)
            continue

        if status == 428 or token_data.get("error") in {"authorization_pending", "slow_down"}:
            continue
        if status != 200:
            err = token_data.get("error_description") or token_data.get("error") or text[:300]
            print(f"Device auth failed: {err}", file=sys.stderr)
            return 1

        try:
            token_data = complete_device_token(auth_base_url, token_data, code_verifier, client_id)
        except Exception as e:
            print(str(e), file=sys.stderr)
            return 1

        normalized = normalize_token_response(token_data, client_id)
        if not normalized.get("access_token"):
            print("Token response did not include an access token.", file=sys.stderr)
            return 1

        save_codex_auth(normalized)
        config.update({"openai_auth_mode": "codex_device", "default_backend": "openai"})
        save_config(config)
        email = normalized.get("email") or "(email unavailable)"
        print(f"Codex device auth linked for {email}.")
        print("OpenAI backend is now set to use codex_device auth.")
        return 0

    print("Device auth timed out before authorization completed.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
