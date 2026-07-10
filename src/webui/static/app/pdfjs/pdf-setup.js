(function () {
  // ---- PDFViewer setup ----
  // Creates the container DOM and constructs a PDFViewer.
  // Returns { viewer, eventBus }
  window.pdfjsSetupViewer = function pdfjsSetupViewer(container) {
    container.innerHTML = '';
    container.style.overflow = 'auto';
    container.style.position = 'absolute';
    container.style.inset = '0';
    var viewerEl = document.createElement('div');
    viewerEl.className = 'pdfViewer';
    viewerEl.style.height = '100%';
    viewerEl.style.width = '100%';
    container.appendChild(viewerEl);

    var eventBus = new window.pdfjsViewer.EventBus();
    var viewer = new window.pdfjsViewer.PDFViewer({
      container: container,
      viewer: viewerEl,
      eventBus: eventBus,
      imageResourcesPath: '/static/app/pdfjs/images/',
      textLayerMode: 1,
      annotationMode: window.pdfjsLib.AnnotationMode.ENABLE,
    });
    return { viewer: viewer, eventBus: eventBus };
  };

  // ---- Fetch + load PDF document ----
  // Fetches the PDF at `url` and loads it into `viewer`.
  // `signal` — optional AbortSignal to cancel the fetch.
  // Returns a promise that resolves with the pdf document (numPages, etc.)
  window.pdfjsLoadPdf = function pdfjsLoadPdf(url, viewer, signal) {
    return fetch(url, { signal: signal })
      .then(function (r) {
        if (!r.ok) throw new Error('Fetch: HTTP ' + r.status);
        return r.arrayBuffer();
      })
      .then(function (buf) {
        if (!buf || buf.byteLength === 0) throw new Error('Empty PDF data');
        return window.pdfjsLib.getDocument({ data: buf }).promise;
      })
      .then(function (doc) {
        viewer.setDocument(doc);
        viewer.currentScaleValue = 'page-width';
        return doc;
      });
  };

  window.pdfjsGetSelectedText = function pdfjsGetSelectedText(container) {
    var selection = document.getSelection();
    return selection ? selection.toString() : '';
  };

  // Some review/manuscript PDFs inject tiny line numbers after every text
  // line. Those text objects sit in the column gutter and become part of the
  // browser range, even though the pointer never entered the next column.
  // Remove only those tiny numeric objects (and their leading spacer) from the
  // transparent text layer; the canvas-rendered PDF remains unchanged.
  window.pdfjsInstallSelectionSanitizer = function pdfjsInstallSelectionSanitizer(container, viewer, eventBus) {
    var ctrl = new AbortController();

    function median(values) {
      if (!values.length) return 0;
      values.sort(function (a, b) { return a - b; });
      var middle = Math.floor(values.length / 2);
      return values.length % 2 ? values[middle] : (values[middle - 1] + values[middle]) / 2;
    }

    function sanitizePage(pageNumber) {
      var pageView = (viewer._pages || [])[pageNumber - 1];
      var page = pageView && pageView.div;
      var highlighter = pageView && (pageView._textHighlighter || (pageView.textLayer && pageView.textLayer.highlighter));
      if (!page || !highlighter) return;
      var divs = highlighter.textDivs || [];
      var strings = highlighter.textContentItemsStr || [];
      var pageRect = page.getBoundingClientRect();
      var normalSizes = [];
      for (var i = 0; i < divs.length; i++) {
        var value = strings[i] || '';
        if (value.trim().length >= 3) normalSizes.push(parseFloat(getComputedStyle(divs[i]).fontSize) || 0);
      }
      var normalSize = median(normalSizes.filter(function (size) { return size > 0; }));
      if (!normalSize) return;

      for (var j = 0; j < divs.length; j++) {
        var text = strings[j] || '';
        if (!/^\d{1,3}$/.test(text.trim())) continue;
        var div = divs[j];
        var rect = div.getBoundingClientRect();
        var size = parseFloat(getComputedStyle(div).fontSize) || normalSize;
        var relativeX = (rect.left - pageRect.left) / pageRect.width;
        var inColumnNumberBand = relativeX >= 0.48 && relativeX <= 0.56;
        var inRightMarginNumberBand = relativeX >= 0.9;
        if (size >= normalSize * 0.72 || (!inColumnNumberBand && !inRightMarginNumberBand)) continue;

        div.dataset.pdfSelectionSkip = 'line-number';
        div.textContent = '';
        var previous = divs[j - 1];
        if (previous && /^\s*$/.test(strings[j - 1] || '')) {
          previous.dataset.pdfSelectionSkip = 'line-number-space';
          previous.textContent = '';
        }
      }
    }

    function onTextLayerRendered(event) {
      window.setTimeout(function () { sanitizePage(event.pageNumber); }, 0);
    }

    eventBus.on('textlayerrendered', onTextLayerRendered);
    ctrl.signal.addEventListener('abort', function () {
      eventBus.off('textlayerrendered', onTextLayerRendered);
    }, { once: true });
    return ctrl;
  };

  // ---- Copy fix: PDF.js 文本层因字体宽度不一致导致复制丢失末尾字符 ----
  // 拦截 copy 事件(capture phase,在 PDF.js handler 之前),改用
  // textContentItemsStr(PDF 精确文本)拼接选中文字,绕过 DOM 选区对齐问题。
  // Returns an AbortController; call .abort() to remove the listener.
  window.pdfjsInstallCopyFix = function pdfjsInstallCopyFix(container, viewer) {
    var ctrl = new AbortController();
    window.addEventListener('copy', function (ce) {
      var sel = document.getSelection();
      if (!sel || sel.isCollapsed) return;
      if (!sel.toString()) return;
      // 确认选区在 PDF viewer 容器内
      var node = sel.focusNode;
      while (node && node !== container) node = node.parentNode;
      if (node !== container) return;

      // 从 PDFViewer 获取所有 pageView 的 textLayer 数据
      var pageViews = viewer._pages || [];
      var items = [];
      for (var p = 0; p < pageViews.length; p++) {
        var pg = pageViews[p];
        var tl = pg._textHighlighter || (pg.textLayer && pg.textLayer.highlighter);
        if (tl) {
          var divs = tl.textDivs || [];
          var strs = tl.textContentItemsStr || [];
          for (var d = 0; d < divs.length; d++) {
            if (divs[d] && divs[d].dataset && divs[d].dataset.pdfSelectionSkip) continue;
            items.push({ textDiv: divs[d], content: strs[d] || '' });
          }
        }
      }
      if (!items.length) return;

      // 定位选中跨度的 textDiv 索引范围
      var R = sel.getRangeAt(0);
      var sc = R.startContainer, ec = R.endContainer;
      var so = R.startOffset, eo = R.endOffset;

      function findTextDiv(n) {
        while (n && n !== container) {
          var p = n.parentNode;
          if (p && p.classList && p.classList.contains('textLayer')) return n;
          n = p;
        }
        return null;
      }
      var startDiv = findTextDiv(sc);
      var endDiv = findTextDiv(ec);
      if (!startDiv || !endDiv) return;

      var si = -1, ei = -1;
      for (var k = 0; k < items.length; k++) {
        if (items[k].textDiv === startDiv) si = k;
        if (items[k].textDiv === endDiv) ei = k;
      }
      if (si < 0 || ei < 0 || si > ei) return;

      // 用 textContentItemsStr 拼接
      var text = '';
      if (si === ei) {
        text = items[si].content.substring(so, eo);
      } else {
        text = items[si].content.substring(so);
        for (var m = si + 1; m < ei; m++) {
          text += items[m].content;
        }
        text += items[ei].content.substring(0, eo);
      }
      if (text) {
        ce.preventDefault();
        ce.stopPropagation();
        ce.clipboardData.setData('text/plain', text);
      }
    }, { capture: true, signal: ctrl.signal });
    return ctrl;
  };
})();
