export function acceptsAssistantScope(chat, isolated) {
  const managed = chat.kind === "personal-assistant" || chat.kind === "assistant-task";
  return Boolean(isolated) === managed;
}
