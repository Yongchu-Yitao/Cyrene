[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("x64", "arm64")]
    [string]$Arch
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$repoRoot = Split-Path -Parent $PSScriptRoot
$distRoot = Join-Path $repoRoot "dist"
$electronDist = Join-Path $repoRoot "dist-electron"
$frozenExe = Join-Path $distRoot "Cyrene\Cyrene.exe"
$runnerTemp = [IO.Path]::GetFullPath($env:RUNNER_TEMP)
$installDir = [IO.Path]::GetFullPath((Join-Path $runnerTemp "cyrene-installed-$Arch"))
$smokeRoot = Join-Path $runnerTemp "cyrene-windows-smoke-$Arch"

if (-not $installDir.StartsWith($runnerTemp, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to install outside RUNNER_TEMP: $installDir"
}

function Invoke-CapturedProcess {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $safeLabel = $Label -replace '[^a-zA-Z0-9_-]', '_'
    $stdoutPath = Join-Path $runnerTemp "$safeLabel-stdout.log"
    $stderrPath = Join-Path $runnerTemp "$safeLabel-stderr.log"
    Remove-Item -Force -ErrorAction SilentlyContinue $stdoutPath, $stderrPath

    $process = Start-Process `
        -FilePath $Path `
        -ArgumentList $Arguments `
        -Wait `
        -PassThru `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath

    $stdout = if (Test-Path $stdoutPath) { Get-Content -Raw $stdoutPath } else { "" }
    $stderr = if (Test-Path $stderrPath) { Get-Content -Raw $stderrPath } else { "" }
    $combined = ($stdout, $stderr) -join [Environment]::NewLine
    if ($combined) { Write-Host $combined }

    return @{
        ExitCode = $process.ExitCode
        Output = $combined
    }
}

function Assert-SmokeSucceeded {
    param(
        [Parameter(Mandatory = $true)]$Result,
        [Parameter(Mandatory = $true)][string]$SuccessMarker,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if ($Result.ExitCode -ne 0) {
        throw "$Label exited with code $($Result.ExitCode)"
    }
    if ($Result.Output -match "SMOKE TEST FAILED|DESKTOP_SMOKE_TEST=failed") {
        throw "$Label reported a failure despite exit code 0"
    }
    if ($Result.Output -notmatch [regex]::Escape($SuccessMarker)) {
        throw "$Label did not emit required success marker: $SuccessMarker"
    }
}

if (-not (Test-Path $frozenExe)) { throw "Frozen backend missing: $frozenExe" }
$numpyDir = Join-Path $distRoot "Cyrene\_internal\numpy"
if (-not (Test-Path $numpyDir)) { throw "NumPy package missing from frozen backend" }
$numpyCore = @(Get-ChildItem -Path $numpyDir -Recurse -Filter "_multiarray_umath*.pyd")
if ($numpyCore.Count -lt 1) { throw "NumPy _multiarray_umath extension missing from frozen backend" }

$portableSmoke = Invoke-CapturedProcess `
    -Path $frozenExe `
    -Arguments @("--smoke-test") `
    -Label "windows-$Arch-portable-python"
Assert-SmokeSucceeded `
    -Result $portableSmoke `
    -SuccessMarker "Cyrene smoke test OK:" `
    -Label "Portable frozen backend smoke test"
if ($portableSmoke.Output -notmatch '(?m)^numpy=') {
    throw "Portable frozen backend did not confirm NumPy import"
}

$installers = @(Get-ChildItem -Path $electronDist -Filter "Cyrene-*-win-$Arch.exe")
if ($installers.Count -ne 1) {
    throw "Expected exactly one Windows $Arch installer, found $($installers.Count)"
}
if (Test-Path $installDir) { Remove-Item -Recurse -Force $installDir }

$installer = $installers[0].FullName
$installProcess = Start-Process `
    -FilePath $installer `
    -ArgumentList @("/S", "/D=$installDir") `
    -Wait `
    -PassThru
if ($installProcess.ExitCode -ne 0) {
    throw "NSIS installer exited with code $($installProcess.ExitCode)"
}

$installedApp = Join-Path $installDir "Cyrene.exe"
$installedBackend = Join-Path $installDir "resources\python-bundle\Cyrene.exe"
if (-not (Test-Path $installedApp)) { throw "Installed Electron app missing: $installedApp" }
if (-not (Test-Path $installedBackend)) { throw "Installed Python backend missing: $installedBackend" }

$installedSmoke = Invoke-CapturedProcess `
    -Path $installedBackend `
    -Arguments @("--smoke-test") `
    -Label "windows-$Arch-installed-python"
Assert-SmokeSucceeded `
    -Result $installedSmoke `
    -SuccessMarker "Cyrene smoke test OK:" `
    -Label "Installed frozen backend smoke test"
if ($installedSmoke.Output -notmatch '(?m)^numpy=') {
    throw "Installed frozen backend did not confirm NumPy import"
}

$env:CYRENE_USER_DATA_DIR = Join-Path $smokeRoot "data"
$env:CYRENE_CACHE_DIR = Join-Path $smokeRoot "cache"
$env:CYRENE_TEMP_DIR = Join-Path $smokeRoot "tmp"
$desktopSmoke = Invoke-CapturedProcess `
    -Path $installedApp `
    -Arguments @("--desktop-smoke-test") `
    -Label "windows-$Arch-installed-desktop"
Assert-SmokeSucceeded `
    -Result $desktopSmoke `
    -SuccessMarker "DESKTOP_SMOKE_TEST=ok" `
    -Label "Installed Electron desktop smoke test"

Write-Host "WINDOWS_INSTALL_SMOKE_TEST=ok arch=$Arch installDir=$installDir"
