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
