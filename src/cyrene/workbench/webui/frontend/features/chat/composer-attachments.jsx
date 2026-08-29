import { workbenchServices } from "../../shared/runtime/services.jsx"
import {
  useWbcEffect,
  useWbcRef,
  useWbcState,
  wbcErrorText,
  wbcT,
} from "../../workbench-chat.jsx"
import { wbcLoadAttachments, wbcSaveAttachments } from "./messages.jsx"

function useWbcComposerAttachments({
  awaitingAnswer,
  canAttachFiles,
  canAttachImages,
  chatId,
  draftNamespace,
  model,
  previousChatIdRef,
  running,
  setDraft,
}) {
  var [attachments, setAttachments] = useWbcState(function () {
    return wbcLoadAttachments(chatId, draftNamespace)
  })
  var [uploading, setUploading] = useWbcState(false)
  var [failedImagePreviews, setFailedImagePreviews] = useWbcState({})
  var fileRef = useWbcRef(null)
  var uploadCountRef = useWbcRef(0)
  var attachRef = useWbcRef(attachments)

  useWbcEffect(function () { attachRef.current = attachments })
  useWbcEffect(function () {
    if (previousChatIdRef.current === chatId) {
      wbcSaveAttachments(chatId, attachments, draftNamespace)
    }
  }, [attachments])

  function pickFiles() {
    if (fileRef.current) fileRef.current.click()
  }

  function addFiles(files) {
    if (awaitingAnswer || !files || !files.length) return
    if (!canAttachFiles) {
      var nonImageFiles = Array.prototype.filter.call(files || [], function (file) {
        return !(file && (String(file.type || "").indexOf("image/") === 0 || String(file.kind || "") === "image"))
      })
      if (nonImageFiles.length) {
        workbenchServices.feedback().showToast(
          wbcT("workbenchChat.capability.noFile", "This Agent does not support file input"),
          "error"
        )
      }
      files = Array.prototype.filter.call(files || [], function (file) {
        return file && (String(file.type || "").indexOf("image/") === 0 || String(file.kind || "") === "image")
      })
      if (!files.length) return
    }
    if (!canAttachImages) {
      var imageFiles = Array.prototype.filter.call(files || [], function (file) {
        return file && (String(file.type || "").indexOf("image/") === 0 || String(file.kind || "") === "image")
      })
      if (imageFiles.length) {
        workbenchServices.feedback().showToast(
          wbcT("workbenchChat.capability.noImage", "This Agent does not support image input"),
          "error"
        )
      }
      files = Array.prototype.filter.call(files || [], function (file) {
        return !(file && (String(file.type || "").indexOf("image/") === 0 || String(file.kind || "") === "image"))
      })
      if (!files.length) return
    }
    uploadCountRef.current += 1
    setUploading(true)
    model.uploadFiles(files)
      .then(function (uploaded) { setAttachments(function (prev) { return prev.concat(uploaded) }) })
      .catch(function (error) {
        workbenchServices.feedback().showToast(
          wbcT("workbenchChat.uploadFailed", "Upload failed: {error}", { error: wbcErrorText(error) }),
          "error"
        )
      })
      .finally(function () {
        uploadCountRef.current = Math.max(0, uploadCountRef.current - 1)
        if (uploadCountRef.current === 0) setUploading(false)
        if (fileRef.current) fileRef.current.value = ""
      })
  }

  function onFilePick(event) {
    addFiles(event.target.files)
  }

  function onPaste(event) {
    if (running || awaitingAnswer) return
    var clipboard = event && (event.clipboardData || (event.nativeEvent && event.nativeEvent.clipboardData))
    if (!clipboard) return
    var files = Array.prototype.slice.call(clipboard.files || []).filter(function (file) { return !!file })
    if (!files.length) {
      files = Array.prototype.slice.call(clipboard.items || []).map(function (item) {
        return item && item.kind === "file" ? item.getAsFile() : null
      }).filter(function (file) { return !!file })
    }
    if (!files.length) return
    event.preventDefault()
    addFiles(files)
  }

  useWbcEffect(function () {
    function onDroppedFiles(event) {
      if (awaitingAnswer) return
      var detail = event && event.detail || {}
      if (detail.targetChatId && String(detail.targetChatId) !== String(chatId)) return
      if (detail.resource && detail.resource.kind === "file") {
        var file = detail.resource.file || detail.resource
        var resourceIsImage = file
          && (String(file.kind || "") === "image" || String(file.content_type || file.type || "").indexOf("image/") === 0)
        if (!canAttachImages && resourceIsImage) {
          workbenchServices.feedback().showToast(wbcT("workbenchChat.capability.noImage", "This Agent does not support image input"), "error")
          return
        }
        if (!canAttachFiles && !resourceIsImage) {
          workbenchServices.feedback().showToast(wbcT("workbenchChat.capability.noFile", "This Agent does not support file input"), "error")
          return
        }
        setAttachments(function (prev) {
          var key = String(file.id || file.path || file.url || file.name || "")
          if (key && prev.some(function (item) {
            return String(item.id || item.path || item.url || item.name || "") === key
          })) return prev
          return prev.concat([file])
        })
        return
      }
      if (detail.resource && detail.resource.kind === "snippet") {
        var quote = String(detail.resource.text || "").trim().split("\n").map(function (line) {
          return "> " + line
        }).join("\n")
        if (quote) setDraft(function (prev) { return prev ? prev + "\n\n" + quote : quote })
        return
      }
      addFiles(detail.files)
    }
    window.addEventListener("cyrene:add-chat-attachments", onDroppedFiles)
    return function () { window.removeEventListener("cyrene:add-chat-attachments", onDroppedFiles) }
  }, [chatId, awaitingAnswer, canAttachFiles, canAttachImages])

  return {
    attachRef: attachRef,
    attachments: attachments,
    failedImagePreviews: failedImagePreviews,
    fileRef: fileRef,
    onFilePick: onFilePick,
    onPaste: onPaste,
    pickFiles: pickFiles,
    setAttachments: setAttachments,
    setFailedImagePreviews: setFailedImagePreviews,
    uploading: uploading,
  }
}

export { useWbcComposerAttachments }
