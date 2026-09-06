"""Bounded offline checks: no Plugin imports, database migrations or writes."""
from __future__ import annotations

import ast
import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .evidence import direction


@contextmanager
def readonly_db(path: Path):
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
    connection.execute("PRAGMA query_only=ON")
    deadline = time.monotonic() + 2
    connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    try:
        yield connection
    finally:
        connection.close()


def read_document(connection, key: str) -> dict:
    row = connection.execute("SELECT payload_json FROM workbench_state WHERE key = ?", (key,)).fetchone()
    if not row:
        return {}
    if len(row[0]) > 8_000_000:
        raise ValueError("Document exceeds diagnostic limit")
    value = json.loads(row[0])
    if not isinstance(value, dict):
        raise ValueError("Invalid document")
    return value


def finding(code, status, zh, en, evidence=None, actions=None):
    return {"id": "", "code": code, "status": status, "summary": {"zh": zh, "en": en},
            "direction": direction(code), "evidence": evidence or {}, "actions": actions or []}


def config_check(data: Path):
    encrypted, key = data / "config.enc", data / ".config_key"
    if not encrypted.exists():
        return [finding("config_missing", "unknown", "尚无已保存的配置", "No saved configuration")], {}
    if not key.is_file():
        return [finding("config_key_missing", "failed", "配置解密密钥缺失；保留原配置并从备份恢复密钥", "Configuration key missing; preserve configuration and recover its key")], {}
    from cryptography.fernet import Fernet
    if encrypted.stat().st_size > 8_000_000 or key.stat().st_size > 4096:
        raise ValueError("Configuration exceeds diagnostic limit")
    config = json.loads(Fernet(key.read_bytes()).decrypt(encrypted.read_bytes()))
    if not isinstance(config, dict) or not isinstance(config.get("settings", {}), dict):
        raise ValueError("Invalid configuration schema")
    return [finding("config_readable", "passed", "配置可读取和解密", "Configuration can be read and decrypted")], config.get("settings", {})


def plugin_checks(root: Path):
    root = root.resolve()
    if not root.is_dir():
        return [finding("plugins_missing", "unknown", "插件目录不存在", "Plugin directory is absent")]
    results, count, total_bytes, observed = [], 0, 0, {}
    for path in root.rglob("*.py"):
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if root in parent.parents) or any(p.startswith(".") or p == "__pycache__" for p in path.relative_to(root).parts):
            continue
        count += 1
        if count > 2000:
            results.append(finding("plugin_scan_limit", "unknown", "插件扫描已达到上限", "Plugin scan limit reached"))
            break
        if path.stat().st_size > 1_000_000:
            results.append(finding("plugin_file_limit", "unknown", "插件文件过大，未分析", "Plugin file exceeds scan limit"))
            continue
        total_bytes += path.stat().st_size
        if total_bytes > 16_000_000:
            results.append(finding("plugin_scan_limit", "unknown", "插件扫描已达到大小上限", "Plugin scan byte limit reached"))
            break
        try:
            raw = path.read_bytes()
            observed[path.relative_to(root).as_posix()] = hashlib.sha256(raw).hexdigest()
            ast.parse(raw, filename=path.name)
        except (SyntaxError, UnicodeError) as exc:
            relative = path.relative_to(root)
            results.append(finding("plugin_syntax_error", "failed", "自定义插件存在语法或编码错误", "Editable Plugin has a syntax or encoding error",
                {"plugin": relative.parts[0], "file": relative.as_posix(), "line": getattr(exc, "lineno", None)},
                [{"kind": "restore_plugin", "target": relative.parts[0]}]))
    manifest = root / ".upstream-hashes.json"
    if manifest.is_file() and not manifest.is_symlink() and manifest.stat().st_size <= 8_000_000:
        try:
            baseline = json.loads(manifest.read_text(encoding="utf-8"))
            if baseline.get("version") != 1 or not isinstance(baseline.get("files"), dict):
                raise ValueError("Unknown manifest")
            modified = {Path(name).parts[0] for name, digest in observed.items() if name in baseline["files"] and baseline["files"][name] != digest}
            failed = {row["evidence"].get("plugin") for row in results}
            for target in sorted(modified - failed):
                results.append(finding("plugin_customized", "info", "插件与发行版基线不同；修改本身不代表故障", "Plugin differs from its release baseline; edits alone do not prove failure",
                    {"plugin": target}, [{"kind": "restore_plugin", "target": target}]))
        except (OSError, ValueError, AttributeError):
            results.append(finding("plugin_manifest_invalid", "unknown", "插件基线清单无法读取，未判断自定义差异", "Plugin baseline manifest could not be read; customizations were not compared"))
    return results or [finding("plugin_syntax_valid", "passed", "已扫描的插件语法正常；未执行插件代码", "Scanned Plugin syntax is valid; no Plugin code executed", {"files": count})]


