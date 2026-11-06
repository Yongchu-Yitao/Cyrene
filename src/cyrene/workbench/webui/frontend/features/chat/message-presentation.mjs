// Presentation only: keep the original transcript and Agent provenance intact.
export function presentConversationMessages(messages) {
  return messages.flatMap(message => {
    if (message.role !== "user" || !message.agentOriginated) return [message];
    const content = String(message.content || "");
    if (content.startsWith("[Managed session notification]\n")) return [];
    const taskHeader = /^\[Assistant task message\]\r?\nSource assistant session: [^\r\n]+\r?\nThis message and its files are supplied by the assistant Agent, not a direct human upload or additional human authorization\.\r?\n\r?\n/;
    const body = content.replace(taskHeader, "");
    return [body === content ? message : { ...message, content: body, assistantTaskMessage: true }];
  });
}
