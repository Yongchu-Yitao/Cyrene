from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_workbench_background_preferences_are_exposed_and_applied_before_paint():
    settings = (ROOT / "src/webui/frontend/settings-overlay.jsx").read_text(encoding="utf-8")
    bootstrap = (ROOT / "src/webui/frontend/entry/bootstrap.jsx").read_text(encoding="utf-8")
    index = (ROOT / "src/webui/frontend/index.html").read_text(encoding="utf-8")
    styles = (ROOT / "src/webui/frontend/workbench.css").read_text(encoding="utf-8")
    translations = (ROOT / "src/webui/frontend/workbench-i18n.jsx").read_text(encoding="utf-8")

    for key in ("backgroundLight", "backgroundDark"):
        assert f'readTweak("{key}", null)' in settings
        assert f'readWorkbenchTweak("{key}", null)' in bootstrap
        assert f'readTweak("{key}", null)' in index

    assert "function ColorPickerPopover(p)" in settings
    assert "React.createElement(ColorPickerPopover" in settings
    assert 'className: "wb-color-swatch wb-background-swatch"' in settings
    assert "wb-background-hex-input" not in settings
    assert 'className: "wb-btn muted wb-background-reset"' in settings
    assert 'disabled: !normalizeAccentHex(value)' in settings
    assert 't("settings.workbenchBackground")' in settings
    assert '"settings.workbenchBackground": "Workbench background"' in translations
    assert '"settings.workbenchBackground": "Workbench 背景"' in translations
    assert "--wb-surface: var(--wb-user-bg-light, #f5f6f8);" in styles
    assert "--wb-surface: var(--wb-user-bg-dark, #1a2230);" in styles
    assert "background: var(--wb-user-bg-light, #f5f6f8);" in index
    assert "background: var(--wb-user-bg-dark, #1a2230);" in index

    composer_styles = styles.split(".wbc-composer-box {", 1)[1].split("}", 1)[0]
    assert "background: color-mix(in srgb, var(--wb-card-bg) 72%, transparent)" in composer_styles
    assert "backdrop-filter: blur(18px) saturate(120%) contrast(102%)" in composer_styles
    assert "border: 1px solid color-mix(in srgb, var(--wb-line-2) 64%, transparent)" in composer_styles

    background_styles = styles.split(".wb-workbench-backgrounds", 1)[1].split(".wb-accent-popover-actions", 1)[0]
    assert ".wb-field:has(.wb-workbench-backgrounds)" in styles
    assert "align-items: center" in background_styles
    assert "wb-background-hex-input" not in styles
    assert ".wb-background-reset" in background_styles


def test_quick_chat_tracks_workbench_background_preferences():
    quick_chat = (ROOT / "src/webui/frontend/workbench-quick-chat.jsx").read_text(encoding="utf-8")

    assert 'quickChatReadTweak("backgroundLight", null)' in quick_chat
    assert 'quickChatReadTweak("backgroundDark", null)' in quick_chat
    assert 'e.key === "cyrene-tweak-backgroundLight"' in quick_chat
    assert 'e.key === "cyrene-tweak-backgroundDark"' in quick_chat
