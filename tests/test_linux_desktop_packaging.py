import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_linux_packages_include_appimage_deb_and_rpm():
    package = json.loads((ROOT / "electron" / "package.json").read_text(encoding="utf-8"))
    targets = {
        (entry["target"], tuple(entry["arch"]))
        for entry in package["build"]["linux"]["target"]
    }

    assert ("AppImage", ("x64",)) in targets
    assert ("deb", ("x64",)) in targets
    assert ("rpm", ("x64",)) in targets
    assert package["build"]["linux"]["artifactName"] == "Cyrene-${version}-x64.${ext}"


def test_release_pipeline_smoke_tests_and_publishes_both_linux_packages():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "Smoke test packaged AppImage UI" in workflow
    assert "--appimage-extract-and-run" in workflow
    assert "--desktop-smoke-test" in workflow
    assert "name: linux-packages" in workflow
    assert "dist-electron/Cyrene-*-x64.AppImage" in workflow
    assert "dist-electron/Cyrene-*-x64.deb" in workflow
    assert "dist-electron/Cyrene-*-x64.rpm" in workflow


def test_linux_desktop_uses_software_rendering_and_reports_renderer_failures():
    main = (ROOT / "electron" / "main.js").read_text(encoding="utf-8")

    assert "CYRENE_ENABLE_HARDWARE_ACCELERATION" in main
    assert "app.disableHardwareAcceleration()" in main
    assert "function installWindowDiagnostics(window, label)" in main
    assert "'did-fail-load'" in main
    assert "'render-process-gone'" in main
    assert "app.setPath('userData', path.join(getCyreneTempDir(), 'electron-smoke-profile'))" in main
    assert "state.rootChildren < 1" in main
    assert "state.launchScreenPresent" in main
    assert "nonWhitePixels < 100" in main
