from ._shared import LIST_SESSIONS_DEF, list_sessions_handler
TOOL_DEF = LIST_SESSIONS_DEF
handler = list_sessions_handler
TOOL_METADATA = {"read_only": True, "resource_keys": ("office:sessions",), "requires_order": False}
