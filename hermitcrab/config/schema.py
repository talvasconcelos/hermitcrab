"""Configuration schema using Pydantic."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel
from pydantic_settings import BaseSettings

from hermitcrab.providers.registry import PROVIDERS, find_by_name, normalize_provider_name


class Base(BaseModel):
    """Base model that accepts both camelCase and snake_case keys."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


def default_nostr_relays() -> list[str]:
    """Return the default bootstrap relays for Nostr connectivity."""
    return [
        "wss://relay.damus.io",
        "wss://relay.primal.net",
        "wss://nostr-pub.wellorder.net",
    ]


_HEX_PUBKEY_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_HEX_PRIVATE_KEY_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_IDENTITY_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_RESERVED_IDENTITY_NAMES = frozenset({"shared", "system"})


def normalize_nostr_pubkey(value: str) -> str:
    """Normalize configured Nostr key to lowercase pubkey hex."""
    key = value.strip()
    if _HEX_PUBKEY_RE.fullmatch(key):
        return key.lower()

    try:
        from pynostr.key import PrivateKey, PublicKey

        if key.startswith("npub"):
            return PublicKey.from_npub(key).hex().lower()
        if key.startswith("nsec"):
            return PrivateKey.from_nsec(key).public_key.hex().lower()
    except Exception as exc:
        raise ValueError("pubkey must be npub/nsec or 64-char hex") from exc

    raise ValueError("pubkey must be npub/nsec or 64-char hex")


def normalize_nostr_private_key(value: str) -> str:
    """Normalize configured Nostr private key to lowercase hex."""
    key = value.strip()
    if _HEX_PRIVATE_KEY_RE.fullmatch(key):
        return key.lower()

    try:
        from pynostr.key import PrivateKey

        if key.startswith("nsec"):
            return PrivateKey.from_nsec(key).hex().lower()
    except Exception as exc:
        raise ValueError("private key must be nsec or 64-char hex") from exc

    raise ValueError("private key must be nsec or 64-char hex")


def nostr_pubkey_from_private_key(value: str) -> str:
    """Return lowercase hex public key for a Nostr private key."""
    private_key = normalize_nostr_private_key(value)
    try:
        from pynostr.key import PrivateKey

        return PrivateKey.from_hex(private_key).public_key.hex().lower()
    except Exception as exc:
        raise ValueError("private key must be nsec or 64-char hex") from exc


def generate_nostr_keypair() -> tuple[str, str]:
    """Generate a Nostr private/public keypair as lowercase hex strings."""
    from pynostr.key import PrivateKey

    private_key = PrivateKey()
    return private_key.hex().lower(), private_key.public_key.hex().lower()


def _validate_identity_slug(value: str, *, field_name: str) -> str:
    """Validate a filesystem-safe identity slug."""
    slug = value.strip()
    if not slug:
        raise ValueError(f"{field_name} must be non-empty")
    if slug in _RESERVED_IDENTITY_NAMES:
        raise ValueError(f"{field_name} '{slug}' is reserved")
    if not _IDENTITY_SLUG_RE.fullmatch(slug):
        raise ValueError(
            f"{field_name} must start with a letter or number and contain only letters, numbers, "
            "underscores, or hyphens"
        )
    return slug


class TelegramConfig(Base):
    """Telegram channel configuration."""

    enabled: bool = False
    token: str = ""  # Bot token from @BotFather
    allow_from: list[str] = Field(default_factory=list)  # Allowed user IDs or usernames
    proxy: str | None = (
        None  # HTTP/SOCKS5 proxy URL, e.g. "http://127.0.0.1:7890" or "socks5://127.0.0.1:1080"
    )
    reply_to_message: bool = False  # If true, bot replies quote the original message


class EmailConfig(Base):
    """Email channel configuration (IMAP inbound + SMTP outbound)."""

    enabled: bool = False
    consent_granted: bool = False  # Explicit owner permission to access mailbox data

    # IMAP (receive)
    imap_host: str = ""
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""
    imap_mailbox: str = "INBOX"
    imap_use_ssl: bool = True

    # SMTP (send)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    from_address: str = ""

    # Behavior
    auto_reply_enabled: bool = (
        True  # If false, inbound email is read but no automatic reply is sent
    )
    poll_interval_seconds: int = 30
    mark_seen: bool = True
    max_body_chars: int = 12000
    subject_prefix: str = "Re: "
    allow_from: list[str] = Field(default_factory=list)  # Allowed sender email addresses


