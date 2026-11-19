import { ActionIcon } from "./action-icon.jsx"
import { workbenchServices } from "../../shared/runtime/services.jsx"

export function EditorDialog({ title, onClose, busy = false, className = "", children }) {
  const t = workbenchServices.i18n().t
  const dialog = React.useRef(null)
  const titleId = React.useId()
  React.useLayoutEffect(() => {
    const node = dialog.current
    node.showModal()
    return () => node.close()
  }, [])
  return <dialog className={"svc-editor-dialog " + className} ref={dialog} aria-labelledby={titleId}
    onCancel={event => { event.preventDefault(); if (!busy) onClose() }}>
    <header className="dialog-title">
      <h3 id={titleId}>{title}</h3>
      <button type="button" disabled={busy} aria-label={t("settings.closeEditor")} onClick={onClose}><ActionIcon name="x"/></button>
    </header>
    {children}
  </dialog>
}
