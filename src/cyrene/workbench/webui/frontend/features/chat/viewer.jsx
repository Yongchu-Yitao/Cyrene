import { workbenchServices } from "../../shared/runtime/services.jsx"
import { PluginFrontendService } from "../../platform/plugins.jsx"
import { WBC_ICONS, WBC_OFFICE_MAX_FILE_BYTES, useWbcEffect, useWbcLayoutEffect, useWbcMemo, useWbcRef, useWbcState, wbcFileViewKind, wbcHardenOfficeLinks, wbcLoadOfficeRenderer, wbcRenderMapMarkdown, wbcRenderMarkdown, wbcT, wbcValidateOfficeArchive } from "../../workbench-chat.jsx"
import { WBC_PROJECT_FILE_DRAFTS, useWbcMapData, wbcCanEditProjectTextFile, wbcMapItemKey, wbcProjectFileDraftKey, wbcProjectFileEditUrl, wbcZoomAnchorRestorer } from "./split-pane.jsx"
import { WbcFileVisual, wbcCanOpenExternally, wbcDownloadLink, wbcHtmlPreviewDocument } from "./file-resources.jsx"

// Workbench chat feature module with explicit ESM dependencies.
function WbcPdfJsViewer({ file, url, onViewed }) {
  var pdf = workbenchServices.pdf();
  var gestureRef = useWbcRef(null);
  var containerRef = useWbcRef(null);
  var viewerRef = useWbcRef(null);
  var fitScaleRef = useWbcRef(1);
  var [pageNum, setPageNum] = useWbcState(1);
  var [pageCount, setPageCount] = useWbcState(0);
  var [scale, setScale] = useWbcState(1);
  var [loading, setLoading] = useWbcState(true);
  var [failed, setFailed] = useWbcState(false);
  var [failReason, setFailReason] = useWbcState("");
  var [analyzing, setAnalyzing] = useWbcState(false);
  var [analysisResult, setAnalysisResult] = useWbcState("");
  var analyzeButtonRef = useWbcRef(null);
  var pdfPinchRef = useWbcRef({ distance: 0, scale: 1 });

  useWbcEffect(function () {
    var container = containerRef.current;
    if (!container) { setFailReason('container not mounted'); setFailed(true); setLoading(false); return; }
    if (!url) { setFailReason('no URL'); setFailed(true); setLoading(false); return; }
    if (!pdf.lib || !pdf.viewer || !pdf.setupViewer) { setFailReason('PDF.js not loaded'); setFailed(true); setLoading(false); return; }

    var cancelled = false;
    var abortLoader = new AbortController();
    var loadTimedOut = false;
    var timer = setTimeout(function () {
      loadTimedOut = true;
      abortLoader.abort(new DOMException('PDF loading timed out', 'TimeoutError'));
      setFailReason('timeout (60s)');
      setFailed(true);
      setLoading(false);
    }, 60000);

    var result = pdf.setupViewer(container);
    var viewer = result.viewer;
    var eventBus = result.eventBus;
    var loadedDocument = null;
    var fitFrame = 0;
    var lastFitWidth = 0;
    viewerRef.current = viewer;
    var gestureSurface = gestureRef.current;
    if (gestureSurface) {
      gestureSurface.addEventListener("wheel", handlePdfWheel, { passive: false });
      gestureSurface.addEventListener("touchstart", handlePdfTouchStart, { passive: true });
      gestureSurface.addEventListener("touchmove", handlePdfTouchMove, { passive: false });
    }

    // Track page changes
    function onPageChanging(evt) {
      if (!cancelled) setPageNum(evt.pageNumber);
    }
    eventBus.on('pagechanging', onPageChanging);

    // Handle resize (e.g. sidebar panel resize)
    function fitViewerWidth(force) {
      var measuredWidth = container.getBoundingClientRect().width;
      if (!force && lastFitWidth && Math.abs(measuredWidth - lastFitWidth) < 1) {
        viewer.update();
        return;
      }
      lastFitWidth = measuredWidth;
      window.cancelAnimationFrame(fitFrame);
      fitFrame = window.requestAnimationFrame(function () {
        if (cancelled || document.body.classList.contains("wbc-resizing-side-agent")) return;
        viewer.currentScaleValue = 'page-width';
        viewer.update();
        fitScaleRef.current = viewer.currentScale;
        setScale(viewer.currentScale);
      });
    }
    function onPagesInit() { fitViewerWidth(true); }
    function onContainerResize() { fitViewerWidth(false); }
    function onSplitResizeEnd() { fitViewerWidth(true); }
    eventBus.on('pagesinit', onPagesInit);
    var resizeObserver = new ResizeObserver(onContainerResize);
    resizeObserver.observe(container);
    window.addEventListener("workbench:split-resize-end", onSplitResizeEnd);

    // Copy the original PDF text rather than browser-measured text-layer content.
    var selectionSanitizer = pdf.installSelectionSanitizer(container, viewer, eventBus);
    var copyFix = pdf.installCopyFix(container, viewer);

    // Fetch and load PDF document
    pdf.loadPdf(url, viewer, abortLoader.signal).then(function (doc) {
      loadedDocument = doc;
      if (cancelled) {
        try { doc.destroy(); } catch (e) {}
        return;
      }
      clearTimeout(timer);
      setPageCount(doc.numPages);
      setPageNum(1);
      setLoading(false);
      fitViewerWidth(true);
      if (onViewed) onViewed();
    }).catch(function (err) {
      if (!cancelled) {
        clearTimeout(timer);
        setFailReason(loadTimedOut ? 'timeout (60s)' : String(err && err.message || err));
        setFailed(true);
        setLoading(false);
      }
    });

    return function () {
      cancelled = true;
      window.cancelAnimationFrame(fitFrame);
      clearTimeout(timer);
      abortLoader.abort();
      selectionSanitizer.abort();
      copyFix.abort();
      resizeObserver.disconnect();
      if (gestureSurface) {
        gestureSurface.removeEventListener("wheel", handlePdfWheel);
        gestureSurface.removeEventListener("touchstart", handlePdfTouchStart);
        gestureSurface.removeEventListener("touchmove", handlePdfTouchMove);
      }
      window.removeEventListener("workbench:split-resize-end", onSplitResizeEnd);
      eventBus.off('pagechanging', onPageChanging);
      eventBus.off('pagesinit', onPagesInit);
      if (viewerRef.current) {
        try { viewerRef.current.setDocument(null); } catch (e) {}
      }
      if (loadedDocument) {
        try { loadedDocument.destroy(); } catch (e) {}
      }
      viewerRef.current = null;
    };
  }, [url]);

  function zoomIn() {
    var v = viewerRef.current;
    if (v) applyPdfGestureScale(v.currentScale * 1.15);
  }
  function zoomOut() {
    var v = viewerRef.current;
    if (v) applyPdfGestureScale(v.currentScale / 1.15);
  }
  function zoomReset() {
    var v = viewerRef.current;
    if (v) {
      var restoreAnchor = wbcZoomAnchorRestorer(containerRef.current, v.currentScale);
      v.currentScaleValue = 'page-width';
      fitScaleRef.current = v.currentScale;
      setScale(v.currentScale);
      restoreAnchor(v.currentScale);
    }
  }
  function applyPdfGestureScale(nextScale, clientX, clientY) {
    var v = viewerRef.current;
    if (!v) return;
    var oldScale = v.currentScale;
    var restoreAnchor = wbcZoomAnchorRestorer(containerRef.current, oldScale, clientX, clientY);
    v.currentScale = Math.max(fitScaleRef.current, Math.min(5, nextScale));
    setScale(v.currentScale);
    restoreAnchor(v.currentScale);
  }
  function handlePdfWheel(event) {
    if (!event.ctrlKey && !event.metaKey) return;
    event.preventDefault();
    var v = viewerRef.current;
    if (v) applyPdfGestureScale(v.currentScale * Math.exp(-event.deltaY * 0.01), event.clientX, event.clientY);
  }
  function handlePdfTouchStart(event) {
    if (event.touches.length !== 2) return;
    var dx = event.touches[0].clientX - event.touches[1].clientX;
    var dy = event.touches[0].clientY - event.touches[1].clientY;
    pdfPinchRef.current = { distance: Math.hypot(dx, dy), scale: viewerRef.current ? viewerRef.current.currentScale : scale };
  }
  function handlePdfTouchMove(event) {
    if (event.touches.length !== 2 || !pdfPinchRef.current.distance) return;
    event.preventDefault();
    var dx = event.touches[0].clientX - event.touches[1].clientX;
    var dy = event.touches[0].clientY - event.touches[1].clientY;
    applyPdfGestureScale(
      pdfPinchRef.current.scale * Math.hypot(dx, dy) / pdfPinchRef.current.distance,
      (event.touches[0].clientX + event.touches[1].clientX) / 2,
      (event.touches[0].clientY + event.touches[1].clientY) / 2
    );
  }

  // Text selection → agent analysis
  function analyzePdfText() {
    var text = pdf.getSelectedText(containerRef.current).trim();
    if (!text || analyzing) return;
    var language = workbenchServices.i18n().getLang();

    setAnalyzing(true);
    setAnalysisResult('');

    if (!pdf.buildAnalysisInventory || !pdf.extractAnalysisContext) {
      setAnalysisResult(wbcT(
        "workbenchChat.pdfAnalysisFailed",
        "Analysis failed: {error}",
        { error: wbcT("workbenchChat.pdfAnalysisUnavailable", "PDF context tools unavailable") }
      ));
      setAnalyzing(false);
      return;
    }

    pdf.buildAnalysisInventory(containerRef.current, viewerRef.current, pageNum)
      .then(function (inventory) {
        return fetch('/api/pdf/context-plan', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            text: text,
            pdf_name: file ? file.name || 'PDF' : 'PDF',
            lang: language,
            inventory: inventory,
          }),
        }).then(function (response) { return response.json(); })
          .then(function (plan) {
            if (plan.error) throw new Error(plan.error);
            return pdf.extractAnalysisContext(
              viewerRef.current,
              plan.page_numbers,
              inventory,
              plan.reason
            );
          });
      })
      .then(function (context) {
        return fetch('/api/pdf/analyze', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            text: text,
            pdf_name: file ? file.name || 'PDF' : 'PDF',
            lang: language,
            context: context,
          }),
        });
      })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) throw new Error(data.error);
        setAnalysisResult(data.result || wbcT("workbenchChat.pdfAnalysisEmpty", "No result"));
        var sel = window.getSelection();
        if (sel) sel.removeAllRanges();
      }).catch(function (err) {
        setAnalysisResult(wbcT(
          "workbenchChat.pdfAnalysisFailed",
          "Analysis failed: {error}",
          { error: err.message }
        ));
      }).finally(function () {
        setAnalyzing(false);
        if (analyzeButtonRef.current) analyzeButtonRef.current.style.display = 'none';
      });
  }

  var head = (
    <div className="wbc-viewer-head">
      <span className="wbc-viewer-name" title={file && file.name}>{(file && file.name) || "PDF"}</span>
      {!loading && !failed && (
        <span className="wbc-viewer-switch wbc-viewer-zoom">
          <button type="button" onClick={zoomOut} disabled={scale <= fitScaleRef.current + 0.001}>−</button>
          <button type="button" onClick={zoomReset}>{Math.round(scale * 100) + "%"}</button>
          <button type="button" onClick={zoomIn}>+</button>
        </span>
      )}
      {!loading && !failed && (
        <span style={{ fontSize: 11, color: 'var(--text-3)', marginLeft: 4, whiteSpace: 'nowrap' }}>
          {pageNum} / {pageCount}
        </span>
      )}
      {url ? <a className="wbc-viewer-open" href={"/pdf/viewer?url=" + encodeURIComponent(url) + "&name=" + encodeURIComponent((file && file.name) || "PDF") + "&lang=" + encodeURIComponent(workbenchServices.i18n().getLang())} target="_blank" rel="noreferrer" title={wbcT("workbenchChat.viewerOpenExternal", "Open in a new window")}>↗</a> : null}
      {file ? wbcDownloadLink(file, { className: "wbc-viewer-download" }) : null}
    </div>
  );

  var body = (
    <div ref={gestureRef} className="wbc-viewer-scroll wbc-gesture-zoom" style={{ overflow: 'hidden', position: 'relative' }} onMouseUp={function () {
      if (loading || failed) return;
      setTimeout(function () {
        if (pdf.getSelectedText(containerRef.current).trim()) {
          if (analyzeButtonRef.current) analyzeButtonRef.current.style.display = 'inline-flex';
        }
      }, 200);
    }}>
      {/* Container div for PDF.js — always rendered so ref is available */}
      <div ref={containerRef} style={{ position: 'relative', overflow: 'auto', height: '100%' }} />

      {loading && (
        <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg, #fff)', zIndex: 10 }}>
          <p className="workbench-muted wbc-viewer-pad">{wbcT("settings.pathLoading", "Loading...")}</p>
        </div>
      )}
      {failed && (
        <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg, #fff)', zIndex: 10 }}>
          <p className="workbench-muted wbc-viewer-pad">
            {wbcT("workbenchChat.viewerLoadFailed", "File failed to load.")}
            {url ? " " + wbcT("workbenchChat.viewerOpenFallback", "Try opening it in a new window.") : ""}
            {failReason ? <><br /><small style={{ opacity: 0.6 }}>{failReason}</small></> : null}
          </p>
        </div>
      )}

      <button ref={analyzeButtonRef} id="wbc-pdf-analyze-btn" className="wbc-pdf-analyze" style={{ display: 'none' }}
        onClick={analyzePdfText}
        disabled={analyzing}
      >
        {analyzing ? <span className="wbc-pdf-analysis-spinner" aria-hidden="true" /> : null}
        <span>{analyzing
          ? wbcT("workbenchChat.pdfAnalyzing", "Analyzing…")
          : wbcT("workbenchChat.pdfAnalyze", "Analyze selection")}</span>
      </button>

      {(analyzing || analysisResult) ? (
        <section className="wbc-pdf-analysis" role="region" aria-live="polite" aria-label={wbcT("workbenchChat.pdfAnalysisTitle", "PDF analysis")}>
          {!analyzing ? <button type="button" className="wbc-pdf-analysis-close" aria-label={wbcT("workbenchChat.pdfAnalysisClose", "Close PDF analysis")} onClick={function () { setAnalysisResult(''); }}>×</button> : null}
          {analyzing ? (
            <div className="wbc-pdf-analysis-loading">
              <span className="wbc-pdf-analysis-spinner" aria-hidden="true" />
              <span>{wbcT("workbenchChat.pdfAnalysisLoading", "Agent is choosing and reading the relevant PDF context…")}</span>
            </div>
          ) : (
            <div className="wbc-pdf-analysis-body markdown wbc-msg-body" dangerouslySetInnerHTML={{ __html: wbcRenderMarkdown(analysisResult) }} />
          )}
        </section>
      ) : null}
    </div>
  );

  return (
    <div className="wbc-viewer">
      {head}
      {body}
    </div>
  );
}

