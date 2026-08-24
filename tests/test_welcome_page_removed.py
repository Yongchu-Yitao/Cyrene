from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "src" / "webui" / "frontend"


def read(relative_path: str) -> str:
    return (FRONTEND / relative_path).read_text(encoding="utf-8")


def test_get_started_page_has_no_navigation_or_startup_entry_point():
    workbench = read("workbench.jsx")
    project_controller = read("features/task/project-controller.jsx")
    module_presentation = read("features/shell/module-presentation.jsx")
    shell_composition = read("features/shell/shell-composition.jsx")
    help_center = read("features/shell/support.jsx")
    tour_guides = read("shared/tour/guides.jsx")

    assert 'setFullPage(function (current) { return current == null ? "welcome"' not in project_controller
    assert 'var isWelcome = fullPage === "welcome"' not in module_presentation
    assert "showWelcomePage" not in module_presentation
    assert "showWelcomePage" not in shell_composition
    assert 'onOpenPage("welcome")' not in help_center
    assert 'navigate: { page: "welcome" }' not in tour_guides
    assert 'stored && stored !== "welcome"' in workbench


def test_required_model_and_personality_setup_is_still_registered():
    welcome = read("workbench-welcome.jsx")
    overlays = read("features/shell/app-overlays.jsx")

    assert "function OnboardingFlow(props)" in welcome
    assert "Page: OnboardingFlow" in welcome
    assert "workbenchServices.welcome().Page" in overlays
