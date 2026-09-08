"""Bounded, run-scoped reads of durable runtime diagnostics."""
from __future__ import annotations

import json
import re
import sqlite3
import zlib
from pathlib import Path

from .checks import finding, readonly_db
from .evidence import redact

# Never include messages, tool arguments/results, request bodies or model text.
_FIELDS = ('type', 'event_type', 'stage', 'status', 'code', 'failure_kind', 'failureKind',
           'termination_reason', 'error_type', 'exception_type', 'retry_scope', 'finish_reason',
           'tool_name', 'transition', 'transition_kind', 'kind', 'name', 'operation', 'phase')


def metadata(value):
    if not isinstance(value, dict):
        return {}
    result = {k: v[:240] if isinstance(v, str) else v for k in _FIELDS
              if isinstance((v := value.get(k)), (str, bool, int, float))}
    for key in ('payload', 'attributes', 'error_details'):
        if isinstance(value.get(key), dict):
            result.update(metadata(value[key]))
    return redact(result)


def decode(raw):
    if len(raw) > 1_000_000:
        raise ValueError('Diagnostic record too large')
    if isinstance(raw, bytes):
        from cyrene.workbench.chat.chat_runs import DURABLE_EVENT_PREFIX
        if raw.startswith(DURABLE_EVENT_PREFIX):
            raw = zlib.decompressobj().decompress(raw[len(DURABLE_EVENT_PREFIX):], 1_000_001)
            if len(raw) > 1_000_000:
                raise ValueError('Expanded record too large')
    return json.loads(raw)


def collect_runtime_evidence(database: Path, data: Path, scope: dict) -> list[dict]:
    run = scope.get('run_id')
    if not run:
        return []
    findings = []
    def add(code, status, zh, en, evidence):
        findings.append(finding(code, status, zh, en, {'run_id': run, **evidence}))
    sources = (
        ('workbench_chat_runs', 'SELECT status, termination_reason, outcome_kind FROM workbench_chat_runs WHERE run_id = ? AND chat_id = ? LIMIT 1'),
        ('workbench_chat_run_events', 'SELECT event_json FROM workbench_chat_run_events WHERE run_id = ? ORDER BY seq DESC LIMIT 80'),
        ('workbench_agent_run_events', 'SELECT payload_json, event_type, termination_reason FROM workbench_agent_run_events WHERE run_id = ? ORDER BY created_at DESC LIMIT 40'),
        ('runtime_trace_spans', 'SELECT attributes_json, kind, name, status FROM runtime_trace_spans WHERE run_id = ? ORDER BY started_at DESC LIMIT 40'),
    )
    for source, sql in sources:
        count = 0
        try:
            with readonly_db(database) as db:
                args = (run, scope.get('chat_id', '')) if source == 'workbench_chat_runs' else (run,)
                rows = db.execute(sql, args).fetchall()
            for row in rows:
                try:
                    if source == 'workbench_chat_runs':
                        value = dict(zip(('status', 'termination_reason', 'outcome_kind'), row))
                    else:
                        value = metadata(decode(row[0]))
                        if source == 'workbench_agent_run_events':
                            value.update(event_type=row[1], termination_reason=row[2])
                        elif source == 'runtime_trace_spans':
                            value.update(kind=row[1], name=row[2], status=row[3])
                    failed = value.get('status') in {'error', 'failed'} or value.get('outcome_kind') == 'error' or value.get('type') == 'error' or value.get('event_type') == 'run.failed'
                    if failed or count < 3:
                        code = value.get('failure_kind') or value.get('failureKind') or value.get('code') or ('run_terminal_failed' if failed else 'run_trace')
                        add(str(code), 'failed' if failed else 'info', '本次运行终止记录与轨迹', 'Run termination and trace', {'source': source, **value})
                        count += 1
                except (ValueError, TypeError, UnicodeError, zlib.error):
                    continue
            add('diagnostic_source_checked', 'info', '已读取本地运行证据源', 'Local runtime evidence source inspected', {'source': source, 'records_read': len(rows), 'records_kept': count})
        except (OSError, sqlite3.Error):
            add('diagnostic_source_unavailable', 'unknown', '此安装没有可读的该类运行记录', 'This runtime evidence source is unavailable', {'source': source})
    # Only structured metadata on lines naming this exact run. No free-form log
    # bodies or adjacent lines from concurrent conversations leave the host.
    paths = sorted((data / 'logs').glob('cyrene.log*'), reverse=True)[:8]
    matched = 0
    read = 0
    for path in paths:
        if path.is_symlink() or not path.is_file():
            continue
        try:
            with path.open('rb') as stream:
                stream.seek(max(0, path.stat().st_size - 512_000))
                lines = stream.read(512_000).decode('utf-8', errors='replace').splitlines()
            read += 1
            for line in lines:
                if not re.search(r'(?<![\w-])' + re.escape(run) + r'(?![\w-])', line):
                    continue
                matched += 1
                fields = {}
                if 'agent.operation ' in line:
                    try:
                        fields = metadata(json.loads(line.split('agent.operation ', 1)[1]))
                    except ValueError:
                        pass
                fields.update(dict(re.findall(r'\b(' + '|'.join(_FIELDS) + r')=[\"\']?([\w.:-]+)', line)))
                if fields and matched <= 20:
                    add('run_log_metadata', 'info', '本次运行日志元数据', 'Run log metadata', metadata(fields))
        except OSError:
            continue
    add('run_logs_checked', 'info', '已检索本地运行日志', 'Local run logs searched', {'files_read': read, 'matching_lines': matched, 'scope': 'last 512 KB of up to 8 files'})
    return findings
