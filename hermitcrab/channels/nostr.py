"""Nostr channel using WebSocket + pynostr for NIP-04 encrypted DMs."""

from __future__ import annotations

import asyncio
import json
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
                hex_pk = self.PublicKey.from_npub(pk).hex() if pk.startswith("npub") else pk
                normalized.add(hex_pk)
            except Exception as e:
                logger.warning("Invalid pubkey format '{}', skipping: {}", pk, e)
        if not normalized:
            logger.info("Nostr allowlist is empty - denying all DMs. Set ['*'] for open mode.")
        return normalized

    async def _connect_to_relays(self) -> None:
        for relay_url in self.config.relays:
            try:
                logger.debug("Connecting to relay: {}", relay_url)
                ws = await websockets.connect(relay_url)
                self._ws_connections[relay_url] = ws
                logger.info("Connected to relay: {}", relay_url)
            except Exception as e:
                logger.warning("Failed to connect to relay {}: {}", relay_url, e)

    async def _subscribe_to_dms(self) -> None:
        filter_dict = {
            "kinds": [4],
            "#p": [self._hex_pub],
            "since": self._listen_start,
            "limit": 10,
        }
        subscription_msg = ["REQ", self._subscription_id, filter_dict]
        for relay_url, ws in self._ws_connections.items():
            try:
                await ws.send(json.dumps(subscription_msg))
                logger.info("Subscribed to DMs on {} (#p=[{}...])", relay_url, self._hex_pub[:8])
            except Exception as e:
                logger.warning("Failed to subscribe on {}: {}", relay_url, e)

    async def _receive_loop(self) -> None:
        tasks = [asyncio.create_task(self._receive_from_relay(url, ws)) for url, ws in self._ws_connections.items()]
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            logger.debug("Receive loop cancelled")

    async def _receive_from_relay(self, relay_url: str, ws: ClientConnection) -> None:
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
            logger.info("Connection closed for {}: {}", relay_url, e)
        except asyncio.CancelledError:
            logger.debug("Receive loop cancelled for {}", relay_url)
        except Exception as e:
            logger.error("Receive loop error for {}: {}", relay_url, e)

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
            logger.debug("NOTICE from {}: {}", relay_url, data[1] if len(data) > 1 else "")
        elif msg_type == "OK":
            event_id = data[1] if len(data) > 1 else "unknown"
            success = data[2] if len(data) > 2 else False
            logger.debug("OK from {}: event={}... success={}", relay_url, str(event_id)[:8], success)

    async def _handle_event(self, relay_url: str, event: dict[str, Any]) -> None:
        try:
            event_id = event.get("id", "")
            event_kind = event.get("kind")
            event_pubkey = event.get("pubkey", "")
            event_tags = event.get("tags", [])
            event_content = event.get("content", "")
            event_created_at = event.get("created_at", 0)

            if event_kind != 4:
                return

            if event_created_at < self._listen_start:
                logger.debug("Skipping old message (created_at={} < listen_start={})", event_created_at, self._listen_start)
                return

            if event_id in self._seen_event_ids:
                logger.debug("Skipping duplicate event {}", event_id[:8])
                return
            self._seen_event_ids.add(event_id)

            p_tags = [tag[1] for tag in event_tags if len(tag) > 1 and tag[0] == "p"]
            if self._hex_pub not in p_tags:
                return

            sender_pubkey = event_pubkey

            if self._allowed_pubkeys and "*" not in self._allowed_pubkeys:
                if sender_pubkey not in self._allowed_pubkeys:
                    logger.warning("Message from unauthorized pubkey {} (allowed={})", sender_pubkey[:8], [p[:8] for p in self._allowed_pubkeys])
                    return

            try:
                pynostr_event = self.Event(kind=4, pubkey=event_pubkey, created_at=event_created_at, tags=event_tags, content=event_content)
                if pynostr_event.id != event_id:
                    logger.warning("Event ID mismatch, skipping")
                    return
                dm = self.EncryptedDirectMessage.from_event(pynostr_event)
                dm.decrypt(private_key_hex=self._hex_priv, public_key_hex=sender_pubkey)
                content = dm.cleartext_content
            except Exception as e:
                logger.error("DM decryption failed: {}", e)
                return

            logger.info("Received DM from {}...: {}...", sender_pubkey[:8], content[:50] if content else "(empty)")

            session_key = f"nostr:{sender_pubkey}"
            await self._handle_inbound_message(
                session_key=session_key,
                content=content or "",
                metadata={"event_id": event_id, "sender_pubkey": sender_pubkey, "relay_url": relay_url, "created_at": event_created_at},
            )
        except Exception as e:
            logger.error("Error handling event: {}", e)

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
        for relay_url, ws in self._ws_connections.items():
            try:
                await ws.close()
                logger.debug("Closed connection to {}", relay_url)
            except Exception:
                pass
        self._ws_connections.clear()
        logger.info("Nostr channel stopped")

    async def send(self, msg: OutboundMessage) -> None:
        if not self._running:
            logger.warning("Nostr channel not running")
            return

        recipient_pubkey = msg.chat_id
        content = msg.content.strip()
        if not content or content.lower() in {"thinking...", "thinking..", "thinking.", "thinking"}:
            logger.debug("Skipping empty/thinking message")
            return

        try:
            dm = self.EncryptedDirectMessage()
            dm.encrypt(private_key_hex=self._hex_priv, recipient_pubkey=recipient_pubkey, cleartext_content=content)
            event = dm.to_event()
            event.sign(self._hex_priv)
            publish_msg = event.to_message()

            for relay_url, ws in self._ws_connections.items():
                try:
                    await ws.send(publish_msg)
                    logger.debug("Published event to {}", relay_url)
                except Exception as e:
                    logger.warning("Failed to publish to {}: {}", relay_url, e)

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
        return sender_id in self._allowed_pubkeys

    @property
    def our_pubkey_hex(self) -> str:
        return self._hex_pub

    @property
    def our_pubkey_npub(self) -> str:
        return self._private_key.public_key.bech32()
