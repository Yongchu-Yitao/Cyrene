(function () {
  var pdfjsAnalysisPageTextCache = new WeakMap();

  // ---- PDFViewer setup ----
  // Creates the container DOM and constructs a PDFViewer.
  // Returns { viewer, eventBus }
  function pdfjsSetupViewer(container) {
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
  }

  // ---- Stream + load PDF document ----
  // Let PDF.js own the network request so it can use range requests and begin
  // parsing before the entire file has been buffered in renderer memory.
  // `signal` — optional AbortSignal used to destroy the PDF.js loading task.
  // Returns a promise that resolves with the pdf document (numPages, etc.)
  function pdfjsLoadPdf(url, viewer, signal) {
    var loadingTask = window.pdfjsLib.getDocument({
      url: url,
      withCredentials: true,
    });
    var abortLoading = function () {
      try { loadingTask.destroy(); } catch (error) {}
    };

    if (signal && signal.aborted) {
      abortLoading();
      return Promise.reject(signal.reason || new DOMException('PDF loading aborted', 'AbortError'));
    }
    if (signal) signal.addEventListener('abort', abortLoading, { once: true });

    return loadingTask.promise
      .then(function (doc) {
        viewer.setDocument(doc);
        viewer.currentScaleValue = 'page-width';
        return doc;
      })
      .finally(function () {
        if (signal) signal.removeEventListener('abort', abortLoading);
      });
  }

  function pdfjsGetSelectedText(container) {
    var selection = document.getSelection();
    return selection ? selection.toString() : '';
  }

  function pdfjsSelectionPageNumbers(container, totalPages, fallbackPage) {
    var currentPage = Math.max(1, Math.min(totalPages || 1, Number(fallbackPage) || 1));

    function pageNumberForNode(node) {
      if (node && node.nodeType === 3) node = node.parentNode;
      while (node && node !== container) {
        if (node.nodeType === 1 && node.classList && node.classList.contains('page')) {
          var value = Number(node.dataset && node.dataset.pageNumber || node.getAttribute && node.getAttribute('data-page-number'));
          return value >= 1 && value <= totalPages ? value : 0;
        }
        node = node.parentNode;
      }
      return 0;
    }

    var selection = document.getSelection();
    var selectedPages = [];
    if (selection && selection.rangeCount && !selection.isCollapsed) {
      var anchorPage = pageNumberForNode(selection.anchorNode);
      var focusPage = pageNumberForNode(selection.focusNode);
      if (anchorPage) selectedPages.push(anchorPage);
      if (focusPage && focusPage !== anchorPage) selectedPages.push(focusPage);
    }
    if (!selectedPages.length) selectedPages.push(currentPage);
    return selectedPages.sort(function (a, b) { return a - b; });
  }

  function pdfjsTextFromContent(content) {
    var output = '';
    var items = content && Array.isArray(content.items) ? content.items : [];
    for (var index = 0; index < items.length; index++) {
      var item = items[index] || {};
      var value = typeof item.str === 'string' ? item.str : '';
      if (!value) continue;
      if (output && !/[\s\n]$/.test(output) && !/^[,.;:!?，。；：！？、)\]}]/.test(value)) output += ' ';
      output += value;
      output += item.hasEOL ? '\n' : ' ';
    }
    return output
      .replace(/[ \t]+\n/g, '\n')
      .replace(/[ \t]{2,}/g, ' ')
      .replace(/\n{3,}/g, '\n\n')
      .trim();
  }

  function pdfjsAnalysisPageText(pdfDocument, pageNumber) {
    var documentCache = pdfjsAnalysisPageTextCache.get(pdfDocument);
    if (!documentCache) {
      documentCache = new Map();
      pdfjsAnalysisPageTextCache.set(pdfDocument, documentCache);
    }
    if (!documentCache.has(pageNumber)) {
      documentCache.set(pageNumber, pdfDocument.getPage(pageNumber)
        .then(function (page) { return page.getTextContent(); })
        .then(pdfjsTextFromContent)
        .catch(function () { return ''; }));
    }
    return documentCache.get(pageNumber);
  }

  function pdfjsRunWithConcurrency(values, concurrency, worker) {
    var results = new Array(values.length);
    var nextIndex = 0;
    function runNext() {
      var index = nextIndex++;
      if (index >= values.length) return Promise.resolve();
      return Promise.resolve(worker(values[index], index)).then(function (result) {
        results[index] = result;
        return runNext();
      });
    }
    var runners = [];
    for (var index = 0; index < Math.min(concurrency, values.length); index++) runners.push(runNext());
    return Promise.all(runners).then(function () { return results; });
  }

  // Build a lightweight document index. The LLM receives these page previews
  // and decides which pages are relevant; fixed neighbouring-page heuristics
  // are deliberately avoided.
  function pdfjsBuildAnalysisInventory(container, viewer, fallbackPage) {
    var pdfDocument = viewer && viewer.pdfDocument;
    var totalPages = pdfDocument ? Number(pdfDocument.numPages) || 0 : 0;
    var currentPage = Math.max(1, Math.min(totalPages || 1, Number(fallbackPage) || 1));
    var emptyInventory = {
      current_page: currentPage,
      total_pages: totalPages,
      selected_pages: [],
      page_previews: [],
    };
    if (!container || !pdfDocument || !totalPages) return Promise.resolve(emptyInventory);

    var selectedPages = pdfjsSelectionPageNumbers(container, totalPages, currentPage);
    var pageNumbers = [];
    for (var pageNumber = 1; pageNumber <= totalPages; pageNumber++) pageNumbers.push(pageNumber);
    return pdfjsRunWithConcurrency(pageNumbers, 4, function (number) {
      return pdfjsAnalysisPageText(pdfDocument, number).then(function (text) {
        return { page_number: number, text: text.slice(0, 700) };
      });
    }).then(function (pagePreviews) {
      return {
        current_page: currentPage,
        total_pages: totalPages,
        selected_pages: selectedPages,
        page_previews: pagePreviews.filter(function (page) { return !!page.text; }),
      };
    });
  }

  // After the planning agent chooses a reference range, retrieve only those
  // pages at full fidelity for the final analysis call.
  function pdfjsExtractAnalysisContext(viewer, pageNumbers, inventory, planReason) {
    var pdfDocument = viewer && viewer.pdfDocument;
    var totalPages = pdfDocument ? Number(pdfDocument.numPages) || 0 : 0;
    var selectedPages = inventory && Array.isArray(inventory.selected_pages) ? inventory.selected_pages : [];
    var normalized = [];
    (Array.isArray(pageNumbers) ? pageNumbers : []).forEach(function (value) {
      var number = Number(value);
      if (number >= 1 && number <= totalPages && normalized.indexOf(number) < 0) normalized.push(number);
    });
    selectedPages.forEach(function (number) {
      if (number >= 1 && number <= totalPages && normalized.indexOf(number) < 0) normalized.unshift(number);
    });
    normalized = normalized.slice(0, 5).sort(function (a, b) { return a - b; });
    if (!pdfDocument) normalized = [];

    return Promise.all(normalized.map(function (pageNumber) {
      return pdfjsAnalysisPageText(pdfDocument, pageNumber).then(function (text) {
        return { page_number: pageNumber, text: text.slice(0, 7000) };
      });
    })).then(function (pages) {
      var remaining = 20000;
      var boundedPages = [];
      pages.forEach(function (page) {
        if (!page.text || remaining <= 0) return;
        var text = page.text.slice(0, remaining);
        boundedPages.push({ page_number: page.page_number, text: text });
        remaining -= text.length;
      });
      return {
        current_page: inventory && inventory.current_page || 1,
        total_pages: totalPages,
        selected_pages: selectedPages,
        pages: boundedPages,
        plan_reason: String(planReason || '').slice(0, 600),
      };
    });
  }

  // Some review/manuscript PDFs inject tiny line numbers after every text
  // line. Those text objects sit in the column gutter and become part of the
  // browser range, even though the pointer never entered the next column.
  // Remove only those tiny numeric objects (and their leading spacer) from the
  // transparent text layer; the canvas-rendered PDF remains unchanged.
  function pdfjsInstallSelectionSanitizer(container, viewer, eventBus) {
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
  }

  // ---- Copy fix: PDF.js 文本层因字体宽度不一致导致复制丢失末尾字符 ----
  // 拦截 copy 事件(capture phase,在 PDF.js handler 之前),改用
  // textContentItemsStr(PDF 精确文本)拼接选中文字,绕过 DOM 选区对齐问题。
  // Returns an AbortController; call .abort() to remove the listener.
  function pdfjsInstallCopyFix(container, viewer) {
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
  }

  var pdfService = {
    lib: window.pdfjsLib,
    viewer: window.pdfjsViewer,
    setupViewer: pdfjsSetupViewer,
    loadPdf: pdfjsLoadPdf,
    getSelectedText: pdfjsGetSelectedText,
    buildAnalysisInventory: pdfjsBuildAnalysisInventory,
    extractAnalysisContext: pdfjsExtractAnalysisContext,
    installSelectionSanitizer: pdfjsInstallSelectionSanitizer,
    installCopyFix: pdfjsInstallCopyFix,
  };
  window.CyreneUI.pdf = window.CyreneUI.register("pdf", pdfService);
})();
