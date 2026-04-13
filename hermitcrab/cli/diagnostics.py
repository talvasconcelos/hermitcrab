"""Shared status and doctor diagnostics for the CLI."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from hermitcrab.agent.skills import SkillsLoader
from hermitcrab.config.loader import get_config_path
from hermitcrab.config.schema import Config
from hermitcrab.providers.registry import PROVIDERS


@dataclass(slots=True)
class ProviderStatus:
    name: str
    label: str
    configured: bool
    selected: bool
    detail: str


@dataclass(slots=True)
class SkillStatus:
    name: str
    source: str
    available: bool
    missing_requirements: str


@dataclass(slots=True)
class DiagnosticFinding:
    check_id: str
    severity: str
    title: str
    detail: str
    remediation: str


@dataclass(slots=True)
class StatusReport:
    config_path: str
    config_exists: bool
    config_valid: bool
    config_error: str | None
    workspace: str
    workspace_exists: bool
    bootstrap_ready: bool
    selected_model: str
    resolved_model: str
    selected_provider: str | None
    provider_statuses: list[ProviderStatus] = field(default_factory=list)
    skill_statuses: list[SkillStatus] = field(default_factory=list)
    mcp_servers_configured: int = 0
    mcp_servers_valid: int = 0
    overall_state: str = "error"
    ready_for_chat: bool = False
    next_steps: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "provider_statuses": [asdict(item) for item in self.provider_statuses],
            "skill_statuses": [asdict(item) for item in self.skill_statuses],
        }


@dataclass(slots=True)
class DoctorReport:
    status: StatusReport
    findings: list[DiagnosticFinding] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.as_dict(),
            "findings": [asdict(item) for item in self.findings],
        }


def build_status_report(config_path: Path | None = None) -> StatusReport:
    """Build a structured runtime status snapshot."""
    path = config_path or get_config_path()
    config, config_error = _load_config_with_error(path)
    workspace = config.workspace_path
    selected_model = config.agents.defaults.model
    resolved_model = config.resolve_model_config(selected_model).model or ""
    selected_provider = config.get_provider_name(selected_model)
    provider_statuses = _build_provider_statuses(config, selected_provider)
    skill_statuses = _build_skill_statuses(workspace)
    mcp_servers_valid = sum(
        1 for server in config.tools.mcp_servers.values() if _is_valid_mcp(server)
    )

    next_steps = _build_next_steps(
        config_exists=path.exists(),
        config_valid=config_error is None,
        workspace_exists=workspace.exists(),
        bootstrap_ready=(workspace / "AGENTS.md").exists(),
        selected_provider=selected_provider,
        provider_statuses=provider_statuses,
    )
    overall_state, ready_for_chat = _derive_status_health(
        config_exists=path.exists(),
        config_valid=config_error is None,
        workspace_exists=workspace.exists(),
        bootstrap_ready=(workspace / "AGENTS.md").exists(),
        selected_provider=selected_provider,
        provider_statuses=provider_statuses,
    )

    return StatusReport(
        config_path=str(path),
        config_exists=path.exists(),
        config_valid=config_error is None,
        config_error=config_error,
        workspace=str(workspace),
        workspace_exists=workspace.exists(),
        bootstrap_ready=(workspace / "AGENTS.md").exists(),
        selected_model=selected_model,
        resolved_model=resolved_model,
        selected_provider=selected_provider,
        provider_statuses=provider_statuses,
        skill_statuses=skill_statuses,
        mcp_servers_configured=len(config.tools.mcp_servers),
        mcp_servers_valid=mcp_servers_valid,
        overall_state=overall_state,
        ready_for_chat=ready_for_chat,
        next_steps=next_steps,
    )


def build_doctor_report(config_path: Path | None = None) -> DoctorReport:
    """Build a structured doctor report with remediation guidance."""
    status = build_status_report(config_path)
    findings: list[DiagnosticFinding] = []

    if not status.config_exists:
        findings.append(
            DiagnosticFinding(
                check_id="config.missing",
                severity="error",
                title="Config file is missing",
                detail="HermitCrab has no config file yet.",
                remediation="Run `hermitcrab onboard` to create config and workspace defaults.",
            )
        )
    elif not status.config_valid:
        findings.append(
            DiagnosticFinding(
                check_id="config.invalid",
                severity="error",
                title="Config file is invalid",
                detail=status.config_error or "The config file could not be parsed.",
                remediation="Fix the JSON or rerun `hermitcrab onboard` and refresh the config.",
            )
        )

    if not status.workspace_exists:
        findings.append(
            DiagnosticFinding(
                check_id="workspace.missing",
                severity="error",
                title="Workspace directory is missing",
                detail=f"Configured workspace `{status.workspace}` does not exist.",
                remediation="Run `hermitcrab onboard` or create the workspace path manually.",
            )
        )
    elif not status.bootstrap_ready:
        findings.append(
            DiagnosticFinding(
                check_id="workspace.bootstrap_missing",
                severity="warning",
                title="Workspace bootstrap files look incomplete",
                detail="The workspace exists, but `AGENTS.md` is missing.",
                remediation="Run `hermitcrab onboard` to restore the default workspace templates.",
            )
        )

    selected_provider = next(
        (item for item in status.provider_statuses if item.selected),
        None,
    )
    if selected_provider is None or not selected_provider.configured:
        findings.append(
            DiagnosticFinding(
                check_id="provider.not_ready",
                severity="error",
                title="Selected model does not have a ready provider",
                detail=(
                    f"Model `{status.selected_model}` resolves to provider "
                    f"`{status.selected_provider or 'none'}`, but that provider is not configured."
                ),
                remediation=(
                    "Add the provider credentials or local endpoint in `~/.hermitcrab/config.json`, "
                    "or switch to a configured model."
                ),
            )
        )

    if status.selected_provider == "ollama" and shutil.which("ollama") is None:
        findings.append(
            DiagnosticFinding(
                check_id="provider.ollama.binary_missing",
                severity="warning",
                title="Ollama is selected but not installed locally",
                detail="The configured model points to Ollama, but the `ollama` binary was not found.",
                remediation="Install Ollama, start `ollama serve`, and pull the chosen model.",
            )
        )

    for server_name, issue in _mcp_findings(status).items():
        findings.append(
            DiagnosticFinding(
                check_id=f"mcp.{server_name}",
                severity=issue["severity"],
                title=issue["title"],
                detail=issue["detail"],
                remediation=issue["remediation"],
            )
        )

    unavailable_skills = [skill for skill in status.skill_statuses if not skill.available]
    if unavailable_skills:
        preview = ", ".join(
            f"{skill.name} ({skill.missing_requirements})"
            for skill in unavailable_skills[:3]
            if skill.missing_requirements
        )
        findings.append(
            DiagnosticFinding(
                check_id="skills.missing_requirements",
                severity="info",
                title="Some built-in skills are currently unavailable",
                detail=preview or "Some skills are hidden until local requirements are met.",
                remediation="Install the missing CLIs or set the required environment variables as needed.",
            )
        )

    if not findings:
        findings.append(
            DiagnosticFinding(
                check_id="doctor.ok",
                severity="ok",
                title="No obvious setup blockers found",
                detail="HermitCrab looks ready for a first useful response.",
                remediation="Run `hermitcrab agent` to start chatting.",
            )
        )

    return DoctorReport(status=status, findings=findings)


def render_json_report(report: StatusReport | DoctorReport) -> str:
    """Serialize a status-like report as stable JSON."""
    data = report.as_dict()
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _load_config_with_error(path: Path) -> tuple[Config, str | None]:
    if not path.exists():
        return Config(), None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return Config(), str(exc)

    try:
        return Config.model_validate(data), None
    except Exception as exc:  # pragma: no cover - pydantic error shape is not the point here
        return Config(), str(exc)


def _build_provider_statuses(config: Config, selected_provider: str | None) -> list[ProviderStatus]:
    statuses: list[ProviderStatus] = []
    for spec in PROVIDERS:
        provider_config = getattr(config.providers, spec.name, None)
        if provider_config is None:
            continue
        configured, detail = _provider_ready(spec.name, spec, provider_config)
        statuses.append(
            ProviderStatus(
                name=spec.name,
                label=spec.label,
                configured=configured,
                selected=spec.name == selected_provider,
                detail=detail,
            )
        )
    return statuses


def _provider_ready(spec_name: str, spec: Any, provider_config: Any) -> tuple[bool, str]:
    if spec.is_oauth:
        return _oauth_provider_ready(spec_name)
    if spec.is_local:
        api_base = provider_config.api_base or spec.default_api_base
        return bool(api_base), api_base or "Local endpoint not configured"
    if provider_config.api_key:
        return True, "API key configured"
    return False, "API key not configured"


def _oauth_provider_ready(spec_name: str) -> tuple[bool, str]:
    if spec_name in {"openai_oauth", "openai_codex"}:
        try:
            from oauth_cli_kit.providers import OPENAI_CODEX_PROVIDER
            from oauth_cli_kit.storage import FileTokenStorage

            storage = FileTokenStorage(token_filename=OPENAI_CODEX_PROVIDER.token_filename)
            if storage.load():
                return True, "OAuth login detected"
        except Exception:
            pass
        command = "openai-oauth" if spec_name == "openai_oauth" else "openai-codex"
        return False, f"Run `hermitcrab provider login {command}`"

    if spec_name == "qwen_oauth":
        try:
            from hermitcrab.providers.qwen_oauth_provider import resolve_qwen_runtime_credentials

            resolve_qwen_runtime_credentials(refresh_if_expiring=False)
            return True, "OAuth login detected"
        except Exception:
            return False, "Run `hermitcrab provider login qwen-oauth`"

    return False, "OAuth login required"


def _build_skill_statuses(workspace: Path) -> list[SkillStatus]:
    loader = SkillsLoader(workspace)
    inspected = loader.inspect_skills()
    return [
        SkillStatus(
            name=str(item["name"]),
            source=str(item["source"]),
            available=bool(item["available"]),
            missing_requirements=str(item.get("missing_requirements") or ""),
        )
        for item in inspected
    ]


def _build_next_steps(
    *,
    config_exists: bool,
    config_valid: bool,
    workspace_exists: bool,
    bootstrap_ready: bool,
    selected_provider: str | None,
    provider_statuses: list[ProviderStatus],
) -> list[str]:
    steps: list[str] = []
    if not config_exists:
        steps.append("Run `hermitcrab onboard` to create the default config and workspace.")
        return steps
    if not config_valid:
        steps.append("Fix `~/.hermitcrab/config.json` or rerun `hermitcrab onboard`.")
        return steps
    if not workspace_exists:
        steps.append("Create the configured workspace or rerun `hermitcrab onboard`.")
    elif not bootstrap_ready:
        steps.append("Restore the workspace bootstrap files with `hermitcrab onboard`.")
    selected = next((item for item in provider_statuses if item.selected), None)
    if selected is None or not selected.configured:
        if selected_provider == "ollama":
            steps.append(
                "Install or start Ollama, pull the selected model, then run `hermitcrab doctor`."
            )
        elif selected_provider in {"openai_oauth", "openai_codex"}:
            command = "openai-oauth" if selected_provider == "openai_oauth" else "openai-codex"
            steps.append(
                f"Run `hermitcrab provider login {command}`, then try `hermitcrab agent -m \"Hello!\"`."
            )
        elif selected_provider == "qwen_oauth":
            steps.append(
                "Run `hermitcrab provider login qwen-oauth`, then try `hermitcrab agent -m \"Hello!\"`."
            )
        elif selected_provider:
            steps.append(
                "Add credentials or endpoint settings for the selected provider in "
                "`~/.hermitcrab/config.json`, then run `hermitcrab doctor`."
            )
        else:
            steps.append(
                "Choose a configured model/provider in `~/.hermitcrab/config.json`, then run "
                "`hermitcrab doctor`."
            )
    elif selected_provider == "ollama":
        if shutil.which("ollama") is None:
            steps.append(
                "Install Ollama from https://ollama.com, then run `hermitcrab doctor`."
            )
            return steps
        steps.append(
            "If you have not started it yet, run `ollama serve` before `hermitcrab agent`."
        )
        steps.append('Try a one-shot smoke test with `hermitcrab agent -m "Hello!"`.')
    else:
        steps.append('Try a one-shot smoke test with `hermitcrab agent -m "Hello!"`.')
        steps.append("Run `hermitcrab agent` to start a local interactive session.")
    return steps


def _derive_status_health(
    *,
    config_exists: bool,
    config_valid: bool,
    workspace_exists: bool,
    bootstrap_ready: bool,
    selected_provider: str | None,
    provider_statuses: list[ProviderStatus],
) -> tuple[str, bool]:
    selected = next((item for item in provider_statuses if item.selected), None)
    provider_ready = selected is not None and selected.configured

    if not config_exists or not config_valid or not workspace_exists or not provider_ready:
        return "error", False
    if not bootstrap_ready:
        return "warning", True
    if selected_provider == "ollama" and shutil.which("ollama") is None:
        return "warning", True
    return "ready", True


def _is_valid_mcp(server: Any) -> bool:
    return bool(server.command or server.url)


def _mcp_findings(status: StatusReport) -> dict[str, dict[str, str]]:
    findings: dict[str, dict[str, str]] = {}
    config_path = Path(status.config_path)
    config, _ = _load_config_with_error(config_path)
    for server_name, server in config.tools.mcp_servers.items():
        if not _is_valid_mcp(server):
            findings[server_name] = {
                "severity": "warning",
                "title": "MCP server configuration is incomplete",
                "detail": f"Server `{server_name}` has neither a command nor a URL configured.",
                "remediation": "Add either `command`/`args` for stdio or `url` for HTTP transport.",
            }
            continue
        if (
            server.command
            and shutil.which(server.command) is None
            and not os.path.isabs(server.command)
        ):
            findings[server_name] = {
                "severity": "warning",
                "title": "MCP server command is not installed",
                "detail": f"Server `{server_name}` uses command `{server.command}`, which is not on PATH.",
                "remediation": "Install the command or replace it with the correct executable path.",
            }
    return findings
