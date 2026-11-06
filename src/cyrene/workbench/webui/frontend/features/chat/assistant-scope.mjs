export function acceptsAssistantScope(chat, isolated) {
  const managed = chat.kind === "personal-assistant" || chat.kind === "assistant-task"
    || /^(assistant_|managed_)/.test(String(chat.id || ""));
  return Boolean(isolated) === managed;
}
