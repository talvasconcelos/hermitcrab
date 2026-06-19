"""OpenAI Codex OAuth token storage and refresh."""

from __future__ import annotations

import base64
import json
import os
import stat
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

DEFAULT_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
CODEX_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
CODEX_ACCESS_TOKEN_REFRESH_SKEW_SECONDS = 120


class CodexAuthError(RuntimeError):
    """Raised when Codex OAuth credentials are missing or invalid."""


def get_auth_store_path() -> Path:
    """Return HermitCrab's local auth store path."""
    return Path.home() / ".hermitcrab" / "auth.json"


def _load_auth_store() -> dict[str, Any]:
    path = get_auth_store_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CodexAuthError(f"Failed to read Codex auth store: {exc}") from exc
    return payload if isinstance(payload, dict) else {}


def _save_auth_store(payload: dict[str, Any]) -> None:
    path = get_auth_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if os.name != "nt":
        os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)
        os.chmod(path.parent, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    tmp_path.replace(path)


def _provider_state(store: dict[str, Any]) -> dict[str, Any]:
    providers = store.setdefault("providers", {})
    if not isinstance(providers, dict):
        providers = {}
        store["providers"] = providers
    state = providers.setdefault("openai-codex", {})
    if not isinstance(state, dict):
        state = {}
        providers["openai-codex"] = state
    return state


def read_codex_tokens() -> dict[str, Any]:
    """Read Codex OAuth tokens from HermitCrab's auth store."""
    store = _load_auth_store()
    providers = store.get("providers") if isinstance(store.get("providers"), dict) else {}
    state = providers.get("openai-codex") if isinstance(providers, dict) else None
    tokens = state.get("tokens") if isinstance(state, dict) else None
    if not isinstance(tokens, dict):
        raise CodexAuthError("No Codex credentials stored. Run `hermitcrab provider login openai-codex`.")
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise CodexAuthError("Codex credentials are missing access_token.")
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        raise CodexAuthError("Codex credentials are missing refresh_token.")
    return {"tokens": tokens, "last_refresh": state.get("last_refresh")}


def save_codex_tokens(tokens: dict[str, str], last_refresh: str | None = None) -> None:
    """Save Codex OAuth tokens to HermitCrab's auth store."""
    if last_refresh is None:
        last_refresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    store = _load_auth_store()
    state = _provider_state(store)
    state["tokens"] = tokens
    state["last_refresh"] = last_refresh
    state["auth_mode"] = "chatgpt"
    _save_auth_store(store)


def _decode_jwt_claims(token: str) -> dict[str, Any]:
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload_b64))
        return claims if isinstance(claims, dict) else {}
    except Exception:
        return {}


def codex_access_token_is_expiring(token: Any, skew_seconds: int) -> bool:
    """Return True when a JWT access token is expired or near expiry."""
    if not isinstance(token, str) or not token.strip():
        return True
    exp = _decode_jwt_claims(token).get("exp")
    try:
        return time.time() + skew_seconds >= float(exp)
    except Exception:
        return True


def _refresh_codex_oauth(
    access_token: str,
    refresh_token: str,
    *,
    timeout_seconds: float,
) -> dict[str, str]:
    del access_token
    try:
        response = httpx.post(
            CODEX_OAUTH_TOKEN_URL,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CODEX_OAUTH_CLIENT_ID,
            },
            timeout=timeout_seconds,
        )
    except Exception as exc:
        raise CodexAuthError(f"Codex token refresh failed: {exc}") from exc

    if response.status_code != 200:
        detail = response.text.strip()
        suffix = f" Response: {detail}" if detail else ""
        raise CodexAuthError(
            "Codex token refresh failed. Re-run "
            f"`hermitcrab provider login openai-codex`.{suffix}"
        )

    try:
        payload = response.json()
    except Exception as exc:
        raise CodexAuthError(f"Codex token refresh returned invalid JSON: {exc}") from exc

    refreshed_access = str(payload.get("access_token", "") or "").strip()
    if not refreshed_access:
        raise CodexAuthError("Codex token refresh response missing access_token.")

    next_refresh = str(payload.get("refresh_token", refresh_token) or refresh_token).strip()
    return {"access_token": refreshed_access, "refresh_token": next_refresh}


def resolve_codex_runtime_credentials(
    *,
    force_refresh: bool = False,
    refresh_if_expiring: bool = True,
) -> dict[str, Any]:
    """Resolve a valid Codex access token from HermitCrab's auth store."""
    data = read_codex_tokens()
    tokens = dict(data["tokens"])
    access_token = str(tokens.get("access_token", "") or "").strip()
    refresh_token = str(tokens.get("refresh_token", "") or "").strip()

    should_refresh = force_refresh or (
        refresh_if_expiring
        and codex_access_token_is_expiring(access_token, CODEX_ACCESS_TOKEN_REFRESH_SKEW_SECONDS)
    )
    if should_refresh:
        timeout_seconds = float(os.getenv("HERMITCRAB_CODEX_REFRESH_TIMEOUT_SECONDS", "20"))
        tokens = _refresh_codex_oauth(
            access_token,
            refresh_token,
            timeout_seconds=timeout_seconds,
        )
        save_codex_tokens(tokens)
        access_token = tokens["access_token"]

    base_url = (
        os.getenv("HERMITCRAB_CODEX_BASE_URL", "").strip().rstrip("/")
        or DEFAULT_CODEX_BASE_URL
    )
    return {
        "provider": "openai-codex",
        "api_key": access_token,
        "base_url": base_url,
        "source": "hermitcrab-auth-store",
        "last_refresh": data.get("last_refresh"),
    }


def read_codex_cli_tokens() -> dict[str, str] | None:
    """Read valid tokens from Codex CLI without mutating its auth file."""
    codex_home = os.getenv("CODEX_HOME", "").strip() or str(Path.home() / ".codex")
    auth_path = Path(codex_home).expanduser() / "auth.json"
    if not auth_path.exists():
        return None
    try:
        payload = json.loads(auth_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    tokens = payload.get("tokens") if isinstance(payload, dict) else None
    if not isinstance(tokens, dict):
        return None
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    if not isinstance(access_token, str) or not isinstance(refresh_token, str):
        return None
    if codex_access_token_is_expiring(access_token, 0):
        return None
    return {"access_token": access_token, "refresh_token": refresh_token}


def extract_chatgpt_account_id(access_token: str) -> str | None:
    """Extract ChatGPT account id from an access-token JWT."""
    claims = _decode_jwt_claims(access_token)
    auth_claims = claims.get("https://api.openai.com/auth")
    if isinstance(auth_claims, dict):
        account_id = auth_claims.get("chatgpt_account_id")
        if isinstance(account_id, str) and account_id.strip():
            return account_id.strip()
    return None


def codex_cloudflare_headers(access_token: str) -> dict[str, str]:
    """Return Codex CLI-like headers for chatgpt.com/backend-api/codex."""
    headers = {
        "User-Agent": "codex_cli_rs/0.0.0 (HermitCrab)",
        "originator": "codex_cli_rs",
    }
    account_id = extract_chatgpt_account_id(access_token)
    if account_id:
        headers["ChatGPT-Account-ID"] = account_id
    return headers

