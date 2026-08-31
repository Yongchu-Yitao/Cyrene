import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_electron_dev_launcher_is_cross_platform_and_uses_uv_backend():
    package = json.loads(
        (ROOT / "electron" / "package.json").read_text(encoding="utf-8")
    )
    launcher = (ROOT / "electron" / "dev-launcher.js").read_text(
        encoding="utf-8"
    )
    main = (ROOT / "electron" / "main.js").read_text(encoding="utf-8")

    assert package["scripts"]["dev"] == "node dev-launcher.js"
    assert "ELECTRON_DEV: '1'" in launcher
    assert "spawn(electronPath, ['.']" in launcher
    assert "return 'uv';" in main
    assert "'run'," in main
    assert "'cyrene'," in main
    assert "cwd: cwd" in main


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
    assert "--force-reinstall --no-deps --no-binary cffi" in arm_job
    assert "import _cffi_backend" in arm_job
    assert arm_job.index("arm64-cryptography-ok") < arm_job.index(
        "requirements-windows-release.txt"
    )
    assert arm_job.index("Build native ARM64 cryptography runtime") < arm_job.index(
        "Install MCP runtime"
    )
    assert "Install native ARM64 VC runtime" in arm_job
    assert "vc_redist.arm64.exe" in arm_job
    assert "$PSNativeCommandUseErrorActionPreference = $true" in arm_job
    assert "platforms:" in workflow
    assert "- windows-x64" in workflow
    assert "- windows-arm64" in workflow
    assert workflow.count("inputs.platforms == '' || inputs.platforms == 'all'") == 2
    assert "inputs.platforms != 'windows-arm64'" in workflow
    assert workflow.count("inputs.platforms != 'windows-x64'") == 2

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
    webui_build = (
        ROOT / "src" / "cyrene" / "workbench" / "webui" / "build-jsx.mjs"
    ).read_text(encoding="utf-8")
    main = (ROOT / "electron" / "main.js").read_text(encoding="utf-8")
    lifecycle_soak = (
        ROOT / "electron" / "terminal-lifecycle-soak.js"
    ).read_text(encoding="utf-8")
    electron_package = (ROOT / "electron" / "package.json").read_text(
        encoding="utf-8"
    )

    assert workflow.count("Install and smoke test Windows package") == 2
    assert 'tags: ["v*"]' in workflow
    assert "pull_request:" not in workflow
    workflow_soak_calls = sum(
        path.read_text(encoding="utf-8").count("windows-release-smoke.ps1")
        for path in (ROOT / ".github" / "workflows").glob("*.yml")
    )
    assert workflow_soak_calls == 2
    assert "build\\windows-release-smoke.ps1 -Arch x64" in workflow
    assert "build\\windows-release-smoke.ps1 -Arch arm64" in workflow
    assert '"_multiarray_umath*.pyd"' in smoke
    assert '@("/S", "/D=$installDir")' in smoke
    assert '"resources\\python-bundle\\Cyrene.exe"' in smoke
    assert 'Arguments @("--desktop-smoke-test")' in smoke
    assert smoke.count("        -TriggerSecondInstance") == 1
    assert "second instance did not hand off within 30 seconds" in smoke
    assert 'Arguments @("--terminal-smoke-test")' in smoke
    assert smoke.count('Arguments @("--terminal-lifecycle-soak-test")') == 2
    assert 'SuccessMarker "Cyrene smoke test OK:"' in smoke
    assert 'SuccessMarker "DESKTOP_SMOKE_TEST=ok"' in smoke
    assert 'SuccessMarker "CYRENE_WINDOWS_TERMINAL_SMOKE=ok"' in smoke
    assert '$installedLifecycleCycles = if ($Arch -eq "arm64") { 10 } else { 20 }' in smoke
    assert 'SuccessMarker "CYRENE_WINDOWS_TERMINAL_LIFECYCLE_SOAK=ok cycles=$installedLifecycleCycles"' in smoke
    assert 'SuccessMarker "CYRENE_WINDOWS_TERMINAL_LIFECYCLE_SOAK=ok cycles=5"' in smoke
    assert '$env:CYRENE_TERMINAL_SOAK_CYCLES = [string]$installedLifecycleCycles' in smoke
    assert '$env:CYRENE_TERMINAL_SOAK_CYCLES = "5"' in smoke
    assert "installed-terminal-lifecycle-result.log" in smoke
    assert "portable-terminal-lifecycle-result.log" in smoke
    assert "TERMINAL_LIFECYCLE_SOAK=failed" in smoke
    assert "$validationFailures = [Collections.Generic.List[string]]::new()" in smoke
    assert smoke.count("Invoke-ReleaseValidation -Label") >= 8
    assert "WINDOWS_VALIDATION_RESULT=failed" in smoke
    assert "release validation reported $($validationFailures.Count) failure(s)" in smoke
    assert "still running (${elapsed}s elapsed)" in smoke
    assert "before writing its result" in smoke
    assert "--terminal-lifecycle-soak-test" in main
    assert "runTerminalLifecycleSoak" in main
    assert "Terminal Daemon was replaced during lifecycle cycle" in lifecycle_soak
    assert "Terminal process did not survive lifecycle cycle" in lifecycle_soak
    ready_wait = lifecycle_soak.split("async function waitForBackendReady", 1)[1].split(
        "async function waitForBackendRestart", 1
    )[0]
    assert "timeoutMs = 120000" in ready_wait
    restart_wait = lifecycle_soak.split("async function waitForBackendRestart", 1)[1].split(
        "async function runTerminalLifecycleSoak", 1
    )[0]
    assert "timeoutMs = 120000" in restart_wait
    assert "'/api/projects?detail=summary'" in ready_wait
    assert "requestBackendJson('GET', '/api/status')" not in lifecycle_soak
    assert "`/api/terminals?" in restart_wait
    assert "path.join(userDataDir, 'workspace', 'terminal-lifecycle-workspace')" in lifecycle_soak
    assert "path.join(tempDir, 'terminal-lifecycle-workspace')" not in lifecycle_soak
    assert "CYRENE_TERMINAL_SOAK_BURST_COMPLETE" in lifecycle_soak
    assert "120000" in lifecycle_soak
    assert "await daemonRequest(cleanupConnection, 'shutdown'" in lifecycle_soak
    assert '"terminal-lifecycle-soak.js"' in electron_package
    client = (
        ROOT
        / "src" / "cyrene" / "plugins" / "builtin"
        / "cyrene_code"
        / "terminal"
        / "client.py"
    ).read_text(encoding="utf-8")
    assert "_CREATE_BREAKAWAY_FROM_JOB" in client
    assert "QueryInformationJobObject" in client
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
    assert "function Invoke-DesktopSmokeProcess" in smoke
    assert "did not write its success result within $TimeoutSeconds seconds" in smoke
    assert "isolated smoke process tree" in smoke
    assert "-FilePath taskkill.exe" in smoke
    assert '@("/pid", [string]$process.Id, "/f", "/t")' in smoke
    assert "-ResultPath $installedResultPath" in smoke
    assert "-ResultPath $portableResultPath" in smoke
    captured_process = smoke.split("function Invoke-CapturedProcess", 1)[1].split(
        "function Assert-SmokeSucceeded", 1
    )[0]
    assert "Start-Process" in captured_process
    assert "\n        -Wait `" not in captured_process
    assert "$process.WaitForExit($TimeoutSeconds * 1000)" in captured_process
    assert "timed out after $TimeoutSeconds seconds" in captured_process
    assert "if ($null -eq $exitCode)" in captured_process
    assert "ExitCode = $exitCode" in captured_process
    assert "split(sep).join('/')" in webui_build


