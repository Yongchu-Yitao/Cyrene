from ._shared import UNDO_BATCH_DEF, office_tool_metadata, undo_batch_handler
TOOL_DEF = UNDO_BATCH_DEF
handler = undo_batch_handler
TOOL_METADATA = office_tool_metadata(read_only=False)
