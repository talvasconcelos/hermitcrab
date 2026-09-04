"""
hermitcrab - A lightweight AI agent framework
"""

from importlib.metadata import PackageNotFoundError, version

__logo__ = "🦀"

# Single source of truth for the version is pyproject.toml (`project.version`).
# Read it from installed package metadata so the two can never drift apart.
try:
    __version__ = version("hermitcrab-ai")
except PackageNotFoundError:
    __version__ = "0.0.0"
