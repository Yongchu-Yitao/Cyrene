"""Shared context pack for Workbench task sessions.

This module is intentionally scoped to Workbench *task* sessions.  It does not
participate in ordinary chat sessions, quick chat, scheduler runs, or global
agent memory.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cyrene.config import DB_PATH
from cyrene.observability.context_trace import context_block
from cyrene.workbench.store import (
    has_document_data,
    read_project_bundle,
    summarize_task_session,
    write_project_bundle,
)

_DOC_KEY = "projects"
_MAX_OUTCOME_ENTRIES = 80
_MAX_OUTCOME_TEXT_CHARS = 2400
_RECENT_OUTCOME_TAIL = 6


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _short_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _default_projects_doc() -> dict[str, Any]:
    return {"projects": [], "activeProjectId": "", "activeSessionId": ""}


def _workbench_doc_available(db: str | Path) -> bool:
    """Return True when Workbench state already exists.

    Read-only context resolution must not create an empty Workbench projects
    document for ordinary agent sessions that merely happen to have a session id.
    """
    db_path = Path(db)
    if not db_path.exists():
        return False
    return has_document_data(db_path, _DOC_KEY)


def _clean_text(value: Any, limit: int = 0) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if limit and len(text) > limit:
        return text[: max(0, limit - 1)] + "…"
    return text


def _session_summary_text(session: dict[str, Any]) -> str:
    raw = session.get("summary")
    if isinstance(raw, dict):
        raw = raw.get("text") or raw.get("body") or raw.get("content") or raw.get("summary")
    return _clean_text(raw)


def _project_task_description(project: dict[str, Any]) -> str:
    context = project.get("context") if isinstance(project.get("context"), dict) else {}
    candidates = [
        project.get("description"),
        context.get("summary") if isinstance(context, dict) else "",
        project.get("name"),
    ]
    for value in candidates:
        text = _clean_text(value)
        if text:
            return text
    return ""


def _project_final_goal(project: dict[str, Any]) -> str:
    context = project.get("context") if isinstance(project.get("context"), dict) else {}
    candidates = [
        project.get("finalGoal"),
        project.get("goal"),
        context.get("finalGoal") if isinstance(context, dict) else "",
        context.get("goal") if isinstance(context, dict) else "",
    ]
    for value in candidates:
        text = _clean_text(value)
        if text:
            return text
    return ""


def ensure_shared_context(project: dict[str, Any]) -> dict[str, Any]:
    """Return a normalized project-level shared context object."""
    shared = project.get("sharedContext")
    if not isinstance(shared, dict):
        shared = {}
        project["sharedContext"] = shared
    shared.setdefault("revision", 0)
    shared.setdefault("taskDescription", _project_task_description(project))
    shared.setdefault("finalGoal", _project_final_goal(project))
    outcome = shared.get("currentOutcome")
    if not isinstance(outcome, dict):
        outcome = {}
        shared["currentOutcome"] = outcome
    outcome.setdefault("summary", "")
    entries = outcome.get("entries")
    if not isinstance(entries, list):
        entries = []
        outcome["entries"] = entries
    return shared


def is_task_session(session: dict[str, Any] | None) -> bool:
    if not isinstance(session, dict):
        return False
    return str(session.get("kind") or "task") == "task"


def find_task_scope(payload: dict[str, Any], session_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Find a Workbench task session by id, excluding chat/init sessions."""
    sid = str(session_id or "").strip()
    if not sid:
        return None, None
    for project in payload.get("projects") or []:
        if not isinstance(project, dict):
            continue
        for session in project.get("sessions") or []:
            if isinstance(session, dict) and str(session.get("id") or "") == sid and is_task_session(session):
                return project, session
    return None, None


