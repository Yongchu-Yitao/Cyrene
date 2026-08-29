"""Persistent interactive terminals shared by Workbench surfaces."""

from .client import get_terminal_daemon_client
from .manager import TerminalManager, get_terminal_manager

__all__ = ["TerminalManager", "get_terminal_daemon_client", "get_terminal_manager"]
