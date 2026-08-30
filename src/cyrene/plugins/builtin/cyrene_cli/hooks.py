"""CLI subprocess integrations dispatched by the tree-local Hook system."""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import os
import re
import stat
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cyrene.core.hook import (
    POST_TOOL_USE,
    PRE_TOOL_USE,
    SESSION_END,
    SESSION_START,
    STOP,
    TURN_START,
    HookEvent,
)
from cyrene.core.hook.storage import encode_event_payload
from cyrene.config import DATA_DIR
from cyrene.plugins.builtin.cyrene_extensions.extension_service import agent_process_environment
from cyrene.platform.secret_redaction import redact_text
from cyrene.platform.settings_store import get as get_setting, set_ as set_setting

logger = logging.getLogger(__name__)

CLI_HOOK_EVENTS = (
    PRE_TOOL_USE,
    POST_TOOL_USE,
    SESSION_START,
    TURN_START,
    SESSION_END,
    STOP,
)
_HOOKS_KEY = "cli_plugin_hooks"
_PROPOSALS_KEY = "cli_plugin_hook_proposals"
_RESULTS_KEY = "cli_plugin_hook_configuration_results"
_AUDIT_FILE = DATA_DIR / "cli_plugin_hook_audit.jsonl"
_DEFAULT_TIMEOUT_SECONDS = 10.0
_MAX_STDOUT_BYTES = 256 * 1024
_MAX_STDERR_BYTES = 64 * 1024
_ENVIRONMENT_KEYS = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "TEMP", "TMP",
    "LANG", "LC_ALL", "LC_CTYPE", "SYSTEMROOT", "WINDIR", "COMSPEC",
    "PATHEXT", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _safe_id(value: Any) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip())
    return normalized.strip("-.")[:80]


def _audit(record: Mapping[str, Any]) -> None:
    try:
        _AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _AUDIT_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"timestamp": _now(), **dict(record)}, ensure_ascii=False, default=str) + "\n")
    except Exception:
        logger.warning("Unable to append CLI Hook audit record", exc_info=True)


def hook_audit_records(limit: int = 200) -> list[dict[str, Any]]:
    if not _AUDIT_FILE.is_file():
        return []
    try:
        lines = _AUDIT_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []
    records: list[dict[str, Any]] = []
    for line in reversed(lines[-max(1, min(int(limit), 1000)) :]):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def hook_process_environment(custom: Mapping[str, Any] | None = None) -> dict[str, str]:
    """Give Hook subprocesses CLI paths without installer credentials."""

    source = agent_process_environment()
    environment = {
        key: str(value)
        for key, value in source.items()
        if key.upper() in _ENVIRONMENT_KEYS or key.upper().startswith("LC_")
    }
    if custom:
        environment.update({str(key): str(value) for key, value in custom.items()})
    return environment


