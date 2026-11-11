import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"
import vm from "node:vm"

const source = readFileSync(new URL("./pane-detachment.jsx", import.meta.url), "utf8")
  .replace(/^import .*$/gm, "").replace(/export \{[\s\S]*?\}/, "")
const sandbox = {
  wbcPaneCard: (kind, payload, options) => ({ kind, payload, ...options }),
  wbcPaneCardLocation: (layout, id) => layout.left.concat(layout.right).find(card => card.id === id),
}
vm.createContext(sandbox)
vm.runInContext(source, sandbox)

function workspace(projectId) {
  let layout = { left: [], right: [], leftRatio: 0.5, rightRatio: 0.5 }
  const selected = []
  return {
    projectId, selected,
    updatePaneLayout(update) { layout = update(layout) },
    selectChat: id => selected.push(id),
    cards: () => [...layout.left, ...layout.right],
  }
}

function returned(projectId, chatId = "task") {
  return {
    cardId: "chat:" + chatId, layoutOwnerChatId: chatId, sourceSide: "right",
    descriptor: { kind: "chat", payload: chatId, ownerChatId: chatId, project: { id: projectId } },
  }
}

test("broadcast returns restore only into the originating workspace", () => {
  for (const origin of ["__personal_assistant__", "project-a", "project-b"]) {
    const pages = ["__personal_assistant__", "project-a", "project-b"].map(workspace)
    const info = returned(origin)
    for (const page of pages) sandbox.wbcRestoreReturnedDetachedPane(page, info)
    for (const page of pages) {
      assert.equal(page.cards().length, page.projectId === origin ? 1 : 0)
      assert.deepEqual(page.selected, page.projectId === origin ? ["task"] : [])
    }
  }
})

test("legacy assistant windows cannot return into Work, even with a leaked project", () => {
  for (const projectId of ["", "project-a", "__personal_assistant__"]) {
    const info = returned(projectId, "assistant_global")
    const work = workspace("project-a")
    const assistant = workspace("__personal_assistant__")
    sandbox.wbcRestoreReturnedDetachedPane(work, info)
    sandbox.wbcRestoreReturnedDetachedPane(assistant, info)
    assert.equal(work.cards().length, 0)
    assert.deepEqual(work.selected, [])
    assert.equal(assistant.cards()[0].payload, "assistant_global")
  }
})

test("unknown return scopes do not insert panes or change the selected conversation", () => {
  const work = workspace("project-a")
  sandbox.wbcRestoreReturnedDetachedPane(work, returned(""))
  assert.equal(work.cards().length, 0)
  assert.deepEqual(work.selected, [])
})
