import {
  SectionTitle,
  FieldRow,
  Toggle,
} from "./shared.jsx"

// ── Agents Panel ──
function AgentsPanel(p) {
  var { t, config, setConfig, configLoading, soulDraft, setSoulDraft, soulStatus, saveSoul, agentProactive, setAgentProactive, saveAgents } = p;

  return React.createElement("div", { className: "settings-panel" },
    SectionTitle(t("settings.agents"), t("settings.agentsSubtitle")),

    // SOUL.md
    React.createElement("div", { className: "wb-field wb-field-stack wb-field-soul", id: "setting-soul" },
      React.createElement("div", { className: "wb-label" }, t("settings.soulMd"), React.createElement("small", null, t("settings.soulMdHint"))),
      React.createElement("textarea", { className: "wb-textarea mono wb-textarea-soul", value: soulDraft, onChange: function (e) { setSoulDraft(e.target.value); } }),
      React.createElement("div", { className: "wb-inline-row wb-inline-row-start", style: { marginTop: 8 } },
        React.createElement("button", { className: "wb-btn primary", onClick: saveSoul }, t("settings.saveSoul")),
        React.createElement("span", { className: "wb-hint" }, soulStatus || (configLoading ? t("settings.pathLoading") : config.soul_path)),
      ),
    ),

    FieldRow(t("settings.spawnPolicy"), t("settings.spawnPolicyHint"),
      React.createElement("select", { className: "wb-select", value: config.spawn_policy || "conservative", onChange: function (e) { setConfig({ ...config, spawn_policy: e.target.value }); } },
        React.createElement("option", { value: "aggressive" }, t("settings.aggressive")),
        React.createElement("option", { value: "conservative" }, t("settings.conservative")),
        React.createElement("option", { value: "off" }, t("settings.off")),
      ),
      undefined, "setting-spawn-policy",
    ),
    FieldRow(t("settings.agentProactive"), t("settings.agentProactiveHint"), Toggle(agentProactive, function () { setAgentProactive(!agentProactive); }),
      undefined, "setting-agent-proactive"),
    FieldRow(t("settings.backgroundSkillLearning"), t("settings.backgroundSkillLearningHint"), Toggle(config.background_skill_learning !== false, function () {
      setConfig({ ...config, background_skill_learning: config.background_skill_learning === false });
    }), undefined, "setting-background-skill-learning"),
    FieldRow(t("settings.heartbeatInterval"), t("settings.heartbeatIntervalHint"),
      React.createElement("input", { className: "wb-input mono", type: "number", min: "60", step: "1", value: config.heartbeat_interval, onChange: function (e) { setConfig({ ...config, heartbeat_interval: e.target.value }); }, style: { maxWidth: 120 } }),
      undefined, "setting-heartbeat",
    ),
    React.createElement("div", { className: "wb-save-actions" },
      React.createElement("button", { className: "wb-btn primary", onClick: saveAgents }, t("settings.saveApply")),
    ),
  );
}

export { AgentsPanel };