class NostrConfig(Base):
    """Nostr channel configuration for legacy NIP-04 or modern NIP-17 DMs."""

    enabled: bool = False
    private_key: str = ""  # nsec or hex private key (required if enabled)
    relays: list[str] = Field(default_factory=lambda: default_nostr_relays())  # Default popular relays
    protocol: Literal["nip04", "nip17"] = "nip04"  # NIP-04 legacy DMs, NIP-17 modern chat DMs
    nip17_fallback_to_configured_relays: bool = (
        True  # If kind 10050 is missing/unreadable, fall back to configured relays
    )
    nip17_relay_discovery_timeout_s: float = 4.0
    nip17_relay_cache_ttl_s: int = 10 * 60
    allowed_pubkeys: list[str] = Field(
        default_factory=list
    )  # npub/hex, or "*" for open mode, or [] for strict/deny-all
    identity_bindings: dict[str, list[str]] = Field(
        default_factory=dict
    )  # {"identity-name": ["<sender-pubkey-hex>", ...]}

    @model_validator(mode="before")
    @classmethod
    def reject_workspace_bindings(cls, data: Any) -> Any:
        """Reject removed workspace routing config."""
        if isinstance(data, dict) and (
            "workspaceBindings" in data or "workspace_bindings" in data
        ):
            raise ValueError("Nostr workspace bindings were removed; use identityBindings")
        return data

    def validate_for_use(self) -> None:
        """
        Validate configuration when Nostr channel is enabled.

        Raises:
            ValueError: If private_key is missing when enabled.
        """
        if self.enabled and not self.private_key:
            raise ValueError(
                "Nostr channel is enabled but private_key is not configured. "
                "Set nostr.private_key in config.json (nsec or hex format). "
                "Generate a key with: python -c 'from pynostr.key import PrivateKey; "
                'k = PrivateKey(); print(f"nsec: {k.bech32()}")\''
            )


class ChannelsConfig(Base):
    """Configuration for chat channels."""

    send_progress: bool = True  # stream agent's text progress to the channel
    send_tool_hints: bool = False  # stream tool-call hints (e.g. read_file("…"))
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)
    nostr: NostrConfig = Field(default_factory=NostrConfig)


class AgentJobModels(Base):
    """
    Model configuration per job class.

    Fallback scheme (explicit, not heuristic):
    1. Use job-specific model if configured (non-empty string)
    2. For INTERACTIVE_RESPONSE: never fall back (must be configured)
    3. For JOURNAL_SYNTHESIS/REFLECTION: fall back to primary model
    4. For DISTILLATION: None means "skip" (local only, don't escalate)
    5. For SUMMARISATION: fall back to primary model

    Configuration examples:
    ```json
    {
      "interactive_response": "anthropic/claude-opus-4-5",  // Primary
      "journal_synthesis": "ollama/llama-3.2-3b",          // Weak local
      "distillation": "ollama/phi-3-mini",                 // Local only
      "reflection": "",                                     // Empty = use primary
      "summarisation": null                                 // Null = use primary
    }
    ```

    Reasoning effort (for models that support it, e.g., OpenAI o1/o3, thinking models):
    - "none": Disable reasoning/thinking (fast, deterministic tasks)
    - "low": Minimal reasoning (quick tasks)
    - "medium": Default reasoning (balanced)
    - "high": Maximum reasoning (complex problems)

    LiteLLM silently ignores this parameter for models that don't support it.
    """

    interactive_response: str = ""  # Required (falls back to primary if empty)
    journal_synthesis: str | None = None  # None = use primary
    distillation: str | None = None  # None = skip (local only, don't escalate)
    reflection: str | None = None  # None = use primary
    summarisation: str | None = None  # None = use primary
    subagent: str | None = None  # None = use primary (dedicated model for subagents)

    # Reasoning effort control (passed to LiteLLM, ignored by unsupported models)
    reasoning_effort: Literal["none", "low", "medium", "high"] = "medium"

    def get_model(self, job_class: str, primary_model: str) -> str | None:
        """
        Get model for a job class with explicit fallback logic.

        Args:
            job_class: Job class name (e.g., "interactive_response").
            primary_model: Primary/interactive model as ultimate fallback.

        Returns:
            Model string, or None to skip (distillation only).

        Fallback rules:
        - Empty string ("") → use primary_model
        - None → use primary_model (except distillation)
        - Distillation with None → return None (skip, local only)
        """
        # Get the job-specific model
        job_model = getattr(self, job_class, None)

        # Case 1: Explicitly configured (non-empty string)
        if job_model and isinstance(job_model, str) and job_model.strip():
            return job_model.strip()

        # Case 2: Distillation with None/empty → skip (local only policy)
        if job_class == "distillation":
            return None  # Don't escalate to external model

        # Case 3: All other jobs → fall back to primary model
        return primary_model


