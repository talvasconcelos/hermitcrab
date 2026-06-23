"""Cron helpers shared by CLI commands."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from hermitcrab.cli.config_helpers import load_runtime_config
from hermitcrab.cron.service import CronService


def build_cron_service(
    *,
    identity_root: Path | None = None,
    identity_name: str | None = None,
    conflict_finder: Callable[[Any, int], list[Any]] | None = None,
) -> CronService:
    """Build the CronService for an identity root."""
    if identity_root is None or identity_name is None:
        config = load_runtime_config()
        identity_root = identity_root or config.owner_identity_root_path
        identity_name = identity_name or config.owner_identity_name
    return CronService(
        identity_root / "cron" / "jobs.json",
        identity_name=identity_name,
        conflict_finder=conflict_finder,
    )
