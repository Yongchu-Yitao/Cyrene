import ApplicationServices
import AppKit
import Foundation

enum HelperFailure: Error {
    case typed(String, String)
}

func fail(_ type: String, _ message: String) throws -> Never {
    throw HelperFailure.typed(type, message)
}

func jsonOutput(_ value: [String: Any]) {
    let data = try! JSONSerialization.data(withJSONObject: value, options: [])
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data([0x0A]))
}

func number(_ value: Any?, _ name: String) throws -> Double {
    guard let value, let result = (value as? NSNumber)?.doubleValue, result.isFinite else {
        try fail("invalid_arguments", "\(name) must be a finite number.")
    }
    return result
}

func stringAttribute(_ element: AXUIElement, _ attribute: CFString) -> String {
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, attribute, &value) == .success else { return "" }
    return value as? String ?? ""
}

func elementBounds(_ element: AXUIElement) -> [String: Double]? {
    var positionValue: CFTypeRef?
    var sizeValue: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, kAXPositionAttribute as CFString, &positionValue) == .success,
          AXUIElementCopyAttributeValue(element, kAXSizeAttribute as CFString, &sizeValue) == .success,
          let positionValue, let sizeValue,
          CFGetTypeID(positionValue) == AXValueGetTypeID(),
          CFGetTypeID(sizeValue) == AXValueGetTypeID()
    else { return nil }
    var point = CGPoint.zero
    var size = CGSize.zero
    guard AXValueGetValue(positionValue as! AXValue, .cgPoint, &point),
          AXValueGetValue(sizeValue as! AXValue, .cgSize, &size)
    else { return nil }
    return ["x": point.x, "y": point.y, "width": size.width, "height": size.height]
}

let actionMappings: [String: [String]] = [
    "press": [kAXPressAction as String, kAXConfirmAction as String, kAXPickAction as String],
    "select": [kAXPressAction as String, kAXConfirmAction as String, kAXPickAction as String],
    "toggle": [kAXPressAction as String, "AXToggle"],
]

func supportedAction(_ element: AXUIElement, preferred: [String]) -> (String, String)? {
    var actionValues: CFArray?
    guard AXUIElementCopyActionNames(element, &actionValues) == .success,
          let actionNames = actionValues as? [String]
    else { return nil }
    let available = Set(actionNames)
    for semantic in preferred {
        for native in actionMappings[semantic] ?? [] where available.contains(native) {
            return (semantic, native)
        }
    }
    return nil
}

func errorType(_ error: AXError) -> String {
    switch error {
    case .apiDisabled:
        return "permission_required"
    case .cannotComplete:
        return "accessibility_query_timeout"
    case .invalidUIElement:
        return "stale_session"
    default:
        return "provider_error"
    }
}

func children(_ element: AXUIElement) -> [AXUIElement] {
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, kAXChildrenAttribute as CFString, &value) == .success,
          let items = value as? [AXUIElement]
    else { return [] }
    return items
}

let semanticAttributeNames = [
    kAXRoleAttribute as String,
    kAXSubroleAttribute as String,
    kAXTitleAttribute as String,
    kAXDescriptionAttribute as String,
    kAXHelpAttribute as String,
    kAXValueAttribute as String,
    kAXEnabledAttribute as String,
    kAXFocusedAttribute as String,
    kAXSelectedAttribute as String,
    kAXPositionAttribute as String,
    kAXSizeAttribute as String,
    "AXIdentifier",
    "AXRoleDescription",
    "AXPlaceholderValue",
    "AXChildrenInNavigationOrder",
    kAXChildrenAttribute as String,
    "AXVisibleChildren",
    "AXRows",
    "AXContents",
]

func multipleAttributes(_ element: AXUIElement, _ names: [String] = semanticAttributeNames) -> [String: Any] {
    var copied: CFArray?
    let attributes = names.map { $0 as CFString } as CFArray
    let error = AXUIElementCopyMultipleAttributeValues(
        element,
        attributes,
        AXCopyMultipleAttributeOptions(rawValue: 0),
        &copied
    )
    guard error == .success, let values = copied as? [Any] else { return [:] }
    var result: [String: Any] = [:]
    for (index, name) in names.enumerated() where index < values.count {
        result[name] = values[index]
    }
    return result
}

func textValue(_ value: Any?) -> String {
    if let text = value as? String { return text }
    if let attributed = value as? NSAttributedString { return attributed.string }
    return ""
}

func booleanValue(_ value: Any?, default fallback: Bool = false) -> Bool {
    return (value as? NSNumber)?.boolValue ?? fallback
}

func jsonValue(_ value: Any?) -> Any? {
    if let text = value as? String { return text }
    if let attributed = value as? NSAttributedString { return attributed.string }
    if let number = value as? NSNumber { return number }
    return nil
}

func boundsFromAttributes(_ values: [String: Any]) -> [String: Double]? {
    guard let positionValue = values[kAXPositionAttribute as String],
          let sizeValue = values[kAXSizeAttribute as String],
          CFGetTypeID(positionValue as CFTypeRef) == AXValueGetTypeID(),
          CFGetTypeID(sizeValue as CFTypeRef) == AXValueGetTypeID()
    else { return nil }
    var point = CGPoint.zero
    var size = CGSize.zero
    guard AXValueGetValue(positionValue as! AXValue, .cgPoint, &point),
          AXValueGetValue(sizeValue as! AXValue, .cgSize, &size)
    else { return nil }
    return ["x": point.x, "y": point.y, "width": size.width, "height": size.height]
}

