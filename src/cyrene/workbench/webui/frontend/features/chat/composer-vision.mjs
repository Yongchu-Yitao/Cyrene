export function requiresVisionModel(attachments, model, builtin = true) {
  if (!builtin) return false;
  const hasImage = (attachments || []).some(item => item.kind === "image"
    || String(item.content_type || "").startsWith("image/")
    || /\.(png|jpe?g|webp|gif|bmp|tiff?|heic|heif|avif)$/i.test(String(item.name || item.path || "")));
  return hasImage && !(model && Array.isArray(model.capabilities) && model.capabilities.includes("vision"));
}

export function attachmentSubmissionBlocked(imageModelBlocked, uploading) {
  return imageModelBlocked || uploading;
}

export function composerSendDisabled(disabled, awaitingAnswer, showStopButton, attachmentBlocked) {
  return disabled || (!awaitingAnswer && !showStopButton && attachmentBlocked);
}
