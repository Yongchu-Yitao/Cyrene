import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_selected_text_opens_independent_persistent_side_agent_tabs():
    source = (
        ROOT / "src/webui/frontend/workbench-chat.jsx"
    ).read_text(encoding="utf-8")
    styles = (
        ROOT / "src/webui/frontend/workbench.css"
    ).read_text(encoding="utf-8")

    assert 'className={"wbc-selection-menu "' in source
    assert "onAskSelection(selectedText);" in source
    assert "model.createSideAgent(parentChatId, quote)" in source
    assert "current.concat([agent])" in source
    assert "model.listSideAgents(chatId)" in source
    assert 'setSideTab("side-agents")' in source
    assert 'id: "side-agents"' in source
    assert "function WbcSideAgentsPanel" in source
    assert "activeSideAgentByChat" in source
    assert "<WbcSideAgentTab" in source
    assert "WorkbenchChatModel.sendMessage(" in source
    assert "attachments: attachments" in source
    assert "onDelete(agent.id);" in source
    assert 'className="wbc-side-agent-head"' not in source
    assert "wbc-side-agent-index-title" not in source
    assert 'className="wbc-side-agent-split-picker"' in source
    assert "function WbcSplitPickerMenu" in source
    assert "var hasAsked = messages.some" in source
    assert "<WbcComposer" in source
    assert "compact={true}" in source
    assert "<WbcUserMessage" in source
    assert "<WbcAssistantMessage" in source
    assert "<WbcLiveMessage" in source
    assert "wbc-side-agent-message" not in source
    assert "wbcSideAgentTabId" not in source

    assert ".wbc-selection-menu" in styles
    assert ".wbc-side-agent-split-menu" in styles
    assert ".wbc-side-agent-thread" in styles
    assert ".wbc-side-agent-composer-host" in styles
    assert ".wbc-side-agent-index-close" in styles


def test_selection_menu_uses_text_fragments_and_sits_below_their_center():
    source = (
        ROOT / "src/webui/frontend/workbench-chat.jsx"
    ).read_text(encoding="utf-8")
    styles = (
        ROOT / "src/webui/frontend/workbench.css"
    ).read_text(encoding="utf-8")
    helper = "function wbcSelectionTextRect(" + source.split(
        "function wbcSelectionTextRect(", 1
    )[1].split("function WbcMain(", 1)[0]
    script = f"""
eval({json.dumps(helper)});
const range = {{
  getClientRects: () => [
    {{ left: 138, top: 65, right: 342, bottom: 109, width: 204, height: 44 }}
  ],
  getBoundingClientRect: () => (
    {{ left: 0, top: 20, right: 930, bottom: 109, width: 930, height: 89 }}
  )
}};
process.stdout.write(JSON.stringify(wbcSelectionTextRect(range)));
"""
    completed = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    rect = json.loads(completed.stdout)

    assert rect == {
        "left": 138,
        "top": 65,
        "right": 342,
        "bottom": 109,
        "width": 204,
        "height": 44,
    }
    selection_effect = source.split("function readSelection()", 1)[1].split(
        "function handlePointerUp", 1
    )[0]
    assert "rect.left + rect.width / 2" in selection_effect
    assert "top: rect.bottom + 10" in selection_effect
    assert 'placement: "below"' in selection_effect
    selection_portal = source.split("var selectionMenuPortal =", 1)[1].split(
        "if (!project)", 1
    )[0]
    assert "ReactDOM.createPortal" in selection_portal
    assert "), document.body)" in selection_portal
    menu_css = styles.split(".wbc-selection-menu {", 1)[1].split("}", 1)[0]
    assert "transform: translate(-50%, 0);" in menu_css
