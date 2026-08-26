from ._shared import RENDER_SLIDE_DEF, office_tool_metadata, render_slide_handler
TOOL_DEF = RENDER_SLIDE_DEF
handler = render_slide_handler
TOOL_METADATA = office_tool_metadata(read_only=True)
