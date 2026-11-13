import { workbenchServices } from "../../shared/runtime/services.jsx"
import {
  useWbcEffect,
  useWbcRef,
  useWbcState,
  wbcErrorText,
  wbcT,
} from "../../workbench-chat.jsx"
import { wbcLoadAttachments, wbcSaveAttachments } from "./messages.jsx"
import { wbcHasFileResourceDrag, wbcReadResourceDrag } from "./drag-layout.jsx"

export function WbcVisionModelNotice({ blocked }) {
  return blocked ? <div role="status" className="wbc-tools-empty">{wbcT("workbenchChat.imageModelRequired", "Select a vision-capable model before sending images.")}</div> : null;
}

function wbcComposerAtFileDrop(event) {
  var target = event.target
  if (!target || !target.closest) return null
  var box = target.closest(".wbc-composer-box")
  if (box || !target.closest(".wbc-pane-card-drop-layer")) return box
  // Split sensors sit on top of the conversation during a resource drag.
  // Give the input beneath those sensors first refusal on files.
  for (var node of document.elementsFromPoint(event.clientX, event.clientY)) {
    box = node.closest(".wbc-composer-box")
    if (box) return box
  }
  return null
}

function wbcAttachmentKey(file) {
  return String(file.id || file.path || file.url || file.name || "")
}

function useWbcComposerResourceDrops({ chatId, composerBoxRef, awaitingAnswer, running, addResourceFile, addFiles, setDraft }) {
  useWbcEffect(function () {
    function onDroppedFiles(event) {
      if (awaitingAnswer) return
      var detail = event && event.detail || {}
      if (detail.targetChatId && String(detail.targetChatId) !== String(chatId)) return
      if (detail.resource && detail.resource.kind === "file") {
        addResourceFile(detail.resource.file || detail.resource)
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
  }, [chatId, awaitingAnswer, addResourceFile, addFiles, setDraft])

  useWbcEffect(function () {
    if (!composerBoxRef) return
    function acceptFileResource(event) {
      var box = composerBoxRef.current
      if (!box || !wbcHasFileResourceDrag(event) || wbcComposerAtFileDrop(event) !== box) return
      event.preventDefault()
      event.stopPropagation()
      if (awaitingAnswer || running) {
        event.dataTransfer.dropEffect = "none"
        return
      }
      event.dataTransfer.dropEffect = "copy"
      if (event.type !== "drop") return
      var resource = wbcReadResourceDrag(event)
      if (resource && resource.kind === "file") addResourceFile(resource.file || resource)
    }
    document.addEventListener("dragover", acceptFileResource, true)
    document.addEventListener("drop", acceptFileResource, true)
    return function () {
      document.removeEventListener("dragover", acceptFileResource, true)
      document.removeEventListener("drop", acceptFileResource, true)
    }
  }, [composerBoxRef, awaitingAnswer, running, addResourceFile])
}

function useWbcComposerAttachments({
  awaitingAnswer,
  canAttachFiles,
  canAttachImages,
  chatId,
  composerBoxRef,
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
    uploadFiles(function () { return model.uploadFiles(files) }, function (uploaded) {
      setAttachments(function (prev) { return prev.concat(uploaded) })
    })
  }

  function uploadFiles(upload, onUploaded) {
    uploadCountRef.current += 1
    setUploading(true)
    upload()
      .then(onUploaded)
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

  function addResourceFile(file) {
    if (awaitingAnswer || !file) return
    var image = String(file.kind || "") === "image"
      || String(file.content_type || file.type || "").indexOf("image/") === 0
    if (image ? !canAttachImages : !canAttachFiles) {
      workbenchServices.feedback().showToast(image
        ? wbcT("workbenchChat.capability.noImage", "This Agent does not support image input")
        : wbcT("workbenchChat.capability.noFile", "This Agent does not support file input"), "error")
      return
    }
    var key = wbcAttachmentKey(file)
    if (file.path) {
      setAttachments(function (prev) {
        return key && prev.some(function (item) { return wbcAttachmentKey(item) === key }) ? prev : prev.concat([file])
      })
    } else if (file.url) {
      // Transcript attachments expose URLs rather than private file paths.
      // Upload the original bytes so the draft can actually send this file.
      uploadFiles(async function () {
        var response = await fetch(file.url)
        if (!response.ok) throw new Error("HTTP " + response.status)
        var blob = await response.blob()
        return model.uploadFiles([new File([blob], file.name || "file", { type: file.content_type || blob.type })])
      }, function (uploaded) {
        if (previousChatIdRef.current !== chatId) return
        setAttachments(function (prev) {
          return prev.filter(function (item) { return item.path || wbcAttachmentKey(item) !== key }).concat(uploaded)
        })
      })
    }
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

  useWbcComposerResourceDrops({ chatId, composerBoxRef, awaitingAnswer, running, addResourceFile, addFiles, setDraft })

  return {
    attachRef, attachments, failedImagePreviews, fileRef, onFilePick, onPaste,
    pickFiles, setAttachments, setFailedImagePreviews, uploading,
  }
}

export { useWbcComposerAttachments }
