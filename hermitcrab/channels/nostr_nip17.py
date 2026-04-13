"""Helpers for NIP-17 direct messages built on NIP-44 and NIP-59."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import secrets
import struct
import time
from dataclasses import dataclass
from typing import Any

from pynostr.event import Event
from pynostr.key import PrivateKey

NIP17_CHAT_MESSAGE_KIND = 14
NIP17_GIFT_WRAP_KIND = 1059
NIP59_SEAL_KIND = 13
NIP44_V2_VERSION = 2
_NIP44_SALT = b"nip44-v2"
_NIP17_RANDOMIZED_PAST_SECONDS = 2 * 24 * 60 * 60


class NIP17Error(ValueError):
    """Raised when a NIP-17/NIP-44 payload is invalid."""


@dataclass(slots=True)
class ParsedNIP17Message:
    """Decrypted NIP-17 payload layers for a received direct message."""

    gift_wrap: Event
    seal: Event
    rumor: Event
    sender_pubkey: str
    content: str


def _serialize_event(event: Event) -> str:
    return json.dumps(event.to_dict(), separators=(",", ":"), ensure_ascii=False)


def _parse_event(payload: str, *, require_signature: bool) -> Event:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise NIP17Error("invalid event JSON") from exc
    if not isinstance(data, dict):
        raise NIP17Error("invalid event payload")

    content = data.get("content")
    pubkey = data.get("pubkey")
    created_at = data.get("created_at")
    kind = data.get("kind")
    tags = data.get("tags", [])
    sig = data.get("sig")

    if not isinstance(content, str):
        raise NIP17Error("event content must be a string")
    if not isinstance(pubkey, str):
        raise NIP17Error("event pubkey must be a string")
    if not isinstance(created_at, int):
        raise NIP17Error("event created_at must be an integer")
    if not isinstance(kind, int):
        raise NIP17Error("event kind must be an integer")
    if not isinstance(tags, list):
        raise NIP17Error("event tags must be a list")

    event = Event(
        content=content,
        pubkey=pubkey,
        created_at=created_at,
        kind=kind,
        tags=tags,
        sig=sig if isinstance(sig, str) else None,
    )

    expected_id = data.get("id")
    if isinstance(expected_id, str) and event.id != expected_id:
        raise NIP17Error("event id mismatch")

    if require_signature:
        if not isinstance(sig, str):
            raise NIP17Error("signed event is missing a signature")
        if not event.verify():
            raise NIP17Error("invalid event signature")

    return event


def _hkdf_extract(ikm: bytes, salt: bytes) -> bytes:
    return hmac.new(salt, ikm, hashlib.sha256).digest()


def _hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    blocks: list[bytes] = []
    previous = b""
    counter = 1
    while sum(len(block) for block in blocks) < length:
        previous = hmac.new(prk, previous + info + bytes([counter]), hashlib.sha256).digest()
        blocks.append(previous)
        counter += 1
    return b"".join(blocks)[:length]


def _rotl32(value: int, shift: int) -> int:
    return ((value << shift) & 0xFFFFFFFF) | (value >> (32 - shift))


def _quarter_round(state: list[int], a: int, b: int, c: int, d: int) -> None:
    state[a] = (state[a] + state[b]) & 0xFFFFFFFF
    state[d] ^= state[a]
    state[d] = _rotl32(state[d], 16)

    state[c] = (state[c] + state[d]) & 0xFFFFFFFF
    state[b] ^= state[c]
    state[b] = _rotl32(state[b], 12)

    state[a] = (state[a] + state[b]) & 0xFFFFFFFF
    state[d] ^= state[a]
    state[d] = _rotl32(state[d], 8)

    state[c] = (state[c] + state[d]) & 0xFFFFFFFF
    state[b] ^= state[c]
    state[b] = _rotl32(state[b], 7)


def _chacha20_block(key: bytes, nonce: bytes, counter: int) -> bytes:
    if len(key) != 32:
        raise NIP17Error("invalid ChaCha20 key length")
    if len(nonce) != 12:
        raise NIP17Error("invalid ChaCha20 nonce length")

    state = [
        0x61707865,
        0x3320646E,
        0x79622D32,
        0x6B206574,
        *struct.unpack("<8L", key),
        counter & 0xFFFFFFFF,
        *struct.unpack("<3L", nonce),
    ]
    working = state.copy()
    for _ in range(10):
        _quarter_round(working, 0, 4, 8, 12)
        _quarter_round(working, 1, 5, 9, 13)
        _quarter_round(working, 2, 6, 10, 14)
        _quarter_round(working, 3, 7, 11, 15)
        _quarter_round(working, 0, 5, 10, 15)
        _quarter_round(working, 1, 6, 11, 12)
        _quarter_round(working, 2, 7, 8, 13)
        _quarter_round(working, 3, 4, 9, 14)

    output = [(left + right) & 0xFFFFFFFF for left, right in zip(working, state, strict=True)]
    return struct.pack("<16L", *output)


def _chacha20_xor(key: bytes, nonce: bytes, data: bytes) -> bytes:
    result = bytearray()
    counter = 0
    for offset in range(0, len(data), 64):
        block = data[offset : offset + 64]
        keystream = _chacha20_block(key, nonce, counter)
        result.extend(byte ^ key_byte for byte, key_byte in zip(block, keystream, strict=False))
        counter += 1
    return bytes(result)


def calc_padded_len(unpadded_len: int) -> int:
    if unpadded_len < 1 or unpadded_len > 0xFFFF:
        raise NIP17Error("invalid plaintext length")
    if unpadded_len <= 32:
        return 32

    next_power = 1 << (math.floor(math.log2(unpadded_len - 1)) + 1)
    chunk = 32 if next_power <= 256 else next_power // 8
    return chunk * (((unpadded_len - 1) // chunk) + 1)


def _pad_plaintext(plaintext: str) -> bytes:
    unpadded = plaintext.encode("utf-8")
    unpadded_len = len(unpadded)
    padded_len = calc_padded_len(unpadded_len)
    return unpadded_len.to_bytes(2, "big") + unpadded + bytes(padded_len - unpadded_len)


def _unpad_plaintext(padded: bytes) -> str:
    if len(padded) < 34:
        raise NIP17Error("invalid padded plaintext")
    unpadded_len = int.from_bytes(padded[:2], "big")
    unpadded = padded[2 : 2 + unpadded_len]
    if (
        unpadded_len == 0
        or len(unpadded) != unpadded_len
        or len(padded) != 2 + calc_padded_len(unpadded_len)
    ):
        raise NIP17Error("invalid padding")
    return unpadded.decode("utf-8")


def get_conversation_key(private_key_hex: str, public_key_hex: str) -> bytes:
    private_key = PrivateKey.from_hex(private_key_hex)
    shared_x = private_key.compute_shared_secret(public_key_hex)
    return _hkdf_extract(shared_x, _NIP44_SALT)


def _get_message_keys(conversation_key: bytes, nonce: bytes) -> tuple[bytes, bytes, bytes]:
    if len(conversation_key) != 32:
        raise NIP17Error("invalid conversation key length")
    if len(nonce) != 32:
        raise NIP17Error("invalid NIP-44 nonce length")
    keys = _hkdf_expand(conversation_key, nonce, 76)
    return keys[:32], keys[32:44], keys[44:76]


def nip44_encrypt(
    plaintext: str,
    *,
    private_key_hex: str,
    public_key_hex: str,
    nonce: bytes | None = None,
) -> str:
    message_nonce = nonce or secrets.token_bytes(32)
    conversation_key = get_conversation_key(private_key_hex, public_key_hex)
    chacha_key, chacha_nonce, hmac_key = _get_message_keys(conversation_key, message_nonce)
    padded = _pad_plaintext(plaintext)
    ciphertext = _chacha20_xor(chacha_key, chacha_nonce, padded)
    mac = hmac.new(hmac_key, message_nonce + ciphertext, hashlib.sha256).digest()
    payload = bytes([NIP44_V2_VERSION]) + message_nonce + ciphertext + mac
    return base64.b64encode(payload).decode("ascii")


def nip44_decrypt(
    payload: str,
    *,
    private_key_hex: str,
    public_key_hex: str,
) -> str:
    if not payload or payload[0] == "#":
        raise NIP17Error("unsupported NIP-44 payload version")
    if len(payload) < 132 or len(payload) > 87472:
        raise NIP17Error("invalid NIP-44 payload size")

    try:
        decoded = base64.b64decode(payload, validate=True)
    except Exception as exc:
        raise NIP17Error("invalid NIP-44 payload encoding") from exc

    if len(decoded) < 99 or len(decoded) > 65603:
        raise NIP17Error("invalid NIP-44 decoded payload size")
    if decoded[0] != NIP44_V2_VERSION:
        raise NIP17Error(f"unsupported NIP-44 version {decoded[0]}")

    nonce = decoded[1:33]
    ciphertext = decoded[33:-32]
    mac = decoded[-32:]
    conversation_key = get_conversation_key(private_key_hex, public_key_hex)
    chacha_key, chacha_nonce, hmac_key = _get_message_keys(conversation_key, nonce)
    expected_mac = hmac.new(hmac_key, nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(expected_mac, mac):
        raise NIP17Error("invalid NIP-44 MAC")
    padded_plaintext = _chacha20_xor(chacha_key, chacha_nonce, ciphertext)
    return _unpad_plaintext(padded_plaintext)


def randomized_past_timestamp(now: int | None = None) -> int:
    current = int(time.time()) if now is None else now
    return current - secrets.randbelow(_NIP17_RANDOMIZED_PAST_SECONDS + 1)


def randomized_past_window_seconds() -> int:
    """Return the NIP-17 timestamp randomization window."""
    return _NIP17_RANDOMIZED_PAST_SECONDS


def build_nip17_message(
    *,
    sender_private_key_hex: str,
    recipient_pubkey_hex: str,
    content: str,
    now: int | None = None,
    relay_hint: str | None = None,
    include_sender_copy: bool = True,
    rumor_nonce: bytes | None = None,
    seal_nonce: bytes | None = None,
    sender_copy_rumor_nonce: bytes | None = None,
    sender_copy_seal_nonce: bytes | None = None,
    recipient_wrapper_private_key_hex: str | None = None,
    sender_wrapper_private_key_hex: str | None = None,
    rumor_created_at: int | None = None,
    recipient_seal_created_at: int | None = None,
    recipient_wrap_created_at: int | None = None,
    sender_seal_created_at: int | None = None,
    sender_wrap_created_at: int | None = None,
) -> list[Event]:
    sender_private_key = PrivateKey.from_hex(sender_private_key_hex)
    sender_pubkey = sender_private_key.public_key.hex()

    rumor = Event(
        content=content,
        pubkey=sender_pubkey,
        created_at=rumor_created_at or int(time.time() if now is None else now),
        kind=NIP17_CHAT_MESSAGE_KIND,
        tags=[["p", recipient_pubkey_hex, relay_hint]] if relay_hint else [["p", recipient_pubkey_hex]],
    )
    rumor.sig = None

    recipient_events = [
        _build_wrapped_events(
            rumor=rumor,
            sender_private_key_hex=sender_private_key_hex,
            recipient_pubkey_hex=recipient_pubkey_hex,
            wrapper_private_key_hex=recipient_wrapper_private_key_hex,
            rumor_nonce=rumor_nonce,
            seal_nonce=seal_nonce,
            seal_created_at=recipient_seal_created_at,
            wrap_created_at=recipient_wrap_created_at,
            now=now,
            relay_hint=relay_hint,
        )
    ]

    if include_sender_copy:
        recipient_events.append(
            _build_wrapped_events(
                rumor=rumor,
                sender_private_key_hex=sender_private_key_hex,
                recipient_pubkey_hex=sender_pubkey,
                wrapper_private_key_hex=sender_wrapper_private_key_hex,
                rumor_nonce=sender_copy_rumor_nonce,
                seal_nonce=sender_copy_seal_nonce,
                seal_created_at=sender_seal_created_at,
                wrap_created_at=sender_wrap_created_at,
                now=now,
            )
        )

    return recipient_events


def _build_wrapped_events(
    *,
    rumor: Event,
    sender_private_key_hex: str,
    recipient_pubkey_hex: str,
    wrapper_private_key_hex: str | None,
    rumor_nonce: bytes | None,
    seal_nonce: bytes | None,
    seal_created_at: int | None,
    wrap_created_at: int | None,
    now: int | None,
    relay_hint: str | None = None,
) -> Event:
    seal = Event(
        content=nip44_encrypt(
            _serialize_event(rumor),
            private_key_hex=sender_private_key_hex,
            public_key_hex=recipient_pubkey_hex,
            nonce=rumor_nonce,
        ),
        pubkey=PrivateKey.from_hex(sender_private_key_hex).public_key.hex(),
        created_at=seal_created_at or randomized_past_timestamp(now),
        kind=NIP59_SEAL_KIND,
        tags=[],
    )
    seal.sign(sender_private_key_hex)

    wrapper_private_key_hex = wrapper_private_key_hex or PrivateKey().hex()
    gift_wrap = Event(
        content=nip44_encrypt(
            _serialize_event(seal),
            private_key_hex=wrapper_private_key_hex,
            public_key_hex=recipient_pubkey_hex,
            nonce=seal_nonce,
        ),
        pubkey=PrivateKey.from_hex(wrapper_private_key_hex).public_key.hex(),
        created_at=wrap_created_at or randomized_past_timestamp(now),
        kind=NIP17_GIFT_WRAP_KIND,
        tags=[["p", recipient_pubkey_hex, relay_hint]] if relay_hint else [["p", recipient_pubkey_hex]],
    )
    gift_wrap.sign(wrapper_private_key_hex)
    return gift_wrap


def parse_nip17_message(
    gift_wrap_event: dict[str, Any] | Event,
    *,
    recipient_private_key_hex: str,
) -> ParsedNIP17Message:
    gift_wrap = (
        gift_wrap_event
        if isinstance(gift_wrap_event, Event)
        else _parse_event(json.dumps(gift_wrap_event), require_signature=True)
    )
    if gift_wrap.kind != NIP17_GIFT_WRAP_KIND:
        raise NIP17Error("event is not a NIP-17 gift wrap")

    seal_payload = nip44_decrypt(
        gift_wrap.content or "",
        private_key_hex=recipient_private_key_hex,
        public_key_hex=gift_wrap.pubkey or "",
    )
    seal = _parse_event(seal_payload, require_signature=True)
    if seal.kind != NIP59_SEAL_KIND:
        raise NIP17Error("gift wrap did not contain a seal")
    if seal.tags:
        raise NIP17Error("seal tags must be empty")

    rumor_payload = nip44_decrypt(
        seal.content or "",
        private_key_hex=recipient_private_key_hex,
        public_key_hex=seal.pubkey or "",
    )
    rumor = _parse_event(rumor_payload, require_signature=False)
    if rumor.kind != NIP17_CHAT_MESSAGE_KIND:
        raise NIP17Error("unsupported NIP-17 rumor kind")
    if rumor.sig is not None:
        raise NIP17Error("NIP-17 rumor must be unsigned")
    if rumor.pubkey != seal.pubkey:
        raise NIP17Error("seal sender does not match rumor sender")

    return ParsedNIP17Message(
        gift_wrap=gift_wrap,
        seal=seal,
        rumor=rumor,
        sender_pubkey=rumor.pubkey or "",
        content=rumor.content or "",
    )
