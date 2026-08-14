param(
    [Parameter(Mandatory = $true)]
    [string]$PayloadBase64
)

$ErrorActionPreference = 'Stop'
$MaxScrollAtAmount = 50000
$MaxScrollEventAmount = 10
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms

Add-Type @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
public static class CyreneWindowApi {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern IntPtr SetFocus(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll", SetLastError=true)] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc callback, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr hWnd);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
    [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
    [DllImport("user32.dll", SetLastError=true)] public static extern bool AttachThreadInput(uint sourceThreadId, uint targetThreadId, bool attach);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern bool GetCursorPos(out POINT point);
    [DllImport("user32.dll", SetLastError=true)] public static extern uint SendInput(uint count, INPUT[] inputs, int size);
    [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X; public int Y; }
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
    [StructLayout(LayoutKind.Sequential)] public struct MOUSEINPUT {
        public int dx; public int dy; public uint mouseData; public uint dwFlags; public uint time; public IntPtr dwExtraInfo;
    }
    [StructLayout(LayoutKind.Explicit)] public struct INPUTUNION { [FieldOffset(0)] public MOUSEINPUT mi; }
    [StructLayout(LayoutKind.Sequential)] public struct INPUT { public uint type; public INPUTUNION U; }
    public static void Mouse(uint flags, uint data = 0) {
        var input = new INPUT { type = 0, U = new INPUTUNION { mi = new MOUSEINPUT { dwFlags = flags, mouseData = data } } };
        if (SendInput(1, new [] { input }, Marshal.SizeOf(typeof(INPUT))) != 1) throw new InvalidOperationException("SendInput did not inject the mouse event.");
    }
    public static POINT Cursor() { POINT point; if (!GetCursorPos(out point)) throw new InvalidOperationException("GetCursorPos failed."); return point; }
    public static RECT WindowRect(IntPtr hWnd) {
        RECT rect;
        if (!GetWindowRect(hWnd, out rect)) throw new InvalidOperationException("GetWindowRect failed.");
        return rect;
    }
    public static bool FocusWindow(IntPtr hWnd) {
        ShowWindowAsync(hWnd, 9);
        if (SetForegroundWindow(hWnd) && GetForegroundWindow() == hWnd) return true;
        IntPtr foreground = GetForegroundWindow();
        uint ignoredPid;
        uint currentThread = GetCurrentThreadId();
        uint foregroundThread = foreground == IntPtr.Zero ? 0 : GetWindowThreadProcessId(foreground, out ignoredPid);
        uint targetThread = GetWindowThreadProcessId(hWnd, out ignoredPid);
        bool attachedForeground = false;
        bool attachedTarget = false;
        try {
            if (foregroundThread != 0 && foregroundThread != currentThread) {
                attachedForeground = AttachThreadInput(currentThread, foregroundThread, true);
            }
            if (targetThread != 0 && targetThread != currentThread && targetThread != foregroundThread) {
                attachedTarget = AttachThreadInput(currentThread, targetThread, true);
            }
            BringWindowToTop(hWnd);
            SetForegroundWindow(hWnd);
            SetFocus(hWnd);
            Thread.Sleep(100);
            return GetForegroundWindow() == hWnd;
        } finally {
            if (attachedTarget) AttachThreadInput(currentThread, targetThread, false);
            if (attachedForeground) AttachThreadInput(currentThread, foregroundThread, false);
        }
    }
    public static void Move(int fromX, int fromY, int toX, int toY, int durationMs, bool drag) {
        int steps = Math.Max(1, Math.Min(120, (int)Math.Ceiling(Math.Max(0, durationMs) / 16.0)));
        for (int i = 1; i <= steps; i++) {
            double ratio = i / (double)steps;
            int x = (int)Math.Round(fromX + ((toX - fromX) * ratio));
            int y = (int)Math.Round(fromY + ((toY - fromY) * ratio));
            SetCursorPos(x, y);
            if (drag) Mouse(0x0001);
            if (durationMs > 0) Thread.Sleep(Math.Max(1, durationMs / steps));
        }
    }
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

function Finite-Number($Value, [string]$Name) {
    $number = 0.0
    if (-not [double]::TryParse([string]$Value, [ref]$number) -or [double]::IsNaN($number) -or [double]::IsInfinity($number)) { throw "$Name must be a finite number." }
    return $number
}

function Screen-Point($Target, $Parameters, [string]$XName, [string]$YName) {
    $bounds = $Target.bounds
    $left = Finite-Number $bounds.x 'target.bounds.x'
    $top = Finite-Number $bounds.y 'target.bounds.y'
    $width = Finite-Number $bounds.width 'target.bounds.width'
    $height = Finite-Number $bounds.height 'target.bounds.height'
    $x = Finite-Number $Parameters.$XName $XName
    $y = Finite-Number $Parameters.$YName $YName
    $space = if ($Parameters.coordinate_space) { ([string]$Parameters.coordinate_space).ToLowerInvariant() } else { 'window' }
    if ($space -eq 'window') { $x += $left; $y += $top }
    elseif ($space -ne 'screen') { throw 'coordinate_space must be window or screen.' }
    if ($x -lt $left -or $y -lt $top -or $x -gt ($left + $width) -or $y -gt ($top + $height)) { throw 'Point is outside the connected window bounds.' }
    return @{ x = [int][Math]::Round($x); y = [int][Math]::Round($y) }
}

function Mouse-Click($Point, [bool]$Right, [int]$Count, [int]$IntervalMs) {
    [void][CyreneWindowApi]::SetCursorPos($Point.x, $Point.y)
    $down = if ($Right) { [uint32]0x0008 } else { [uint32]0x0002 }
    $up = if ($Right) { [uint32]0x0010 } else { [uint32]0x0004 }
    for ($index = 0; $index -lt $Count; $index += 1) {
        [CyreneWindowApi]::Mouse($down, 0); Start-Sleep -Milliseconds 25; [CyreneWindowApi]::Mouse($up, 0)
        if ($index + 1 -lt $Count) { Start-Sleep -Milliseconds ([Math]::Max(40, [Math]::Min(500, $IntervalMs))) }
    }
}

function Perform-CoordinateAction($Target, [string]$Capability, $Parameters) {
    if ($Capability -in @('click_at', 'double_click', 'right_click', 'hover_at', 'scroll_at')) {
        $point = Screen-Point $Target $Parameters 'x' 'y'
        $before = [CyreneWindowApi]::Cursor()
        if ($Capability -eq 'click_at') { Mouse-Click $point $false 1 0 }
        elseif ($Capability -eq 'double_click') { Mouse-Click $point $false 2 $(if ($Parameters.interval_ms) { [int]$Parameters.interval_ms } else { 100 }) }
        elseif ($Capability -eq 'right_click') { Mouse-Click $point $true 1 0 }
        elseif ($Capability -eq 'hover_at') { [CyreneWindowApi]::Move($before.X, $before.Y, $point.x, $point.y, $(if ($Parameters.duration_ms) { [int]$Parameters.duration_ms } else { 0 }), $false) }
        else {
            [void][CyreneWindowApi]::SetCursorPos($point.x, $point.y)
            $direction = if ($Parameters.direction) { ([string]$Parameters.direction).ToLowerInvariant() } else { 'down' }
            if ($direction -notin @('up', 'down', 'left', 'right')) { throw 'direction must be up, down, left, or right.' }
            $amountNumber = Finite-Number $(if ($null -ne $Parameters.amount) { $Parameters.amount } else { 3 }) 'amount'
            if ($amountNumber -ne [Math]::Truncate($amountNumber) -or $amountNumber -lt 1 -or $amountNumber -gt $MaxScrollAtAmount) { throw "scroll_at amount must be an integer from 1 to $MaxScrollAtAmount." }
            $amount = [int64]$amountNumber
            $remaining = $amount
            $scrollEventCount = 0
            while ($remaining -gt 0) {
                $step = [int32][Math]::Min($MaxScrollEventAmount, $remaining)
                $delta = [int32]$(if ($direction -in @('up', 'left')) { 120 * $step } else { -120 * $step })
                $data = [BitConverter]::ToUInt32([BitConverter]::GetBytes($delta), 0)
                [CyreneWindowApi]::Mouse($(if ($direction -in @('left', 'right')) { [uint32]0x1000 } else { [uint32]0x0800 }), $data)
                $remaining -= $step
                $scrollEventCount += 1
            }
        }
        $actual = [CyreneWindowApi]::Cursor()
        $verified = [Math]::Abs($actual.X - $point.x) -le 2 -and [Math]::Abs($actual.Y - $point.y) -le 2
        $diagnostics = @{ method = 'SendInput'; point = $point; actualPointer = @{ x = $actual.X; y = $actual.Y }; pointerVerified = $verified; foregroundRequired = $true }
        if ($Capability -eq 'scroll_at') { $diagnostics.scrollEventCount = $scrollEventCount }
        return @{ ok = $true; verified = $verified; uncertain = -not $verified; skipSnapshot = $true; visualChangeExpected = ($Capability -ne 'hover_at'); summary = "Performed $Capability at ($($point.x), $($point.y))."; diagnostics = $diagnostics }
    }
    $from = if ($Capability -eq 'drag') { Screen-Point $Target $Parameters 'from_x' 'from_y' } else { Screen-Point $Target $Parameters 'x' 'y' }
    if ($Capability -eq 'drag') { $to = Screen-Point $Target $Parameters 'to_x' 'to_y' }
    else {
        $direction = ([string]$Parameters.direction).ToLowerInvariant()
        if ($direction -notin @('up', 'down', 'left', 'right')) { throw 'direction must be up, down, left, or right.' }
        $distance = [Math]::Max(1, [Math]::Min(2000, $(if ($Parameters.distance) { [double]$Parameters.distance } else { 240 })))
        $toParams = [pscustomobject]@{ coordinate_space = 'screen'; to_x = $from.x; to_y = $from.y }
        if ($direction -eq 'up') { $toParams.to_y -= $distance } elseif ($direction -eq 'down') { $toParams.to_y += $distance } elseif ($direction -eq 'left') { $toParams.to_x -= $distance } else { $toParams.to_x += $distance }
        $to = Screen-Point $Target $toParams 'to_x' 'to_y'
    }
    [void][CyreneWindowApi]::SetCursorPos($from.x, $from.y); [CyreneWindowApi]::Mouse([uint32]0x0002, 0)
    [CyreneWindowApi]::Move($from.x, $from.y, $to.x, $to.y, $(if ($Parameters.duration_ms) { [int]$Parameters.duration_ms } else { 350 }), $true)
    [CyreneWindowApi]::Mouse([uint32]0x0004, 0)
    $actual = [CyreneWindowApi]::Cursor(); $verified = [Math]::Abs($actual.X - $to.x) -le 2 -and [Math]::Abs($actual.Y - $to.y) -le 2
    return @{ ok = $true; verified = $verified; uncertain = -not $verified; skipSnapshot = $true; visualChangeExpected = $true; summary = "Performed $Capability gesture."; diagnostics = @{ method = 'SendInput'; from = $from; to = $to; actualPointer = @{ x = $actual.X; y = $actual.Y }; pointerVerified = $verified; foregroundRequired = $true } }
}

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
            # Window coordinates are a Win32 property and must remain available
            # even when the application exposes no UI Automation tree.
            $nativeBounds = [CyreneWindowApi]::WindowRect($handleValue)
            $rect = @{
                x = $nativeBounds.Left
                y = $nativeBounds.Top
                width = $nativeBounds.Right - $nativeBounds.Left
                height = $nativeBounds.Bottom - $nativeBounds.Top
            }
            if ($rect.width -le 0 -or $rect.height -le 0) { continue }
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
    # ControlView intentionally hides structural UIA nodes and can collapse an
    # Electron/Chromium subtree to a few generic containers. RawView is the
    # provider's complete semantic tree; downstream node limits keep it bounded.
    $walker = [System.Windows.Automation.TreeWalker]::RawViewWalker
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
    $maxNodes = if ($options.maxNodes) { [Math]::Max(1, [Math]::Min(500, [int]$options.maxNodes)) } else { if ($InspectOnly) { 40 } else { 80 } }
    $maxDepth = if ($options.maxDepth) { [Math]::Max(1, [Math]::Min(24, [int]$options.maxDepth)) } else { if ($InspectOnly) { 3 } else { 8 } }
    $nodes = [System.Collections.Generic.List[object]]::new()
    $queue = [System.Collections.Generic.Queue[object]]::new()
    $rootNode = Get-Node $start $nativeRef
    $rootNode['childCount'] = @(Get-Children $start).Count
    $nodes.Add($rootNode)
    $rootChildren = @(Get-Children $start)
    for ($childIndex = 0; $childIndex -lt $rootChildren.Count; $childIndex += 1) {
        $queue.Enqueue(@($rootChildren[$childIndex], "$nativeRef/e$childIndex", 1))
    }
    $truncated = $false
    $failedNodes = 0
    $depthLimited = $false
    $visited = 1
    while ($queue.Count -gt 0 -and $nodes.Count -lt $maxNodes) {
        $entry = $queue.Dequeue()
        $element = $entry[0]
        $elementRef = [string]$entry[1]
        $depth = [int]$entry[2]
        $visited += 1
        try {
            $node = Get-Node $element $elementRef
            $role = ([string]$node.role).ToLowerInvariant()
            $name = ([string]$node.name).Trim()
            $description = ([string]$node.description).Trim().ToLowerInvariant()
            $genericDescription = @('', 'group', 'application', 'pane', 'panel', 'container', 'unknown', '组', '应用', '窗格', '面板', '容器', '未知') -contains $description
            $children = @(Get-Children $element)
            $node['childCount'] = $children.Count
            $transparent = $children.Count -gt 0 -and [string]::IsNullOrWhiteSpace($name) -and $genericDescription -and $role -match 'application|group|pane|panel|container|custom|unknown'
            if ($transparent) {
                if ($depth -lt $maxDepth) {
                    for ($index = 0; $index -lt $children.Count; $index += 1) {
                        $queue.Enqueue(@($children[$index], "$elementRef/e$index", $depth + 1))
                    }
                } else {
                    $depthLimited = $true
                    $node.parentNativeRef = $nativeRef
                    $nodes.Add($node)
                }
            } else {
                $node.parentNativeRef = $nativeRef
                $nodes.Add($node)
            }
        } catch { $failedNodes += 1 }
        if ($nodes.Count -ge $maxNodes) { $truncated = $queue.Count -gt 0; break }
    }
    if ($queue.Count -gt 0) { $truncated = $true }
    if ($failedNodes -gt 0) { $truncated = $true }
    if ($depthLimited) { $truncated = $true }
    return @{ ok = $true; nodes = @($nodes); truncated = $truncated; depthLimited = $depthLimited; visited = $visited; failedNodes = $failedNodes; traversal = 'uia_raw_view_current_semantic_layer'; provider = 'UIAutomation' }
}

function Invoke-BackgroundAction($Element, [string]$Action) {
    if ($Action -eq 'press') {
        $invoke = Try-Pattern $Element ([System.Windows.Automation.InvokePattern]::Pattern)
        if ($null -ne $invoke) { $invoke.Invoke(); return 'Invoke' }
        $legacy = Try-Pattern $Element ([System.Windows.Automation.LegacyIAccessiblePattern]::Pattern)
        if ($null -ne $legacy) { $legacy.DoDefaultAction(); return 'LegacyIAccessible.DefaultAction' }
    }
    elseif ($Action -eq 'toggle') {
        $toggle = Try-Pattern $Element ([System.Windows.Automation.TogglePattern]::Pattern)
        if ($null -ne $toggle) { $toggle.Toggle(); return 'Toggle' }
        $expand = Try-Pattern $Element ([System.Windows.Automation.ExpandCollapsePattern]::Pattern)
        if ($null -ne $expand) {
            if ($expand.Current.ExpandCollapseState -eq [System.Windows.Automation.ExpandCollapseState]::Collapsed) { $expand.Expand() } else { $expand.Collapse() }
            return 'ExpandCollapse'
        }
    }
    elseif ($Action -eq 'select') {
        $selection = Try-Pattern $Element ([System.Windows.Automation.SelectionItemPattern]::Pattern)
        if ($null -ne $selection) { $selection.Select(); return 'SelectionItem' }
        $invoke = Try-Pattern $Element ([System.Windows.Automation.InvokePattern]::Pattern)
        if ($null -ne $invoke) { $invoke.Invoke(); return 'Invoke' }
    }
    return $null
}

function Hit-Test($Payload) {
    $target = $Payload.target
    $point = Screen-Point $target ([pscustomobject]@{ coordinate_space = 'screen'; x = $Payload.point.x; y = $Payload.point.y }) 'x' 'y'
    $preferred = @($Payload.preferredActions | ForEach-Object { ([string]$_).ToLowerInvariant() } | Where-Object { $_ -in @('press', 'select', 'toggle') })
    if ($preferred.Count -eq 0) { throw 'preferredActions must contain press, select, or toggle.' }
    $root = Get-Root $target
    $queue = [System.Collections.Generic.Queue[object]]::new()
    $queue.Enqueue(@($root, 0))
    $visited = 0
    $best = $null
    $bestArea = [double]::PositiveInfinity
    $timer = [Diagnostics.Stopwatch]::StartNew()
    while ($queue.Count -gt 0 -and $visited -lt 80 -and $timer.ElapsedMilliseconds -lt 2500) {
        $entry = $queue.Dequeue(); $element = $entry[0]; $depth = [int]$entry[1]
        $visited += 1
        try {
            $node = Get-Node $element ''
            $bounds = $node.bounds
            $contains = $depth -eq 0 -or (
                $bounds.width -gt 0 -and $bounds.height -gt 0 -and
                $point.x -ge $bounds.x -and $point.y -ge $bounds.y -and
                $point.x -lt ($bounds.x + $bounds.width) -and $point.y -lt ($bounds.y + $bounds.height)
            )
            if (-not $contains) { continue }
            foreach ($action in $preferred) {
                if ($node.actions -contains $action) {
                    $area = [double]$bounds.width * [double]$bounds.height
                    if ($area -lt $bestArea) { $best = @{ element = $element; node = $node; action = $action; depth = $depth }; $bestArea = $area }
                    break
                }
            }
            if ($depth -lt 12) {
                foreach ($child in @(Get-Children $element)) { $queue.Enqueue(@($child, $depth + 1)) }
            }
        } catch {}
    }
    if ($null -eq $best) {
        return @{ ok = $true; found = $false; diagnostics = @{ method = 'UIAutomation coordinate-pruned traversal'; point = $point; visited = $visited; treeScanUsed = $false; reason = 'no_supported_action' } }
    }
    $performed = $false; $nativeAction = [string]$best.node.nativeActions[0]
    if ($Payload.perform -eq $true) {
        $nativeAction = Invoke-BackgroundAction $best.element $best.action
        if ([string]::IsNullOrWhiteSpace($nativeAction)) { return @{ ok = $true; found = $false; performed = $false; diagnostics = @{ method = 'UIAutomation'; reason = 'element_changed_or_action_unsupported'; point = $point } } }
        $performed = $true
    }
    return @{
        ok = $true; found = $true; performed = $performed; verified = $performed
        action = $best.action; nativeAction = $nativeAction; role = $best.node.role; name = $best.node.name; bounds = $best.node.bounds
        diagnostics = @{ method = $(if ($performed) { 'UIAutomation pattern action' } else { 'UIAutomation coordinate-pruned traversal' }); point = $point; visited = $visited; depth = $best.depth; backgroundSafe = $true; treeScanUsed = $false }
        nextValidActions = @('call:wait', 'call:snapshot', 'disconnect')
    }
}

function Focus-Target($Target) {
    $handle = Target-Handle $Target
    $focused = [CyreneWindowApi]::FocusWindow($handle)
    $foreground = [CyreneWindowApi]::GetForegroundWindow().ToInt64()
    return @{
        ok = $true
        verified = ($focused -and $foreground -eq $handle.ToInt64())
        summary = "Focused $($Target.appName)."
        diagnostics = @{
            method = 'SetForegroundWindow+AttachThreadInput'
            requestedWindow = $handle.ToInt64()
            foregroundWindow = $foreground
            sameIntegrityLevelRequired = $true
        }
    }
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
    $nonModifiers = @($lowered | Where-Object { $modifiers -notcontains $_ })
    if ($nonModifiers.Count -ne 1) { throw 'key_chord requires exactly one non-modifier key.' }
    $key = $nonModifiers[0]
    $special = @{
        enter = '{ENTER}'; return = '{ENTER}'; tab = '{TAB}'; escape = '{ESC}'; esc = '{ESC}'
        backspace = '{BACKSPACE}'; delete = '{DELETE}'; up = '{UP}'; down = '{DOWN}'
        left = '{LEFT}'; right = '{RIGHT}'; space = ' '
    }
    $encoded = if ($special.ContainsKey($key)) { $special[$key] } else { Escape-SendKeys $key }
    [System.Windows.Forms.SendKeys]::SendWait($prefix + $encoded)
}

function Get-TextPattern($Element) {
    $pattern = Try-Pattern $Element ([System.Windows.Automation.TextPattern]::Pattern)
    if ($null -eq $pattern) { throw 'Element does not support TextPattern text selection.' }
    return $pattern
}

function Set-TextSelectionRange($Element, [int]$Start, [int]$End) {
    $pattern = Get-TextPattern $Element
    $document = $pattern.DocumentRange
    $text = [string]$document.GetText(-1)
    if ($Start -lt 0 -or $End -lt $Start -or $End -gt $text.Length) { throw "Selection range must satisfy 0 <= start <= end <= $($text.Length)." }
    $startRange = $document.Clone(); $startRange.MoveEndpointByRange([System.Windows.Automation.Text.TextPatternRangeEndpoint]::End, $startRange, [System.Windows.Automation.Text.TextPatternRangeEndpoint]::Start)
    [void]$startRange.Move([System.Windows.Automation.Text.TextUnit]::Character, $Start)
    $endRange = $document.Clone(); $endRange.MoveEndpointByRange([System.Windows.Automation.Text.TextPatternRangeEndpoint]::End, $endRange, [System.Windows.Automation.Text.TextPatternRangeEndpoint]::Start)
    [void]$endRange.Move([System.Windows.Automation.Text.TextUnit]::Character, $End)
    $selection = $document.Clone()
    $selection.MoveEndpointByRange([System.Windows.Automation.Text.TextPatternRangeEndpoint]::Start, $startRange, [System.Windows.Automation.Text.TextPatternRangeEndpoint]::Start)
    $selection.MoveEndpointByRange([System.Windows.Automation.Text.TextPatternRangeEndpoint]::End, $endRange, [System.Windows.Automation.Text.TextPatternRangeEndpoint]::Start)
    $selection.Select()
    $observed = @($pattern.GetSelection() | ForEach-Object { $_.GetText(-1) }) -join ''
    $expected = $text.Substring($Start, $End - $Start)
    return @{ start = $Start; end = $End; expected = $expected; observed = $observed; verified = ($observed -eq $expected) }
}

function Perform-KeySequence($Steps) {
    $items = @($Steps)
    if ($items.Count -lt 1 -or $items.Count -gt 64) { throw 'key_sequence requires between 1 and 64 steps.' }
    foreach ($step in $items) {
        $type = ([string]$step.type).ToLowerInvariant()
        if ($type -eq 'shortcut') { Send-KeyChord $step.keys }
        elseif ($type -eq 'text') { [System.Windows.Forms.SendKeys]::SendWait((Escape-SendKeys ([string]$step.text))) }
        elseif ($type -eq 'key') { Send-KeyChord @([string]$step.key) }
        elseif ($type -eq 'pause') { Start-Sleep -Milliseconds ([Math]::Max(0, [Math]::Min(5000, [int]$step.ms))) }
        else { throw "Unsupported key_sequence step type: $type" }
    }
    return @{ ok = $true; verified = $false; uncertain = $true; skipSnapshot = $true; visualChangeExpected = $true; summary = 'Executed the atomic keyboard sequence.'; diagnostics = @{ method = 'SendKeys'; stepCount = $items.Count; foregroundRequired = $true } }
}

function Perform-Action($Payload) {
    $root = Get-Root $Payload.target
    $element = if ($Payload.nativeRef) { Resolve-Element $root ([string]$Payload.nativeRef) } else { $root }
    $capability = [string]$Payload.capability
    $parameters = $Payload.parameters
    if ($capability -in @('click_at', 'double_click', 'right_click', 'hover_at', 'drag', 'swipe', 'scroll_at')) { return Perform-CoordinateAction $Payload.target $capability $parameters }
    if ($capability -eq 'key_sequence') { return Perform-KeySequence $parameters.steps }
    if ($capability -in @('semantic_double_click', 'semantic_drag')) {
        $legacy = Try-Pattern $element ([System.Windows.Automation.LegacyIAccessiblePattern]::Pattern)
        if ($null -eq $legacy) { throw "Element does not expose a native $capability action." }
        $legacy.DoDefaultAction()
        return @{ ok = $true; verified = $true; summary = "Performed $capability through UI Automation."; diagnostics = @{ method = 'LegacyIAccessible.DoDefaultAction' } }
    }
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
    if ($capability -eq 'set_selection_range') {
        $selection = Set-TextSelectionRange $element ([int]$parameters.start) ([int]$parameters.end)
        return @{ ok = $true; verified = $selection.verified; uncertain = -not $selection.verified; skipSnapshot = $true; summary = $(if ($selection.verified) { 'Selected and verified the requested text range.' } else { 'Selected the requested text range, but verification differed.' }); diagnostics = @{ method = 'TextPatternRange.Select'; start = $selection.start; end = $selection.end; expected = $selection.expected; observed = $selection.observed; foregroundRequired = $true } }
    }
    if ($capability -eq 'select_text') {
        $pattern = Get-TextPattern $element
        $value = [string]$pattern.DocumentRange.GetText(-1)
        $needle = [string]$parameters.text
        if ([string]::IsNullOrEmpty($needle)) { throw 'select_text requires non-empty text.' }
        $comparison = if ($parameters.case_sensitive -eq $true) { [StringComparison]::Ordinal } else { [StringComparison]::OrdinalIgnoreCase }
        $occurrence = [Math]::Max(1, $(if ($parameters.occurrence) { [int]$parameters.occurrence } else { 1 }))
        $start = -1; $cursor = 0
        for ($index = 0; $index -lt $occurrence; $index += 1) { $start = $value.IndexOf($needle, $cursor, $comparison); if ($start -lt 0) { throw "Could not find occurrence $occurrence of the requested text." }; $cursor = $start + $needle.Length }
        $selection = Set-TextSelectionRange $element $start ($start + $needle.Length)
        return @{ ok = $true; verified = $selection.verified; uncertain = -not $selection.verified; skipSnapshot = $true; summary = $(if ($selection.verified) { 'Selected and verified the requested text.' } else { 'Selected the requested text, but verification differed.' }); diagnostics = @{ method = 'TextPatternRange.Select'; start = $selection.start; end = $selection.end; expected = $selection.expected; observed = $selection.observed; foregroundRequired = $true } }
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
        'hit_test' { Result (Hit-Test $payload) }
        'perform' { Result (Perform-Action $payload) }
        'focus' { Result (Focus-Target $payload.target) }
        default { Fail 'invalid_arguments' "Unknown operation: $($payload.operation)" }
    }
} catch {
    $message = [string]$_.Exception.Message
    $type = if ($message -match 'access|denied|privilege|integrity') { 'permission_required' } else { 'provider_error' }
    Fail $type $message
}
