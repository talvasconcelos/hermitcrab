"""Focused regressions for beta4 system audit logs."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from typer.testing import CliRunner

from hermitcrab.agent.audit import AuditTrail
from hermitcrab.agent.loop import AgentLoop
from hermitcrab.bus.events import InboundMessage
from hermitcrab.bus.queue import MessageBus
from hermitcrab.cli.commands import app, bootstrap_beta4_layout
from hermitcrab.cli.diagnostics import build_status_report
from hermitcrab.config.loader import save_config
from hermitcrab.config.schema import Config

runner = CliRunner()


def _provider() -> MagicMock:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    return provider


def test_agent_loop_writes_audit_to_system_root_with_identity_metadata(tmp_path) -> None:
    config = Config.model_validate({"root": str(tmp_path), "identities": {"ownerIdentity": "tal"}})
    bootstrap_beta4_layout(config)
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=config.owner_identity_root_path,
        identity_name=config.owner_identity_name,
        identity_root=config.owner_identity_root_path,
        system_root=config.system_root_path,
    )

    loop.audit_event("model.switch", session_key="cli:default")

    audit_path = config.system_root_path / "logs" / "audit.jsonl"
    assert audit_path.exists()
    assert not (config.owner_identity_root_path / "logs" / "audit.jsonl").exists()

    entry = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert entry["event"] == "model.switch"
    assert entry["identity_name"] == "tal"
    assert entry["session_key"] == "cli:default"


def test_audit_identity_metadata_uses_runtime_identity(tmp_path) -> None:
    config = Config.model_validate({"root": str(tmp_path), "identities": {"ownerIdentity": "tal"}})
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=config.owner_identity_root_path,
        identity_name="tal",
        identity_root=config.owner_identity_root_path,
        system_root=config.system_root_path,
    )

    loop.audit_event("channel.event", identity_name="spoofed")

    entry = json.loads(loop.audit.path.read_text(encoding="utf-8").splitlines()[0])
    assert entry["identity_name"] == "tal"


def test_audit_routed_identity_metadata_does_not_override_runtime_identity(tmp_path) -> None:
    config = Config.model_validate({"root": str(tmp_path), "identities": {"ownerIdentity": "tal"}})
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=config.owner_identity_root_path,
        identity_name="tal",
        identity_root=config.owner_identity_root_path,
        system_root=config.system_root_path,
    )
    msg = InboundMessage(
        channel="nostr",
        sender_id="sender",
        chat_id="sender",
        content="hello",
        metadata={"identity_name": "alice", "identity_target": "identity"},
    )

    loop.audit_event("gateway.identity_route", msg=msg)

    entry = json.loads(loop.audit.path.read_text(encoding="utf-8").splitlines()[0])
    assert entry["identity_name"] == "tal"
    assert entry["routed_identity_name"] == "alice"


def test_system_audit_rotation_uses_system_archive(tmp_path) -> None:
    system_root = tmp_path / "system"
    trail = AuditTrail(system_root, max_bytes=120, max_archives=1)

    trail.record("first", identity_name="tal", detail="x" * 40)
    trail.record("second", identity_name="tal", detail="y" * 40)

    archives = sorted((system_root / "logs" / "archive").glob("audit-*.jsonl"))
    active_entries = [json.loads(line) for line in trail.path.read_text(encoding="utf-8").splitlines()]

    assert len(archives) == 1
    assert trail.path == system_root / "logs" / "audit.jsonl"
    assert [entry["event"] for entry in active_entries] == ["second"]


def test_status_report_reads_system_audit_log(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config = Config.model_validate(
        {
            "root": str(tmp_path),
            "providers": {"anthropic": {"apiKey": "test-key"}},
        }
    )
    save_config(config, config_path)
    bootstrap_beta4_layout(config)
    (config.system_root_path / "logs" / "audit.jsonl").write_text(
        '{"event":"model.switch","identity_name":"owner","ts":"2026-04-29T12:00:00+00:00"}\n',
        encoding="utf-8",
    )

    report = build_status_report(config_path)

    assert report.audit is not None
    assert report.audit.path == str(config.system_root_path / "logs" / "audit.jsonl")
    assert report.audit.exists is True
    assert report.audit.event_count == 1
    assert report.audit.last_event == "model.switch"


def test_audit_command_reads_system_audit_log(monkeypatch, tmp_path) -> None:
    config = Config.model_validate({"root": str(tmp_path)})
    bootstrap_beta4_layout(config)
    (config.system_root_path / "logs" / "audit.jsonl").write_text(
        '{"event":"tool.policy_denied","identity_name":"owner","ts":"2026-04-29T12:00:00+00:00"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("hermitcrab.cli.commands._load_runtime_config", lambda: config)

    result = runner.invoke(app, ["audit"])

    assert result.exit_code == 0
    assert "system" in result.output
    assert "audit.jsonl" in result.output
    assert "tool.policy_denied" in result.output
    assert "identity_name: owner" in result.output
