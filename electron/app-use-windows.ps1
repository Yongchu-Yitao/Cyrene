param(
    [Parameter(Mandatory = $true)]
    [string]$PayloadBase64
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms

Add-Type @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public static class CyreneWindowApi {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc callback, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr hWnd);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
    public static IntPtr[] VisibleTopLevelWindows() {
        var result = new List<IntPtr>();
        EnumWindows((hWnd, lParam) => {
            if (IsWindowVisible(hWnd) && GetWindowTextLength(hWnd) > 0) result.Add(hWnd);
            return true;
        }, IntPtr.Zero);
        return result.ToArray();
    }
    public static string WindowTitle(IntPtr hWnd) {
        int length = GetWindowTextLength(hWnd);
        var builder = new StringBuilder(length + 1);
        GetWindowText(hWnd, builder, builder.Capacity);
        return builder.ToString();
    }
    public static int WindowProcessId(IntPtr hWnd) {
        uint processId;
        GetWindowThreadProcessId(hWnd, out processId);
        return (int)processId;
    }
}
"@

function Result([hashtable]$Value) {
    $Value | ConvertTo-Json -Depth 12 -Compress
    exit 0
}

function Fail([string]$Type, [string]$Message) {
    Result @{ ok = $false; errorType = $Type; error = $Message }
}

function Target-Handle($Target) {
    if ($null -eq $Target -or -not $Target.windowId) { throw 'Target window id is missing.' }
    return [IntPtr]([Int64]::Parse([string]$Target.windowId))
}

function Get-Targets($Payload) {
    $foreground = [CyreneWindowApi]::GetForegroundWindow().ToInt64()
    $excludePid = if ($null -ne $Payload.excludePid) { [int]$Payload.excludePid } else { 0 }
    $excludedApplicationIds = @($Payload.excludeApplicationIds | ForEach-Object { ([string]$_).ToLowerInvariant() })
    $excludedAppNames = @($Payload.excludeAppNames | ForEach-Object { ([string]$_).ToLowerInvariant() })
    $targets = @()
    foreach ($handleValue in [CyreneWindowApi]::VisibleTopLevelWindows()) {
        try {
            $processId = [CyreneWindowApi]::WindowProcessId($handleValue)
            if (-not $processId -or $processId -eq $excludePid) { continue }
            $process = Get-Process -Id $processId
            $processPath = [string]$process.Path
            if ($excludedAppNames -contains ([string]$process.ProcessName).ToLowerInvariant()) { continue }
            if ($excludedApplicationIds -contains $processPath.ToLowerInvariant()) { continue }
            $handle = $handleValue.ToInt64()
            $rect = $null
            try {
                $root = [System.Windows.Automation.AutomationElement]::FromHandle($handleValue)
                $bounds = $root.Current.BoundingRectangle
                $rect = @{ x = $bounds.X; y = $bounds.Y; width = $bounds.Width; height = $bounds.Height }
            } catch {}
            $targets += @{
                platform = 'win32'
                pid = $process.Id
                processStartTime = $process.StartTime.ToUniversalTime().Ticks.ToString()
                appName = $process.ProcessName
                applicationId = $processPath
                windowId = $handle.ToString()
                windowIndex = 0
                windowTitle = [CyreneWindowApi]::WindowTitle($handleValue)
                foreground = ($handle -eq $foreground)
                minimized = [CyreneWindowApi]::IsIconic($handleValue)
                bounds = $rect
            }
        } catch {}
    }
    return @{ ok = $true; targets = $targets }
}

function Get-Root($Target) {
    $handle = Target-Handle $Target
    $root = [System.Windows.Automation.AutomationElement]::FromHandle($handle)
    if ($null -eq $root) { throw 'Target application window is no longer available.' }
    return $root
}

function Get-Children([System.Windows.Automation.AutomationElement]$Element) {
    $children = @()
    $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
    $child = $walker.GetFirstChild($Element)
    while ($null -ne $child) {
        $children += $child
        $child = $walker.GetNextSibling($child)
    }
    return $children
}

