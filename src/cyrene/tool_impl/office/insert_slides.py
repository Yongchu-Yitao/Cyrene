from cyrene.tool_impl.office._shared import INSERT_SLIDES_DEF, insert_slides_handler, office_tool_metadata
TOOL_DEF = INSERT_SLIDES_DEF
handler = insert_slides_handler
TOOL_METADATA = office_tool_metadata(read_only=False)
