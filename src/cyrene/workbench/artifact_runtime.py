"""Workspace-diff and artifact domain operations."""

from __future__ import annotations

import asyncio
import difflib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from cyrene.config import WORKSPACE_DIR, cyrene_dir
from cyrene.runtime.attachments import EXPORTS_DIR as _EXPORTS_DIR
from cyrene.workbench import project_runtime
from cyrene.workbench.workspace_changes import is_cyrene_managed_workspace_path

def _workbench_workspace_root(project: dict[str, Any] | None) -> Path | None:
    project_id = str((project or {}).get('id') or '').strip()
    workspace_source = str((project or {}).get('workspacePathSource') or '').strip().lower()
    if workspace_source == 'generated' and project_id:
        return (cyrene_dir(WORKSPACE_DIR) / 'projects' / project_id).resolve()
    workspace_path = str((project or {}).get('workspacePath') or '').strip()
    if not workspace_path:
        return None
    try:
        candidate = Path(workspace_path).expanduser().resolve()
    except OSError:
        return None
    return candidate

def _workbench_display_path(path_value: Any, workspace_root: Path | None=None) -> str:
    raw = str(path_value or '').strip()
    if not raw:
        return ''
    try:
        path = Path(raw).expanduser()
        if workspace_root:
            root = workspace_root.resolve()
            resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
            try:
                return resolved.relative_to(root).as_posix()
            except ValueError:
                return ''
        if path.is_absolute():
            return path.resolve().as_posix()
        return path.as_posix().lstrip('./')
    except Exception:
        return ''

def _workbench_file_change(path_value: Any, status: str, workspace_root: Path | None=None, source: str='') -> dict[str, Any] | None:
    path = _workbench_display_path(path_value, workspace_root)
    if not path or is_cyrene_managed_workspace_path(path, workspace_root):
        return None
    return {'id': project_runtime._short_id('file'), 'path': path, 'status': status, 'changeType': status, 'source': source}

def _workbench_file_changes_from_tool_event(event: dict[str, Any], workspace_root: Path | None=None) -> list[dict[str, Any]]:
    tool = str(event.get('tool') or '').strip()
    args = event.get('args') if isinstance(event.get('args'), dict) else {}
    result = str(event.get('result') or '')
    changes: list[dict[str, Any]] = []
    if tool == 'Write' and isinstance(args, dict):
        change = _workbench_file_change(args.get('path'), 'created/updated', workspace_root, tool)
        if change:
            changes.append(change)
    elif tool == 'Edit' and isinstance(args, dict):
        change = _workbench_file_change(args.get('path'), 'modified', workspace_root, tool)
        if change:
            changes.append(change)
    elif tool == 'send_file' and isinstance(args, dict):
        change = _workbench_file_change(args.get('path'), 'produced', workspace_root, tool)
        if change:
            try:
                parsed = json.loads(result or '{}')
                attachment = (parsed or {}).get('attachment')
                if isinstance(attachment, dict) and attachment.get('id'):
                    change['attachment'] = attachment
            except Exception:
                pass
            changes.append(change)
    for match in re.finditer('\\b(Wrote|Edited)\\s+([^\\n]+?)(?:\\. Replacements:.*)?$', result, flags=re.MULTILINE):
        verb = match.group(1)
        path_text = match.group(2).strip()
        status = 'modified' if verb == 'Edited' else 'created/updated'
        change = _workbench_file_change(path_text, status, workspace_root, tool)
        if change:
            changes.append(change)
    return _workbench_merge_file_changes(changes)

