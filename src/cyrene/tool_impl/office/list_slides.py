from cyrene.tool_impl.office._shared import LIST_SLIDES_DEF, list_slides_handler, office_tool_metadata
TOOL_DEF = LIST_SLIDES_DEF
handler = list_slides_handler
TOOL_METADATA = office_tool_metadata(read_only=True)