def _normalize_hook(
    raw: Mapping[str, Any],
    *,
    existing: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    previous = dict(existing or {})
    event = str(raw.get("event", previous.get("event", ""))).strip()
    if event not in CLI_HOOK_EVENTS:
        raise ValueError("unsupported CLI Hook event")
    hook_id = _safe_id(raw.get("id") or previous.get("id") or f"hook-{uuid.uuid4().hex[:12]}")
    if not hook_id:
        raise ValueError("Hook id is required")
    name = str(raw.get("name", previous.get("name", hook_id))).strip()[:120]
    if not name:
        raise ValueError("Hook name is required")
    previous_runner = previous.get("runner") if isinstance(previous.get("runner"), Mapping) else {}
    runner = raw.get("runner", previous_runner)
    if not isinstance(runner, Mapping):
        raise ValueError("runner must be an object")
    runner_type = str(runner.get("type") or previous_runner.get("type") or "command").strip().lower()
    if runner_type not in {"command", "script"}:
        raise ValueError("runner.type must be command or script")
    target_key = "executable" if runner_type == "command" else "path"
    target = str(runner.get(target_key, previous_runner.get(target_key, "")) or "").strip()
    if not target:
        raise ValueError(f"runner.{target_key} is required")
    arguments = runner.get("args", previous_runner.get("args", []))
    if not isinstance(arguments, list) or not all(isinstance(item, str) for item in arguments):
        raise ValueError("runner.args must be an array of strings")
    environment = runner.get("env", previous_runner.get("env", {}))
    if not isinstance(environment, Mapping) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in environment.items()
    ):
        raise ValueError("runner.env must contain string keys and values")
    try:
        timeout = float(raw.get("timeout_seconds", previous.get("timeout_seconds", _DEFAULT_TIMEOUT_SECONDS)))
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout_seconds must be numeric") from exc
    if not 0.1 <= timeout <= 60:
        raise ValueError("timeout_seconds must be between 0.1 and 60")
    try:
        priority = int(raw.get("priority", previous.get("priority", 100)))
    except (TypeError, ValueError) as exc:
        raise ValueError("priority must be an integer") from exc
    if not -10000 <= priority <= 10000:
        raise ValueError("priority must be between -10000 and 10000")
    failure_policy = str(raw.get("failure_policy", previous.get("failure_policy", "open"))).strip().lower()
    if failure_policy not in {"open", "block"}:
        raise ValueError("failure_policy must be open or block")
    if failure_policy == "block" and event != PRE_TOOL_USE:
        raise ValueError("only PreToolUse Hooks may block on failure")
    matcher = str(raw.get("matcher", previous.get("matcher", "*")) or "*").strip()[:200]
    if event not in {PRE_TOOL_USE, POST_TOOL_USE}:
        matcher = "*"
    extension = raw.get("extension", previous.get("extension", {}))
    configuration_status = str(
        raw.get("configuration_status", previous.get("configuration_status", "ready"))
    )
    if configuration_status not in {"configuring", "ready", "failed"}:
        raise ValueError("configuration_status is invalid")
    return {
        "id": hook_id,
        "name": name,
        "description": str(raw.get("description", previous.get("description", ""))).strip()[:500],
        "event": event,
        "matcher": matcher,
        "enabled": bool(raw.get("enabled", previous.get("enabled", False))),
        "priority": priority,
        "failure_policy": failure_policy,
        "timeout_seconds": timeout,
        "runner": {
            "type": runner_type,
            target_key: target,
            "args": list(arguments),
            "env": dict(environment),
        },
        "extension": _copy(extension) if isinstance(extension, Mapping) else {},
        "action_instruction": str(
            raw.get("action_instruction", previous.get("action_instruction", ""))
        ).strip()[:4000],
        "configured_by_agent": bool(
            raw.get("configured_by_agent", previous.get("configured_by_agent", False))
        ),
        "configuration_status": configuration_status,
        "configuration_error": str(
            raw.get("configuration_error", previous.get("configuration_error", ""))
        ).strip()[:1000],
        "generation_preserve_tuning": bool(
            raw.get(
                "generation_preserve_tuning",
                previous.get("generation_preserve_tuning", False),
            )
        ),
        "created_at": str(previous.get("created_at") or raw.get("created_at") or _now()),
        "updated_at": _now(),
    }


def public_hook(hook: Mapping[str, Any]) -> dict[str, Any]:
    value = _copy(hook)
    value.pop("generation_preserve_tuning", None)
    runner = value.get("runner") if isinstance(value.get("runner"), dict) else {}
    environment = runner.pop("env", {})
    runner["environment_keys"] = sorted(str(key) for key in environment) if isinstance(environment, dict) else []
    value["runner"] = runner
    return value


def public_proposal(proposal: Mapping[str, Any]) -> dict[str, Any]:
    value = _copy(proposal)
    if isinstance(value.get("hook"), dict):
        value["hook"] = public_hook(value["hook"])
    return value


