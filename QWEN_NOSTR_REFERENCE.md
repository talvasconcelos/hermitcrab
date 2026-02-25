# Nostr & NIP-04 Reference Guide

**Created:** 2026-02-24  
**For:** HermitCrab AI Agent Development

---

## Table of Contents

1. [What is Nostr?](#what-is-nostr)
2. [NIP-04: Encrypted Direct Messages](#nip-04-encrypted-direct-messages)
3. [Python Libraries](#python-libraries)
4. [Getting Started with pynostr](#getting-started-with-pynostr)
5. [Connecting to Relays](#connecting-to-relays)
6. [Sending & Receiving Messages](#sending--receiving-messages)
7. [NIP-04 Encryption/Decryption](#nip-04-encryptiondecryption)
8. [Complete Working Examples](#complete-working-examples)
9. [Event Kinds Reference](#event-kinds-reference)
10. [Common Relays](#common-relays)

---

## What is Nostr?

**Nostr** (Notes and Other Stuff Transmitted by Relays) is a decentralized social networking protocol.

### Architecture

```
┌─────────────┐      ┌─────────────┐      ┌─────────────┐
│   Client    │ ───► │   Relay     │ ◄─── │   Client    │
│  (npub...)  │      │  (WebSocket)│      │  (npub...)  │
└─────────────┘      └─────────────┘      └─────────────┘
     │                      │                      │
     │  Publish Events      │  Store/Forward       │  Subscribe
     │─────────────────────►│─────────────────────►│
```

### Key Concepts

| Term | Description |
|------|-------------|
| **Event** | Signed data object (note, DM, profile, etc.) |
| **Relay** | WebSocket server that stores/forwards events |
| **Public Key** | User identity (npub1... in bech32, or hex) |
| **Private Key** | Signing key (nsec1... in bech32, or hex) |
| **Kind** | Event type (1=text note, 4=DM, 0=metadata) |
| **Tag** | Metadata array (e.g., `["p", pubkey]` for mentions) |

---

## NIP-04: Encrypted Direct Messages

### ⚠️ Status: **Deprecated**

NIP-04 is **legacy**. [NIP-17](https://nips.nostr.com/17) (Gift Wrap) is the modern replacement with better metadata protection. However, NIP-04 is still widely supported.

### Event Structure

```json
{
  "id": "<event-hash>",
  "pubkey": "<sender-pubkey-hex>",
  "created_at": 1234567890,
  "kind": 4,
  "tags": [
    ["p", "<recipient-pubkey-hex>"],
    ["e", "<previous-event-id>"]  // Optional: for threading
  ],
  "content": "<base64-encoded-aes-ciphertext>?iv=<base64-iv>",
  "sig": "<schnorr-signature>"
}
```

### Encryption Specification

| Component | Algorithm | Details |
|-----------|-----------|---------|
| **Key Exchange** | ECDH | secp256k1 curve, X-coordinate only (not hashed) |
| **Cipher** | AES-256-CBC | 256-bit key, 128-bit IV |
| **IV** | Random | 16 bytes, generated per message |
| **Padding** | PKCS7 | Block size alignment |
| **Encoding** | Base64 | Format: `<ciphertext>?iv=<iv>` |

### Shared Secret Derivation

**Critical:** Nostr uses **X-coordinate only** (not SHA256 hashed):

```python
# Standard ECDH (SHA256 hashed):
shared_secret = SHA256(secp256k1_ecdh(privkey, pubkey))

# Nostr NIP-04 (X-coordinate only, NOT hashed):
shared_point = secp256k1_ecdh(privkey, pubkey)
shared_secret = shared_point.x_coordinate  # 32 bytes, raw
```

### Encryption Flow

```
1. Compute shared_secret = ECDH(sender_privkey, recipient_pubkey)
2. Generate random IV (16 bytes)
3. Encrypt: ciphertext = AES-256-CBC(plaintext, shared_secret, IV)
4. Encode: content = base64(ciphertext) + "?iv=" + base64(IV)
5. Create event with kind=4, tags=[["p", recipient_pubkey]]
6. Sign and publish
```

### Decryption Flow

```
1. Parse content: ciphertext_b64, iv_b64 = content.split("?iv=")
2. Decode: ciphertext = base64decode(ciphertext_b64)
3. Decode: iv = base64decode(iv_b64)
4. Compute shared_secret = ECDH(recipient_privkey, sender_pubkey)
5. Decrypt: plaintext = AES-256-CBC-decrypt(ciphertext, shared_secret, iv)
```

### ⚠️ Security Warnings

- **Metadata leaks:** Recipient pubkey visible in tags (relays can see who talks to whom)
- **Use NIP-17** for production if privacy is critical
- **Clients must NOT** process `@npub` mentions in `.content` (leaks tags)

---

## Python Libraries

### Two Main Options

| Library | Package Name | GitHub | Status |
|---------|--------------|--------|--------|
| **python-nostr** | `nostr` | jeffthibault/python-nostr | Original, simpler |
| **pynostr** | `pynostr` | holgern/pynostr | Fork, more features, actively maintained |

### Recommendation: **pynostr**

More NIPs supported, better maintained, more examples.

### Installation

```bash
# pynostr (recommended)
pip install pynostr

# With websocket-client support
pip install pynostr[websocket-client]

# For Android/Termux
pkg update && pkg install build-essential binutils python-cryptography
pip install pynostr --no-binary all

# python-nostr (alternative)
pip install nostr
```

### Dependencies for Manual NIP-04

```bash
pip install cryptography coincurve
```

---

## Getting Started with pynostr

### Generate Keys

```python
from pynostr.key import PrivateKey

# Generate new key pair
private_key = PrivateKey()
public_key = private_key.public_key

print(f"Private (nsec): {private_key.bech32()}")
print(f"Public (npub): {public_key.bech32()}")
print(f"Private (hex): {private_key.hex()}")
print(f"Public (hex): {public_key.hex()}")

# Load existing key
existing_pk = PrivateKey.from_nsec("nsec1...")
# Or from hex
existing_pk = PrivateKey.from_hex("7f3b6c2444c526fc...")
```

### Key Formats

| Format | Example | Use |
|--------|---------|-----|
| **bech32 (nsec)** | `nsec1qx...` | Human-readable private key |
| **bech32 (npub)** | `npub1qx...` | Human-readable public key |
| **hex** | `7f3b6c2444c5...` | Internal use, signing |

---

## Connecting to Relays

### Using RelayManager (Recommended)

```python
from pynostr.relay_manager import RelayManager
import time

# Initialize
relay_manager = RelayManager(timeout=2)

# Add relays
relay_manager.add_relay("wss://relay.damus.io")
relay_manager.add_relay("wss://nostr-pub.wellorder.net")
relay_manager.add_relay("wss://nos.lol")

# Connect
relay_manager.run_sync()

# Check for notices
while relay_manager.message_pool.has_notices():
    notice = relay_manager.message_pool.get_notice()
    print(f"Notice: {notice.content}")

# Close when done
relay_manager.close_all_relay_connections()
```

### Using Single Relay (Tornado-based)

```python
from pynostr.relay import Relay
from pynostr.message_pool import MessagePool
from pynostr.base_relay import RelayPolicy
import tornado.ioloop

message_pool = MessagePool(first_response_only=False)
policy = RelayPolicy()
io_loop = tornado.ioloop.IOLoop.current()

relay = Relay(
    "wss://relay.damus.io",
    message_pool,
    io_loop,
    policy,
    timeout=2
)

try:
    io_loop.run_sync(relay.connect)
except:
    pass
io_loop.stop()
```

---

## Sending & Receiving Messages

### Subscribe to Events

```python
from pynostr.filters import FiltersList, Filters
from pynostr.event import EventKind
import uuid

# Create filters
filters = FiltersList([
    Filters(
        kinds=[EventKind.TEXT_NOTE],      # Event kind(s)
        authors=["<pubkey-hex>"],          # Optional: filter by authors
        limit=100                          # Max events
    )
])

# Create subscription
subscription_id = uuid.uuid1().hex
relay_manager.add_subscription_on_all_relays(subscription_id, filters)
```

### Publish Text Note

```python
from pynostr.event import Event

# Create event
event = Event(content="Hello Nostr!")
event.sign(private_key.hex())

# Publish
relay_manager.publish_event(event)

# Wait for confirmation
time.sleep(2)

# Check OK notices
while relay_manager.message_pool.has_ok_notices():
    ok = relay_manager.message_pool.get_ok_notice()
    print(f"Published: {ok}")
```

### Reply to a Note

```python
from pynostr.event import Event

reply = Event(content="Great point!")

# Add 'e' tag (referenced event)
reply.add_event_ref(original_note_id)

# Add 'p' tag (referenced user)
reply.add_pubkey_ref(original_author_pubkey)

reply.sign(private_key.hex())
relay_manager.publish_event(reply)
```

### Receive Events

```python
# After subscribing and running sync
while relay_manager.message_pool.has_events():
    event_msg = relay_manager.message_pool.get_event()
    event = event_msg.event
    
    print(f"From: {event.pubkey}")
    print(f"Kind: {event.kind}")
    print(f"Content: {event.content}")
    print(f"Created: {event.created_at}")
```

---

## NIP-04 Encryption/Decryption

### Send Encrypted DM (pynostr)

```python
from pynostr.encrypted_dm import EncryptedDirectMessage

# Create DM
dm = EncryptedDirectMessage()
dm.encrypt(
    private_key.hex(),                    # Sender's private key
    recipient_pubkey=recipient_pubkey_hex, # Recipient's public key
    cleartext_content="Secret message!"
)

# Convert to event and sign
dm_event = dm.to_event()
dm_event.sign(private_key.hex())

# Publish
relay_manager.publish_event(dm_event)
```

### Receive & Decrypt DM (pynostr)

```python
from pynostr.encrypted_dm import EncryptedDirectMessage

# Get event from relay (kind=4)
event_msg = relay_manager.message_pool.get_event()
event = event_msg.event

if event.kind == 4:
    # Create encrypted DM object
    enc_dm = EncryptedDirectMessage(
        receiver_pubkey=private_key.public_key.hex(),  # Your pubkey
        sender_pubkey=event.pubkey,                     # Sender's pubkey
        encrypted_message=event.content                 # Encrypted content
    )
    
    # Decrypt
    enc_dm.decrypt(private_key.hex())
    
    print(f"DM from {event.pubkey}: {enc_dm.cleartext_content}")
```

### Manual NIP-04 Implementation (cryptography + coincurve)

```python
import os
import base64
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from coincurve import PrivateKey as CoincurvePrivateKey

def compute_shared_secret(privkey_hex: str, pubkey_hex: str) -> bytes:
    """Compute ECDH shared secret (X-coordinate only)."""
    privkey = CoincurvePrivateKey.from_hex(privkey_hex)
    pubkey_bytes = bytes.fromhex(pubkey_hex)
    # ECDH returns 32-byte X coordinate
    return privkey.ecdh(pubkey_bytes)

def encrypt_nip04(plaintext: str, recipient_pubkey: str, sender_privkey: str) -> str:
    """Encrypt message using NIP-04."""
    # Shared secret
    shared_secret = compute_shared_secret(sender_privkey, recipient_pubkey)
    
    # Random IV
    iv = os.urandom(16)
    
    # PKCS7 padding
    content_bytes = plaintext.encode('utf-8')
    pad_len = 16 - (len(content_bytes) % 16)
    padded = content_bytes + bytes([pad_len] * pad_len)
    
    # AES-256-CBC encrypt
    cipher = Cipher(algorithms.AES(shared_secret), modes.CBC(iv), default_backend())
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    
    # Encode: base64(ciphertext)?iv=base64(iv)
    return base64.b64encode(ciphertext).decode() + "?iv=" + base64.b64encode(iv).decode()

def decrypt_nip04(encrypted: str, sender_pubkey: str, recipient_privkey: str) -> str:
    """Decrypt message using NIP-04."""
    # Parse
    ciphertext_b64, iv_b64 = encrypted.split("?iv=")
    ciphertext = base64.b64decode(ciphertext_b64)
    iv = base64.b64decode(iv_b64)
    
    # Shared secret
    shared_secret = compute_shared_secret(recipient_privkey, sender_pubkey)
    
    # AES-256-CBC decrypt
    cipher = Cipher(algorithms.AES(shared_secret), modes.CBC(iv), default_backend())
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    
    # Remove PKCS7 padding
    return padded[:-padded[-1]].decode('utf-8')

# Usage
encrypted = encrypt_nip04("Hello!", recipient_pubkey, sender_privkey)
decrypted = decrypt_nip04(encrypted, sender_pubkey, recipient_privkey)
```

---

## Complete Working Examples

### Example 1: Send & Receive DMs

```python
#!/usr/bin/env python3
"""Complete NIP-04 DM example with pynostr."""

from pynostr.key import PrivateKey
from pynostr.relay_manager import RelayManager
from pynostr.encrypted_dm import EncryptedDirectMessage
from pynostr.filters import FiltersList, Filters
from pynostr.event import EventKind
import uuid
import time

def main():
    # 1. Load/generate keys
    my_key = PrivateKey()  # Or PrivateKey.from_nsec("nsec1...")
    print(f"My pubkey: {my_key.public_key.bech32()}")
    
    # 2. Connect to relays
    relay_manager = RelayManager(timeout=6)
    relay_manager.add_relay("wss://relay.damus.io")
    relay_manager.add_relay("wss://nos.lol")
    
    # 3. Subscribe to incoming DMs (kind 4)
    filters = FiltersList([
        Filters(
            kinds=[EventKind.ENCRYPTED_DIRECT_MESSAGE],
            authors=[my_key.public_key.hex()],
            limit=100
        )
    ])
    subscription_id = uuid.uuid1().hex
    relay_manager.add_subscription_on_all_relays(subscription_id, filters)
    
    # 4. Send a DM
    recipient = "npub1..."  # Replace with actual recipient
    recipient_pk = PrivateKey.from_bech32(recipient).public_key.hex()
    
    dm = EncryptedDirectMessage()
    dm.encrypt(
        my_key.hex(),
        recipient_pubkey=recipient_pk,
        cleartext_content="Hello via Nostr!"
    )
    dm_event = dm.to_event()
    dm_event.sign(my_key.hex())
    relay_manager.publish_event(dm_event)
    print("DM sent!")
    
    # 5. Wait and process incoming DMs
    print("Waiting for DMs...")
    time.sleep(10)
    
    while relay_manager.message_pool.has_events():
        event_msg = relay_manager.message_pool.get_event()
        event = event_msg.event
        
        if event.kind == 4:
            enc_dm = EncryptedDirectMessage(
                my_key.public_key.hex(),
                event.pubkey,
                encrypted_message=event.content,
            )
            enc_dm.decrypt(my_key.hex())
            print(f"DM from {event.pubkey[:8]}...: {enc_dm.cleartext_content}")
    
    # 6. Cleanup
    relay_manager.close_all_relay_connections()
    print("Done!")

if __name__ == "__main__":
    main()
```

### Example 2: DM Chat Bot

```python
#!/usr/bin/env python3
"""Simple DM bot that echoes encrypted messages."""

from pynostr.key import PrivateKey
from pynostr.relay_manager import RelayManager
from pynostr.encrypted_dm import EncryptedDirectMessage
from pynostr.filters import FiltersList, Filters
from pynostr.event import EventKind
import uuid
import time
import os

class EchoBot:
    def __init__(self, nsec: str):
        self.key = PrivateKey.from_nsec(nsec)
        self.relay_manager = RelayManager(timeout=6)
        self.processed_ids = set()
        
    def connect(self):
        self.relay_manager.add_relay("wss://relay.damus.io")
        self.relay_manager.add_relay("wss://nos.lol")
        
        # Subscribe to DMs
        filters = FiltersList([
            Filters(
                kinds=[EventKind.ENCRYPTED_DIRECT_MESSAGE],
                authors=[self.key.public_key.hex()],
                limit=100
            )
        ])
        sub_id = uuid.uuid1().hex
        self.relay_manager.add_subscription_on_all_relays(sub_id, filters)
        
    def send_dm(self, recipient_pubkey: str, message: str):
        dm = EncryptedDirectMessage()
        dm.encrypt(
            self.key.hex(),
            recipient_pubkey=recipient_pubkey,
            cleartext_content=message
        )
        event = dm.to_event()
        event.sign(self.key.hex())
        self.relay_manager.publish_event(event)
        
    def process_dms(self):
        while self.relay_manager.message_pool.has_events():
            event_msg = self.relay_manager.message_pool.get_event()
            event = event_msg.event
            
            # Skip already processed
            if event.id in self.processed_ids:
                continue
            self.processed_ids.add(event.id)
            
            # Decrypt
            enc_dm = EncryptedDirectMessage(
                self.key.public_key.hex(),
                event.pubkey,
                encrypted_message=event.content,
            )
            enc_dm.decrypt(self.key.hex())
            
            print(f"Received from {event.pubkey[:8]}...: {enc_dm.cleartext_content}")
            
            # Echo back
            reply = f"Echo: {enc_dm.cleartext_content}"
            self.send_dm(event.pubkey, reply)
            print(f"Replied to {event.pubkey[:8]}...")
            
    def run(self):
        self.connect()
        print(f"Bot running as {self.key.public_key.bech32()}")
        
        try:
            while True:
                self.process_dms()
                time.sleep(2)
        except KeyboardInterrupt:
            print("\nShutting down...")
        finally:
            self.relay_manager.close_all_relay_connections()

if __name__ == "__main__":
    # Load key from environment or generate new
    nsec = os.environ.get("NOSTR_NSEC")
    if not nsec:
        key = PrivateKey()
        print(f"Generated new key: {key.bech32()}")
        print("Save this! Set NOSTR_NSEC env var to use it.")
        exit(1)
    
    bot = EchoBot(nsec)
    bot.run()
```

### Example 3: Multi-Relay Publisher

```python
#!/usr/bin/env python3
"""Publish to multiple relays with confirmation."""

from pynostr.key import PrivateKey
from pynostr.relay_manager import RelayManager
from pynostr.event import Event
import time

RELAYS = [
    "wss://relay.damus.io",
    "wss://nos.lol",
    "wss://nostr-pub.wellorder.net",
    "wss://relay.nostr.band",
]

def publish_note(nsec: str, content: str):
    key = PrivateKey.from_nsec(nsec)
    rm = RelayManager(timeout=10)
    
    for relay in RELAYS:
        rm.add_relay(relay)
    
    event = Event(content=content)
    event.sign(key.hex())
    
    print(f"Publishing to {len(RELAYS)} relays...")
    rm.publish_event(event)
    
    # Wait for confirmations
    time.sleep(5)
    
    ok_count = 0
    while rm.message_pool.has_ok_notices():
        ok = rm.message_pool.get_ok_notice()
        print(f"✓ {ok.relay_url}")
        ok_count += 1
    
    print(f"Published to {ok_count}/{len(RELAYS)} relays")
    rm.close_all_relay_connections()
```

---

## Event Kinds Reference

| Kind | Type | Description |
|------|------|-------------|
| **0** | Metadata | User profile (name, bio, picture) |
| **1** | Text Note | Regular text post |
| **2** | Recommend Relay | Relay recommendation |
| **3** | Contacts | Contact list (follows) |
| **4** | Encrypted DM | NIP-04 encrypted message |
| **5** | Event Deletion | Delete request |
| **6** | Repost | Repost/boost |
| **7** | Reaction | Like/emoji reaction |
| **8** | Badge Award | Badge award |
| **16** | Channel Creation | Chat channel |
| **40-41** | Channel Messages | Channel chat |
| **1063** | File Metadata | File upload metadata |
| **30023** | Long-form Content | Articles, blog posts |

### pynostr EventKind Enum

```python
from pynostr.event import EventKind

EventKind.METADATA                    # 0
EventKind.TEXT_NOTE                   # 1
EventKind.RECOMMEND_RELAY             # 2
EventKind.CONTACTS                    # 3
EventKind.ENCRYPTED_DIRECT_MESSAGE    # 4
EventKind.EVENT_DELETION              # 5
EventKind.REPOST                      # 6
EventKind.REACTION                    # 7
```

---

## Common Relays

| Relay | URL | Notes |
|-------|-----|-------|
| **Damus** | `wss://relay.damus.io` | Popular, reliable |
| **nos.lol** | `wss://nos.lol` | Fast, well-maintained |
| **Wellorder** | `wss://nostr-pub.wellorder.net` | Public, stable |
| **Nostr.Band** | `wss://relay.nostr.band` | Discovery-focused |
| **Purple Pagoda** | `wss://purplepagoda.io` | Community relay |

### Finding Relays

- **Nostr.watch** - Browse and test relay speeds
- **Relay Exchange** - Paid relay listings
- **nostr.directory** - Relay search engine

---

## NIP-17: The Modern Alternative

NIP-17 (Gift Wrap) improves on NIP-04:

| Feature | NIP-04 | NIP-17 |
|---------|--------|--------|
| **Recipient metadata** | Visible in tags | Encrypted |
| **Sender metadata** | Visible | Encrypted |
| **Relay visibility** | Sees who talks to whom | Only sees encrypted blob |
| **Complexity** | Simple | More complex (nested events) |
| **Adoption** | Wide | Growing |

### NIP-17 Structure

```json
{
  "kind": 1059,  // Gift wrap
  "content": "<encrypted inner event>",
  "tags": [["p", "<relay-pubkey>"]]
}
```

**Inner event** (encrypted):
```json
{
  "kind": 4,
  "content": "<actual message>",
  "tags": [["p", "<recipient>"]]
}
```

---

## Debugging Tips

### Common Issues

| Problem | Solution |
|---------|----------|
| Events not publishing | Check relay WebSocket connection, increase timeout |
| Can't decrypt DM | Verify using correct key pair (sender vs recipient) |
| No events received | Check subscription filters, ensure relays are connected |
| Connection drops | Use multiple relays, implement reconnection logic |

### Enable Logging

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

### Test Keys

```python
# Generate test keypair
from pynostr.key import PrivateKey
key = PrivateKey()
print(f"nsec: {key.bech32()}")
print(f"npub: {key.public_key.bech32()}")

# Save for testing
import os
os.environ["TEST_NSEC"] = key.bech32()
```

---

## Resources

- **Official NIPs:** https://github.com/nostr-protocol/nips
- **NIP-04 Spec:** https://nips.nostr.com/4
- **NIP-17 Spec:** https://nips.nostr.com/17
- **pynostr:** https://github.com/holgern/pynostr
- **python-nostr:** https://github.com/jeffthibault/python-nostr
- **Nostr.how:** https://nostr.how
- **Nostr.watch:** https://nostr.watch

---

## Quick Reference Cheatsheet

```python
# === KEYS ===
from pynostr.key import PrivateKey
pk = PrivateKey()                    # Generate
pk.bech32()                          # nsec format
pk.hex()                             # Hex format
PrivateKey.from_nsec("nsec1...")     # Load from nsec

# === RELAYS ===
from pynostr.relay_manager import RelayManager
rm = RelayManager(timeout=6)
rm.add_relay("wss://relay.damus.io")
rm.run_sync()
rm.close_all_relay_connections()

# === SUBSCRIBE ===
from pynostr.filters import FiltersList, Filters
from pynostr.event import EventKind
import uuid
filters = FiltersList([Filters(kinds=[EventKind.TEXT_NOTE], limit=100)])
sub_id = uuid.uuid1().hex
rm.add_subscription_on_all_relays(sub_id, filters)

# === PUBLISH ===
from pynostr.event import Event
event = Event(content="Hello!")
event.sign(pk.hex())
rm.publish_event(event)

# === ENCRYPTED DM ===
from pynostr.encrypted_dm import EncryptedDirectMessage
dm = EncryptedDirectMessage()
dm.encrypt(pk.hex(), recipient_pubkey, "Secret!")
event = dm.to_event()
event.sign(pk.hex())
rm.publish_event(event)

# === DECRYPT DM ===
enc_dm = EncryptedDirectMessage(my_pubkey, sender_pubkey, encrypted_content)
enc_dm.decrypt(pk.hex())
print(enc_dm.cleartext_content)

# === RECEIVE ===
while rm.message_pool.has_events():
    event = rm.message_pool.get_event().event
    print(event.content)
```