function WbcOfficeViewer({ file, url, kind, onViewed }) {
  var containerRef = useWbcRef(null);
  var contentRef = useWbcRef(null);
  var viewerRef = useWbcRef(null);
  var docxFitRef = useWbcRef(null);
  var zoomRef = useWbcRef(100);
  var [loading, setLoading] = useWbcState(true);
  var [failureCode, setFailureCode] = useWbcState("");
  var [zoomPercent, setZoomPercent] = useWbcState(100);
  var officePinchRef = useWbcRef({ distance: 0, zoom: 100 });

  useWbcEffect(function () {
    var container = containerRef.current;
    if (!container || !url) {
      setLoading(false);
      setFailureCode("office_renderer_unavailable");
      return undefined;
    }
    var cancelled = false;
    var timedOut = false;
    var officeFitFrame = 0;
    var officeResizeObserver = null;
    var abortController = new AbortController();
    setLoading(true);
    setFailureCode("");
    zoomRef.current = 100;
    setZoomPercent(100);
    docxFitRef.current = null;
    container.replaceChildren();
    var renderTarget = document.createElement("div");
    renderTarget.className = "wbc-office-content";
    renderTarget.style.transform = "scale(1)";
    container.appendChild(renderTarget);
    contentRef.current = renderTarget;
    container.addEventListener("wheel", handleOfficeWheel, { passive: false });
    container.addEventListener("touchstart", handleOfficeTouchStart, { passive: true });
    container.addEventListener("touchmove", handleOfficeTouchMove, { passive: false });
    var timer = window.setTimeout(function () {
      if (cancelled) return;
      timedOut = true;
      abortController.abort();
      setLoading(false);
      setFailureCode("office_render_timeout");
    }, 60000);

    Promise.resolve().then(function () {
      if (Number(file && file.size || 0) > WBC_OFFICE_MAX_FILE_BYTES) {
        throw new Error("office_file_too_large");
      }
      return fetch(url, { signal: abortController.signal });
    }).then(function (response) {
      if (!response.ok) throw new Error("HTTP " + response.status);
      var contentLength = Number(response.headers.get("content-length") || 0);
      if (contentLength > WBC_OFFICE_MAX_FILE_BYTES) throw new Error("office_file_too_large");
      return response.arrayBuffer();
    }).then(function (buffer) {
      if (buffer.byteLength > WBC_OFFICE_MAX_FILE_BYTES) throw new Error("office_file_too_large");
      wbcValidateOfficeArchive(buffer);
      return Promise.all([buffer, wbcLoadOfficeRenderer(kind)]);
    }).then(function (result) {
      var buffer = result[0];
      var renderer = result[1];
      if (cancelled || timedOut) return null;
      if (kind === "docx") {
        return renderer.renderAsync(buffer, renderTarget, renderTarget, {
          className: "cyrene-docx",
          inWrapper: true,
          breakPages: true,
          ignoreLastRenderedPageBreak: false,
          useBase64URL: true,
          renderChanges: false,
          renderComments: false,
          renderAltChunks: false,
          experimental: false,
          debug: false,
        }).then(function () {
          wbcHardenOfficeLinks(container);
          function fitDocxPages() {
            window.cancelAnimationFrame(officeFitFrame);
            officeFitFrame = window.requestAnimationFrame(function () {
              if (cancelled) return;
              var availableWidth = Math.max(1, container.clientWidth - 36);
              Array.prototype.forEach.call(renderTarget.querySelectorAll("section.cyrene-docx"), function (page) {
                page.style.zoom = "1";
                var pageWidth = Math.max(1, page.offsetWidth);
                var fitScale = Math.min(1, availableWidth / pageWidth);
                page.style.zoom = String(fitScale);
              });
            });
          }
          docxFitRef.current = fitDocxPages;
          officeResizeObserver = new ResizeObserver(fitDocxPages);
          officeResizeObserver.observe(container);
          fitDocxPages();
          return null;
        });
      }
      return renderer.PptxViewer.open(buffer, renderTarget, {
        signal: abortController.signal,
        zipLimits: renderer.RECOMMENDED_ZIP_LIMITS,
        fitMode: "contain",
        zoomPercent: 100,
        scrollContainer: container,
        lazySlides: true,
        lazyMedia: true,
        listOptions: {
          windowed: true,
          initialSlides: 4,
          batchSize: 6,
          overscanViewport: 1.25,
        },
      }).then(function (viewer) {
        viewerRef.current = viewer;
        wbcHardenOfficeLinks(renderTarget);
        return viewer;
      });
    }).then(function () {
      if (cancelled || timedOut) {
        if (viewerRef.current) {
          try { viewerRef.current.destroy(); } catch (e) {}
          viewerRef.current = null;
        }
        container.replaceChildren();
        return;
      }
      window.clearTimeout(timer);
      setLoading(false);
      if (onViewed) onViewed();
    }).catch(function (error) {
      if (cancelled || timedOut) return;
      window.clearTimeout(timer);
      setLoading(false);
      setFailureCode(String(error && error.message || "office_renderer_unavailable"));
    });

    return function () {
      cancelled = true;
      window.cancelAnimationFrame(officeFitFrame);
      if (officeResizeObserver) officeResizeObserver.disconnect();
      container.removeEventListener("wheel", handleOfficeWheel);
      container.removeEventListener("touchstart", handleOfficeTouchStart);
      container.removeEventListener("touchmove", handleOfficeTouchMove);
      docxFitRef.current = null;
      contentRef.current = null;
      window.clearTimeout(timer);
      abortController.abort();
      if (viewerRef.current) {
        try { viewerRef.current.destroy(); } catch (e) {}
        viewerRef.current = null;
      }
      container.replaceChildren();
    };
  }, [url, kind]);

  function applyOfficeZoom(nextZoom, clientX, clientY) {
    var next = Math.max(100, Math.min(300, Number(nextZoom) || 100));
    var restoreAnchor = wbcZoomAnchorRestorer(containerRef.current, zoomRef.current / 100, clientX, clientY);
    zoomRef.current = next;
    setZoomPercent(Math.round(next));
    if (contentRef.current) contentRef.current.style.transform = "scale(" + (next / 100) + ")";
    restoreAnchor(next / 100);
  }
  function handleOfficeWheel(event) {
    if (!event.ctrlKey && !event.metaKey) return;
    event.preventDefault();
    applyOfficeZoom(zoomRef.current * Math.exp(-event.deltaY * 0.01), event.clientX, event.clientY);
  }
  function handleOfficeTouchStart(event) {
    if (event.touches.length !== 2) return;
    var dx = event.touches[0].clientX - event.touches[1].clientX;
    var dy = event.touches[0].clientY - event.touches[1].clientY;
    officePinchRef.current = { distance: Math.hypot(dx, dy), zoom: zoomRef.current };
  }
  function handleOfficeTouchMove(event) {
    if (event.touches.length !== 2 || !officePinchRef.current.distance) return;
    event.preventDefault();
    var dx = event.touches[0].clientX - event.touches[1].clientX;
    var dy = event.touches[0].clientY - event.touches[1].clientY;
    applyOfficeZoom(
      officePinchRef.current.zoom * Math.hypot(dx, dy) / officePinchRef.current.distance,
      (event.touches[0].clientX + event.touches[1].clientX) / 2,
      (event.touches[0].clientY + event.touches[1].clientY) / 2
    );
  }

  var failureText = failureCode === "office_file_too_large"
    ? wbcT("workbenchChat.officeFileTooLarge", "This Office file is too large to preview safely.")
    : (failureCode === "office_archive_too_large"
      ? wbcT("workbenchChat.officeArchiveTooLarge", "The expanded Office document is too large to preview safely.")
      : (failureCode === "office_invalid_archive"
        ? wbcT("workbenchChat.officeInvalid", "This is not a valid DOCX or PPTX file.")
        : (failureCode === "office_render_timeout"
          ? wbcT("workbenchChat.officeTimeout", "Office preview timed out.")
          : wbcT("workbenchChat.viewerLoadFailed", "File failed to load."))));

  return (
    <div className={"wbc-office-viewer is-" + kind}>
      {!loading && !failureCode && (
        <span className="wbc-viewer-switch wbc-viewer-zoom wbc-office-zoom">
          <button type="button" disabled={zoomPercent <= 100} onClick={function () { applyOfficeZoom(zoomRef.current - 25); }} aria-label={wbcT("workbenchChat.zoomOut", "Zoom out")}>−</button>
          <button type="button" onClick={function () { applyOfficeZoom(100); }} title={wbcT("workbenchChat.fitWidth", "Fit width")}>{zoomPercent + "%"}</button>
          <button type="button" onClick={function () { applyOfficeZoom(zoomRef.current + 25); }} aria-label={wbcT("workbenchChat.zoomIn", "Zoom in")}>+</button>
        </span>
      )}
      <div ref={containerRef} className="wbc-office-render-surface wbc-gesture-zoom" />
      {loading && (
        <div className="wbc-office-viewer-state" role="status">
          <span className="wbc-pdf-analysis-spinner" aria-hidden="true" />
          <span>{wbcT("workbenchChat.officeLoading", "Preparing Office preview...")}</span>
        </div>
      )}
      {!loading && failureCode && (
        <div className="wbc-office-viewer-state is-error" role="alert">
          <p>{failureText}</p>
          <small>{wbcT("workbenchChat.officeDownloadFallback", "You can still download or open the original file.")}</small>
        </div>
      )}
    </div>
  );
}

