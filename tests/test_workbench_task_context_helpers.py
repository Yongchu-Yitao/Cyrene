from conftest import frontend_module_source


def test_task_context_panel_imports_shared_presentation_helpers():
    panel = frontend_module_source("features/task/context-panel.jsx")
    presentation = frontend_module_source("features/task/presentation.jsx")
    task_page = frontend_module_source("features/task/index.jsx")

    assert (
        'import { ICONS, hasAcceptanceFailure, priorityText, wbRealGoal, wbRenderMarkdown, wbT } '
        'from "./presentation.jsx"'
    ) in panel
    assert "function priorityText(priority)" in presentation
    assert "function hasAcceptanceFailure(session)" in presentation
    assert "function wbRenderMarkdown(text)" in presentation
    assert "hasAcceptanceFailure, priorityText" in presentation.split("export {", 1)[1]
    assert "function priorityText(" not in task_page
    assert "function hasAcceptanceFailure(" not in task_page
    assert "function wbRenderMarkdown(" not in task_page
