"""Identity CLI helper functions."""

from __future__ import annotations

from pathlib import Path

from hermitcrab.config.schema import Config, IdentityConfig, normalize_nostr_pubkey


def effective_identity_model(config: Config, identity: IdentityConfig) -> str:
    """Return the effective interactive model ref for one identity."""
    return (
        identity.models.get("interactiveResponse")
        or identity.models.get("interactive_response")
        or config.identities.default_identity_model
        or config.agents.defaults.model
    )


def identity_rows(config: Config) -> list[tuple[str, IdentityConfig, Path]]:
    """Return configured identities for CLI display."""
    return [
        (name, identity, config.get_identity_path(name))
        for name, identity in sorted(config.identities.registry.items())
    ]


def remove_identity_routes(config: Config, identity_name: str) -> None:
    """Remove inbound Nostr routes for one identity."""
    removed_pubkeys = {
        normalize_nostr_pubkey(pubkey)
        for pubkey in config.channels.nostr.identity_bindings.pop(identity_name, [])
    }
    if not removed_pubkeys:
        return

    remaining_routed = {
        normalize_nostr_pubkey(pubkey)
        for bindings in config.channels.nostr.identity_bindings.values()
        for pubkey in bindings
    }
    config.channels.nostr.allowed_pubkeys = [
        pubkey
        for pubkey in config.channels.nostr.allowed_pubkeys
        if pubkey.strip().lower() in {"*", "all"}
        or normalize_nostr_pubkey(pubkey) not in removed_pubkeys
        or normalize_nostr_pubkey(pubkey) in remaining_routed
    ]


def bind_nostr_pubkey_to_identity(config: Config, identity_name: str, pubkey: str) -> str:
    """Bind one normalized sender pubkey to an identity and maintain allowlist."""
    normalized = normalize_nostr_pubkey(pubkey)
    for existing_name, pubkeys in config.channels.nostr.identity_bindings.items():
        if existing_name == identity_name:
            continue
        if normalized in {normalize_nostr_pubkey(value) for value in pubkeys}:
            raise ValueError(f"pubkey already routed to user '{existing_name}'")

    routes = config.channels.nostr.identity_bindings.setdefault(identity_name, [])
    if normalized not in {normalize_nostr_pubkey(value) for value in routes}:
        routes.append(normalized)

    allowed = config.channels.nostr.allowed_pubkeys
    allowed_modes = {value.strip().lower() for value in allowed}
    allowed_pubkeys = {
        normalize_nostr_pubkey(value)
        for value in allowed
        if value.strip().lower() not in {"*", "all"}
    }
    if not allowed_modes.intersection({"*", "all"}) and normalized not in allowed_pubkeys:
        allowed.append(normalized)
    return normalized


async def send_nostr_onboarding_intro(
    config: Config,
    recipient_pubkey: str,
    identity_name: str,
) -> bool:
    """Best-effort onboarding intro DM; never raise to caller."""
    if not config.channels.nostr.enabled or not config.channels.nostr.private_key:
        return False

    try:
        from hermitcrab.bus.events import OutboundMessage
        from hermitcrab.bus.queue import MessageBus
        from hermitcrab.channels.nostr import NostrChannel
    except Exception:
        return False

    bus = MessageBus()
    channel = NostrChannel(
        config.channels.nostr,
        bus,
        identity_resolver=config.resolve_nostr_sender_identity,
    )
    try:
        await channel.start()
        await channel.send(
            OutboundMessage(
                channel="nostr",
                chat_id=recipient_pubkey,
                content=(
                    f"Hello from HermitCrab. You were added as user '{identity_name}'. "
                    "If this was unexpected, contact the operator."
                ),
            )
        )
        return True
    except Exception:
        return False
    finally:
        try:
            await channel.stop()
        except Exception:
            pass