func elementArray(_ value: Any?) -> [AXUIElement] {
    guard let value, CFGetTypeID(value as CFTypeRef) == CFArrayGetTypeID() else { return [] }
    let array = unsafeBitCast(value as CFTypeRef, to: CFArray.self)
    var result: [AXUIElement] = []
    for index in 0..<CFArrayGetCount(array) {
        guard let pointer = CFArrayGetValueAtIndex(array, index) else { continue }
        let element = Unmanaged<AXUIElement>.fromOpaque(pointer).takeUnretainedValue()
        if CFGetTypeID(element) == AXUIElementGetTypeID() { result.append(element) }
    }
    return result
}

func appendUniqueElements(_ source: [AXUIElement], to destination: inout [AXUIElement]) {
    for candidate in source where !destination.contains(where: { CFEqual($0, candidate) }) {
        destination.append(candidate)
    }
}

func nativeChildren(_ element: AXUIElement, attributes: [String: Any]? = nil) -> [AXUIElement] {
    let values = attributes ?? multipleAttributes(
        element,
        ["AXChildrenInNavigationOrder", kAXChildrenAttribute as String, "AXVisibleChildren", "AXRows", "AXContents"]
    )
    var result: [AXUIElement] = []
    let childAttributes = ["AXChildrenInNavigationOrder", kAXChildrenAttribute as String, "AXVisibleChildren", "AXRows", "AXContents"]
    for name in childAttributes {
        appendUniqueElements(elementArray(values[name]), to: &result)
    }
    if result.isEmpty {
        for name in childAttributes {
            var copied: CFTypeRef?
            if AXUIElementCopyAttributeValue(element, name as CFString, &copied) == .success {
                appendUniqueElements(elementArray(copied), to: &result)
            }
        }
    }
    return result
}

func nativeActionNames(_ element: AXUIElement) -> [String] {
    var copied: CFArray?
    guard AXUIElementCopyActionNames(element, &copied) == .success else { return [] }
    return copied as? [String] ?? []
}

func semanticActions(_ nativeActions: [String], role: String, valueSettable: Bool) -> [String] {
    var actions: [String] = []
    if nativeActions.contains(where: { [kAXPressAction as String, kAXConfirmAction as String, kAXPickAction as String, "AXOpen"].contains($0) }) {
        actions.append("press")
    }
    if nativeActions.contains("AXDoublePress") { actions.append("semantic_double_click") }
    if nativeActions.contains(where: { ["AXDrag", "AXMove", "AXReorder", "AXResize"].contains($0) }) {
        actions.append("semantic_drag")
    }
    if role.range(of: "ScrollArea|ScrollBar|Slider|Stepper", options: .regularExpression) != nil,
       nativeActions.contains(where: { $0 == kAXIncrementAction as String || $0 == kAXDecrementAction as String }) {
        actions.append("scroll")
    }
    if role.range(of: "CheckBox|Switch|DisclosureTriangle", options: .regularExpression) != nil,
       actions.contains("press") {
        actions.append("toggle")
    }
    if role.range(of: "RadioButton|Row|MenuItem|Tab|ListItem", options: .regularExpression) != nil,
       actions.contains("press") {
        actions.append("select")
    }
    if valueSettable,
       role.range(of: "TextField|TextArea|ComboBox|SearchField", options: .regularExpression) != nil {
        actions.append("set_value")
    }
    return Array(NSOrderedSet(array: actions)) as? [String] ?? actions
}

struct NativeNodeRead {
    let node: [String: Any]
    let children: [AXUIElement]
}

func readNativeNode(_ element: AXUIElement, nativeRef: String, parentNativeRef: String) -> NativeNodeRead {
    let values = multipleAttributes(element)
    let nodeChildren = nativeChildren(element, attributes: values)
    let role = textValue(values[kAXRoleAttribute as String])
    let title = textValue(values[kAXTitleAttribute as String])
    let description = textValue(values[kAXDescriptionAttribute as String])
    let help = textValue(values[kAXHelpAttribute as String])
    let roleDescription = textValue(values["AXRoleDescription"])
    let placeholder = textValue(values["AXPlaceholderValue"])
    let nativeActions = nativeActionNames(element)
    var valueSettable = DarwinBoolean(false)
    _ = AXUIElementIsAttributeSettable(element, kAXValueAttribute as CFString, &valueSettable)
    let semantic = semanticActions(nativeActions, role: role, valueSettable: valueSettable.boolValue)
    var node: [String: Any] = [
        "nativeRef": nativeRef,
        "role": role.replacingOccurrences(of: "AX", with: "", options: .anchored),
        "subrole": textValue(values[kAXSubroleAttribute as String]).replacingOccurrences(of: "AX", with: "", options: .anchored),
        "name": title.isEmpty ? (description.isEmpty ? placeholder : description) : title,
        "description": description.isEmpty ? roleDescription : description,
        "help": help,
        "automationId": textValue(values["AXIdentifier"]),
        "enabled": booleanValue(values[kAXEnabledAttribute as String], default: true),
        "focused": booleanValue(values[kAXFocusedAttribute as String]),
        "selected": booleanValue(values[kAXSelectedAttribute as String]),
        "actions": semantic,
        "nativeActions": nativeActions,
        "childCount": nodeChildren.count,
    ]
    if !parentNativeRef.isEmpty { node["parentNativeRef"] = parentNativeRef }
    if let value = jsonValue(values[kAXValueAttribute as String]) { node["value"] = value }
    if let bounds = boundsFromAttributes(values) { node["bounds"] = bounds }
    return NativeNodeRead(node: node, children: nodeChildren)
}

