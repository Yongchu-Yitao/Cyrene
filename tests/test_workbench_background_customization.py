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
    assert "--wb-surface: var(--wb-user-bg-dark, #101114);" in styles
    assert "--wb-conversation-surface: color-mix(in srgb, #1a1a1a 76%, transparent);" in styles
    assert "background: var(--wb-user-bg-light, #f5f6f8);" in index
    assert "background: var(--wb-user-bg-dark, #101114);" in index

    composer_styles = styles.split(".wbc-composer-box {", 1)[1].split("}", 1)[0]
    assert "background: color-mix(in srgb, var(--wb-card-bg) 72%, transparent)" in composer_styles
    assert "backdrop-filter: blur(18px) saturate(120%) contrast(102%)" in composer_styles
    assert "border: 1px solid color-mix(in srgb, var(--wb-line-2) 64%, transparent)" in composer_styles
    light_composer_styles = styles.split(
        'html[data-theme="light"] .wbc-composer-box {', 1
    )[1].split("}", 1)[0]
    assert "background: color-mix(in srgb, #fff 76%, transparent);" in light_composer_styles
    assert "0 10px 28px rgba(15, 23, 42, .12)" in light_composer_styles
    assert "backdrop-filter: blur(18px) saturate(120%) contrast(102%);" in light_composer_styles
    dark_conversation_styles = styles.split(
        'html[data-theme="dark"] :is(.wbc-composer-box, .wbc-side-card) {', 1
    )[1].split("}", 1)[0]
    assert "background: var(--wb-conversation-surface);" in dark_conversation_styles
    assert "0 10px 28px rgba(0, 0, 0, .24)" in dark_conversation_styles
    assert "color-mix(in srgb, #fff 10%, transparent)" in dark_conversation_styles
    assert "--wb-composer-surface-color: var(" in styles
    assert "--wb-conversation-surface," in styles
    dark_integrated_styles = styles.split(
        'html[data-theme="dark"] .workbench-grid.integrated-sidebars {', 1
    )[1].split("}", 1)[0]
    assert "0 14px 34px rgba(0, 0, 0, .28)" in dark_integrated_styles

    user_bubble_styles = styles.split("\n.wbc-bubble {", 1)[1].split("}", 1)[0]
    assert "background: var(--wb-accent);" in user_bubble_styles
    assert "color: var(--wb-accent-text);" in user_bubble_styles
    assert "border-radius: 12px;" in user_bubble_styles
    assert "border: 0;" in user_bubble_styles
    assert "padding: 7px 14px;" in user_bubble_styles
    assert "box-shadow: none;" in user_bubble_styles
    assert "font-size: calc(13.5px * var(--wb-ui-font-scale, 1));" in user_bubble_styles

    dark_scroll_styles = styles.split(
        'html[data-theme="dark"] .wbc-scroll-to-bottom {', 1
    )[1].split("}", 1)[0]
    assert "background: var(--wb-conversation-surface);" in dark_scroll_styles
    assert "0 10px 28px rgba(0, 0, 0, .24)" in dark_scroll_styles
    assert "backdrop-filter: blur(18px) saturate(120%) contrast(102%);" in dark_scroll_styles

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
