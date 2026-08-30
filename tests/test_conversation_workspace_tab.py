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


def test_workspace_tab_is_conditional_and_opens_the_started_surface() -> None:
    side = (CHAT_FEATURES / "split-pane.jsx").read_text(encoding="utf-8")
    page = (CHAT_FEATURES / "page.jsx").read_text(encoding="utf-8")

    assert "if (workspaceAvailable && onOpenWorkspace)" in side
    assert 'item.id === "workspace"' in side
    assert "if (item.id === \"workspace\" && onOpenWorkspace) onOpenWorkspace();" in side
    assert 'renderer.id === "workspace-composite"' in page
    assert '["opened", "replaced", "updated"].indexOf(result.outcome) >= 0' in page
    assert "persistWorkspaceSurface(ownerChatId, workspaceDescriptor)" in page
    assert "workspaceAvailable={!!activeWorkspaceDescriptor}" in page
    assert "(visibleChat || selectedChatSummary).workspaceSurface" in page


def test_manually_reopened_workspace_is_claimed_by_the_user() -> None:
    page = (CHAT_FEATURES / "page.jsx").read_text(encoding="utf-8")
    reopen = page.split("function wbcOpenStartedWorkspace(options)", 1)[1].split(
        "function WorkbenchChatPage", 1
    )[0]

    assert 'attention: "reveal"' in reopen
    assert "wbcClaimSurfaceCard(card)" in reopen
    assert "surfaceSuppressionRef" not in reopen
    assert 'result.outcome === "deferred"' in reopen


def test_workspace_surface_is_persisted_through_the_chat_api() -> None:
    page = (CHAT_FEATURES / "page.jsx").read_text(encoding="utf-8")
    model = (CHAT_FEATURES / "model-api.jsx").read_text(encoding="utf-8")

    persist = page.split("function useWbcWorkspaceSurfaceState", 1)[1].split(
        "function useWbcSurfaceIntentListener", 1
    )[0]
    assert "model.updateChatPreferences(chatId, { workspaceSurface: durable })" in persist
    assert "persistedRef.current.get(chatId) === signature" in persist
    assert "workspaceSurface: persisted" in persist
    assert "function updateChatPreferences" in model
