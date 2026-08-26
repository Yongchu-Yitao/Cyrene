import re
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

    assert 'href="shared/search/overlay.css?v=0.8.0-beta4"' in index
    for selector in (
        ".search-overlay {",
        ".search-overlay-panel {",
        ".search-overlay-header {",
        ".search-overlay-body {",
        ".search-result-item {",
    ):
        assert selector in css


def test_built_search_overlay_assets_match_the_frontend_sources():
    built_index = (STATIC_APP / "index.html").read_text(encoding="utf-8")
    source_index = (FRONTEND / "index.html").read_text(encoding="utf-8")
    revisions = set(re.findall(r"\?v=([A-Za-z0-9.+-]+)", built_index))
    assert len(revisions) == 1
    revision = revisions.pop()
    assert re.fullmatch(r"0\.8\.0-beta4-[0-9a-f]{10}", revision)
    normalized_built_index = re.sub(
        r'<script>window\.CyreneIconAssets=Object\.freeze\(.*?\);</script>',
        "<!-- CYRENE_ICON_ASSETS -->",
        built_index.replace(revision, "0.8.0-beta4"),
    )
    assert normalized_built_index == source_index
    assert (STATIC_APP / "shared" / "search" / "overlay.css").read_bytes() == (
        FRONTEND / "shared" / "search" / "overlay.css"
    ).read_bytes()

    build_source = (WEBUI / "build-jsx.mjs").read_text(encoding="utf-8")
    assert "frontendRevision(files, cssFiles, assetFiles, indexTemplate)" in build_source
    assert "writeFileSync(INDEX_SOURCE, indexHtml)" not in build_source
