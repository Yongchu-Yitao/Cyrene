import { workbenchServices } from "./runtime/services.jsx"

function wbT(key, fallback, params) {
  return workbenchServices.i18n().t(key, params, fallback);
}

// Document-level file drop target used by the task, conversation and knowledge
// pages. Listening on document makes the whole visible module accept files,
// including its rail and side panels, while the ref keeps the listener stable
// across renders and avoids stale upload callbacks.
function useWorkbenchFileDrop(onFiles, enabled) {
  var [active, setActive] = React.useState(false);
  var callbackRef = React.useRef(onFiles);
  var depthRef = React.useRef(0);
  callbackRef.current = onFiles;

  React.useEffect(function () {
    if (!enabled) {
      depthRef.current = 0;
      setActive(false);
      return undefined;
    }

    function hasFiles(event) {
      var transfer = event && event.dataTransfer;
      if (!transfer) return false;
      var types = Array.prototype.slice.call(transfer.types || []);
      return types.indexOf("Files") >= 0;
    }
    function onDragEnter(event) {
      if (!hasFiles(event)) return;
      event.preventDefault();
      depthRef.current += 1;
      setActive(true);
    }
    function onDragOver(event) {
      if (!hasFiles(event)) return;
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
      setActive(true);
    }
    function onDragLeave(event) {
      if (!hasFiles(event)) return;
      event.preventDefault();
      depthRef.current = Math.max(0, depthRef.current - 1);
      if (depthRef.current === 0) setActive(false);
    }
    function reset() {
      depthRef.current = 0;
      setActive(false);
    }
    function onDrop(event) {
      if (!hasFiles(event)) return;
      event.preventDefault();
      reset();
      var files = event.dataTransfer && event.dataTransfer.files;
      if (files && files.length && callbackRef.current) callbackRef.current(files);
    }

    document.addEventListener("dragenter", onDragEnter);
    document.addEventListener("dragover", onDragOver);
    document.addEventListener("dragleave", onDragLeave);
    document.addEventListener("drop", onDrop);
    window.addEventListener("blur", reset);
    return function () {
      document.removeEventListener("dragenter", onDragEnter);
      document.removeEventListener("dragover", onDragOver);
      document.removeEventListener("dragleave", onDragLeave);
      document.removeEventListener("drop", onDrop);
      window.removeEventListener("blur", reset);
    };
  }, [!!enabled]);

  return active;
}

function WorkbenchFileDropOverlay({ label, busy }) {
  return (
    <div className="wb-file-drop-overlay" role="status" aria-live="polite">
      <div className="wb-file-drop-card">
        <svg viewBox="0 0 24 24" width="30" height="30" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 16V4" />
          <path d="m7 9 5-5 5 5" />
          <path d="M5 14v4a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-4" />
        </svg>
        <b>{busy ? wbT("workbenchChat.uploading", "Uploading...") : label}</b>
      </div>
    </div>
  );
}

export { WorkbenchFileDropOverlay, useWorkbenchFileDrop }
