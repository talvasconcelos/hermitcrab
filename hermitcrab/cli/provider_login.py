"""OAuth provider login commands."""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx
import typer
from rich.console import Console

from hermitcrab import __logo__

provider_app = typer.Typer(help="Manage providers")
console = Console()

_LOGIN_HANDLERS: dict[str, Callable[[], None]] = {}


def _register_login(name: str):
    def decorator(fn: Callable[[], None]) -> Callable[[], None]:
        _LOGIN_HANDLERS[name] = fn
        return fn

    return decorator


@provider_app.command("login")
def provider_login(
    provider: str = typer.Argument(..., help="OAuth provider (e.g. 'openai-codex')"),
):
    """Authenticate with an OAuth provider."""
    from hermitcrab.providers.registry import PROVIDERS, normalize_provider_name

    key = normalize_provider_name(provider)
    spec = next((s for s in PROVIDERS if s.name == key and s.is_oauth), None)
    if not spec:
        names = ", ".join(s.name.replace("_", "-") for s in PROVIDERS if s.is_oauth)
        console.print(f"[red]Unknown OAuth provider: {provider}[/red]  Supported: {names}")
        raise typer.Exit(1)

    handler = _LOGIN_HANDLERS.get(spec.name)
    if not handler:
        console.print(f"[red]Login not implemented for {spec.label}[/red]")
        raise typer.Exit(1)

    console.print(f"{__logo__} OAuth Login - {spec.label}\n")
    handler()


@_register_login("openai_codex")
def _login_openai_codex() -> None:
    from hermitcrab.providers.openai_codex_auth import (
        CODEX_OAUTH_CLIENT_ID,
        CODEX_OAUTH_TOKEN_URL,
        DEFAULT_CODEX_BASE_URL,
        codex_access_token_is_expiring,
        read_codex_cli_tokens,
        resolve_codex_runtime_credentials,
        save_codex_tokens,
    )

    try:
        existing = resolve_codex_runtime_credentials()
        access_token = str(existing.get("api_key", "") or "")
        if access_token and not codex_access_token_is_expiring(access_token, 60):
            console.print("[green]✓ Existing Codex OAuth credentials are valid[/green]")
            return
    except Exception:
        pass

    cli_tokens = read_codex_cli_tokens()
    if cli_tokens:
        console.print("Found existing Codex CLI credentials at [cyan]~/.codex/auth.json[/cyan].")
        console.print("HermitCrab can import them, but a separate login avoids token rotation conflicts.")
        if typer.confirm("Import Codex CLI credentials instead of starting a new login?", default=False):
            save_codex_tokens(cli_tokens)
            console.print("[green]✓ Imported Codex OAuth credentials[/green]")
            return

    issuer = "https://auth.openai.com"
    try:
        with httpx.Client(timeout=httpx.Timeout(15.0)) as client:
            response = client.post(
                f"{issuer}/api/accounts/deviceauth/usercode",
                json={"client_id": CODEX_OAUTH_CLIENT_ID},
                headers={"Content-Type": "application/json"},
            )
    except Exception as exc:
        console.print(f"[red]Failed to request Codex device code: {exc}[/red]")
        raise typer.Exit(1)

    if response.status_code != 200:
        console.print(f"[red]Codex device code request failed: HTTP {response.status_code}[/red]")
        raise typer.Exit(1)

    device_data = response.json()
    user_code = str(device_data.get("user_code", "") or "")
    device_auth_id = str(device_data.get("device_auth_id", "") or "")
    poll_interval = max(3, int(device_data.get("interval", "5") or 5))
    if not user_code or not device_auth_id:
        console.print("[red]Codex device code response was missing required fields[/red]")
        raise typer.Exit(1)

    console.print("To continue, open this URL in your browser:")
    console.print(f"[cyan]{issuer}/codex/device[/cyan]\n")
    console.print("Enter this code:")
    console.print(f"[bold cyan]{user_code}[/bold cyan]\n")
    console.print("Waiting for sign-in... press Ctrl+C to cancel.")

    code_response = None
    deadline = time.monotonic() + 15 * 60
    try:
        with httpx.Client(timeout=httpx.Timeout(15.0)) as client:
            while time.monotonic() < deadline:
                time.sleep(poll_interval)
                poll_response = client.post(
                    f"{issuer}/api/accounts/deviceauth/token",
                    json={"device_auth_id": device_auth_id, "user_code": user_code},
                    headers={"Content-Type": "application/json"},
                )
                if poll_response.status_code == 200:
                    code_response = poll_response.json()
                    break
                if poll_response.status_code in (403, 404):
                    continue
                console.print(
                    f"[red]Codex device auth polling failed: HTTP {poll_response.status_code}[/red]"
                )
                raise typer.Exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Login cancelled[/yellow]")
        raise typer.Exit(130)

    if code_response is None:
        console.print("[red]Codex login timed out after 15 minutes[/red]")
        raise typer.Exit(1)

    authorization_code = str(code_response.get("authorization_code", "") or "")
    code_verifier = str(code_response.get("code_verifier", "") or "")
    if not authorization_code or not code_verifier:
        console.print("[red]Codex device auth response was missing exchange fields[/red]")
        raise typer.Exit(1)

    try:
        with httpx.Client(timeout=httpx.Timeout(15.0)) as client:
            token_response = client.post(
                CODEX_OAUTH_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": authorization_code,
                    "redirect_uri": f"{issuer}/deviceauth/callback",
                    "client_id": CODEX_OAUTH_CLIENT_ID,
                    "code_verifier": code_verifier,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except Exception as exc:
        console.print(f"[red]Codex token exchange failed: {exc}[/red]")
        raise typer.Exit(1)

    if token_response.status_code != 200:
        console.print(f"[red]Codex token exchange failed: HTTP {token_response.status_code}[/red]")
        raise typer.Exit(1)

    tokens = token_response.json()
    access_token = str(tokens.get("access_token", "") or "")
    refresh_token = str(tokens.get("refresh_token", "") or "")
    if not access_token or not refresh_token:
        console.print("[red]Codex token exchange did not return complete credentials[/red]")
        raise typer.Exit(1)

    save_codex_tokens({"access_token": access_token, "refresh_token": refresh_token})
    console.print("[green]✓ Authenticated with OpenAI Codex OAuth[/green]")
    console.print(f"[dim]Endpoint: {DEFAULT_CODEX_BASE_URL}[/dim]")


@_register_login("github_copilot")
def _login_github_copilot() -> None:
    import asyncio

    console.print("[cyan]Starting GitHub Copilot device flow...[/cyan]\n")

    async def _trigger():
        from litellm import acompletion

        await acompletion(
            model="github_copilot/gpt-4o",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1,
        )

    try:
        asyncio.run(_trigger())
        console.print("[green]✓ Authenticated with GitHub Copilot[/green]")
    except Exception as e:
        console.print(f"[red]Authentication error: {e}[/red]")
        raise typer.Exit(1)
