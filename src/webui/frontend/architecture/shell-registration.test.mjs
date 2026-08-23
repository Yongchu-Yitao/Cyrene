import assert from "node:assert/strict"
import fs from "node:fs"
import path from "node:path"
import test from "node:test"
import { fileURLToPath } from "node:url"

const FRONTEND_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")

test("the shell registers WorkbenchApp only in the module that owns it", () => {
  const shell = fs.readFileSync(path.join(FRONTEND_ROOT, "workbench.jsx"), "utf8")
  const support = fs.readFileSync(
    path.join(FRONTEND_ROOT, "features", "shell", "support.jsx"),
    "utf8",
  )

  assert.match(shell, /function WorkbenchApp\s*\(/)
  assert.match(shell, /import \{ WbColResizer, wbApplyStoredRightWidth \}/)
  assert.match(shell, /window\.CyreneUI\.register\("shell", \{\s*App: WorkbenchApp,\s*ColResizer: WbColResizer/s)
  assert.doesNotMatch(support, /\bWorkbenchApp\b/)
  assert.doesNotMatch(support, /\bWbColResizer\b/)
  assert.match(support, /import \{ wbSetBrowserOverlayObscured \} from "\.\.\/\.\.\/shared\/browser\/overlays\.jsx"/)
})

test("extracted shell and pane modules import or project their runtime dependencies", () => {
  const topbar = fs.readFileSync(
    path.join(FRONTEND_ROOT, "features", "shell", "topbar.jsx"),
    "utf8",
  )
  const paneWorkspace = fs.readFileSync(
    path.join(FRONTEND_ROOT, "features", "chat", "pane-workspace.jsx"),
    "utf8",
  )
  const chatPage = fs.readFileSync(
    path.join(FRONTEND_ROOT, "features", "chat", "page.jsx"),
    "utf8",
  )
  const resourceSplits = fs.readFileSync(
    path.join(FRONTEND_ROOT, "features", "chat", "resource-splits.jsx"),
    "utf8",
  )
  const splitPane = fs.readFileSync(
    path.join(FRONTEND_ROOT, "features", "chat", "split-pane.jsx"),
    "utf8",
  )
  const conversation = fs.readFileSync(
    path.join(FRONTEND_ROOT, "features", "chat", "conversation.jsx"),
    "utf8",
  )

  assert.match(topbar, /import \{ WorkbenchHelpCenter \} from "\.\/support\.jsx"/)
  assert.match(paneWorkspace, /singlePaneDropUsesContextTracks: singlePaneDropUsesContextTracks/)
  assert.match(paneWorkspace, /paneDropSessionActive: paneDropSessionActive/)
  assert.match(chatPage, /var singlePaneDropUsesContextTracks = panePresentation\.singlePaneDropUsesContextTracks/)
  assert.match(chatPage, /var paneDropSessionActive = panePresentation\.paneDropSessionActive/)
  assert.match(resourceSplits, /export \{[^}]*\bwbcChatDeliveredArtifacts\b/)
  assert.match(splitPane, /import \{[^}]*\bwbcChatDeliveredArtifacts\b[^}]*\} from "\.\/resource-splits\.jsx"/)
  assert.match(resourceSplits, /export \{[^}]*\bwbcViewerFileFromItems\b/)
  assert.match(splitPane, /import \{[^}]*\bwbcViewerFileFromItems\b[^}]*\} from "\.\/resource-splits\.jsx"/)
  assert.match(conversation, /durableMessages: durableMessages/)
  assert.match(conversation, /messagesRevision=\{projection\.durableMessages\}/)
})
