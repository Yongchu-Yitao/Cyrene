import { ReplyStyleFields } from "./reply-style.jsx"
import { executionLimits, discussionLimits } from "./agent-limits.jsx"
import { FieldRow, Toggle } from "./shared.jsx"

function AgentsPanel({ t, config, setConfig, configLoading, soulDraft, setSoulDraft,
  soulStatus, saveSoul, saveAgents, pluginModules = [] }) {
  const hasReplyStyle = pluginModules.includes("response_style");
  const hasProactive = pluginModules.includes("proactive");
  const hasSoul = pluginModules.includes("soul");
  const hasSoulSwitch = hasSoul && typeof config.soul_active === "boolean";
  const hasSubagent = pluginModules.includes("subagent");
  const hasSkills = pluginModules.includes("skills");
  function change(key, value) {
    setConfig(previous => ({ ...previous, [key]: value }));
  }

  function limitRows(fields) {
    return fields.map(({ key, label, min, max, step = 1, defaultValue = "" }) => {
      const hintId = key + "-hint";
      return FieldRow(<label htmlFor={key}>{t(label)}</label>,
        <span id={hintId}>{t(label + "Hint")}</span>,
        <input className="wb-input agent-limit" id={key} type="number" required
          aria-describedby={hintId} min={min} max={max} step={step} value={config[key] ?? defaultValue}
          onChange={event => change(key, event.target.value)} />,
        key, "setting-" + key);
    });
  }

  return <form className="settings-panel agent-behavior" onSubmit={event => {
    event.preventDefault();
    saveAgents();
  }}>
    {hasSoul && <section>
      <h2 className="settings-heading">{t("settings.soulMd")}</h2>
      {hasSoulSwitch && FieldRow(t("settings.soulDefault"), t("settings.soulDefaultHint"),
        Toggle(config.soul_active, () => change("soul_active", !config.soul_active),
          configLoading, t("settings.soulDefault")), undefined, "setting-soul-default")}
      <details className="agent-persona-editor" id="setting-soul">
        <summary className="wb-label">{t("settings.editSoul")}<small>{t("settings.soulMdHint")}</small></summary>
        <textarea className="wb-textarea mono wb-textarea-soul" aria-label={t("settings.editSoul")}
          value={soulDraft} onChange={event => setSoulDraft(event.target.value)} />
        <div className="wb-inline-row wb-inline-row-start">
          <button className="wb-btn primary" type="button" onClick={saveSoul}>{t("settings.saveSoul")}</button>
          <span className="wb-hint">{soulStatus || (configLoading ? t("settings.pathLoading") : config.soul_path)}</span>
        </div>
      </details>
    </section>}
    {hasReplyStyle && <section>
      <h2 className="settings-heading">{t("settings.replyStyle")}</h2>
      <ReplyStyleFields config={config} change={change} t={t} disabled={configLoading}/>
    </section>}
    {hasSubagent && <section>
      <h2 className="settings-heading">{t("settings.executionLimits")}</h2>
      {FieldRow(t("settings.spawnPolicy"), t("settings.spawnPolicyHint"),
        <select className="wb-select" aria-label={t("settings.spawnPolicy")} value={config.spawn_policy || "conservative"}
          onChange={event => change("spawn_policy", event.target.value)}>
          <option value="aggressive">{t("settings.aggressive")}</option>
          <option value="conservative">{t("settings.conservative")}</option>
          <option value="off">{t("settings.off")}</option>
        </select>, undefined, "setting-spawn-policy")}
      {limitRows(executionLimits)}
    </section>}
    {hasSubagent && <section>
      <h2 className="settings-heading">{t("settings.discussionLimits")}</h2>
      {limitRows(discussionLimits)}
    </section>}
    {(hasProactive || hasSkills) && <section>
      <h2 className="settings-heading">{t("settings.backgroundActivity")}</h2>
      {hasProactive && FieldRow(t("settings.proactiveWork"), t("settings.proactiveWorkHint"),
        Toggle(config.agent_proactive !== false, () => change("agent_proactive", config.agent_proactive === false),
          configLoading, t("settings.proactiveWork")), undefined, "setting-assistant-proactive")}
      {hasProactive && config.agent_proactive !== false && limitRows([{
        key: "agent_proactive_daily_frequency", label: "settings.proactiveFrequency",
        min: 0.09, max: 64, step: "any", defaultValue: 1,
      }])}
      {hasSkills && FieldRow(t("settings.backgroundSkillLearning"), t("settings.backgroundSkillLearningHint"),
        Toggle(config.background_skill_learning !== false,
          () => change("background_skill_learning", config.background_skill_learning === false),
          configLoading, t("settings.backgroundSkillLearning")), undefined, "setting-background-skill-learning")}
    </section>}
    {(hasReplyStyle || hasProactive || hasSoulSwitch || hasSubagent || hasSkills) && <div className="wb-save-actions">
      <button className="wb-btn primary" type="submit" disabled={configLoading || config.revision == null}>
        {t("settings.saveApply")}
      </button>
    </div>}
  </form>;
}

export default AgentsPanel;
