"""Persistent generated repair plans, reviewed commits and recoverable rollback."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import difflib
import json
from pathlib import Path
from uuid import uuid4

from .agent_repair import RepairWorkspace, generate_repair
from .evidence import redact, redact_text
from .repair_executor import capabilities, run_probe, syntax_check
from .repair_workspace import (apply_changes, atomic_write, decode_snapshot, digest, encode_snapshot,
                               manifest, repair_lock, snapshot, target_path)
from .repository import ReportRepository


def plan_hash(plan):
    return digest(json.dumps({key: plan.get(key) for key in
                              ('action', 'source_manifest', 'changes', 'probe', 'baseline', 'verification')}, sort_keys=True).encode())


class RepairService:
    def __init__(self, doctor):
        self.doctor = doctor
        self.repository = ReportRepository(doctor.data / 'doctor' / 'repairs', retain=None, max_size=16_000_000)
        self.commits = set()

    def save(self, plan, event=None):
        if event:
            plan.setdefault('journal', []).append({'event': event, 'at': datetime.now(timezone.utc).isoformat()})
        return self.repository.save(plan)

    def get(self, identifier):
        plan = self.repository.get(identifier)
        if plan.get('action', {}).get('kind') != 'patch_plugin':
            return plan
        if plan['status'] == 'generating' and identifier not in self.doctor.tasks:
            plan['status'] = 'interrupted'
            self.save(plan, 'generation_interrupted')
        if plan['status'] in {'applying', 'rolling_back'} and not self.commits:
            plan['status'] = 'interrupted'
            self.save(plan, 'commit_interrupted')
        return plan

    def recent(self, scope):
        results = []
        paths = sorted(self.repository.directory.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in paths[:200]:
            try:
                plan = self.get(path.stem)
                if plan.get('action', {}).get('kind') == 'patch_plugin' and plan['scope'] == scope:
                    results.append({key: plan[key] for key in ('id', 'status', 'action', 'created_at')})
                    if len(results) == 10:
                        break
            except (OSError, ValueError, KeyError):
                continue
        return results

    @staticmethod
    def public(plan):
        result = {key: plan[key] for key in ('id', 'status', 'action', 'report_id', 'scope', 'created_at',
                  'summary', 'phase', 'error', 'plan_hash', 'baseline', 'verification', 'outcome', 'journal') if key in plan}
        result['files'] = [change['path'] for change in plan.get('changes', [])]
        result['diff'] = '\n'.join(''.join(difflib.unified_diff(change['before'].splitlines(True), change['after'].splitlines(True),
                                   fromfile=change['path'], tofile=change['path'])) for change in plan.get('changes', []))
        result['reproduction'] = plan.get('probe') or ''
        result['can_rollback'] = bool(plan.get('commit_started')) and plan['status'] in {'applied', 'failed', 'interrupted'}
        result['executor'] = capabilities()
        public = redact(result)
        public['diff'] = redact_text(result['diff'], limit=None)
        public['reproduction'] = redact_text(result['reproduction'], limit=None)
        return public

    async def start(self, report_id, target, description):
        doctor = self.doctor
        if doctor.host is None:
            raise ValueError('Generated repairs require an online model gateway')
        if doctor.lock.locked() or doctor.tasks:
            raise ValueError('Wait for the current Doctor operation to finish')
        if not isinstance(description, str) or len(description) > 4000:
            raise ValueError('Problem description must be at most 4000 characters')
        report = doctor.get(report_id)
        # Bound the snapshot before invoking the model, without loading plugin code.
        files = snapshot(doctor.plugins, target)
        identifier = 'repair_' + uuid4().hex
        plan = {'id': identifier, 'status': 'generating', 'action': {'kind': 'patch_plugin', 'target': target},
                'report_id': report_id, 'scope': report['scope'], 'created_at': datetime.now(timezone.utc).isoformat(),
                'source_manifest': manifest(files), 'original': encode_snapshot(files), 'journal': []}
        self.save(plan, 'snapshot_created')
        report['repairs'].append(identifier)
        doctor.repository.save(report)

        async def work():
            def progress(phase):
                plan['phase'] = phase
                self.save(plan, phase)
            workspace = RepairWorkspace(files, progress=progress)
            try:
                context = {**report, 'id': identifier, 'user_description': redact(description)}
                result = await asyncio.wait_for(generate_repair(context, doctor.host.model_gateway,
                    doctor.data / 'doctor' / 'sessions' / identifier, workspace), 900)
                # Revalidate model-produced artifacts at the host boundary.
                apply_changes(files, result['changes'])
                plan.update(result)
                plan['status'] = 'planned'
                plan['plan_hash'] = plan_hash(plan)
            except asyncio.CancelledError:
                plan['status'] = 'cancelled'
            except Exception as exc:
                plan['status'] = 'failed'
                plan['error'] = {'message': redact(str(exc)), 'type': type(exc).__name__}
            finally:
                try:
                    self.save(plan, plan['status'])
                finally:
                    doctor.tasks.pop(identifier, None)
        doctor.tasks[identifier] = asyncio.create_task(work())
        return self.public(plan)

    async def cancel(self, identifier):
        plan = self.get(identifier)
        if plan['status'] != 'generating':
            return self.public(plan)
        task = self.doctor.tasks.get(identifier)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        plan = self.repository.get(identifier)
        if plan['status'] == 'generating':
            plan['status'] = 'cancelled'
            self.save(plan, 'cancelled')
        self.doctor.tasks.pop(identifier, None)
        return self.public(plan)

    async def commit(self, identifier, expected_hash, *, rollback=False):
        # A disconnected HTTP client must not strand a partially written plugin.
        task = asyncio.create_task(self._commit(identifier, expected_hash, rollback=rollback))
        self.commits.add(task)
        task.add_done_callback(self.commits.discard)
        return await asyncio.shield(task)

    async def _commit(self, identifier, expected_hash, *, rollback):
        doctor = self.doctor
        async with doctor.lock:
            plan = self.get(identifier)
            if doctor.host is None or doctor.tasks:
                raise ValueError('Wait for Doctor analysis and repair generation to finish')
            if not rollback and (not expected_hash or expected_hash != plan.get('plan_hash') or expected_hash != plan_hash(plan)):
                raise ValueError('Review the current repair diff before applying it')
            if rollback and plan.get('plan_hash') != plan_hash(plan):
                raise ValueError('Stored repair plan is inconsistent')
            if not rollback and plan['status'] == 'applied':
                return self.public(plan)  # Exact-plan retry after a lost HTTP response.
            if (not rollback and plan['status'] != 'planned') or (rollback and not self.public(plan)['can_rollback']):
                raise ValueError('The repair is not in a committable state')
            original = decode_snapshot(plan['original'])
            candidate = apply_changes(original, plan['changes'])
            if manifest(original) != plan['source_manifest']:
                raise ValueError('Stored repair snapshot is inconsistent')
            from cyrene.platform.run_coordinator import run_coordinator_for
            from cyrene.plugins.background import maintenance_lock
            async with maintenance_lock():
                with repair_lock(self.repository.directory), run_coordinator_for(str(doctor.database)).maintenance():
                    memory = doctor.host.service('memory')
                    if memory is not None and memory.project_memory.has_active_learning():
                        raise ValueError('Wait for active memory learning to finish')
                    current = snapshot(doctor.plugins, plan['action']['target'])
                    if rollback:
                        # Resume a crash between file replacements only if every
                        # byte is either the reviewed original or candidate.
                        if set(current) != set(original) or any(raw not in (original[name], candidate[name]) for name, raw in current.items()):
                            raise ValueError('Plugin changed after repair; automatic rollback is unsafe')
                    elif manifest(current) != plan['source_manifest']:
                        raise ValueError('Plugin changed after planning; generate a new repair')
                    plan['status'] = 'rolling_back' if rollback else 'applying'
                    plan['commit_started'] = True
                    self.save(plan, plan['status'])
                    try:
                        await doctor.host._stop_pack(plan['action']['target'])
                        wanted = original if rollback else candidate
                        for change in plan['changes']:
                            name = change['path']
                            # Recheck after asynchronous shutdown and before each write.
                            latest = snapshot(doctor.plugins, plan['action']['target'])
                            if latest != current:
                                raise ValueError('Plugin changed during commit')
                            path = doctor.plugins.resolve() / name
                            target_path(doctor.plugins, plan['action']['target'])
                            self.save(plan, 'write_intent:' + name)
                            atomic_write(path, wanted[name])
                            current[name] = wanted[name]
                            self.save(plan, 'write_complete:' + name)
                        await doctor.host.reload_user_plugins(seed=False)
                        if rollback:
                            if snapshot(doctor.plugins, plan['action']['target']) != original:
                                raise ValueError('Plugin changed during rollback reload')
                            plan['status'] = 'rolled_back'
                            plan['outcome'] = {'status': 'rolled_back'}
                        else:
                            failures = doctor.host.startup_failures
                            target = plan['action']['target']
                            if target in failures or any(Path(f.path).name == target for f in doctor.host.load_failures):
                                raise ValueError('Repaired plugin still fails to load')
                            live = snapshot(doctor.plugins, target)
                            if live != candidate:
                                raise ValueError('Plugin changed during reload; verification is stale')
                            checks = {'syntax': syntax_check(live)}
                            if plan.get('probe'):
                                checks['probe'] = await run_probe(live, plan['probe'])
                            if snapshot(doctor.plugins, target) != live:
                                raise ValueError('Plugin changed during verification')
                            if checks['syntax']['status'] != 'passed' or checks.get('probe', {}).get('status') == 'failed':
                                raise ValueError('Post-commit verification failed')
                            syntax_fixed = plan['baseline']['syntax']['status'] == 'failed'
                            probe_fixed = plan['baseline'].get('probe', {}).get('status') == 'failed' and checks.get('probe', {}).get('status') == 'passed'
                            plan['outcome'] = {'status': 'verified' if syntax_fixed or probe_fixed else 'unverified',
                                               'basis': 'syntax' if syntax_fixed else 'reproduction' if probe_fixed else 'static_only', 'checks': checks}
                            plan['status'] = 'applied'
                        self.save(plan, plan['status'])
                    except Exception as exc:
                        plan['error'] = {'message': redact(str(exc)), 'type': type(exc).__name__}
                        plan['status'] = 'failed'
                        self.save(plan, 'commit_failed')
                        # Never compensate over an intervening user edit.
                        try:
                            latest = snapshot(doctor.plugins, plan['action']['target'])
                            if latest != current:
                                raise ValueError('Concurrent change prevents automatic rollback')
                            for change in plan['changes']:
                                atomic_write(doctor.plugins.resolve() / change['path'], original[change['path']])
                            await doctor.host.reload_user_plugins(seed=False)
                            if snapshot(doctor.plugins, plan['action']['target']) != original:
                                raise ValueError('Plugin changed during recovery reload')
                            plan['status'] = 'rolled_back'
                            plan['outcome'] = {'status': 'rolled_back'}
                            self.save(plan, 'automatic_rollback')
                        except Exception as recovery:
                            plan['outcome'] = {'status': 'partial', 'reason': redact(str(recovery))}
                            self.save(plan, 'recovery_required')
            return self.public(plan)
