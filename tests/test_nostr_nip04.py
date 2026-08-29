"""Focused regressions for NIP-04 signature verification."""

from __future__ import annotations

from pynostr.key import PrivateKey

from hermitcrab.bus.queue import MessageBus
from hermitcrab.channels.nostr import NostrChannel
from hermitcrab.config.schema import NostrConfig


def _signed_nip04_event(sender_priv: PrivateKey, recipient_pubkey_hex: str, content: str) -> dict:
    channel = NostrChannel(NostrConfig(private_key=PrivateKey().hex(), protocol="nip04"), MessageBus())
    dm = channel.EncryptedDirectMessage()
    dm.encrypt(
        private_key_hex=sender_priv.hex(),
        recipient_pubkey=recipient_pubkey_hex,
        cleartext_content=content,
    )
    event = dm.to_event()
    event.sign(sender_priv.hex())
    return event.to_dict()


def test_nip04_signed_event_is_accepted() -> None:
    sender_priv = PrivateKey()
    channel_priv = PrivateKey()
    channel = NostrChannel(
        NostrConfig(private_key=channel_priv.hex(), protocol="nip04", allowed_pubkeys=["*"]),
        MessageBus(),
    )

    event = _signed_nip04_event(sender_priv, channel.our_pubkey_hex, "hello from a signed sender")

    sender_pubkey, content, _ = channel._handle_nip04_event(event=event, event_id=event["id"])

    assert sender_pubkey == sender_priv.public_key.hex().lower()
    assert content == "hello from a signed sender"


def test_nip04_event_with_forged_pubkey_is_rejected() -> None:
    sender_priv = PrivateKey()
    channel_priv = PrivateKey()
    channel = NostrChannel(
        NostrConfig(private_key=channel_priv.hex(), protocol="nip04", allowed_pubkeys=["*"]),
        MessageBus(),
    )

    event = _signed_nip04_event(sender_priv, channel.our_pubkey_hex, "spoof attempt")
    event["pubkey"] = PrivateKey().public_key.hex()

    sender_pubkey, content, _ = channel._handle_nip04_event(event=event, event_id=event["id"])

    assert sender_pubkey is None
    assert content is None
