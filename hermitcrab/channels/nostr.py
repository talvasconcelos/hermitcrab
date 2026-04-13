"""Nostr channel using WebSocket + pynostr for NIP-04 encrypted DMs."""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

import websockets
from loguru import logger
from websockets.asyncio.client import ClientConnection

from hermitcrab.bus.events import OutboundMessage
from hermitcrab.bus.queue import MessageBus
from hermitcrab.channels.base import BaseChannel
from hermitcrab.channels.nostr_nip17 import (
    NIP17_GIFT_WRAP_KIND,
    NIP17Error,
    build_nip17_message,
    parse_nip17_message,
    randomized_past_window_seconds,
)
from hermitcrab.config.schema import NostrConfig, default_nostr_relays


def _split_message(content: str, max_len: int = 1800) -> list[str]:
    """Split long outbound messages into relay/client-friendlier chunks."""
    if len(content) <= max_len:
        return [content]

    chunks: list[str] = []
    while content:
        if len(content) <= max_len:
            chunks.append(content)
            break
        cut = content[:max_len]
        pos = cut.rfind("\n")
        if pos == -1:
            pos = cut.rfind(" ")
        if pos == -1:
            pos = max_len
        chunks.append(content[:pos])
        content = content[pos:].lstrip()
    return chunks


