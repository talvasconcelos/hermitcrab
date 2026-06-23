"""Setup CLI commands."""

import typer
from rich.console import Console

from hermitcrab import __logo__
from hermitcrab.cli.bootstrap import (
    bootstrap_standard_layout,
    build_onboard_next_steps,
)
from hermitcrab.cli.config_helpers import (
    api_key_from_env,
    configure_provider,
    provider_options,
)
from hermitcrab.config.schema import Config, NamedModelConfig

console = Console()


def register_setup_commands(app: typer.Typer) -> None:
    """Register top-level setup commands."""
    app.command()(onboard)
    app.command()(setup)


def onboard():
    """Initialize hermitcrab configuration and workspace."""
    from hermitcrab.config.loader import get_config_path, load_config, save_config

    config_path = get_config_path()

    if config_path.exists():
        console.print(f"[yellow]Config already exists at {config_path}[/yellow]")
        console.print("  [bold]y[/bold] = overwrite with defaults (existing values will be lost)")
        console.print(
            "  [bold]N[/bold] = refresh config, keeping existing values and adding new fields"
        )
        if typer.confirm("Overwrite?"):
            config = Config()
            save_config(config)
            console.print(f"[green]✓[/green] Config reset to defaults at {config_path}")
        else:
            config = load_config()
            save_config(config)
            console.print(
                f"[green]✓[/green] Config refreshed at {config_path} (existing values preserved)"
            )
    else:
        config = Config()
        save_config(config)
        console.print(f"[green]✓[/green] Created config at {config_path}")

    bootstrap_standard_layout(config, announce=console.print)

    console.print(f"\n{__logo__} hermitcrab is ready!")
    for line in build_onboard_next_steps():
        console.print(line)


def setup(
    yes: bool = typer.Option(False, "--yes", "-y", help="Run non-interactively with safe defaults"),
    provider: str | None = typer.Option(None, "--provider", help="Provider to configure, e.g. openrouter or ollama"),
    model: str | None = typer.Option(None, "--model", help="Default model id or existing named model"),
    model_name: str = typer.Option("main", "--model-name", help="Name to save the default model under"),
    api_key_env: str | None = typer.Option(
        None, "--api-key-env", help="Read provider API key from this environment variable"
    ),
    owner_label: str | None = typer.Option(None, "--owner-label", help="Display label for the owner identity"),
):
    """Guided admin setup for config, owner identity, and default model."""
    from hermitcrab.config.loader import get_config_path, load_config, save_config

    config_path = get_config_path()
    config = load_config() if config_path.exists() else Config(root=str(config_path.parent))

    if not yes:
        console.print(f"{__logo__} hermitcrab setup\n")
        console.print("This configures the admin CLI and the owner identity.")
        console.print(
            "Other users can be added later and should normally talk through channels like Nostr DMs.\n"
        )

        if provider is None:
            provider = typer.prompt(
                "Provider (ollama/openrouter/custom, blank to keep current)", default=""
            ) or None
        if model is None:
            model = typer.prompt("Default model id or named model (blank to keep current)", default="") or None
        if owner_label is None:
            owner_label = typer.prompt("Owner display label (blank to keep current)", default="") or None

    if provider:
        configure_provider(config, provider, api_key=api_key_from_env(api_key_env))

    if model:
        if model in config.models:
            config.agents.defaults.model = model
        else:
            options = provider_options(provider)
            config.models[model_name] = NamedModelConfig(model=model, provider_options=options)
            config.agents.defaults.model = model_name

    owner = config.identities.registry[config.owner_identity_name]
    if owner_label:
        owner.label = owner_label

    validated = Config.model_validate(config.model_dump(by_alias=True))
    save_config(validated)
    bootstrap_standard_layout(validated, announce=console.print)

    console.print(f"[green]✓[/green] Setup saved at {config_path}")
    console.print(f"Owner identity: [cyan]{validated.owner_identity_name}[/cyan]")
    console.print(f"Default model: [cyan]{validated.agents.defaults.model}[/cyan]")
    console.print("\nNext steps:")
    console.print("  1. Run [cyan]hermitcrab doctor[/cyan]")
    console.print('  2. Try [cyan]hermitcrab agent -m "Hello"[/cyan]')
    console.print("  3. Add users when needed: [cyan]hermitcrab user add alice --label Alice[/cyan]")
