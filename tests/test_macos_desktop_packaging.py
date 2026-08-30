import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_extension_runtime_installers_are_built_and_packaged():
    package = json.loads((ROOT / "electron" / "package.json").read_text(encoding="utf-8"))
    before_pack = (ROOT / "electron" / "build-app-use-macos.js").read_text(encoding="utf-8")
    runtime_builder = (ROOT / "electron" / "build-runtime-tools.js").read_text(encoding="utf-8")
    main = (ROOT / "electron" / "main.js").read_text(encoding="utf-8")

    assert "build-runtime-tools" in before_pack
    assert "execFileSync('tar', ['-xf', uvArchive, '-C', temp])" in runtime_builder
    resources = {(item["from"], item["to"]) for item in package["build"]["extraResources"]}
    assert ("runtime-tools", "runtime-tools") in resources
    for target in ("darwin-arm64", "darwin-x64", "win32-arm64", "win32-x64", "linux-arm64", "linux-x64"):
        assert f"'{target}'" in runtime_builder
    assert "Checksum mismatch" in runtime_builder
    assert "attempt <= 4" in runtime_builder
    assert "attempt * 1500" in runtime_builder
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
    assert "for attempt in 1 2 3" in smoke
    assert "Unable to mount macOS DMG after 3 attempts" in smoke
    assert 'Contents/Resources/python-bundle/Cyrene' in smoke
    assert 'Contents/MacOS/Cyrene' in smoke
    assert 'run_and_require "Cyrene smoke test OK:"' in smoke
    assert 'run_and_require "DESKTOP_SMOKE_TEST=ok"' in smoke
    assert "SMOKE TEST FAILED|DESKTOP_SMOKE_TEST=failed" in smoke
    assert "MACOS_INSTALL_SMOKE_TEST=ok" in smoke


def test_macos_dmg_is_created_once_after_signing():
    build = (ROOT / "build" / "build.py").read_text(encoding="utf-8")

    assert 'cmd.extend(["--mac", "--dir"])' in build


def test_release_publishes_each_verified_platform_independently():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )

    release_job = workflow.split("\n  release:\n", 1)[1]
    assert "needs: [build-macos, build-windows, build-windows-arm, build-linux]" not in release_job
    assert "Create or update Release" in release_job
    assert "draft: false" in release_job
    assert "Verify tag, version, main ancestry, and CI" in release_job
    assert "Publish macOS DMG" in workflow
    assert "Publish Windows x64 packages" in workflow
    assert "Publish Windows ARM64 packages" in workflow
    assert workflow.count("gh release upload") == 6
    assert workflow.count("--clobber") == 6
    assert "release-windows-arm:" not in workflow
    windows_arm_job = workflow.split("\n  build-windows-arm:\n", 1)[1].split(
        "\n  build-linux:\n", 1
    )[0]
    assert "needs: [release, build-windows-arm-sidecars]" in windows_arm_job
    assert "continue-on-error: true" in windows_arm_job
    assert "Report all Windows ARM64 validation failures" in windows_arm_job
    assert "pre-release-summary:" in workflow
    assert "Report every platform result" in workflow
