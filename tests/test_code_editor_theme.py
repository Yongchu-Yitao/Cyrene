import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEBUI = ROOT / "src" / "cyrene" / "workbench" / "webui"
EDITOR = WEBUI / "frontend" / "code" / "editor.jsx"


def test_code_editor_uses_a_dark_specific_high_contrast_syntax_theme() -> None:
    source = EDITOR.read_text(encoding="utf-8")
    package = json.loads((WEBUI / "package.json").read_text(encoding="utf-8"))

    assert package["dependencies"]["@lezer/highlight"] == "^1.2.3"
    assert 'import { tags } from "@lezer/highlight";' in source
    assert "var darkHighlightStyle = HighlightStyle.define([" in source
    assert 'color: "#ff938a"' in source
    assert 'color: "#dcb8ff"' in source
    assert 'color: "#a8d7ff"' in source
    assert "syntaxHighlighting(dark ? darkHighlightStyle : defaultHighlightStyle" in source
    assert "themeCompartment.of(editorThemeExtensions(isDark()))" in source
    assert "reconfigure(editorThemeExtensions(isDark()))" in source
