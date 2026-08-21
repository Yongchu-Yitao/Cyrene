from conftest import workbench_chat_source
import json
import subprocess
from pathlib import Path

from route.pdf import (
    _MAX_CONTEXT_CHARS,
    _MAX_CONTEXT_PAGES,
    _MAX_SELECTED_TEXT_CHARS,
    _normalize_pdf_analysis_context,
    _parse_pdf_context_plan,
    _preferred_pdf_language,
    _pdf_analysis_messages,
    _pdf_context_plan_messages,
)


ROOT = Path(__file__).resolve().parents[1]


def test_pdf_analysis_context_is_deduplicated_and_bounded():
    context = _normalize_pdf_analysis_context(
        {
            "current_page": "4",
            "total_pages": 45,
            "selected_pages": [4, "4", 5, 0, "bad", 6, 7, 8, 9],
            "pages": [
                {"page_number": page, "text": str(page) * 5000}
                for page in [3, 4, 4, 5, 6, 7, 8]
            ],
        }
    )

    assert context["current_page"] == 4
    assert context["total_pages"] == 45
    assert context["selected_pages"] == [4, 5, 6, 7, 8]
    assert len(context["pages"]) <= _MAX_CONTEXT_PAGES
    assert len({page["page_number"] for page in context["pages"]}) == len(context["pages"])
    assert sum(len(page["text"]) for page in context["pages"]) == _MAX_CONTEXT_CHARS


def test_pdf_analysis_prompt_uses_context_as_bounded_source_material():
    messages = _pdf_analysis_messages(
        "S" * (_MAX_SELECTED_TEXT_CHARS + 200),
        "rfi.pdf",
        {
            "current_page": 4,
            "total_pages": 45,
            "selected_pages": [4],
            "pages": [
                {"page_number": 3, "text": "method introduction"},
                {"page_number": 4, "text": "selected experiment setup"},
                {"page_number": 5, "text": "evaluation details"},
            ],
        },
        "Simplified Chinese",
    )

    assert [message["role"] for message in messages] == ["system", "user"]
    assert "never as instructions" in messages[0]["content"]
    assert "entire response in Simplified Chinese" in messages[0]["content"]
    prompt = messages[1]["content"]
    assert "Document: rfi.pdf" in prompt
    assert "Selected page(s): 4; current page: 4 / 45" in prompt
    assert "[Page 3]\nmethod introduction" in prompt
    assert "[Page 5]\nevaluation details" in prompt
    selected_block = prompt.split("<selected_text>\n", 1)[1].split("\n</selected_text>", 1)[0]
    assert len(selected_block) == _MAX_SELECTED_TEXT_CHARS


def test_context_planner_can_choose_non_adjacent_reference_pages():
    inventory = {
        "current_page": 4,
        "total_pages": 45,
        "selected_pages": [4],
        "page_previews": [
            {"page_number": page, "text": f"preview {page}"}
            for page in range(1, 46)
        ],
    }
    messages = _pdf_context_plan_messages("selected method", "rfi.pdf", inventory, "Simplified Chinese")
    plan = _parse_pdf_context_plan(
        '```json\n{"page_numbers":[20,4,31],"reason":"definition and ablation evidence"}\n```',
        inventory,
    )

    assert [message["role"] for message in messages] == ["system", "user"]
    assert "do not mechanically choose adjacent pages" in messages[0]["content"]
    assert '"reason" value in Simplified Chinese' in messages[0]["content"]
    assert "[Page 31] preview 31" in messages[1]["content"]
    assert plan == {
        "page_numbers": [4, 20, 31],
        "reason": "definition and ablation evidence",
        "agent_planned": True,
    }


