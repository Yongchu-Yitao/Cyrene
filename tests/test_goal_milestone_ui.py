from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_STYLES = (
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
RUNTIME_STYLES = (
    ROOT
    / "src"
    / "cyrene"
    / "workbench"
    / "webui"
    / "static"
    / "app"
    / "features"
    / "chat"
    / "workspace.css"
)


def _rule(styles: str, selector: str) -> str:
    return styles.split(selector + " {", 1)[1].split("}", 1)[0]


def test_goal_milestone_cards_are_centered_in_the_conversation_lane() -> None:
    frontend = FRONTEND_STYLES.read_text(encoding="utf-8")
    runtime = RUNTIME_STYLES.read_text(encoding="utf-8")

    milestone = _rule(frontend, ".wbc-goal-milestone")
    assert "width: min(520px, 100%)" in milestone
    assert "margin: 6px auto 10px" in milestone
    assert runtime == frontend
