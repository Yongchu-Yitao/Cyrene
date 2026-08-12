"""General, global Agent hooks with a small JSON subprocess protocol.

Hooks receive one JSON object on stdin.  They may write one JSON object to
stdout and diagnostic text to stderr.  Hook child processes are launched
directly (never through a shell or Cyrene tool), so their own work cannot
recursively trigger this hook dispatcher.
"""

from __future__ import annotations

import asyncio
import fnmatch
import importlib
import json
import logging
import os
import re
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cyrene.agent.context import current_run_context
from cyrene.config import DATA_DIR
from cyrene.runtime.secret_redaction import redact_text
from cyrene.runtime.settings_store import get as get_setting, set_ as set_setting

logger = logging.getLogger(__name__)

HOOK_EVENTS = frozenset({"PreToolUse", "PostToolUse", "SessionStart", "SessionEnd", "Stop"})
_SETTING_KEY = "agent_hooks"
_PROPOSAL_SETTING_KEY = "agent_hook_proposals"
_AUDIT_FILE = DATA_DIR / "agent_hook_audit.jsonl"
_MAX_STDOUT_BYTES = 256 * 1024
_MAX_STDERR_BYTES = 64 * 1024
_DEFAULT_TIMEOUT_SECONDS = 10.0
_HOOK_ENVIRONMENT_KEYS = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "SHELL",
    "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL", "LC_CTYPE",
    "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "USERPROFILE",
    "APPDATA", "LOCALAPPDATA",
})