class AgentDefaults(Base):
    """Default agent configuration."""

    workspace: str | None = None
    model: str = "anthropic/claude-opus-4-5"  # Primary model for interactive responses
    job_models: AgentJobModels = Field(default_factory=AgentJobModels)
    enable_distillation: bool = (
        False  # Distillation is fallback cognition, disabled unless explicitly enabled
    )
    max_tokens: int = 8192
    temperature: float = 0.1
    max_tool_iterations: int = 40
    memory_window: int = 100
    inactivity_timeout_s: int = 30 * 60
    llm_max_retries: int = 3
    llm_retry_base_delay_s: float = 0.6
    max_loop_seconds: int = 5 * 60
    max_identical_tool_cycles: int = 2
    memory_context_max_chars: int = 10000
    memory_context_max_items_per_category: int = 20
    memory_context_max_item_chars: int = 500


class SystemConfig(Base):
    """System-owned operational and generated state."""

    root: str = "system"

    @model_validator(mode="after")
    def validate_root(self) -> "SystemConfig":
        """Require non-empty system root."""
        self.root = self.root.strip()
        if not self.root:
            raise ValueError("system.root must be non-empty")
        return self


class IdentityConfig(Base):
    """Identity-specific configuration."""

    root: str | None = None
    label: str | None = None
    nostr_public_key: str = ""
    nostr_private_key: str = ""
    role: str = "managed"
    models: dict[str, str] = Field(default_factory=dict)
    excluded_models: list[str] = Field(default_factory=list)
    active: bool = True

    @model_validator(mode="after")
    def validate_root(self) -> "IdentityConfig":
        """Reject blank explicit roots."""
        if self.root is not None:
            self.root = self.root.strip()
            if not self.root:
                raise ValueError("identity root must be non-empty when set")

        self.nostr_public_key = self.nostr_public_key.strip()
        self.nostr_private_key = self.nostr_private_key.strip()
        if self.nostr_private_key:
            self.nostr_private_key = normalize_nostr_private_key(self.nostr_private_key)
            self.nostr_public_key = nostr_pubkey_from_private_key(self.nostr_private_key)
        elif self.nostr_public_key:
            raise ValueError("identity nostrPrivateKey is required when nostrPublicKey is set")
        else:
            self.nostr_private_key, self.nostr_public_key = generate_nostr_keypair()
        return self


