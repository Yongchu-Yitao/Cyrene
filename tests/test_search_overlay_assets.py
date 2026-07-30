from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEBUI = ROOT / "src" / "webui"
FRONTEND = WEBUI / "frontend"
STATIC_APP = WEBUI / "static" / "app"


def test_search_overlay_styles_are_owned_and_loaded_by_shared_search():
    index = (FRONTEND / "index.html").read_text(encoding="utf-8")
    css = (FRONTEND / "shared" / "search" / "overlay.css").read_text(
        encoding="utf-8"
    )

    assert 'href="shared/search/overlay.css?v=0.7.0b9"' in index
    for selector in (
        ".search-overlay {",
        ".search-overlay-panel {",
        ".search-overlay-header {",
        ".search-overlay-body {",
        ".search-result-item {",
    ):
        assert selector in css


def test_built_search_overlay_assets_match_the_frontend_sources():
    assert (STATIC_APP / "index.html").read_bytes() == (
        FRONTEND / "index.html"
    ).read_bytes()
    assert (STATIC_APP / "shared" / "search" / "overlay.css").read_bytes() == (
        FRONTEND / "shared" / "search" / "overlay.css"
    ).read_bytes()
