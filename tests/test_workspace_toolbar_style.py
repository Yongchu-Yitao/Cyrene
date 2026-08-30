from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_STYLES = (
    ROOT
    / "src"
    / "cyrene"
    / "workbench"
    / "webui"
    / "frontend"
    / "features"
    / "chat"
    / "workspace.css"
)


def _rule(styles: str, selector: str) -> str:
    return styles.split(selector + " {", 1)[1].split("}", 1)[0]


def test_workspace_toolbar_uses_compact_controls_without_glow() -> None:
    styles = WORKSPACE_STYLES.read_text(encoding="utf-8")

    toolbar = _rule(styles, ".wbc-workspace-toolbar")
    picker = _rule(styles, ".wbc-workspace-action-picker")
    button = _rule(styles, ".wbc-workspace-toolbar button")
    picker_focus = _rule(styles, ".wbc-workspace-action-picker:focus-within")
    primary = _rule(styles, ".wbc-workspace-toolbar button.is-primary")

    assert "min-height: 44px" in toolbar
    assert "height: 32px" in picker
    assert "height: 32px" in button
    assert "box-shadow: none" in picker_focus
    assert "box-shadow: none" in primary
