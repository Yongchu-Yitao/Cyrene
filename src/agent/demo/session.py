"""Compatibility import for the original Context Tree demo."""

from ..prompt import DEFAULT_SYSTEM_PROMPT
from ..session import AgentSession, AgentTreeSession

__all__ = ["AgentSession", "AgentTreeSession", "DEFAULT_SYSTEM_PROMPT"]