function Resolve-Element([System.Windows.Automation.AutomationElement]$Root, [string]$NativeRef) {
    if ([string]::IsNullOrWhiteSpace($NativeRef) -or $NativeRef -eq 'w0') { return $Root }
    $element = $Root
    $parts = $NativeRef.Split('/')
    for ($partIndex = 1; $partIndex -lt $parts.Count; $partIndex += 1) {
        if ($parts[$partIndex] -notmatch '^e(\d+)$') { throw "Invalid native element path: $NativeRef" }
        $index = [int]$Matches[1]
        $children = @(Get-Children $element)
        if ($index -lt 0 -or $index -ge $children.Count) { throw "Element path is stale: $NativeRef" }
        $element = $children[$index]
    }
    return $element
}

function Try-Pattern($Element, $Pattern) {
    $patternObject = $null
    if ($Element.TryGetCurrentPattern($Pattern, [ref]$patternObject)) { return $patternObject }
    return $null
}

function Get-Node($Element, [string]$NativeRef) {
    $current = $Element.Current
    $bounds = $current.BoundingRectangle
    $actions = @()
    $nativeActions = @()
    if ($null -ne (Try-Pattern $Element ([System.Windows.Automation.InvokePattern]::Pattern))) { $actions += 'press'; $nativeActions += 'Invoke' }
    if ($null -ne (Try-Pattern $Element ([System.Windows.Automation.ValuePattern]::Pattern))) { $actions += 'set_value'; $nativeActions += 'Value' }
    if ($null -ne (Try-Pattern $Element ([System.Windows.Automation.TogglePattern]::Pattern))) { $actions += 'toggle'; $nativeActions += 'Toggle' }
    if ($null -ne (Try-Pattern $Element ([System.Windows.Automation.SelectionItemPattern]::Pattern))) { $actions += 'select'; $nativeActions += 'SelectionItem' }
    if ($null -ne (Try-Pattern $Element ([System.Windows.Automation.ExpandCollapsePattern]::Pattern))) { $actions += 'toggle'; $nativeActions += 'ExpandCollapse' }
    if ($null -ne (Try-Pattern $Element ([System.Windows.Automation.ScrollPattern]::Pattern))) { $actions += 'scroll'; $nativeActions += 'Scroll' }
    if ($null -ne (Try-Pattern $Element ([System.Windows.Automation.ScrollItemPattern]::Pattern))) { $actions += 'scroll'; $nativeActions += 'ScrollItem' }
    $value = ''
    $valuePattern = Try-Pattern $Element ([System.Windows.Automation.ValuePattern]::Pattern)
    if ($null -ne $valuePattern) { $value = $valuePattern.Current.Value }
    return @{
        nativeRef = $NativeRef
        role = ([string]$current.ControlType.ProgrammaticName).Replace('ControlType.', '')
        name = [string]$current.Name
        description = [string]$current.HelpText
        automationId = [string]$current.AutomationId
        className = [string]$current.ClassName
        value = [string]$value
        enabled = [bool]$current.IsEnabled
        focused = [bool]$current.HasKeyboardFocus
        offscreen = [bool]$current.IsOffscreen
        bounds = @{ x = $bounds.X; y = $bounds.Y; width = $bounds.Width; height = $bounds.Height }
        actions = @($actions | Select-Object -Unique)
        nativeActions = $nativeActions
    }
}

function Get-Snapshot($Payload, [bool]$InspectOnly) {
    $root = Get-Root $Payload.target
    $options = $Payload.options
    $nativeRef = if ($Payload.nativeRef) { [string]$Payload.nativeRef } elseif ($options.nativeRef) { [string]$options.nativeRef } else { 'w0' }
    $start = Resolve-Element $root $nativeRef
    $maxNodes = if ($options.maxNodes) { [Math]::Max(1, [Math]::Min(200, [int]$options.maxNodes)) } else { if ($InspectOnly) { 40 } else { 80 } }
    $maxDepth = if ($options.maxDepth) { [Math]::Max(1, [Math]::Min(16, [int]$options.maxDepth)) } else { if ($InspectOnly) { 3 } else { 8 } }
    $nodes = [System.Collections.Generic.List[object]]::new()
    $queue = [System.Collections.Generic.Queue[object]]::new()
    $queue.Enqueue(@($start, $nativeRef, 0))
    $truncated = $false
    while ($queue.Count -gt 0) {
        $entry = $queue.Dequeue()
        $element = $entry[0]
        $elementRef = [string]$entry[1]
        $depth = [int]$entry[2]
        try { $nodes.Add((Get-Node $element $elementRef)) } catch {}
        if ($nodes.Count -ge $maxNodes) { $truncated = $queue.Count -gt 0; break }
        if ($depth -ge $maxDepth) { continue }
        $children = @(Get-Children $element)
        for ($index = 0; $index -lt $children.Count; $index += 1) {
            $queue.Enqueue(@($children[$index], "$elementRef/e$index", $depth + 1))
        }
    }
    return @{ ok = $true; nodes = @($nodes); truncated = $truncated }
}

