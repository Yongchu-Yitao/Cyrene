"""PowerShell update script rendering; inputs are already safely quoted."""
import os

def windows_wait_for_exit_script() -> str:
    process_ids = {os.getpid()}
    electron_pid = os.environ.get("CYRENE_ELECTRON_PID", "")
    if electron_pid.isdecimal() and int(electron_pid) > 0:
        process_ids.add(int(electron_pid))
    ids = ", ".join(str(pid) for pid in sorted(process_ids))
    return f"""
function Wait-CyreneExit {{
    $deadline = (Get-Date).AddSeconds(120)
    foreach ($processId in @({ids})) {{
        $running = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($null -eq $running) {{ continue }}
        $remaining = [int][Math]::Max(0, ($deadline - (Get-Date)).TotalMilliseconds)
        if (-not $running.WaitForExit($remaining)) {{
            throw "Cyrene did not exit within 120 seconds (PID $processId); update aborted."
        }}
    }}
}}
"""


def portable_restart_script(update_literal: str, app_expression: str, wait_for_exit: str,
                           stop_terminal: str, report_script: str) -> str:
    return f"""$ErrorActionPreference = 'Stop'
$logPath = Join-Path $env:TEMP 'cyrene_update.log'
$updatePath = {update_literal}
$appPath = {app_expression}
$newPath = $appPath + '.new'
{wait_for_exit}
{stop_terminal}
{report_script}

function Write-UpdateLog {{
    param([string]$Message)
    $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'
    Add-Content -LiteralPath $logPath -Value "$timestamp $Message" -Encoding UTF8
}}

try {{
    Write-UpdateLog "Starting portable update. Update=$updatePath Target=$appPath"
    Write-UpdateResult 'running' ''
    Wait-CyreneExit
    $stage = 'closing_terminal'
    Stop-CyreneTerminal
    $stage = 'installing'
    Copy-Item -LiteralPath $updatePath -Destination $newPath -Force
    # The portable wrapper can outlive Electron while cleaning its extraction.
    $replaceDeadline = (Get-Date).AddSeconds(60)
    while ($true) {{
        try {{
            Move-Item -LiteralPath $newPath -Destination $appPath -Force
            break
        }} catch {{
            if ((Get-Date) -ge $replaceDeadline) {{ throw }}
            Start-Sleep -Milliseconds 500
        }}
    }}
    $stage = 'restarting'
    Start-Process -FilePath $appPath
    Write-UpdateResult 'completed' ''
    Remove-Item -LiteralPath $updatePath -Force -ErrorAction SilentlyContinue
    Write-UpdateLog 'Portable update complete.'
    exit 0
}} catch {{
    Write-UpdateResult 'failed' $_.Exception.Message 1
    Write-UpdateLog ("Portable update failed: " + $_.Exception.Message)
    Remove-Item -LiteralPath $newPath -Force -ErrorAction SilentlyContinue
    exit 1
}}
"""


def installed_restart_script(update_literal: str, app_expression: str, wait_for_exit: str,
                           stop_terminal: str, report_script: str) -> str:
    return f"""$ErrorActionPreference = 'Stop'
$logPath = Join-Path $env:TEMP 'cyrene_update.log'
$updatePath = {update_literal}
$appPath = {app_expression}
{wait_for_exit}
{stop_terminal}
{report_script}

function Write-UpdateLog {{
    param([string]$Message)
    $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'
    Add-Content -LiteralPath $logPath -Value "$timestamp $Message" -Encoding UTF8
}}

try {{
    Write-UpdateLog "Starting installed update. Installer=$updatePath Target=$appPath"
    Write-UpdateResult 'running' ''
    Wait-CyreneExit
    $stage = 'closing_terminal'
    Stop-CyreneTerminal
    $stage = 'installing'
    Write-UpdateLog 'Launching elevated installer.'
    # --updated enables electron-builder's update-specific process shutdown path.
    $installer = Start-Process -FilePath $updatePath -ArgumentList @('/S', '--updated') -Verb RunAs -Wait -PassThru -WindowStyle Hidden
    $installerExitCode = $installer.ExitCode
    Write-UpdateLog "Installer exit code: $installerExitCode"
    if ($installerExitCode -ne 0) {{
        Write-UpdateResult 'failed' "Installer exited with code $installerExitCode" $installerExitCode
        exit $installerExitCode
    }}

    Start-Sleep -Seconds 1
    if (-not (Test-Path -LiteralPath $appPath -PathType Leaf)) {{
        throw "Updated application executable was not found: $appPath"
    }}
    Write-UpdateLog "Restarting application: $appPath"
    $stage = 'restarting'
    Start-Process -FilePath $appPath
    Write-UpdateResult 'completed' ''
    Remove-Item -LiteralPath $updatePath -Force -ErrorAction SilentlyContinue
    Write-UpdateLog 'Installed update complete.'
    exit 0
}} catch {{
    Write-UpdateResult 'failed' $_.Exception.Message 1
    Write-UpdateLog ("Installed update failed: " + $_.Exception.Message)
    exit 1
}}
"""

