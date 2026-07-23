"""Code, Git, shell-session, and coding-agent tools."""

from cyrene.tool_impl.code.analysis import register_to as _register_analysis
from cyrene.tool_impl.code.git import register_to as _register_git
from cyrene.tool_impl.code.indexer import register_to as _register_indexer


def register_all(tool_defs: list, tool_handlers: dict) -> None:
    _register_analysis(tool_defs, tool_handlers)
    _register_git(tool_defs, tool_handlers)
    _register_indexer(tool_defs, tool_handlers)
