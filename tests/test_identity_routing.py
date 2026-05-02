"""Focused regressions for gateway identity routing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from hermitcrab.bus.events import InboundMessage
from hermitcrab.bus.queue import MessageBus
from hermitcrab.channels.nostr import NostrChannel
from hermitcrab.cli.commands import (
    GatewayIdentityRuntimeState,
    _resolve_gateway_identity_route,
)
from hermitcrab.config.schema import Config, NostrConfig
from hermitcrab.cron.service import CronService


def _pubkey() -> str:
    from pynostr.key import PrivateKey

    return PrivateKey().public_key.hex()


def _provider() -> MagicMock:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    return provider


def _bootstrap_identity(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "IDENTITY.md").write_text("# Identity\n", encoding="utf-8")


def test_nostr_sender_binding_resolves_to_active_identity(tmp_path: Path) -> None:
    sender = _pubkey()
    config = Config.model_validate(
        {
            "root": str(tmp_path),
            "identities": {"registry": {"alice": {}}},
            "channels": {"nostr": {"identityBindings": {"alice": [sender]}}},
        }
    )

    resolution = config.resolve_nostr_sender_identity(sender)

    assert resolution.target == "identity"
    assert resolution.identity_name == "alice"
    assert resolution.identity_path == tmp_path / "identities" / "alice"
    assert resolution.reason == "identity_binding"


def test_allowed_unbound_nostr_sender_falls_back_to_owner_identity(tmp_path: Path) -> None:
    sender = _pubkey()
    config = Config.model_validate(
        {
            "root": str(tmp_path),
            "identities": {"ownerIdentity": "tal"},
            "channels": {"nostr": {"allowedPubkeys": [sender]}},
        }
    )

    resolution = config.resolve_nostr_sender_identity(sender)

    assert resolution.target == "identity"
    assert resolution.identity_name == "tal"
    assert resolution.identity_path == tmp_path / "identities" / "tal"
    assert resolution.reason == "allowlist_owner_fallback"


def test_bound_nostr_sender_to_inactive_identity_is_denied(tmp_path: Path) -> None:
    sender = _pubkey()
    config = Config.model_validate(
        {
            "root": str(tmp_path),
            "identities": {"registry": {"alice": {"active": False}}},
            "channels": {"nostr": {"identityBindings": {"alice": [sender]}}},
        }
    )

    resolution = config.resolve_nostr_sender_identity(sender)

    assert resolution.target == "denied"
    assert resolution.identity_name == "alice"
    assert resolution.reason == "identity_inactive"


def test_nostr_channel_emits_identity_metadata_and_session_key(tmp_path: Path) -> None:
    from pynostr.key import PrivateKey

    sender = _pubkey()
    private_key = PrivateKey().hex()
    config = Config.model_validate(
        {
            "root": str(tmp_path),
            "identities": {"registry": {"alice": {}}},
            "channels": {
                "nostr": {
                    "privateKey": private_key,
                    "identityBindings": {"alice": [sender]},
                }
            },
        }
    )
    channel = NostrChannel(
        NostrConfig(private_key=private_key),
        MessageBus(),
        identity_resolver=config.resolve_nostr_sender_identity,
    )

    metadata = channel._identity_metadata(sender)
    session_key = channel._session_key_for_sender(sender, metadata)

    assert metadata["identity_target"] == "identity"
    assert metadata["identity_name"] == "alice"
    assert metadata["identity_reason"] == "identity_binding"
    assert session_key == f"nostr:alice:{sender}"
    assert channel._is_sender_allowed(sender) is True


def test_gateway_route_decision_denies_unresolved_nostr_identity() -> None:
    msg = InboundMessage(
        channel="nostr",
        sender_id="sender",
        chat_id="sender",
        content="hello",
        metadata={"identity_target": "denied", "identity_reason": "not_allowed"},
    )

    route = _resolve_gateway_identity_route(msg, owner_identity_name="owner")

    assert route.target == "denied"
    assert route.reason == "channel_metadata_denied"


@pytest.mark.asyncio
async def test_gateway_reuses_active_identity_agent(tmp_path: Path) -> None:
    class Channels:
        enabled_channels: list[str] = []

    config = Config.model_validate({"root": str(tmp_path), "identities": {"registry": {"alice": {}}}})
    _bootstrap_identity(config.get_identity_path("alice"))
    state = GatewayIdentityRuntimeState(
        config=config,
        bus=MessageBus(),
        channels=Channels(),
        create_provider=_provider,
        cron_service_factory=lambda **kwargs: CronService(
            kwargs["identity_root"] / "cron" / "jobs.json",
            identity_name=kwargs["identity_name"],
            conflict_finder=kwargs["conflict_finder"],
        ),
        heartbeat_service_factory=MagicMock(),
        on_reminder_notify=MagicMock(),
        heartbeat_interval_s=60,
        heartbeat_enabled=True,
        reminder_interval_s=60,
        reminder_service_factory=MagicMock(),
        agents={},
        cron_services={},
        heartbeat_services={},
        reminder_services={},
    )

    first = await state.get_or_create_agent("alice")
    second = await state.get_or_create_agent("alice")

    assert second is first
    assert first.identity_name == "alice"
    assert first.identity_root == tmp_path / "identities" / "alice"
