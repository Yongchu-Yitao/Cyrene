from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "src" / "cyrene" / "workbench" / "webui" / "frontend"
FONT_DIR = FRONTEND / "assets" / "fonts"


def test_cross_platform_fonts_are_bundled_and_declared_without_size_overrides():
    expected_fonts = {
        "manrope-variable.woff2",
        "noto-sans-sc-variable.woff2",
        "ibm-plex-mono-regular.woff2",
        "ibm-plex-mono-medium.woff2",
        "ibm-plex-mono-semibold.woff2",
    }
    for name in expected_fonts:
        payload = (FONT_DIR / name).read_bytes()
        assert payload[:4] == b"wOF2"

    for license_name in (
        "OFL-Manrope.txt",
        "OFL-NotoSansSC.txt",
        "OFL-IBMPlexMono.txt",
    ):
        assert "SIL OPEN FONT LICENSE" in (FONT_DIR / license_name).read_text(
            encoding="utf-8"
        )

    fonts_css = (FRONTEND / "fonts.css").read_text(encoding="utf-8")
    assert 'font-family: "Manrope";' in fonts_css
    assert 'font-weight: 200 800;' in fonts_css
    assert 'font-family: "Noto Sans SC";' in fonts_css
    assert 'font-weight: 100 900;' in fonts_css
    assert fonts_css.count('font-family: "IBM Plex Mono";') == 3
    assert "font-size:" not in fonts_css


def test_font_assets_are_preloaded_hashed_and_copied_to_static_output():
    index = (FRONTEND / "index.html").read_text(encoding="utf-8")
    build_script = (ROOT / "src/cyrene/workbench/webui/build-jsx.mjs").read_text(encoding="utf-8")
    base_css = (FRONTEND / "shared/theme/base.css").read_text(encoding="utf-8")

    assert 'href="assets/fonts/manrope-variable.woff2" as="font"' in index
    assert 'href="assets/fonts/noto-sans-sc-variable.woff2" as="font"' in index
    assert 'href="fonts.css?v=0.9.0-beta6"' in index
    assert "document.fonts.load('560 16px \"Manrope\"'" in index
    assert "document.fonts.load('560 16px \"Noto Sans SC\"'" in index
    assert "font-size: 34px" in index

    assert "frontendRevision(files, cssFiles, assetFiles, indexTemplate)" in build_script
    assert "const assetFiles = existsSync(ASSETS_DIR)" in build_script
    assert "for (const srcPath of assetFiles)" in build_script
    assert "electronOverlayTemplate(electronSource, constantName)" in build_script

    assert '--sans: "Manrope", "Noto Sans SC"' in base_css
    assert '--mono: "IBM Plex Mono"' in base_css
    assert "font-synthesis-weight: none;" in base_css


def test_electron_browser_overlays_load_the_same_origin_bundled_fonts():
    main = (ROOT / "electron/main.js").read_text(encoding="utf-8")

    assert main.count('font-family: Manrope, "Noto Sans SC", system-ui') == 2
    assert "static/app/electron/browser-chat-overlay.html" in main
    assert "static/app/electron/browser-tab-picker.html" in main
    assert "font-synthesis-weight: none;" in main


def test_low_dpi_windows_uses_native_hinted_fonts_and_quantized_weights():
    index = (FRONTEND / "index.html").read_text(encoding="utf-8")
    base_css = (FRONTEND / "shared/theme/base.css").read_text(encoding="utf-8")
    workbench_css = (FRONTEND / "features/chat/workspace.css").read_text(encoding="utf-8")
    main = (ROOT / "electron/main.js").read_text(encoding="utf-8")

    assert "(window.cyrene && window.cyrene.platform)" in index
    assert "@media (max-resolution: 1.5dppx)" in base_css
    assert 'html[data-platform="win32"]' in base_css
    assert '--sans: "Segoe UI Variable", "Microsoft YaHei UI", "Segoe UI"' in base_css

    low_dpi = workbench_css.split(
        "/* Windows 1080p / low-DPI typography", 1
    )[1].split(
        "/* Performance mode is deliberately renderer-wide", 1
    )[0]
    assert 'html[data-platform="win32"]' in low_dpi
    assert "font-weight: 400 !important;" in low_dpi
    assert "font-weight: 600 !important;" in low_dpi
    assert "font-weight: 700 !important;" in low_dpi
    assert "font-size:" not in low_dpi

    assert main.count("@media (max-resolution: 1.5dppx)") == 2
    assert main.count('"Segoe UI Variable", "Microsoft YaHei UI"') == 2
    assert main.count("new URLSearchParams(location.search).get('platform')") == 2
    assert main.count("?platform=${encodeURIComponent(process.platform)}") == 2