def memory_checks(connection, scope: dict):
    project_id, chat_id = scope.get("project_id", ""), scope.get("chat_id", "")
    chat = {}
    results = []
    if chat_id:
        row = connection.execute("SELECT payload_json FROM workbench_chats WHERE chat_id = ?", (chat_id,)).fetchone()
        if not row:
            return [finding("chat_missing", "unknown", "未找到指定对话", "Selected conversation was not found")]
        if len(row[0]) > 8_000_000:
            raise ValueError("Chat exceeds diagnostic limit")
        chat = json.loads(row[0])
        actual = str(chat.get("projectId") or "")
        if project_id and project_id != actual:
            raise ValueError("Conversation does not belong to this project")
        project_id = actual
        scope["project_id"] = actual
    if not project_id:
        return [finding("memory_scope_missing", "skipped", "选择项目或对话可检查记忆链路", "Select a project or conversation to inspect memory")]
    if chat_id:
        pipeline = read_document(connection, "memory_pipeline:" + chat_id)
        if pipeline:
            results.append(finding(pipeline.get("reason", "memory_pipeline"), pipeline.get("status", "unknown"),
                "最近记忆处理阶段：" + str(pipeline.get("stage", "")), "Latest memory stage: " + str(pipeline.get("stage", "")), pipeline))
    document = read_document(connection, "project_memory_prompt:" + project_id)
    if document and document.get("schemaVersion") not in (None, 1):
        return [finding("memory_schema_unknown", "unknown", "记忆存储版本无法识别", "Unrecognized memory storage version")]
    current = document.get("current") or {}
    frozen = chat.get("projectMemorySnapshot") or {}
    if frozen and current.get("hash") and frozen.get("hash") != current.get("hash"):
        results.append(finding("memory_snapshot_older", "info", "记忆已更新，此对话仍使用创建时的快照；新建对话可使用新记忆", "Stored memory changed; this conversation retains its initial snapshot. A new conversation uses the new memory.",
            {"stored_version": current.get("modifiedAt", ""), "conversation_version": frozen.get("modifiedAt", "")}))
    if chat.get("projectMemoryActive") is False:
        results.append(finding("memory_disabled", "skipped", "此对话的项目记忆已关闭", "Project memory is disabled for this conversation"))
    jobs = document.get("jobs") or []
    for job in jobs[-30:]:
        if chat_id and job.get("chatId") != chat_id:
            continue
        if scope.get("job_id") and job.get("id") != scope["job_id"]:
            continue
        status = job.get("status")
        state = "failed" if status in {"failed", "conflict"} else "passed" if status in {"saved", "unchanged"} else "info"
        code = str(job.get("errorType") or "memory_" + str(status))
        results.append(finding(code, state, "项目记忆任务：" + str(status), "Project memory job: " + str(status),
            {"job_id": job.get("id"), "chat_id": job.get("chatId"), "status": status},
            [{"kind": "retry_memory", "target": job.get("id")}] if state == "failed" else []))
    if not results:
        results.append(finding("memory_no_job", "skipped", "未发现匹配的学习任务；可能尚未达到阈值或没有可学习内容", "No matching learning job; threshold may not be reached or content may be ineligible"))
    return results


def run_checks(*, data: Path, database: Path, plugins: Path, scope: dict) -> tuple[list, dict]:
    findings, settings = [], {}
    try:
        rows, settings = config_check(data)
        findings.extend(rows)
    except Exception as exc:
        findings.append(finding("config_unreadable", "failed", "配置读取或解密失败；不要重置原文件", "Configuration cannot be read or decrypted; preserve the original", {"exception_type": type(exc).__name__}))
    try:
        findings.extend(plugin_checks(plugins))
    except Exception as exc:
        findings.append(finding("plugin_scan_failed", "unknown", "无法完成插件扫描", "Plugin scan could not complete", {"exception_type": type(exc).__name__}))
    if not database.is_file():
        findings.append(finding("database_missing", "unknown", "运行数据库不存在", "Runtime database is absent"))
    else:
        try:
            with readonly_db(database) as connection:
                check = connection.execute("PRAGMA quick_check(1)").fetchone()[0]
                if check != "ok":
                    raise ValueError("Database quick check failed")
                findings.append(finding("database_readable", "passed", "数据库只读检查通过；未验证实际写入", "Database read checks passed; writing was not tested"))
                findings.extend(memory_checks(connection, scope))
        except Exception as exc:
            findings.append(finding("storage_check_failed", "unknown", "存储检查未完成：检查数据库、版本或对话归属", "Storage check incomplete: inspect database, schema or conversation scope", {"exception_type": type(exc).__name__}))
    if scope.get("chat_id"):
        digest = hashlib.sha256(scope["chat_id"].encode()).hexdigest()
        tree = database.parent / "agent-state" / "context" / "trees" / digest[:2] / (digest + ".sqlite3")
        if tree.exists():
            try:
                with readonly_db(tree) as connection:
                    rows = connection.execute("SELECT sequence, hook_id, status, attempts FROM hook_queue WHERE status IN ('failed', 'blocked', 'running') ORDER BY sequence DESC LIMIT 30").fetchall()
                    for sequence, hook_id, status, attempts in rows:
                        findings.append(finding("hook_" + status, "failed" if status in {"failed", "blocked"} else "info",
                            "后台 Hook 投递：" + status, "Background Hook delivery: " + status,
                            {"delivery_id": sequence, "hook_id": hook_id, "status": status, "attempts": attempts}))
            except (OSError, sqlite3.Error):
                findings.append(finding("hook_state_unknown", "unknown", "无法检查上下文 Hook 队列", "Context Hook queue could not be inspected"))
    return findings, settings
