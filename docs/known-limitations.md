# Known limitations

This page records behavior that is deliberately bounded. Treat it as part of the
data and delivery contract, not as a bug list.

## Nostr offline catch-up is not a mailbox

HermitCrab does not drain an unbounded offline DM backlog on startup. The exact
behavior depends on the protocol:

### NIP-04 (legacy)

- The subscription `since` value is the process start time.
- Inbound NIP-04 events older than process start are rejected, even if a relay
  replays them.
- A DM sent while the gateway is offline is **not** acted on later.

### NIP-17

- NIP-17 gift-wrap timestamps are randomized up to **2 days into the past**, so
  the subscription starts at `process_start - 2 days`.
- The startup catch-up query asks each configured relay for gift wraps with
  `limit: 100`, merges results across relays, and deduplicates by wrap event id.
- The live subscription asks for `limit: 25` events per relay per connection and
  processes NIP-17 events that arrive before EOSE. NIP-04 events before EOSE are
  ignored.
- Messages whose canonical (rumor) timestamp is older than process start are
  **skipped** during startup catch-up, recorded as processed, and audited as
  `nostr.inbound_catchup_old_skipped`. Ask the sender to resend if the request
  still matters.
- There is no pagination beyond those limits. Backlog completeness across more
  than 25 or 100 events, repeated restarts, or relay overlap is not guaranteed.

### Common behavior

- Canonical ids already processed are persisted in
  `<root>/system/nostr/processed-events.jsonl`; in-process duplicate detection uses
  a bounded in-memory set (10,000 ids, cleared wholesale when full).
- The durable inbound ledger (below) recovers events that HermitCrab already
  received. It does **not** ask relays for older events, so it does not extend the
  lookback windows above.
- Multiple relays are merged and deduplicated; the same event delivered by two
  relays is handled once.

Do not describe HermitCrab as an always-available Nostr mailbox. A future release
may add paginated backfill with durable progress and an explicit reconfirmation
policy; until then, the limits above are the contract.

## Durable inbound work

Channel events carrying a canonical id are written to
`<root>/system/inbox/inbound.sqlite3` before they are queued. States are:

| State | Meaning |
| --- | --- |
| `pending` | Recorded but not yet consumed; replayed on the next gateway start |
| `processing` | Claimed by an agent turn |
| `completed` | The turn finished and was acknowledged |
| `failed` | The turn raised; not replayed automatically |
| `uncertain` | The process crashed mid-turn; never replayed automatically |

An `uncertain` row is surfaced in logs with its canonical id. Treat it as "the
request may or may not have had side effects" and ask the sender to confirm,
rather than assuming it was lost or repeating it blindly.

## Reminder and notification delivery

Due reminders are recorded in `<root>/system/outbox/delivery.sqlite3` with states
`queued`, `sending`, `sent`, `failed`, and `uncertain`, plus an attempt count and
last error. A reminder advances its schedule only after the target channel
accepts the message.

- Retries are bounded (default 5 attempts). After that the reminder stays active
  with `delivery_status: failed` and `last_delivery_error` in its frontmatter for
  inspection; it is not silently consumed.
- An interrupted send (`sending` at crash) becomes `uncertain` and is not resent
  automatically, because the previous attempt may have reached the relay.
- "Sent" means the channel handed the event to a relay or platform API. It is not
  proof that the person read it, and relay `OK` acceptance is logged separately.

Reminders are the first delivery path to use this ledger. Cron results, heartbeat
notifications, and ordinary replies still use the in-memory outbound bus, so a
crash between producing and dispatching those messages can still lose them.

## Session history is authoritative

The session database contains authoritative conversation history. Markdown
memory, indexes, and distilled knowledge are derived or user-facing stores; do not
assume they can reconstruct a deleted session.
