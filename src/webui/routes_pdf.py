"""PDF viewer page and text selection analysis routes."""

import html
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

logger = logging.getLogger(__name__)


def register_pdf_routes(router: APIRouter) -> None:
    @router.get("/pdf/viewer", response_class=HTMLResponse)
    async def pdf_viewer_page(request: Request):
        """Standalone PDF viewer page with PDF.js rendering + text selection.

        Query params:
          url  – URL to the PDF file (e.g. /api/chat/upload/filename.pdf)
          name – Display name (optional)
        """
        pdf_url = request.query_params.get("url", "")
        pdf_name = request.query_params.get("name", "Document")
        return HTMLResponse(_PDF_VIEWER_HTML(pdf_url, pdf_name))

    @router.post("/api/pdf/analyze")
    async def pdf_analyze_text(request: Request):
        """Analyze selected PDF text via the LLM.

        Body: { text: str, pdf_name?: str }
        Returns: { result: str }
        """
        body = await request.json()
        selected_text = (body.get("text") or "").strip()
        pdf_name = (body.get("pdf_name") or "PDF").strip()
        if not selected_text:
            return JSONResponse({"error": "no text provided"}, status_code=400)

        from cyrene.call_llm import call_llm as _call_llm

        prompt = (
            f"The user selected the following text from the PDF \"{pdf_name}\".\n"
            "Please analyze this text: summarize, explain key points, "
            "and provide any relevant context or insights.\n\n"
            f"Selected text:\n```\n{selected_text}\n```"
        )
        try:
            result = await _call_llm(
                [{"role": "user", "content": prompt}],
                model_type="primary",
                thinking="disabled",
                publish_events=False,
                record_usage=False,
                return_text=True,
            )
            return {"result": str(result)}
        except Exception as exc:
            logger.error("PDF analysis LLM call failed: %s", exc, exc_info=True)
            return JSONResponse({"error": "Analysis temporarily unavailable"}, status_code=500)


def _PDF_VIEWER_HTML(pdf_url: str, pdf_name_raw: str) -> str:
    js_url = json.dumps(pdf_url).replace('</', '<\\/')
    pdf_name_safe = html.escape(pdf_name_raw)
    js_name = json.dumps(pdf_name_raw)
    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{pdf_name_safe} — PDF Viewer</title>
