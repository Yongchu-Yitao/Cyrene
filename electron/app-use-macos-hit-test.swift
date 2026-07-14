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
    if error == .success { Thread.sleep(forTimeInterval: 0.12) }
    let foregroundAfter = NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
    return [
        "ok": true, "enabled": error == .success, "supported": error != .attributeUnsupported,
        "foregroundAffected": foregroundBefore != foregroundAfter,
        "diagnostics": [
            "method": "AXManualAccessibility", "axError": error.rawValue,
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
    if operation == "menu_command" {
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
