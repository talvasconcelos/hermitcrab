"""Configuration schema using Pydantic."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel
from pydantic_settings import BaseSettings

from hermitcrab.providers.registry import PROVIDERS, find_by_name


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


def normalize_nostr_pubkey(value: str) -> str:
    """Normalize configured Nostr sender pubkey into lowercase hex."""
    pubkey = value.strip()
    if _HEX_PUBKEY_RE.fullmatch(pubkey):
        return pubkey.lower()
    raise ValueError("pubkey must be 64-char hex")


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
    workspace_bindings: dict[str, list[str]] = Field(
        default_factory=dict
    )  # {"workspace-name": ["<sender-pubkey-hex>", ...]}

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

    workspace: str = "~/.hermitcrab/workspace"
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


class WorkspaceConfig(Base):
    """Named personal workspace configuration."""

    path: str
    label: str | None = None
    channel_only: bool = True

    @model_validator(mode="after")
    def validate_path(self) -> "WorkspaceConfig":
        """Require non-empty path."""
        self.path = self.path.strip()
        if not self.path:
            raise ValueError("workspace entries must include a non-empty path")
        return self


class WorkspacesConfig(Base):
    """Owner-managed personal workspace registry."""

    root: str = "~/.hermitcrab/workspaces"
    registry: dict[str, WorkspaceConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_registry(self) -> "WorkspacesConfig":
        """Normalize names and reject blank registry keys."""
        normalized: dict[str, WorkspaceConfig] = {}
        for name, workspace in self.registry.items():
            slug = name.strip()
            if not slug:
                raise ValueError("workspace registry keys must be non-empty")
            normalized[slug] = workspace
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
    qwen_oauth: ProviderConfig = Field(default_factory=ProviderConfig)  # Qwen Portal OAuth
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
class NostrWorkspaceResolution:
    """Resolved workspace target for one inbound Nostr sender."""

    target: Literal["admin", "workspace", "denied"]
    workspace_name: str | None = None
    workspace_path: Path | None = None
    normalized_pubkey: str | None = None
    reason: str | None = None


class Config(BaseSettings):
    """Root configuration for hermitcrab."""

    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    workspaces: WorkspacesConfig = Field(default_factory=WorkspacesConfig)
    models: dict[str, NamedModelConfig] = Field(default_factory=dict)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    reflection: ReflectionConfig = Field(default_factory=ReflectionConfig)

    @model_validator(mode="after")
    def validate_multi_workspace_nostr_bindings(self) -> "Config":
        """Validate additive multi-workspace Nostr binding rules."""
        bindings = self.channels.nostr.workspace_bindings
        if not bindings:
            return self

        allowlist = {value.strip().lower() for value in self.channels.nostr.allowed_pubkeys if value.strip()}
        if "*" in allowlist or "all" in allowlist:
            raise ValueError(
                "channels.nostr.allowed_pubkeys cannot use '*' or 'all' when workspace bindings are configured"
            )

        configured_workspaces = set(self.workspaces.registry)
        normalized_allowlist: set[str] = set()
        for pubkey in self.channels.nostr.allowed_pubkeys:
            normalized_allowlist.add(normalize_nostr_pubkey(pubkey))

        seen_pubkeys: dict[str, str] = {}
        for workspace_name, pubkeys in bindings.items():
            if workspace_name not in configured_workspaces:
                raise ValueError(
                    f"channels.nostr.workspace_bindings references unknown workspace '{workspace_name}'"
                )
            for pubkey in pubkeys:
                normalized = normalize_nostr_pubkey(pubkey)
                previous = seen_pubkeys.get(normalized)
                if previous is not None:
                    raise ValueError(
                        "channels.nostr.workspace_bindings pubkeys must be unique; "
                        f"{normalized} is assigned to both '{previous}' and '{workspace_name}'"
                    )
                if normalized not in normalized_allowlist:
                    raise ValueError(
                        "channels.nostr.workspace_bindings pubkeys must also appear in "
                        "channels.nostr.allowed_pubkeys"
                    )
                seen_pubkeys[normalized] = workspace_name

        return self

    @property
    def workspace_path(self) -> Path:
        """Get expanded admin workspace path."""
        return Path(self.agents.defaults.workspace).expanduser()

    @property
    def admin_workspace_path(self) -> Path:
        """Get expanded admin workspace path."""
        return self.workspace_path

    @property
    def workspaces_root_path(self) -> Path:
        """Get expanded root path for additive personal workspaces."""
        return Path(self.workspaces.root).expanduser()

    def get_workspace_path(self, workspace_name: str | None = None) -> Path:
        """Resolve admin workspace or configured named workspace path."""
        if workspace_name is None:
            return self.workspace_path

        workspace = self.workspaces.registry.get(workspace_name)
        if workspace is None:
            raise KeyError(f"Unknown workspace: {workspace_name}")

        path = Path(workspace.path).expanduser()
        if not path.is_absolute():
            path = self.workspaces_root_path / path
        return path

    def configured_workspaces(self) -> dict[str, Path]:
        """Return configured named workspace paths."""
        return {
            name: self.get_workspace_path(name)
            for name in self.workspaces.registry
        }

    def normalized_nostr_workspace_bindings(self) -> dict[str, set[str]]:
        """Return normalized Nostr workspace bindings."""
        return {
            workspace_name: {normalize_nostr_pubkey(pubkey) for pubkey in pubkeys}
            for workspace_name, pubkeys in self.channels.nostr.workspace_bindings.items()
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

    def resolve_nostr_sender_workspace(self, sender_pubkey: str) -> NostrWorkspaceResolution:
        """Resolve inbound Nostr sender to named workspace, admin workspace, or denial."""
        try:
            normalized_pubkey = normalize_nostr_pubkey(sender_pubkey)
        except ValueError:
            return NostrWorkspaceResolution(target="denied", reason="invalid_pubkey")

        for workspace_name, pubkeys in self.normalized_nostr_workspace_bindings().items():
            if normalized_pubkey in pubkeys:
                return NostrWorkspaceResolution(
                    target="workspace",
                    workspace_name=workspace_name,
                    workspace_path=self.get_workspace_path(workspace_name),
                    normalized_pubkey=normalized_pubkey,
                    reason="workspace_binding",
                )

        allowed_pubkeys = self.normalized_nostr_allowed_pubkeys()
        if "*" in allowed_pubkeys or normalized_pubkey in allowed_pubkeys:
            return NostrWorkspaceResolution(
                target="admin",
                workspace_path=self.admin_workspace_path,
                normalized_pubkey=normalized_pubkey,
                reason="allowlist_admin_fallback",
            )

        return NostrWorkspaceResolution(
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
        model_lower = resolved_model.lower()
        model_normalized = model_lower.replace("-", "_")
        model_prefix = model_lower.split("/", 1)[0] if "/" in model_lower else ""
        normalized_prefix = model_prefix.replace("-", "_")

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
