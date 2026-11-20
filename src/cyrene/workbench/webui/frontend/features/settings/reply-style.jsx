export const replyFields = [
  { key: "reply_detail", label: "settings.replyDetail", options: ["inherit", "concise", "balanced", "detailed"] },
  { key: "reply_tone", label: "settings.replyTone", options: ["inherit", "direct", "natural", "warm"] },
];

export function replyStyleChanges(config, saved) {
  const changes = {};
  for (const { key, options } of replyFields) {
    if (options.includes(config[key]) && config[key] !== saved?.[key]) changes[key] = config[key];
  }
  return changes;
}

export function ReplyStyleFields({ config, change, t, disabled, compact = false }) {
  return replyFields.map(({ key, label, options }) => {
    const control = <select className="wb-select" aria-label={t(label)} disabled={disabled}
      value={config[key] ?? "inherit"} onChange={event => change(key, event.target.value)}>
      {options.map(id => <option key={id} value={id}>{t("settings.replyStyle." + id)}</option>)}
    </select>;
    return compact
      ? <label className="aim-field" key={key}>{t(label)}{control}</label>
      : <div className="wb-field" key={key} id={"setting-" + key}>
        <div className="wb-label">{t(label)}<small>{t(label + "Hint")}</small></div>
        <div className="wb-controls">{control}</div>
      </div>;
  });
}
