// Presentation only: keep the original transcript and Agent provenance intact.
export function presentConversationMessages(messages) {
  return messages.flatMap(message => {
    if (message.role !== "user" || !message.agentOriginated) return [message];
    const content = String(message.content || "");
    if (message.agentMessageKind === "task_notification") return [];
    const taskHeader = /^\[Task input\]\r?\nThis message and its files are execution inputs, not a direct human upload or additional human authorization\.\r?\n\r?\n/;
    const body = content.replace(taskHeader, "");
    return [body === content ? message : { ...message, content: body, assistantTaskMessage: true }];
  });
}
