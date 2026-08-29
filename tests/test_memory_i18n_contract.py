from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from cyrene.core.plugin import PluginContext
from cyrene.plugins.builtin.cyrene_memory import application
from cyrene.plugins.builtin.cyrene_memory import recall_memory
from cyrene.plugins.builtin.cyrene_memory import structured


def test_memory_read_model_exposes_stable_ids_in_compatibility_label_fields(
    monkeypatch,
):
    entry = {
        "id": "mem_contract",
        "content": "Keep the public read model locale-neutral.",
        "category": "habit",
        "source": "agent",
        "confidence": "high",
        "first_seen": "2026-08-01",
        "last_mentioned": "2026-08-02",
        "mention_count": 2,
        "citations": [{"at": "2026-08-02", "source": "agent"}],
        "history": [{"at": "2026-08-01", "action": "created"}],
    }
    monkeypatch.setattr(structured, "_load", lambda _workspace: [entry])

    payload = structured.build_memory_payload("project-contract")
    item = payload["memories"][0]

    assert item["category_label"] == item["category"] == "habit"
    assert item["source_label"] == item["source"] == "agent"
    assert item["confidence_label"] == item["confidence"] == "high"
    assert item["citations"][0]["source_label"] == "agent"
    assert item["history"][0]["action_label"] == "created"
    assert all(category["label"] == category["id"] for category in payload["categories"])
    assert all(source["label"] == source["id"] for source in payload["sources"])
    label_values = [
        item["category_label"],
        item["source_label"],
        item["confidence_label"],
        item["citations"][0]["source_label"],
        item["history"][0]["action_label"],
        *(category["label"] for category in payload["categories"]),
        *(source["label"] for source in payload["sources"]),
    ]
    assert not any(re.search(r"[\u3400-\u9fff]", value) for value in label_values)


def test_machine_generated_history_details_use_stable_codes(monkeypatch):
    entries = [{
        "id": "mem_old",
        "content": "Old project fact.",
        "category": "fact",
        "source": "agent",
        "first_seen": "2026-08-01",
        "last_mentioned": "2026-08-01",
    }]
    monkeypatch.setattr(structured, "_load", lambda _workspace: entries)
    monkeypatch.setattr(structured, "_save", lambda _workspace, _entries: None)

    retired, changed = structured.retire_project_memory(
        "project-contract",
        "mem_old",
    )

    assert changed is True
    assert entries[0]["history"][-1] == {
        "at": entries[0]["history"][-1]["at"],
        "action": "stale",
        "detail": "",
        "detail_code": "retired_by_agent",
    }
    assert retired["history"][-1]["detail"] == ""
    assert retired["history"][-1]["detail_code"] == "retired_by_agent"


def test_memory_storage_strips_presentation_labels_and_migrates_legacy_detail():
    entry = {
        "content": "避免：不要重复无效方案。",
        "category": "reflection",
        "tags": ["reflection", "dead_end"],
        "category_label": "事实信息",
        "source_label": "Agent 记录",
        "confidence_label": "高",
        "citations": [{"source": "agent", "source_label": "Agent 引用"}],
        "history": [{
            "action": "stale",
            "action_label": "标记过时",
            "detail": "被新记忆取代",
        }],
    }

    structured._canonicalize_storage_entry(entry)

    assert "category_label" not in entry
    assert "source_label" not in entry
    assert "confidence_label" not in entry
    assert "source_label" not in entry["citations"][0]
    assert "action_label" not in entry["history"][0]
    assert entry["history"][0]["detail"] == ""
    assert entry["history"][0]["detail_code"] == "superseded"
    assert entry["content"] == "不要重复无效方案。"


@pytest.mark.asyncio
async def test_memory_tool_empty_results_follow_plugin_context_language(monkeypatch):
    monkeypatch.setattr(recall_memory, "load_entries", lambda: [])

    english = json.loads(await recall_memory._tool_recall_memory(
        {}, PluginContext(data={"language": "en"})
    ))
    chinese = json.loads(await recall_memory._tool_recall_memory(
        {}, PluginContext(data={"language": "zh"})
    ))

    assert english["note"] == "No recent memories match the requested filters."
    assert chinese["note"] == "没有匹配当前筛选条件的近期记忆。"


def test_memory_search_defaults_use_frontend_translation_markers(monkeypatch, tmp_path):
    from cyrene.workbench.sessions import context as workbench_context
    from cyrene.workbench.persistence import store as workbench_store

    monkeypatch.setattr(workbench_context, "read_projects", lambda: [])
    monkeypatch.setattr(
        workbench_store,
        "list_document_keys",
        lambda _db_path, prefix="": ["memory:orphan"],
    )
    monkeypatch.setattr(
        workbench_store,
        "read_document",
        lambda *_args, **_kwargs: [{
            "id": "mem_orphan",
            "content": "",
            "category": "fact",
            "tags": ["needle"],
        }],
    )

    service = application.MemoryApplication(
        "memory.db", object(), object(), tmp_path
    )
    result = service._search("needle", 10)[0]

    assert result["title"] == ""
    assert result["titleKey"] == "search.default.memory"
    assert result["projectName"] == ""
    assert result["projectNameDefault"] is True


def test_memory_frontend_localizes_codes_instead_of_rendering_backend_labels():
    root = Path(__file__).resolve().parents[1]
    source = (root / "src/cyrene/workbench/webui/frontend/workbench-memory.jsx").read_text(
        encoding="utf-8"
    )
    en = (root / "src/cyrene/workbench/webui/frontend/shared/i18n/catalog-en.jsx").read_text(
        encoding="utf-8"
    )
    zh = (root / "src/cyrene/workbench/webui/frontend/shared/i18n/catalog-zh.jsx").read_text(
        encoding="utf-8"
    )

    assert "citationSourceLabel(c.source, t)" in source
    assert "historyActionLabel(ev.action, t)" in source
    assert "historyDetailText(ev, t)" in source
    assert "sourceLabel(m.source, t)" in source
    assert "m.source_label" not in source
    assert "ev.action_label" not in source
    for key in (
        "memory.citationSource.conversation",
        "memory.historyAction.created",
        "memory.historyDetail.retired_by_agent",
        "memory.historyDetail.superseded",
    ):
        assert f'"{key}"' in en
        assert f'"{key}"' in zh
