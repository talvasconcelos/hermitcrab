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
from hermitcrab.config.schema import NostrConfig


class NostrChannel(BaseChannel):
    """Nostr channel for NIP-04 encrypted DMs using WebSocket connections."""

    name = "nostr"
    _HEX_PUBKEY_RE = re.compile(r"^[0-9a-fA-F]{64}$")
    _RECONNECT_DELAY_SECONDS = 2
    _MAX_RECONNECT_DELAY_SECONDS = 30
    _MAX_SEEN_EVENTS = 10_000

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

    async def _connect_to_relays(self) -> None:
        for relay_url in self.config.relays:
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

    async def _remove_connection_if_same(self, relay_url: str, ws: ClientConnection) -> None:
        async with self._ws_lock:
            current = self._ws_connections.get(relay_url)
            if current is ws:
                self._ws_connections.pop(relay_url, None)

    def _next_reconnect_delay(self, relay_url: str) -> int:
        current = self._relay_reconnect_delay.get(relay_url, self._RECONNECT_DELAY_SECONDS)
        delay = min(current, self._MAX_RECONNECT_DELAY_SECONDS)
        self._relay_reconnect_delay[relay_url] = min(delay * 2, self._MAX_RECONNECT_DELAY_SECONDS)
        return delay

    async def _subscribe_to_dms(self) -> None:
        for relay_url, ws in await self._connection_snapshot():
            await self._subscribe_to_relay(relay_url, ws)

    async def _subscribe_to_relay(self, relay_url: str, ws: ClientConnection) -> None:
        filter_dict = {
            "kinds": [4],
            "#p": [self._hex_pub],
            "since": self._listen_start,
            "limit": 25,
        }
        subscription_msg = ["REQ", self._subscription_id, filter_dict]
        try:
            await ws.send(json.dumps(subscription_msg))
            logger.info("Subscribed to DMs on {} (#p=[{}...])", relay_url, self._hex_pub[:8])
        except Exception as e:
            logger.warning("Failed to subscribe on {}: {}", relay_url, e)

    async def _receive_loop(self) -> None:
        tasks = [asyncio.create_task(self._receive_from_relay(url)) for url in self.config.relays]
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

            if event_kind != 4:
                return

            if event_created_at < self._listen_start:
                return

            sender_pubkey, content, message_created_at = self._handle_nip04_event(
                event_pubkey=event_pubkey,
                event_tags=event_tags,
                event_content=event_content,
                event_created_at=event_created_at,
                event_id=event_id,
            )
            if sender_pubkey is None:
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
                metadata={"event_id": event_id, "sender_pubkey": sender_pubkey, "relay_url": relay_url, "created_at": message_created_at, "kind": event_kind},
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
            dm = self.EncryptedDirectMessage()
            dm.encrypt(private_key_hex=self._hex_priv, recipient_pubkey=recipient_pubkey, cleartext_content=content)
            event = dm.to_event()
            event.sign(self._hex_priv)
            publish_msg = event.to_message()

            for relay_url, ws in await self._connection_snapshot():
                try:
                    await ws.send(publish_msg)
                except Exception as e:
                    logger.warning("Failed to publish to {}: {}", relay_url, e)
                    await self._remove_connection_if_same(relay_url, ws)

            logger.info("Sent DM to {}... (event={}...)", recipient_pubkey[:8], event.id[:8] if event.id else "unknown")
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