def resolve_task_scope(
    session_id: str,
    *,
    db_path: str | Path | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    """Read the latest Workbench projects document and resolve a task session."""
    db = str(db_path or DB_PATH)
    if not _workbench_doc_available(db):
        return None, None, None
    payload = read_project_bundle(
        db,
        _default_projects_doc,
        summarize_task_session,
    )
    project, session = find_task_scope(payload, session_id)
    return payload, project, session


def _acceptance_texts(session: dict[str, Any]) -> list[str]:
    raw = session.get("acceptanceCriteria")
    if not isinstance(raw, list):
        return []
    texts: list[str] = []
    for item in raw:
        text = _clean_text(item.get("text") if isinstance(item, dict) else item)
        if text:
            texts.append(text)
        if len(texts) >= 8:
            break
    return texts


def _ordered_plan(session: dict[str, Any]) -> list[dict[str, Any]]:
    plan = session.get("plan") if isinstance(session.get("plan"), list) else []
    return sorted(
        (item for item in plan if isinstance(item, dict)),
        key=lambda item: int(item.get("order") or 0),
    )


def render_project_fixed_block(project: dict[str, Any]) -> str:
    shared = ensure_shared_context(project)
    task_description = _clean_text(shared.get("taskDescription")) or _project_task_description(project)
    final_goal = _clean_text(shared.get("finalGoal")) or _project_final_goal(project)
    outcome = shared.get("currentOutcome") if isinstance(shared.get("currentOutcome"), dict) else {}
    summary = _clean_text(outcome.get("summary") if isinstance(outcome, dict) else "")
    lines = ["## Workbench 项目共享上下文"]
    lines.append("### 项目任务描述")
    lines.append(task_description or "（未设置）")
    lines.append("### 最终目标")
    lines.append(final_goal or "（未设置）")
    lines.append("### 当前成果")
    lines.append(summary or "最新成果条目会放在上下文尾部；如果没有条目，表示暂无共享成果。")
    return "\n".join(lines)


def render_outcome_tail_block(project: dict[str, Any], *, limit: int = _RECENT_OUTCOME_TAIL) -> str:
    shared = ensure_shared_context(project)
    outcome = shared.get("currentOutcome") if isinstance(shared.get("currentOutcome"), dict) else {}
    entries = outcome.get("entries") if isinstance(outcome, dict) else []
    if not isinstance(entries, list):
        return ""
    rows: list[str] = []
    for entry in entries[-max(1, limit):]:
        if not isinstance(entry, dict):
            continue
        text = _clean_text(entry.get("text"), 900)
        if not text:
            continue
        source = _clean_text(entry.get("source"), 40) or "agent"
        agent = _clean_text(entry.get("agentId"), 40)
        session_id = _clean_text(entry.get("sessionId"), 40)
        label = source + (f"/{agent}" if agent else "") + (f" @ {session_id}" if session_id else "")
        rows.append(f"- [{label}] {text}")
    if not rows:
        return ""
    return "## 最新共享成果（项目内所有任务可见）\n" + "\n".join(rows)


def render_session_task_block(session: dict[str, Any], *, subtask_prompt: str = "", parent_task: bool = False) -> str:
    title = _clean_text(session.get("title"), 120)
    goal = _clean_text(session.get("goal"))
    summary = _session_summary_text(session)
    lines = ["## 当前 session 任务"]
    if subtask_prompt:
        lines.append("这是当前 session 下派给子代理的子任务；子代理只负责完成这段 prompt。")
        if title or goal:
            parent = title or goal
            lines.append(f"- 父 session：{parent}")
        lines.append(f"- 子任务：{str(subtask_prompt).strip()}")
    else:
        if title:
            lines.append(f"- 标题：{title}")
        if goal:
            lines.append(f"- 目标：{goal}")
        if summary:
            lines.append(f"- 简介：{summary}")
    if parent_task and not subtask_prompt and not title and not goal and not summary:
        lines.append("（未设置）")
    return "\n".join(lines)


def render_session_plan_block(session: dict[str, Any]) -> str:
    rows: list[str] = []
    titles_by_id = {
        str(step.get("id") or ""): _clean_text(step.get("title"), 80)
        for step in _ordered_plan(session)
    }
    for index, step in enumerate(_ordered_plan(session), start=1):
        title = _clean_text(step.get("title"), 90) or f"步骤 {index}"
        description = _clean_text(step.get("description"), 180)
        status = _clean_text(step.get("status") or "pending", 30)
        depends_on = step.get("dependsOn") if isinstance(step.get("dependsOn"), list) else []
        dep_titles = [titles_by_id.get(str(dep), str(dep)) for dep in depends_on if str(dep)]
        suffix = f" — {description}" if description else ""
        dep_suffix = f"；前置步骤：{'、'.join(dep_titles)}" if dep_titles else ""
        rows.append(f"{index}. [{status}] {title}{suffix}{dep_suffix}")
    if not rows:
        return ""
    return "## 当前 session 计划\n" + "\n".join(rows)


def render_session_constraints_block(session: dict[str, Any]) -> str:
    raw = session.get("constraints")
    if not isinstance(raw, list):
        return ""
    rows: list[str] = []
    for item in raw[:8]:
        text = _clean_text(item, 300)
        if text:
            rows.append(f"- {text}")
    if not rows:
        return ""
    return "## 当前 session 任务约束\n" + "\n".join(rows)


def render_session_acceptance_block(session: dict[str, Any]) -> str:
    raw = session.get("acceptanceCriteria")
    if not isinstance(raw, list):
        return ""
    rows: list[str] = []
    status_labels = {
        "passed": "已通过",
        "done": "已通过",
        "failed": "未通过",
        "pending": "待验证",
    }
    for item in raw[:8]:
        if not isinstance(item, dict):
            text = _clean_text(item)
            if text:
                rows.append(f"- [{status_labels['pending']}] {text}")
            continue
        text = _clean_text(item.get("text"))
        if not text:
            continue
        status = status_labels.get(str(item.get("status") or "pending"), "待验证")
        evidence = _clean_text(item.get("evidence"), 600)
        row = f"- [{status}] {text}"
        if evidence:
            row += f"；验收依据：{evidence}"
        rows.append(row)
    if not rows:
        return ""
    reason = _clean_text(session.get("verifyReason"), 1000)
    # Keep the stable heading so existing context consumers remain compatible;
    # status/evidence on each row carry the latest verification result.
    result = "## 当前计划的验收标准\n" + "\n".join(rows)
    if reason:
        result += "\n验收结论：" + reason
    return result


def build_main_context(
    project: dict[str, Any] | None,
    session: dict[str, Any],
) -> str:
    """Build the cache-stable Workbench task context prefix for the main agent."""
    if not project or not is_task_session(session):
        return ""
    parts = [
        render_project_fixed_block(project),
        render_session_task_block(session, parent_task=True),
        render_session_constraints_block(session),
        render_session_plan_block(session),
        render_session_acceptance_block(session),
    ]
    return "\n\n".join(part for part in parts if part).strip()


def build_subagent_context(
    project: dict[str, Any] | None,
    session: dict[str, Any] | None,
    subtask_prompt: str,
) -> str:
    """Build Workbench task context for a subagent.

    It shares project/system/plan/acceptance context with the parent session, but
    replaces "current session task" with the subtask prompt as requested.
    """
    if not project or not session or not is_task_session(session):
        return ""
    parts = [
        render_project_fixed_block(project),
        render_outcome_tail_block(project),
        render_session_task_block(session, subtask_prompt=subtask_prompt),
        render_session_constraints_block(session),
        render_session_plan_block(session),
        render_session_acceptance_block(session),
    ]
    return "\n\n".join(part for part in parts if part).strip()


def build_volatile_context(project: dict[str, Any] | None, session: dict[str, Any]) -> str:
    if not project or not is_task_session(session):
        return ""
    return render_outcome_tail_block(project).strip()


def context_trace_blocks(project: dict[str, Any] | None, session: dict[str, Any]) -> list[dict[str, Any]]:
    if not project or not is_task_session(session):
        return []
    return [
        context_block(
            "workbench.project.shared.fixed",
            "workbench_project",
            source="cyrene.workbench.task_context",
            reason="project-shared task context prefix",
            content=render_project_fixed_block(project),
            metadata={"projectId": str(project.get("id") or "")},
        ),
        context_block(
            "workbench.session.task",
            "workbench_session",
            source="cyrene.workbench.task_context",
            reason="current Workbench task session",
            content=render_session_task_block(session, parent_task=True),
            metadata={"sessionId": str(session.get("id") or "")},
        ),
        context_block(
            "workbench.session.plan",
            "workbench_plan",
            source="cyrene.workbench.task_context",
            reason="current Workbench task plan",
            content=render_session_plan_block(session),
            metadata={"sessionId": str(session.get("id") or "")},
        ),
        context_block(
            "workbench.session.acceptance",
            "workbench_acceptance",
            source="cyrene.workbench.task_context",
            reason="current Workbench acceptance criteria",
            content=render_session_acceptance_block(session),
            metadata={"sessionId": str(session.get("id") or "")},
        ),
    ]


def _outcome_entry_id(session_id: str, agent_id: str, text: str) -> str:
    digest = hashlib.sha256(f"{session_id}\0{agent_id}\0{text}".encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"outcome_{digest}"


def append_shared_outcome(
    *,
    db_path: str | Path | None = None,
    session_id: str,
    agent_id: str,
    source: str,
    text: str,
) -> dict[str, Any] | None:
    """Append a subagent/main-agent result to the project shared context.

    Returns the stored entry, or ``None`` when the session is not a Workbench task
    session or the text is empty.
    """
    clean = str(text or "").strip()
    if not clean:
        return None
    db = str(db_path or DB_PATH)
    if not _workbench_doc_available(db):
        return None
    payload = read_project_bundle(
        db,
        _default_projects_doc,
        summarize_task_session,
    )
    project, session = find_task_scope(payload, session_id)
    if not project or not session:
        return None
    shared = ensure_shared_context(project)
    outcome = shared["currentOutcome"]
    entries = outcome.setdefault("entries", [])
    if not isinstance(entries, list):
        entries = []
        outcome["entries"] = entries
    truncated = clean[:_MAX_OUTCOME_TEXT_CHARS]
    entry_id = _outcome_entry_id(str(session_id), str(agent_id), truncated)
    for existing in entries:
        if isinstance(existing, dict) and str(existing.get("id") or "") == entry_id:
            return existing
    now = _utc_now_iso()
    entry = {
        "id": entry_id or _short_id("outcome"),
        "sessionId": str(session_id or ""),
        "agentId": str(agent_id or ""),
        "source": str(source or "agent"),
        "text": truncated,
        "createdAt": now,
    }
    entries.append(entry)
    if len(entries) > _MAX_OUTCOME_ENTRIES:
        del entries[: len(entries) - _MAX_OUTCOME_ENTRIES]
    try:
        shared["revision"] = int(shared.get("revision") or 0) + 1
    except (TypeError, ValueError):
        shared["revision"] = 1
    project["updatedAt"] = now
    write_project_bundle(
        db,
        payload,
        _default_projects_doc,
        summarize_task_session,
    )
    return entry
