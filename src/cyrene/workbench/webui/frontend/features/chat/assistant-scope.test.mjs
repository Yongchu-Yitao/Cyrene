import test from "node:test";
import assert from "node:assert/strict";
import { acceptsAssistantScope } from "./assistant-scope.mjs";
test("managed events never leak into ordinary chat lists", () => {
  for (const chat of [{id: "assistant_global", kind: "personal-assistant"}, {id: "managed_123", kind: "assistant-task"}, {id: "x", kind: "assistant-task"}]) {
    assert.equal(acceptsAssistantScope(chat, false), false);
    assert.equal(acceptsAssistantScope(chat, true), true);
  }
  assert.equal(acceptsAssistantScope({id: "wbchat_123", kind: "chat"}, true), false);
  assert.equal(acceptsAssistantScope({id: "wbchat_123"}, false), true);
});

test("conversation scope follows its type, not its ID prefix", () => {
  for (const id of ["assistant_123", "managed_123"]) {
    assert.equal(acceptsAssistantScope({id, kind: "chat"}, false), true);
    assert.equal(acceptsAssistantScope({id, kind: "chat"}, true), false);
  }
});