class NostrChannel(BaseChannel):
    """Nostr channel for NIP-04 encrypted DMs using WebSocket connections."""

    name = "nostr"
    _HEX_PUBKEY_RE = re.compile(r"^[0-9a-fA-F]{64}$")
    _RECONNECT_DELAY_SECONDS = 2
    _MAX_RECONNECT_DELAY_SECONDS = 30
    _MAX_SEEN_EVENTS = 10_000
    _NIP17_RELAY_LIST_KIND = 10050

    def __init__(self, config: NostrConfig, bus: MessageBus) -> None:
        super().__init__(config, bus)
        self.config: NostrConfig = config

        try:
            from pynostr.encrypted_dm import EncryptedDirectMessage
            from pynostr.event import Event, EventKind
            from pynostr.key import PrivateKey, PublicKey

            self.EncryptedDirectMessage = EncryptedDirectMessage
            self.Event = Event
            self.EventKind = EventKind
            self.PrivateKey = PrivateKey
            self.PublicKey = PublicKey
        except ImportError as e:
            logger.error("pynostr not installed. Run: pip install pynostr")
            raise ImportError("pynostr is required for Nostr channel") from e

        self._private_key = self._load_private_key(config.private_key)
        self._hex_priv = self._private_key.hex()
        self._hex_pub = self._private_key.public_key.hex()
        self._allowed_pubkeys: set[str] = self._normalize_allowed_pubkeys(config.allowed_pubkeys)

        self._ws_connections: dict[str, ClientConnection] = {}
        self._ws_lock = asyncio.Lock()
        self._relay_reconnect_delay: dict[str, int] = {}
        self._subscription_id = f"hermitcrab-{int(time.time())}"
        self._running = False
        self._receive_task: asyncio.Task | None = None
        self._seen_event_ids: set[str] = set()
        self._listen_start: int = 0
        self._nip17_relay_cache: dict[str, tuple[float, list[str]]] = {}
        self._active_relays: list[str] = self._normalize_relay_urls(config.relays) or default_nostr_relays()

        logger.info("Nostr channel initialized (pubkey: {}...)", self._hex_pub[:8])

    def _load_private_key(self, key: str | None) -> Any:
        if not key:
            logger.warning("Nostr private key not provided, generating new keypair")
            new_key = self.PrivateKey()
            logger.warning("Generated new Nostr keypair. Save this nsec key: {}", new_key.bech32())
            return new_key

        key = key.strip()
        if key.startswith("npub"):
            raise ValueError("Nostr private_key cannot be npub; use nsec or hex")

        try:
            if key.startswith("nsec"):
                return self.PrivateKey.from_nsec(key)
            return self.PrivateKey.from_hex(key)
        except Exception as e:
            raise ValueError(f"Invalid Nostr private key format: {e}") from e

    def _normalize_allowed_pubkeys(self, pubkeys: list[str]) -> set[str]:
        normalized: set[str] = set()
        for pk in pubkeys:
            pk = pk.strip()
            if pk.lower() in ("*", "all"):
                logger.info("Nostr allowlist set to '*' - open mode")
                return {"*"}
            try:
                hex_pk = self._normalize_pubkey_to_hex(pk)
                normalized.add(hex_pk)
            except Exception as e:
                logger.warning("Invalid pubkey format '{}', skipping: {}", pk, e)
        if not normalized:
            logger.info("Nostr allowlist is empty - denying all DMs. Set ['*'] for open mode.")
        return normalized

    def _normalize_pubkey_to_hex(self, pubkey: str) -> str:
        """Normalize npub/hex pubkey into canonical lowercase 64-char hex."""
        value = pubkey.strip()
        if value.startswith("npub"):
            return self.PublicKey.from_npub(value).hex().lower()
        if self._HEX_PUBKEY_RE.fullmatch(value):
            return value.lower()
        raise ValueError("pubkey must be npub or 64-char hex")

    def _protocol(self) -> str:
        config = getattr(self, "config", None)
        return getattr(config, "protocol", "nip04")

    def _normalize_relay_urls(self, relays: list[str]) -> list[str]:
        normalized: list[str] = []
        for relay in relays:
            relay_url = relay.strip()
            if not relay_url or not relay_url.startswith("ws"):
                continue
            if relay_url not in normalized:
                normalized.append(relay_url)
        return normalized

    def _desired_relays(self) -> list[str]:
        config = getattr(self, "config", None)
        return self._normalize_relay_urls(list(getattr(config, "relays", [])))

    def _configured_relays(self) -> list[str]:
        return list(self._active_relays)

    def _bootstrap_relays(self) -> list[str]:
        desired = self._desired_relays()
        return desired or default_nostr_relays()

    async def _connect_to_relays(self) -> None:
        for relay_url in self._configured_relays():
            await self._connect_to_relay(relay_url)

    async def _connect_to_relay(self, relay_url: str) -> bool:
        old_ws: ClientConnection | None = None
        try:
            logger.debug("Connecting to relay: {}", relay_url)
            ws = await websockets.connect(
                relay_url,
                open_timeout=15,
                close_timeout=10,
                ping_interval=20,
                ping_timeout=20,
            )
            async with self._ws_lock:
                old_ws = self._ws_connections.get(relay_url)
                self._ws_connections[relay_url] = ws
            logger.info("Connected to relay: {}", relay_url)
            return True
        except Exception as e:
            logger.warning("Failed to connect to relay {}: {}", relay_url, e)
            async with self._ws_lock:
                self._ws_connections.pop(relay_url, None)
            return False
        finally:
            if old_ws is not None:
                try:
                    await old_ws.close()
                except Exception:
                    pass

    async def _connection_snapshot(self) -> list[tuple[str, ClientConnection]]:
        async with self._ws_lock:
            return list(self._ws_connections.items())

    async def _connection_for_relay(self, relay_url: str) -> ClientConnection | None:
        async with self._ws_lock:
            return self._ws_connections.get(relay_url)

    async def _remove_connection_if_same(self, relay_url: str, ws: ClientConnection) -> None:
        async with self._ws_lock:
            current = self._ws_connections.get(relay_url)
            if current is ws:
                self._ws_connections.pop(relay_url, None)

    def _relay_discovery_timeout_s(self) -> float:
        config = getattr(self, "config", None)
        return float(getattr(config, "nip17_relay_discovery_timeout_s", 4.0))

    def _relay_cache_ttl_s(self) -> int:
        config = getattr(self, "config", None)
        return int(getattr(config, "nip17_relay_cache_ttl_s", 600))

    def _nip17_fallback_to_configured_relays(self) -> bool:
        config = getattr(self, "config", None)
        return bool(getattr(config, "nip17_fallback_to_configured_relays", True))

    def _extract_p_tag_pubkey(self, tags: list[Any]) -> str | None:
        for tag in tags:
            if not isinstance(tag, list) or len(tag) < 2 or tag[0] != "p":
                continue
            try:
                return self._normalize_pubkey_to_hex(str(tag[1]))
            except Exception:
                continue
        return None

    def _extract_relay_tags(self, event: dict[str, Any]) -> list[str]:
        relays = [tag[1] for tag in event.get("tags", []) if isinstance(tag, list) and len(tag) >= 2 and tag[0] == "relay" and isinstance(tag[1], str)]
        return self._normalize_relay_urls(relays)

    async def _fetch_own_kind10050_relays(self, relay_urls: list[str]) -> list[str]:
        latest_event: dict[str, Any] | None = None
        latest_created_at = -1
        for relay_url in relay_urls:
            event = await self._fetch_latest_event_from_relay(
                relay_url,
                authors=[self._hex_pub],
                kinds=[self._NIP17_RELAY_LIST_KIND],
            )
            if (
                event is not None
                and isinstance(event.get("created_at"), int)
                and int(event["created_at"]) >= latest_created_at
            ):
                latest_event = event
                latest_created_at = int(event["created_at"])
        if latest_event is None:
            return []
        return self._extract_relay_tags(latest_event)

    async def _fetch_latest_event_from_relay(
        self,
        relay_url: str,
        *,
        authors: list[str],
        kinds: list[int],
    ) -> dict[str, Any] | None:
        subscription_id = f"{self._subscription_id}-{kinds[0]}-{int(time.time() * 1000)}"
        filter_dict = {"authors": authors, "kinds": kinds, "limit": 1}
        try:
            async with websockets.connect(
                relay_url,
                open_timeout=15,
                close_timeout=10,
                ping_interval=20,
                ping_timeout=20,
            ) as ws:
                await ws.send(json.dumps(["REQ", subscription_id, filter_dict]))
                deadline = time.time() + self._relay_discovery_timeout_s()
                latest_event: dict[str, Any] | None = None

                while time.time() < deadline:
                    timeout = max(0.1, deadline - time.time())
                    raw_message = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    data = json.loads(raw_message)
                    if not isinstance(data, list) or len(data) < 2:
                        continue
                    if data[0] == "EVENT" and len(data) >= 3 and data[1] == subscription_id:
                        event = data[2]
                        if isinstance(event, dict) and event.get("kind") in kinds:
                            latest_event = event
                    if data[0] == "EOSE" and data[1] == subscription_id:
                        break

                await ws.send(json.dumps(["CLOSE", subscription_id]))
                return latest_event
        except asyncio.TimeoutError:
            logger.debug("Timed out fetching kinds={} on {}", kinds, relay_url)
            return None
        except Exception as e:
            logger.debug("Failed fetching kinds={} on {}: {}", kinds, relay_url, e)
            return None

    async def _discover_recipient_relays(self, recipient_pubkey: str) -> list[str]:
        now = time.time()
        cached = self._nip17_relay_cache.get(recipient_pubkey)
        if cached and cached[0] > now:
            return list(cached[1])

        discovered: list[str] = []
        for relay_url in self._configured_relays():
            relay_list = await self._fetch_recipient_relays_from_relay(relay_url, recipient_pubkey)
            for discovered_relay in relay_list:
                if discovered_relay not in discovered:
                    discovered.append(discovered_relay)

        if discovered:
            self._nip17_relay_cache[recipient_pubkey] = (now + self._relay_cache_ttl_s(), discovered)
        return discovered

    async def _fetch_recipient_relays_from_relay(
        self,
        relay_url: str,
        recipient_pubkey: str,
    ) -> list[str]:
        event = await self._fetch_latest_event_from_relay(
            relay_url,
            authors=[recipient_pubkey],
            kinds=[self._NIP17_RELAY_LIST_KIND],
        )
        if event is None:
            return []
        return self._extract_relay_tags(event)

    def _build_kind10050_event(self, relay_urls: list[str]) -> Any:
        event = self.Event(
            kind=self._NIP17_RELAY_LIST_KIND,
            pubkey=self._hex_pub,
            created_at=int(time.time()),
            tags=[["relay", relay_url] for relay_url in relay_urls],
            content="",
        )
        event.sign(self._hex_priv)
        return event

    async def _reconcile_own_kind10050(self) -> None:
        bootstrap_relays = self._bootstrap_relays()
        configured_relays = self._desired_relays()
        published_relays = await self._fetch_own_kind10050_relays(bootstrap_relays)

        if not configured_relays and published_relays:
            self._active_relays = published_relays
            logger.info(
                "Using published kind 10050 relays for NIP-17 startup ({} relay(s))",
                len(self._active_relays),
            )
            return

        if configured_relays:
            self._active_relays = configured_relays
            if published_relays == configured_relays:
                logger.info("Existing kind 10050 matches configured NIP-17 relays")
                return

            publish_relays = self._normalize_relay_urls(configured_relays + published_relays + bootstrap_relays)
            await self._publish_event_to_relays(self._build_kind10050_event(configured_relays), publish_relays)
            logger.info(
                "Published updated kind 10050 for NIP-17 startup ({} relay(s))",
                len(configured_relays),
            )
            return

        self._active_relays = bootstrap_relays
        logger.warning(
            "No configured or published kind 10050 relays found; using bootstrap relays for this run"
        )

    async def _publish_event_to_relays(self, event: Any, relay_urls: list[str]) -> None:
        publish_msg = event.to_message()
        for relay_url in relay_urls:
            ws = await self._connection_for_relay(relay_url)
            if ws is not None:
                try:
                    await ws.send(publish_msg)
                    continue
                except Exception as e:
                    logger.warning("Failed to publish to {}: {}", relay_url, e)
                    await self._remove_connection_if_same(relay_url, ws)

            try:
                async with websockets.connect(
                    relay_url,
                    open_timeout=15,
                    close_timeout=10,
                    ping_interval=20,
                    ping_timeout=20,
                ) as temp_ws:
                    await temp_ws.send(publish_msg)
            except Exception as e:
                logger.warning("Failed temporary publish to {}: {}", relay_url, e)

    async def _relay_targets_for_nip17_recipient(self, recipient_pubkey: str) -> list[str]:
        discovered = await self._discover_recipient_relays(recipient_pubkey)
        if discovered:
            return discovered
        if self._nip17_fallback_to_configured_relays():
            logger.warning(
                "No kind 10050 inbox relays found for {}..., falling back to configured relays",
                recipient_pubkey[:8],
            )
            return self._configured_relays()
        logger.warning("No kind 10050 inbox relays found for {}..., skipping publish", recipient_pubkey[:8])
        return []

    def _next_reconnect_delay(self, relay_url: str) -> int:
        current = self._relay_reconnect_delay.get(relay_url, self._RECONNECT_DELAY_SECONDS)
        delay = min(current, self._MAX_RECONNECT_DELAY_SECONDS)
        self._relay_reconnect_delay[relay_url] = min(delay * 2, self._MAX_RECONNECT_DELAY_SECONDS)
        return delay

    async def _subscribe_to_dms(self) -> None:
        for relay_url, ws in await self._connection_snapshot():
            await self._subscribe_to_relay(relay_url, ws)

    async def _subscribe_to_relay(self, relay_url: str, ws: ClientConnection) -> None:
        subscribed_kind = 4 if self._protocol() == "nip04" else NIP17_GIFT_WRAP_KIND
        since = (
            self._listen_start
            if self._protocol() == "nip04"
            else max(0, self._listen_start - randomized_past_window_seconds())
        )
        filter_dict = {
            "kinds": [subscribed_kind],
            "#p": [self._hex_pub],
            "since": since,
            "limit": 25,
        }
        subscription_msg = ["REQ", self._subscription_id, filter_dict]
        try:
            await ws.send(json.dumps(subscription_msg))
            logger.info("Subscribed to DMs on {} (#p=[{}...])", relay_url, self._hex_pub[:8])
        except Exception as e:
            logger.warning("Failed to subscribe on {}: {}", relay_url, e)

    async def _receive_loop(self) -> None:
        tasks = [asyncio.create_task(self._receive_from_relay(url)) for url in self._configured_relays()]
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            logger.debug("Receive loop cancelled")

    async def _receive_from_relay(self, relay_url: str) -> None:
        while self._running:
            async with self._ws_lock:
                ws = self._ws_connections.get(relay_url)
            if ws is None:
                connected = await self._connect_to_relay(relay_url)
                if connected:
                    self._relay_reconnect_delay[relay_url] = self._RECONNECT_DELAY_SECONDS
                    async with self._ws_lock:
                        current_ws = self._ws_connections.get(relay_url)
                    if current_ws is not None:
                        await self._subscribe_to_relay(relay_url, current_ws)
                else:
                    await asyncio.sleep(self._next_reconnect_delay(relay_url))
                    continue

            async with self._ws_lock:
                ws = self._ws_connections.get(relay_url)
            if ws is None:
                await asyncio.sleep(self._next_reconnect_delay(relay_url))
                continue

            logger.info("Starting receive loop for {}", relay_url)
            try:
                async for message in ws:
                    if not self._running:
                        break
                    try:
                        data = json.loads(message)
                        await self._process_relay_message(relay_url, data)
                    except json.JSONDecodeError:
                        logger.warning("Invalid JSON from {}: {}", relay_url, message[:100])
                    except Exception as e:
                        logger.error("Error processing message from {}: {}", relay_url, e)
            except websockets.ConnectionClosed as e:
                logger.warning("Connection closed for {}: {}. Reconnecting...", relay_url, e)
            except asyncio.CancelledError:
                logger.debug("Receive loop cancelled for {}", relay_url)
                break
            except Exception as e:
                logger.error("Receive loop error for {}: {}. Reconnecting...", relay_url, e)
            finally:
                await self._remove_connection_if_same(relay_url, ws)
                try:
                    await ws.close()
                except Exception:
                    pass

            if self._running:
                await asyncio.sleep(self._next_reconnect_delay(relay_url))

    async def _process_relay_message(self, relay_url: str, data: list[Any]) -> None:
        if not isinstance(data, list) or len(data) < 2:
            return
        msg_type = data[0]
        if msg_type == "EVENT":
            subscription_id = data[1]
            if subscription_id != self._subscription_id:
                return
            event = data[2] if len(data) > 2 else None
            if event:
                await self._handle_event(relay_url, event)
        elif msg_type == "NOTICE":
            pass  # Skip relay notices (too verbose)
        elif msg_type == "OK":
            pass  # Skip OK confirmations (too verbose)

    async def _handle_event(self, relay_url: str, event: dict[str, Any]) -> None:
        try:
            event_id = event.get("id", "")
            event_kind = event.get("kind")
            event_pubkey = event.get("pubkey", "")
            event_tags = event.get("tags", [])
            event_content = event.get("content", "")
            event_created_at = event.get("created_at", 0)

            if event_id and event_id in self._seen_event_ids:
                return

            if self._protocol() == "nip04" and event_kind != 4:
                return
            if self._protocol() == "nip17" and event_kind != NIP17_GIFT_WRAP_KIND:
                return

            if self._protocol() == "nip04" and event_created_at < self._listen_start:
                return

            if self._protocol() == "nip17":
                sender_pubkey, content, message_created_at, metadata = self._handle_nip17_event(
                    event=event,
                    relay_url=relay_url,
                )
            else:
                sender_pubkey, content, message_created_at = self._handle_nip04_event(
                    event_pubkey=event_pubkey,
                    event_tags=event_tags,
                    event_content=event_content,
                    event_created_at=event_created_at,
                    event_id=event_id,
                )
                metadata = {
                    "event_id": event_id,
                    "sender_pubkey": sender_pubkey,
                    "relay_url": relay_url,
                    "created_at": message_created_at,
                    "kind": event_kind,
                }
            if sender_pubkey is None:
                return
            if sender_pubkey == self._hex_pub:
                logger.debug("Ignoring locally wrapped outbound NIP-17 copy")
                return

            if event_id:
                self._seen_event_ids.add(event_id)
                if len(self._seen_event_ids) > self._MAX_SEEN_EVENTS:
                    self._seen_event_ids.clear()

            logger.info("Received DM from {}...: {}...", sender_pubkey[:8], content[:50] if content else "(empty)")

            session_key = f"nostr:{sender_pubkey}"
            await self._handle_inbound_message(
                session_key=session_key,
                content=content or "",
                metadata=metadata,
            )
        except Exception as e:
            logger.error("Error handling event: {}", e)

    def _handle_nip04_event(
        self,
        event_pubkey: str,
        event_tags: list[Any],
        event_content: str,
        event_created_at: int,
        event_id: str,
    ) -> tuple[str | None, str | None, int]:
        if not self._event_targets_us(event_tags):
            return None, None, event_created_at

        try:
            sender_pubkey = self._normalize_pubkey_to_hex(event_pubkey)
        except Exception:
            logger.warning("Invalid sender pubkey in NIP-04 event: {}", str(event_pubkey)[:16])
            return None, None, event_created_at

        if not self._is_sender_allowed(sender_pubkey):
            return None, None, event_created_at

        try:
            pynostr_event = self.Event(kind=4, pubkey=event_pubkey, created_at=event_created_at, tags=event_tags, content=event_content)
            dm = self.EncryptedDirectMessage.from_event(pynostr_event)
            dm.decrypt(private_key_hex=self._hex_priv, public_key_hex=sender_pubkey)
            return sender_pubkey, dm.cleartext_content or "", event_created_at
        except Exception as e:
            logger.error("NIP-04 DM decryption failed: {}", e)
            return None, None, event_created_at

    def _handle_nip17_event(
        self,
        *,
        event: dict[str, Any],
        relay_url: str,
    ) -> tuple[str | None, str | None, int, dict[str, Any]]:
        event_tags = event.get("tags", [])
        if not self._event_targets_us(event_tags):
            return None, None, int(event.get("created_at", 0) or 0), {}

        try:
            parsed = parse_nip17_message(event, recipient_private_key_hex=self._hex_priv)
        except NIP17Error as exc:
            logger.error("NIP-17 DM decryption failed: {}", exc)
            return None, None, int(event.get("created_at", 0) or 0), {}

        sender_pubkey = parsed.sender_pubkey
        if sender_pubkey == self._hex_pub:
            return sender_pubkey, parsed.content, parsed.rumor.created_at or 0, {}
        if not self._is_sender_allowed(sender_pubkey):
            return None, None, parsed.rumor.created_at or 0, {}

        metadata = {
            "event_id": parsed.gift_wrap.id,
            "gift_wrap_id": parsed.gift_wrap.id,
            "seal_id": parsed.seal.id,
            "rumor_id": parsed.rumor.id,
            "sender_pubkey": sender_pubkey,
            "relay_url": relay_url,
            "created_at": parsed.rumor.created_at,
            "kind": parsed.rumor.kind,
            "gift_wrap_kind": parsed.gift_wrap.kind,
        }
        return sender_pubkey, parsed.content, parsed.rumor.created_at or 0, metadata

    def _event_targets_us(self, tags: list[Any]) -> bool:
        for tag in tags:
            if not isinstance(tag, list) or len(tag) < 2 or tag[0] != "p":
                continue
            try:
                tag_hex = self._normalize_pubkey_to_hex(str(tag[1]))
            except Exception:
                continue
            if tag_hex == self._hex_pub:
                return True
        return False

    def _is_sender_allowed(self, sender_pubkey: str) -> bool:
        if self.is_allowed(sender_pubkey):
            return True
        allowed_short = [p[:8] if p != "*" else "*" for p in sorted(self._allowed_pubkeys)]
        logger.warning(
            "Message from unauthorized pubkey {} (allowed={})",
            sender_pubkey[:8],
            allowed_short,
        )
        return False

    async def start(self) -> None:
        self._running = True
        self._listen_start = int(time.time())

        if self._protocol() == "nip17":
            await self._reconcile_own_kind10050()

        logger.info("Connecting to relays...")
        await self._connect_to_relays()
        if not self._ws_connections:
            logger.error("No relay connections established")
            return

        logger.info("Subscribing to DM events...")
        await self._subscribe_to_dms()

        logger.info("Starting receive loop...")
        self._receive_task = asyncio.create_task(self._receive_loop())
        logger.info("Nostr channel started")

    async def stop(self) -> None:
        self._running = False
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        for relay_url, ws in await self._connection_snapshot():
            try:
                await ws.close()
                logger.debug("Closed connection to {}", relay_url)
            except Exception:
                pass
        async with self._ws_lock:
            self._ws_connections.clear()
        logger.info("Nostr channel stopped")

    async def send(self, msg: OutboundMessage) -> None:
        if not self._running:
            logger.warning("Nostr channel not running")
            return

        recipient_pubkey = msg.chat_id
        content = msg.content.strip()
        if not content or content.lower() in {"thinking...", "thinking..", "thinking.", "thinking"}:
            return

        try:
            recipient_pubkey = self._normalize_pubkey_to_hex(recipient_pubkey)
            chunks = _split_message(content)
            for index, chunk in enumerate(chunks, start=1):
                if self._protocol() == "nip17":
                    events = build_nip17_message(
                        sender_private_key_hex=self._hex_priv,
                        recipient_pubkey_hex=recipient_pubkey,
                        content=chunk,
                    )
                else:
                    dm = self.EncryptedDirectMessage()
                    dm.encrypt(
                        private_key_hex=self._hex_priv,
                        recipient_pubkey=recipient_pubkey,
                        cleartext_content=chunk,
                    )
                    event = dm.to_event()
                    event.sign(self._hex_priv)
                    events = [event]

                for event in events:
                    if self._protocol() == "nip17":
                        event_recipient = self._extract_p_tag_pubkey(event.tags)
                        if event_recipient is None:
                            logger.warning("Skipping malformed NIP-17 event without recipient p-tag")
                            continue
                        relay_targets = await self._relay_targets_for_nip17_recipient(event_recipient)
                        if not relay_targets:
                            continue
                        await self._publish_event_to_relays(event, relay_targets)
                    else:
                        publish_msg = event.to_message()
                        for relay_url, ws in await self._connection_snapshot():
                            try:
                                await ws.send(publish_msg)
                            except Exception as e:
                                logger.warning("Failed to publish to {}: {}", relay_url, e)
                                await self._remove_connection_if_same(relay_url, ws)

                logger.info(
                    "Sent DM chunk {}/{} to {}... (protocol={}, events={}, chars={})",
                    index,
                    len(chunks),
                    recipient_pubkey[:8],
                    self._protocol(),
                    len(events),
                    len(chunk),
                )
                await asyncio.sleep(1)
        except Exception as e:
            logger.error("Failed to send Nostr DM: {}", e)

    async def _handle_inbound_message(self, session_key: str, content: str, metadata: dict[str, Any] | None = None) -> None:
        if not session_key.startswith("nostr:"):
            logger.warning("Invalid session_key format: {}", session_key)
            return
        sender_pubkey = session_key.replace("nostr:", "", 1)
        await self._handle_message(
            sender_id=sender_pubkey,
            chat_id=sender_pubkey,
            content=content,
            metadata=metadata or {},
            session_key=session_key,
        )

    def is_allowed(self, sender_id: str) -> bool:
        if "*" in self._allowed_pubkeys:
            return True
        if not self._allowed_pubkeys:
            return False
        try:
            sender_hex = self._normalize_pubkey_to_hex(sender_id)
        except Exception:
            return False
        return sender_hex in self._allowed_pubkeys

    @property
    def our_pubkey_hex(self) -> str:
        return self._hex_pub

    @property
    def our_pubkey_npub(self) -> str:
        return self._private_key.public_key.bech32()
