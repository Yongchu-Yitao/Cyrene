from ._shared import READ_TEXT_DEF, office_tool_metadata, read_text_handler
TOOL_DEF = READ_TEXT_DEF
handler = read_text_handler
TOOL_METADATA = office_tool_metadata(read_only=True)
