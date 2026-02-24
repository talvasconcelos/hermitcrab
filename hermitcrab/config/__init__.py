"""Configuration module for hermitcrab."""

from hermitcrab.config.loader import load_config, get_config_path
from hermitcrab.config.schema import Config

__all__ = ["Config", "load_config", "get_config_path"]
