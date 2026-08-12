import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_electron_builder_produces_distinct_windows_portable_apps():
    package = json.loads((ROOT / "electron" / "package.json").read_text(encoding="utf-8"))
    build = package["build"]

    assert build["win"]["target"] == ["nsis", "portable"]
    assert build["portable"]["artifactName"] == "Cyrene-${version}-win-${arch}-portable.${ext}"
    assert build["win"]["artifactName"] == "Cyrene-${version}-win-${arch}.${ext}"


def test_release_uploads_and_smoke_tests_both_portable_architectures():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    smoke = (ROOT / "build" / "windows-release-smoke.ps1").read_text(encoding="utf-8")

    assert "dist-electron/Cyrene-*-win-x64-portable.exe" in workflow
    assert "dist-electron/Cyrene-*-win-arm64-portable.exe" in workflow
    assert '"Cyrene-*-win-$Arch-portable.exe"' in smoke
    assert '"Portable Electron desktop smoke test"' in smoke
    assert '"DESKTOP_SMOKE_TEST=ok"' in smoke
    assert "CYRENE_DESKTOP_SMOKE_RESULT" in smoke
    assert "portable-desktop-result.log" in smoke


def test_windows_portable_updates_replace_the_original_executable():
    main = (ROOT / "electron" / "main.js").read_text(encoding="utf-8")

    assert "process.env.PORTABLE_EXECUTABLE_FILE" in main
    assert "path.resolve(process.env.PORTABLE_EXECUTABLE_FILE)" in main