class HookBlocked(RuntimeError):
    """Raised when an enforcing PreToolUse hook denies a tool call."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _safe_id(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value or "").strip()).strip("-.")
    return normalized[:80]


def agent_process_environment() -> dict[str, str]:
    """Resolve the current Agent environment without a static service cycle."""
    return importlib.import_module(
        "cyrene.extensions.service"
    ).agent_process_environment()


def hook_process_environment(custom: dict[str, Any] | None = None) -> dict[str, str]:
    """Build the least-privilege environment inherited by Hook processes."""
    source = agent_process_environment()
    env = {
        key: str(value)
        for key, value in source.items()
        if key.upper() in _HOOK_ENVIRONMENT_KEYS or key.upper().startswith("LC_")
    }
    if custom:
        env.update({str(key): str(value) for key, value in custom.items()})
    return env


def _redact_hook_text(value: Any, secrets: dict[str, Any] | None = None) -> str:
    text = str(redact_text(str(value or "")) or "")
    for secret in (secrets or {}).values():
        secret_text = str(secret or "")
        if secret_text:
            text = text.replace(secret_text, "[REDACTED]")
    return text


async def _read_hook_stream(
    stream: asyncio.StreamReader | None,
    *,
    limit: int,
    strict: bool,
    label: str,
) -> bytes:
    if stream is None:
        return b""
    retained = bytearray()
    total = 0
    while True:
        chunk = await stream.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if strict and total > limit:
            raise RuntimeError(f"hook {label} exceeded {limit // 1024} KB")
        if len(retained) < limit:
            retained.extend(chunk[: limit - len(retained)])
    return bytes(retained)


async def _communicate_hook_process(process: asyncio.subprocess.Process, stdin: bytes) -> tuple[bytes, bytes]:
    if process.stdin is not None:
        try:
            process.stdin.write(stdin)
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            process.stdin.close()
    stdout_task = asyncio.create_task(_read_hook_stream(
        process.stdout, limit=_MAX_STDOUT_BYTES, strict=True, label="stdout",
    ))
    stderr_task = asyncio.create_task(_read_hook_stream(
        process.stderr, limit=_MAX_STDERR_BYTES, strict=False, label="stderr",
    ))
    try:
        stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
        await process.wait()
        return stdout, stderr
    except BaseException:
        stdout_task.cancel()
        stderr_task.cancel()
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        raise


def _normalize_hook(raw: dict[str, Any], *, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("hook must be an object")
    previous = existing or {}
    event = str(raw.get("event", previous.get("event", ""))).strip()
    if event not in HOOK_EVENTS:
        raise ValueError("unsupported hook event")
    hook_id = _safe_id(str(raw.get("id") or previous.get("id") or f"hook-{uuid.uuid4().hex[:12]}"))
    if not hook_id:
        raise ValueError("hook id is required")
    name = str(raw.get("name", previous.get("name", hook_id))).strip()[:120]
    if not name:
        raise ValueError("hook name is required")
    previous_runner = previous.get("runner", {}) if isinstance(previous.get("runner", {}), dict) else {}
    runner = raw.get("runner", previous_runner)
    if not isinstance(runner, dict):
        raise ValueError("runner must be an object")
    runner_type = str(runner.get("type") or "command").strip().lower()
    if runner_type not in {"command", "script"}:
        raise ValueError("runner.type must be command or script")
    target_key = "executable" if runner_type == "command" else "path"
    target = str(runner.get(target_key, previous_runner.get(target_key, "")) or "").strip()
    if not target:
        raise ValueError(f"runner.{target_key} is required")
    args = runner.get("args", previous_runner.get("args", []))
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise ValueError("runner.args must be an array of strings")
    # The management API intentionally omits environment values.  When an
    # existing hook is edited through that API, absence means "preserve".
    env = runner.get("env", previous_runner.get("env", {}))
    if not isinstance(env, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in env.items()):
        raise ValueError("runner.env must contain string keys and values")
    timeout = raw.get("timeout_seconds", previous.get("timeout_seconds", _DEFAULT_TIMEOUT_SECONDS))
    try:
        timeout = float(timeout)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout_seconds must be numeric") from exc
    if not 0.1 <= timeout <= 60:
        raise ValueError("timeout_seconds must be between 0.1 and 60")
    failure_policy = str(raw.get("failure_policy", previous.get("failure_policy", "open"))).strip().lower()
    if failure_policy not in {"open", "block"}:
        raise ValueError("failure_policy must be open or block")
    if failure_policy == "block" and event != "PreToolUse":
        raise ValueError("only PreToolUse hooks may block on failure")
    matcher = str(raw.get("matcher", previous.get("matcher", "*")) or "*").strip()[:200]
    if event not in {"PreToolUse", "PostToolUse"}:
        matcher = "*"
    created_at = str(previous.get("created_at") or raw.get("created_at") or _now())
    extension = raw.get("extension", previous.get("extension", {}))
    if not isinstance(extension, dict):
        extension = {}
    return {
        "id": hook_id,
        "name": name,
        "description": str(raw.get("description", previous.get("description", ""))).strip()[:500],
        "event": event,
        "matcher": matcher,
        "enabled": bool(raw.get("enabled", previous.get("enabled", False))),
        "priority": max(-10000, min(10000, int(raw.get("priority", previous.get("priority", 100))))),
        "failure_policy": failure_policy,
        "timeout_seconds": timeout,
        "runner": {
            "type": runner_type,
            target_key: target,
            "args": list(args),
            "env": dict(env),
        },
        "extension": _json_copy(extension),
        "created_at": created_at,
        "updated_at": _now(),
    }


def _audit(record: dict[str, Any]) -> None:
    try:
        _AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _AUDIT_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"timestamp": _now(), **record}, ensure_ascii=False, default=str) + "\n")
    except Exception:
        logger.warning("failed to append Agent hook audit record", exc_info=True)


def hook_audit_records(limit: int = 200) -> list[dict[str, Any]]:
    if not _AUDIT_FILE.is_file():
        return []
    try:
        lines = _AUDIT_FILE.read_text(encoding="utf-8").splitlines()[-max(1, min(int(limit), 1000)):]
    except OSError:
        return []
    records: list[dict[str, Any]] = []
    for line in reversed(lines):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def public_hook_config(hook: dict[str, Any]) -> dict[str, Any]:
    """Return a management-safe Hook representation without environment secrets."""
    value = _json_copy(hook)
    runner = value.get("runner") if isinstance(value.get("runner"), dict) else {}
    env = runner.pop("env", {})
    runner["environment_keys"] = sorted(str(key) for key in env) if isinstance(env, dict) else []
    value["runner"] = runner
    return value


def public_hook_proposal(proposal: dict[str, Any]) -> dict[str, Any]:
    value = _json_copy(proposal)
    if isinstance(value.get("hook"), dict):
        value["hook"] = public_hook_config(value["hook"])
    return value


class HookService:
    """Persistent hook registry and bounded subprocess dispatcher."""

    def list(self) -> list[dict[str, Any]]:
        raw = get_setting(_SETTING_KEY, [])
        if not isinstance(raw, list):
            return []
        hooks = [item for item in raw if isinstance(item, dict)]
        return sorted(_json_copy(hooks), key=lambda item: (int(item.get("priority", 100)), str(item.get("created_at", "")), str(item.get("id", ""))))

    def get(self, hook_id: str) -> dict[str, Any] | None:
        return next((item for item in self.list() if item.get("id") == hook_id), None)

    def save(self, raw: dict[str, Any], *, actor: str = "user") -> dict[str, Any]:
        hooks = self.list()
        requested_id = _safe_id(str(raw.get("id") or ""))
        existing = next((item for item in hooks if item.get("id") == requested_id), None) if requested_id else None
        normalized = _normalize_hook(raw, existing=existing)
        if existing:
            hooks = [normalized if item.get("id") == normalized["id"] else item for item in hooks]
            action = "update"
        else:
            if any(item.get("id") == normalized["id"] for item in hooks):
                raise ValueError("hook id already exists")
            hooks.append(normalized)
            action = "create"
        set_setting(_SETTING_KEY, hooks)
        _audit({"kind": "configuration", "action": action, "actor": actor, "hook_id": normalized["id"], "event": normalized["event"], "enabled": normalized["enabled"], "result": "ok"})
        return _json_copy(normalized)

    def delete(self, hook_id: str, *, actor: str = "user") -> bool:
        hooks = self.list()
        remaining = [item for item in hooks if item.get("id") != hook_id]
        if len(remaining) == len(hooks):
            return False
        set_setting(_SETTING_KEY, remaining)
        _audit({"kind": "configuration", "action": "delete", "actor": actor, "hook_id": hook_id, "result": "ok"})
        return True

    def set_enabled(self, hook_id: str, enabled: bool, *, actor: str = "user") -> dict[str, Any]:
        existing = self.get(hook_id)
        if existing is None:
            raise ValueError("hook not found")
        existing["enabled"] = bool(enabled)
        result = self.save(existing, actor=actor)
        _audit({"kind": "configuration", "action": "enable" if enabled else "disable", "actor": actor, "hook_id": hook_id, "result": "ok"})
        return result

    def proposals(self, *, status: str = "") -> list[dict[str, Any]]:
        raw = get_setting(_PROPOSAL_SETTING_KEY, [])
        if not isinstance(raw, list):
            return []
        proposals = [_json_copy(item) for item in raw if isinstance(item, dict)]
        if status:
            proposals = [item for item in proposals if str(item.get("status") or "") == status]
        return sorted(proposals, key=lambda item: str(item.get("created_at") or ""), reverse=True)

    def add_proposal(
        self,
        *,
        extension: dict[str, Any],
        hook: dict[str, Any],
        rationale: str,
        actor: str = "agent",
    ) -> dict[str, Any]:
        normalized = _normalize_hook({**hook, "enabled": False})
        proposal = {
            "id": f"proposal-{uuid.uuid4().hex[:12]}",
            "status": "pending",
            "extension": _json_copy(extension),
            "hook": normalized,
            "rationale": str(rationale or "").strip()[:2000],
            "actor": actor,
            "created_at": _now(),
            "decided_at": "",
        }
        proposals = self.proposals()
        # One current proposal per extension prevents reinstall/retry storms.
        extension_key = str((extension or {}).get("key") or "")
        proposals = [
            item for item in proposals
            if not (
                str(item.get("status") or "") == "pending"
                and str((item.get("extension") or {}).get("key") or "") == extension_key
            )
        ]
        proposals.append(proposal)
        set_setting(_PROPOSAL_SETTING_KEY, proposals)
        _audit({"kind": "proposal", "action": "create", "actor": actor, "proposal_id": proposal["id"], "hook_id": normalized["id"], "extension": extension_key, "result": "pending"})
        return _json_copy(proposal)

    def decide_proposal(self, proposal_id: str, approve: bool, *, actor: str = "user") -> dict[str, Any]:
        proposals = self.proposals()
        proposal = next((item for item in proposals if item.get("id") == proposal_id), None)
        if proposal is None:
            raise ValueError("hook proposal not found")
        if proposal.get("status") != "pending":
            raise ValueError("hook proposal is already decided")
        installed = None
        if approve:
            hook = dict(proposal.get("hook") or {})
            hook["enabled"] = True
            installed = self.save(hook, actor=actor)
        proposal["status"] = "approved" if approve else "rejected"
        proposal["decided_at"] = _now()
        proposal["decided_by"] = actor
        set_setting(_PROPOSAL_SETTING_KEY, [proposal if item.get("id") == proposal_id else item for item in proposals])
        _audit({"kind": "proposal", "action": proposal["status"], "actor": actor, "proposal_id": proposal_id, "hook_id": (proposal.get("hook") or {}).get("id"), "result": "ok"})
        return {"ok": True, "proposal": _json_copy(proposal), "hook": installed}

    def matching(self, event: str, tool_name: str = "") -> list[dict[str, Any]]:
        return [
            hook for hook in self.list()
            if hook.get("enabled") is True
            and hook.get("event") == event
            and (event not in {"PreToolUse", "PostToolUse"} or fnmatch.fnmatchcase(tool_name, str(hook.get("matcher") or "*")))
        ]

    async def execute(self, hook: dict[str, Any], payload: dict[str, Any], *, test: bool = False) -> dict[str, Any]:
        runner = hook.get("runner") or {}
        runner_type = str(runner.get("type") or "command")
        target = str(runner.get("executable") if runner_type == "command" else runner.get("path") or "").strip()
        args = [str(item) for item in runner.get("args", [])]
        if not target:
            raise RuntimeError("hook runner target is empty")
        if runner_type == "script":
            script = Path(target).expanduser()
            if not script.is_file():
                raise RuntimeError(f"hook script does not exist: {script}")
            if os.name != "nt" and not (script.stat().st_mode & stat.S_IXUSR):
                raise RuntimeError("hook script must be executable and include a shebang")
            if os.name != "nt":
                with script.open("rb") as handle:
                    if handle.read(2) != b"#!":
                        raise RuntimeError("hook script must be executable and include a shebang")
            target = str(script)
        runner_env = runner.get("env") if isinstance(runner.get("env"), dict) else {}
        env = hook_process_environment(runner_env)
        started = asyncio.get_running_loop().time()
        status = "ok"
        error = ""
        stdout_text = ""
        stderr_text = ""
        try:
            process = await asyncio.create_subprocess_exec(
                target, *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdin = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            try:
                stdout, stderr = await asyncio.wait_for(
                    _communicate_hook_process(process, stdin),
                    timeout=float(hook.get("timeout_seconds") or _DEFAULT_TIMEOUT_SECONDS),
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                raise RuntimeError(f"hook timed out after {float(hook.get('timeout_seconds') or _DEFAULT_TIMEOUT_SECONDS):g} seconds")
            stdout_text = stdout.decode("utf-8", errors="replace")
            stderr_text = stderr.decode("utf-8", errors="replace")
            if process.returncode != 0:
                raise RuntimeError(f"hook exited with code {process.returncode}")
            output: dict[str, Any] = {}
            if stdout_text.strip():
                parsed = json.loads(stdout_text)
                if not isinstance(parsed, dict):
                    raise RuntimeError("hook stdout JSON must be an object")
                output = parsed
            return output
        except asyncio.CancelledError:
            status = "cancelled"
            error = "cancelled"
            raise
        except Exception as exc:
            status = "error"
            error = str(exc)
            if "process" in locals() and process.returncode is None:
                process.kill()
                await process.wait()
            raise
        finally:
            elapsed_ms = round((asyncio.get_running_loop().time() - started) * 1000, 2)
            _audit({
                "kind": "execution", "test": bool(test), "hook_id": hook.get("id"),
                "hook_name": hook.get("name"), "event": hook.get("event"),
                "tool": payload.get("tool", {}).get("name") if isinstance(payload.get("tool"), dict) else "",
                "status": status, "error": _redact_hook_text(error, runner_env)[:1000],
                "stderr": _redact_hook_text(stderr_text, runner_env)[:4000], "duration_ms": elapsed_ms,
            })

    async def test(self, hook_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        hook = self.get(hook_id)
        if hook is None:
            raise ValueError("hook not found")
        event = _base_event(str(hook.get("event") or "SessionStart"))
        event["test"] = True
        if isinstance(payload, dict):
            event.update(_json_copy(payload))
        hook_event = str(hook.get("event") or "")
        if hook_event in {"PreToolUse", "PostToolUse"} and not isinstance(event.get("tool"), dict):
            matcher = str(hook.get("matcher") or "*")
            event["tool"] = {
                "name": matcher if not any(char in matcher for char in "*?[") else "HookTestTool",
                "arguments": {},
            }
        if hook_event == "PostToolUse" and not isinstance(event.get("result"), dict):
            event["result"] = {"success": True, "value": "Hook test", "error": ""}
        output = await self.execute(hook, event, test=True)
        return {"ok": True, "output": output}


_SERVICE = HookService()


def get_hook_service() -> HookService:
    return _SERVICE


def _base_event(event: str, *, parent_agent_id: str = "", reason: str = "") -> dict[str, Any]:
    context = current_run_context()
    return {
        "protocol_version": 1,
        "event": event,
        "timestamp": _now(),
        "agent": {
            "id": context.agent_id,
            "parent_id": str(parent_agent_id or ""),
            "caller": context.caller,
        },
        "run": {
            "session_id": context.session_id,
            "round_id": context.round_id,
            "client_request_id": context.client_request_id,
            "conversation_source": context.conversation_source,
            "reason": str(reason or ""),
        },
    }


async def run_pre_tool_hooks(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    service = get_hook_service()
    current = _json_copy(arguments)
    for hook in service.matching("PreToolUse", name):
        payload = _base_event("PreToolUse")
        payload["tool"] = {"name": name, "arguments": _json_copy(current)}
        try:
            output = await service.execute(hook, payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("PreToolUse hook %s failed: %s", hook.get("id"), exc)
            if hook.get("failure_policy") == "block":
                raise HookBlocked(f"{hook.get('name') or hook.get('id')}: {exc}") from exc
            continue
        decision = str(output.get("decision") or "allow").strip().lower()
        if decision == "block":
            raise HookBlocked(str(output.get("reason") or hook.get("name") or hook.get("id") or "blocked"))
        if decision not in {"allow", "modify"}:
            exc = RuntimeError("PreToolUse decision must be allow, modify, or block")
            if hook.get("failure_policy") == "block":
                raise HookBlocked(str(exc)) from exc
            logger.warning("PreToolUse hook %s returned invalid decision", hook.get("id"))
            continue
        if "arguments" in output:
            modified = output.get("arguments")
            if not isinstance(modified, dict):
                exc = RuntimeError("PreToolUse arguments must be an object")
                if hook.get("failure_policy") == "block":
                    raise HookBlocked(str(exc)) from exc
                logger.warning("PreToolUse hook %s returned invalid arguments", hook.get("id"))
                continue
            current = _json_copy(modified)
    return current


async def run_post_tool_hooks(name: str, arguments: dict[str, Any], result: Any, *, success: bool, error: str = "") -> None:
    service = get_hook_service()
    for hook in service.matching("PostToolUse", name):
        payload = _base_event("PostToolUse")
        payload["tool"] = {"name": name, "arguments": _json_copy(arguments)}
        payload["result"] = {"success": bool(success), "value": _json_copy(result), "error": str(error or "")}
        try:
            await service.execute(hook, payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("PostToolUse hook %s failed", hook.get("id"), exc_info=True)


async def run_lifecycle_hooks(
    event: str,
    *,
    parent_agent_id: str = "",
    reason: str = "",
    details: dict[str, Any] | None = None,
) -> str:
    if event not in {"SessionStart", "SessionEnd", "Stop"}:
        raise ValueError("invalid lifecycle hook event")
    contexts: list[str] = []
    service = get_hook_service()
    for hook in service.matching(event):
        try:
            payload = _base_event(event, parent_agent_id=parent_agent_id, reason=reason)
            if isinstance(details, dict):
                payload["outcome"] = _json_copy(details)
            output = await service.execute(hook, payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("%s hook %s failed", event, hook.get("id"), exc_info=True)
            continue
        if event == "SessionStart":
            context = str(output.get("context") or "").strip()
            if context:
                contexts.append(context[:16000])
    return "\n\n".join(contexts)