// ---- side viewer (PDF / Office / HTML / Markdown / 代码 / 图片) -------------

function WbcMarkdownRenderedEditor({ value, onChange, onSave, onLinkClick, containerRef }) {
  var localRef = useWbcRef(null);
  var lastEmittedRef = useWbcRef(null);

  function editorNode() {
    return localRef.current;
  }

  function applyRenderedValue(node, markdown) {
    if (!node) return;
    node.innerHTML = wbcRenderMarkdown(markdown);
    Array.prototype.forEach.call(node.querySelectorAll("[data-wbc-source]"), function (block) {
      block.setAttribute("contenteditable", "false");
      block.setAttribute("title", wbcT("workbenchChat.editorProtectedBlock", "Edit this block in source mode"));
    });
  }

  useWbcLayoutEffect(function () {
    var node = editorNode();
    if (!node) return;
    if (lastEmittedRef.current === value) return;
    if (node.contains(document.activeElement)) return;
    applyRenderedValue(node, value);
  }, [value]);

  useWbcEffect(function () {
    if (containerRef) containerRef.current = editorNode();
    return function () { if (containerRef) containerRef.current = null; };
  }, []);

  function emitMarkdown() {
    var node = editorNode();
    var converter = window.CyreneCodeMirror && window.CyreneCodeMirror.markdownFromElement;
    if (!node || !converter) return;
    var markdown = converter(node);
    lastEmittedRef.current = markdown;
    if (onChange) onChange(markdown);
  }

  function insertPlainText(text) {
    if (document.execCommand && document.execCommand("insertText", false, text)) return;
    var selection = window.getSelection && window.getSelection();
    if (!selection || !selection.rangeCount) return;
    var range = selection.getRangeAt(0);
    range.deleteContents();
    var node = document.createTextNode(text);
    range.insertNode(node);
    range.setStartAfter(node);
    range.collapse(true);
    selection.removeAllRanges();
    selection.addRange(range);
    emitMarkdown();
  }

  return (
    <div
      ref={localRef}
      className="wbc-markdown-rendered-editor wbc-viewer-md wbc-msg-body markdown"
      contentEditable="true"
      suppressContentEditableWarning={true}
      spellCheck="true"
      role="textbox"
      aria-multiline="true"
      aria-label={wbcT("workbenchChat.editorRenderedLabel", "Rendered Markdown editor")}
      data-placeholder={wbcT("workbenchChat.editorRenderedPlaceholder", "Start writing…")}
      onClick={onLinkClick}
      onInput={emitMarkdown}
      onBlur={emitMarkdown}
      onPaste={function (event) {
        event.preventDefault();
        insertPlainText(String(event.clipboardData && event.clipboardData.getData("text/plain") || ""));
      }}
      onKeyDown={function (event) {
        if ((event.metaKey || event.ctrlKey) && String(event.key).toLowerCase() === "s") {
          event.preventDefault();
          if (onSave) onSave();
        }
      }}
    />
  );
}