func targetWindow(_ target: [String: Any]) throws -> (AXUIElement, AXUIElement, String) {
    let pid = pid_t(try number(target["pid"], "target.pid"))
    guard AXIsProcessTrusted() else { try fail("permission_required", "Accessibility permission is required.") }
    let application = AXUIElementCreateApplication(pid)
    _ = AXUIElementSetMessagingTimeout(application, 0.75)
    var navigationValue: CFTypeRef?
    _ = AXUIElementCopyAttributeValue(application, "AXChildrenInNavigationOrder" as CFString, &navigationValue)
    let navigationChildren = elementArray(navigationValue)
    var windows = navigationChildren.filter {
        stringAttribute($0, kAXRoleAttribute as CFString) == kAXWindowRole as String
    }
    var windowsValue: CFTypeRef?
    let windowError = AXUIElementCopyAttributeValue(application, kAXWindowsAttribute as CFString, &windowsValue)
    if windows.isEmpty {
        windows = elementArray(windowsValue).filter {
            stringAttribute($0, kAXRoleAttribute as CFString) == kAXWindowRole as String
        }
    }
    if windows.isEmpty {
        windows = nativeChildren(application).filter {
            stringAttribute($0, kAXRoleAttribute as CFString) == kAXWindowRole as String
        }
    }
    guard !windows.isEmpty else {
        let roles = navigationChildren.map { stringAttribute($0, kAXRoleAttribute as CFString) }
        try fail(errorType(windowError), "The target application does not currently expose an accessibility window (navigation roles: \(roles)).")
    }
    let requestedTitle = String(describing: target["windowTitle"] ?? "")
    let requestedIndex = (target["windowIndex"] as? NSNumber)?.intValue ?? 0
    let requestedBounds = target["bounds"] as? [String: Any]
    var bestIndex = max(0, min(requestedIndex, windows.count - 1))
    var bestScore = Double.greatestFiniteMagnitude
    for (index, candidate) in windows.enumerated() {
        let title = stringAttribute(candidate, kAXTitleAttribute as CFString)
        var score = requestedTitle.isEmpty || title == requestedTitle ? 0.0 : 100_000.0
        if let expected = requestedBounds, let observed = elementBounds(candidate) {
            for key in ["x", "y", "width", "height"] {
                score += abs(((expected[key] as? NSNumber)?.doubleValue ?? observed[key]!) - observed[key]!)
            }
        } else if index != requestedIndex {
            score += 1000
        }
        if score < bestScore { bestScore = score; bestIndex = index }
    }
    return (application, windows[bestIndex], "w\(requestedIndex)")
}

func resolveNativeElement(_ root: AXUIElement, nativeRef: String) throws -> AXUIElement {
    let parts = nativeRef.split(separator: "/").map(String.init)
    if parts.isEmpty { return root }
    guard parts[0].range(of: "^w\\d+$", options: .regularExpression) != nil else {
        try fail("invalid_arguments", "Invalid native element path: \(nativeRef)")
    }
    var current = root
    for part in parts.dropFirst() {
        guard part.first == "e", let index = Int(part.dropFirst()) else {
            try fail("invalid_arguments", "Invalid native element path: \(nativeRef)")
        }
        let descendants = nativeChildren(current)
        guard index >= 0 && index < descendants.count else {
            try fail("stale_element", "Element path is stale: \(nativeRef)")
        }
        current = descendants[index]
    }
    return current
}

