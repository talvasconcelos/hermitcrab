"""Chat channels module with plugin architecture."""

from hermitcrab.channels.base import BaseChannel
from hermitcrab.channels.manager import ChannelManager

__all__ = ["BaseChannel", "ChannelManager"]
