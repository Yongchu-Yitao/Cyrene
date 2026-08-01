import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parent.parent
SETTINGS_SOURCE = ROOT / "src" / "webui" / "frontend" / "settings-overlay.jsx"


def _run_codex_selection_helpers(expression: str):
    source = SETTINGS_SOURCE.read_text(encoding="utf-8")
    helpers = source[
        source.index("function codexModelId") : source.index(
            "async function readSettingsResponse"
        )
    ]
    completed = subprocess.run(
        ["node", "-e", helpers + f"\nprocess.stdout.write(JSON.stringify({expression}));"],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_codex_settings_keep_the_persisted_selection_visible_during_catalog_load():
    source = SETTINGS_SOURCE.read_text(encoding="utf-8")

    assert "function codexModelSelectOptions(models, selectedModel)" in source
    assert "options.unshift({ model: selected, displayName: selected, persisted: true });" in source
    assert "codexModelSelectOptions(codexState.models, codexModel)" in source
    assert "codexModelReasoningEfforts(selectedCodexModel, codexEffort)" in source

    result = _run_codex_selection_helpers(
        "codexModelSelectOptions([{model: 'gpt-current'}], 'gpt-saved')"
    )
    assert [item["model"] for item in result] == ["gpt-saved", "gpt-current"]
    assert result[0]["persisted"] is True


def test_codex_settings_keep_saved_effort_and_accept_snake_case_catalogs():
    saved_only = _run_codex_selection_helpers(
        "codexModelReasoningEfforts(null, 'xhigh')"
    )
    assert saved_only == ["xhigh"]

    catalog = _run_codex_selection_helpers(
        "codexModelReasoningEfforts({supported_reasoning_efforts: "
        "[{reasoning_effort: 'low'}, {reasoning_effort: 'high'}]}, 'high')"
    )
    assert catalog == ["low", "high"]


def test_codex_catalog_refresh_reads_the_latest_persisted_candidate():
    source = SETTINGS_SOURCE.read_text(encoding="utf-8")

    assert "codexCandidateRef.current = codexCandidate;" in source
    assert "var saved = codexCandidateRef.current;" in source
    assert "model.supportedReasoningEfforts || model.supported_reasoning_efforts" in source
