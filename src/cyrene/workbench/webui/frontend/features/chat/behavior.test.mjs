import assert from "node:assert/strict"
import test from "node:test"

import {
  contextBlockColorIndex,
  moveChatOrderBlock,
  normalizePermissionMode,
  permissionOptionLabel,
  resolveRefreshedChatSelection,
  settleChatListItem,
  toolPresentationKind,
} from "./behavior.mjs"


test("visible system-prefix blocks receive distinct categorical colors", () => {
  const blocks = [
    { id: "system.identity", type: "identity" },
    { id: "system.behavior", type: "instructions" },
    { id: "system.tools", type: "tools" },
    { id: "context.persona", type: "system" },
    { id: "context.memory", type: "memory" },
    { id: "context.learned_skills", type: "system" },
    { id: "system.message_overhead", type: "overhead" },
  ]
  const colors = blocks.map(contextBlockColorIndex)
  assert.equal(new Set(colors).size, blocks.length)
  assert.notEqual(
    contextBlockColorIndex({ id: "context.persona", type: "system" }),
    contextBlockColorIndex({ id: "context.learned_skills", type: "system" }),
  )
})


test("permission labels follow protocol semantics instead of display language", () => {
  const labels = {
    "workbenchChat.permissionOnce": "允许一次",
    "workbenchChat.permissionAlways": "始终允许",
    "workbenchChat.permissionSession": "本次会话允许",
    "workbenchChat.reject": "拒绝",
  }
  const translate = (key, fallback) => labels[key] || fallback
  assert.deepEqual([
    permissionOptionLabel({ optionId: "allow_once", label: "Allow once" }, 0, 3, translate),
    permissionOptionLabel({ optionId: "allow_always", label: "Always allow" }, 1, 3, translate),
    permissionOptionLabel({ optionId: "reject", label: "Reject" }, 2, 3, translate),
  ], ["允许一次", "始终允许", "拒绝"])
})

test("permission mode normalization is bounded to declared modes", () => {
  const modes = ["default", "auto", "read_only"]
  assert.equal(normalizePermissionMode(" AUTO ", "default", modes), "auto")
  assert.equal(normalizePermissionMode("unknown", "read_only", modes), "read_only")
  assert.equal(normalizePermissionMode("unknown", "also_unknown", modes), "default")
})

test("moving a grouped chat block preserves internal and surrounding order", () => {
  assert.deepEqual(
    moveChatOrderBlock(["a", "b", "c", "d", "e"], ["b", "c"], ["e"], "after"),
    ["a", "d", "e", "b", "c"],
  )
  assert.deepEqual(
    moveChatOrderBlock(["a", "b", "c"], ["c"], ["a"], "before"),
    ["c", "a", "b"],
  )
})

test("tool presentation kinds reject unknown renderer instructions", () => {
  assert.equal(toolPresentationKind({ presentation: { kind: "diff" } }), "diff")
  assert.equal(toolPresentationKind({ presentation: { kind: "script" } }), "generic")
  assert.equal(toolPresentationKind(null), "generic")
})

test("a stale chat refresh never overwrites a newer selection", () => {
  const chats = [{ id: "old" }, { id: "other" }]
  assert.equal(resolveRefreshedChatSelection(chats, "", "old", "new"), null)
  assert.equal(resolveRefreshedChatSelection(chats, "", "old", "old"), null)
  assert.equal(resolveRefreshedChatSelection([{ id: "other" }], "", "old", "old"), "other")
  assert.equal(resolveRefreshedChatSelection(chats, "other", "old", "new"), "other")
  assert.equal(resolveRefreshedChatSelection(chats, "missing", "old", "new"), "old")
})

test("terminal events synchronously settle the chat-list summary", () => {
  assert.deepEqual(settleChatListItem({
    id: "chat_1",
    status: "running",
    runStatus: "running",
    agentBusy: { type: "reply" },
    lastRun: { id: "old", status: "running" },
  }, "completed", {
    runId: "run_1",
    timestamp: "2026-08-18T12:17:30Z",
  }), {
    id: "chat_1",
    status: "idle",
    runStatus: "completed",
    agentBusy: null,
    lastRun: {
      id: "run_1",
      status: "done",
      completedAt: "2026-08-18T12:17:30Z",
    },
  })
})