function wbcUpdateProjectFileDraft(draftKey, content, baseContent, version, dirty) {
  if (!draftKey) return;
  if (!dirty) {
    delete WBC_PROJECT_FILE_DRAFTS[draftKey];
    return;
  }
  WBC_PROJECT_FILE_DRAFTS[draftKey] = {
    content: content,
    baseContent: baseContent,
    version: version,
  };
}

function WbcViewerTab({ file, onViewed, hideHeader, htmlMode: controlledHtmlMode, onHtmlModeChange, markdownMode: controlledMarkdownMode, onMarkdownModeChange, onDirtyChange }) {
  var kind = wbcFileViewKind(file);
  var [pluginSnapshot, setPluginSnapshot] = useWbcState(function () { return PluginFrontendService.snapshot(); });
  useWbcEffect(function () { return PluginFrontendService.subscribe(setPluginSnapshot); }, []);
  var contributedFileType = pluginSnapshot.loaded ? PluginFrontendService.fileTypeFor(
    file && (file.path || file.name),
    file && (file.content_type || file.contentType)
  ) : null;
  if (kind === "download" && contributedFileType && contributedFileType.editable === true) kind = "code";
  var [text, setText] = useWbcState("");
  var [localHtmlMode, setLocalHtmlMode] = useWbcState("rendered");
  var htmlMode = controlledHtmlMode || localHtmlMode;
  function setHtmlMode(next) {
    setLocalHtmlMode(next);
    if (onHtmlModeChange) onHtmlModeChange(next);
  }
  var [localMarkdownMode, setLocalMarkdownMode] = useWbcState("rendered");
  var markdownMode = controlledMarkdownMode || localMarkdownMode;
  function setMarkdownMode(next) {
    setLocalMarkdownMode(next);
    if (onMarkdownModeChange) onMarkdownModeChange(next);
  }
  var [failed, setFailed] = useWbcState(false);
  var [textLoading, setTextLoading] = useWbcState(false);
  var [editorDirty, setEditorDirty] = useWbcState(false);
  var [editorSaving, setEditorSaving] = useWbcState(false);
  var [editorConflict, setEditorConflict] = useWbcState(null);
  var [editorAutoSavePaused, setEditorAutoSavePaused] = useWbcState(false);
  var editorBaseTextRef = useWbcRef("");
  var editorVersionRef = useWbcRef("");
  var editorTextRef = useWbcRef("");
  var editorDirtyRef = useWbcRef(false);
  var editorSavingRef = useWbcRef(false);
  var [imageZoom, setImageZoom] = useWbcState(100);
  var imageZoomRef = useWbcRef(100);
  var imagePinchRef = useWbcRef({ distance: 0, zoom: 100 });
  var imageSurfaceRef = useWbcRef(null);
  var codeRef = useWbcRef(null);
  var markdownRef = useWbcRef(null);
  var viewedRef = useWbcRef("");
  var url = file && file.url;
  var editUrl = wbcCanEditProjectTextFile(file) ? wbcProjectFileEditUrl(file) : "";
  var draftKey = wbcProjectFileDraftKey(file);
  function confirmViewed() {
    var key = String(url || "") + "::" + String(file && file.name || "");
    if (!key || viewedRef.current === key) return;
    viewedRef.current = key;
    if (onViewed) onViewed(file);
  }
  var htmlPreview = useWbcMemo(function () {
    return kind === "html" ? wbcHtmlPreviewDocument(text, url) : "";
  }, [text, url, kind]);

  function publishEditorDirty(next) {
    editorDirtyRef.current = next;
    setEditorDirty(next);
    if (onDirtyChange) onDirtyChange(next);
  }

  function applyLoadedEditor(payload, preserveDraft) {
    var savedContent = String(payload && payload.content || "");
    var nextVersion = String(payload && payload.version || "");
    var draft = preserveDraft && draftKey ? WBC_PROJECT_FILE_DRAFTS[draftKey] : null;
    editorVersionRef.current = draft && draft.version ? draft.version : nextVersion;
    editorBaseTextRef.current = draft ? String(draft.baseContent || "") : savedContent;
    var nextText = draft ? String(draft.content || "") : savedContent;
    editorTextRef.current = nextText;
    setText(nextText);
    publishEditorDirty(nextText !== editorBaseTextRef.current);
    setEditorConflict(null);
    setEditorAutoSavePaused(false);
  }

  function readEditorResponse(response) {
    return response.json().catch(function () { return {}; }).then(function (payload) {
      if (!response.ok) {
        var error = new Error(payload.error || ("HTTP " + response.status));
        error.status = response.status;
        error.payload = payload;
        throw error;
      }
      return payload;
    });
  }

  // text-ish contents are fetched (PDF is handled by WbcPdfJsViewer)
  useWbcEffect(function () {
    setText("");
    setFailed(false);
    setTextLoading(false);
    setEditorSaving(false);
    editorSavingRef.current = false;
    setEditorConflict(null);
    setEditorAutoSavePaused(false);
    editorBaseTextRef.current = "";
    editorVersionRef.current = "";
    editorTextRef.current = "";
    publishEditorDirty(false);
    imageZoomRef.current = 100;
    setImageZoom(100);
    setHtmlMode("rendered");
    setMarkdownMode("rendered");
    if (!url) return;
    var cancelled = false;
    if (kind === "html" || kind === "markdown" || kind === "code") {
      setTextLoading(true);
      var request = editUrl
        ? fetch(editUrl, { cache: "no-store" }).then(readEditorResponse)
        : fetch(url, { cache: "no-store" }).then(function (r) {
            if (!r.ok) throw new Error("HTTP " + r.status);
            return r.text();
          });
      request.then(function (body) {
        if (!cancelled) {
          if (editUrl) applyLoadedEditor(body, true);
          else {
            editorTextRef.current = body;
            setText(body);
          }
          confirmViewed();
        }
      }).catch(function (error) {
        if (!cancelled) {
          setFailed(true);
        }
      }).finally(function () { if (!cancelled) setTextLoading(false); });
    }
    return function () { cancelled = true; };
  }, [url, kind, editUrl]);

  useWbcEffect(function () {
    if (!editorDirty) return undefined;
    function warnBeforeUnload(event) {
      event.preventDefault();
      event.returnValue = "";
    }
    window.addEventListener("beforeunload", warnBeforeUnload);
    return function () { window.removeEventListener("beforeunload", warnBeforeUnload); };
  }, [editorDirty]);

  useWbcEffect(function () {
    if (!editUrl || !file || !file.projectId || !file.path) return undefined;
    function onWorkspaceFileChanged(event) {
      var detail = event && event.detail || {};
      if (String(detail.projectId || "") !== String(file.projectId || "")
        || String(detail.path || "") !== String(file.path || "")) return;
      if (editorDirtyRef.current) {
        setEditorConflict({ external: true, version: String(detail.version || "") });
        setEditorAutoSavePaused(true);
        return;
      }
      reloadEditor();
    }
    window.addEventListener("cyrene:workspace-file-changed", onWorkspaceFileChanged);
    return function () { window.removeEventListener("cyrene:workspace-file-changed", onWorkspaceFileChanged); };
  }, [editUrl, file && file.projectId, file && file.path]);

  useWbcEffect(function () {
    if (!editUrl || textLoading || !editorDirty || editorSaving || editorConflict || editorAutoSavePaused) return undefined;
    var timer = window.setTimeout(function () { saveEditor(false); }, 650);
    return function () { window.clearTimeout(timer); };
  }, [text, editUrl, textLoading, editorDirty, editorSaving, editorConflict, editorAutoSavePaused]);

  // syntax highlight code once loaded
  useWbcEffect(function () {
    if (kind === "code" && text && codeRef.current && window.hljs) {
      try { window.hljs.highlightElement(codeRef.current); } catch (e) {}
    }
  }, [text, kind]);

  useWbcEffect(function () {
    if (kind !== "markdown" || !markdownRef.current) return;
    var container = markdownRef.current;
    var usedIds = Object.create(null);
    Array.prototype.forEach.call(container.querySelectorAll("h1,h2,h3,h4,h5,h6"), function (heading) {
      var base = String(heading.id || heading.textContent || "")
        .trim()
        .toLowerCase()
        .replace(/[\s]+/g, "-")
        .replace(/[^\p{L}\p{N}_-]+/gu, "")
        .replace(/-+/g, "-")
        .replace(/^-|-$/g, "") || "section";
      var id = base;
      var suffix = 1;
      while (usedIds[id]) { id = base + "-" + suffix; suffix += 1; }
      usedIds[id] = true;
      heading.id = id;
    });
    Array.prototype.forEach.call(container.querySelectorAll("a[href]"), function (anchor) {
      if (/^https?:\/\//i.test(String(anchor.getAttribute("href") || ""))) {
        anchor.setAttribute("target", "_blank");
        anchor.setAttribute("rel", "noopener noreferrer");
      }
    });
  }, [text, kind, markdownMode]);

  function handleMarkdownLink(event) {
    var anchor = event.target && event.target.closest ? event.target.closest("a[href]") : null;
    if (!anchor || !markdownRef.current || !markdownRef.current.contains(anchor)) return;
    var href = String(anchor.getAttribute("href") || "").trim();
    if (href.charAt(0) !== "#") return;
    event.preventDefault();
    event.stopPropagation();
    var id = href.slice(1);
    try { id = decodeURIComponent(id); } catch (e) {}
    var target = Array.prototype.find.call(markdownRef.current.querySelectorAll("[id]"), function (node) {
      return node.id === id;
    });
    if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function handleEditorChange(nextText) {
    var next = String(nextText == null ? "" : nextText);
    var dirty = next !== editorBaseTextRef.current;
    editorTextRef.current = next;
    setText(next);
    publishEditorDirty(dirty);
    setEditorAutoSavePaused(false);
    wbcUpdateProjectFileDraft(
      draftKey, next, editorBaseTextRef.current, editorVersionRef.current, dirty
    );
  }

  function saveEditor(force) {
    if (!editUrl || editorSavingRef.current) return Promise.resolve(false);
    if (!editorDirtyRef.current && !force) return Promise.resolve(true);
    var contentToSave = editorTextRef.current;
    editorSavingRef.current = true;
    setEditorSaving(true);
    return fetch(editUrl, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        content: contentToSave,
        expectedVersion: editorVersionRef.current,
        force: !!force,
      }),
    }).then(readEditorResponse).then(function (payload) {
      var persistedContent = typeof payload.content === "string" ? payload.content : contentToSave;
      editorVersionRef.current = String(payload.version || "");
      var latestText = editorTextRef.current;
      if (latestText === contentToSave && latestText !== persistedContent) {
        latestText = persistedContent;
        editorTextRef.current = persistedContent;
        setText(persistedContent);
      }
      editorBaseTextRef.current = persistedContent;
      var stillDirty = latestText !== persistedContent;
      publishEditorDirty(stillDirty);
      setEditorConflict(null);
      setEditorAutoSavePaused(false);
      wbcUpdateProjectFileDraft(
        draftKey, latestText, persistedContent, editorVersionRef.current, stillDirty
      );
      return true;
    }).catch(function (error) {
      setEditorAutoSavePaused(true);
      if (error.status === 409 && error.payload) {
        setEditorConflict(error.payload);
        var feedback = workbenchServices.feedback();
        var request = feedback.confirmModal({
          title: wbcT("workbenchChat.editorConflictShort", "Conflict"),
          body: wbcT("workbenchChat.editorAutoSaveConflict", "The file changed outside the editor. Overwrite it with your version, or reload the external version?"),
          confirmLabel: wbcT("workbenchChat.editorOverwrite", "Overwrite"),
          cancelLabel: wbcT("workbenchChat.editorReload", "Reload latest"),
          danger: true,
        });
        Promise.resolve(request).then(function (overwrite) {
          if (overwrite) saveEditor(true);
          else reloadEditor();
        });
      } else {
        workbenchServices.feedback().showToast(
          error.message || wbcT("workbenchChat.editorSaveFailed", "Save failed"),
          "error"
        );
      }
      return false;
    }).finally(function () {
      editorSavingRef.current = false;
      setEditorSaving(false);
    });
  }

  function reloadEditor() {
    if (!editUrl || editorSavingRef.current) return;
    editorSavingRef.current = true;
    setEditorSaving(true);
    fetch(editUrl, { cache: "no-store" }).then(readEditorResponse).then(function (payload) {
      if (draftKey) delete WBC_PROJECT_FILE_DRAFTS[draftKey];
      applyLoadedEditor(payload, false);
      setFailed(false);
    }).catch(function (error) {
      workbenchServices.feedback().showToast(
        error.message || wbcT("workbenchChat.viewerLoadFailed", "File failed to load."),
        "error"
      );
    }).finally(function () {
      editorSavingRef.current = false;
      setEditorSaving(false);
    });
  }

  function applyImageZoom(nextZoom, clientX, clientY) {
    var next = Math.max(100, Math.min(400, Number(nextZoom) || 100));
    var restoreAnchor = wbcZoomAnchorRestorer(imageSurfaceRef.current, imageZoomRef.current / 100, clientX, clientY);
    imageZoomRef.current = next;
    setImageZoom(Math.round(next));
    restoreAnchor(next / 100);
  }
  function handleImageWheel(event) {
    if (!event.ctrlKey && !event.metaKey) return;
    event.preventDefault();
    applyImageZoom(imageZoomRef.current * Math.exp(-event.deltaY * 0.01), event.clientX, event.clientY);
  }
  function handleImageTouchStart(event) {
    if (event.touches.length !== 2) return;
    var dx = event.touches[0].clientX - event.touches[1].clientX;
    var dy = event.touches[0].clientY - event.touches[1].clientY;
    imagePinchRef.current = { distance: Math.hypot(dx, dy), zoom: imageZoomRef.current };
  }
  function handleImageTouchMove(event) {
    if (event.touches.length !== 2 || !imagePinchRef.current.distance) return;
    event.preventDefault();
    var dx = event.touches[0].clientX - event.touches[1].clientX;
    var dy = event.touches[0].clientY - event.touches[1].clientY;
    applyImageZoom(
      imagePinchRef.current.zoom * Math.hypot(dx, dy) / imagePinchRef.current.distance,
      (event.touches[0].clientX + event.touches[1].clientX) / 2,
      (event.touches[0].clientY + event.touches[1].clientY) / 2
    );
  }

  useWbcEffect(function () {
    var surface = kind === "image" ? imageSurfaceRef.current : null;
    if (!surface) return undefined;
    surface.addEventListener("wheel", handleImageWheel, { passive: false });
    surface.addEventListener("touchstart", handleImageTouchStart, { passive: true });
    surface.addEventListener("touchmove", handleImageTouchMove, { passive: false });
    return function () {
      surface.removeEventListener("wheel", handleImageWheel);
      surface.removeEventListener("touchstart", handleImageTouchStart);
      surface.removeEventListener("touchmove", handleImageTouchMove);
    };
  }, [kind, url]);

  if (!file) return <p className="workbench-muted">{wbcT("workbenchChat.viewerEmpty", "Select a file from message attachments or artifacts.")}</p>;

  // PDF is handled entirely by its own component — skip the wrapper.
  if (kind === "pdf") {
    return <WbcPdfJsViewer file={file} url={url} onViewed={confirmViewed} />;
  }
  if (kind === "docx" || kind === "pptx") {
    return <WbcOfficeViewer key={url} file={file} url={url} kind={kind} onViewed={confirmViewed} />;
  }

  var head = (
    <div className="wbc-viewer-head">
      <span className="wbc-viewer-name" title={file.name}>{file.name || "file"}</span>
      {kind === "html" && (
        <span className="wbc-viewer-switch">
          <button type="button" className={htmlMode === "rendered" ? "active" : ""} onClick={function () { setHtmlMode("rendered"); }}>{wbcT("workbenchChat.viewerRendered", "Rendered")}</button>
          <button type="button" className={htmlMode === "source" ? "active" : ""} onClick={function () { setHtmlMode("source"); }}>{wbcT("workbenchChat.viewerSource", "Source")}</button>
        </span>
      )}
      {kind === "markdown" && editUrl && (
        <span className="wbc-viewer-switch">
          <button type="button" className={markdownMode === "rendered" ? "active" : ""} onClick={function () { setMarkdownMode("rendered"); }}>{wbcT("workbenchChat.viewerRendered", "Rendered")}</button>
          <button type="button" className={markdownMode === "source" ? "active" : ""} onClick={function () { setMarkdownMode("source"); }}>{wbcT("workbenchChat.viewerSource", "Source")}</button>
        </span>
      )}
      {kind === "image" && (
        <span className="wbc-viewer-switch wbc-viewer-zoom">
          <button type="button" disabled={imageZoom <= 100} onClick={function () { applyImageZoom(imageZoomRef.current - 25); }} aria-label={wbcT("workbenchChat.zoomOut", "Zoom out")}>−</button>
          <button type="button" onClick={function () { applyImageZoom(100); }} title={wbcT("workbenchChat.fitWidth", "Fit width")}>{imageZoom + "%"}</button>
          <button type="button" onClick={function () { applyImageZoom(imageZoomRef.current + 25); }} aria-label={wbcT("workbenchChat.zoomIn", "Zoom in")}>+</button>
        </span>
      )}
      {wbcCanOpenExternally(file) ? <a className="wbc-viewer-open" href={url} target="_blank" rel="noreferrer" title={wbcT("workbenchChat.viewerOpenExternal", "Open in a new window")}>↗</a> : null}
      {wbcDownloadLink(file, { className: "wbc-viewer-download" })}
    </div>
  );

  var editorBody = null;
  if (editUrl && (kind === "markdown" || kind === "code" || kind === "html") && !failed && !textLoading) {
    var CodeMirrorEditor = window.CyreneCodeMirror && window.CyreneCodeMirror.Editor;
    var editorControl = CodeMirrorEditor
      ? <CodeMirrorEditor
          file={file}
          value={text}
          onChange={handleEditorChange}
          onSave={function () { saveEditor(false); }}
          ariaLabel={wbcT("workbenchChat.editorLabel", "Project file editor")}
        />
      : <textarea className="wbc-text-editor-fallback" value={text} onChange={function (event) { handleEditorChange(event.target.value); }} spellCheck="false" />;
    editorBody = kind === "markdown"
      ? (markdownMode === "rendered"
          ? <WbcMarkdownRenderedEditor value={text} onChange={handleEditorChange} onSave={function () { saveEditor(false); }} onLinkClick={handleMarkdownLink} containerRef={markdownRef} />
          : <div className="wbc-text-editor-surface">{editorControl}</div>)
      : <div className="wbc-text-editor-surface">{editorControl}</div>;
    editorBody = <div className="wbc-text-editor">{editorBody}</div>;
  }

  var body = null;
  if (textLoading) {
    body = <div className="wbc-text-editor-loading" role="status"><span className="wbc-spinner" aria-hidden="true" />{wbcT("workbenchChat.editorLoading", "Loading editor…")}</div>;
  } else if (failed) {
    body = <p className="workbench-muted wbc-viewer-pad">{wbcT("workbenchChat.viewerLoadFailed", "File failed to load.")}{url ? " " + wbcT("workbenchChat.viewerOpenFallback", "Try opening it in a new window.") : ""}</p>;
  } else if (kind === "image") {
    body = <div ref={imageSurfaceRef} className="wbc-viewer-scroll is-image wbc-gesture-zoom"><div className="wbc-viewer-image-stage" style={{ width: imageZoom + "%", height: imageZoom + "%" }}><img className="wbc-viewer-img" src={url} alt={file.name || "image"} decoding="async" onLoad={confirmViewed} onError={function () { setFailed(true); }} /></div></div>;
  } else if (kind === "audio") {
    body = <div className="wbc-viewer-media-wrap"><audio className="wbc-viewer-media" src={url} controls onCanPlay={confirmViewed} /></div>;
  } else if (kind === "video") {
    body = <div className="wbc-viewer-media-wrap"><video className="wbc-viewer-media" src={url} controls onCanPlay={confirmViewed} /></div>;
  } else if (kind === "html") {
    body = editUrl && htmlMode === "source"
      ? editorBody
      : htmlMode === "rendered"
      ? <iframe key={url + "::" + (text ? "1" : "0")} className="wbc-viewer-iframe" sandbox="allow-scripts" srcDoc={htmlPreview} title={file.name || "HTML"} />
      : <pre className="wbc-viewer-pre">{text}</pre>;
  } else if (editorBody) {
    body = editorBody;
  } else if (kind === "markdown") {
    body = <div ref={markdownRef} className="wbc-viewer-md wbc-msg-body markdown" onClick={handleMarkdownLink} dangerouslySetInnerHTML={{ __html: wbcRenderMarkdown(text) }} />;
  } else if (kind === "code") {
    body = <pre className="wbc-viewer-pre"><code ref={codeRef}>{text}</code></pre>;
  } else {
    body = (
      <div className="wbc-viewer-unsupported" role="status">
        <div className="wbc-viewer-unsupported-content">
          <WbcFileVisual file={file} className="wbc-file-visual wbc-viewer-unsupported-icon" />
          <div className="wbc-viewer-unsupported-copy">
            <p className="wbc-viewer-unsupported-title">{wbcT("workbenchChat.viewerUnsupported", "Preview is not supported for this file type.")}</p>
            <p className="wbc-viewer-unsupported-hint">{wbcT("workbenchChat.viewerUnsupportedHint", "You can still open it with an app that supports this format.")}</p>
          </div>
          {url ? (
            <a className="wb-btn tonal wbc-viewer-unsupported-action" href={url} target="_blank" rel="noreferrer">
              {WBC_ICONS.openExternal}
              <span>{wbcT("workbenchChat.viewerOpenExternal", "Open in a new window")}</span>
            </a>
          ) : null}
        </div>
      </div>
    );
  }

  return (
    <div className="wbc-viewer">
      {(!hideHeader || kind === "image") && head}
      {editorConflict ? <div className="wbc-editor-external-conflict" role="status">
        <span>{wbcT("workbenchChat.editorExternalChange", "This file changed outside the editor.")}</span>
        <button type="button" onClick={reloadEditor}>{wbcT("workbenchChat.editorReload", "Reload latest")}</button>
        <button type="button" onClick={function () { setEditorConflict(null); setEditorAutoSavePaused(true); }}>{wbcT("workbenchChat.editorKeepDraft", "Keep draft")}</button>
      </div> : null}
      {body}
    </div>
  );
}

