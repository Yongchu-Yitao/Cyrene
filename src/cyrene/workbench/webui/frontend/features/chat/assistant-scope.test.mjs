import test from "node:test";
import assert from "node:assert/strict";
import { acceptsAssistantScope } from "./assistant-scope.mjs";
test("managed events never leak into ordinary chat lists", () => {
  for (const chat of [{id: "assistant_123"}, {id: "managed_123"}, {id: "x", kind: "assistant-task"}]) {
    assert.equal(acceptsAssistantScope(chat, false), false);
    assert.equal(acceptsAssistantScope(chat, true), true);
  }
  assert.equal(acceptsAssistantScope({id: "wbchat_123", kind: "chat"}, true), false);
  assert.equal(acceptsAssistantScope({id: "wbchat_123"}, false), true);
});
