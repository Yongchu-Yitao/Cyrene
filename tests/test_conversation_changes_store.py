from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHAT_FEATURES = (
    ROOT
    / "src"
    / "cyrene"
    / "workbench"
    / "webui"
    / "frontend"
    / "features"
    / "chat"
)


def test_changes_tab_uses_one_durable_conversation_store() -> None:
    side = (CHAT_FEATURES / "split-pane.jsx").read_text(encoding="utf-8")
    store = (CHAT_FEATURES / "conversation-changes.jsx").read_text(encoding="utf-8")

    assert "useWbcConversationChanges(activeChatId)" in side
    assert "changesState={conversationChanges}" in side
    assert "changesAvailability" not in side
    assert "WorkbenchChatModel.getChanges" not in side.split(
        "function WbcChangesTab", 1
    )[1].split("function WbcSubagentsTab", 1)[0]

    assert "var wbcConversationChangesCache = new Map()" in store
    assert 'window.addEventListener("workbench:workspace-changes"' in store
    assert store.count("WorkbenchChatModel.getChanges") == 1
    assert "wbcConversationChangesSnapshot(key)" in store


def test_changes_event_keeps_the_tab_available_across_panel_unmounts() -> None:
    store = (CHAT_FEATURES / "conversation-changes.jsx").read_text(encoding="utf-8")

    event_handler = store.split(
        "function wbcHandleConversationChangesEvent", 1
    )[1].split('window.addEventListener("workbench:workspace-changes"', 1)[0]
    assert "wbcPublishConversationChanges(chatId, { ...current, hasChanges: true })" in event_handler
    assert "wbcScheduleConversationChangesRefresh(chatId)" in event_handler
    assert "wbcConversationChangesCache.delete" not in store
