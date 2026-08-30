from __future__ import annotations

import json
from pathlib import Path

import pytest

from cyrene.plugins.builtin.cyrene_code import workspace_execution
from cyrene.plugins.builtin.cyrene_project_javascript import detect
from cyrene.workbench.projects.project_execution import normalize_execution_actions


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_SURFACE = (
    ROOT
    / "src"
    / "cyrene"
    / "workbench"
    / "webui"
    / "frontend"
    / "features"
    / "chat"
    / "workspace-surface.jsx"
)


def test_workspace_action_protocol_preserves_bounded_authored_translations() -> None:
    action = normalize_execution_actions([{
        "id": "app.preview",
        "label": "Preview app",
        "i18n": {
            "zh_CN": {"label": "预览应用", "ignored": "not public"},
            "en": {"label": "Preview application"},
        },
        "kind": "preview",
        "program": "node",
        "args": ["server.mjs"],
    }])[0]

    assert action["label"] == "Preview app"
    assert action["i18n"] == {
        "zh": {"label": "预览应用"},
        "en": {"label": "Preview application"},
    }
    with pytest.raises(ValueError, match="i18n must map locales to objects"):
        normalize_execution_actions([{
            "id": "invalid",
            "kind": "run",
            "program": "node",
            "i18n": {"zh": "无效"},
        }])


def test_project_plugin_actions_and_terminal_titles_use_the_same_translation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "package.json").write_text(json.dumps({
        "scripts": {"dev": "vite"},
    }), encoding="utf-8")

    action = detect(tmp_path, ".")[0]
    monkeypatch.setattr(workspace_execution, "app_language", lambda _value="": "zh")

    assert action["i18n"]["zh"]["label"] == "启动开发服务器"
    assert workspace_execution._localized_action_label(action) == "启动开发服务器"


def test_workspace_action_picker_resolves_labels_from_the_current_ui_language() -> None:
    source = WORKSPACE_SURFACE.read_text(encoding="utf-8")

    assert "function workspaceActionLabel(action, lang)" in source
    assert "workspaceActionLabel(action, lang)" in source
    assert 'wbcT("workspace.actionUnavailable", "Unavailable")' in source
    assert '" — unavailable"' not in source
