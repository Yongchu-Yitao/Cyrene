from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


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
