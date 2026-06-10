"""Provider-specific request attribution headers."""

from __future__ import annotations

from urllib.parse import urlparse


_OPENROUTER_ATTRIBUTION_HEADERS = {
    "HTTP-Referer": "https://github.com/talvasconcelos/hermitcrab",
    "X-Title": "HermitCrab",
    "X-OpenRouter-Categories": "productivity,agent",
}


def _host_matches(api_base: str | None, needle: str) -> bool:
    if not api_base:
        return False
    try:
        hostname = urlparse(api_base).hostname or ""
    except Exception:
        hostname = ""
    return needle.lower() in hostname.lower()


def provider_default_headers(*, provider_name: str | None, api_base: str | None) -> dict[str, str]:
    """Return HermitCrab's default request headers for a provider."""
    if provider_name == "openrouter" or _host_matches(api_base, "openrouter.ai"):
        return dict(_OPENROUTER_ATTRIBUTION_HEADERS)
    return {}


def merge_provider_headers(
    *,
    provider_name: str | None,
    api_base: str | None,
    configured_headers: dict[str, str] | None,
) -> dict[str, str] | None:
    """Merge app attribution headers with user-configured provider headers.

    User-configured headers win so advanced users can override attribution or add
    provider-specific flags without HermitCrab clobbering them.
    """
    headers = provider_default_headers(provider_name=provider_name, api_base=api_base)
    if configured_headers:
        headers.update(configured_headers)
    return headers or None