class IdentitiesConfig(Base):
    """Identity registry and defaults."""

    root: str = "identities"
    owner_identity: str = "owner"
    default_identity_model: str | None = None
    registry: dict[str, IdentityConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_registry(self) -> "IdentitiesConfig":
        """Normalize identity names and reject reserved or unsafe slugs."""
        self.root = self.root.strip()
        if not self.root:
            raise ValueError("identities.root must be non-empty")

        self.owner_identity = _validate_identity_slug(self.owner_identity, field_name="ownerIdentity")

        normalized: dict[str, IdentityConfig] = {}
        for name, identity in self.registry.items():
            slug = _validate_identity_slug(name, field_name="identity registry key")
            normalized[slug] = identity
        if self.owner_identity not in normalized:
            normalized[self.owner_identity] = IdentityConfig(role="owner")

        seen_pubkeys: dict[str, str] = {}
        for name, identity in normalized.items():
            previous = seen_pubkeys.get(identity.nostr_public_key)
            if previous is not None:
                raise ValueError(
                    "identity Nostr pubkeys must be unique; "
                    f"{identity.nostr_public_key} is assigned to both '{previous}' and '{name}'"
                )
            seen_pubkeys[identity.nostr_public_key] = name
        self.registry = normalized
        return self


class NamedModelConfig(Base):
    """Reusable named model definition with optional provider-specific request options."""

    model: str
    reasoning_effort: Literal["none", "low", "medium", "high"] | None = None
    provider_options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_model(self) -> "NamedModelConfig":
        """Require a non-empty model string."""
        self.model = self.model.strip()
        if not self.model:
            raise ValueError("named model entries must include a non-empty model")
        return self


class ModelAliasConfig(Base):
    """Structured model alias with optional thinking control."""

    model: str
    reasoning_effort: Literal["none", "low", "medium", "high"] | None = None
    thinking: bool | None = None

    @model_validator(mode="after")
    def validate_model(self) -> "ModelAliasConfig":
        """Require a non-empty model string."""
        self.model = self.model.strip()
        if not self.model:
            raise ValueError("model alias entries must include a non-empty model")
        return self

    def effective_reasoning_effort(self) -> Literal["none", "low", "medium", "high"] | None:
        """Resolve the effective reasoning override for this alias."""
        if self.reasoning_effort is not None:
            return self.reasoning_effort
        if self.thinking is False:
            return "none"
        return None


class AgentsConfig(Base):
    """Agent configuration."""

    defaults: AgentDefaults = Field(default_factory=AgentDefaults)
    model_aliases: dict[str, str | ModelAliasConfig] = Field(
        default_factory=dict
    )  # Friendly aliases: {"qwen": "ollama/qwen2.5:7b"} or {"fast": {"model": "...", "thinking": false}}


class ProviderConfig(Base):
    """LLM provider configuration."""

    api_key: str = ""
    api_base: str | None = None
    extra_headers: dict[str, str] | None = None  # Custom headers (e.g. APP-Code for AiHubMix)


class ProvidersConfig(Base):
    """Configuration for LLM providers."""

    custom: ProviderConfig = Field(default_factory=ProviderConfig)  # Any OpenAI-compatible endpoint
    anthropic: ProviderConfig = Field(default_factory=ProviderConfig)
    openai: ProviderConfig = Field(default_factory=ProviderConfig)
    openrouter: ProviderConfig = Field(default_factory=ProviderConfig)
    deepseek: ProviderConfig = Field(default_factory=ProviderConfig)
    groq: ProviderConfig = Field(default_factory=ProviderConfig)
    zhipu: ProviderConfig = Field(default_factory=ProviderConfig)
    dashscope: ProviderConfig = Field(default_factory=ProviderConfig)  # 阿里云通义千问
    vllm: ProviderConfig = Field(default_factory=ProviderConfig)
    gemini: ProviderConfig = Field(default_factory=ProviderConfig)
    moonshot: ProviderConfig = Field(default_factory=ProviderConfig)
    minimax: ProviderConfig = Field(default_factory=ProviderConfig)
    aihubmix: ProviderConfig = Field(default_factory=ProviderConfig)  # AiHubMix API gateway
    siliconflow: ProviderConfig = Field(
        default_factory=ProviderConfig
    )  # SiliconFlow (硅基流动) API gateway
    volcengine: ProviderConfig = Field(
        default_factory=ProviderConfig
    )  # VolcEngine (火山引擎) API gateway
    openai_oauth: ProviderConfig = Field(default_factory=ProviderConfig)  # ChatGPT/Codex OAuth
    openai_codex: ProviderConfig = Field(default_factory=ProviderConfig)  # OpenAI Codex (OAuth)
    github_copilot: ProviderConfig = Field(default_factory=ProviderConfig)  # Github Copilot (OAuth)
    ollama: ProviderConfig = Field(default_factory=ProviderConfig)  # Ollama via LiteLLM routing
    nvidia_nim: ProviderConfig = Field(default_factory=ProviderConfig)  # NVIDIA NIM API


class HeartbeatConfig(Base):
    """Heartbeat service configuration."""

    enabled: bool = True
    interval_s: int = 30 * 60  # 30 minutes


class ReminderPollingConfig(Base):
    """Reminder delivery polling configuration."""

    interval_s: int = 60  # 1 minute


class ReflectionPromotionConfig(Base):
    """
    Reflection promotion to bootstrap files configuration.

    Controls how reflections are automatically promoted to update
    AGENTS.md, SOUL.md, IDENTITY.md, and TOOLS.md files.
    """

    auto_promote: bool = (
        False  # Safer default: propose/log reflections, don't self-edit files automatically
    )
    target_files: list[str] = Field(
        default_factory=lambda: ["AGENTS.md", "SOUL.md", "IDENTITY.md", "TOOLS.md"]
    )  # Which bootstrap files to update
    max_file_lines: int = 500  # Archive old sections if file exceeds this limit
    notify_user: bool = True  # Inform user when bootstrap files are updated


class ReflectionConfig(Base):
    """Reflection system configuration."""

    promotion: ReflectionPromotionConfig = Field(default_factory=ReflectionPromotionConfig)


class GatewayConfig(Base):
    """Gateway/server configuration."""

    host: str = "0.0.0.0"
    port: int = 18790
    heartbeat: HeartbeatConfig = Field(default_factory=HeartbeatConfig)
    reminders: ReminderPollingConfig = Field(default_factory=ReminderPollingConfig)


class WebSearchConfig(Base):
    """Web search tool configuration."""

    api_key: str = ""  # Brave Search API key
    max_results: int = 5


class WebToolsConfig(Base):
    """Web tools configuration."""

    search: WebSearchConfig = Field(default_factory=WebSearchConfig)


class ExecToolConfig(Base):
    """Shell exec tool configuration."""

    timeout: int = 60


class MCPServerConfig(Base):
    """MCP server connection configuration (stdio or HTTP)."""

    command: str = ""  # Stdio: Command to run (e.g. "npx")
    args: list[str] = Field(default_factory=list)  # Stdio: Command arguments
    env: dict[str, str] = Field(default_factory=dict)  # Stdio: Extra env vars
    url: str = ""  # HTTP: Streamable HTTP endpoint URL
    headers: dict[str, str] = Field(default_factory=dict)  # HTTP: Custom HTTP headers
    tool_timeout: int = 30  # Seconds before a tool call is cancelled


class ToolsConfig(Base):
    """Tools configuration."""

    web: WebToolsConfig = Field(default_factory=WebToolsConfig)
    exec: ExecToolConfig = Field(default_factory=ExecToolConfig)
    restrict_to_workspace: bool = False  # If true, restrict all tool access to workspace directory
    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)


