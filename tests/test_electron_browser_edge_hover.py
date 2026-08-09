from pathlib import Path


def test_sidebar_browser_never_moves_as_a_resize_handle_hover_effect():
    root = Path(__file__).resolve().parent.parent
    main = (root / "electron" / "main.js").read_text(encoding="utf-8")
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    assert "BROWSER_LEFT_EDGE_PREFIX" not in main
    assert "_leftEdgeHovered" not in main
    assert "targetBounds.x += 12" not in main
    sidebar_browser_rule = styles.split(
        ".wbc-side-body .browser-view.native {", 1
    )[1].split("}", 1)[0]
    assert "--browser-resize-gutter: 0px;" in sidebar_browser_rule
    side_rule = styles.split("/* ---- right panel ---- */", 1)[1].split(
        ".wbc-side {", 1
    )[1].split("}", 1)[0]
    assert "border-left: 0;" in side_rule


def test_sidebar_browser_keeps_resize_cursor_without_native_page_handle():
    root = Path(__file__).resolve().parent.parent
    main = (root / "electron" / "main.js").read_text(encoding="utf-8")
    viewport = (
        root / "src" / "webui" / "frontend" / "shared" / "browser" / "viewport.jsx"
    ).read_text(encoding="utf-8")
    workbench = (root / "src" / "webui" / "frontend" / "workbench.jsx").read_text(
        encoding="utf-8"
    )
    styles = (root / "src" / "webui" / "frontend" / "workbench.css").read_text(
        encoding="utf-8"
    )

    assert 'data-cyrene-resize-edge-hint' not in main
    assert "resizeEdgeHint.style" not in main
    assert 'showResizeEdgeHint(event.clientX < 14);' in main
    assert "cursor: col-resize !important" in main
    assert 'toggleAttribute("data-cyrene-resize-edge-active", active)' in main
    assert "window.__cyreneSetResizeEdgeHint" in main
    assert "resizeEdgeHintEnabled = ${JSON.stringify(this.zoomEnabled === false)}" in main
    assert "resizeEdgeHintColor" not in main
    assert "resizeEdgeHintColor" not in viewport
    assert 'window.dispatchEvent(new CustomEvent("workbench:right-resize-hint"' in workbench
    assert 'window.addEventListener("workbench:right-resize-hint"' in viewport
    assert "resizeEdgeHintActive: resizeEdgeHintActiveRef.current" in viewport
    assert "BROWSER_RESIZE_EDGE_PREFIX" in main
    assert 'classList.toggle("wb-col-resize-hover"' in main
    assert "body.wb-col-resize-hover .wb-col-resizer::after" in styles
