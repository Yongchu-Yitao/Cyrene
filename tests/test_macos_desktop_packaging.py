import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_extension_runtime_installers_are_built_and_packaged():
    package = json.loads((ROOT / "electron" / "package.json").read_text(encoding="utf-8"))
    before_pack = (ROOT / "electron" / "build-app-use-macos.js").read_text(encoding="utf-8")
    runtime_builder = (ROOT / "electron" / "build-runtime-tools.js").read_text(encoding="utf-8")
    main = (ROOT / "electron" / "main.js").read_text(encoding="utf-8")

    assert "build-runtime-tools" in before_pack
    assert "param([string]$Archive,[string]$Destination)" in runtime_builder
    assert "'-Archive'" in runtime_builder
    assert "'-Destination'" in runtime_builder
    resources = {(item["from"], item["to"]) for item in package["build"]["extraResources"]}
    assert ("runtime-tools", "runtime-tools") in resources
    for target in ("darwin-arm64", "darwin-x64", "win32-arm64", "win32-x64", "linux-arm64", "linux-x64"):
        assert f"'{target}'" in runtime_builder
    assert "Checksum mismatch" in runtime_builder
    assert "installQuitTitle" in main
    assert "cancelExtensionTasksAndWait" in main


def test_release_mounts_and_runs_the_built_macos_dmg():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    smoke = (ROOT / "build" / "macos-release-smoke.sh").read_text(
        encoding="utf-8"
    )

    assert "Install and smoke test macOS package" in workflow
    assert "build/macos-release-smoke.sh" in workflow
    assert "hdiutil attach" in smoke
    assert 'Contents/Resources/python-bundle/Cyrene' in smoke
    assert 'Contents/MacOS/Cyrene' in smoke
    assert 'run_and_require "Cyrene smoke test OK:"' in smoke
    assert 'run_and_require "DESKTOP_SMOKE_TEST=ok"' in smoke
    assert "SMOKE TEST FAILED|DESKTOP_SMOKE_TEST=failed" in smoke
    assert "MACOS_INSTALL_SMOKE_TEST=ok" in smoke


def test_release_waits_for_every_published_platform():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )

    release_job = workflow.split("\n  release:\n", 1)[1]
    assert "needs: [build-macos, build-windows, build-windows-arm, build-linux]" in release_job
    assert "name: windows-installer-arm" in release_job
    assert "release-windows-arm:" not in workflow
    windows_arm_job = workflow.split("\n  build-windows-arm:\n", 1)[1].split(
        "\n  build-linux:\n", 1
    )[0]
    assert "continue-on-error: true" not in windows_arm_job
