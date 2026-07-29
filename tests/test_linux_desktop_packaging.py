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
    assert package["build"]["deb"]["afterInstall"] == "linux-after-install.sh"
    assert package["build"]["rpm"]["afterInstall"] == "linux-after-install.sh"


def test_release_pipeline_smoke_tests_and_publishes_all_linux_packages():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "Smoke test packaged AppImage UI" in workflow
    assert "--appimage-extract-and-run" in workflow
    assert "--no-sandbox" in workflow
    assert "--desktop-smoke-test" in workflow
    assert "Install and smoke test Debian package" in workflow
    assert "stat -c '%u:%a' /opt/Cyrene/chrome-sandbox" in workflow
    assert "/opt/Cyrene/cyrene" in workflow
    assert "name: linux-packages" in workflow
    assert "dist-electron/Cyrene-*-x64.AppImage" in workflow
    assert "dist-electron/Cyrene-*-x64.deb" in workflow
    assert "dist-electron/Cyrene-*-x64.rpm" in workflow


def test_linux_desktop_uses_software_rendering_and_reports_renderer_failures():
    main = (ROOT / "electron" / "main.js").read_text(encoding="utf-8")

    assert "CYRENE_ENABLE_HARDWARE_ACCELERATION" in main
    assert "app.disableHardwareAcceleration()" in main
    assert "sandboxStat.uid === 0" in main
    assert "(sandboxStat.mode & 0o4000) !== 0" in main
    assert "app.commandLine.appendSwitch('no-sandbox')" in main
    assert "function installWindowDiagnostics(window, label)" in main
    assert "'did-fail-load'" in main
    assert "'render-process-gone'" in main
    assert "app.setPath('userData', path.join(getCyreneTempDir(), 'electron-smoke-profile'))" in main
    assert "state.rootChildren < 1" in main
    assert "state.launchScreenPresent" in main
    assert "nonWhitePixels < 100" in main


def test_linux_install_script_repairs_chromium_sandbox_permissions():
    script = (ROOT / "electron" / "linux-after-install.sh").read_text(
        encoding="utf-8"
    )

    assert 'sandbox_path="/opt/Cyrene/chrome-sandbox"' in script
    assert 'chown root:root "$sandbox_path"' in script
    assert 'chmod 4755 "$sandbox_path"' in script