async def _read_stream(
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
            raise RuntimeError(f"Hook {label} exceeded {limit // 1024} KB")
        if len(retained) < limit:
            retained.extend(chunk[: limit - len(retained)])
    return bytes(retained)


async def _communicate(process: asyncio.subprocess.Process, payload: bytes) -> tuple[bytes, bytes]:
    if process.stdin is not None:
        try:
            process.stdin.write(payload)
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            process.stdin.close()
    stdout_task = asyncio.create_task(_read_stream(process.stdout, limit=_MAX_STDOUT_BYTES, strict=True, label="stdout"))
    stderr_task = asyncio.create_task(_read_stream(process.stderr, limit=_MAX_STDERR_BYTES, strict=False, label="stderr"))
    try:
        stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
        await process.wait()
        return stdout, stderr
    except BaseException:
        stdout_task.cancel()
        stderr_task.cancel()
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        raise


def event_payload(event: HookEvent, *, test: bool = False) -> dict[str, Any]:
    payload = json.loads(encode_event_payload(event))
    result = {
        "protocol_version": 2,
        "event": event.name,
        "timestamp": event.time.isoformat(),
        "tree": {
            "id": event.tree_id,
            "node_id": event.node_id or "",
            "is_root": bool(event.is_root),
        },
    }
    if isinstance(payload, Mapping):
        result.update(payload)
    else:
        result["payload"] = payload
    if test:
        result["test"] = True
    return result


class CliHookService:
    """Configuration store and subprocess dispatcher for CLI Plugin Hooks."""

    def list(self) -> list[dict[str, Any]]:
        raw = get_setting(_HOOKS_KEY, [])
        hooks = [dict(item) for item in raw if isinstance(item, Mapping)] if isinstance(raw, list) else []
        return sorted(hooks, key=lambda item: (int(item.get("priority", 100)), str(item.get("created_at", "")), str(item.get("id", ""))))

    def get(self, hook_id: str) -> dict[str, Any] | None:
        return next((item for item in self.list() if str(item.get("id")) == str(hook_id)), None)

    def save(self, raw: Mapping[str, Any], *, actor: str = "user") -> dict[str, Any]:
        hooks = self.list()
        requested_id = _safe_id(raw.get("id"))
        existing = next((item for item in hooks if str(item.get("id")) == requested_id), None) if requested_id else None
        normalized = _normalize_hook(raw, existing=existing)
        if existing is None:
            hooks.append(normalized)
            action = "create"
        else:
            hooks = [normalized if str(item.get("id")) == normalized["id"] else item for item in hooks]
            action = "update"
        set_setting(_HOOKS_KEY, hooks)
        _audit({"kind": "configuration", "action": action, "actor": actor, "hook_id": normalized["id"], "event": normalized["event"], "enabled": normalized["enabled"]})
        return _copy(normalized)

    def create_generation_request(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        """Persist the small user-authored brief before an Agent configures it."""

        event = str(raw.get("event") or "").strip()
        if event not in CLI_HOOK_EVENTS:
            raise ValueError("unsupported CLI Hook event")
        name = str(raw.get("name") or "").strip()[:120]
        if not name:
            raise ValueError("Hook name is required")
        instruction = str(raw.get("action_instruction") or "").strip()[:4000]
        if not instruction:
            raise ValueError("Hook action instruction is required")
        matcher = str(raw.get("matcher") or "*").strip()[:200] or "*"
        if event not in {PRE_TOOL_USE, POST_TOOL_USE}:
            matcher = "*"
        hook_id = _safe_id(f"user-{name}-{uuid.uuid4().hex[:8]}")
        now = _now()
        hook = {
            "id": hook_id,
            "name": name,
            "description": str(raw.get("description") or "").strip()[:500],
            "event": event,
            "matcher": matcher,
            "enabled": False,
            "priority": 100,
            "failure_policy": "open",
            "timeout_seconds": _DEFAULT_TIMEOUT_SECONDS,
            "runner": {"type": "script", "path": "", "args": [], "env": {}},
            "extension": {},
            "action_instruction": instruction,
            "configured_by_agent": True,
            "configuration_status": "configuring",
            "configuration_error": "",
            "generation_preserve_tuning": False,
            "created_at": now,
            "updated_at": now,
        }
        hooks = self.list()
        hooks.append(hook)
        set_setting(_HOOKS_KEY, hooks)
        _audit({
            "kind": "configuration",
            "action": "request_agent_configuration",
            "actor": "user",
            "hook_id": hook_id,
            "event": event,
            "matcher": matcher,
        })
        return _copy(hook)

    def update_generation_request(
        self,
        hook_id: str,
        raw: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Update an Agent brief and return it to the configuring state."""

        hooks = self.list()
        current = next(
            (item for item in hooks if str(item.get("id")) == str(hook_id)),
            None,
        )
        if current is None or current.get("configured_by_agent") is not True:
            raise ValueError("Agent-configured Hook not found")
        event = str(raw.get("event", current.get("event", ""))).strip()
        if event not in CLI_HOOK_EVENTS:
            raise ValueError("unsupported CLI Hook event")
        instruction = str(
            raw.get("action_instruction", current.get("action_instruction", ""))
        ).strip()[:4000]
        if not instruction:
            raise ValueError("Hook action instruction is required")
        matcher = str(
            raw.get("matcher", current.get("matcher", "*")) or "*"
        ).strip()[:200] or "*"
        if event not in {PRE_TOOL_USE, POST_TOOL_USE}:
            matcher = "*"
        try:
            timeout = float(
                raw.get("timeout_seconds", current.get("timeout_seconds", 10))
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("timeout_seconds must be numeric") from exc
        if not 0.1 <= timeout <= 60:
            raise ValueError("timeout_seconds must be between 0.1 and 60")
        try:
            priority = int(raw.get("priority", current.get("priority", 100)))
        except (TypeError, ValueError) as exc:
            raise ValueError("priority must be an integer") from exc
        if not -10000 <= priority <= 10000:
            raise ValueError("priority must be between -10000 and 10000")
        current.update({
            "name": str(raw.get("name", current.get("name", hook_id))).strip()[:120],
            "description": str(
                raw.get("description", current.get("description", ""))
            ).strip()[:500],
            "event": event,
            "matcher": matcher,
            "enabled": False,
            "timeout_seconds": timeout,
            "priority": priority,
            "action_instruction": instruction,
            "configuration_status": "configuring",
            "configuration_error": "",
            "generation_preserve_tuning": True,
            "updated_at": _now(),
        })
        if not current["name"]:
            raise ValueError("Hook name is required")
        set_setting(
            _HOOKS_KEY,
            [current if str(item.get("id")) == str(hook_id) else item for item in hooks],
        )
        _audit({
            "kind": "configuration",
            "action": "request_agent_reconfiguration",
            "actor": "user",
            "hook_id": hook_id,
            "event": event,
            "matcher": matcher,
        })
        return _copy(current)

    def set_generation_state(
        self,
        hook_id: str,
        *,
        status: str,
        error: str = "",
    ) -> dict[str, Any]:
        if status not in {"configuring", "failed"}:
            raise ValueError("invalid Hook generation status")
        hooks = self.list()
        current = next(
            (item for item in hooks if str(item.get("id")) == str(hook_id)),
            None,
        )
        if current is None or current.get("configured_by_agent") is not True:
            raise ValueError("Agent-configured Hook not found")
        current["configuration_status"] = status
        current["configuration_error"] = str(error or "").strip()[:1000]
        current["enabled"] = False
        current["updated_at"] = _now()
        set_setting(
            _HOOKS_KEY,
            [current if str(item.get("id")) == str(hook_id) else item for item in hooks],
        )
        return _copy(current)

    def complete_generation(
        self,
        hook_id: str,
        generated: Mapping[str, Any],
    ) -> dict[str, Any]:
        current = self.get(hook_id)
        if current is None or current.get("configured_by_agent") is not True:
            raise ValueError("Agent-configured Hook not found")
        return self.save(
            {
                **dict(current),
                **dict(generated),
                "id": hook_id,
                "configured_by_agent": True,
                "configuration_status": "ready",
                "configuration_error": "",
                "enabled": True,
            },
            actor="agent",
        )

    def delete(self, hook_id: str, *, actor: str = "user") -> bool:
        hooks = self.list()
        remaining = [item for item in hooks if str(item.get("id")) != str(hook_id)]
        if len(remaining) == len(hooks):
            return False
        set_setting(_HOOKS_KEY, remaining)
        _audit({"kind": "configuration", "action": "delete", "actor": actor, "hook_id": hook_id})
        return True

    def set_enabled(self, hook_id: str, enabled: bool, *, actor: str = "user") -> dict[str, Any]:
        existing = self.get(hook_id)
        if existing is None:
            raise ValueError("CLI Hook not found")
        if (
            existing.get("configured_by_agent") is True
            and existing.get("configuration_status") != "ready"
        ):
            raise ValueError("Agent configuration is not complete")
        existing["enabled"] = bool(enabled)
        return self.save(existing, actor=actor)

    def proposals(self) -> list[dict[str, Any]]:
        raw = get_setting(_PROPOSALS_KEY, [])
        values = [_copy(item) for item in raw if isinstance(item, Mapping)] if isinstance(raw, list) else []
        return sorted(values, key=lambda item: str(item.get("created_at", "")), reverse=True)

    def add_proposal(
        self,
        *,
        extension: Mapping[str, Any],
        hook: Mapping[str, Any],
        rationale: str,
        actor: str = "agent",
    ) -> dict[str, Any]:
        normalized = _normalize_hook({**dict(hook), "enabled": False})
        proposal = {
            "id": f"proposal-{uuid.uuid4().hex[:12]}",
            "status": "pending",
            "extension": _copy(extension),
            "hook": normalized,
            "rationale": str(rationale or "").strip()[:2000],
            "actor": actor,
            "created_at": _now(),
            "decided_at": "",
        }
        extension_key = str(extension.get("key") or "")
        proposals = [
            item for item in self.proposals()
            if not (str(item.get("status")) == "pending" and str((item.get("extension") or {}).get("key")) == extension_key)
        ]
        proposals.append(proposal)
        set_setting(_PROPOSALS_KEY, proposals)
        _audit({"kind": "proposal", "action": "create", "actor": actor, "proposal_id": proposal["id"], "hook_id": normalized["id"], "extension": extension_key})
        return _copy(proposal)

    def decide_proposal(self, proposal_id: str, approve: bool, *, actor: str = "user") -> dict[str, Any]:
        proposals = self.proposals()
        proposal = next((item for item in proposals if str(item.get("id")) == str(proposal_id)), None)
        if proposal is None:
            raise ValueError("CLI Hook proposal not found")
        if proposal.get("status") != "pending":
            raise ValueError("CLI Hook proposal is already decided")
        installed = None
        if approve:
            installed = self.save({**dict(proposal.get("hook") or {}), "enabled": True}, actor=actor)
        proposal["status"] = "approved" if approve else "rejected"
        proposal["decided_at"] = _now()
        proposal["decided_by"] = actor
        set_setting(_PROPOSALS_KEY, [proposal if str(item.get("id")) == str(proposal_id) else item for item in proposals])
        _audit({"kind": "proposal", "action": proposal["status"], "actor": actor, "proposal_id": proposal_id})
        return {"ok": True, "proposal": public_proposal(proposal), "hook": public_hook(installed) if installed else None}

    def configuration_results(self) -> dict[str, Any]:
        raw = get_setting(_RESULTS_KEY, {})
        return dict(raw) if isinstance(raw, Mapping) else {}

    def record_configuration_result(self, extension_key: str, result: Mapping[str, Any]) -> None:
        records = self.configuration_results()
        records[str(extension_key)] = _copy(result)
        set_setting(_RESULTS_KEY, records)

    def matching(self, event: HookEvent) -> list[dict[str, Any]]:
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        tool = payload.get("tool") if isinstance(payload, Mapping) else {}
        tool_name = str(tool.get("name") or "") if isinstance(tool, Mapping) else ""
        return [
            hook for hook in self.list()
            if hook.get("enabled") is True
            and hook.get("event") == event.name
            and (event.name not in {PRE_TOOL_USE, POST_TOOL_USE} or fnmatch.fnmatchcase(tool_name, str(hook.get("matcher") or "*")))
        ]

    async def execute(self, hook: Mapping[str, Any], payload: Mapping[str, Any], *, test: bool = False) -> dict[str, Any]:
        runner = hook.get("runner") if isinstance(hook.get("runner"), Mapping) else {}
        runner_type = str(runner.get("type") or "command")
        target = str(
            (runner.get("executable") if runner_type == "command" else runner.get("path"))
            or ""
        ).strip()
        arguments = [str(item) for item in runner.get("args", [])]
        if not target:
            raise RuntimeError("CLI Hook runner target is empty")
        if runner_type == "script":
            script = Path(target).expanduser()
            if not script.is_file():
                raise RuntimeError(f"CLI Hook script does not exist: {script}")
            if os.name != "nt":
                with script.open("rb") as handle:
                    has_shebang = handle.read(2) == b"#!"
                if not script.stat().st_mode & stat.S_IXUSR or not has_shebang:
                    raise RuntimeError("CLI Hook script must be executable and include a shebang")
            target = str(script)
        custom_environment = runner.get("env") if isinstance(runner.get("env"), Mapping) else {}
        environment = hook_process_environment(custom_environment)
        started = asyncio.get_running_loop().time()
        status, error, stderr_text = "ok", "", ""
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                target,
                *arguments,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=environment,
            )
            raw_payload = json.dumps(dict(payload), ensure_ascii=False, default=str).encode("utf-8")
            try:
                stdout, stderr = await asyncio.wait_for(
                    _communicate(process, raw_payload),
                    timeout=float(hook.get("timeout_seconds") or _DEFAULT_TIMEOUT_SECONDS),
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                raise RuntimeError(f"CLI Hook timed out after {float(hook.get('timeout_seconds') or _DEFAULT_TIMEOUT_SECONDS):g} seconds") from None
            stderr_text = stderr.decode("utf-8", errors="replace")
            if process.returncode != 0:
                raise RuntimeError(f"CLI Hook exited with code {process.returncode}")
            if not stdout.strip():
                return {}
            parsed = json.loads(stdout.decode("utf-8", errors="replace"))
            if not isinstance(parsed, dict):
                raise RuntimeError("CLI Hook stdout JSON must be an object")
            return parsed
        except asyncio.CancelledError:
            status, error = "cancelled", "cancelled"
            raise
        except Exception as exc:
            status, error = "error", str(exc)
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            raise
        finally:
            secrets = custom_environment.values() if isinstance(custom_environment, Mapping) else ()
            redacted_error = str(redact_text(error) or "")
            redacted_stderr = str(redact_text(stderr_text) or "")
            for secret in secrets:
                if str(secret):
                    redacted_error = redacted_error.replace(str(secret), "[REDACTED]")
                    redacted_stderr = redacted_stderr.replace(str(secret), "[REDACTED]")
            _audit({
                "kind": "execution", "test": bool(test), "hook_id": hook.get("id"),
                "hook_name": hook.get("name"), "event": hook.get("event"),
                "status": status, "error": redacted_error[:1000],
                "stderr": redacted_stderr[:4000],
                "duration_ms": round((asyncio.get_running_loop().time() - started) * 1000, 2),
            })

    async def dispatch(self, event: HookEvent) -> dict[str, Any]:
        current_arguments: dict[str, Any] | None = None
        contexts: list[str] = []
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        tool = payload.get("tool") if isinstance(payload, Mapping) else {}
        if event.name == PRE_TOOL_USE and isinstance(tool, Mapping):
            current_arguments = dict(tool.get("arguments") or {})
        for hook in self.matching(event):
            effective_event = event
            if current_arguments is not None:
                effective_event = HookEvent(
                    event.name,
                    event.tree_id,
                    event.time,
                    payload={**dict(payload), "tool": {**dict(tool), "arguments": dict(current_arguments)}},
                    node_id=event.node_id,
                    is_root=event.is_root,
                )
            try:
                output = await self.execute(hook, event_payload(effective_event))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("CLI Hook %s failed", hook.get("id"), exc_info=True)
                if event.name == PRE_TOOL_USE and hook.get("failure_policy") == "block":
                    return {"decision": "block", "reason": f"{hook.get('name') or hook.get('id')}: {exc}"}
                continue
            if event.name == PRE_TOOL_USE:
                decision = str(output.get("decision") or "allow").strip().lower()
                if decision == "block":
                    return {"decision": "block", "reason": str(output.get("reason") or hook.get("name") or hook.get("id"))}
                if decision not in {"allow", "modify"}:
                    if hook.get("failure_policy") == "block":
                        return {"decision": "block", "reason": "CLI Hook returned an invalid decision"}
                    continue
                if "arguments" in output:
                    if not isinstance(output.get("arguments"), Mapping):
                        if hook.get("failure_policy") == "block":
                            return {"decision": "block", "reason": "CLI Hook returned invalid arguments"}
                    else:
                        current_arguments = dict(output["arguments"])
            elif event.name in {SESSION_START, TURN_START}:
                context = str(output.get("context") or "").strip()
                if context:
                    contexts.append(context[:16000])
        if event.name == PRE_TOOL_USE:
            return {"decision": "modify" if current_arguments != dict(tool.get("arguments") or {}) else "allow", "arguments": current_arguments or {}}
        if event.name in {SESSION_START, TURN_START} and contexts:
            return {"context": "\n\n".join(contexts)}
        return {}

    async def test(self, hook_id: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        hook = self.get(hook_id)
        if hook is None:
            raise ValueError("CLI Hook not found")
        if (
            hook.get("configured_by_agent") is True
            and hook.get("configuration_status") != "ready"
        ):
            raise ValueError("Agent configuration is not complete")
        event = str(hook.get("event") or SESSION_START)
        payload_data = dict(payload or {})
        if event in {PRE_TOOL_USE, POST_TOOL_USE} and not isinstance(payload_data.get("tool"), Mapping):
            matcher = str(hook.get("matcher") or "*")
            payload_data["tool"] = {"name": matcher if not any(char in matcher for char in "*?[") else "HookTestTool", "arguments": {}}
        if event == POST_TOOL_USE and not isinstance(payload_data.get("result"), Mapping):
            payload_data["result"] = {"success": True, "value": "Hook test", "error": ""}
        hook_event = HookEvent(event, "cli-hook-test", datetime.now(timezone.utc), payload=payload_data, node_id="root", is_root=True)
        output = await self.execute(hook, event_payload(hook_event, test=True), test=True)
        return {"ok": True, "output": output}


__all__ = [
    "CLI_HOOK_EVENTS",
    "CliHookService",
    "hook_audit_records",
    "hook_process_environment",
    "public_hook",
    "public_proposal",
]
