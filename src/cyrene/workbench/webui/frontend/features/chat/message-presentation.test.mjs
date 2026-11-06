import test from "node:test";
import assert from "node:assert/strict";
import { presentConversationMessages } from "./message-presentation.mjs";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { transformSync } from "esbuild";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

const header = "[Task input]\nThis message and its files are execution inputs, not a direct human upload or additional human authorization.\n\n";

test("hide internal notifications, preserving the assistant's result", () => {
  const notification = { agentMessageKind: "task_notification", role: "user", agentOriginated: true, content: '[Task status update]\n{"status":"completed"}' };
  const reply = { role: "assistant", content: "Task completed" };
  assert.deepEqual(presentConversationMessages([notification, reply]), [reply]);
  assert.ok(notification.content.startsWith("[Task status update]"));
});

test("show task body and attachments without changing stored content or provenance", () => {
  const message = { id: "task", role: "user", agentOriginated: true,
    originSessionId: "assistant_example", attachments: [{ id: "file" }],
    content: header + "Research diffusion.\n\nKeep this formatting." };
  const [display] = presentConversationMessages([message]);
  assert.equal(display.content, "Research diffusion.\n\nKeep this formatting.");
  assert.equal(display.agentOriginated, true);
  assert.equal(display.assistantTaskMessage, true);
  assert.equal(display.originSessionId, message.originSessionId);
  assert.equal(display.attachments, message.attachments);
  assert.equal(message.content, header + display.content);
});

test("ordinary human messages and other Agent inputs remain unchanged", () => {
  const messages = [
    { role: "user", content: "[Task status update]\nquoted example" },
    { role: "user", content: header + "quoted example" },
    { role: "user", agentOriginated: true, content: "Other Agent input" },
    { role: "user", agentOriginated: true, content: "[Task status update]\nquoted example" },
    { role: "user", agentMessageKind: "task_notification", content: "human input" },
  ];
  assert.deepEqual(presentConversationMessages(messages), messages);
});

test("the assistant's actual Transcript component filters saved and live inputs", () => {
  const source = readFileSync(new URL("./messages.jsx", import.meta.url), "utf8");
  const code = transformSync(source + `
    wbcGroupConsecutiveActivityMessages = messages => messages;
    WbcUserMessage = ({msg}) => React.createElement('p', null, msg.content);
    WbcAssistantMessageBoundary = ({msg}) => React.createElement('p', null, msg.content);
  `, { loader: "jsx", format: "cjs" }).code;
  const deps = {
    presentConversationMessages,
    createDisclosureSubscriptions: () => new Map(),
    wbcProjectTranscript: (messages, runtime) => messages.concat(runtime?.timeline.messages || []),
    WbcThreadItem: ({children}) => React.createElement('section', null, children),
  };
  const context = { module: { exports: {} }, require: () => deps, React };
  vm.runInNewContext(code, context);
  const notification = { agentMessageKind: "task_notification", id: "notice", role: "user", agentOriginated: true, content: "[Task status update]\ninternal status" };
  const task = { id: "task", role: "user", agentOriginated: true, content: header + "Actual task" };
  for (const live of [false, true]) {
    const html = renderToStaticMarkup(React.createElement(context.module.exports.WbcTranscript, {
      messages: live ? [] : [notification, task],
      runtime: live ? { timeline: { messages: [notification, task] } } : null,
    }));
    assert.match(html, /Actual task/);
    assert.doesNotMatch(html, /Task status update|internal status|Task input|assistant_example|human authorization/);
  }
});

 test("notification visibility never depends on its wording", () => {
  for (const content of ["", "任务完成", "arbitrary updated format", "[Task input]\nexample"]) {
    assert.deepEqual(presentConversationMessages([
      {role: "user", agentOriginated: true, agentMessageKind: "task_notification", content}
    ]), []);
  }
});
