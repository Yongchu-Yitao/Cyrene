import { settingsFetch, showSettingsToast, workbenchServices } from "./shared.jsx"

function backupDefaultName() {
  var now = new Date();
  function pad(value) { return String(value).padStart(2, "0"); }
  return "cyrene_backup_" + now.getFullYear() + pad(now.getMonth() + 1) + pad(now.getDate()) + "_" + pad(now.getHours()) + pad(now.getMinutes()) + pad(now.getSeconds()) + ".zip";
}

function confirmBackup(options) {
  var feedback = workbenchServices.feedback();
  if (feedback && typeof feedback.confirmModal === "function") return feedback.confirmModal(options);
  return window.confirm(options.title + "\n\n" + options.body);
}

export async function createBackup({ t, setBackupMsg, formatBytes, loadBackups }) {
  var bridge = window.cyrene;
  if (!bridge || typeof bridge.pickBackupSavePath !== "function") {
    showSettingsToast(t("settings.backupPickerUnavailable"), "error");
    return;
  }
  try {
    if (!await confirmBackup({
      title: t("settings.backupExportBtn"),
      body: t("settings.backupScope"),
      confirmLabel: t("settings.backupChooseSaveTitle"),
    })) return;
    var selection = await bridge.pickBackupSavePath({ title: t("settings.backupChooseSaveTitle"), defaultName: backupDefaultName() });
    if (!selection || selection.cancelled || !selection.path) return;
    setBackupMsg("");
    showSettingsToast(t("settings.backupExporting"), "info");
    var response = await settingsFetch("/api/backup/export", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path: selection.path }) });
    var result = await response.json();
    if (!result.ok) throw new Error(result.error || t("settings.failed"));
    showSettingsToast(t("settings.backupExported", { n: result.entries.length, size: formatBytes(result.size) }), "success");
    loadBackups();
  } catch (e) {
    showSettingsToast(t("settings.failed") + ": " + e.message, "error");
  }
}

export async function restoreBackup({ t }, path) {
  try {
    if (!path) {
      var bridge = window.cyrene;
      if (!bridge || typeof bridge.pickBackupFile !== "function") {
        showSettingsToast(t("settings.backupPickerUnavailable"), "error");
        return;
      }
      var selection = await bridge.pickBackupFile({ title: t("settings.backupChooseFileTitle") });
      if (!selection || selection.cancelled || !selection.path) return;
      path = selection.path;
    }
    var filename = path.split(/[\\/]/).pop();
    if (!await confirmBackup({
      title: t("settings.backupConfirmTitle"),
      body: filename + "\n\n" + t("settings.backupConfirmBody"),
      confirmLabel: t("settings.backupConfirmRestore"),
      danger: true,
    })) return;
    var response = await settingsFetch("/api/backup/restore", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path: path }) });
    var result = await response.json();
    if (!result.ok) throw new Error(result.error || (result.errors || []).join(";") || t("settings.backupRestoreFailed"));
    showSettingsToast(t("settings.backupRestored", { n: result.restored.length }) + " " + t("settings.backupRestartRequired"), "success");
  } catch (e) {
    showSettingsToast(t("settings.backupRestoreFailed") + ": " + e.message, "error");
  }
}
