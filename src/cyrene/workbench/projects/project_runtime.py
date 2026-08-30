"""Workbench project domain helpers."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from cyrene.config import WORKSPACE_DIR
from cyrene.localization import localized

def _safe_workbench_data_key(value: Any) -> str:
    raw = str(value or '').strip()
    cleaned = re.sub('[^A-Za-z0-9._-]+', '_', raw).strip('._')
    return cleaned or 'project'

def _workbench_default_project_name() -> str:
    if WORKSPACE_DIR.name == 'workspace' and WORKSPACE_DIR.parent.name:
        return WORKSPACE_DIR.parent.name
    return WORKSPACE_DIR.name or 'Cyrene'

def _workbench_project_data_key(project: dict[str, Any] | None) -> str:
    if not project:
        return ''
    return _safe_workbench_data_key(project.get('dataKey') or project.get('id'))

def _workbench_project_resource_key(project: dict[str, Any] | None) -> str:
    """Return the stable project identity used by Plugin-owned resources."""
    if not project:
        return 'default'
    return _safe_workbench_data_key(project.get('id'))

def _primary_candidate() -> dict[str, Any]:
    from cyrene.core.plugin import application_plugin_service

    service = application_plugin_service("model_configuration")
    candidates = service.candidates_for_route("primary") if service is not None else []
    return dict(candidates[0]) if candidates else {}

def _live_llm_config() -> tuple[str, str]:
    candidate = _primary_candidate()
    return (
        str(candidate.get('model') or ''),
        str(candidate.get('base_url') or ''),
    )

def _get_model() -> str:
    return str(_primary_candidate().get('model') or '')

def _get_base_url() -> str:
    return str(_primary_candidate().get('base_url') or '')

def _parse_ctx_limit(ctx_str: str) -> int:
    """Parse human-readable context limit like '128K', '1M', '200K' to int."""
    ctx_str = (ctx_str or '').strip().upper()
    if not ctx_str:
        return 0
    try:
        if ctx_str.endswith('M'):
            return int(float(ctx_str[:-1]) * 1000000)
        if ctx_str.endswith('K'):
            return int(float(ctx_str[:-1]) * 1000)
        return int(ctx_str)
    except (ValueError, TypeError):
        return 0

def _ctx_limit_for_model(model_name: str) -> int:
    """Resolve a model window from canonical profiles, then known families."""

    from cyrene.core.plugin import application_plugin_service

    target = str(model_name or '').strip()
    ctx_limit = 0
    service = application_plugin_service("model_configuration")
    configuration = service.get_model_configuration() if service is not None else {}
    for profile in configuration.get('profiles') or []:
        if target not in {
            str(profile.get('id') or '').strip(),
            str(profile.get('model') or '').strip(),
            str(profile.get('name') or '').strip(),
        }:
            continue
        ctx_limit = int(profile.get('context_limit') or 0) or _parse_ctx_limit(
            str(profile.get('ctx') or '')
        )
        break
    if not ctx_limit:
        model_lower = target.lower()
        if any((x in model_lower for x in ('claude-opus-4', 'opus-4'))):
            ctx_limit = 200000
        elif any((x in model_lower for x in ('claude-sonnet-4', 'sonnet-4'))):
            ctx_limit = 200000
        elif any((x in model_lower for x in ('claude-haiku-4', 'haiku-4'))):
            ctx_limit = 200000
        elif 'gpt-4' in model_lower or 'gpt-4o' in model_lower:
            ctx_limit = 128000
        elif 'gpt-3.5' in model_lower:
            ctx_limit = 16000
        elif 'deepseek' in model_lower:
            ctx_limit = 128000
        elif 'qwen' in model_lower:
            ctx_limit = 128000
        elif 'gemini' in model_lower:
            ctx_limit = 1000000
    return ctx_limit

def _get_current_model_ctx_limit() -> int:
    """Look up the canonical primary model's context window limit."""
    return _ctx_limit_for_model(_get_model())

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _short_id(prefix: str) -> str:
    return f'{prefix}_{uuid.uuid4().hex[:10]}'

def _workbench_default_project() -> dict[str, Any]:
    now = _utc_now_iso()
    project_id = _short_id('project')
    workspace_name = _workbench_default_project_name()
    workspace_summary = localized(
        f'Workspace at {WORKSPACE_DIR}',
        f'工作区位于 {WORKSPACE_DIR}',
    )
    return {'projects': [{'id': project_id, 'name': workspace_name, 'dataKey': _safe_workbench_data_key(project_id), 'workspacePath': str(WORKSPACE_DIR), 'workspacePathSource': 'user', 'status': 'active', 'model': _get_model(), 'accountTier': 'Pro', 'context': {'summary': workspace_summary, 'stack': [], 'decisions': [], 'knowledgeDocumentIds': []}, 'createdAt': now, 'updatedAt': now, 'sharedArtifacts': []}], 'activeProjectId': project_id}

workbench_project_data_key = _workbench_project_data_key

__all__ = ["workbench_project_data_key"]
