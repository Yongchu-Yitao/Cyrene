import ast
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
    assert package["desktopName"] == "cyrene.desktop"
    assert package["build"]["linux"]["syncDesktopName"] is True
    assert package["build"]["deb"]["afterInstall"] == "linux-after-install.sh"
    assert package["build"]["rpm"]["afterInstall"] == "linux-after-install.sh"


def test_electron_package_includes_main_process_modules():
    package = json.loads((ROOT / "electron" / "package.json").read_text(encoding="utf-8"))

    packaged_files = set(package["build"]["files"])
    assert {
        "agent-cursor.js",
        "app-use.js",
        "browser-input.js",
        "browser-target.js",
        "host-control.js",
        "main.js",
    } <= packaged_files


def test_frozen_build_keeps_numpy_native_extensions():
    source = (ROOT / "build" / "cyrene.spec").read_text(encoding="utf-8")
    entrypoint = (ROOT / "build" / "run_cyrene.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    excludes = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "_excludes"
            for target in node.targets
        )
    )

    assert "numpy" not in excludes
    assert '    "numpy",\n' in source
    assert '        "numpy": None,\n' in entrypoint
    assert "critical frozen import" in entrypoint


def test_release_pipeline_smoke_tests_and_publishes_all_linux_packages():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "Smoke test packaged AppImage UI" in workflow
    assert "--appimage-extract-and-run" in workflow
    assert "--no-sandbox" in workflow
    assert "--desktop-smoke-test" in workflow
    assert "Install and smoke test Debian package" in workflow
    assert "stat -c '%u:%a' /opt/Cyrene/chrome-sandbox" in workflow
    assert "/opt/Cyrene/cyrene" in workflow
    assert "config.enc.missing-key.bak" in workflow
    assert "data/.config_key" in workflow
    assert "Install and smoke test RPM package" in workflow
    assert "fedora:latest" in workflow
    assert "build/linux-rpm-release-smoke.sh" in workflow
    assert "name: linux-packages" in workflow
    assert "dist-electron/Cyrene-*-x64.AppImage" in workflow
    assert "dist-electron/Cyrene-*-x64.deb" in workflow
    assert "dist-electron/Cyrene-*-x64.rpm" in workflow

    rpm_smoke = (ROOT / "build" / "linux-rpm-release-smoke.sh").read_text(
        encoding="utf-8"
    )
    assert "dnf install -y" in rpm_smoke
    assert "dbus-daemon" in rpm_smoke
    assert "/opt/Cyrene/resources/python-bundle/Cyrene --smoke-test" in rpm_smoke
    assert "Cyrene smoke test OK: v0.7.4" in rpm_smoke
    assert "numpy=[0-9]+\\.[0-9]+" in rpm_smoke
    assert "dbus-run-session" in rpm_smoke
    assert "/opt/Cyrene/cyrene --no-sandbox --desktop-smoke-test" in rpm_smoke
    assert 'CYRENE_TEMP_DIR/cyrene_error.log' in rpm_smoke
    assert "SMOKE TEST FAILED|DESKTOP_SMOKE_TEST=failed" in rpm_smoke
    assert "DESKTOP_SMOKE_TEST=ok" in rpm_smoke
    assert "LINUX_RPM_INSTALL_SMOKE_TEST=ok" in rpm_smoke


def test_linux_desktop_uses_software_rendering_and_reports_renderer_failures():
    main = (ROOT / "electron" / "main.js").read_text(encoding="utf-8")

    assert "CYRENE_ENABLE_HARDWARE_ACCELERATION" in main
    assert "app.disableHardwareAcceleration()" in main
    assert "sandboxStat.uid === 0" in main
    assert "(sandboxStat.mode & 0o4000) !== 0" in main
    assert "app.commandLine.appendSwitch('no-sandbox')" in main
    assert "function installWindowDiagnostics(window, label)" in main
    assert "'console-message'" in main
    assert "'preload-error'" in main
    assert "renderer-${level}" in main
    assert "'did-fail-load'" in main
    assert "'render-process-gone'" in main
    assert "app.setPath('userData', path.join(getCyreneTempDir(), 'electron-smoke-profile'))" in main
    assert "state.rootChildren < 1" in main
    assert "state.launchScreenPresent" in main
    assert "nonWhitePixels < 100" in main
    assert "Post-load smoke validation failed" in main
    assert main.count("await runDesktopSmokeTest(mainWindow);") == 2
    window_options = main.split("const windowOptions = {", 1)[1].split(
        "mainWindow = new BrowserWindow(windowOptions);", 1
    )[0]
    assert "if (isLinux)" in window_options
    assert "const iconPath = getNotificationIconPath();" in window_options
    assert "if (iconPath) windowOptions.icon = iconPath;" in window_options


def test_linux_appimage_update_targets_the_image_instead_of_temporary_mount():
    main = (ROOT / "electron" / "main.js").read_text(encoding="utf-8")

    resolver = main.split("function getCurrentAppExecutablePath() {", 1)[1].split(
        "\n}", 1
    )[0]
    assert "isLinux && process.env.APPIMAGE" in resolver
    assert "path.resolve(process.env.APPIMAGE)" in resolver
    assert "return app.getPath('exe');" in resolver
    assert "CYRENE_APP_EXECUTABLE: getCurrentAppExecutablePath()," in main


def test_linux_install_script_repairs_chromium_sandbox_permissions():
    script = (ROOT / "electron" / "linux-after-install.sh").read_text(
        encoding="utf-8"
    )

    assert 'sandbox_path="/opt/Cyrene/chrome-sandbox"' in script
    assert 'chown root:root "$sandbox_path"' in script
    assert 'chmod 4755 "$sandbox_path"' in script


def test_frozen_build_bundles_and_executes_codex_runtime():
    spec = (ROOT / "build" / "cyrene.spec").read_text(encoding="utf-8")
    entrypoint = (ROOT / "build" / "run_cyrene.py").read_text(encoding="utf-8")

    assert '"openai_codex"' in spec
    assert '"codex_cli_bin"' in spec
    assert "from codex_cli_bin import bundled_codex_path" in entrypoint
    assert '[str(codex_path), "--version"]' in entrypoint