def test_windows_backend_termination_does_not_hold_electron_open():
    main = (ROOT / "electron" / "main.js").read_text(encoding="utf-8")
    assert "DESKTOP_SMOKE_TEST=awaiting_harness_cleanup" in main
    taskkill_calls = main.split("spawn('taskkill'")[1:]

    assert len(taskkill_calls) == 2
    assert all("stdio: 'ignore'" in call[:220] for call in taskkill_calls)
    assert all("'pipe'" not in call[:220] for call in taskkill_calls)
    assert main.count("taskkill.unref();") == 2


def test_frozen_smoke_imports_numpy_native_extension():
    entrypoint = (ROOT / "build" / "run_cyrene.py").read_text(encoding="utf-8")

    assert '"numpy._core._multiarray_umath": None' in entrypoint


def test_frozen_terminal_smoke_sends_command_through_daemon_input():
    entrypoint = (ROOT / "build" / "run_cyrene.py").read_text(encoding="utf-8")
    terminal_smoke = entrypoint.split("def _run_terminal_smoke_test()", 1)[1].split(
        "def _main()", 1
    )[0]

    assert 'argv = [command, "/d", "/q", "/k"]' in terminal_smoke
    assert 'f"echo {marker}\\rexit\\r"' in terminal_smoke
    assert 'actor="user"' in terminal_smoke
    assert "exit_deadline = time.monotonic() + 20" in terminal_smoke
    assert 'str(item.get("id") or "") == terminal_id' in terminal_smoke
    assert 'terminal_state.get("status") != "exited"' in terminal_smoke
    assert "daemonPidBefore={daemon_pid}" in terminal_smoke
    assert '"/k", f"echo {marker}"' not in terminal_smoke


def test_woa_core_excludes_x64_only_features_and_packages_sidecars():
    spec = (ROOT / "build" / "cyrene.spec").read_text(encoding="utf-8")
    package = (ROOT / "electron" / "package.json").read_text(encoding="utf-8")
    manager = (
        ROOT
        / "src" / "cyrene" / "plugins" / "builtin"
        / "cyrene_content"
        / "search_service.py"
    ).read_text(encoding="utf-8")

    assert '"simplexng", "rapidocr", "pyclipper", "cv2", "brotli", "fasttext"' in spec
    assert 'if _IS_WIN:\n    # pywinpty' in spec
    assert '_collect_package("winpty")' in spec
    assert '"openconsole.exe", "conpty.dll"' in spec
    assert "_missing_winpty_runtime" in spec
    assert '"from": "../dist/x64-sidecars"' in package
    assert '"x64-sidecars" / "simplexng" / "CyreneSimpleXNG.exe"' in manager

    ocr_spec = (ROOT / "build" / "cyrene_ocr_sidecar.spec").read_text(encoding="utf-8")
    search_spec = (ROOT / "build" / "cyrene_simplexng_sidecar.spec").read_text(encoding="utf-8")
    search_entry = (ROOT / "build" / "run_simplexng_sidecar.py").read_text(encoding="utf-8")
    assert '"shapely", "yaml", "omegaconf", "tqdm", "colorlog", "requests", "six"' in ocr_spec
    assert '"httpx", "httpcore", "httpx_socks", "anyio", "sniffio", "certifi"' in search_spec
    assert "from cyrene.simplexng_child import main" in search_entry
    assert "cyrene.plugins" not in search_entry