@dataclass(frozen=True)
class ResolvedModelConfig:
    """Resolved model reference with request-level metadata."""

    model: str | None
    reasoning_effort: Literal["none", "low", "medium", "high"] | None = None
    provider_options: dict[str, Any] | None = None
    name: str | None = None


@dataclass(frozen=True)
class NostrIdentityResolution:
    """Resolved identity target for one inbound Nostr sender."""

    target: Literal["identity", "denied"]
    identity_name: str | None = None
    identity_path: Path | None = None
    normalized_pubkey: str | None = None
    reason: str | None = None


class Config(BaseSettings):
    """Root configuration for hermitcrab."""

    root: str = "~/.hermitcrab"
    system: SystemConfig = Field(default_factory=SystemConfig)
    identities: IdentitiesConfig = Field(default_factory=IdentitiesConfig)
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    models: dict[str, NamedModelConfig] = Field(default_factory=dict)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    reflection: ReflectionConfig = Field(default_factory=ReflectionConfig)

    @model_validator(mode="before")
    @classmethod
    def reject_removed_workspace_registry(cls, data: Any) -> Any:
        """Reject removed named workspace registry config."""
        if isinstance(data, dict) and "workspaces" in data:
            raise ValueError("workspaces config was removed; use identities.registry")
        return data

    @model_validator(mode="after")
    def validate_root(self) -> "Config":
        """Require a non-empty HermitCrab root path."""
        self.root = self.root.strip()
        if not self.root:
            raise ValueError("root must be non-empty")
        return self

    @model_validator(mode="after")
    def validate_nostr_routing_bindings(self) -> "Config":
        """Validate Nostr identity routing binding rules."""
        identity_bindings = self.channels.nostr.identity_bindings
        if not identity_bindings:
            return self

        seen_pubkeys: dict[str, str] = {}
        for identity_name, pubkeys in identity_bindings.items():
            if identity_name not in self.identities.registry:
                raise ValueError(
                    f"channels.nostr.identity_bindings references unknown identity '{identity_name}'"
                )
            for pubkey in pubkeys:
                normalized = normalize_nostr_pubkey(pubkey)
                previous = seen_pubkeys.get(normalized)
                if previous is not None:
                    raise ValueError(
                        "channels.nostr routing pubkeys must be unique; "
                        f"{normalized} is assigned to both '{previous}' and 'identity:{identity_name}'"
                    )
                seen_pubkeys[normalized] = f"identity:{identity_name}"

        return self

    @property
    def hermitcrab_root_path(self) -> Path:
        """Get expanded HermitCrab root path."""
        return Path(self.root).expanduser()

    @property
    def system_root_path(self) -> Path:
        """Get expanded system-owned state root."""
        return self._resolve_under_root(self.system.root)

    @property
    def identities_root_path(self) -> Path:
        """Get expanded identities root."""
        return self._resolve_under_root(self.identities.root)

    @property
    def owner_identity_name(self) -> str:
        """Return the configured owner identity name."""
        return self.identities.owner_identity

    @property
    def owner_identity_root_path(self) -> Path:
        """Get expanded owner identity root."""
        return self.get_identity_path(self.owner_identity_name)

    def get_identity_path(self, identity_name: str | None = None) -> Path:
        """Resolve an identity root path."""
        name = identity_name or self.owner_identity_name
        slug = _validate_identity_slug(name, field_name="identity name")
        identity = self.identities.registry.get(slug)
        root = identity.root if identity and identity.root else slug
        path = Path(root).expanduser()
        if not path.is_absolute():
            path = self.identities_root_path / path
        return path

    def configured_identities(self) -> dict[str, Path]:
        """Return configured identity paths."""
        return {
            name: self.get_identity_path(name)
            for name in self.identities.registry
        }

    @property
    def workspace_path(self) -> Path:
        """Get the owner identity root path."""
        return self.owner_identity_root_path

    @property
    def admin_workspace_path(self) -> Path:
        """Get the owner identity root path."""
        return self.workspace_path

    def _resolve_under_root(self, value: str) -> Path:
        """Resolve a config path under the HermitCrab root unless absolute."""
        path = Path(value).expanduser()
        if path.is_absolute():
            return path
        return self.hermitcrab_root_path / path

    def normalized_nostr_identity_bindings(self) -> dict[str, set[str]]:
        """Return normalized Nostr identity bindings."""
        return {
            identity_name: {normalize_nostr_pubkey(pubkey) for pubkey in pubkeys}
            for identity_name, pubkeys in self.channels.nostr.identity_bindings.items()
        }

    def normalized_nostr_allowed_pubkeys(self) -> set[str]:
        """Return normalized Nostr allowlist."""
        normalized: set[str] = set()
        for pubkey in self.channels.nostr.allowed_pubkeys:
            value = pubkey.strip().lower()
            if not value:
                continue
            if value in {"*", "all"}:
                return {"*"}
            normalized.add(normalize_nostr_pubkey(pubkey))
        return normalized

    def resolve_nostr_sender_identity(self, sender_pubkey: str) -> NostrIdentityResolution:
        """Resolve inbound Nostr sender to an identity or denial."""
        try:
            normalized_pubkey = normalize_nostr_pubkey(sender_pubkey)
        except ValueError:
            return NostrIdentityResolution(target="denied", reason="invalid_pubkey")

        for identity_name, pubkeys in self.normalized_nostr_identity_bindings().items():
            if normalized_pubkey in pubkeys:
                identity = self.identities.registry.get(identity_name)
                if identity is None:
                    return NostrIdentityResolution(
                        target="denied",
                        normalized_pubkey=normalized_pubkey,
                        reason="identity_not_configured",
                    )
                if not identity.active:
                    return NostrIdentityResolution(
                        target="denied",
                        identity_name=identity_name,
                        normalized_pubkey=normalized_pubkey,
                        reason="identity_inactive",
                    )
                return NostrIdentityResolution(
                    target="identity",
                    identity_name=identity_name,
                    identity_path=self.get_identity_path(identity_name),
                    normalized_pubkey=normalized_pubkey,
                    reason="identity_binding",
                )

        allowed_pubkeys = self.normalized_nostr_allowed_pubkeys()
        if "*" in allowed_pubkeys or normalized_pubkey in allowed_pubkeys:
            owner_identity = self.identities.registry[self.owner_identity_name]
            if not owner_identity.active:
                return NostrIdentityResolution(
                    target="denied",
                    identity_name=self.owner_identity_name,
                    normalized_pubkey=normalized_pubkey,
                    reason="owner_identity_inactive",
                )
            return NostrIdentityResolution(
                target="identity",
                identity_name=self.owner_identity_name,
                identity_path=self.owner_identity_root_path,
                normalized_pubkey=normalized_pubkey,
                reason="allowlist_owner_fallback",
            )

        return NostrIdentityResolution(
            target="denied",
            normalized_pubkey=normalized_pubkey,
            reason="not_allowed",
        )

    def resolve_model_config(self, model: str | None = None) -> ResolvedModelConfig:
        """Resolve a model reference to an actual model string and metadata."""
        ref = model or self.agents.defaults.model
        if ref is None:
            return ResolvedModelConfig(model=None)

        ref = ref.strip()
        if not ref:
            return ResolvedModelConfig(model=ref)

        named = self.models.get(ref)
        if named:
            return ResolvedModelConfig(
                model=named.model,
                reasoning_effort=named.reasoning_effort,
                provider_options=dict(named.provider_options),
                name=ref,
            )

        return ResolvedModelConfig(model=ref)

    def _match_provider(
        self, model: str | None = None
    ) -> tuple["ProviderConfig | None", str | None]:
        """Match provider config and its registry name. Returns (config, spec_name)."""
        resolved_model = self.resolve_model_config(model).model or ""
        raw_model_prefix = resolved_model.split("/", 1)[0] if "/" in resolved_model else ""
        model_lower = resolved_model.lower()
        model_normalized = model_lower.replace("-", "_")
        model_prefix = model_lower.split("/", 1)[0] if "/" in model_lower else ""
        normalized_prefix = normalize_provider_name(raw_model_prefix)

        def _kw_matches(kw: str) -> bool:
            kw = kw.lower()
            return kw in model_lower or kw.replace("-", "_") in model_normalized

        # Explicit provider prefix wins — prevents `github-copilot/...codex` matching openai_codex.
        for spec in PROVIDERS:
            p = getattr(self.providers, spec.name, None)
            if p and model_prefix and normalized_prefix == spec.name:
                if spec.is_oauth or spec.is_local or p.api_key:
                    return p, spec.name

        # Match by keyword (order follows PROVIDERS registry)
        for spec in PROVIDERS:
            p = getattr(self.providers, spec.name, None)
            if p and any(_kw_matches(kw) for kw in spec.keywords):
                if spec.is_oauth or spec.is_local or p.api_key:
                    return p, spec.name

        # Fallback: gateways first, then others (follows registry order)
        # OAuth providers are NOT valid fallbacks — they require explicit model selection
        for spec in PROVIDERS:
            if spec.is_oauth:
                continue
            p = getattr(self.providers, spec.name, None)
            if p and (p.api_key or spec.is_local):
                return p, spec.name
        return None, None

    def get_provider(self, model: str | None = None) -> ProviderConfig | None:
        """Get matched provider config (api_key, api_base, extra_headers). Falls back to first available."""
        p, _ = self._match_provider(model)
        return p

    def get_provider_name(self, model: str | None = None) -> str | None:
        """Get the registry name of the matched provider (e.g. "deepseek", "openrouter")."""
        _, name = self._match_provider(model)
        return name

    def get_api_key(self, model: str | None = None) -> str | None:
        """Get API key for the given model. Falls back to first available key."""
        p = self.get_provider(model)
        return p.api_key if p else None

    def get_api_base(self, model: str | None = None) -> str | None:
        """Get API base URL for the given model. Applies default URLs for known gateways."""
        p, name = self._match_provider(model)
        if p and p.api_base:
            return p.api_base
        # Only gateways get a default api_base here. Standard providers
        # (like Moonshot) set their base URL via env vars in _setup_env
        # to avoid polluting the global litellm.api_base.
        if name:
            spec = find_by_name(name)
            if spec and (spec.is_gateway or spec.is_local) and spec.default_api_base:
                return spec.default_api_base
        return None

    model_config = ConfigDict(env_prefix="HERMITCRAB_", env_nested_delimiter="__")
