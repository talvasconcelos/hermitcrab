"""Focused regressions for gateway identity routing."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from hermitcrab.bus.events import InboundMessage, OutboundMessage
from hermitcrab.bus.queue import MessageBus
from hermitcrab.channels.nostr import NostrChannel, _split_message
from hermitcrab.channels.nostr_nip17 import build_nip17_message
from hermitcrab.cli.commands import (
    GatewayIdentityRuntimeState,
    _resolve_gateway_identity_route,
    _run_gateway_inbound_router,
)
from hermitcrab.config.schema import Config, NostrConfig
from hermitcrab.cron.service import CronService


def _pubkey() -> str:
    from pynostr.key import PrivateKey

    return PrivateKey().public_key.hex()


def _private_key() -> str:
    from pynostr.key import PrivateKey

    return PrivateKey().hex()


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


@pytest.mark.asyncio
async def test_gateway_router_does_not_block_other_identities() -> None:
    class FakeBus:
        def __init__(self) -> None:
            self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
            self.outbound: list[OutboundMessage] = []
            self.published = asyncio.Event()

        async def consume_inbound(self) -> InboundMessage:
            return await self.inbound.get()

        async def publish_outbound(self, msg: OutboundMessage) -> None:
            self.outbound.append(msg)
            self.published.set()

    class FakeAgent:
        def __init__(self, identity_name: str) -> None:
            self.identity_name = identity_name
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.audit_events: list[str] = []

        def audit_event(self, event: str, **_: object) -> None:
            self.audit_events.append(event)

        async def handle_inbound(self, msg: InboundMessage) -> OutboundMessage:
            self.started.set()
            await self.release.wait()
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=f"{self.identity_name} response",
            )

    bus = FakeBus()
    owner = FakeAgent("owner")
    paula = FakeAgent("paula")
    agents = {"owner": owner, "paula": paula}

    async def get_or_create_agent(name: str) -> FakeAgent:
        return agents[name]

    def identity_agent_key(name: str) -> str:
        return f"identity:{name}"

    await bus.inbound.put(
        InboundMessage(
            channel="nostr",
            sender_id="owner-pubkey",
            chat_id="owner-pubkey",
            content="slow owner request",
            metadata={"identity_target": "identity", "identity_name": "owner"},
            session_key_override="nostr:owner:owner-pubkey",
        )
    )
    await bus.inbound.put(
        InboundMessage(
            channel="nostr",
            sender_id="paula-pubkey",
            chat_id="paula-pubkey",
            content="independent paula request",
            metadata={"identity_target": "identity", "identity_name": "paula"},
            session_key_override="nostr:paula:paula-pubkey",
        )
    )

    router = asyncio.create_task(
        _run_gateway_inbound_router(
            bus=bus,
            owner_agent=owner,
            get_or_create_agent=get_or_create_agent,
            identity_agent_key=identity_agent_key,
        )
    )
    try:
        await asyncio.wait_for(owner.started.wait(), timeout=1)
        await asyncio.wait_for(paula.started.wait(), timeout=1)

        paula.release.set()
        await asyncio.wait_for(bus.published.wait(), timeout=1)

        assert [msg.content for msg in bus.outbound] == ["paula response"]
    finally:
        router.cancel()
        with pytest.raises(asyncio.CancelledError):
            await router


@pytest.mark.asyncio
async def test_nip17_send_publishes_only_recipient_wrap(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeEvent:
        tags = [["p", _pubkey()]]

        def to_message(self) -> str:
            return "event"

    calls: list[dict[str, object]] = []

    def fake_build_nip17_message(**kwargs):
        calls.append(kwargs)
        return [FakeEvent()]

    channel = NostrChannel(
        NostrConfig(private_key=_private_key(), protocol="nip17"),
        MessageBus(),
    )
    channel._running = True
    channel._relay_targets_for_nip17_recipient = AsyncMock(return_value=["wss://relay.example"])
    channel._publish_event_to_relays = AsyncMock()
    monkeypatch.setattr("hermitcrab.channels.nostr.build_nip17_message", fake_build_nip17_message)

    await channel.send(
        OutboundMessage(
            channel="nostr",
            chat_id=_pubkey(),
            content="hello",
        )
    )

    assert calls
    assert calls[0]["include_sender_copy"] is False
    channel._publish_event_to_relays.assert_awaited_once()


def test_nostr_message_split_keeps_medium_replies_together() -> None:
    assert _split_message("x" * 3000) == ["x" * 3000]


@pytest.mark.asyncio
async def test_nip17_events_are_processed_before_relay_eose() -> None:
    channel = NostrChannel(
        NostrConfig(private_key=_private_key(), protocol="nip17"),
        MessageBus(),
    )
    channel._handle_event = AsyncMock()

    await channel._process_relay_message(
        "wss://relay.example",
        [
            "EVENT",
            channel._subscription_id,
            {"id": "event", "kind": 1059, "tags": [["p", channel.our_pubkey_hex]]},
        ],
    )

    channel._handle_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_nip17_randomized_wrapper_timestamp_before_start_still_routes() -> None:
    sender_private_key = _private_key()
    channel_private_key = _private_key()
    channel = NostrChannel(
        NostrConfig(private_key=channel_private_key, protocol="nip17", allowed_pubkeys=["*"]),
        MessageBus(),
    )
    channel._listen_start = 2_000
    channel._handle_inbound_message = AsyncMock()
    event = build_nip17_message(
        sender_private_key_hex=sender_private_key,
        recipient_pubkey_hex=channel.our_pubkey_hex,
        content="new message with older wrapper timestamp",
        include_sender_copy=False,
        rumor_created_at=2_001,
        recipient_seal_created_at=1_000,
        recipient_wrap_created_at=1_000,
    )[0]

    await channel._handle_event("wss://relay.example", event.to_dict())

    channel._handle_inbound_message.assert_awaited_once()
    metadata = channel._handle_inbound_message.await_args.kwargs["metadata"]
    assert metadata["created_at"] == 2_001
    assert metadata["rumor_created_at"] == 2_001
    assert metadata["seal_created_at"] == 1_000
    assert metadata["gift_wrap_created_at"] == 1_000


@pytest.mark.asyncio
async def test_nip17_startup_catchup_skips_messages_before_gateway_start(tmp_path: Path) -> None:
    sender_private_key = _private_key()
    channel_private_key = _private_key()
    channel = NostrChannel(
        NostrConfig(private_key=channel_private_key, protocol="nip17", allowed_pubkeys=["*"]),
        MessageBus(),
        processed_store_path=tmp_path / "nostr" / "processed-events.jsonl",
    )
    channel._startup_catchup_min_created_at = 2_000
    channel._handle_inbound_message = AsyncMock()
    old_event = build_nip17_message(
        sender_private_key_hex=sender_private_key,
        recipient_pubkey_hex=channel.our_pubkey_hex,
        content="already handled before restart",
        include_sender_copy=False,
        rumor_created_at=1_999,
    )[0]

    await channel._handle_event("startup-catchup", old_event.to_dict())

    channel._handle_inbound_message.assert_not_awaited()
    processed_lines = (tmp_path / "nostr" / "processed-events.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(processed_lines) == 1
    processed = json.loads(processed_lines[0])
    assert processed["canonical_id"].startswith("nip17:")
    assert processed["created_at"] == 1_999


@pytest.mark.asyncio
async def test_nip17_startup_catchup_routes_messages_at_gateway_start_second(
    tmp_path: Path,
) -> None:
    sender_private_key = _private_key()
    channel_private_key = _private_key()
    processed_store_path = tmp_path / "nostr" / "processed-events.jsonl"
    channel = NostrChannel(
        NostrConfig(private_key=channel_private_key, protocol="nip17", allowed_pubkeys=["*"]),
        MessageBus(),
        processed_store_path=processed_store_path,
    )
    channel._startup_catchup_min_created_at = 2_000
    channel._handle_inbound_message = AsyncMock()
    event = build_nip17_message(
        sender_private_key_hex=sender_private_key,
        recipient_pubkey_hex=channel.our_pubkey_hex,
        content="sent in gateway start second",
        include_sender_copy=False,
        rumor_created_at=2_000,
    )[0]

    await channel._handle_event("startup-catchup", event.to_dict())

    channel._handle_inbound_message.assert_awaited_once()
    assert channel._handle_inbound_message.await_args.kwargs["content"] == (
        "sent in gateway start second"
    )
    processed_lines = processed_store_path.read_text(encoding="utf-8").splitlines()
    assert len(processed_lines) == 1
    processed = json.loads(processed_lines[0])
    assert processed["created_at"] == 2_000


@pytest.mark.asyncio
async def test_nip17_live_relay_messages_are_not_dropped_by_gateway_start_cutoff(tmp_path: Path) -> None:
    sender_private_key = _private_key()
    channel_private_key = _private_key()
    channel = NostrChannel(
        NostrConfig(private_key=channel_private_key, protocol="nip17", allowed_pubkeys=["*"]),
        MessageBus(),
        processed_store_path=tmp_path / "nostr" / "processed-events.jsonl",
    )
    channel._startup_catchup_min_created_at = 2_000
    channel._handle_inbound_message = AsyncMock()
    old_event = build_nip17_message(
        sender_private_key_hex=sender_private_key,
        recipient_pubkey_hex=channel.our_pubkey_hex,
        content="relay replay after restart",
        include_sender_copy=False,
        rumor_created_at=1_999,
    )[0]
    new_event = build_nip17_message(
        sender_private_key_hex=sender_private_key,
        recipient_pubkey_hex=channel.our_pubkey_hex,
        content="sent after gateway restart",
        include_sender_copy=False,
        rumor_created_at=2_001,
        recipient_wrap_created_at=1_000,
    )[0]

    await channel._handle_event("wss://relay.example", old_event.to_dict())
    await channel._handle_event("wss://relay.example", new_event.to_dict())

    assert channel._handle_inbound_message.await_count == 2
    contents = [call.kwargs["content"] for call in channel._handle_inbound_message.await_args_list]
    assert contents == ["relay replay after restart", "sent after gateway restart"]
