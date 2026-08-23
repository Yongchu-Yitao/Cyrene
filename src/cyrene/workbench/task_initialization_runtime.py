"""Task initialization and exploratory planning workflows."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import httpx

from cyrene.agent.context import bind_run_context
from cyrene.model_runtime.errors import format_httpx_error
from cyrene.observability import debug
from cyrene.tooling.result_store import (
    ToolResultReferenceError,
    project_tool_result_for_model,
    read_tool_result,
)
from cyrene.workbench import generation_gateway, planning_contracts, project_runtime

logger = logging.getLogger(__name__)


def _option_label(option: Any) -> str:
    """Return the display label emitted by permissive model schemas."""
    if isinstance(option, dict):
        for key in ("label", "text", "value", "title", "name"):
            value = str(option.get(key, "") or "").strip()
            if value:
                return value
        return ""
    return str(option or "").strip()

def _workbench_init_workspace_relationship_guidance(project: dict[str, Any]) -> str:
    """Prompt guardrails for non-empty workspaces during project init."""
    template = str(project.get('template') or '').strip()
    template_label = planning_contracts._WORKBENCH_TEMPLATE_LABELS.get(template, template or '空白项目')
    workspace_source = str(project.get('workspacePathSource') or 'user').strip().lower()
    user_selected_workspace = workspace_source != 'generated'
    if template == 'import':
        return '工作区关系判断：用户选择的是“导入项目”类型，可以把已有文件视为导入对象的重要线索，但仍需要用问题确认导入范围、保留/改造边界和后续目标。'
    if user_selected_workspace:
        return f'工作区关系判断：用户为新项目选择/使用了一个已有文件夹，且当前项目类型是「{template_label}」。这只说明工作区非空，不等于用户确认这些文件就是本项目，也不等于用户要围绕这些文件继续开发。尤其当项目类型是“空白项目”时，它可能只是默认选项，不能当作用户明确声明。\n生成表单时必须把已有文件当作“待确认线索”，不要把探索到的题材、IP、代码库、素材或文档直接描述成已确认的项目定位。第一组问题应优先确认：这些文件和新项目的关系（复用/导入、仅作参考、需要忽略、需要整理归档或另建空目录），以及用户真正想启动的目标。'
    return '工作区关系判断：工作区已有文件，可作为项目现状线索；仍不要把探索结论写成绝对事实，需要通过问题确认用户希望如何处理已有内容。'

class _WorkbenchGenerationError(RuntimeError):
    """Structured, user-displayable failure from a workbench generation call."""

    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = str(category or 'unknown')
        self.message = str(message or '未知错误')

class _WorkbenchAgentRunError(RuntimeError):
    """Structured failure from the main Workbench task agent.

    Agent execution must never degrade into an ordinary assistant reply: callers
    use this exception to persist/return an explicit failed or paused state.
    """

    def __init__(self, code: str, message: str, *, status_code: int=502):
        super().__init__(message)
        self.code = str(code or 'workbench_agent_run_failed')
        self.message = str(message or 'Agent 执行失败。')
        self.status_code = int(status_code)

def _workbench_redact_error_text(value: Any) -> str:
    text = str(value or '')
    text = re.sub('(?i)\\bBearer\\s+\\S+', 'Bearer <redacted>', text)
    text = re.sub('\\bsk-[A-Za-z0-9_-]{8,}\\b', 'sk-<redacted>', text)
    text = re.sub('(?i)(api[_ -]?key["\\\']?\\s*[:=]\\s*["\\\']?)[^"\\\'\\s,}]+', '\\1<redacted>', text)
    return text

def _workbench_generation_error(exc: Exception) -> _WorkbenchGenerationError:
    """Convert low-level model errors into useful, secret-safe UI details."""
    if isinstance(exc, _WorkbenchGenerationError):
        return exc
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, httpx.TimeoutException)):
        return _WorkbenchGenerationError('timeout', '模型请求超时。')
    if isinstance(exc, httpx.HTTPStatusError):
        status = int(exc.response.status_code)
        body = re.sub('\\s+', ' ', _workbench_redact_error_text(exc.response.text).strip())[:500]
        if status in (401, 403):
            category = 'authentication'
            summary = f'模型服务鉴权失败（HTTP {status}）。'
        elif status == 429:
            category = 'rate_limit'
            summary = '模型服务触发限流（HTTP 429）。'
        elif status >= 500:
            category = 'upstream'
            summary = f'模型服务暂时异常（HTTP {status}）。'
        else:
            category = 'http'
            summary = f'模型服务返回 HTTP {status}。'
        if body:
            summary += f' 响应：{body}'
        return _WorkbenchGenerationError(category, summary)
    if isinstance(exc, httpx.RequestError):
        return _WorkbenchGenerationError('network', _workbench_redact_error_text(format_httpx_error(exc)))
    return _WorkbenchGenerationError('internal', _workbench_redact_error_text(f"{type(exc).__name__}: {str(exc or '未知错误').strip()}"))

def _workbench_coerce_init_form(raw: Any, base: dict[str, Any]) -> dict[str, Any] | None:
    """Validate/normalize an LLM-produced init form into our schema.

    Returns ``None`` when the payload is unusable so the caller can keep the
    deterministic fallback.
    """
    if not isinstance(raw, dict):
        return None
    raw_sections = raw.get('sections')
    if not isinstance(raw_sections, list) or not raw_sections:
        return None
    sections: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for s_index, section in enumerate(raw_sections):
        if not isinstance(section, dict):
            continue
        title = str(section.get('title') or '').strip()
        raw_questions = section.get('questions')
        if not title or not isinstance(raw_questions, list):
            continue
        sid = str(section.get('id') or '').strip() or f'section_{s_index + 1}'
        while sid in used_ids:
            sid = f'{sid}_{s_index + 1}'
        used_ids.add(sid)
        questions: list[dict[str, Any]] = []
        used_q_ids: set[str] = set()
        for q_index, question in enumerate(raw_questions):
            if not isinstance(question, dict):
                continue
            label = str(question.get('label') or question.get('question') or '').strip()
            if not label:
                continue
            qtype = str(question.get('type') or 'text').strip().lower()
            if qtype not in planning_contracts._INIT_QUESTION_TYPES:
                qtype = 'text'
            qid = str(question.get('id') or '').strip() or f'{sid}_q{q_index + 1}'
            while qid in used_q_ids:
                qid = f'{qid}_{q_index + 1}'
            used_q_ids.add(qid)
            item: dict[str, Any] = {'id': qid, 'type': qtype, 'label': label[:160]}
            placeholder = str(question.get('placeholder') or '').strip()
            if placeholder:
                item['placeholder'] = placeholder[:160]
            if qtype in ('single', 'multi'):
                options = [lbl for o in question.get('options', []) if (lbl := _option_label(o))]
                if not options:
                    qtype = 'text'
                    item['type'] = 'text'
                else:
                    item['options'] = options[:8]
            questions.append(item)
        if questions:
            sections.append({'id': sid, 'title': title[:60], 'questions': questions[:6]})
    if not sections:
        return None
    greeting = str(raw.get('greeting') or '').strip() or base.get('greeting', '')
    return {'generated': True, 'completed': bool(base.get('completed')), 'greeting': greeting, 'sections': sections[:6], 'answers': base.get('answers') if isinstance(base.get('answers'), dict) else {}}
_WORKBENCH_EMPTY_WORKSPACE_SKIP_DIRS = frozenset({'.git', '.github', '.vscode', '.idea', '__pycache__', 'node_modules', '.venv', 'venv', '.tox', '.egg-info', 'dist', 'build', 'target', '.next', '.nuxt', '.cache'})

def _is_workspace_empty(workspace_root: Path | None) -> bool:
    """Return True when the workspace directory is missing, empty, or only
    contains hidden / build-artifact metadata (no actual source files)."""
    if not workspace_root or not workspace_root.is_dir():
        return True
    try:
        for p in workspace_root.iterdir():
            if p.name.startswith('.') or p.name in _WORKBENCH_EMPTY_WORKSPACE_SKIP_DIRS:
                continue
            if p.name in ('LICENSE', 'LICENSE.txt', 'LICENSE.md'):
                continue
            return False
    except OSError:
        pass
    return True
_WORKBENCH_EXPLORE_TOOLS = [{'type': 'function', 'function': {'name': 'read_tool_result', 'description': '按范围读取或搜索本会话中先前被截断的完整工具结果。', 'parameters': {'type': 'object', 'properties': {'content_ref': {'type': 'string'}, 'offset': {'type': 'integer', 'minimum': 0, 'default': 0}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100000, 'default': 4000}, 'query': {'type': 'string'}}, 'required': ['content_ref'], 'additionalProperties': False}}}, {'type': 'function', 'function': {'name': 'list_directory', 'description': '列出工作区指定路径下的文件和目录。返回文件名/目录名列表，不递归。', 'parameters': {'type': 'object', 'properties': {'path': {'type': 'string', 'description': "相对于工作区根目录的路径，例如 '.'（根目录）或 'src'。默认 '.'", 'default': '.'}}}}}, {'type': 'function', 'function': {'name': 'read_file', 'description': '按字符范围读取工作区中的文本文件。优先读取尚未观察的范围；二进制文件会提示不可读。', 'parameters': {'type': 'object', 'properties': {'path': {'type': 'string', 'description': "相对于工作区根目录的文件路径，例如 'README.md' 或 'src/main.py'"}, 'offset': {'type': 'integer', 'minimum': 0, 'default': 0, 'description': '从第几个字符开始读取，默认 0'}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 12000, 'default': 4000, 'description': '最多读取多少字符，默认 4000'}}, 'required': ['path']}}}, {'type': 'function', 'function': {'name': 'glob', 'description': "按通配符模式搜索工作区中的文件路径。支持 ** 递归匹配。例如：'**/*.py' 查找所有 Python 文件，'*.toml' 查找根目录下的 TOML 文件，'src/**/*.tsx' 查找 src 下所有 React 组件。自动跳过隐藏文件。最多返回 50 条结果。", 'parameters': {'type': 'object', 'properties': {'pattern': {'type': 'string', 'description': 'glob 搜索模式，相对于工作区根目录'}}, 'required': ['pattern']}}}]

def _workbench_parse_json_object(text: str) -> dict[str, Any] | None:
    """Parse a JSON object from an LLM reply, tolerating prose / code fences.

    Models often wrap the JSON in a ```json … ``` fence and/or prefix it with
    prose ("以下是总结：…"), so try several extractions before giving up.
    """
    raw = str(text or '').strip()
    if not raw:
        return None
    candidates: list[str] = [raw]
    fence = re.search('```(?:json)?\\s*(.*?)```', raw, re.DOTALL | re.IGNORECASE)
    if fence and fence.group(1).strip():
        candidates.append(fence.group(1).strip())
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    decoder = json.JSONDecoder()
    top_level_object_starts: list[int] = []
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(raw):
        if in_string:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == '{':
            if depth == 0:
                top_level_object_starts.append(index)
            depth += 1
        elif char == '}' and depth > 0:
            depth -= 1
    for start in top_level_object_starts:
        try:
            parsed, _end = decoder.raw_decode(raw[start:])
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None

async def _workbench_repair_json_response(messages: list[dict[str, Any]], invalid_content: str, *, max_tokens: int, timeout: float, secondary: bool) -> dict[str, Any] | None:
    """Ask the model once to convert a malformed final reply into strict JSON."""
    content = str(invalid_content or '').strip()
    repair_messages = list(messages)
    if content:
        repair_messages.append({'role': 'assistant', 'content': content})
    repair_messages.append({'role': 'user', 'content': '你刚才的最终回答无法解析为 JSON。不要继续探索，也不要解释。请保留原回答的结论和字段，只修正格式，并且只输出一个合法 JSON 对象。不要使用 Markdown 代码块，不要输出 JSON 之外的任何文字。（输出必须是单个合法的 json 对象。）'})
    repaired = await asyncio.wait_for(generation_gateway.call_llm(repair_messages, tools=None, max_tokens=max_tokens, secondary=secondary, thinking='disabled', response_format=planning_contracts._WORKBENCH_JSON_RESPONSE_FORMAT), timeout=timeout)
    if not isinstance(repaired, dict):
        return None
    return _workbench_parse_json_object(repaired.get('content') or '')

def _workbench_explore_parse_failure(response: Any, content: Any) -> _WorkbenchGenerationError:
    """Classify a final reply that survived parse + repair but is still not a
    JSON object.

    An empty body or a ``finish_reason == "length"`` truncation is a transient
    glitch worth retrying, so it gets its own category rather than the generic
    ``response_format`` verdict (which callers may treat as a hard failure).
    """
    finish_reason = str(response.get('finish_reason') or '') if isinstance(response, dict) else ''
    stripped = _workbench_redact_error_text(str(content or '')).strip()
    preview = re.sub('\\s+', ' ', stripped)[:500]
    if finish_reason == 'length':
        detail = '模型在产出 JSON 前被 max_tokens 截断（finish_reason=length）。'
        if preview:
            detail += f' 已生成片段：{preview[:300]}'
        return _WorkbenchGenerationError('truncated', detail)
    if not stripped:
        return _WorkbenchGenerationError('empty_response', '模型返回了空响应。')
    detail = '模型响应不是有效的 JSON 对象。'
    if preview:
        detail += f' 响应片段：{preview}'
    return _WorkbenchGenerationError('response_format', detail)

async def _workbench_run_json_generation(prompt: str, *, max_tokens: int, timeout: float, secondary: bool=False) -> dict[str, Any] | None:
    """Run a no-tool JSON generation call and parse/repair the final object."""
    messages = [{'role': 'user', 'content': prompt}]
    try:
        response = await asyncio.wait_for(generation_gateway.call_llm(messages, tools=None, max_tokens=max_tokens, secondary=secondary, thinking='disabled', response_format=planning_contracts._WORKBENCH_JSON_RESPONSE_FORMAT), timeout=timeout)
    except Exception:
        logger.exception('Workbench JSON generation failed')
        return None
    if not isinstance(response, dict):
        return None
    content = response.get('content') or ''
    parsed = _workbench_parse_json_object(content)
    if parsed is not None:
        return parsed
    try:
        return await _workbench_repair_json_response(messages, content, max_tokens=max_tokens, timeout=timeout, secondary=secondary)
    except Exception:
        logger.exception('Workbench JSON generation repair failed')
        return None

def _workbench_stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))

def _workbench_hash_json(value: Any) -> str:
    return hashlib.sha256(_workbench_stable_json(value).encode('utf-8')).hexdigest()

def _workbench_workspace_state(workspace_root: Path | None) -> tuple[str, dict[str, str]]:
    """Cheap tree revision used to invalidate directory/glob observations.

    File observations still carry a content SHA-256. The tree revision uses
    names, types, sizes and mtimes so an unchanged workspace can be recognized
    without rereading every file body before each planning revision.
    """
    if not workspace_root or not workspace_root.is_dir():
        return ('missing', {})
    digest = hashlib.sha256()
    snapshot: dict[str, str] = {}
    try:
        for root, dirs, files in os.walk(workspace_root):
            dirs[:] = sorted((name for name in dirs if not name.startswith('.') and name not in _WORKBENCH_EMPTY_WORKSPACE_SKIP_DIRS))
            root_path = Path(root)
            for name in dirs:
                path = root_path / name
                rel = path.relative_to(workspace_root).as_posix()
                try:
                    stat = path.stat()
                    row = ('d', rel, stat.st_mtime_ns)
                except OSError:
                    row = ('d', rel, 0)
                digest.update(_workbench_stable_json(row).encode('utf-8'))
                if len(snapshot) < 5000:
                    snapshot[rel + '/'] = f'd:{row[-1]}'
            for name in sorted(files):
                if name.startswith('.'):
                    continue
                path = root_path / name
                rel = path.relative_to(workspace_root).as_posix()
                try:
                    stat = path.stat()
                    row = ('f', rel, stat.st_size, stat.st_mtime_ns)
                except OSError:
                    row = ('f', rel, 0, 0)
                digest.update(_workbench_stable_json(row).encode('utf-8'))
                if len(snapshot) < 5000:
                    snapshot[rel] = f'f:{row[-2]}:{row[-1]}'
    except OSError:
        return ('unavailable', {})
    return (digest.hexdigest(), snapshot)

def _workbench_workspace_revision(workspace_root: Path | None) -> str:
    return _workbench_workspace_state(workspace_root)[0]

def _workbench_planning_thread(session: dict[str, Any], workspace_root: Path | None) -> dict[str, Any]:
    raw = session.get('planningThread')
    thread = raw if isinstance(raw, dict) else {}
    current_root = str(workspace_root or '')
    if thread and str(thread.get('workspaceRoot') or '') not in ('', current_root):
        thread = {}
    if str(thread.get('contractVersion') or '') != planning_contracts._WORKBENCH_PLANNER_CONTRACT_VERSION:
        thread = {}
    thread.setdefault('id', project_runtime._short_id('planning'))
    thread['contractVersion'] = planning_contracts._WORKBENCH_PLANNER_CONTRACT_VERSION
    thread.setdefault('messages', [])
    thread.setdefault('observationCache', {})
    thread.setdefault('inspectedResources', {})
    thread.setdefault('metrics', [])
    thread['workspaceRoot'] = current_root
    session['planningThread'] = thread
    return thread

def _workbench_planning_checkpoint(thread: dict[str, Any], latest_assistant_content: str) -> list[dict[str, Any]]:
    inspected = thread.get('inspectedResources')
    checkpoint = {'type': 'planning_checkpoint', 'goal': thread.get('goal') or '', 'constraints': thread.get('constraints') or [], 'currentPlan': thread.get('currentPlan') or [], 'workspaceRevision': thread.get('workspaceRevision') or '', 'inspectedResources': inspected if isinstance(inspected, dict) else {}, 'confirmedFacts': thread.get('confirmedFacts') or [], 'userDecisions': thread.get('userDecisions') or [], 'doNotRepeat': sorted((inspected or {}).keys()) if isinstance(inspected, dict) else []}
    return [{'role': 'system', 'content': planning_contracts._WORKBENCH_PLANNER_SYSTEM_PROMPT}, {'role': 'user', 'content': _workbench_stable_json(checkpoint)}, {'role': 'assistant', 'content': latest_assistant_content}]

def _workbench_planning_context_chars(messages: list[dict[str, Any]]) -> int:
    return sum((len(_workbench_stable_json(message)) for message in messages))

def _workbench_maybe_compact_planning_thread(thread: dict[str, Any]) -> None:
    messages = thread.get('messages')
    if not isinstance(messages, list) or not messages:
        return
    if not thread.pop('compactionPending', False):
        return
    latest_content = str(messages[-1].get('content') or '') if isinstance(messages[-1], dict) else ''
    thread['messages'] = _workbench_planning_checkpoint(thread, latest_content)
    thread['compactionCount'] = int(thread.get('compactionCount') or 0) + 1

async def _workbench_classify_plan_routing(session: dict[str, Any], project: dict[str, Any], *, feedback: str, requested_operation: str) -> dict[str, Any]:
    """Semantically decide workspace use and plan revision behavior."""
    goal = str(session.get('goal') or session.get('title') or '').strip()
    project_context = project.get('context') if isinstance(project.get('context'), dict) else {}
    prompt = f"""请为任务规划器做一次语义路由判断。不要按关键词机械判断，要结合任务目标、项目说明和本次反馈的真实含义。只返回 JSON：{{"workspaceRelationship":"related|independent|unclear","needsWorkspaceRefresh":true|false,"revisionMode":"revise|replace"}}。\nworkspaceRelationship：任务是否需要当前项目/代码工作区的信息。needsWorkspaceRefresh：本次反馈是否要求核对当前文件、实现或项目事实；仅调整步骤文字、顺序或依赖时为 false。revisionMode：保留并协调已有步骤用 revise；只有用户明确要求舍弃原计划或目标/路线整体改变时才用 replace。否定表达必须按语义理解。\n\n项目名称：{project.get('name') or ''}\n项目说明：{project.get('description') or project_context.get('summary') or ''}\n任务目标：{goal}\n本次反馈：{feedback or '（无）'}"""
    fallback = {'workspaceRelationship': 'unclear', 'needsWorkspaceRefresh': False, 'revisionMode': 'revise'}
    try:
        response = await asyncio.wait_for(generation_gateway.call_llm([{'role': 'user', 'content': prompt}], tools=None, max_tokens=300, secondary=True, thinking='disabled'), timeout=20)
    except Exception:
        logger.exception('Workbench plan routing classification failed')
        return fallback
    content = str(response.get('content') or '') if isinstance(response, dict) else ''
    parsed = _workbench_parse_json_object(content)
    if not isinstance(parsed, dict):
        return fallback
    relationship = str(parsed.get('workspaceRelationship') or '').strip().lower()
    revision_mode = str(parsed.get('revisionMode') or '').strip().lower()
    return {'workspaceRelationship': relationship if relationship in {'related', 'independent', 'unclear'} else 'unclear', 'needsWorkspaceRefresh': parsed.get('needsWorkspaceRefresh') is True, 'revisionMode': revision_mode if revision_mode in {'revise', 'replace'} else 'revise'}

async def _workbench_plan_tool_bundle(session: dict[str, Any], project: dict[str, Any], workspace_root: Path | None, *, feedback: str, requested_operation: str, auto_start: bool) -> tuple[str, str, dict[str, str], dict[str, Any]]:
    current_revision, current_snapshot = _workbench_workspace_state(workspace_root)
    thread = _workbench_planning_thread(session, workspace_root)
    previous_revision = str(thread.get('workspaceRevision') or '')
    has_history = bool(thread.get('messages'))
    workspace_changed = bool(previous_revision and previous_revision != current_revision)
    workspace_empty = _is_workspace_empty(workspace_root)
    routing = {'workspaceRelationship': 'unclear', 'needsWorkspaceRefresh': False, 'revisionMode': 'revise'} if workspace_empty else await _workbench_classify_plan_routing(session, project, feedback=feedback, requested_operation=requested_operation)
    if workspace_empty:
        bundle = planning_contracts._WORKBENCH_PLANNER_NO_TOOLS_VERSION
    elif routing['workspaceRelationship'] == 'independent':
        bundle = planning_contracts._WORKBENCH_PLANNER_NO_TOOLS_VERSION
    elif auto_start:
        bundle = planning_contracts._WORKBENCH_PLANNER_EXPLORE_VERSION
    elif not has_history:
        bundle = planning_contracts._WORKBENCH_PLANNER_EXPLORE_VERSION
    elif workspace_changed or routing['needsWorkspaceRefresh']:
        bundle = planning_contracts._WORKBENCH_PLANNER_EXPLORE_VERSION
    else:
        bundle = planning_contracts._WORKBENCH_PLANNER_NO_TOOLS_VERSION
    return (bundle, current_revision, current_snapshot, routing)

async def _workbench_exec_explore_tool(tc: dict, workspace_root: Path | None, *, session_id: str='', observation_cache: dict[str, Any] | None=None, runtime_cache: dict[str, str] | None=None, metrics: dict[str, int] | None=None, workspace_revision: str='', inspected_resources: dict[str, Any] | None=None) -> str:
    """Execute one workspace-exploration tool call, confined to workspace_root."""
    name = tc['function']['name']
    try:
        args = json.loads(tc['function'].get('arguments') or '{}')
    except json.JSONDecodeError:
        return 'Error: invalid tool arguments'
    if name == 'read_tool_result':
        try:
            return read_tool_result(str(args.get('content_ref') or ''), offset=int(args.get('offset') or 0), limit=int(args.get('limit') or 4000), query=str(args.get('query') or ''), session_id=session_id)
        except (TypeError, ValueError, ToolResultReferenceError) as exc:
            return f'Error: {exc}'
    rel_path = str(args.get('path') or '.').strip()
    if not workspace_root or not workspace_root.is_dir():
        return 'Error: workspace directory does not exist or is inaccessible'
    target = (workspace_root / rel_path).resolve()
    try:
        target.relative_to(workspace_root)
    except ValueError:
        return 'Error: path is outside the workspace directory'
    observation_cache = observation_cache if isinstance(observation_cache, dict) else {}
    runtime_cache = runtime_cache if isinstance(runtime_cache, dict) else {}
    inspected_resources = inspected_resources if isinstance(inspected_resources, dict) else {}
    metrics = metrics if isinstance(metrics, dict) else {}
    metrics.setdefault('workspaceCacheHits', 0)
    metrics.setdefault('workspaceCacheMisses', 0)
    metrics.setdefault('duplicateCallsBlocked', 0)
    normalized_args: dict[str, Any]
    if name == 'read_file':
        try:
            offset = max(0, int(args.get('offset') or 0))
        except (TypeError, ValueError):
            offset = 0
        try:
            limit = min(12000, max(1, int(args.get('limit') or 4000)))
        except (TypeError, ValueError):
            limit = 4000
        normalized_args = {'path': rel_path, 'offset': offset, 'limit': limit}
    elif name == 'glob':
        normalized_args = {'pattern': str(args.get('pattern') or '').strip()}
    else:
        normalized_args = {'path': rel_path}
    logical_key = f'{workspace_root.as_posix()}:{name}:{_workbench_stable_json(normalized_args)}'
    if logical_key in runtime_cache:
        metrics['duplicateCallsBlocked'] += 1
        return runtime_cache[logical_key]
    try:
        if name == 'list_directory':
            stat = target.stat()
            fingerprint = f'{workspace_revision}:{stat.st_mtime_ns}'
            cached = observation_cache.get(logical_key)
            if isinstance(cached, dict) and cached.get('resourceFingerprint') == fingerprint:
                result = str(cached.get('result') or '')
                runtime_cache[logical_key] = result
                metrics['workspaceCacheHits'] += 1
                return result
            entries: list[str] = []
            for p in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                if p.name.startswith('.'):
                    continue
                suffix = '/' if p.is_dir() else ''
                entries.append(f'{p.name}{suffix}')
            result = '\n'.join(entries) if entries else '(empty directory)'
        elif name == 'read_file':
            if not target.is_file():
                return 'Error: not a file or does not exist'
            if target.stat().st_size > 256 * 1024:
                return 'Error: file too large (>256KB)'
            stat = target.stat()
            stat_fingerprint = f'{stat.st_size}:{stat.st_mtime_ns}'
            cached = observation_cache.get(logical_key)
            if isinstance(cached, dict) and cached.get('statFingerprint') == stat_fingerprint:
                result = str(cached.get('result') or '')
                runtime_cache[logical_key] = result
                metrics['workspaceCacheHits'] += 1
                return result
            try:
                text = target.read_text(encoding='utf-8', errors='replace')
                file_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()
                result = text[offset:offset + limit]
                if offset + limit < len(text):
                    result += f'\n\n...(truncated; next offset={offset + limit})'
                fingerprint = file_hash
            except (UnicodeDecodeError, LookupError):
                return 'Error: binary file (cannot read as text)'
        elif name == 'glob':
            pattern = normalized_args['pattern']
            if not pattern:
                return 'Error: missing glob pattern'
            fingerprint = workspace_revision
            cached = observation_cache.get(logical_key)
            if isinstance(cached, dict) and cached.get('resourceFingerprint') == fingerprint:
                result = str(cached.get('result') or '')
                runtime_cache[logical_key] = result
                metrics['workspaceCacheHits'] += 1
                return result
            it = workspace_root.rglob(pattern.lstrip('/'))
            matches: list[str] = []
            for p in sorted(it):
                if any((part.startswith('.') or part in _WORKBENCH_EMPTY_WORKSPACE_SKIP_DIRS for part in p.relative_to(workspace_root).parts)):
                    continue
                rel = str(p.relative_to(workspace_root))
                suffix = '/' if p.is_dir() else ''
                matches.append(f'{rel}{suffix}')
            if len(matches) > 50:
                matches = matches[:50] + [f'... and {len(matches) - 50} more']
            result = '\n'.join(matches) if matches else '(no matches)'
        else:
            return f"Error: unknown tool '{name}'"
    except PermissionError:
        return 'Error: permission denied'
    except OSError as e:
        return f'Error: {e}'
    metrics['workspaceCacheMisses'] += 1
    record = {'tool': name, 'canonicalArgs': normalized_args, 'workspaceRevision': workspace_revision, 'resourceFingerprint': fingerprint, 'result': result, 'facts': [f"已读取 {rel_path} 的字符范围 {normalized_args.get('offset', 0)}..{normalized_args.get('offset', 0) + normalized_args.get('limit', 0)}" if name == 'read_file' else f'已观察 {name} 参数 {_workbench_stable_json(normalized_args)}'], 'valid': True}
    if name == 'read_file':
        record['statFingerprint'] = stat_fingerprint
    observation_cache[logical_key] = record
    runtime_cache[logical_key] = result
    inspected_resources[logical_key] = {'resourceFingerprint': fingerprint, 'workspaceRevision': workspace_revision, 'facts': record['facts']}
    return result

async def _workbench_run_explore_agent(workspace_root: Path | None, prompt: str, *, max_turns: int=8, max_tokens: int | None=9000, timeout: float=90, secondary: bool=False, session_id: str='', clean_context: bool=False, raise_on_failure: bool=False, planning_thread: dict[str, Any] | None=None, tool_bundle_version: str=planning_contracts._WORKBENCH_PLANNER_EXPLORE_VERSION, workspace_revision: str='') -> dict[str, Any] | None:
    """Run an LLM that may explore the workspace (list_directory/read_file/glob)
    before answering, and return the JSON object it emits (or None on failure).

    Rich workspaces can tempt the model to keep exploring past the turn budget,
    so after ``max_turns`` of tool use we force one final answer WITHOUT tools —
    the model must return the JSON from what it has already gathered.

    When ``session_id`` is given the run is tagged with it (via the agent-state
    ContextVar) so each LLM "thinking" round and exploration tool call publishes
    a live SSE event the workbench task card can stream — otherwise this agent
    works invisibly and the UI can only show a spinner.

    ``clean_context=True`` keeps the model call detached from the task's agent
    session while still publishing explicit tool events to ``session_id``. Use
    this for independent reviewers that must not inherit execution context.
    """
    event_sid = str(session_id or '').strip()
    context_sid = '' if clean_context else event_sid
    binding = bind_run_context(session_id=context_sid) if event_sid or clean_context else None
    thread = planning_thread if isinstance(planning_thread, dict) else None
    use_explore_tools = tool_bundle_version == planning_contracts._WORKBENCH_PLANNER_EXPLORE_VERSION
    tools: list[dict[str, Any]] | None = _WORKBENCH_EXPLORE_TOOLS if use_explore_tools else None
    observation_cache = thread.setdefault('observationCache', {}) if thread is not None else {}
    inspected_resources = thread.setdefault('inspectedResources', {}) if thread is not None else {}
    runtime_cache: dict[str, str] = {}
    call_metrics: dict[str, Any] = {'promptBundleVersion': planning_contracts._WORKBENCH_PLANNER_CONTRACT_VERSION if thread is not None else '', 'toolBundleVersion': tool_bundle_version if thread is not None else '', 'systemPromptHash': hashlib.sha256(planning_contracts._WORKBENCH_PLANNER_SYSTEM_PROMPT.encode('utf-8')).hexdigest() if thread is not None else '', 'toolsHash': _workbench_hash_json(tools or []), 'planningThreadId': str(thread.get('id') or '') if thread is not None else '', 'workspaceRevision': workspace_revision, 'promptTokens': 0, 'cachedTokens': 0, 'workspaceCacheHits': 0, 'workspaceCacheMisses': 0, 'duplicateCallsBlocked': 0}

    def _record_usage(response: Any) -> None:
        if not isinstance(response, dict):
            return
        usage = response.get('usage')
        if not isinstance(usage, dict):
            return
        call_metrics['promptTokens'] += int(usage.get('prompt_tokens') or 0)
        call_metrics['cachedTokens'] += int(usage.get('prompt_cache_hit_tokens') or usage.get('cached_tokens') or 0)

    def _commit_thread(messages: list[dict[str, Any]], content: str, parsed: dict[str, Any]) -> None:
        if thread is None:
            return
        final_content = str(content or '').strip() or _workbench_stable_json(parsed)
        if not messages or messages[-1].get('role') != 'assistant':
            messages.append({'role': 'assistant', 'content': final_content})
        thread['messages'] = messages
        thread['workspaceRevision'] = workspace_revision
        thread['lastToolBundleVersion'] = tool_bundle_version
        metrics_history = thread.setdefault('metrics', [])
        if isinstance(metrics_history, list):
            metrics_history.append(dict(call_metrics))
            if len(metrics_history) > 50:
                del metrics_history[:-50]
        if _workbench_planning_context_chars(messages) > planning_contracts._WORKBENCH_PLANNING_THREAD_MAX_CHARS:
            thread['compactionPending'] = True
        logger.info('Workbench planning metrics: %s', _workbench_stable_json(call_metrics))

    async def _emit_tool_event(tc: dict[str, Any]) -> None:
        if not event_sid:
            return
        try:
            fn = tc.get('function') or {}
            name = str(fn.get('name') or '').strip()
            if not name:
                return
            try:
                args = json.loads(fn.get('arguments') or '{}')
            except (json.JSONDecodeError, TypeError):
                args = {}
            await debug.publish_event({'type': 'tool_call', 'session_id': event_sid, 'tool': name, 'args': args, 'caller': 'explore', 'timestamp': project_runtime._utc_now_iso()})
        except Exception:
            pass
    try:
        if thread is not None:
            prior_messages = thread.get('messages')
            messages = [dict(message) for message in prior_messages if isinstance(message, dict)] if isinstance(prior_messages, list) else []
            if not messages:
                messages.append({'role': 'system', 'content': planning_contracts._WORKBENCH_PLANNER_SYSTEM_PROMPT})
            messages.append({'role': 'user', 'content': prompt})
        else:
            messages = [{'role': 'user', 'content': prompt}]
        for turn in range(max_turns):
            try:
                response = await asyncio.wait_for(generation_gateway.call_llm(messages, tools=tools, max_tokens=max_tokens, secondary=secondary, thinking='disabled'), timeout=timeout)
            except Exception as exc:
                logger.exception('Workbench explore-agent failed (turn %d)', turn + 1)
                if raise_on_failure:
                    raise _workbench_generation_error(exc)
                return None
            if not isinstance(response, dict):
                error = _WorkbenchGenerationError('configuration', '模型未配置，或模型服务返回了空响应。')
                if raise_on_failure:
                    raise error
                return None
            _record_usage(response)
            tool_calls = response.get('tool_calls') or []
            if not tool_calls:
                content = response.get('content') or ''
                parsed = _workbench_parse_json_object(content)
                if parsed is not None:
                    _commit_thread(messages, content, parsed)
                    return parsed
                try:
                    repaired = await _workbench_repair_json_response(messages, content, max_tokens=max_tokens, timeout=timeout, secondary=secondary)
                except Exception as exc:
                    logger.exception('Workbench explore-agent JSON repair failed')
                    if raise_on_failure:
                        raise _workbench_generation_error(exc)
                    return None
                if repaired is not None:
                    _commit_thread(messages, _workbench_stable_json(repaired), repaired)
                    return repaired
                if raise_on_failure:
                    raise _workbench_explore_parse_failure(response, content)
                return None
            if tools is None:
                messages.append({'role': 'assistant', 'content': response.get('content') or '', 'tool_calls': tool_calls})
                for tc in tool_calls:
                    messages.append({'role': 'tool', 'tool_call_id': tc.get('id') or project_runtime._short_id('blocked_tool'), 'content': 'Error: workspace tools are not available for this planning revision; use existing observations.'})
                messages.append({'role': 'user', 'content': '不要调用工具。请基于已有规划历史和观察结果直接返回最终 JSON。'})
                continue
            assistant_entry: dict[str, Any] = {'role': 'assistant', 'content': response.get('content') or '', 'tool_calls': tool_calls}
            if response.get('reasoning_content'):
                assistant_entry['reasoning_content'] = response['reasoning_content']
            messages.append(assistant_entry)
            for tc in tool_calls:
                await _emit_tool_event(tc)
                result = await _workbench_exec_explore_tool(tc, workspace_root, session_id=event_sid, observation_cache=observation_cache, runtime_cache=runtime_cache, metrics=call_metrics, workspace_revision=workspace_revision, inspected_resources=inspected_resources)
                projected = project_tool_result_for_model(result, tool_name=str((tc.get('function') or {}).get('name') or ''), tool_call_id=tc['id'], session_id=event_sid, secondary=secondary)
                messages.append({'role': 'tool', 'tool_call_id': tc['id'], 'content': projected.content})
            if call_metrics['duplicateCallsBlocked'] >= 2:
                tools = None
        messages.append({'role': 'user', 'content': '请停止探索。基于你已经了解到的信息，现在只返回最终的 JSON 对象本身，不要再调用任何工具，也不要任何额外说明或 Markdown 代码块标记。（输出必须是单个合法的 json 对象。）'})
        try:
            final = await asyncio.wait_for(generation_gateway.call_llm(messages, tools=None, max_tokens=max_tokens, secondary=secondary, thinking='disabled', response_format=planning_contracts._WORKBENCH_JSON_RESPONSE_FORMAT), timeout=timeout)
        except Exception as exc:
            logger.exception('Workbench explore-agent final answer failed')
            if raise_on_failure:
                raise _workbench_generation_error(exc)
            return None
        _record_usage(final)
        if not isinstance(final, dict):
            if raise_on_failure:
                raise _WorkbenchGenerationError('configuration', '模型未配置，或模型服务返回了空响应。')
            return None
        content = final.get('content') or ''
        parsed = _workbench_parse_json_object(content)
        if parsed is not None:
            _commit_thread(messages, content, parsed)
            return parsed
        try:
            repaired = await _workbench_repair_json_response(messages, content, max_tokens=max_tokens, timeout=timeout, secondary=secondary)
        except Exception as exc:
            logger.exception('Workbench explore-agent final JSON repair failed')
            if raise_on_failure:
                raise _workbench_generation_error(exc)
            return None
        if repaired is not None:
            _commit_thread(messages, _workbench_stable_json(repaired), repaired)
            return repaired
        if raise_on_failure:
            raise _workbench_explore_parse_failure(final, content)
        return None
    finally:
        if binding is not None:
            binding.reset()

async def _workbench_generate_init_form(project: dict[str, Any], lang: str='') -> dict[str, Any] | None:
    """Ask an agent (with file-exploration tools) to produce onboarding
    questions tailored to this project.

    If the workspace is empty (no real source files), use the user's project
    description to generate tailored questions without workspace tools. If
    there is no description or generation fails, fall back to the deterministic
    template form.

    ``lang`` is the user's UI language code (e.g. ``"zh"``, ``"en"``) —
    defaults to ``"zh"`` when empty so the prompt instructs the LLM in the
    right language without hardcoding.

    Returns a normalized init form, or ``None`` when generation is unavailable
    (the caller then keeps the deterministic fallback form).
    """
    name = str(project.get('name') or '新项目').strip()
    description = str(project.get('description') or '').strip()
    template = str(project.get('template') or '').strip()
    template_label = planning_contracts._WORKBENCH_TEMPLATE_LABELS.get(template, template)
    base_form = project_runtime._workbench_default_init_form(project)
    _LANG_NAMES = {'zh': '简体中文', 'en': 'English', 'ja': '日本語'}
    language = _LANG_NAMES.get(lang, _LANG_NAMES.get('zh'))
    details = [f'项目名称：{name}']
    if description:
        details.append(f'项目描述：{description}')
    if template_label:
        details.append(f'项目类型：{template_label}')
    details_block = '\n'.join(details)
    workspace_path = str(project.get('workspacePath') or '').strip()
    workspace_root = Path(workspace_path).expanduser().resolve() if workspace_path else None
    workspace_relationship_guidance = _workbench_init_workspace_relationship_guidance(project)
    init_form_schema = f'最后只返回一个 JSON 对象，不要包含任何额外说明或 Markdown 代码块标记。JSON 结构如下：\n{{\n  "greeting": "一句友好的开场白，说明你将协助完成项目初始化",\n  "sections": [\n    {{\n      "id": "英文小写下划线短标识",\n      "title": "分组标题（{language}，简洁）",\n      "questions": [\n        {{"id": "英文标识", "type": "text|textarea|single|multi", "label": "问题（{language}）", "placeholder": "示例答案（text/textarea 适用）", "options": ["选项1", "选项2"]}}\n      ]\n    }}\n  ]\n}}\n\n'
    if _is_workspace_empty(workspace_root):
        logger.info('Workspace %s is empty — generating metadata-based init form for project %s', workspace_path or '(none)', project.get('id'))
        if description:
            prompt = f'你是一个项目初始化助理。用户刚刚创建了一个全新项目，工作区目前还没有代码或资料文件。你不能探索文件；请只根据用户提供的项目名称、项目类型和项目描述，设计一组贴合该项目目标的引导式问题，帮助用户把需求、范围、约束和第一批任务澄清清楚。\n\n项目信息：\n{details_block}\n\n' + init_form_schema + f'要求：\n- 必须围绕项目描述中的具体目标、场景、对象或产出提问，不要只套用通用模板；\n- 不要重复询问描述中已经明确的项目目标，而要追问边界、优先级、用户/受众、关键约束、验收标准或第一阶段计划；\n- 根据描述自主决定 3-5 个分组，每个分组 2-4 个问题；\n- 多数问题用 text 或 textarea；涉及阶段/选择类的用 single 或 multi 并给出 options；\n- 全部使用{language}，语气友好专业。最后只返回 JSON。'
            parsed = await _workbench_run_json_generation(prompt, max_tokens=15000, timeout=90)
            generated_form = _workbench_coerce_init_form(parsed, base_form) if parsed else None
            if generated_form:
                return generated_form
        empty_form = project_runtime._workbench_default_init_form(project)
        empty_form['generated'] = True
        if language == 'English':
            empty_form['greeting'] = "Hi! I'm your project initialization assistant. It looks like this is a brand-new project with no code in the workspace yet. Let's start with a few key questions to help you plan the direction and scope."
        else:
            empty_form['greeting'] = '你好！我是你的项目初始化助理。看起来这是一个全新的项目，工作区还没有代码。我们先从几个关键问题开始，帮你规划好方向和范围。'
        return empty_form
    prompt = f"你是一个项目初始化助理。用户刚刚创建了一个新项目，工作区已有文件。你需要探索工作区，了解里面可能存在的内容、结构和现状，然后结合用户的项目描述、项目类型和已有文件线索，设计一组贴合实际的引导式问题，帮助用户完成项目初始化。\n\n项目信息：\n{details_block}\n\n{workspace_relationship_guidance}\n\n你可以使用 list_directory、read_file 和 glob 工具深度探索工作区。\n\n请多花几轮仔细探索，推荐的探索步骤：\n1. list_directory('.') — 先了解顶层结构\n2. glob('**/*') 或按文件类型了解内容分布\n3. 读 README、配置文件或关键入口文件了解项目概况\n4. 如果文件较多，深入看几个关键目录的内容\n\n充分了解后再生成 JSON，不要过早下结论。\n\n" + init_form_schema + f'要求：\n- greeting 必须保持中性谨慎：可以说“我看到工作区里有一些已有文件/资料”，但不能说“这是一个围绕某某的项目”或“与你描述的空白项目差异较大”，除非用户描述中明确这么说；\n- 不能把已有文件夹内容当作已确认项目事实；文件探索结论只能作为待确认线索来设计问题；\n- 根据工作区的实际情况，自主决定需要几个分组以及覆盖哪些方向；\n- 用户提供的项目描述是最高优先级需求信号；问题必须同时回应项目描述和文件现状，不要只围绕代码结构提问；\n- 如果项目描述与工作区内容存在缺口或不一致，要设计问题澄清差异和下一步取舍；\n- 如果用户没有明确说明要导入/复用已有文件，第一组问题必须先确认已有文件与新项目的关系，再追问具体规划；\n- 每个分组 2-4 个问题，问题要贴合项目实际情况，避免空泛；\n- 优先围绕项目已有的内容提问（如需要完善的地方、可以补充的方向、后续步骤等）；\n- 多数问题用 text 或 textarea；涉及阶段/选择类的用 single 或 multi 并给出 options；\n- 全部使用{language}，语气友好专业。最后只返回 JSON。'
    parsed = await _workbench_run_explore_agent(workspace_root, prompt, max_tokens=18000, timeout=120)
    if not parsed:
        return None
    return _workbench_coerce_init_form(parsed, base_form)

def _workbench_init_brief(project: dict[str, Any], form: dict[str, Any]) -> str:
    """Render the collected onboarding answers into a Markdown project brief."""
    answers = form.get('answers') if isinstance(form.get('answers'), dict) else {}
    lines = [f"# {project.get('name') or '项目'} · 初始化总结", '']
    for section in form.get('sections', []):
        section_lines: list[str] = []
        for question in section.get('questions', []):
            qid = question.get('id')
            value = answers.get(qid)
            if isinstance(value, list):
                value = '、'.join((str(v) for v in value if str(v).strip()))
            text = str(value or '').strip()
            if text:
                section_lines.append(f"- **{question.get('label')}** {text}")
        if section_lines:
            lines.append(f"## {section.get('title')}")
            lines.extend(section_lines)
            lines.append('')
    return '\n'.join(lines).strip()

def _workbench_answer_text(form: dict[str, Any], key: str) -> str:
    answers = form.get('answers') if isinstance(form.get('answers'), dict) else {}
    value = answers.get(key)
    if isinstance(value, list):
        return '、'.join((str(item).strip() for item in value if str(item).strip()))
    return str(value or '').strip()

def _workbench_fallback_init_task_plan(project: dict[str, Any], form: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a useful deterministic task plan from onboarding answers."""
    goal = _workbench_answer_text(form, 'goal') or str(project.get('description') or '').strip()
    requirements = _workbench_answer_text(form, 'requirements')
    tech = _workbench_answer_text(form, 'tech')
    out_of_scope = _workbench_answer_text(form, 'out_of_scope')
    deadline = _workbench_answer_text(form, 'deadline')
    constraints: list[str] = []
    if out_of_scope:
        constraints.append(f'范围限制：{out_of_scope}')
    if deadline:
        constraints.append(f'时间约束：{deadline}')
    if tech:
        constraints.append(f'偏好工具或平台：{tech}')
    base_goal = goal or f"推进 {project.get('name') or '项目'}。"
    tasks = [{'title': '明确目标与范围', 'goal': f"整理项目目标、背景和边界，形成清晰的范围定义。{(' 重点覆盖：' + requirements if requirements else '')}".strip(), 'priority': 'high', 'constraints': constraints[:], 'acceptanceCriteria': ['目标清晰', '范围已定义', '优先级已确认']}, {'title': '制定执行方案', 'goal': f'基于项目信息设计具体执行方案和计划。项目总目标：{base_goal}', 'priority': 'high', 'constraints': constraints[:], 'acceptanceCriteria': ['执行方案已形成', '步骤可追踪', '依赖已记录']}, {'title': '推进执行与交付', 'goal': f'按计划推进执行，完成项目目标。项目总目标：{base_goal}', 'priority': 'medium', 'constraints': constraints[:], 'acceptanceCriteria': ['项目目标已完成', '结果可验证', '符合预期要求']}]
    return tasks

