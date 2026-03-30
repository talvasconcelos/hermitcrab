"""Shared provider-side helpers."""

from __future__ import annotations

from typing import Any


def function_parts(function: Any) -> tuple[str | None, Any]:
    """Extract function-call name and arguments from dict or object payloads."""
    if isinstance(function, dict):
        return function.get("name"), function.get("arguments")
    return getattr(function, "name", None), getattr(function, "arguments", None)
