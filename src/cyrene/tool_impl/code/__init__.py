"""Code, Git, shell-session, and coding-agent tools."""


def register_all(tool_defs: list, tool_handlers: dict) -> None:
    from cyrene.tool_impl.code.analysis import register_to as register_analysis
    from cyrene.tool_impl.code.git import register_to as register_git
    from cyrene.tool_impl.code.indexer import register_to as register_indexer

    register_analysis(tool_defs, tool_handlers)
    register_git(tool_defs, tool_handlers)
    register_indexer(tool_defs, tool_handlers)
