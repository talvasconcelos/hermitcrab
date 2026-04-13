"""Qwen Portal OAuth provider."""

from __future__ import annotations

import json
import os
import platform
import stat
import time
from pathlib import Path
from typing import Any

import httpx
import json_repair
from openai import AsyncOpenAI

from hermitcrab.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from hermitcrab.providers.utils import function_parts

DEFAULT_QWEN_URL = "https://portal.qwen.ai/v1"
QWEN_OAUTH_CLIENT_ID = "f0304373b74a44d2b584a3fb70ca9e56"
QWEN_OAUTH_TOKEN_URL = "https://chat.qwen.ai/api/v1/oauth2/token"
QWEN_CLI_AUTH_PATH = Path.home() / ".qwen" / "oauth_creds.json"
QWEN_ACCESS_TOKEN_REFRESH_SKEW_SECONDS = 60
_QWEN_CODE_VERSION = "0.14.1"


def qwen_portal_headers() -> dict[str, str]:
    """Return the required headers for the Qwen Portal endpoint."""
    user_agent = (
        f"QwenCode/{_QWEN_CODE_VERSION} ({platform.system().lower()}; {platform.machine()})"
    )
    return {
        "User-Agent": user_agent,
        "X-DashScope-CacheControl": "enable",
        "X-DashScope-UserAgent": user_agent,
        "X-DashScope-AuthType": "qwen-oauth",
    }


def _read_qwen_cli_tokens() -> dict[str, Any]:
    if not QWEN_CLI_AUTH_PATH.exists():
        raise RuntimeError("Qwen CLI credentials not found. Run `qwen auth qwen-oauth` first.")
    try:
        data = json.loads(QWEN_CLI_AUTH_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Failed to read Qwen CLI credentials: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("Invalid Qwen CLI credentials file.")
    return data


def _save_qwen_cli_tokens(tokens: dict[str, Any]) -> None:
    QWEN_CLI_AUTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = QWEN_CLI_AUTH_PATH.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(tokens, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)
    tmp_path.replace(QWEN_CLI_AUTH_PATH)


def _qwen_access_token_is_expiring(expiry_date_ms: Any) -> bool:
    try:
        expiry_ms = int(expiry_date_ms)
    except Exception:
        return True
    return (time.time() + QWEN_ACCESS_TOKEN_REFRESH_SKEW_SECONDS) * 1000 >= expiry_ms


def _refresh_qwen_cli_tokens(tokens: dict[str, Any], timeout_seconds: float = 20.0) -> dict[str, Any]:
    refresh_token = str(tokens.get("refresh_token", "") or "").strip()
    if not refresh_token:
        raise RuntimeError("Qwen OAuth refresh token missing. Re-run `qwen auth qwen-oauth`.")

    try:
        response = httpx.post(
            QWEN_OAUTH_TOKEN_URL,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": QWEN_OAUTH_CLIENT_ID,
            },
            timeout=timeout_seconds,
        )
    except Exception as exc:
        raise RuntimeError(f"Qwen OAuth refresh failed: {exc}") from exc

    if response.status_code >= 400:
        body = response.text.strip()
        detail = f" Response: {body}" if body else ""
        raise RuntimeError(f"Qwen OAuth refresh failed. Re-run `qwen auth qwen-oauth`.{detail}")

    try:
        payload = response.json()
    except Exception as exc:
        raise RuntimeError(f"Qwen OAuth refresh returned invalid JSON: {exc}") from exc

    access_token = str(payload.get("access_token", "") or "").strip()
    if not access_token:
        raise RuntimeError("Qwen OAuth refresh response missing access_token.")

    try:
        expires_in_seconds = int(payload.get("expires_in"))
    except Exception:
        expires_in_seconds = 6 * 60 * 60

    refreshed = {
        "access_token": access_token,
        "refresh_token": str(payload.get("refresh_token", refresh_token) or refresh_token).strip(),
        "token_type": str(payload.get("token_type", tokens.get("token_type", "Bearer")) or "Bearer"),
        "resource_url": str(
            payload.get("resource_url", tokens.get("resource_url", "portal.qwen.ai"))
            or "portal.qwen.ai"
        ).strip(),
        "expiry_date": int(time.time() * 1000) + max(1, expires_in_seconds) * 1000,
    }
    _save_qwen_cli_tokens(refreshed)
    return refreshed


def resolve_qwen_runtime_credentials(*, refresh_if_expiring: bool = True) -> dict[str, Any]:
    """Load a valid Qwen OAuth access token from the local Qwen CLI store."""
    tokens = _read_qwen_cli_tokens()
    if refresh_if_expiring and _qwen_access_token_is_expiring(tokens.get("expiry_date")):
        tokens = _refresh_qwen_cli_tokens(tokens)

    access_token = str(tokens.get("access_token", "") or "").strip()
    if not access_token:
        raise RuntimeError("Qwen OAuth access token missing. Re-run `qwen auth qwen-oauth`.")

    return {
        "api_key": access_token,
        "api_base": DEFAULT_QWEN_URL,
        "auth_file": str(QWEN_CLI_AUTH_PATH),
    }


def _strip_model_prefix(model: str) -> str:
    if model.startswith("qwen-oauth/") or model.startswith("qwen_oauth/"):
        return model.split("/", 1)[1]
    if model.startswith("qwen-portal/") or model.startswith("qwen_portal/"):
        return model.split("/", 1)[1]
    return model


class QwenOAuthProvider(LLMProvider):
    """Use Qwen Portal with OAuth credentials sourced from the local Qwen CLI."""

    def __init__(
        self,
        default_model: str = "qwen-oauth/coder-model",
        api_base: str | None = None,
    ):
        super().__init__(api_key=None, api_base=api_base or DEFAULT_QWEN_URL)
        self.default_model = default_model

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        del reasoning_effort

        model_name = _strip_model_prefix(model or self.default_model)
        try:
            creds = resolve_qwen_runtime_credentials()
            client = AsyncOpenAI(
                api_key=creds["api_key"],
                base_url=self.api_base or creds["api_base"],
                default_headers=qwen_portal_headers(),
            )
        except Exception as exc:
            return LLMResponse(content=f"Error: {exc}", finish_reason="error")

        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": self._sanitize_empty_content(messages),
            "max_tokens": max(1, max_tokens),
            "temperature": temperature,
        }
        if tools:
            kwargs.update(tools=tools, tool_choice="auto")

        try:
            response = await client.chat.completions.create(**kwargs)
            return self._parse(response)
        except Exception as exc:
            return LLMResponse(content=f"Error: {exc}", finish_reason="error")
        finally:
            await client.close()

    def _parse(self, response: Any) -> LLMResponse:
        choice = response.choices[0]
        msg = choice.message
        tool_calls: list[ToolCallRequest] = []
        for tc in msg.tool_calls or []:
            call_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
            function = tc.get("function") if isinstance(tc, dict) else getattr(tc, "function", None)
            name, arguments = function_parts(function)
            if not name:
                continue
            if isinstance(arguments, str):
                arguments = json_repair.loads(arguments)
            tool_calls.append(
                ToolCallRequest(
                    id=call_id or f"call_{len(tool_calls)}",
                    name=name,
                    arguments=arguments if isinstance(arguments, dict) else {},
                )
            )

        usage = getattr(response, "usage", None)
        finish_reason = choice.finish_reason or "stop"
        if finish_reason == "tool_calls" and not tool_calls:
            finish_reason = "stop"

        return LLMResponse(
            content=msg.content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage={
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
            }
            if usage
            else {},
        )

    def get_default_model(self) -> str:
        return self.default_model