def _workbench_merge_file_changes(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    rank = {'produced': 5, 'created': 4, 'modified': 3, 'deleted': 3, 'renamed': 3, 'created/updated': 2}
    for item in changes:
        if not isinstance(item, dict):
            continue
        path = str(item.get('path') or item.get('name') or '').strip()
        if not path:
            continue
        key = path
        if key not in merged:
            merged[key] = dict(item)
            order.append(key)
            continue
        old = merged[key]
        new_status = str(item.get('status') or item.get('changeType') or '')
        old_status = str(old.get('status') or old.get('changeType') or '')
        old_source = str(old.get('source') or '').strip().lower()
        new_source = str(item.get('source') or '').strip().lower()
        inferred_cannot_override_explicit = new_source == 'git' and old_source in {'write', 'edit', 'send_file'}
        if not inferred_cannot_override_explicit and rank.get(new_status, 0) > rank.get(old_status, 0):
            old['status'] = new_status
            old['changeType'] = new_status
            if new_status == 'produced' and item.get('source'):
                old['source'] = item.get('source')
            if new_status == 'produced' and isinstance(item.get('attachment'), dict):
                old['attachment'] = item.get('attachment')
        if item.get('source') and (not old.get('source')):
            old['source'] = item.get('source')
        if item.get('attachment') and (not old.get('attachment')):
            old['attachment'] = item.get('attachment')
        if item.get('diff') and (not old.get('diff')):
            old['diff'] = item.get('diff')
            if item.get('diffSource'):
                old['diffSource'] = item.get('diffSource')
        if item.get('diffUnavailableReason') and (not old.get('diff')) and (not old.get('diffUnavailableReason')):
            old['diffUnavailableReason'] = item.get('diffUnavailableReason')
    return [merged[key] for key in order]
_WORKBENCH_SNAPSHOT_IGNORED_DIRS = {'.git', '.hg', '.svn', '.idea', '.vscode', '.pytest_cache', '.mypy_cache', '.ruff_cache', '.tox', '.venv', '__pycache__', 'node_modules'}
_WORKBENCH_TEXT_SNAPSHOT_MAX_BYTES = 1000000
_WORKBENCH_TEXT_SNAPSHOT_MAX_TOTAL_BYTES = 8000000
_WORKBENCH_TEXT_SNAPSHOT_MAX_FILES = 500

def _workbench_workspace_file_snapshot(workspace_root: Path | None) -> dict[str, tuple[int, int]]:
    """Capture cheap file identity for shell/subagent output detection."""
    if not workspace_root:
        return {}
    try:
        root = workspace_root.resolve()
    except OSError:
        return {}
    if not root.exists() or not root.is_dir():
        return {}
    snapshot: dict[str, tuple[int, int]] = {}
    try:
        for current, dirnames, filenames in os.walk(root):
            dirnames[:] = [name for name in dirnames if name not in _WORKBENCH_SNAPSHOT_IGNORED_DIRS and (not name.startswith('.'))]
            current_path = Path(current)
            if current_path == root:
                dirnames[:] = [name for name in dirnames if not is_cyrene_managed_workspace_path(name, root)]
            for filename in filenames:
                if filename.startswith('.'):
                    continue
                target = current_path / filename
                try:
                    if not target.is_file() or target.is_symlink():
                        continue
                    stat = target.stat()
                    rel = target.relative_to(root).as_posix()
                except (OSError, ValueError):
                    continue
                snapshot[rel] = (int(stat.st_mtime_ns), int(stat.st_size))
    except OSError:
        return snapshot
    return snapshot

def _workbench_workspace_text_snapshot(workspace_root: Path | None) -> dict[str, str]:
    """Capture bounded UTF-8 file content so Workbench can diff without Git."""
    if not workspace_root:
        return {}
    try:
        root = workspace_root.resolve()
    except OSError:
        return {}
    if not root.exists() or not root.is_dir():
        return {}
    snapshot: dict[str, str] = {}
    total_bytes = 0
    try:
        for current, dirnames, filenames in os.walk(root):
            dirnames[:] = [name for name in dirnames if name not in _WORKBENCH_SNAPSHOT_IGNORED_DIRS and (not name.startswith('.'))]
            current_path = Path(current)
            if current_path == root:
                dirnames[:] = [name for name in dirnames if not is_cyrene_managed_workspace_path(name, root)]
            if len(snapshot) >= _WORKBENCH_TEXT_SNAPSHOT_MAX_FILES:
                break
            for filename in filenames:
                if len(snapshot) >= _WORKBENCH_TEXT_SNAPSHOT_MAX_FILES:
                    break
                if filename.startswith('.'):
                    continue
                target = current_path / filename
                try:
                    if not target.is_file() or target.is_symlink():
                        continue
                    stat = target.stat()
                    if stat.st_size > _WORKBENCH_TEXT_SNAPSHOT_MAX_BYTES:
                        continue
                    if total_bytes + stat.st_size > _WORKBENCH_TEXT_SNAPSHOT_MAX_TOTAL_BYTES:
                        return snapshot
                    rel = target.relative_to(root).as_posix()
                    data = target.read_bytes()
                except OSError:
                    continue
                if b'\x00' in data:
                    continue
                try:
                    snapshot[rel] = data.decode('utf-8')
                    total_bytes += len(data)
                except UnicodeDecodeError:
                    continue
    except OSError:
        return snapshot
    return snapshot

def _workbench_workspace_snapshot_delta(before: dict[str, tuple[int, int]], after: dict[str, tuple[int, int]], evidence: str='', before_text: dict[str, str] | None=None, after_text: dict[str, str] | None=None) -> list[dict[str, Any]]:
    """Return workspace changes and mark files explicitly named as outputs."""
    evidence_text = str(evidence or '')
    before_text = before_text or {}
    after_text = after_text or {}
    changes: list[dict[str, Any]] = []
    for path, signature in after.items():
        previous = before.get(path)
        if previous == signature:
            continue
        status = 'created' if previous is None else 'modified'
        name = path.rsplit('/', 1)[-1]
        explicitly_named = path in evidence_text or name in evidence_text
        change = _workbench_file_change(path, 'produced' if explicitly_named else status, source='workspace_output' if explicitly_named else 'workspace')
        if change:
            if path in after_text and (previous is None or path in before_text):
                diff = _workbench_unified_diff(before_text.get(path, ''), after_text[path], f'a/{path}' if previous is not None else '/dev/null', f'b/{path}')
                if diff.strip():
                    change['diff'] = diff
                    change['diffSource'] = 'workspace_snapshot'
                else:
                    change['diffUnavailableReason'] = 'no_text_difference'
            else:
                change['diffUnavailableReason'] = 'text_snapshot_unavailable'
            changes.append(change)
    for path, previous in before.items():
        if path in after:
            continue
        change = _workbench_file_change(path, 'deleted', source='workspace')
        if change:
            if path in before_text:
                diff = _workbench_unified_diff(before_text[path], '', f'a/{path}', '/dev/null')
                if diff.strip():
                    change['diff'] = diff
                    change['diffSource'] = 'workspace_snapshot'
                else:
                    change['diffUnavailableReason'] = 'no_text_difference'
            else:
                change['diffUnavailableReason'] = 'text_snapshot_unavailable'
            changes.append(change)
    return changes

def _workbench_collect_run_file_changes(tool_events: list[dict[str, Any]], git_before: dict[str, str], git_after: dict[str, str], workspace_before: dict[str, tuple[int, int]], workspace_after: dict[str, tuple[int, int]], workspace_root: Path | None, evidence: str='', workspace_text_before: dict[str, str] | None=None, workspace_text_after: dict[str, str] | None=None) -> list[dict[str, Any]]:
    return _workbench_merge_file_changes([*[change for event in tool_events for change in event.get('fileChanges') or []], *_workbench_git_status_delta(git_before, git_after, workspace_root), *_workbench_workspace_snapshot_delta(workspace_before, workspace_after, evidence, before_text=workspace_text_before, after_text=workspace_text_after)])

def _workbench_git_context(workspace_root: Path | None) -> tuple[Path, str] | None:
    if not workspace_root:
        return None
    try:
        proc = subprocess.run(['git', '-C', str(workspace_root), 'rev-parse', '--show-toplevel', '--show-prefix'], capture_output=True, text=True, timeout=5, check=False)
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    lines = proc.stdout.splitlines()
    if not lines:
        return None
    try:
        repo_root = Path(lines[0]).expanduser().resolve()
        prefix = (lines[1] if len(lines) > 1 else '').replace('\\', '/')
        if prefix and (not prefix.endswith('/')):
            prefix += '/'
        return (repo_root, prefix)
    except OSError:
        return None

def _workbench_git_status_snapshot(workspace_root: Path | None) -> dict[str, str]:
    context = _workbench_git_context(workspace_root)
    if not workspace_root or context is None:
        return {}
    _, prefix = context
    try:
        proc = subprocess.run(['git', '-C', str(workspace_root), 'status', '--porcelain=v1', '-z', '--no-renames', '--untracked-files=all', '--', '.'], capture_output=True, text=True, timeout=5, check=False)
    except Exception:
        return {}
    if proc.returncode != 0:
        return {}
    snapshot: dict[str, str] = {}
    for record in proc.stdout.split('\x00'):
        if len(record) < 4:
            continue
        code = record[:2]
        repo_path = record[3:].replace('\\', '/')
        if prefix:
            if not repo_path.startswith(prefix):
                continue
            path = repo_path[len(prefix):]
        else:
            path = repo_path
        normalized = _workbench_display_path(path, workspace_root)
        if normalized and (not is_cyrene_managed_workspace_path(normalized, workspace_root)):
            snapshot[normalized] = code
    return snapshot

def _workbench_git_status_change_type(code: str) -> str:
    if 'D' in code:
        return 'deleted'
    if 'R' in code:
        return 'renamed'
    if 'A' in code or code == '??':
        return 'created'
    return 'modified'

def _workbench_git_status_delta(before: dict[str, str], after: dict[str, str], workspace_root: Path | None=None) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for path, code in after.items():
        if before.get(path) == code:
            continue
        change = _workbench_file_change(path, _workbench_git_status_change_type(code), workspace_root, 'git')
        if change:
            changes.append(change)
    return changes

def _workbench_resolve_workspace_file(workspace_root: Path | None, path_value: Any) -> Path:
    if not workspace_root:
        raise ValueError('workspace directory is not configured')
    root = workspace_root.resolve()
    raw = str(path_value or '').strip()
    if not raw:
        raise ValueError('path is required')
    path = Path(raw).expanduser()
    target = path.resolve() if path.is_absolute() else (root / path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise ValueError('path is outside the workspace directory')
    return target

def _workbench_artifact_download_target(project: dict[str, Any], session: dict[str, Any], artifact_id: str) -> tuple[dict[str, Any], Path]:
    artifact = next((item for item in session.get('artifacts') or [] if isinstance(item, dict) and str(item.get('id') or '') == artifact_id), None)
    if artifact is None:
        raise LookupError('artifact not found')
    if artifact.get('type') != 'file_change':
        raise ValueError('artifact is not a downloadable file')
    attachment_id = str((artifact.get('attachment') or {}).get('id') or '').strip()
    if attachment_id:
        try:
            exported = (_EXPORTS_DIR / attachment_id).resolve()
            if exported.is_relative_to(_EXPORTS_DIR.resolve()) and exported.is_file():
                return (artifact, exported)
        except (OSError, ValueError):
            pass
    target = _workbench_resolve_workspace_file(_workbench_workspace_root(project), artifact.get('path') or artifact.get('name'))
    if not target.exists() or not target.is_file():
        raise FileNotFoundError('artifact file not found')
    return (artifact, target)

def _workbench_unified_diff(left_text: str, right_text: str, left_label: str, right_label: str) -> str:
    return ''.join(difflib.unified_diff(left_text.splitlines(keepends=True), right_text.splitlines(keepends=True), fromfile=left_label, tofile=right_label))
_WORKBENCH_DIFF_SNAPSHOT_MAX_BYTES = 1000000

def _workbench_current_file_snapshot_diff(target: Path, rel: str) -> str:
    """Return a displayable text snapshot when no historical/git diff exists."""
    try:
        if not target.is_file() or target.stat().st_size > _WORKBENCH_DIFF_SNAPSHOT_MAX_BYTES:
            return ''
        right_text = target.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return ''
    if not right_text:
        return ''
    return _workbench_unified_diff('', right_text, '/dev/null', f'b/{rel}')

def _workbench_recorded_diff_for_path(session: dict[str, Any], path_value: Any, workspace_root: Path | None=None) -> dict[str, Any] | None:
    rel = _workbench_display_path(path_value, workspace_root) or str(path_value or '').strip()
    if not rel or is_cyrene_managed_workspace_path(rel, workspace_root):
        return None
    candidates: list[dict[str, Any]] = []
    for run in reversed(session.get('runs') or []):
        if isinstance(run, dict):
            candidates.extend((item for item in reversed(run.get('fileChanges') or []) if isinstance(item, dict)))
    for step in reversed(session.get('plan') or []):
        if isinstance(step, dict):
            candidates.extend((item for item in reversed(step.get('relatedFiles') or []) if isinstance(item, dict)))
    candidates.extend((item for item in reversed(session.get('artifacts') or []) if isinstance(item, dict) and item.get('type') == 'file_change'))
    for item in candidates:
        item_path = _workbench_display_path(item.get('path') or item.get('name'), workspace_root)
        if item_path != rel:
            continue
        diff = str(item.get('diff') or '')
        if diff.strip():
            return {'path': rel, 'diff': diff, 'has_changes': True, 'source': str(item.get('diffSource') or 'recorded')}
        reason = str(item.get('diffUnavailableReason') or '').strip()
        if reason:
            return {'path': rel, 'diff': '', 'has_changes': False, 'source': reason, 'reason': reason}
    return None

async def _workbench_git_diff_for_path(workspace_root: Path | None, path_value: Any) -> dict[str, Any]:
    target = _workbench_resolve_workspace_file(workspace_root, path_value)
    root = workspace_root.resolve() if workspace_root else None
    rel = target.relative_to(root).as_posix() if root else str(path_value)
    if is_cyrene_managed_workspace_path(rel, workspace_root):
        return {'path': rel, 'diff': '', 'has_changes': False, 'source': 'cyrene_managed'}
    context = _workbench_git_context(root)
    if context is None:
        diff = _workbench_current_file_snapshot_diff(target, rel)
        return {'path': rel, 'diff': diff, 'has_changes': bool(diff.strip()), 'source': 'snapshot' if diff else 'none'}
    repo_root, prefix = context
    git_rel = f'{prefix}{rel}' if prefix else rel
    try:
        proc = await asyncio.create_subprocess_exec('git', 'diff', '--', git_rel, cwd=str(repo_root), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
    except asyncio.TimeoutError:
        raise TimeoutError('git diff timed out')
    except FileNotFoundError:
        raise RuntimeError('git not available')
    if proc.returncode not in (0, 1):
        raise RuntimeError(stderr.decode('utf-8', errors='replace') or 'git diff failed')
    diff = stdout.decode('utf-8', errors='replace')
    diff_source = 'git' if diff.strip() else 'none'
    if not diff.strip() and target.is_file():
        staged = await asyncio.create_subprocess_exec('git', 'diff', '--cached', '--', git_rel, cwd=str(repo_root), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        staged_stdout, _ = await staged.communicate()
        if staged.returncode in (0, 1):
            diff = staged_stdout.decode('utf-8', errors='replace')
            if diff.strip():
                diff_source = 'git'
    if not diff.strip() and target.is_file():
        tracked = await asyncio.create_subprocess_exec('git', 'ls-files', '--error-unmatch', '--', git_rel, cwd=str(repo_root), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await tracked.communicate()
        if tracked.returncode != 0:
            diff = _workbench_current_file_snapshot_diff(target, rel)
            if diff.strip():
                diff_source = 'snapshot'
    if not diff.strip() and target.is_file():
        diff = _workbench_current_file_snapshot_diff(target, rel)
        if diff.strip():
            diff_source = 'snapshot'
    return {'path': rel, 'diff': diff, 'has_changes': bool(diff.strip()), 'source': diff_source}

def _workbench_apply_step_file_changes(session: dict[str, Any], step_id: str, file_changes: list[dict[str, Any]]) -> None:
    if not step_id or not file_changes:
        return
    plan = session.get('plan') if isinstance(session.get('plan'), list) else []
    for step in plan:
        if not isinstance(step, dict) or str(step.get('id') or '') != step_id:
            continue
        existing = step.get('relatedFiles') if isinstance(step.get('relatedFiles'), list) else []
        step['relatedFiles'] = _workbench_merge_file_changes([*existing, *file_changes])
        break

def _workbench_is_artifact_change(change: dict[str, Any]) -> bool:
    """Return whether a file event explicitly identifies a deliverable.

    Git/workspace diffs are useful for related-file tracking but do not prove
    task ownership. Only an explicit file creation/write or send_file action is
    strong enough to auto-promote a file into the artifact panel.
    """
    source = str(change.get('source') or '').strip().lower()
    change_type = str(change.get('status') or change.get('changeType') or '').strip().lower()
    if source in {'send_file', 'workspace_output'}:
        return change_type == 'produced'
    return False

def _workbench_prune_non_file_artifacts(session: dict[str, Any]) -> bool:
    """Keep the artifact collection limited to unique downloadable files."""
    artifacts = session.get('artifacts')
    if not isinstance(artifacts, list):
        session['artifacts'] = []
        return artifacts is not None
    kept: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or artifact.get('type') != 'file_change':
            continue
        path = str(artifact.get('path') or artifact.get('name') or '').strip()
        if not path or path in seen_paths:
            continue
        seen_paths.add(path)
        kept.append(artifact)
    if kept == artifacts:
        return False
    session['artifacts'] = kept
    return True

def _workbench_promote_file_artifacts(session: dict[str, Any], file_changes: list[dict[str, Any]], now: str) -> int:
    """Surface explicitly produced files as task artifacts (dedup by path).

    Files declared via ``send_file`` (changeType ``produced``) get a durable
    webui_exports copy registered (the same one chat attachments download
    from); the artifact records that copy so downloads survive later edits to
    the Agent-verified source path. Other file changes (created/modified) keep
    their workspace-relative path and resolve on download.
    """
    _workbench_prune_non_file_artifacts(session)
    if not file_changes:
        return 0
    artifacts = session.get('artifacts') if isinstance(session.get('artifacts'), list) else []
    known_paths = {str(a.get('path') or a.get('name') or '').strip() for a in artifacts if isinstance(a, dict)}
    status_map = {'created': 'created', 'created/updated': 'created', 'modified': 'modified', 'renamed': 'modified'}
    added = 0
    for change in file_changes:
        if not isinstance(change, dict):
            continue
        path = str(change.get('path') or change.get('name') or '').strip()
        if not path or path in known_paths:
            continue
        if not _workbench_is_artifact_change(change):
            continue
        change_type = str(change.get('status') or change.get('changeType') or '')
        status = status_map.get(change_type)
        if change_type == 'produced':
            status = 'ready'
        if not status:
            continue
        attachment = change.get('attachment') if isinstance(change.get('attachment'), dict) else None
        known_paths.add(path)
        artifact = {'id': project_runtime._short_id('artifact'), 'type': 'file_change', 'name': path.rsplit('/', 1)[-1] or path, 'path': path, 'status': status, 'createdAt': now, 'summary': path, 'source': change.get('source')}
        if attachment:
            artifact['attachment'] = attachment
        if change.get('diff'):
            artifact['diff'] = change.get('diff')
            if change.get('diffSource'):
                artifact['diffSource'] = change.get('diffSource')
        artifacts.append(artifact)
        added += 1
    session['artifacts'] = artifacts
    return added

_WORKBENCH_FINAL_KNOWLEDGE_STATUSES = {'review', 'completed', 'done'}

def _workbench_final_artifact_file_changes(session: dict[str, Any]) -> list[dict[str, Any]]:
    """Return final deliverables that should be promoted into knowledge.

    ``fileChanges`` is a process log and may contain intermediate build files.
    ``session.artifacts`` is the curated deliverable surface shown to the user,
    so final knowledge ingestion must read from it instead.
    """
    artifacts = session.get('artifacts') if isinstance(session.get('artifacts'), list) else []
    changes: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or artifact.get('type') != 'file_change':
            continue
        path = str(artifact.get('path') or artifact.get('name') or '').strip()
        if not path or path in seen_paths:
            continue
        status = str(artifact.get('status') or '').strip().lower()
        if status in {'deleted', 'removed', 'missing'}:
            continue
        seen_paths.add(path)
        change: dict[str, Any] = {'path': path, 'status': 'produced', 'source': artifact.get('source') or 'final_artifact'}
        if isinstance(artifact.get('attachment'), dict):
            change['attachment'] = artifact.get('attachment')
        changes.append(change)
    return changes

__all__ = ['_WORKBENCH_DIFF_SNAPSHOT_MAX_BYTES', '_WORKBENCH_FINAL_KNOWLEDGE_STATUSES', '_WORKBENCH_SNAPSHOT_IGNORED_DIRS', '_WORKBENCH_TEXT_SNAPSHOT_MAX_BYTES', '_WORKBENCH_TEXT_SNAPSHOT_MAX_FILES', '_WORKBENCH_TEXT_SNAPSHOT_MAX_TOTAL_BYTES', '_workbench_apply_step_file_changes', '_workbench_artifact_download_target', '_workbench_collect_run_file_changes', '_workbench_current_file_snapshot_diff', '_workbench_display_path', '_workbench_file_change', '_workbench_file_changes_from_tool_event', '_workbench_final_artifact_file_changes', '_workbench_git_context', '_workbench_git_diff_for_path', '_workbench_git_status_change_type', '_workbench_git_status_delta', '_workbench_git_status_snapshot', '_workbench_is_artifact_change', '_workbench_merge_file_changes', '_workbench_promote_file_artifacts', '_workbench_prune_non_file_artifacts', '_workbench_recorded_diff_for_path', '_workbench_resolve_workspace_file', '_workbench_unified_diff', '_workbench_workspace_file_snapshot', '_workbench_workspace_root', '_workbench_workspace_snapshot_delta', '_workbench_workspace_text_snapshot']
