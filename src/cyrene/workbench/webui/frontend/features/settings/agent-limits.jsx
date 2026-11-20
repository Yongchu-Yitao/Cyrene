// Bounds match the subagent plugin's execution and discussion settings.
export const executionLimits = [
  { key: "subagent_execution_max_tool_calls", label: "settings.executionSafetyToolCalls", min: 1, max: 5000 },
  { key: "subagent_execution_max_wall_seconds", label: "settings.executionSafetyWallTime", min: 30, max: 86400 },
];

export const discussionLimits = [
  { key: "subagent_discussion_max_rounds", label: "settings.discussionRounds", min: 1, max: 50 },
  { key: "subagent_discussion_max_messages_per_agent", label: "settings.discussionMessagesPerAgent", min: 1, max: 50 },
  { key: "subagent_discussion_max_total_messages", label: "settings.discussionTotalMessages", min: 1, max: 500 },
  { key: "subagent_discussion_max_message_chars", label: "settings.discussionMessageChars", min: 100, max: 20000 },
  { key: "subagent_discussion_max_wall_seconds", label: "settings.discussionWallTime", min: 30, max: 86400 },
  { key: "subagent_discussion_max_tool_calls", label: "settings.discussionToolCalls", min: 1, max: 1000 },
  { key: "subagent_discussion_no_new_info_rounds", label: "settings.discussionNoNewInfo", min: 1, max: 20 },
];

export function agentSettingsPayload(config, modules) {
  const payload = {};
  if (modules.includes("proactive") && typeof config.agent_proactive === "boolean") payload.agent_proactive = config.agent_proactive;
  if (modules.includes("proactive") && config.agent_proactive !== false && config.agent_proactive_daily_frequency != null) {
    payload.agent_proactive_daily_frequency = Number(config.agent_proactive_daily_frequency);
  }
  if (modules.includes("soul") && typeof config.soul_active === "boolean") {
    payload.soul_active = config.soul_active;
  }
  if (modules.includes("subagent")) {
    payload.spawn_policy = config.spawn_policy;
    for (const field of [...executionLimits, ...discussionLimits]) {
      payload[field.key] = Number(config[field.key]);
    }
  }
  if (modules.includes("skills")) payload.background_skill_learning = config.background_skill_learning !== false;
  return payload;
}