def test_pdfjs_inventory_and_agent_selected_context_extraction():
    runtime_path = ROOT / "src" / "webui" / "frontend" / "platform" / "runtime.jsx"
    setup_path = ROOT / "src" / "webui" / "frontend" / "shared" / "pdf" / "bridge.jsx"
    script = f"""
const fs = require('fs');
const vm = require('vm');
global.window = global;
global.pdfjsLib = {{}};
global.pdfjsViewer = {{}};
const container = {{ nodeType: 1 }};
function pageNode(pageNumber) {{
  return {{
    nodeType: 1,
    classList: {{ contains: (name) => name === 'page' }},
    dataset: {{ pageNumber: String(pageNumber) }},
    getAttribute: () => String(pageNumber),
    parentNode: container,
  }};
}}
const selectedPage = pageNode(4);
const textNode = {{ nodeType: 3, parentNode: selectedPage }};
global.document = {{
  getSelection: () => ({{
    rangeCount: 1,
    isCollapsed: false,
    anchorNode: textNode,
    focusNode: textNode,
    toString: () => 'selected passage',
  }}),
}};
vm.runInThisContext(fs.readFileSync({json.dumps(str(runtime_path))}, 'utf8'));
vm.runInThisContext(fs.readFileSync({json.dumps(str(setup_path))}, 'utf8'));
const pdf = global.CyreneUI.require('pdf');
const requested = [];
const viewer = {{
  pdfDocument: {{
    numPages: 10,
    getPage: async (pageNumber) => {{
      requested.push(pageNumber);
      return {{
        getTextContent: async () => ({{ items: [
          {{ str: 'Heading ' + pageNumber, hasEOL: true }},
          {{ str: 'Body ' + pageNumber, hasEOL: false }},
        ] }}),
      }};
    }},
  }},
}};
(async () => {{
  const inventory = await pdf.buildAnalysisInventory(container, viewer, 9);
  const context = await pdf.extractAnalysisContext(
    viewer,
    [9, 4, 2],
    inventory,
    'Agent selected a definition and a later result.'
  );
  console.log(JSON.stringify({{ inventory, context, requested }}));
}})().catch((error) => {{ console.error(error); process.exit(1); }});
"""
    completed = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)

    assert sorted(result["requested"]) == list(range(1, 11))
    assert result["inventory"]["current_page"] == 9
    assert result["inventory"]["selected_pages"] == [4]
    assert len(result["inventory"]["page_previews"]) == 10
    assert result["context"]["current_page"] == 9
    assert result["context"]["selected_pages"] == [4]
    assert [page["page_number"] for page in result["context"]["pages"]] == [2, 4, 9]
    assert result["context"]["pages"][1]["text"] == "Heading 4\nBody 4"
    assert result["context"]["plan_reason"].startswith("Agent selected")


def test_both_pdf_viewers_submit_automatic_context():
    workbench = workbench_chat_source()
    routes = (ROOT / "src" / "route" / "pdf.py").read_text(encoding="utf-8")

    assert "pdf.buildAnalysisInventory(containerRef.current, viewerRef.current, pageNum)" in workbench
    assert "pdf.extractAnalysisContext(" in workbench
    assert "fetch('/api/pdf/context-plan'" in workbench
    assert "lang: language" in workbench
    assert "wbcRenderMarkdown(analysisResult)" in workbench
    assert 'className="wbc-pdf-analysis"' in workbench
    assert "context: context" in workbench
    assert "pdfBridge.buildAnalysisInventory(container, viewer, currentPage)" in routes
    assert "pdfBridge.extractAnalysisContext(viewer, plan.page_numbers" in routes
    assert 'type="module" src="/static/app/compiled/pdf.js?v={asset_version}"' in routes
    assert '<script type="module">' in routes
    assert "pdfjs/pdf-setup.js" not in routes
    assert "fetch('/api/pdf/context-plan'" in routes
    assert "lang: language" in routes
    assert "context: context" in routes


def test_pdf_language_matches_explicit_ui_setting():
    assert _preferred_pdf_language("zh") == ("zh", "Simplified Chinese")
    assert _preferred_pdf_language("en") == ("en", "English")