<link rel="stylesheet" href="/static/app/pdfjs/pdf_viewer.css">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  html, body {{ width: 100%; height: 100%; overflow: hidden; background: #404040; }}
  #pdfContainer {{ position: relative; width: 100%; height: 100%; overflow: auto; }}
  #pdfContainer .pdfViewer .page {{
    margin: 8px auto; border-radius: 2px; box-shadow: 0 2px 8px rgba(0,0,0,0.3);
    background: #fff;
  }}
  .pdf-toolbar {{
    position: fixed; top: 0; left: 0; right: 0; z-index: 100;
    display: flex; align-items: center; gap: 8px;
    padding: 6px 16px; background: #303030; color: #ccc;
    font: 13px -apple-system, BlinkMacSystemFont, sans-serif;
    user-select: none;
  }}
  .pdf-toolbar button {{
    background: #555; color: #eee; border: none; border-radius: 4px;
    padding: 4px 12px; font-size: 13px; cursor: pointer;
  }}
  .pdf-toolbar button:hover {{ background: #666; }}
  .pdf-toolbar .page-info {{ margin-left: auto; }}
  .pdf-toolbar .spacer {{ flex: 1; }}
  #pdfContainer {{ margin-top: 36px; height: calc(100% - 36px); }}

  .wbc-pdf-analyze-btn {{
    position: fixed; bottom: 24px; right: 24px; z-index: 200;
    background: #4a90d9; color: #fff; border: none; border-radius: 8px;
    padding: 10px 20px; font: 14px sans-serif; cursor: pointer;
    box-shadow: 0 2px 12px rgba(0,0,0,0.4); display: none;
  }}
  .wbc-pdf-analyze-btn:hover {{ background: #357abd; }}

  .wbc-pdf-result {{
    position: fixed; bottom: 80px; right: 24px; z-index: 200;
    width: 420px; max-height: 60vh; overflow-y: auto;
    background: #2a2a2a; color: #e0e0e0; border-radius: 10px;
    padding: 16px 20px; font: 13px/1.6 sans-serif;
    box-shadow: 0 4px 20px rgba(0,0,0,0.5); display: none;
  }}
  .wbc-pdf-result h4 {{ margin-bottom: 8px; color: #4a90d9; }}
  .wbc-pdf-result .close-btn {{
    float: right; background: none; border: none; color: #888;
    font-size: 18px; cursor: pointer; padding: 0 4px;
  }}
  .wbc-pdf-result .close-btn:hover {{ color: #fff; }}
  .wbc-pdf-result.loading {{ opacity: 0.7; }}
</style>
</head>
<body>

<div class="pdf-toolbar">
  <span style="font-weight:600;color:#fff;">{pdf_name_safe}</span>
  <span class="spacer"></span>
  <button id="zoomOut">−</button>
  <span id="zoomLabel" style="min-width:44px;text-align:center;">100%</span>
  <button id="zoomIn">+</button>
  <span class="page-info">
    <span id="pageNum">1</span> / <span id="pageCount">—</span>
  </span>
</div>

<div id="pdfContainer">
  <div id="viewerContainer"></div>
</div>

<button class="wbc-pdf-analyze-btn" id="analyzeBtn">分析选中的文字</button>
<div class="wbc-pdf-result" id="resultPanel">
  <button class="close-btn" id="closeResult">×</button>
  <h4>分析结果</h4>
  <div id="resultBody"></div>
</div>

<script src="/static/app/pdfjs/pdf.min.js?v=0.7.3"></script>
<script src="/static/app/pdfjs/pdf_viewer.js?v=0.7.3"></script>
<script src="/static/app/pdfjs/pdf-setup.js?v=0.7.3"></script>
<script>
(function() {{
  var pdfUrl = {js_url};
  if (!pdfUrl) return;

  pdfjsLib.GlobalWorkerOptions.workerSrc = '/static/app/pdfjs/pdf.worker.min.js?v=0.7.3';

  var container = document.getElementById('viewerContainer');
  var result = pdfjsSetupViewer(container);
  var viewer = result.viewer;
  var eventBus = result.eventBus;

  var currentDoc = null;

  pdfjsLoadPdf(pdfUrl, viewer).then(function(doc) {{
    currentDoc = doc;
    document.getElementById('pageCount').textContent = doc.numPages;
  }}).catch(function(err) {{
    container.textContent = ''; container.innerHTML = '<div style="padding:40px;text-align:center;color:#999;">加载失败</div>';
  }});

  eventBus.on('pagechanging', function(evt) {{
    document.getElementById('pageNum').textContent = evt.pageNumber;
  }});

  document.getElementById('zoomIn').addEventListener('click', function() {{
    viewer.currentScale *= 1.15;
    updateZoomLabel();
  }});
  document.getElementById('zoomOut').addEventListener('click', function() {{
    viewer.currentScale = Math.max(0.25, viewer.currentScale / 1.15);
    updateZoomLabel();
  }});
  function updateZoomLabel() {{
    document.getElementById('zoomLabel').textContent = Math.round(viewer.currentScale * 100) + '%';
  }}

  var analyzeBtn = document.getElementById('analyzeBtn');
  var resultPanel = document.getElementById('resultPanel');
  var resultBody = document.getElementById('resultBody');
  var closeResult = document.getElementById('closeResult');
  var selectionTimeout = null;

  container.addEventListener('mouseup', function() {{
    if (selectionTimeout) clearTimeout(selectionTimeout);
    selectionTimeout = setTimeout(function() {{
      var text = pdfjsGetSelectedText(container).trim();
      analyzeBtn.style.display = text ? 'block' : 'none';
    }}, 200);
  }});

  container.addEventListener('mousedown', function() {{
    analyzeBtn.style.display = 'none';
  }});

  analyzeBtn.addEventListener('click', function() {{
    var text = pdfjsGetSelectedText(container).trim();
    if (!text) return;

    analyzeBtn.textContent = '分析中…';
    analyzeBtn.disabled = true;
    resultPanel.className = 'wbc-pdf-result loading';
    resultBody.textContent = '请稍候…';
    resultPanel.style.display = 'block';

    fetch('/api/pdf/analyze', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ text: text, pdf_name: {js_name} }}),
    }})
    .then(function(r) {{ return r.json(); }})
    .then(function(data) {{
      if (data.error) throw new Error(data.error);
      resultPanel.className = 'wbc-pdf-result';
      resultBody.textContent = data.result || '(无结果)';
      var sel = window.getSelection();
      if (sel) sel.removeAllRanges();
    }})
    .catch(function(err) {{
      resultPanel.className = 'wbc-pdf-result';
      resultBody.textContent = '分析失败: ' + err.message;
    }})
    .finally(function() {{
      analyzeBtn.textContent = '分析选中的文字';
      analyzeBtn.disabled = false;
      analyzeBtn.style.display = 'none';
    }});
  }});

  closeResult.addEventListener('click', function() {{
    resultPanel.style.display = 'none';
  }});

  document.addEventListener('keydown', function(e) {{
    if (e.key === 'Escape') resultPanel.style.display = 'none';
    if ((e.key === '+' || e.key === '=') && (e.ctrlKey || e.metaKey)) {{ e.preventDefault(); document.getElementById('zoomIn').click(); }}
    if (e.key === '-' && (e.ctrlKey || e.metaKey)) {{ e.preventDefault(); document.getElementById('zoomOut').click(); }}
  }});

  // Copy the original PDF text rather than browser-measured text-layer content.
  pdfjsInstallSelectionSanitizer(container, viewer, eventBus);
  pdfjsInstallCopyFix(container, viewer);
}})();
</script>
</body>
</html>"""
