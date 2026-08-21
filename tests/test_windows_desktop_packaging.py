from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_windows_arm_keeps_electron_hardware_acceleration_enabled():
    main = (ROOT / "electron" / "main.js").read_text(encoding="utf-8")
    fallback_guard = (
        "if (isLinux && "
        "process.env.CYRENE_DISABLE_HARDWARE_ACCELERATION === '1') {"
    )

    acceleration_prefix = main.split("app.disableHardwareAcceleration()", 1)[0]
    assert acceleration_prefix.rstrip().endswith(fallback_guard)


def test_windows_release_installs_required_native_runtime_packages():
    requirements = (
        ROOT / "build" / "requirements-windows-release.txt"
    ).read_text(encoding="utf-8")
    workflow = (
        ROOT / ".github" / "workflows" / "release.yml"
    ).read_text(encoding="utf-8")

    assert "numpy>=2.1.0" in requirements
    assert "onnxruntime>=1.27,<1.28" in requirements
    assert "sherpa-onnx==1.13.5" in requirements
    assert workflow.count(
        "pip install -r build/requirements-windows-release.txt"
    ) == 2
    assert workflow.count("python build/check_windows_dependencies.py") == 2
    assert workflow.count("numpy._core._multiarray_umath") >= 2
    arm_job = workflow.split("  build-windows-arm:", 1)[1].split(
        "  build-linux:", 1
    )[0]
    assert "runs-on: windows-11-arm" in arm_job
    assert "architecture: arm64" in arm_job
    assert "CYRENE_PYTHON_BUNDLE_ARCH: x64" not in arm_job
    assert "windows-arm-x64-sidecars" in arm_job
    assert "onnxruntime_qnn" in arm_job
    assert "vcpkg install openssl:arm64-windows-static-md" in arm_job
    assert "--force-reinstall --no-deps --no-binary cryptography" in arm_job
    assert arm_job.index("Build native ARM64 cryptography runtime") < arm_job.index(
        "Install MCP runtime"
    )
    assert "Install native ARM64 VC runtime" in arm_job
    assert "vc_redist.arm64.exe" in arm_job
    assert "$PSNativeCommandUseErrorActionPreference = $true" in arm_job

    build = (ROOT / "build" / "build.py").read_text(encoding="utf-8")
    assert 'os.environ["CYRENE_WOA_NATIVE_CORE"] = "1"' in build
    assert "_ensure_windows_arm_runtime_dlls" in build
    assert '"vcruntime140_1.dll"' in build
    assert "stage_woa_x64_sidecars" in build
    assert "build-windows-arm-sidecars:" in workflow
    assert "CYRENE_OCR_SIDECAR_SMOKE=ok" in workflow
    assert "CYRENE_SIMPLEXNG_SIDECAR_SMOKE=ok" in workflow

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
    assert "function Get-PeArchitecture" in smoke
    assert "function Assert-NativeArm64Tree" in smoke
    assert 'contains non-ARM native binaries' in smoke
    assert '$file.Name -ieq "vcruntime140_1.dll" -and $actual -eq "x64"' in smoke
    assert 'Assert-PeArchitecture -Path $installedApp -Expected $Arch' in smoke
    assert 'Assert-PeArchitecture -Path $installedBackend -Expected $backendArch' in smoke
    assert 'Assert-PeArchitecture -Path $ocrSidecar -Expected "x64"' in smoke
    assert '$portableLauncherArch -notin @("x86", $Arch)' in smoke
    assert '0x014c { return "x86" }' in smoke
    assert "SMOKE TEST FAILED|DESKTOP_SMOKE_TEST=failed" in smoke
    assert "WINDOWS_INSTALL_SMOKE_TEST=ok" in smoke
    assert "CYRENE_DESKTOP_SMOKE_RESULT" in smoke


def test_windows_backend_termination_does_not_hold_electron_open():
    main = (ROOT / "electron" / "main.js").read_text(encoding="utf-8")
    taskkill_calls = main.split("spawn('taskkill'")[1:]

    assert len(taskkill_calls) == 2
    assert all("stdio: 'ignore'" in call[:220] for call in taskkill_calls)
    assert all("'pipe'" not in call[:220] for call in taskkill_calls)
    assert main.count("taskkill.unref();") == 2


def test_frozen_smoke_imports_numpy_native_extension():
    entrypoint = (ROOT / "build" / "run_cyrene.py").read_text(encoding="utf-8")

    assert '"numpy._core._multiarray_umath": None' in entrypoint


def test_woa_core_excludes_x64_only_features_and_packages_sidecars():
    spec = (ROOT / "build" / "cyrene.spec").read_text(encoding="utf-8")
    package = (ROOT / "electron" / "package.json").read_text(encoding="utf-8")
    manager = (
        ROOT / "src" / "cyrene" / "tooling" / "backends" / "searxng_manager.py"
    ).read_text(encoding="utf-8")

    assert '"simplexng", "rapidocr", "pyclipper", "cv2", "brotli", "fasttext"' in spec
    assert '"from": "../dist/x64-sidecars"' in package
    assert '"x64-sidecars" / "simplexng" / "CyreneSimpleXNG.exe"' in manager

    ocr_spec = (ROOT / "build" / "cyrene_ocr_sidecar.spec").read_text(encoding="utf-8")
    search_spec = (ROOT / "build" / "cyrene_simplexng_sidecar.spec").read_text(encoding="utf-8")
    assert '"shapely", "yaml", "omegaconf", "tqdm", "colorlog", "requests", "six"' in ocr_spec
    assert '"httpx", "httpcore", "httpx_socks", "anyio", "sniffio", "certifi"' in search_spec
