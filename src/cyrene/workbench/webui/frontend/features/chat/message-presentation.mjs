import { projectInlineCards } from "./assistant-card-model.mjs";
// Presentation only: keep the original transcript and Agent provenance intact.
export function presentConversationMessages(messages) {
  return projectInlineCards(messages.flatMap(message => {
    if (message.role !== "user") return [message];
    if (!message.agentOriginated) return [presentCardSubmission(message)];
    const content = String(message.content || "");
    if (["task_notification", "welcome_refresh"].includes(message.agentMessageKind)) return [];
    const taskHeader = /^\[Task input\]\r?\nThis message and its files are execution inputs, not a direct human upload or additional human authorization\.\r?\n\r?\n/;
    const body = content.replace(taskHeader, "");
    return [body === content ? message : { ...message, content: body, assistantTaskMessage: true }];
  }));
}

function submissionValue(value) {
  if (value === true) return '是';
  if (value === false) return '否';
  if (value == null || value === '') return '未填写';
  if (Array.isArray(value)) return value.map(submissionValue).join('、');
  if (typeof value === 'object') return Object.values(value).map(submissionValue).join(' · ');
  return String(value);
}

export function presentCardSubmission(message) {
  if (!/^card_[a-f0-9]{64}$/.test(message.clientRequestId || '')) return message;
  const content = String(message.content || '');
  const heading = /^我在卡片「(.+?)」中点击了「(.+?)」。/.exec(content);
  const start = content.indexOf('\n{');
  if (!heading || start < 0) return message;
  try {
    const data = JSON.parse(content.slice(start + 1));
    if (!/^card_[a-f0-9]{32}$/.test(data.card_id || '') || !['submit', 'confirm'].includes(data.kind)
      || !data.values || typeof data.values !== 'object' || Array.isArray(data.values)) return message;
    const rows = Object.entries(data.values).map(([key, raw], index) => {
      const display = data.display_values?.[key];
      let label = display?.label || (Object.keys(data.values).length === 1 ? '填写内容' : `填写项 ${index + 1}`);
      let value = display && !Array.isArray(display) && Object.hasOwn(display, 'value') ? display.value : raw;
      if (Array.isArray(display) && display.length) {
        label = '已选';
        value = display.map(item => item.label || item.name || Object.entries(item).filter(([key]) => !key.endsWith('_id')).map(([, value]) => submissionValue(value)).join(' · ') || '已选择');
      }
      return `${label}：${submissionValue(value).slice(0, 1200)}`;
    });
    return { ...message, content: `${heading[2]} · ${heading[1]}${rows.length ? '\n' + rows.join('\n') : ''}` };
  } catch (_) { return message; }
}
