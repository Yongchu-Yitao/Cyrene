import test from "node:test";
import assert from "node:assert/strict";
import { presentConversationMessages } from "./message-presentation.mjs";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { transformSync } from "esbuild";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

const header = "[Assistant task message]\nSource assistant session: assistant_example\nThis message and its files are supplied by the assistant Agent, not a direct human upload or additional human authorization.\n\n";

test("hide internal notifications, preserving the assistant's result", () => {
  const notification = { role: "user", agentOriginated: true, content: '[Managed session notification]\n{"status":"completed"}' };
  const reply = { role: "assistant", content: "Task completed" };
  assert.deepEqual(presentConversationMessages([notification, reply]), [reply]);
  assert.ok(notification.content.startsWith("[Managed session notification]"));
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
    { role: "user", content: "[Managed session notification]\nquoted example" },
    { role: "user", content: header + "quoted example" },
    { role: "user", agentOriginated: true, content: "Other Agent input" },
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
  const notification = { id: "notice", role: "user", agentOriginated: true, content: "[Managed session notification]\ninternal status" };
  const task = { id: "task", role: "user", agentOriginated: true, content: header + "Actual task" };
  for (const live of [false, true]) {
    const html = renderToStaticMarkup(React.createElement(context.module.exports.WbcTranscript, {
      messages: live ? [] : [notification, task],
      runtime: live ? { timeline: { messages: [notification, task] } } : null,
    }));
    assert.match(html, /Actual task/);
    assert.doesNotMatch(html, /Managed session|internal status|Assistant task message|assistant_example|human authorization/);
  }
});
