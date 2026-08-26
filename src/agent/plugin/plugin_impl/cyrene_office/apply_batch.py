from ._shared import APPLY_BATCH_DEF, apply_batch_handler, office_tool_metadata
TOOL_DEF = APPLY_BATCH_DEF
handler = apply_batch_handler
TOOL_METADATA = office_tool_metadata(read_only=False)