// ---- side map (pin_location / connect_pins 结果) ----------------------------

// WGS-84 → GCJ-02 (火星坐标) — AMap tiles use GCJ-02, so raw WGS pins must be
// shifted or they land ~500m off. Same math as the legacy map view.
function wbcWgs84ToGcj02(wgsLat, wgsLng) {
  if (wgsLng < 72.004 || wgsLng > 137.8347 || wgsLat < 0.8293 || wgsLat > 55.8271) return [wgsLat, wgsLng];
  var pi = 3.1415926535897932384626, a = 6378245.0, ee = 0.00669342162296594323;
  function tLat(x, y) {
    var r = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x));
    r += (20.0 * Math.sin(6.0 * x * pi) + 20.0 * Math.sin(2.0 * x * pi)) * 2.0 / 3.0;
    r += (20.0 * Math.sin(y * pi) + 40.0 * Math.sin(y / 3.0 * pi)) * 2.0 / 3.0;
    r += (160.0 * Math.sin(y / 12.0 * pi) + 320.0 * Math.sin(y * pi / 30.0)) * 2.0 / 3.0;
    return r;
  }
  function tLng(x, y) {
    var r = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x));
    r += (20.0 * Math.sin(6.0 * x * pi) + 20.0 * Math.sin(2.0 * x * pi)) * 2.0 / 3.0;
    r += (20.0 * Math.sin(x * pi) + 40.0 * Math.sin(x / 3.0 * pi)) * 2.0 / 3.0;
    r += (150.0 * Math.sin(x / 12.0 * pi) + 300.0 * Math.sin(x / 30.0 * pi)) * 2.0 / 3.0;
    return r;
  }
  var dlat = tLat(wgsLng - 105.0, wgsLat - 35.0);
  var dlng = tLng(wgsLng - 105.0, wgsLat - 35.0);
  var radlat = wgsLat / 180.0 * pi;
  var magic = Math.sin(radlat);
  magic = 1 - ee * magic * magic;
  var sqrtmagic = Math.sqrt(magic);
  dlat = (dlat * 180.0) / ((a * (1 - ee)) / (magic * sqrtmagic) * pi);
  dlng = (dlng * 180.0) / (a / sqrtmagic * Math.cos(radlat) * pi);
  return [wgsLat + dlat, wgsLng + dlng];
}

