"""Pure Task-initialization domain helpers.

Model and workspace-tool execution is owned by :class:`TaskAgentRuntime`.  This
module only validates its JSON projections, maintains lightweight planning
metadata, and creates Task session records.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from cyrene.workbench import planning_contracts, project_runtime


class _WorkbenchGenerationError(RuntimeError):
    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = str(category or "unknown")
        self.message = str(message or "未知错误")


class _WorkbenchAgentRunError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 502):
        super().__init__(message)
        self.code = str(code or "workbench_agent_run_failed")
        self.message = str(message or "Agent 执行失败。")
        self.status_code = int(status_code)


def _option_label(option: Any) -> str:
    if isinstance(option, dict):
        for key in ("label", "text", "value", "title", "name"):
            value = str(option.get(key) or "").strip()
            if value:
                return value
        return ""
    return str(option or "").strip()


def _workbench_redact_error_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"(?i)\bBearer\s+\S+", "Bearer <redacted>", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "sk-<redacted>", text)
    text = re.sub(
        r"(?i)(api[_ -]?key[\"']?\s*[:=]\s*[\"']?)[^\"'\s,}]+",
        r"\1<redacted>",
        text,
    )
    return text


def _workbench_generation_error(exc: Exception) -> _WorkbenchGenerationError:
    if isinstance(exc, _WorkbenchGenerationError):
        return exc
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return _WorkbenchGenerationError("timeout", "模型请求超时。")
    response = getattr(exc, "response", None)
    status_value = getattr(response, "status_code", None)
    if isinstance(status_value, int):
        status = status_value
        if status in {401, 403}:
            category, summary = "authentication", f"模型服务鉴权失败（HTTP {status}）。"
        elif status == 429:
            category, summary = "rate_limit", "模型服务触发限流（HTTP 429）。"
        elif status >= 500:
            category, summary = "upstream", f"模型服务暂时异常（HTTP {status}）。"
        else:
            category, summary = "http", f"模型服务返回 HTTP {status}。"
        return _WorkbenchGenerationError(category, summary)
    category = str(getattr(exc, "code", "internal") or "internal")
    return _WorkbenchGenerationError(
        category,
        _workbench_redact_error_text(
            f"{type(exc).__name__}: {str(exc or '未知错误').strip()}"
        ),
    )


def _workbench_parse_json_object(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    candidates = [raw]
    fence = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL | re.IGNORECASE)
    if fence and fence.group(1).strip():
        candidates.append(fence.group(1).strip())
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    decoder = json.JSONDecoder()
    for index, char in enumerate(raw):
        if char != "{":
            continue
        try:
            parsed, _end = decoder.raw_decode(raw[index:])
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _workbench_stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _workbench_hash_json(value: Any) -> str:
    return hashlib.sha256(_workbench_stable_json(value).encode("utf-8")).hexdigest()


_WORKBENCH_EMPTY_WORKSPACE_SKIP_DIRS = frozenset({
    ".git", ".github", ".vscode", ".idea", "__pycache__", "node_modules",
    ".venv", "venv", ".tox", ".egg-info", "dist", "build", "target",
    ".next", ".nuxt", ".cache",
})


def _is_workspace_empty(workspace_root: Path | None) -> bool:
    if not workspace_root or not workspace_root.is_dir():
        return True
    try:
        return not any(
            path for path in workspace_root.iterdir()
            if not path.name.startswith(".")
            and path.name not in _WORKBENCH_EMPTY_WORKSPACE_SKIP_DIRS
            and path.name not in {"LICENSE", "LICENSE.txt", "LICENSE.md"}
        )
    except OSError:
        return True


def _workbench_workspace_state(
    workspace_root: Path | None,
) -> tuple[str, dict[str, str]]:
    if not workspace_root or not workspace_root.is_dir():
        return "missing", {}
    digest = hashlib.sha256()
    snapshot: dict[str, str] = {}
    try:
        for root, dirs, files in os.walk(workspace_root):
            dirs[:] = sorted(
                name for name in dirs
                if not name.startswith(".")
                and name not in _WORKBENCH_EMPTY_WORKSPACE_SKIP_DIRS
            )
            root_path = Path(root)
            for name in [*dirs, *sorted(file for file in files if not file.startswith("."))]:
                path = root_path / name
                relative = path.relative_to(workspace_root).as_posix()
                try:
                    stat = path.stat()
                    row = ("d" if path.is_dir() else "f", relative, stat.st_size, stat.st_mtime_ns)
                except OSError:
                    row = ("?", relative, 0, 0)
                digest.update(_workbench_stable_json(row).encode("utf-8"))
                if len(snapshot) < 5000:
                    snapshot[relative + ("/" if path.is_dir() else "")] = f"{row[0]}:{row[2]}:{row[3]}"
    except OSError:
        return "unavailable", {}
    return digest.hexdigest(), snapshot


def _workbench_workspace_revision(workspace_root: Path | None) -> str:
    return _workbench_workspace_state(workspace_root)[0]


def _workbench_planning_thread(
    session: dict[str, Any], workspace_root: Path | None
) -> dict[str, Any]:
    thread = session.get("planningThread")
    thread = thread if isinstance(thread, dict) else {}
    current_root = str(workspace_root or "")
    if thread and str(thread.get("workspaceRoot") or "") not in {"", current_root}:
        thread = {}
    thread.setdefault("id", project_runtime._short_id("planning"))
    thread["contractVersion"] = planning_contracts._WORKBENCH_PLANNER_CONTRACT_VERSION
    thread.setdefault("messages", [])
    thread.setdefault("observationCache", {})
    thread.setdefault("inspectedResources", {})
    thread.setdefault("metrics", [])
    thread["workspaceRoot"] = current_root
    session["planningThread"] = thread
    return thread


def _workbench_planning_checkpoint(
    thread: dict[str, Any], latest_assistant_content: str
) -> list[dict[str, Any]]:
    checkpoint = {
        key: thread.get(key)
        for key in (
            "goal", "constraints", "currentPlan", "workspaceRevision",
            "inspectedResources", "confirmedFacts", "userDecisions",
        )
    }
    return [
        {"role": "user", "content": _workbench_stable_json(checkpoint)},
        {"role": "assistant", "content": str(latest_assistant_content or "")},
    ]


def _workbench_planning_context_chars(messages: list[dict[str, Any]]) -> int:
    return sum(len(_workbench_stable_json(message)) for message in messages)


def _workbench_maybe_compact_planning_thread(thread: dict[str, Any]) -> None:
    messages = thread.get("messages")
    if not isinstance(messages, list) or not messages or not thread.pop("compactionPending", False):
        return
    latest = str(messages[-1].get("content") or "") if isinstance(messages[-1], dict) else ""
    thread["messages"] = _workbench_planning_checkpoint(thread, latest)
    thread["compactionCount"] = int(thread.get("compactionCount") or 0) + 1


async def _workbench_classify_plan_routing(
    session: dict[str, Any],
    project: dict[str, Any],
    *,
    feedback: str,
    requested_operation: str,
    agent_runtime: Any = None,
) -> dict[str, Any]:
    fallback = {
        "workspaceRelationship": "unclear",
        "needsWorkspaceRefresh": False,
        "revisionMode": "replace" if requested_operation == "replace" else "revise",
    }
    if agent_runtime is None:
        return fallback
    try:
        response = await agent_runtime._model_response(
            project=project,
            session=session,
            messages=[{
                "role": "user",
                "content": (
                    "Return JSON with workspaceRelationship (related|independent|unclear), "
                    "needsWorkspaceRefresh, and revisionMode (revise|replace).\n\n"
                    + _workbench_stable_json({
                        "goal": session.get("goal") or session.get("title"),
                        "project": project.get("description") or project.get("name"),
                        "feedback": feedback,
                        "requestedOperation": requested_operation,
                    })
                ),
            }],
            purpose="task_plan_routing",
            max_tokens=300,
            response_format={"type": "json_object"},
        )
        parsed = _workbench_parse_json_object(str(response.get("content") or "")) or {}
    except Exception:
        return fallback
    relationship = str(parsed.get("workspaceRelationship") or "").lower()
    mode = str(parsed.get("revisionMode") or "").lower()
    return {
        "workspaceRelationship": relationship if relationship in {"related", "independent", "unclear"} else "unclear",
        "needsWorkspaceRefresh": parsed.get("needsWorkspaceRefresh") is True,
        "revisionMode": mode if mode in {"revise", "replace"} else fallback["revisionMode"],
    }


async def _workbench_plan_tool_bundle(
    session: dict[str, Any],
    project: dict[str, Any],
    workspace_root: Path | None,
    *,
    feedback: str,
    requested_operation: str,
    auto_start: bool,
    agent_runtime: Any = None,
) -> tuple[str, str, dict[str, str], dict[str, Any]]:
    revision, snapshot = _workbench_workspace_state(workspace_root)
    routing = await _workbench_classify_plan_routing(
        session,
        project,
        feedback=feedback,
        requested_operation=requested_operation,
        agent_runtime=agent_runtime,
    )
    bundle = (
        planning_contracts._WORKBENCH_PLANNER_NO_TOOLS_VERSION
        if _is_workspace_empty(workspace_root) or routing["workspaceRelationship"] == "independent"
        else planning_contracts._WORKBENCH_PLANNER_EXPLORE_VERSION
    )
    return bundle, revision, snapshot, routing


async def _workbench_repair_json_response(
    messages: list[dict[str, Any]],
    invalid_content: str,
    *,
    max_tokens: int,
    timeout: float,
    secondary: bool,
    agent_runtime: Any = None,
    project: dict[str, Any] | None = None,
    session: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    del messages, max_tokens, timeout, secondary
    parsed = _workbench_parse_json_object(invalid_content)
    if parsed is not None or agent_runtime is None or project is None or session is None:
        return parsed
    return await agent_runtime._independent_json_agent(
        project=project,
        session=session,
        purpose="json_repair",
        prompt="Repair this into exactly one JSON object:\n" + str(invalid_content or ""),
    )


async def _workbench_run_json_generation(
    prompt: str,
    *,
    max_tokens: int,
    timeout: float,
    secondary: bool = False,
    agent_runtime: Any = None,
    project: dict[str, Any] | None = None,
    session: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    del max_tokens, timeout, secondary
    if agent_runtime is None or project is None or session is None:
        return None
    try:
        return await agent_runtime._independent_json_agent(
            project=project,
            session=session,
            purpose="json_generation",
            prompt=prompt,
        )
    except Exception:
        return None


def _workbench_explore_parse_failure(response: Any, content: Any) -> _WorkbenchGenerationError:
    finish_reason = str(response.get("finish_reason") or "") if isinstance(response, dict) else ""
    if finish_reason == "length":
        return _WorkbenchGenerationError("truncated", "模型响应在 JSON 完成前被截断。")
    if not str(content or "").strip():
        return _WorkbenchGenerationError("empty_response", "模型返回了空响应。")
    return _WorkbenchGenerationError("response_format", "模型响应不是有效的 JSON 对象。")


async def _workbench_exec_explore_tool(*_args: Any, **_kwargs: Any) -> str:
    raise RuntimeError("workspace exploration is owned by TaskAgentRuntime Plugins")


async def _workbench_run_explore_agent(
    workspace_root: Path | None,
    prompt: str,
    *,
    agent_runtime: Any = None,
    project: dict[str, Any] | None = None,
    session: dict[str, Any] | None = None,
    raise_on_failure: bool = False,
    **_options: Any,
) -> dict[str, Any] | None:
    del workspace_root
    if agent_runtime is None or project is None or session is None:
        if raise_on_failure:
            raise _WorkbenchGenerationError(
                "configuration", "TaskAgentRuntime is required for workspace exploration."
            )
        return None
    try:
        return await agent_runtime._independent_json_agent(
            project=project,
            session=session,
            purpose="workspace_exploration",
            prompt=prompt,
        )
    except Exception as exc:
        if raise_on_failure:
            raise _workbench_generation_error(exc) from exc
        return None


def _workbench_init_workspace_relationship_guidance(project: dict[str, Any]) -> str:
    source = str(project.get("workspacePathSource") or "user").lower()
    if str(project.get("template") or "") == "import":
        return "已有文件是导入线索，仍需确认导入范围、保留边界和后续目标。"
    if source != "generated":
        return "已有文件只是待确认线索；先确认它们与新项目的关系，不要当作用户已确认的定位。"
    return "已有文件可作为现状线索，但探索结论仍需用户确认。"


def _workbench_coerce_init_form(
    raw: Any, base: dict[str, Any]
) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("sections"), list):
        return None
    sections: list[dict[str, Any]] = []
    used_section_ids: set[str] = set()
    for section_index, section in enumerate(raw["sections"]):
        if not isinstance(section, dict):
            continue
        title = str(section.get("title") or "").strip()
        questions_raw = section.get("questions")
        if not title or not isinstance(questions_raw, list):
            continue
        section_id = str(section.get("id") or f"section_{section_index + 1}").strip()
        while section_id in used_section_ids:
            section_id += f"_{section_index + 1}"
        used_section_ids.add(section_id)
        questions: list[dict[str, Any]] = []
        used_question_ids: set[str] = set()
        for question_index, question in enumerate(questions_raw):
            if not isinstance(question, dict):
                continue
            label = str(question.get("label") or question.get("question") or "").strip()
            if not label:
                continue
            question_id = str(
                question.get("id") or f"{section_id}_q{question_index + 1}"
            ).strip()
            while question_id in used_question_ids:
                question_id += f"_{question_index + 1}"
            used_question_ids.add(question_id)
            question_type = str(question.get("type") or "text").lower()
            if question_type not in planning_contracts._INIT_QUESTION_TYPES:
                question_type = "text"
            item: dict[str, Any] = {
                "id": question_id,
                "type": question_type,
                "label": label[:160],
            }
            placeholder = str(question.get("placeholder") or "").strip()
            if placeholder:
                item["placeholder"] = placeholder[:160]
            if question_type in {"single", "multi"}:
                options = [
                    label for option in question.get("options") or []
                    if (label := _option_label(option))
                ][:8]
                if options:
                    item["options"] = options
                else:
                    item["type"] = "text"
            questions.append(item)
        if questions:
            sections.append({
                "id": section_id,
                "title": title[:60],
                "questions": questions[:6],
            })
    if not sections:
        return None
    return {
        "generated": True,
        "completed": bool(base.get("completed")),
        "greeting": str(raw.get("greeting") or base.get("greeting") or "").strip(),
        "sections": sections[:6],
        "answers": base.get("answers") if isinstance(base.get("answers"), dict) else {},
    }


async def _workbench_generate_init_form(
    project: dict[str, Any],
    lang: str = "",
    *,
    agent_runtime: Any = None,
) -> dict[str, Any] | None:
    if agent_runtime is None:
        return None
    return await agent_runtime.generate_init_form(project, lang=lang)


def _workbench_init_brief(project: dict[str, Any], form: dict[str, Any]) -> str:
    answers = form.get("answers") if isinstance(form.get("answers"), dict) else {}
    lines = [f"# {project.get('name') or '项目'} · 初始化总结", ""]
    for section in form.get("sections") or []:
        if not isinstance(section, dict):
            continue
        values: list[str] = []
        for question in section.get("questions") or []:
            if not isinstance(question, dict):
                continue
            value = answers.get(question.get("id"))
            if isinstance(value, list):
                value = "、".join(str(item) for item in value if str(item).strip())
            if str(value or "").strip():
                values.append(f"- **{question.get('label')}** {str(value).strip()}")
        if values:
            lines.extend([f"## {section.get('title')}", *values, ""])
    return "\n".join(lines).strip()


def _workbench_answer_text(form: dict[str, Any], key: str) -> str:
    answers = form.get("answers") if isinstance(form.get("answers"), dict) else {}
    value = answers.get(key)
    if isinstance(value, list):
        return "、".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def _workbench_fallback_init_task_plan(
    project: dict[str, Any], form: dict[str, Any]
) -> list[dict[str, Any]]:
    goal = _workbench_answer_text(form, "goal") or str(project.get("description") or "").strip()
    requirements = _workbench_answer_text(form, "requirements")
    tech = _workbench_answer_text(form, "tech")
    out_of_scope = _workbench_answer_text(form, "out_of_scope")
    deadline = _workbench_answer_text(form, "deadline")
    constraints = [
        text for text in (
            f"范围限制：{out_of_scope}" if out_of_scope else "",
            f"时间约束：{deadline}" if deadline else "",
            f"偏好工具或平台：{tech}" if tech else "",
        ) if text
    ]
    target = goal or f"推进 {project.get('name') or '项目'}。"
    return [
        {
            "title": "明确目标与范围",
            "goal": "整理目标、背景和边界。" + (f" 重点覆盖：{requirements}" if requirements else ""),
            "priority": "high",
            "constraints": constraints,
            "acceptanceCriteria": ["目标清晰", "范围已定义", "优先级已确认"],
        },
        {
            "title": "制定执行方案",
            "goal": f"基于项目信息设计具体方案。项目总目标：{target}",
            "priority": "high",
            "constraints": constraints,
            "acceptanceCriteria": ["执行方案已形成", "步骤可追踪", "依赖已记录"],
        },
        {
            "title": "推进执行与交付",
            "goal": f"按计划完成并验证项目目标：{target}",
            "priority": "medium",
            "constraints": constraints,
            "acceptanceCriteria": ["项目目标已完成", "结果可验证", "符合预期要求"],
        },
    ]


def _workbench_coerce_init_task_plan(
    raw: Any, fallback: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    source = raw.get("tasks") if isinstance(raw, dict) else raw
    if not isinstance(source, list):
        return fallback
    tasks: list[dict[str, Any]] = []
    for index, item in enumerate(source[:8]):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        goal = str(item.get("goal") or item.get("description") or "").strip()
        if not title and goal:
            title = goal[:40]
        if not title:
            continue
        priority = str(item.get("priority") or "medium").lower()
        if priority not in {"high", "medium", "low"}:
            priority = "medium"
        constraints = item.get("constraints")
        acceptance = item.get("acceptanceCriteria") or item.get("acceptance")
        tasks.append({
            "id": str(item.get("id") or project_runtime._short_id("init_task")),
            "title": title[:80],
            "goal": (goal or title)[:1200],
            "priority": priority,
            "constraints": [str(value).strip() for value in constraints if str(value).strip()][:8]
            if isinstance(constraints, list) else [],
            "acceptanceCriteria": [str(value).strip() for value in acceptance if str(value).strip()][:8]
            if isinstance(acceptance, list) else [],
            "order": index + 1,
        })
    return tasks or fallback


async def _workbench_generate_init_task_plan(
    project: dict[str, Any],
    form: dict[str, Any],
    feedback: str = "",
    current_plan: list[dict[str, Any]] | None = None,
    max_attempts: int = 3,
    *,
    agent_runtime: Any = None,
    session: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]] | None, bool, dict[str, Any] | None]:
    if agent_runtime is None:
        error = _WorkbenchGenerationError(
            "configuration", "TaskAgentRuntime is required for initialization planning."
        )
        return None, False, {
            "code": "init_plan_generation_failed",
            "attemptCount": 1,
            "category": error.category,
            "summary": error.message,
            "attempts": [{"attempt": 1, "category": error.category, "message": error.message}],
        }
    return await agent_runtime.generate_init_plan(
        project,
        form,
        session=session,
        feedback=feedback,
        current_plan=current_plan,
        max_attempts=max_attempts,
    )


def _workbench_create_sessions_from_init_plan(
    project: dict[str, Any],
    plan: list[dict[str, Any]],
    now: str | None = None,
) -> list[dict[str, Any]]:
    now = now or project_runtime._utc_now_iso()
    created: list[dict[str, Any]] = []
    sessions = project.setdefault("sessions", [])
    for item in plan:
        if not isinstance(item, dict) or not str(item.get("title") or "").strip():
            continue
        title = str(item["title"]).strip()
        session = project_runtime._workbench_new_session(
            str(project.get("id") or ""),
            title,
            str(item.get("goal") or title).strip(),
            now,
            kind="task",
            status="idle",
        )
        priority = str(item.get("priority") or "medium").lower()
        if priority in {"high", "medium", "low"}:
            session["priority"] = priority
        session["constraints"] = [
            str(value).strip() for value in item.get("constraints") or [] if str(value).strip()
        ][:8]
        session["acceptanceCriteria"] = [
            {
                "id": project_runtime._short_id("accept"),
                "text": str(value).strip(),
                "status": "pending",
            }
            for value in item.get("acceptanceCriteria") or [] if str(value).strip()
        ][:8]
        session["events"] = [{
            "id": project_runtime._short_id("event"),
            "type": "CreatedFromInitPlan",
            "createdAt": now,
            "body": "由初始化计划确认后创建。",
        }]
        created.append(session)
    for session in reversed(created):
        sessions.insert(0, session)
    return created


__all__ = [
    "_WorkbenchAgentRunError",
    "_WorkbenchGenerationError",
    "_is_workspace_empty",
    "_workbench_answer_text",
    "_workbench_classify_plan_routing",
    "_workbench_coerce_init_form",
    "_workbench_coerce_init_task_plan",
    "_workbench_create_sessions_from_init_plan",
    "_workbench_exec_explore_tool",
    "_workbench_explore_parse_failure",
    "_workbench_fallback_init_task_plan",
    "_workbench_generate_init_form",
    "_workbench_generate_init_task_plan",
    "_workbench_generation_error",
    "_workbench_hash_json",
    "_workbench_init_brief",
    "_workbench_init_workspace_relationship_guidance",
    "_workbench_maybe_compact_planning_thread",
    "_workbench_parse_json_object",
    "_workbench_plan_tool_bundle",
    "_workbench_planning_checkpoint",
    "_workbench_planning_context_chars",
    "_workbench_planning_thread",
    "_workbench_redact_error_text",
    "_workbench_repair_json_response",
    "_workbench_run_explore_agent",
    "_workbench_run_json_generation",
    "_workbench_stable_json",
    "_workbench_workspace_revision",
    "_workbench_workspace_state",
]
