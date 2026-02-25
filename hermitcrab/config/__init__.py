"""Configuration module for hermitcrab."""

from hermitcrab.config.loader import get_config_path, load_config
from hermitcrab.config.schema import Config

__all__ = ["Config", "load_config", "get_config_path"]
