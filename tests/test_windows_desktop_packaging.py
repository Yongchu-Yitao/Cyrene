from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_windows_release_installs_required_native_runtime_packages():
    requirements = (
        ROOT / "build" / "requirements-windows-release.txt"
    ).read_text(encoding="utf-8")
    workflow = (
        ROOT / ".github" / "workflows" / "release.yml"
    ).read_text(encoding="utf-8")

    assert "numpy>=2.1.0" in requirements
    assert "onnxruntime>=1.27,<1.28" in requirements
    assert "sherpa-onnx==1.13.4" in requirements
    assert workflow.count(
        "pip install -r build/requirements-windows-release.txt"
    ) == 2
    assert workflow.count("python build/check_windows_dependencies.py") == 2
    assert workflow.count("numpy._core._multiarray_umath") >= 2

    dependency_check = (
        ROOT / "build" / "check_windows_dependencies.py"
    ).read_text(encoding="utf-8")
    assert '[sys.executable, "-m", "pip", "check"]' in dependency_check
    assert "simplexng" in dependency_check
    assert "requires uvloop, which is not installed" in dependency_check
    assert "Unexpected Windows dependency conflicts" in dependency_check


def test_windows_release_installs_and_runs_the_built_nsis_package():
    workflow = (
        ROOT / ".github" / "workflows" / "release.yml"
    ).read_text(encoding="utf-8")
    smoke = (
        ROOT / "build" / "windows-release-smoke.ps1"
    ).read_text(encoding="utf-8")

    assert workflow.count("Install and smoke test Windows package") == 2
    assert "build\\windows-release-smoke.ps1 -Arch x64" in workflow
    assert "build\\windows-release-smoke.ps1 -Arch arm64" in workflow
    assert '"_multiarray_umath*.pyd"' in smoke
    assert '@("/S", "/D=$installDir")' in smoke
    assert '"resources\\python-bundle\\Cyrene.exe"' in smoke
    assert 'Arguments @("--desktop-smoke-test")' in smoke
    assert 'SuccessMarker "Cyrene smoke test OK:"' in smoke
    assert 'SuccessMarker "DESKTOP_SMOKE_TEST=ok"' in smoke
    assert "SMOKE TEST FAILED|DESKTOP_SMOKE_TEST=failed" in smoke
    assert "WINDOWS_INSTALL_SMOKE_TEST=ok" in smoke


def test_frozen_smoke_imports_numpy_native_extension():
    entrypoint = (ROOT / "build" / "run_cyrene.py").read_text(encoding="utf-8")

    assert '"numpy._core._multiarray_umath": None' in entrypoint
