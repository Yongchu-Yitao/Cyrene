from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_import_panel_has_three_supported_options_and_no_other_option():
    source = (ROOT / "src" / "webui" / "frontend" / "workbench-welcome.jsx").read_text(
        encoding="utf-8"
    )

    assert source.count('{ id: "docs"') == 1
    assert source.count('{ id: "tasks"') == 1
    assert source.count('{ id: "knowledge", tone: "violet", icon: ICON.importKb') == 1
    assert 'id: "other"' not in source
    assert "ICON.importMore" not in source
    assert 'className="wb-wel-tiles wb-wel-import-tiles"' in source


def test_welcome_footer_links_share_explicit_typography():
    styles = (ROOT / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    rule = styles.split(".wb-wel-more {", 1)[1].split("}", 1)[0]
    assert "font-family: inherit;" in rule
    assert "font-size:" in rule
    assert "font-weight:" in rule
    assert "line-height:" in rule
