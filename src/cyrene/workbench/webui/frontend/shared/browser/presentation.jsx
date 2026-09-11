export function wbcBrowserPictureInPictureAvailable(browser) {
  return !(browser && browser.supportsPictureInPicture === false);
}

export function wbcBrowserPresentationMode(mode, pictureInPictureAvailable) {
  if (!pictureInPictureAvailable) return "maximized";
  return mode === "minimized" ? "pip" : (mode || "pip");
}

export function wbcBrowserRestoreIfAvailable(pictureInPictureAvailable, restore) {
  if (pictureInPictureAvailable && restore) restore();
}

export function wbcBrowserTitlebarActions(mode, pictureInPictureAvailable) {
  if (mode === "pip") return [{ action_id: "maximize", kind: "invoke", gesture_aliases: ["double_press", "maximize_button"], risk: "R1" }];
  if (!pictureInPictureAvailable) return [];
  return [{ action_id: "restore", kind: "invoke", gesture_aliases: ["double_press", "restore_button", "escape_key"], risk: "R1" }];
}

export function wbcBrowserTitlebarHandlers(pictureInPictureAvailable, maximize, restore) {
  return pictureInPictureAvailable ? { maximize: maximize, restore: restore } : { maximize: maximize };
}

export function WbcBrowserRestoreButton({ available, onRestore, icon, title }) {
  if (!available) return null;
  return <button type="button" onClick={onRestore} title={title} aria-label={title}>{icon}</button>;
}
