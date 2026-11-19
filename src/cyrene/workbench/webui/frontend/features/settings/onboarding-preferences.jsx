import { ActionIcon } from "./action-icon.jsx"
import { ChoiceCards } from "./choice-cards.jsx"
import { Disclosure } from "./disclosure.jsx"
import { GENERAL_TIMEZONE_OPTIONS } from "./general.jsx"
import { readSettingsResponse, settingsFetch, Toggle } from "./shared.jsx"

export function SetupDisclosure({ id, open, onToggle, label, children }) {
  return <>
    <button type="button" className="provider-extra-toggle" aria-expanded={open} aria-controls={id} onClick={onToggle}>
      <ActionIcon name="chevron-right"/>{label}
    </button>
    <Disclosure id={id} open={open}>{children}</Disclosure>
  </>;
}

export function savedSetupTimezone() {
  const value = localStorage.getItem("cyrene-timezone");
  return GENERAL_TIMEZONE_OPTIONS.includes(value) ? value : "Asia/Shanghai";
}

export function SetupPreferences({ t, appearance, run, onDesktopChange }) {
  const [zone, setZone] = React.useState(savedSetupTimezone);
  const [expanded, setExpanded] = React.useState(false);
  const [desktop, setDesktop] = React.useState(null);
  const [loadError, setLoadError] = React.useState("");
  const [attempt, setAttempt] = React.useState(0);
  const [notify, setNotify] = React.useState(() => localStorage.getItem("cyrene-desktop-notifications") === "1");
  const host = window.cyrene;
  const hasDesktop = !!(host?.getDesktopSettings && host?.updateDesktopSettings);
  const hasNotifications = typeof Notification !== "undefined";

  React.useEffect(() => {
    if (!hasDesktop) return;
    let cancelled = false;
    host.getDesktopSettings().then(value => {
      if (!cancelled) { setDesktop(value); onDesktopChange(value); }
    }).catch(error => { if (!cancelled) setLoadError(error.message); });
    return () => { cancelled = true; };
  }, [host, hasDesktop, attempt, onDesktopChange]);

  async function saveZone(timezone) {
    await settingsFetch("/api/settings/config", {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ timezone }),
    }).then(readSettingsResponse);
    localStorage.setItem("cyrene-timezone", timezone);
    setZone(timezone);
  }

  async function updateDesktop(changes) {
    const value = await host.updateDesktopSettings(changes);
    setDesktop(value);
    onDesktopChange(value);
    if (value.shortcutUpdateOk === false) throw new Error(t("settings.quickChatShortcutConflict"));
  }

  async function changeNotifications() {
    const enabled = !notify && (Notification.permission === "granted" || await Notification.requestPermission() === "granted");
    localStorage.setItem("cyrene-desktop-notifications", enabled ? "1" : "0");
    setNotify(enabled);
    if (!notify && !enabled) throw new Error(t("welcome.setup.notificationsDenied"));
  }

  function choices(label, value, options, change) {
    return <div className="ob-setting"><b>{t(label)}</b><ChoiceCards compact label={t(label)} value={value}
      options={options.map(id => ({ id, title: t("settings." + id) }))} onChange={next => run(() => change(next))}/></div>;
  }

  return <>
    <h1>{t("welcome.setup.prefs")}</h1><p className="ob-lead">{t("welcome.setup.prefsHint")}</p>
    {choices("settings.theme", appearance.theme, ["system", "light", "dark"], next => appearance.save(next, appearance.textSize))}
    {choices("settings.textSize", appearance.textSize, ["default", "large"], next => appearance.save(appearance.theme, next))}
    <label className="ob-setting"><b>{t("settings.timezone")}</b><select value={zone} onChange={e => run(() => saveZone(e.target.value))}>
      {GENERAL_TIMEZONE_OPTIONS.map(z => <option key={z}>{z}</option>)}
    </select></label>
    {(hasDesktop || hasNotifications) && <SetupDisclosure id="onboarding-desktop" open={expanded} onToggle={() => setExpanded(!expanded)} label={t("welcome.setup.desktop")}>
      {hasNotifications && <div className="ob-setting"><b>{t("settings.desktopNotifications")}</b>{Toggle(notify, () => run(changeNotifications), false, t("settings.desktopNotifications"))}</div>}
      {hasDesktop && <>
        {loadError && <button type="button" className="wb-btn" onClick={() => { setLoadError(""); setAttempt(attempt + 1); }}>{t("settings.retry")}</button>}
        <div className="ob-setting"><b>{t("settings.runInBackground")}</b>{Toggle(desktop?.runInBackground, () => run(() => updateDesktop({ runInBackground: !desktop.runInBackground })), !desktop, t("settings.runInBackground"))}</div>
        <div className="ob-setting"><div><b>{t("settings.quickChatAssistant")}</b><small>{t(desktop?.runInBackground ? "settings.quickChatAssistantHint" : "settings.quickChatAssistantNeedsResident")}</small></div>
          {Toggle(desktop?.quickChatEnabled, () => run(() => updateDesktop({ quickChatEnabled: !desktop.quickChatEnabled })), !desktop?.runInBackground, t("settings.quickChatAssistant"))}</div>
      </>}
    </SetupDisclosure>}
  </>;
}
