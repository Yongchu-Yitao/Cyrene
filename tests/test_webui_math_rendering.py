from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
APP_DIR = ROOT / "src" / "webui" / "static" / "app"


def test_math_assets_are_loaded_before_chat_renderers():
    html = (APP_DIR / "index.html").read_text(encoding="utf-8")

    assert 'href="katex/katex.min.css"' in html
    assert html.index('src="marked.min.js"') < html.index('src="katex/katex.min.js"')
    assert html.index('src="katex/katex.min.js"') < html.index('src="math.js"')
    assert html.index('src="math.js"') < html.index('src="compiled/chat.js')


def test_shared_marked_extension_supports_inline_and_display_delimiters():
    source = (APP_DIR / "math.js").read_text(encoding="utf-8")

    assert 'name: "mathInline"' in source
    assert 'name: "mathBlock"' in source
    assert "renderToString" in source
    assert "displayMode" in source
    assert "\\\\(" in source
    assert "\\\\[" in source
    assert "\\$\\$" in source


def test_katex_assets_are_packaged_locally():
    assert (APP_DIR / "katex" / "katex.min.js").is_file()
    assert (APP_DIR / "katex" / "katex.min.css").is_file()
    assert (APP_DIR / "katex" / "LICENSE").is_file()
    assert any((APP_DIR / "katex" / "fonts").glob("*.woff2"))