// Same provider setting as the legacy map ("direct" = CARTO, "amap" = 高德).
function wbcMapProvider() {
  try { return localStorage.getItem("cyrene-tweak-map-provider") || "direct"; } catch (e) { return "direct"; }
}

function wbcTileConfig(provider) {
  var isDark = document.documentElement.dataset.theme === "dark";
  if (provider === "amap") {
    return {
      url: "https://webrd01.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=" + (isDark ? 8 : 7) + "&x={x}&y={y}&z={z}",
      options: { keepBuffer: 4, updateWhenZooming: false, updateWhenIdle: true },
    };
  }
  return {
    url: "https://{s}.basemaps.cartocdn.com/" + (isDark ? "dark_all" : "light_all") + "/{z}/{x}/{y}{r}.png",
    options: { subdomains: "abcd", keepBuffer: 4, updateWhenZooming: false, updateWhenIdle: true },
  };
}

function WbcMapTab({ chatId, focusItem }) {
  var holderRef = useWbcRef(null);
  var mapRef = useWbcRef(null);
  var layerRef = useWbcRef(null);
  var tileRef = useWbcRef(null);
  var switchedRef = useWbcRef(false);
  var [provider, setProvider] = useWbcState(wbcMapProvider());
  var mapData = useWbcMapData(chatId);
  var data = mapData.loading ? null : mapData;

  useWbcEffect(function () {
    if (!window.L || !holderRef.current || mapRef.current) return;
    var L = window.L;
    var map = L.map(holderRef.current, { zoomControl: true, attributionControl: false }).setView([35, 105], 4);
    mapRef.current = map;
    layerRef.current = L.layerGroup().addTo(map);
    var frame = 0;
    var invalidate = function () {
      if (document.body.classList.contains("wbc-resizing-side-agent")) return;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(function () {
        try { map.invalidateSize({ pan: false, animate: false }); } catch (e) {}
      });
    };
    var observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(invalidate) : null;
    if (observer) observer.observe(holderRef.current);
    window.addEventListener("resize", invalidate);
    window.addEventListener("workbench:split-resize-end", invalidate);
    var settleTimer = setTimeout(invalidate, 560);
    invalidate();
    return function () {
      clearTimeout(settleTimer);
      cancelAnimationFrame(frame);
      if (observer) observer.disconnect();
      window.removeEventListener("resize", invalidate);
      window.removeEventListener("workbench:split-resize-end", invalidate);
      try { map.remove(); } catch (e) {}
      mapRef.current = null;
      layerRef.current = null;
      tileRef.current = null;
    };
  }, []);

  // (Re)mount the tile layer per provider; on repeated tile failures fall back
  // to the other provider once (e.g. CARTO unreachable → 高德, and vice versa).
  useWbcEffect(function () {
    var map = mapRef.current;
    if (!map || !window.L) return;
    var L = window.L;
    if (tileRef.current) { try { map.removeLayer(tileRef.current); } catch (e) {} }
    var config = wbcTileConfig(provider);
    var errors = 0;
    var tiles = L.tileLayer(config.url, config.options);
    tiles.on("tileerror", function () {
      errors += 1;
      if (errors >= 3 && !switchedRef.current) {
        switchedRef.current = true;
        setProvider(provider === "amap" ? "direct" : "amap");
      }
    });
    tiles.addTo(map);
    tileRef.current = tiles;
  }, [provider]);

  // Render pins + routes; AMap needs GCJ-02 coordinates.
  useWbcEffect(function () {
    var layer = layerRef.current;
    if (!layer || !window.L || !data) return;
    var L = window.L;
    layer.clearLayers();
    var pins = Array.isArray(data.pins) ? data.pins : [];
    var routes = Array.isArray(data.routes) ? data.routes : [];
    var convert = provider === "amap"
      ? function (lat, lng) { return wbcWgs84ToGcj02(lat, lng); }
      : function (lat, lng) { return [lat, lng]; };
    var byName = {};
    var latlngs = [];
    var focusKey = wbcMapItemKey(focusItem);
    pins.forEach(function (pin) {
      var lat = Number(pin.lat), lng = Number(pin.lng);
      if (!isFinite(lat) || !isFinite(lng)) return;
      var pos = convert(lat, lng);
      byName[String(pin.name || "")] = pos;
      latlngs.push(pos);
      var selected = focusKey === wbcMapItemKey(Object.assign({ kind: "pin" }, pin));
      var marker = L.circleMarker(pos, {
        radius: selected ? 8 : 5,
        color: selected ? "#dff8ea" : "#ffffff",
        weight: selected ? 3 : 2,
        fillColor: "#22a861",
        fillOpacity: 0.96,
      }).addTo(layer);
      var note = String(pin.note || pin.note_md || "").trim();
      var popup = document.createElement("div");
      popup.className = "wbc-map-popup";
      var title = document.createElement("strong");
      title.className = "wbc-map-popup-title";
      title.textContent = String(pin.name || "");
      popup.appendChild(title);
      if (note) {
        var body = document.createElement("div");
        body.className = "wbc-map-popup-markdown markdown";
        var noteHtml = wbcRenderMapMarkdown(note)
          .replace(/\*\*([^*<]+)\*\*/g, "<strong>$1</strong>")
          .replace(/\\n/g, "<br>");
        body.innerHTML = noteHtml;
        popup.appendChild(body);
      }
      marker.bindPopup(popup, { maxWidth: 340, minWidth: 210 });
    });
    routes.forEach(function (route) {
      var from = byName[String(route.from_name || route.from || "")];
      var to = byName[String(route.to_name || route.to || "")];
      if (!from || !to) return;
      var selected = focusKey === wbcMapItemKey(Object.assign({ kind: "route" }, route));
      var line = L.polyline([from, to], { color: "#22a861", weight: selected ? 5 : 3, opacity: selected ? 1 : 0.78, dashArray: selected ? "" : "6 6" }).addTo(layer);
      var label = [route.transport, route.route_note].filter(Boolean).join(" · ");
      if (label) line.bindPopup(String(label).replace(/</g, "&lt;"));
    });
    if (latlngs.length && mapRef.current) {
      try { mapRef.current.fitBounds(latlngs, { padding: [28, 28], maxZoom: 12 }); } catch (e) {}
    }
  }, [data, provider, wbcMapItemKey(focusItem)]);

  useWbcEffect(function () {
    if (!focusItem || !data || !mapRef.current) return;
    var pins = Array.isArray(data.pins) ? data.pins : [];
    var convert = provider === "amap"
      ? function (lat, lng) { return wbcWgs84ToGcj02(lat, lng); }
      : function (lat, lng) { return [lat, lng]; };
    var targets = [];
    if (focusItem.kind === "route") {
      [focusItem.from_name || focusItem.from, focusItem.to_name || focusItem.to].forEach(function (name) {
        var pin = pins.find(function (candidate) { return String(candidate.name || "") === String(name || ""); });
        if (pin && isFinite(Number(pin.lat)) && isFinite(Number(pin.lng))) targets.push(convert(Number(pin.lat), Number(pin.lng)));
      });
    } else if (isFinite(Number(focusItem.lat)) && isFinite(Number(focusItem.lng))) {
      targets.push(convert(Number(focusItem.lat), Number(focusItem.lng)));
    }
    try {
      if (targets.length > 1) mapRef.current.fitBounds(targets, { padding: [48, 48], maxZoom: 11 });
      else if (targets.length === 1) mapRef.current.setView(targets[0], 12);
    } catch (e) {}
  }, [wbcMapItemKey(focusItem), data, provider]);

  var empty = data && (!Array.isArray(data.pins) || data.pins.length === 0);

  return (
    <div className="wbc-map">
      <div className="wbc-map-holder" ref={holderRef}></div>
      {empty && <div className="wbc-map-empty">{wbcT("workbenchChat.mapEmpty", "No map pins in this chat yet.")}</div>}
    </div>
  );
}

export { WbcMapTab, WbcViewerTab }
