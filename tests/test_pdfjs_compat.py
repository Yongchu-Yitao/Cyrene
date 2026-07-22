from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = ROOT / "src" / "webui" / "build-jsx.mjs"
PDFJS_ASSETS = ROOT / "src" / "webui" / "static" / "app" / "pdfjs"
PDFJS_SETUP = PDFJS_ASSETS / "pdf-setup.js"


def test_pdfjs_build_uses_legacy_assets_for_every_javascript_context():
    source = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert "['legacy/build/pdf.min.mjs', 'pdf.min.js']" in source
    assert "['legacy/build/pdf.worker.min.mjs', 'pdf.worker.min.js']" in source
    assert "['legacy/web/pdf_viewer.mjs', 'pdf_viewer.js']" in source


def test_built_pdfjs_assets_include_collection_compatibility_code():
    for filename in ("pdf.min.js", "pdf.worker.min.js", "pdf_viewer.js"):
        source = (PDFJS_ASSETS / filename).read_text(encoding="utf-8")
        for collection in ("Map", "WeakMap"):
            assert re.search(
                rf"target:\s*[\"']{collection}[\"'].*?"
                r"getOrInsertComputed:\s*function getOrInsertComputed",
                source,
                re.DOTALL,
            )


def test_pdf_loader_uses_pdfjs_streaming_and_abortable_loading_task():
    source = PDFJS_SETUP.read_text(encoding="utf-8")

    assert "pdfjsLib.getDocument({" in source
    assert "url: url" in source
    assert "withCredentials: true" in source
    assert "loadingTask.destroy()" in source
    assert "signal.addEventListener('abort', abortLoading" in source
    assert "fetch(url" not in source
