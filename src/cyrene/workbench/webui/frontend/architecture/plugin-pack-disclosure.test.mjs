import assert from "node:assert/strict"
import fs from "node:fs"
import path from "node:path"
import test from "node:test"
import { fileURLToPath } from "node:url"

const FRONTEND_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")

test("plugin packs are collapsed by default and expose an accessible disclosure", () => {
  const source = fs.readFileSync(
    path.join(FRONTEND_ROOT, "features", "settings", "custom-plugins.jsx"),
    "utf8",
  )

  assert.match(source, /var \[expanded, setExpanded\] = useStateSt\(false\)/)
  assert.match(source, /"aria-expanded": expanded \? "true" : "false"/)
  assert.match(source, /"aria-controls": membersId/)
  assert.match(source, /className: "wb-extension-card wb-registry-pack-card", onClick: toggleFromCard/)
  assert.match(source, /onClick: toggleFromDisclosure/)
  assert.match(source, /className: "wb-registry-pack-members", id: membersId, hidden: !expanded/)
})

test("card clicks toggle the package without hijacking nested controls", () => {
  const source = fs.readFileSync(
    path.join(FRONTEND_ROOT, "features", "settings", "custom-plugins.jsx"),
    "utf8",
  )

  assert.match(source, /function toggleFromCard\(event\)/)
  assert.match(source, /event\.target\.closest/)
  assert.match(source, /"button", "a", "input", "select", "textarea", "label", "form"/)
  assert.match(source, /if \(interactive\) return/)
  assert.match(source, /setExpanded\(function \(value\) \{ return !value \}\)/)
})

test("the package toggle precedes the disclosure button and hidden members do not render", () => {
  const component = fs.readFileSync(
    path.join(FRONTEND_ROOT, "features", "settings", "custom-plugins.jsx"),
    "utf8",
  )
  const styles = fs.readFileSync(
    path.join(FRONTEND_ROOT, "features", "settings", "extensions.css"),
    "utf8",
  )

  const toggleIndex = component.indexOf('Toggle(configuredEnabled(pack)')
  const disclosureIndex = component.indexOf('className: "wb-registry-pack-disclosure"', toggleIndex)
  assert.ok(toggleIndex >= 0 && disclosureIndex > toggleIndex)
  assert.match(styles, /\.wb-registry-pack-members\[hidden\] \{\s*display: none;\s*\}/)
})

test("extension cards use the theme secondary surface instead of white", () => {
  const source = fs.readFileSync(
    path.join(FRONTEND_ROOT, "features", "settings", "extensions.css"),
    "utf8",
  )

  assert.match(source, /\.wb-extension-card \{\s*background: var\(--wb-control-bg\);\s*\}/)
})

test("plugin tool menus escape the package card and stack above following cards", () => {
  const source = fs.readFileSync(
    path.join(FRONTEND_ROOT, "features", "settings", "extensions.css"),
    "utf8",
  )

  assert.match(source, /\.wb-registry-pack-card \{[\s\S]*?position: relative;[\s\S]*?z-index: 0;[\s\S]*?overflow: visible;/)
  assert.match(source, /\.wb-registry-pack-card:has\(\.wb-tool-menu\) \{\s*z-index: 1;\s*\}/)
})