function Focus-Target($Target) {
    $handle = Target-Handle $Target
    [void][CyreneWindowApi]::ShowWindowAsync($handle, 9)
    [void][CyreneWindowApi]::SetForegroundWindow($handle)
    return @{ ok = $true; summary = "Focused $($Target.appName)." }
}

function Escape-SendKeys([string]$Text) {
    return [regex]::Replace($Text, '([+^%~()\[\]{}])', '{$1}')
}

function Send-KeyChord($Keys) {
    $lowered = @($Keys | ForEach-Object { ([string]$_).ToLowerInvariant() })
    $prefix = ''
    if ($lowered -contains 'control' -or $lowered -contains 'ctrl') { $prefix += '^' }
    if ($lowered -contains 'alt' -or $lowered -contains 'option') { $prefix += '%' }
    if ($lowered -contains 'shift') { $prefix += '+' }
    if ($lowered -contains 'command' -or $lowered -contains 'meta') { $prefix += '^' }
    $modifiers = @('control', 'ctrl', 'alt', 'option', 'shift', 'command', 'meta')
    $key = $lowered | Where-Object { $modifiers -notcontains $_ } | Select-Object -First 1
    if (-not $key) { throw 'No non-modifier key was provided.' }
    $special = @{
        enter = '{ENTER}'; return = '{ENTER}'; tab = '{TAB}'; escape = '{ESC}'; esc = '{ESC}'
        backspace = '{BACKSPACE}'; delete = '{DELETE}'; up = '{UP}'; down = '{DOWN}'
        left = '{LEFT}'; right = '{RIGHT}'; space = ' '
    }
    $encoded = if ($special.ContainsKey($key)) { $special[$key] } else { Escape-SendKeys $key }
    [System.Windows.Forms.SendKeys]::SendWait($prefix + $encoded)
}

