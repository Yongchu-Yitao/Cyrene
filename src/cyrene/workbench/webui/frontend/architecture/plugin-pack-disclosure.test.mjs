import assert from "node:assert/strict"
import fs from "node:fs"
import test from "node:test"

const read = name => fs.readFileSync(new URL("../features/settings/" + name, import.meta.url), "utf8")
const tile = read("custom-plugins.jsx").split("function RegistryTile(")[1].split("function PluginPackCard(")[0]
const disclosure = read("disclosure.jsx")
const style = read("layout.css")

test("plugin cards start collapsed and connect the disclosure button with its content", () => {
  assert.match(tile, /\[open,\s*setOpen\]\s*=\s*useStateSt\(false\)/)
  assert.match(tile, /aria-expanded=\{open\} aria-controls=\{detailsId\}/)
  assert.match(tile, /<Disclosure id=\{detailsId\} open=\{open\}>/)
  assert.match(disclosure, /aria-hidden=\{!open\} inert=/)
})

test("card clicks toggle the package without hijacking nested controls", () => {
  assert.match(tile, /onClick=\{toggleFromCard\}/)
  assert.match(tile, /event\.target\.closest/)
  assert.match(tile, /"button", "a", "input", "select", "textarea", "label", "form"/)
  assert.match(tile, /if \(interactive\) return/)
  assert.match(tile, /setOpen\(value => !value\)/)
})

test("the always-visible enable switch precedes the disclosure control", () => {
  const toggleIndex = tile.indexOf("Toggle(enabled,")
  const disclosureIndex = tile.indexOf('className="tile-disclosure"')
  const detailsIndex = tile.indexOf("<Disclosure id=")
  assert.ok(toggleIndex >= 0 && toggleIndex < disclosureIndex && disclosureIndex < detailsIndex)
})

test("extension cards use the theme surface and expanded menus may extend outside the card", () => {
  assert.match(style, /\.registry-tile \{[^}]*background: var\(--wb-card-bg\)/)
  assert.match(style, /\.registry-tile:has\(\.wb-tool-menu\) \{\s*z-index: 1;/)
  assert.match(style, /\[data-settled=true\] > \.motion-clip \{\s*overflow: visible;/)
})
