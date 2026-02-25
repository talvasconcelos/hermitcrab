"""Nostr channel implementation using pynostr for NIP-04 encrypted DMs."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from loguru import logger

from hermitcrab.bus.events import OutboundMessage
from hermitcrab.bus.queue import MessageBus
from hermitcrab.channels.base import BaseChannel
from hermitcrab.config.schema import NostrConfig


class NostrChannel(BaseChannel):
    """
    Nostr channel supporting NIP-04 encrypted direct messages.

    Features:
    - Encrypted DMs via NIP-04 (kind 4 events)
    - Configurable relay list with auto-reconnect
    - Allowlist-based access control
    - Session keys formatted as "nostr:{hex_pubkey}"

    NIP-17 (Gift Wrap) support planned for future group messaging.
    """

    name = "nostr"

    def __init__(self, config: NostrConfig, bus: MessageBus) -> None:
        """
        Initialize the Nostr channel.

        Args:
            config: Nostr configuration with private key, relays, protocol.
            bus: Message bus for communication.
        """
        super().__init__(config, bus)
        self.config: NostrConfig = config

        # Lazy import pynostr to avoid hard dependency if not enabled
        try:
            from pynostr.encrypted_dm import EncryptedDirectMessage
            from pynostr.event import EventKind
            from pynostr.filters import Filters, FiltersList
            from pynostr.key import PrivateKey
            from pynostr.relay_manager import RelayManager

            self.PrivateKey = PrivateKey
            self.RelayManager = RelayManager
            self.EncryptedDirectMessage = EncryptedDirectMessage
            self.FiltersList = FiltersList
            self.Filters = Filters
            self.EventKind = EventKind
        except ImportError as e:
            logger.error("pynostr not installed. Run: pip install pynostr")
            raise ImportError("pynostr is required for Nostr channel") from e

        # Parse and validate private key
        self._private_key = self._load_private_key(config.private_key)
        self._hex_priv = self._private_key.hex()
        self._hex_pub = self._private_key.public_key.hex()

        # Normalize allowed pubkeys to hex
        self._allowed_pubkeys: set[str] = self._normalize_allowed_pubkeys(
            config.allowed_pubkeys
        )

        # Relay manager with timeout=0 to keep connections alive
        self._relay_manager = self.RelayManager(timeout=0)
        self._subscription_id = f"hermitcrab-dm-{uuid.uuid4().hex}"
        self._started_at = int(time.time())
        self._seen_event_ids: set[str] = set()
        self._relay_loop_task: asyncio.Task | None = None
        self._listen_callback: Any = None

        # Connect to relays
        self._connect_relays()

        logger.info(
            "Nostr channel initialized (pubkey: {}...)",
            self._hex_pub[:8]
        )

    def _load_private_key(self, key: str | None) -> Any:
        """
        Load or generate private key.

        Args:
            key: Private key in nsec or hex format.

        Returns:
            PrivateKey instance.

        Raises:
            ValueError: If key format is invalid.
        """
        if not key:
            logger.warning("Nostr private key not provided, generating new keypair")
            new_key = self.PrivateKey()
            logger.warning(
                "Generated new Nostr keypair. Save this nsec key: {}",
                new_key.bech32()
            )
            logger.warning(
                "Your public key (npub): {}",
                new_key.public_key.bech32()
            )
            return new_key

        key = key.strip()

        # Validate format
        if key.startswith("npub"):
            raise ValueError(
                "Nostr private_key cannot be npub (public key); "
                "use nsec or hex private key"
            )

        try:
            if key.startswith("nsec"):
                return self.PrivateKey.from_nsec(key)
            else:
                return self.PrivateKey.from_hex(key)
        except Exception as e:
            raise ValueError(
                f"Invalid Nostr private key format. Expected nsec or hex. Error: {e}"
            ) from e

    def _normalize_allowed_pubkeys(self, pubkeys: list[str]) -> set[str]:
        """
        Normalize allowed pubkeys to hex format.

        Args:
            pubkeys: List of pubkeys in npub or hex format.

        Returns:
            Set of hex-formatted pubkeys.
        """
        normalized: set[str] = set()

        for pk in pubkeys:
            pk = pk.strip()
            if not pk:
                continue

            try:
                if pk.startswith("npub"):
                    from pynostr.key import PublicKey
                    hex_pk = PublicKey.from_npub(pk).hex()
                else:
                    hex_pk = pk
                normalized.add(hex_pk)
            except Exception as e:
                logger.warning("Invalid pubkey format '{}', skipping: {}", pk, e)

        if not normalized:
            logger.warning(
                "No allowed_pubkeys configured. Nostr channel will accept messages from anyone. "
                "Set allowed_pubkeys in config for security."
            )

        return normalized

    def _connect_relays(self) -> None:
        """Connect to configured relays."""
        for relay_url in self.config.relays:
            try:
                self._relay_manager.add_relay(relay_url, close_on_eose=False)
                logger.debug("Added relay: {}", relay_url)
            except Exception as e:
                logger.warning("Failed to add relay {}: {}", relay_url, e)

        logger.info(
            "Connected to {} relays: {}",
            len(self.config.relays),
            ", ".join(self.config.relays)
        )

    def _start_relay_loop(self) -> None:
        """Start background relay sync loop."""
        def _runner() -> None:
            try:
                logger.debug("Nostr relay loop started")
                self._relay_manager.run_sync()
            except Exception as exc:
                logger.error("Nostr relay loop crashed: {}", exc)

        self._relay_loop_task = asyncio.create_task(
            asyncio.to_thread(_runner),
            name="nostr-relay-loop"
        )
        logger.debug("Nostr relay loop task created")

    async def _wait_for_relays(self, timeout: float = 2.0) -> None:
        """Wait for relay connections to establish."""
        await asyncio.sleep(timeout)

    def _subscribe_incoming_dms(self) -> None:
        """Subscribe to incoming encrypted DMs addressed to this bot."""
        filters = self.FiltersList([
            self.Filters(
                kinds=[self.EventKind.ENCRYPTED_DIRECT_MESSAGE],
                pubkey_refs=[self._hex_pub],
                since=self._started_at,
                limit=100,
            )
        ])

        self._relay_manager.add_subscription_on_all_relays(
            self._subscription_id,
            filters
        )

        logger.info(
            "Subscribed to DM events (sub_id={}, recipient={}...)",
            self._subscription_id,
            self._hex_pub[:8]
        )

    async def start(self) -> None:
        """
        Start the Nostr channel and begin listening for messages.

        This connects to relays, subscribes to incoming DMs, and starts
        the relay sync loop.
        """
        self._running = True

        # Start relay loop
        self._start_relay_loop()

        # Wait for connections
        await self._wait_for_relays()

        # Subscribe to incoming DMs
        self._subscribe_incoming_dms()

        logger.info("Nostr channel started, listening for DMs...")

        # Run listener
        await self.listen(self._handle_inbound_message)

    async def stop(self) -> None:
        """Stop the Nostr channel and clean up resources."""
        self._running = False

        # Cancel relay loop
        if self._relay_loop_task and not self._relay_loop_task.done():
            self._relay_loop_task.cancel()
            try:
                await self._relay_loop_task
            except asyncio.CancelledError:
                pass

        # Close relay connections
        self._relay_manager.close_all_relay_connections()
        logger.info("Nostr channel stopped")

    async def send(self, msg: OutboundMessage) -> None:
        """
        Send an encrypted DM through Nostr.

        Args:
            msg: Outbound message with chat_id as recipient pubkey.
        """
        if not self._running:
            logger.warning("Nostr channel not running, cannot send message")
            return

        recipient_pubkey = msg.chat_id

        # Skip "thinking..." messages
        normalized = msg.content.strip().lower()
        if normalized in {"thinking...", "thinking..", "thinking.", "thinking"}:
            logger.debug("Skipping 'thinking...' message")
            return

        try:
            # Create encrypted DM
            dm = self.EncryptedDirectMessage()
            dm.encrypt(
                private_key_hex=self._hex_priv,
                recipient_pubkey=recipient_pubkey,
                cleartext_content=msg.content,
            )

            dm_event = dm.to_event()
            dm_event.sign(private_key_hex=self._hex_priv)

            # Publish to relays
            self._relay_manager.publish_event(dm_event)

            # Wait for propagation
            await asyncio.sleep(1)

            event_id = dm_event.id[:10] if dm_event.id else "unknown"
            logger.info(
                "Sent DM to {}... (kind={}, id={}...)",
                recipient_pubkey[:8],
                dm_event.kind,
                event_id
            )

        except Exception as e:
            logger.error("Failed to send Nostr DM: {}", e)

    async def listen(
        self,
        callback: Any,
    ) -> None:
        """
        Poll relay message pool and process authorized DMs.

        Args:
            callback: Async callback to handle decrypted messages.
        """
        self._listen_callback = callback

        logger.debug("Nostr listener started")

        while self._running:
            try:
                # Process available events
                while self._relay_manager.message_pool.has_events():
                    message = self._relay_manager.message_pool.get_event()
                    event = message.event

                    # Skip duplicates
                    if event.id and event.id in self._seen_event_ids:
                        continue
                    if event.id:
                        self._seen_event_ids.add(event.id)

                    # Only process kind 4 (encrypted DM)
                    if event.kind != self.EventKind.ENCRYPTED_DIRECT_MESSAGE:
                        continue

                    sender = event.pubkey or ""
                    sender_short = sender[:8] if sender else "unknown"

                    # Verify recipient is us
                    p_tags = [
                        tag[1] for tag in event.tags
                        if len(tag) > 1 and tag[0] == "p"
                    ]

                    if self._hex_pub not in p_tags:
                        logger.debug(
                            "Skipping DM not addressed to us (sender={}...)",
                            sender_short
                        )
                        continue

                    # Check allowlist
                    if self._allowed_pubkeys and sender not in self._allowed_pubkeys:
                        logger.warning(
                            "Message from unauthorized pubkey {} ignored",
                            sender
                        )
                        continue

                    # Decrypt
                    try:
                        dm = self.EncryptedDirectMessage.from_event(event)
                        if dm is None:
                            logger.warning("Failed to parse encrypted DM")
                            continue

                        dm.decrypt(
                            private_key_hex=self._hex_priv,
                            public_key_hex=sender
                        )
                    except Exception as e:
                        logger.error("DM decryption failed: {}", e)
                        continue

                    content = (dm.cleartext_content or "").strip()
                    logger.info(
                        "Received DM from {}...: {}...",
                        sender_short,
                        content[:50]
                    )

                    # Call handler
                    session_key = f"nostr:{sender}"

                    try:
                        await callback(
                            session_key=session_key,
                            content=content,
                            metadata={
                                "event_id": event.id,
                                "sender_pubkey": sender,
                                "relay_url": message.url,
                            }
                        )
                    except Exception as e:
                        logger.error("DM handler failed: {}", e)

                # Small delay to avoid busy-waiting
                await asyncio.sleep(0.2)

            except asyncio.CancelledError:
                logger.debug("Nostr listener cancelled")
                break
            except Exception as e:
                logger.error("Listener error: {}", e)
                await asyncio.sleep(1)

    async def _handle_inbound_message(
        self,
        session_key: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Handle an incoming decrypted message.

        Args:
            session_key: Session identifier (nostr:{pubkey}).
            content: Decrypted message content.
            metadata: Event metadata.
        """
        # Extract sender pubkey from session_key
        if not session_key.startswith("nostr:"):
            logger.warning("Invalid session_key format: {}", session_key)
            return

        sender_pubkey = session_key.replace("nostr:", "", 1)

        # Forward to message bus
        await self._handle_message(
            sender_id=sender_pubkey,
            chat_id=sender_pubkey,
            content=content,
            metadata=metadata or {},
            session_key_override=session_key,
        )

    def is_allowed(self, sender_id: str) -> bool:
        """
        Check if a sender is allowed (allowlist check).

        Args:
            sender_id: Sender's pubkey hex.

        Returns:
            True if allowed, False otherwise.
        """
        # If no allowlist, allow everyone (but warn was logged on init)
        if not self._allowed_pubkeys:
            return True

        return sender_id in self._allowed_pubkeys

    @property
    def our_pubkey_hex(self) -> str:
        """Get our public key in hex format."""
        return self._hex_pub

    @property
    def our_pubkey_npub(self) -> str:
        """Get our public key in npub (bech32) format."""
        return self._private_key.public_key.bech32()