def _workbench_coerce_init_task_plan(raw: Any, fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source = raw.get('tasks') if isinstance(raw, dict) else raw
    if not isinstance(source, list):
        return fallback
    tasks: list[dict[str, Any]] = []
    for index, item in enumerate(source):
        if not isinstance(item, dict):
            continue
        title = str(item.get('title') or '').strip()
        goal = str(item.get('goal') or item.get('description') or '').strip()
        if not title and goal:
            title = goal[:40]
        if not title:
            continue
        priority = str(item.get('priority') or 'medium').strip().lower()
        if priority not in ('high', 'medium', 'low'):
            priority = 'medium'
        constraints = [str(value).strip() for value in item.get('constraints', []) if str(value).strip()] if isinstance(item.get('constraints'), list) else []
        acceptance = item.get('acceptanceCriteria')
        if not isinstance(acceptance, list):
            acceptance = item.get('acceptance')
        acceptance_items = [str(value).strip() for value in acceptance if str(value).strip()] if isinstance(acceptance, list) else []
        tasks.append({'id': str(item.get('id') or '').strip() or project_runtime._short_id('init_task'), 'title': title[:80], 'goal': goal[:1200] or title, 'priority': priority, 'constraints': constraints[:8], 'acceptanceCriteria': acceptance_items[:8], 'order': index + 1})
    return tasks[:8] or fallback

async def _workbench_generate_init_task_plan(project: dict[str, Any], form: dict[str, Any], feedback: str='', current_plan: list[dict[str, Any]] | None=None, max_attempts: int=5) -> tuple[list[dict[str, Any]] | None, bool, dict[str, Any] | None]:
    """Ask the initialization agent to split the project into major task sessions.

    Returns ``(plan, from_llm, error)``. No synthetic plan is returned when all
    attempts fail. ``error`` contains a user-displayable summary of every
    attempt so the UI can explain the failure and offer a clean restart.

    When ``current_plan`` is given (a revision), it is shown to the agent so the
    output adjusts the existing plan rather than regenerating from scratch.
    """
    brief = _workbench_init_brief(project, form)
    feedback = str(feedback or '').strip()
    workspace_path = str(project.get('workspacePath') or '').strip()
    workspace_root = Path(workspace_path).expanduser().resolve() if workspace_path else None
    current_plan_block = ''
    if isinstance(current_plan, list) and current_plan:
        try:
            slim = [{'title': str(item.get('title') or ''), 'goal': str(item.get('goal') or ''), 'priority': str(item.get('priority') or 'medium'), 'constraints': item.get('constraints') or [], 'acceptanceCriteria': item.get('acceptanceCriteria') or []} for item in current_plan if isinstance(item, dict)]
            current_plan_block = '当前任务计划（请在此基础上按反馈调整，保留未被反馈提到的部分，不要无故重排或删除）：\n' + json.dumps(slim, ensure_ascii=False) + '\n\n'
        except Exception:
            current_plan_block = ''
    prompt = f"""你是项目初始化 Agent。用户已经完成初始化问答。请把项目拆解成若干个可独立推进的大任务，每个大任务后续会创建为一个 workbench session。\n\n项目名称：{project.get('name') or '项目'}\n项目类型：{planning_contracts._WORKBENCH_TEMPLATE_LABELS.get(str(project.get('template') or ''), str(project.get('template') or ''))}\n初始化总结：\n{brief or '暂无'}\n{('用户对计划的修改反馈：' + feedback if feedback else '')}\n\n{current_plan_block}工作区已有文件，你可以使用 list_directory、read_file、glob 工具先探索项目，让大任务贴合项目实际（尽量引用真实的文件/目录/模块），不要套用空泛模板。\n\n充分了解后再返回 JSON，只返回一个 JSON 对象，不要 Markdown。结构：\n{{\n  "tasks": [\n    {{\n      "title": "大任务标题，中文，动宾短语",\n      "goal": "这个 session 要完成的目标、边界和上下文",\n      "priority": "high|medium|low",\n      "constraints": ["约束"],\n      "acceptanceCriteria": ["验收标准"]\n    }}\n  ]\n}}\n\n要求：生成 3-6 个大任务；每个任务要能对应一个独立 session；避免过细的步骤；保留初始化回答中的时间、范围、技术约束。"""
    attempts: list[dict[str, Any]] = []
    attempt_limit = max(1, int(max_attempts or 1))
    for attempt in range(1, attempt_limit + 1):
        try:
            parsed = await _workbench_run_explore_agent(workspace_root, prompt, max_tokens=12000, timeout=120, secondary=True, raise_on_failure=True)
            plan = _workbench_coerce_init_task_plan(parsed, [])
            if not plan:
                raise _WorkbenchGenerationError('response_format', '模型返回的 JSON 中没有可用的 tasks。')
            return (plan, True, None)
        except Exception as exc:
            error = _workbench_generation_error(exc)
            attempts.append({'attempt': attempt, 'category': error.category, 'message': error.message})
            logger.warning('Workbench init task-plan attempt %d/%d failed for project %s: %s', attempt, attempt_limit, project.get('id'), error.message)
            if attempt < attempt_limit:
                await asyncio.sleep(min(2 ** (attempt - 1), 4))
    last = attempts[-1] if attempts else {'category': 'unknown', 'message': '未知错误'}
    return (None, False, {'code': 'init_plan_generation_failed', 'attemptCount': attempt_limit, 'category': last['category'], 'summary': last['message'], 'attempts': attempts})

def _workbench_create_sessions_from_init_plan(project: dict[str, Any], plan: list[dict[str, Any]], now: str | None=None) -> list[dict[str, Any]]:
    """Initialization-agent tool: create task sessions from confirmed major tasks."""
    now = now or project_runtime._utc_now_iso()
    created: list[dict[str, Any]] = []
    sessions = project.setdefault('sessions', [])
    for item in plan:
        if not isinstance(item, dict):
            continue
        title = str(item.get('title') or '').strip()
        if not title:
            continue
        session = project_runtime._workbench_new_session(str(project.get('id') or ''), title, str(item.get('goal') or title).strip(), now, kind='task', status='idle')
        priority = str(item.get('priority') or 'medium').strip().lower()
        if priority in ('high', 'medium', 'low'):
            session['priority'] = priority
        if isinstance(item.get('constraints'), list):
            session['constraints'] = [str(value).strip() for value in item['constraints'] if str(value).strip()][:8]
        if isinstance(item.get('acceptanceCriteria'), list):
            session['acceptanceCriteria'] = [{'id': project_runtime._short_id('accept'), 'text': str(value).strip(), 'status': 'pending'} for value in item['acceptanceCriteria'] if str(value).strip()][:8]
        session['events'] = [{'id': project_runtime._short_id('event'), 'type': 'CreatedFromInitPlan', 'createdAt': now, 'body': '由初始化计划确认后创建。'}]
        created.append(session)
    for session in reversed(created):
        sessions.insert(0, session)
    return created

__all__ = ['_WORKBENCH_EMPTY_WORKSPACE_SKIP_DIRS', '_WORKBENCH_EXPLORE_TOOLS', '_WorkbenchAgentRunError', '_WorkbenchGenerationError', '_is_workspace_empty', '_workbench_answer_text', '_workbench_classify_plan_routing', '_workbench_coerce_init_form', '_workbench_coerce_init_task_plan', '_workbench_create_sessions_from_init_plan', '_workbench_exec_explore_tool', '_workbench_explore_parse_failure', '_workbench_fallback_init_task_plan', '_workbench_generate_init_form', '_workbench_generate_init_task_plan', '_workbench_generation_error', '_workbench_hash_json', '_workbench_init_brief', '_workbench_init_workspace_relationship_guidance', '_workbench_maybe_compact_planning_thread', '_workbench_parse_json_object', '_workbench_plan_tool_bundle', '_workbench_planning_checkpoint', '_workbench_planning_context_chars', '_workbench_planning_thread', '_workbench_redact_error_text', '_workbench_repair_json_response', '_workbench_run_explore_agent', '_workbench_run_json_generation', '_workbench_stable_json', '_workbench_workspace_revision', '_workbench_workspace_state']