function Perform-Action($Payload) {
    $root = Get-Root $Payload.target
    $element = if ($Payload.nativeRef) { Resolve-Element $root ([string]$Payload.nativeRef) } else { $root }
    $capability = [string]$Payload.capability
    $parameters = $Payload.parameters
    if ($capability -eq 'press') {
        $pattern = Try-Pattern $element ([System.Windows.Automation.InvokePattern]::Pattern)
        if ($null -eq $pattern) {
            $legacy = Try-Pattern $element ([System.Windows.Automation.LegacyIAccessiblePattern]::Pattern)
            if ($null -eq $legacy) { throw 'Element does not support a native invoke action.' }
            $legacy.DoDefaultAction()
        } else { $pattern.Invoke() }
        return @{ ok = $true; summary = 'Invoked the element.' }
    }
    if ($capability -eq 'set_value') {
        $pattern = Try-Pattern $element ([System.Windows.Automation.ValuePattern]::Pattern)
        if ($null -eq $pattern) { throw 'Element does not support ValuePattern.' }
        $pattern.SetValue([string]$parameters.value)
        $after = $pattern.Current.Value
        if ([string]$after -ne [string]$parameters.value) { throw 'The UI Automation value did not change to the requested value.' }
        return @{ ok = $true; verified = $true; summary = 'Set and verified the element value.'; diagnostics = @{ method = 'ValuePattern'; after = [string]$after } }
    }
    if ($capability -eq 'toggle') {
        $pattern = Try-Pattern $element ([System.Windows.Automation.TogglePattern]::Pattern)
        if ($null -ne $pattern) { $pattern.Toggle() }
        else {
            $expand = Try-Pattern $element ([System.Windows.Automation.ExpandCollapsePattern]::Pattern)
            if ($null -eq $expand) { throw 'Element does not support Toggle or ExpandCollapse.' }
            if ($expand.Current.ExpandCollapseState -eq [System.Windows.Automation.ExpandCollapseState]::Collapsed) { $expand.Expand() } else { $expand.Collapse() }
        }
        return @{ ok = $true; summary = 'Toggled the element.' }
    }
    if ($capability -eq 'select') {
        $pattern = Try-Pattern $element ([System.Windows.Automation.SelectionItemPattern]::Pattern)
        if ($null -ne $pattern) { $pattern.Select() }
        else {
            $invoke = Try-Pattern $element ([System.Windows.Automation.InvokePattern]::Pattern)
            if ($null -eq $invoke) { throw 'Element does not support SelectionItem or Invoke.' }
            $invoke.Invoke()
        }
        return @{ ok = $true; summary = 'Selected the element.' }
    }
    if ($capability -eq 'scroll') {
        $direction = if ($parameters.direction) { ([string]$parameters.direction).ToLowerInvariant() } else { 'down' }
        $amountValue = if ($parameters.amount) { [int]$parameters.amount } else { 3 }
        $amount = [Math]::Max(1, [Math]::Min(20, $amountValue))
        $item = Try-Pattern $element ([System.Windows.Automation.ScrollItemPattern]::Pattern)
        if ($null -ne $item) { $item.ScrollIntoView() }
        else {
            $scroll = Try-Pattern $element ([System.Windows.Automation.ScrollPattern]::Pattern)
            if ($null -eq $scroll) { throw 'Element does not support a semantic scroll action.' }
            $horizontal = [System.Windows.Automation.ScrollAmount]::NoAmount
            $vertical = [System.Windows.Automation.ScrollAmount]::NoAmount
            if ($direction -eq 'up') { $vertical = [System.Windows.Automation.ScrollAmount]::SmallDecrement }
            elseif ($direction -eq 'down') { $vertical = [System.Windows.Automation.ScrollAmount]::SmallIncrement }
            elseif ($direction -eq 'left') { $horizontal = [System.Windows.Automation.ScrollAmount]::SmallDecrement }
            else { $horizontal = [System.Windows.Automation.ScrollAmount]::SmallIncrement }
            for ($index = 0; $index -lt $amount; $index += 1) { $scroll.Scroll($horizontal, $vertical) }
        }
        return @{ ok = $true; summary = "Scrolled $direction." }
    }
    if ($capability -eq 'type_text') {
        $pattern = Try-Pattern $element ([System.Windows.Automation.ValuePattern]::Pattern)
        if ($null -eq $pattern -or $pattern.Current.IsReadOnly) { throw 'Element is not a writable UI Automation value control.' }
        $before = [string]$pattern.Current.Value
        $text = [string]$parameters.text
        $expected = if ($parameters.replace -eq $true) { $text } else { $before + $text }
        $pattern.SetValue($expected)
        $after = [string]$pattern.Current.Value
        if ($after -ne $expected) { throw 'Text input could not be verified through UI Automation.' }
        return @{ ok = $true; verified = $true; summary = 'Wrote and verified text through UI Automation.'; diagnostics = @{ method = 'ValuePattern'; before = $before; after = $after; backgroundSafe = $true } }
    }
    if ($capability -eq 'key_chord') {
        Send-KeyChord $parameters.keys
        return @{ ok = $true; verified = $false; uncertain = $true; summary = 'Sent the keyboard shortcut, but its application effect could not be verified.'; diagnostics = @{ method = 'SendKeys'; foregroundRequired = $true } }
    }
    throw "Unsupported Windows capability: $capability"
}

try {
    $json = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($PayloadBase64))
    $payload = $json | ConvertFrom-Json
    switch ([string]$payload.operation) {
        'list_targets' { Result (Get-Targets $payload) }
        'snapshot' { Result (Get-Snapshot $payload $false) }
        'inspect' { Result (Get-Snapshot $payload $true) }
        'perform' { Result (Perform-Action $payload) }
        'focus' { Result (Focus-Target $payload.target) }
        default { Fail 'invalid_arguments' "Unknown operation: $($payload.operation)" }
    }
} catch {
    $message = [string]$_.Exception.Message
    $type = if ($message -match 'access|denied|privilege|integrity') { 'permission_required' } else { 'provider_error' }
    Fail $type $message
}
