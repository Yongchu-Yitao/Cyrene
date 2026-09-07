import { WBC_ICONS, wbcT, wbcAttachmentTypeLabel } from "../../workbench-chat.jsx"
import { WbcFileVisual } from "./file-resources.jsx"

export function WbcComposerAttachmentView({ attachments, failedImagePreviews, setFailedImagePreviews, setAttachments, awaitingAnswer }) {
  return attachments.length > 0 && (
          <div className="wbc-attach-row">
            {attachments.map(function (file, i) {
              var isImg = file.kind === "image" || String(file.content_type || "").indexOf("image") === 0;
              var attachmentKey = String(file.id || file.url || i);
              var showImagePreview = isImg && file.url && !failedImagePreviews[attachmentKey];
              return (
                <div className={"wbc-attach-card" + (showImagePreview ? " image" : " file")} key={attachmentKey}>
                  {showImagePreview
                    ? <img src={file.url} alt="" onError={function () {
                        setFailedImagePreviews(function (prev) {
                          return Object.assign({}, prev, { [attachmentKey]: true });
                        });
                      }} />
                    : <>
                        <WbcFileVisual file={file} className="wbc-composer-file-visual" />
                        <span className="wbc-attach-file-meta">
                          <b title={file.name}>{file.name || "file"}</b>
                          <small>{wbcAttachmentTypeLabel(file)}</small>
                        </span>
                      </>}
                  <button type="button" className="wbc-attach-x" disabled={awaitingAnswer} onClick={function () {
                    setAttachments(attachments.filter(function (_f, idx) { return idx !== i; }));
                  }} aria-label={wbcT("workbenchChat.removeAttachment", "Remove attachment")}>{WBC_ICONS.x}</button>
                </div>
              );
            })}
          </div>
        );
}