func nativeSnapshot(_ payload: [String: Any], inspectOnly: Bool) throws -> [String: Any] {
    guard let target = payload["target"] as? [String: Any] else { try fail("invalid_arguments", "target is required.") }
    let (_, window, defaultRootRef) = try targetWindow(target)
    let options = payload["options"] as? [String: Any] ?? [:]
    let requestedRef = String(describing: payload["nativeRef"] ?? options["nativeRef"] ?? defaultRootRef)
    let start = try resolveNativeElement(window, nativeRef: requestedRef)
    let defaultNodes = inspectOnly ? 40 : 80
    let defaultDepth = inspectOnly ? 3 : 8
    let maxNodes = max(1, min(500, (options["maxNodes"] as? NSNumber)?.intValue ?? defaultNodes))
    let maxDepth = max(1, min(24, (options["maxDepth"] as? NSNumber)?.intValue ?? defaultDepth))
    let maxVisited = max(500, maxNodes * 4)
    let rootRead = readNativeNode(start, nativeRef: requestedRef, parentNativeRef: "")
    var nodes: [[String: Any]] = [rootRead.node]
    var queue: [(AXUIElement, String, Int)] = rootRead.children.enumerated().map {
        ($0.element, "\(requestedRef)/e\($0.offset)", 1)
    }
    var seen: [AXUIElement] = [start]
    var visited = 1
    var depthLimited = false
    while !queue.isEmpty && nodes.count < maxNodes && visited < maxVisited {
        let (element, nativeRef, depth) = queue.removeFirst()
        if seen.contains(where: { CFEqual($0, element) }) { continue }
        seen.append(element)
        visited += 1
        let read = readNativeNode(element, nativeRef: nativeRef, parentNativeRef: requestedRef)
        let role = String(describing: read.node["role"] ?? "").lowercased()
        let name = String(describing: read.node["name"] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let description = String(describing: read.node["description"] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let genericDescription = ["", "group", "application", "pane", "container", "unknown", "组", "应用", "窗格", "容器", "未知"].contains(description.lowercased())
        let transparent = !read.children.isEmpty && name.isEmpty && genericDescription
            && role.range(of: "application|group|pane|container|unknown|hostingview", options: .regularExpression) != nil
        if transparent {
            if depth < maxDepth {
                for (index, child) in read.children.enumerated() {
                    queue.append((child, "\(nativeRef)/e\(index)", depth + 1))
                }
            } else {
                depthLimited = true
                nodes.append(read.node)
            }
        } else {
            nodes.append(read.node)
        }
    }
    return [
        "ok": true,
        "nodes": nodes,
        "truncated": !queue.isEmpty || depthLimited || visited >= maxVisited,
        "depthLimited": depthLimited,
        "visited": visited,
        "traversal": "native_ax_current_semantic_layer",
        "provider": "AXUIElement",
    ]
}

func performPreferredAction(_ element: AXUIElement, preferred: [String]) throws -> String {
    let available = Set(nativeActionNames(element))
    guard let action = preferred.first(where: { available.contains($0) }) else {
        try fail("unsupported_action", "Element does not support any of: \(preferred.joined(separator: ", ")).")
    }
    let result = AXUIElementPerformAction(element, action as CFString)
    guard result == .success else { try fail(errorType(result), "Accessibility action \(action) failed with AX error \(result.rawValue).") }
    return action
}

func nativePerform(_ payload: [String: Any]) throws -> [String: Any] {
    guard let target = payload["target"] as? [String: Any] else { try fail("invalid_arguments", "target is required.") }
    let (_, window, defaultRootRef) = try targetWindow(target)
    let nativeRef = String(describing: payload["nativeRef"] ?? defaultRootRef)
    let element = try resolveNativeElement(window, nativeRef: nativeRef)
    let capability = String(describing: payload["capability"] ?? "")
    let parameters = payload["parameters"] as? [String: Any] ?? [:]
    var diagnostics: [String: Any] = ["provider": "AXUIElement", "nativeRef": nativeRef, "backgroundSafe": true]
    if capability == "set_value" || capability == "type_text" {
        var settable = DarwinBoolean(false)
        guard AXUIElementIsAttributeSettable(element, kAXValueAttribute as CFString, &settable) == .success,
              settable.boolValue
        else { try fail("unsupported_action", "The element does not expose a writable accessibility value.") }
        let before = stringAttribute(element, kAXValueAttribute as CFString)
        let requested = capability == "set_value"
            ? String(describing: parameters["value"] ?? "")
            : String(describing: parameters["text"] ?? "")
        let expected = capability == "type_text" && parameters["replace"] as? Bool != true ? before + requested : requested
        let setError = AXUIElementSetAttributeValue(element, kAXValueAttribute as CFString, expected as CFTypeRef)
        guard setError == .success else { try fail(errorType(setError), "The element rejected AXValue.") }
        let after = stringAttribute(element, kAXValueAttribute as CFString)
        guard after == expected else { try fail("verification_failed", "The accessibility value did not match the requested text.") }
        diagnostics["method"] = "AXValue"
        diagnostics["before"] = before
        diagnostics["after"] = after
        return ["ok": true, "verified": true, "summary": "Wrote and verified text through AXValue.", "diagnostics": diagnostics]
    }
    if capability == "scroll" {
        let direction = String(describing: parameters["direction"] ?? "down").lowercased()
        let amount = max(1, min(20, (parameters["amount"] as? NSNumber)?.intValue ?? 3))
        let action = direction == "up" || direction == "left" ? kAXDecrementAction as String : kAXIncrementAction as String
        for _ in 0..<amount { _ = try performPreferredAction(element, preferred: [action]) }
        diagnostics["nativeAction"] = action
        return ["ok": true, "verified": true, "summary": "Scrolled through native accessibility actions.", "diagnostics": diagnostics]
    }
    let preferred: [String]
    switch capability {
    case "semantic_double_click": preferred = ["AXDoublePress"]
    case "semantic_drag": preferred = ["AXDrag", "AXMove", "AXReorder", "AXResize"]
    case "toggle": preferred = [kAXPressAction as String, "AXToggle"]
    case "press", "select": preferred = [kAXPressAction as String, kAXConfirmAction as String, kAXPickAction as String, "AXOpen"]
    default: try fail("unsupported_action", "Unsupported native semantic capability: \(capability).")
    }
    let action = try performPreferredAction(element, preferred: preferred)
    diagnostics["nativeAction"] = action
    return ["ok": true, "verified": true, "summary": "Performed \(action) through AXUIElement.", "diagnostics": diagnostics]
}

func menuCommand(_ payload: [String: Any]) throws -> [String: Any] {
    guard let target = payload["target"] as? [String: Any] else {
        try fail("invalid_arguments", "target is required.")
    }
    let pid = pid_t(try number(target["pid"], "target.pid"))
    let requestedName = String(describing: payload["name"] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
    let shortcut = (payload["shortcut"] as? [Any] ?? []).map { String(describing: $0).lowercased() }
    guard !requestedName.isEmpty || !shortcut.isEmpty else {
        try fail("invalid_arguments", "menu_command requires a menu item name or shortcut.")
    }
    guard AXIsProcessTrusted() else {
        try fail("permission_required", "Accessibility permission is required for background menu activation.")
    }
    let application = AXUIElementCreateApplication(pid)
    _ = AXUIElementSetMessagingTimeout(application, 0.75)
    var menuBarValue: CFTypeRef?
    guard AXUIElementCopyAttributeValue(application, kAXMenuBarAttribute as CFString, &menuBarValue) == .success,
          let menuBarValue, CFGetTypeID(menuBarValue) == AXUIElementGetTypeID()
    else { try fail("unsupported_action", "The target application does not expose an accessibility menu bar.") }
    let menuBar = unsafeBitCast(menuBarValue, to: AXUIElement.self)
    let modifiers = Set(shortcut.filter { ["command", "meta", "control", "ctrl", "option", "alt", "shift"].contains($0) })
    let shortcutKey = shortcut.first { !["command", "meta", "control", "ctrl", "option", "alt", "shift"].contains($0) } ?? ""
    var queue = children(menuBar)
    var visited = 0
    var match: AXUIElement?
    var matchTitle = ""
    var matchShortcut = ""
    while !queue.isEmpty && visited < 500 {
        let element = queue.removeFirst()
        visited += 1
        let role = stringAttribute(element, kAXRoleAttribute as CFString)
        let title = stringAttribute(element, kAXTitleAttribute as CFString)
        let commandChar = stringAttribute(element, kAXMenuItemCmdCharAttribute as CFString).lowercased()
        let nameMatches = !requestedName.isEmpty && title.localizedCaseInsensitiveContains(requestedName)
        var shortcutMatches = false
        if !shortcutKey.isEmpty && commandChar == shortcutKey.lowercased() {
            var rawModifiers: CFTypeRef?
            let modifierValue = AXUIElementCopyAttributeValue(element, kAXMenuItemCmdModifiersAttribute as CFString, &rawModifiers) == .success
                ? (rawModifiers as? NSNumber)?.intValue ?? 0 : 0
            let expected = (modifiers.contains("shift") ? 1 : 0)
                | ((modifiers.contains("option") || modifiers.contains("alt")) ? 2 : 0)
                | ((modifiers.contains("control") || modifiers.contains("ctrl")) ? 4 : 0)
                | ((modifiers.contains("command") || modifiers.contains("meta")) ? 0 : 8)
            shortcutMatches = modifierValue == expected
        }
        if role == (kAXMenuItemRole as String) && (nameMatches || shortcutMatches), supportedAction(element, preferred: ["press"]) != nil {
            match = element
            matchTitle = title
            matchShortcut = commandChar
            break
        }
        queue.append(contentsOf: children(element))
    }
    guard let match else {
        return ["ok": true, "found": false, "diagnostics": ["method": "AX menu traversal", "visited": visited, "reason": "menu_item_not_found"]]
    }
    let shouldPerform = payload["perform"] as? Bool == true
    let foregroundBefore = NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
    if shouldPerform {
        let actionError = AXUIElementPerformAction(match, kAXPressAction as CFString)
        guard actionError == .success else {
            try fail(errorType(actionError), "Background menu action failed with AX error \(actionError.rawValue).")
        }
        Thread.sleep(forTimeInterval: 0.05)
    }
    let foregroundAfter = NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
    return [
        "ok": true, "found": true, "performed": shouldPerform, "verified": shouldPerform,
        "action": "press", "nativeAction": kAXPressAction as String,
        "role": "MenuItem", "name": matchTitle,
        "foregroundAffected": foregroundBefore != foregroundAfter,
        "diagnostics": [
            "method": shouldPerform ? "AX menu item press" : "AX menu traversal", "visited": visited,
            "shortcut": matchShortcut, "foregroundBefore": Int(foregroundBefore),
            "foregroundAfter": Int(foregroundAfter), "backgroundSafe": foregroundBefore == foregroundAfter,
            "treeScanUsed": false,
        ],
    ]
}

func enableAccessibility(_ payload: [String: Any]) throws -> [String: Any] {
    guard let target = payload["target"] as? [String: Any] else { try fail("invalid_arguments", "target is required.") }
    let pid = pid_t(try number(target["pid"], "target.pid"))
    guard AXIsProcessTrusted() else { try fail("permission_required", "Accessibility permission is required.") }
    let application = AXUIElementCreateApplication(pid)
    _ = AXUIElementSetMessagingTimeout(application, 0.75)
    let foregroundBefore = NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
    let error = AXUIElementSetAttributeValue(application, "AXManualAccessibility" as CFString, kCFBooleanTrue)
    let deadline = Date().addingTimeInterval(5.0)
    var ready = false
    var attempts = 0
    var observedChildren = 0
    repeat {
        attempts += 1
        var value: CFTypeRef?
        let childrenError = AXUIElementCopyAttributeValue(application, kAXChildrenAttribute as CFString, &value)
        if childrenError == .success, let children = value as? [AXUIElement] {
            observedChildren = children.count
            ready = !children.isEmpty
        }
        if !ready { Thread.sleep(forTimeInterval: min(0.1 * Double(attempts), 0.5)) }
    } while !ready && Date() < deadline
    let foregroundAfter = NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
    return [
        "ok": true, "enabled": error == .success, "supported": error != .attributeUnsupported,
        "ready": ready, "status": ready ? "available" : "initializing",
        "foregroundAffected": foregroundBefore != foregroundAfter,
        "diagnostics": [
            "method": "AXManualAccessibility", "axError": error.rawValue,
            "readinessAttempts": attempts, "observedChildren": observedChildren,
            "foregroundBefore": Int(foregroundBefore), "foregroundAfter": Int(foregroundAfter),
            "backgroundSafe": foregroundBefore == foregroundAfter,
        ],
    ]
}

func typeAt(_ payload: [String: Any]) throws -> [String: Any] {
    guard let target = payload["target"] as? [String: Any],
          let pointPayload = payload["point"] as? [String: Any]
    else { try fail("invalid_arguments", "target and point are required.") }
    let pid = pid_t(try number(target["pid"], "target.pid"))
    let x = try number(pointPayload["x"], "point.x")
    let y = try number(pointPayload["y"], "point.y")
    let text = String(describing: payload["text"] ?? "")
    let replace = payload["replace"] as? Bool == true
    let shouldPerform = payload["perform"] as? Bool == true
    guard AXIsProcessTrusted() else { try fail("permission_required", "Accessibility permission is required.") }
    let application = AXUIElementCreateApplication(pid)
    _ = AXUIElementSetMessagingTimeout(application, 0.75)
    var hitElement: AXUIElement?
    let hitError = AXUIElementCopyElementAtPosition(application, Float(x), Float(y), &hitElement)
    guard hitError == .success, let initialElement = hitElement else {
        try fail(errorType(hitError), "Coordinate text hit-test failed with AX error \(hitError.rawValue).")
    }
    var current = initialElement
    var editable: AXUIElement?
    var ancestorDepth = 0
    for depth in 0...12 {
        ancestorDepth = depth
        var settable = DarwinBoolean(false)
        let role = stringAttribute(current, kAXRoleAttribute as CFString)
        if [kAXTextFieldRole as String, kAXTextAreaRole as String, kAXComboBoxRole as String].contains(role),
           AXUIElementIsAttributeSettable(current, kAXValueAttribute as CFString, &settable) == .success && settable.boolValue {
            editable = current
            break
        }
        var parentValue: CFTypeRef?
        guard AXUIElementCopyAttributeValue(current, kAXParentAttribute as CFString, &parentValue) == .success,
              let parentValue, CFGetTypeID(parentValue) == AXUIElementGetTypeID()
        else { break }
        current = unsafeBitCast(parentValue, to: AXUIElement.self)
    }
    guard let editable else {
        return ["ok": true, "found": false, "diagnostics": [
            "method": "AX coordinate editable hit-test", "reason": "no_settable_value",
            "point": ["x": x, "y": y], "ancestorsChecked": ancestorDepth + 1, "treeScanUsed": false,
        ]]
    }
    var beforeValue: CFTypeRef?
    _ = AXUIElementCopyAttributeValue(editable, kAXValueAttribute as CFString, &beforeValue)
    let before = beforeValue as? String ?? ""
    let expected = replace ? text : before + text
    let foregroundBefore = NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
    if shouldPerform {
        _ = AXUIElementSetAttributeValue(editable, kAXFocusedAttribute as CFString, kCFBooleanTrue)
        let setError = AXUIElementSetAttributeValue(editable, kAXValueAttribute as CFString, expected as CFTypeRef)
        guard setError == .success else { try fail(errorType(setError), "The coordinate text control rejected AXValue.") }
    }
    var afterValue: CFTypeRef?
    _ = AXUIElementCopyAttributeValue(editable, kAXValueAttribute as CFString, &afterValue)
    let after = afterValue as? String ?? ""
    let foregroundAfter = NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
    var result: [String: Any] = [
        "ok": true, "found": true, "performed": shouldPerform, "verified": shouldPerform && after == expected,
        "role": stringAttribute(editable, kAXRoleAttribute as CFString).replacingOccurrences(of: "AX", with: "", options: .anchored),
        "name": stringAttribute(editable, kAXTitleAttribute as CFString), "before": before, "after": after,
        "foregroundAffected": foregroundBefore != foregroundAfter,
        "diagnostics": [
            "method": "AXValue coordinate write", "point": ["x": x, "y": y],
            "ancestorDepth": ancestorDepth, "treeScanUsed": false,
            "foregroundBefore": Int(foregroundBefore), "foregroundAfter": Int(foregroundAfter),
            "backgroundSafe": foregroundBefore == foregroundAfter,
        ],
    ]
    if let bounds = elementBounds(editable) { result["bounds"] = bounds }
    return result
}

func pidEventAt(_ payload: [String: Any]) throws -> [String: Any] {
    guard let target = payload["target"] as? [String: Any], let pointPayload = payload["point"] as? [String: Any]
    else { try fail("invalid_arguments", "target and point are required.") }
    let pid = pid_t(try number(target["pid"], "target.pid"))
    let x = try number(pointPayload["x"], "point.x")
    let y = try number(pointPayload["y"], "point.y")
    if let bounds = target["bounds"] as? [String: Any] {
        let left = try number(bounds["x"], "target.bounds.x")
        let top = try number(bounds["y"], "target.bounds.y")
        let width = try number(bounds["width"], "target.bounds.width")
        let height = try number(bounds["height"], "target.bounds.height")
        guard x >= left, y >= top, x < left + width, y < top + height else {
            try fail("invalid_arguments", "The coordinate is outside the connected window bounds.")
        }
    }
    let operation = String(describing: payload["operation"] ?? "pid_click_at")
    let text = String(describing: payload["text"] ?? "")
    let shouldPerform = payload["perform"] as? Bool == true
    let rawWindowID = String(describing: target["windowId"] ?? "")
    guard let windowNumber = Int64(rawWindowID), windowNumber > 0 else {
        try fail(
            "unsupported_background_interaction",
            "The target window does not expose a numeric WindowServer id required for routed background mouse events."
        )
    }
    let foregroundBefore = NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
    let cursorBefore = CGEvent(source: nil)?.location ?? .zero
    if shouldPerform {
        let source = CGEventSource(stateID: .combinedSessionState)
        let point = CGPoint(x: x, y: y)
        guard let moved = CGEvent(mouseEventSource: source, mouseType: .mouseMoved, mouseCursorPosition: point, mouseButton: .left),
              let down = CGEvent(mouseEventSource: source, mouseType: .leftMouseDown, mouseCursorPosition: point, mouseButton: .left),
              let up = CGEvent(mouseEventSource: source, mouseType: .leftMouseUp, mouseCursorPosition: point, mouseButton: .left)
        else { try fail("provider_error", "Could not create targeted mouse events.") }
        // CGEventPostToPid bypasses the WindowServer's normal coordinate-to-window
        // routing.  Without these fields AppKit/Chromium receives a process-level
        // mouse event whose NSEvent has no usable window number, so web contents and
        // streamed surfaces commonly ignore it.  Bind every event in the click to
        // the connected WindowServer window while retaining the global point.
        let mouseEvents = [moved, down, up]
        for event in mouseEvents {
            event.setIntegerValueField(.mouseEventWindowUnderMousePointer, value: windowNumber)
            event.setIntegerValueField(.mouseEventWindowUnderMousePointerThatCanHandleThisEvent, value: windowNumber)
        }
        down.setIntegerValueField(.mouseEventClickState, value: 1)
        up.setIntegerValueField(.mouseEventClickState, value: 1)
        moved.postToPid(pid)
        Thread.sleep(forTimeInterval: 0.01)
        down.postToPid(pid)
        Thread.sleep(forTimeInterval: 0.025)
        up.postToPid(pid)
        if operation == "pid_type_at" {
            Thread.sleep(forTimeInterval: 0.08)
            let units = Array(text.utf16)
            units.withUnsafeBufferPointer { buffer in
                if let keyDown = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: true) {
                    keyDown.keyboardSetUnicodeString(stringLength: units.count, unicodeString: buffer.baseAddress!)
                    keyDown.postToPid(pid)
                }
                if let keyUp = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: false) {
                    keyUp.keyboardSetUnicodeString(stringLength: units.count, unicodeString: buffer.baseAddress!)
                    keyUp.postToPid(pid)
                }
            }
        }
        Thread.sleep(forTimeInterval: 0.08)
    }
    let cursorAfter = CGEvent(source: nil)?.location ?? .zero
    let foregroundAfter = NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
    let cursorMoved = abs(cursorBefore.x - cursorAfter.x) > 0.5 || abs(cursorBefore.y - cursorAfter.y) > 0.5
    return [
        "ok": true, "found": true, "performed": shouldPerform, "verified": shouldPerform && !cursorMoved && foregroundBefore == foregroundAfter,
        "foregroundAffected": foregroundBefore != foregroundAfter, "realCursorMoved": cursorMoved,
        "action": operation == "pid_type_at" ? "type" : "press", "nativeAction": "CGEventPostToPid",
        "diagnostics": [
            "method": "CGEventPostToPid", "pid": Int(pid), "point": ["x": x, "y": y],
            "windowNumber": windowNumber, "windowRoutingFieldsSet": true,
            "eventKind": operation == "pid_type_at" ? "targeted_click_and_unicode" : "targeted_click",
            "foregroundBefore": Int(foregroundBefore), "foregroundAfter": Int(foregroundAfter),
            "cursorBefore": ["x": cursorBefore.x, "y": cursorBefore.y], "cursorAfter": ["x": cursorAfter.x, "y": cursorAfter.y],
            "backgroundSafe": !cursorMoved && foregroundBefore == foregroundAfter, "treeScanUsed": false,
        ],
    ]
}

func run(_ payload: [String: Any]) throws -> [String: Any] {
    guard let target = payload["target"] as? [String: Any],
          let pointPayload = payload["point"] as? [String: Any]
    else { try fail("invalid_arguments", "target and point are required.") }
    let pidValue = try number(target["pid"], "target.pid")
    let pid = pid_t(pidValue)
    let x = try number(pointPayload["x"], "point.x")
    let y = try number(pointPayload["y"], "point.y")
    if let bounds = target["bounds"] as? [String: Any] {
        let left = try number(bounds["x"], "target.bounds.x")
        let top = try number(bounds["y"], "target.bounds.y")
        let width = try number(bounds["width"], "target.bounds.width")
        let height = try number(bounds["height"], "target.bounds.height")
        guard width > 0, height > 0, x >= left, y >= top, x < left + width, y < top + height else {
            try fail("invalid_arguments", "The coordinate is outside the connected window bounds.")
        }
    }
    let preferred = (payload["preferredActions"] as? [Any] ?? [])
        .map { String(describing: $0).lowercased() }
        .filter { actionMappings[$0] != nil }
    guard !preferred.isEmpty else {
        try fail("invalid_arguments", "preferredActions must contain press, select, or toggle.")
    }
    guard AXIsProcessTrusted() else {
        try fail("permission_required", "Accessibility permission is required for background coordinate activation.")
    }

    let application = AXUIElementCreateApplication(pid)
    _ = AXUIElementSetMessagingTimeout(application, 0.75)
    var hitElement: AXUIElement?
    let hitError = AXUIElementCopyElementAtPosition(application, Float(x), Float(y), &hitElement)
    guard hitError == .success, let initialElement = hitElement else {
        if hitError == .noValue || hitError == .invalidUIElement {
            return [
                "ok": true,
                "found": false,
                "diagnostics": [
                    "method": "AXUIElementCopyElementAtPosition", "pid": Int(pid),
                    "point": ["x": x, "y": y], "axError": hitError.rawValue,
                ],
            ]
        }
        try fail(errorType(hitError), "Coordinate accessibility hit-test failed with AX error \(hitError.rawValue).")
    }

    var current = initialElement
    var matched: (String, String)?
    var ancestorDepth = 0
    for depth in 0...12 {
        ancestorDepth = depth
        if let action = supportedAction(current, preferred: preferred) {
            matched = action
            break
        }
        var parentValue: CFTypeRef?
        guard AXUIElementCopyAttributeValue(current, kAXParentAttribute as CFString, &parentValue) == .success,
              let parentValue,
              CFGetTypeID(parentValue) == AXUIElementGetTypeID()
        else { break }
        current = unsafeBitCast(parentValue, to: AXUIElement.self)
    }

    guard let (semanticAction, nativeAction) = matched else {
        return [
            "ok": true,
            "found": false,
            "diagnostics": [
                "method": "AXUIElementCopyElementAtPosition", "pid": Int(pid),
                "point": ["x": x, "y": y], "reason": "no_supported_action",
                "ancestorsChecked": ancestorDepth + 1, "treeScanUsed": false,
            ],
        ]
    }

    let shouldPerform = payload["perform"] as? Bool == true
    if shouldPerform {
        let actionError = AXUIElementPerformAction(current, nativeAction as CFString)
        guard actionError == .success else {
            if actionError == .actionUnsupported || actionError == .invalidUIElement {
                return [
                    "ok": true, "found": false, "performed": false,
                    "diagnostics": [
                        "method": "AXUIElementPerformAction", "pid": Int(pid),
                        "point": ["x": x, "y": y], "axError": actionError.rawValue,
                        "reason": "element_changed_or_action_unsupported",
                    ],
                ]
            }
            try fail(errorType(actionError), "Background accessibility action failed with AX error \(actionError.rawValue).")
        }
    }

    var result: [String: Any] = [
        "ok": true,
        "found": true,
        "performed": shouldPerform,
        "verified": shouldPerform,
        "action": semanticAction,
        "nativeAction": nativeAction,
        "role": stringAttribute(current, kAXRoleAttribute as CFString).replacingOccurrences(of: "AX", with: "", options: .anchored),
        "name": stringAttribute(current, kAXTitleAttribute as CFString),
        "diagnostics": [
            "method": shouldPerform ? "AXUIElementPerformAction" : "AXUIElementCopyElementAtPosition",
            "pid": Int(pid), "point": ["x": x, "y": y], "backgroundSafe": true,
            "treeScanUsed": false, "ancestorDepth": ancestorDepth,
        ],
        "nextValidActions": ["call:wait", "call:snapshot", "disconnect"],
    ]
    if let bounds = elementBounds(current) { result["bounds"] = bounds }
    return result
}

do {
    guard CommandLine.arguments.count == 2,
          let data = CommandLine.arguments[1].data(using: .utf8),
          let payload = try JSONSerialization.jsonObject(with: data) as? [String: Any]
    else { try fail("invalid_arguments", "Expected one JSON payload argument.") }
    let operation = String(describing: payload["operation"] ?? "hit_test")
    if operation == "snapshot" {
        jsonOutput(try nativeSnapshot(payload, inspectOnly: false))
    } else if operation == "inspect" {
        jsonOutput(try nativeSnapshot(payload, inspectOnly: true))
    } else if operation == "perform" {
        jsonOutput(try nativePerform(payload))
    } else if operation == "menu_command" {
        jsonOutput(try menuCommand(payload))
    } else if operation == "enable_accessibility" {
        jsonOutput(try enableAccessibility(payload))
    } else if operation == "type_at" {
        jsonOutput(try typeAt(payload))
    } else if operation == "pid_click_at" || operation == "pid_type_at" {
        jsonOutput(try pidEventAt(payload))
    } else {
        jsonOutput(try run(payload))
    }
} catch HelperFailure.typed(let type, let message) {
    jsonOutput(["ok": false, "errorType": type, "error": message])
} catch {
    jsonOutput(["ok": false, "errorType": "provider_error", "error": String(describing: error)])
}
