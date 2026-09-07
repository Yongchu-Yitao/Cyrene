import { workbenchServices } from "../../shared/runtime/services.jsx"

export function handleComposerKey(event, { slashDraftOpen, slashItems, slashActiveIndex, draft, setSlashActiveIndex, chooseSlashCommand, setSlashDismissedDraft, submit, setToolsOpen, setModelOpen, setModelPanel }) {
  if (handleSlashKey(event, { slashDraftOpen, slashItems, slashActiveIndex, draft, setSlashActiveIndex, chooseSlashCommand, setSlashDismissedDraft })) return;
  var sc = workbenchServices.shortcuts();
  if (sc && sc.matches(event, "composer-send")) {
    if (event.nativeEvent && event.nativeEvent.isComposing) return; // IME guard
    event.preventDefault();
    submit();
    return;
  }
  if (sc && sc.matches(event, "composer-newline")) {
    // Allow the textarea's default Shift+Enter behavior (insert newline).
    return;
  }
  // Fallback when the shortcut module is unavailable: plain Enter sends,
  // Shift/Cmd/Ctrl+Enter inserts a newline.
  if (!sc && event.key === "Enter" && !event.shiftKey && !event.metaKey && !event.ctrlKey) {
    if (event.nativeEvent && event.nativeEvent.isComposing) return; // IME guard
    event.preventDefault();
    submit();
    return;
  }
  if (event.key === "Escape") {
    setToolsOpen(false);
    setModelOpen(false);
    setModelPanel("root");
  }
}


function handleSlashKey(event, { slashDraftOpen, slashItems, slashActiveIndex, draft, setSlashActiveIndex, chooseSlashCommand, setSlashDismissedDraft }) {
  if (slashDraftOpen && slashItems.length && !event.metaKey && !event.ctrlKey && !event.altKey) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      var direction = event.key === "ArrowDown" ? 1 : -1;
      setSlashActiveIndex(function (current) {
        return (current + direction + slashItems.length) % slashItems.length;
      });
      return true;
    }
    if (event.key === "Enter" || event.key === "Tab") {
      if (event.nativeEvent && event.nativeEvent.isComposing) return true;
      event.preventDefault();
      chooseSlashCommand(slashItems[Math.min(slashActiveIndex, slashItems.length - 1)]);
      return true;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      setSlashDismissedDraft(draft);
      return true;
    }
  }
  return false;
}
