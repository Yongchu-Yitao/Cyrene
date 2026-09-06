"""Doctor application service shared by HTTP and offline CLI."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .checks import finding, run_checks
from .evidence import direction, error_code, redact
from .repository import ReportRepository


class DoctorService:
    def __init__(self, *, data: Path, database: Path, plugins: Path, host=None, analyzer=None):
        self.data, self.database, self.plugins = Path(data), Path(database), Path(plugins)
        self.host = host
        self.repository = ReportRepository(self.data / "doctor" / "reports")
        self.tasks = {}
        self.lock = asyncio.Lock()
        self.analyzer = analyzer
        self.ephemeral = {}

    async def diagnose(self, scope=None, *, language="zh", persist=True):
        scope = {key: str(value) for key, value in (scope or {}).items() if key in {"project_id", "chat_id", "job_id", "incident_id", "client_code"} and value}
        if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", value) for value in scope.values()):
            raise ValueError("Invalid diagnostic scope")
        requested_scope = dict(scope)
        findings, settings = await asyncio.to_thread(run_checks, data=self.data, database=self.database, plugins=self.plugins, scope=scope)
        for target, entry in (settings.get("plugin_tool_customizations") or {}).items():
            if isinstance(entry, dict):
                findings.append(finding("tool_customized", "info", "工具存在自定义覆盖；仅凭修改不能判定故障", "Tool has custom overrides; this alone does not prove a fault", {"tool": target, "fields": list(entry)}, [{"kind": "reset_tool", "target": target}]))
        incidents = ReportRepository(self.data / "doctor" / "incidents")
        incident_paths = ([incidents.directory / (scope["incident_id"] + ".json")] if scope.get("incident_id")
                          else sorted(incidents.directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:100])
        for path in incident_paths:
            try:
                value = incidents.get(path.stem)
                if scope.get("incident_id") and value["id"] != scope["incident_id"]:
                    continue
                if any(value.get(k) != v for k, v in requested_scope.items()
                       if k not in {"job_id", "client_code", "incident_id"}
                       and not (k == "project_id" and not value.get(k) and requested_scope.get("chat_id") == value.get("chat_id"))):
                    continue
                findings.append(finding(value["code"], "failed", "近期运行失败，请结合时间确认是否仍有影响", "Recent run failure; check whether it still applies", value))
            except (OSError, ValueError, KeyError):
                continue
        if scope.get("client_code"):
            findings.append(finding(scope["client_code"], "failed", "前端捕获到异常；此分类由客户端提供，尚未确认根因", "Client reported a failure; its category is not a confirmed root cause", {"source": "client", "code": scope["client_code"]}))
        if scope.get("incident_id") and not any(item.get("evidence", {}).get("id") == scope["incident_id"] for item in findings):
            findings.append(finding("incident_missing", "unknown", "故障记录已过期或未能保存；仍可检查当前状态", "Incident expired or could not be saved; current checks remain available"))
        if self.host is not None:
            registry = getattr(self.host, "registry", None)
            if registry is not None:
                for pack in registry.list_packs():
                    if not registry.pack_enabled(pack.id) and (pack.id == "cyrene_memory" or pack.metadata.get("required")):
                        findings.append(finding("required_plugin_disabled", "failed" if pack.metadata.get("required") else "skipped",
                            "相关插件已停用；可在扩展设置中检查是否需要启用", "A relevant Plugin is disabled; inspect its activation in Extensions", {"plugin": pack.id}))
            for failure in self.host.load_failures:
                target = Path(failure.path).name
                findings.append(finding("plugin_load_failed", "failed", "插件加载失败", "Plugin failed to load", {"plugin": target}, [{"kind": "restore_plugin", "target": target}]))
            for target in self.host.startup_failures:
                findings.append(finding("plugin_startup_failed", "failed", "插件启动失败", "Plugin failed to start", {"plugin": target}, [{"kind": "restore_plugin", "target": target}]))
            try:
                from cyrene.plugins.model_catalog import configured_model_candidates
                configured = configured_model_candidates(session_id=scope.get("chat_id", ""))
                findings.append(finding("model_configured" if configured else "model_not_configured", "passed" if configured else "failed",
                    "模型配置已发现；可测试连接" if configured else "没有可用模型配置", "Model configuration found; connection can be tested" if configured else "No model configured"))
            except Exception:
                findings.append(finding("model_configuration_unknown", "unknown", "无法读取模型配置", "Model configuration could not be inspected"))
        else:
            findings.append(finding("runtime_offline", "unknown", "离线诊断：未检查 Agent 和网络连接", "Offline diagnosis: Agent and network were not tested"))
        for index, item in enumerate(findings):
            item["id"] = "e" + str(index + 1)
        report = {"id": "doctor_" + uuid4().hex, "created_at": datetime.now(timezone.utc).isoformat(),
                  "language": "zh" if language == "zh" else "en", "scope": scope,
                  "findings": redact(findings), "analysis": {"status": "idle"}, "repairs": [], "online": self.host is not None}
        if persist:
            try:
                self.repository.save(report)
            except OSError:
                report["persistence_unavailable"] = True
                self.ephemeral[report["id"]] = deepcopy(report)
        return report

    def get(self, identifier):
        if identifier in self.ephemeral:
            return deepcopy(self.ephemeral[identifier])
        report = self.repository.get(identifier)
        if report.get("analysis", {}).get("status") == "running" and identifier not in self.tasks:
            report["analysis"] = {"status": "unavailable", "code": "process_restarted", "direction": direction("process_restarted")}
            self.repository.save(report)
        return report

    async def start_analysis(self, identifier, *, description=None):
        report = self.get(identifier)
        if identifier in self.tasks:
            return report
        if self.lock.locked() or self.tasks:
            raise ValueError("Wait for the current diagnostic operation to finish")
        if description is not None:
            if not isinstance(description, str) or len(description) > 4000:
                raise ValueError("Problem description must be at most 4000 characters")
            report["user_description"] = redact(description.strip())
        report["analysis"] = {"status": "running"}
        self.repository.save(report)

        async def recovering(code, attempt):
            latest = self.get(identifier)
            latest["analysis"] = {"status": "running", "phase": "retrying", "retry_count": attempt, "last_error_code": code}
            self.repository.save(latest)

        async def work():
            from .recovery import ANALYSIS_TIMEOUT
            try:
                if self.analyzer is None:
                    if self.host is None:
                        raise RuntimeError("Agent is unavailable")
                    from .agent_analysis import analyze
                    value = await asyncio.wait_for(analyze(report, self.host.model_gateway, self.data / "doctor" / "analysis" / identifier, on_retry=recovering), ANALYSIS_TIMEOUT)
                else:
                    value = await asyncio.wait_for(self.analyzer(report), ANALYSIS_TIMEOUT)
                analysis = {"status": "completed", "retry_count": self.get(identifier)["analysis"].get("retry_count", 0), **redact(value)}
            except asyncio.CancelledError:
                analysis = {"status": "cancelled"}
            except Exception as exc:
                code = "model_timeout" if isinstance(exc, TimeoutError) else error_code(exc)
                analysis = {"status": "unavailable", "code": code, "direction": direction(code)}
            latest = self.get(identifier)
            latest["analysis"] = analysis
            self.repository.save(latest)
            self.tasks.pop(identifier, None)

        self.tasks[identifier] = asyncio.create_task(work())
        return report

    async def cancel_analysis(self, identifier):
        task = self.tasks.get(identifier)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                report = self.repository.get(identifier)
                report["analysis"] = {"status": "cancelled"}
                self.repository.save(report)
            finally:
                self.tasks.pop(identifier, None)
        return self.get(identifier)

    async def close(self):
        for task in list(self.tasks.values()):
            task.cancel()
        await asyncio.gather(*list(self.tasks.values()), return_exceptions=True)

    async def probe_model(self, identifier):
        if self.tasks:
            raise ValueError("Wait for analysis to finish before probing the model")
        async with self.lock:
            return await self._probe_model(identifier)

    async def _probe_model(self, identifier):
        report = self.get(identifier)
        try:
            if self.host is None:
                raise RuntimeError("Model gateway unavailable")
            response = await asyncio.wait_for(self.host.model_gateway.complete(
                [{"role": "user", "content": "Reply OK."}], max_tokens=16, caller="doctor_probe", session_id=report["scope"].get("chat_id", "")), 15)
            if not isinstance(response, dict) or not response:
                raise ValueError("Empty model response")
            result = {"status": "passed"}
        except Exception as exc:
            code = "model_timeout" if isinstance(exc, TimeoutError) else error_code(exc)
            result = {"status": "failed", "code": code, "direction": direction(code)}
        report = self.get(identifier)
        report["model_probe"] = result
        return self.repository.save(report)

    async def plan_repair(self, identifier, finding_id, action_index=0):
        report = self.get(identifier)
        item = next((f for f in report["findings"] if f["id"] == finding_id), None)
        if item is None or action_index < 0 or action_index >= len(item["actions"]):
            raise ValueError("Unknown repair action")
        action = item["actions"][action_index]
        plan = {"id": "repair_" + uuid4().hex, "status": "planned", "action": action,
                "report_id": identifier, "scope": report["scope"]}
        if action["kind"] == "restore_plugin":
            from cyrene.plugins.plugin_restore import plan_builtin_plugin_restore
            value = plan_builtin_plugin_restore(self.plugins, action["target"])
            plan["plugin_plan"] = {**asdict(value), "directory": str(value.directory)}
        elif action["kind"] == "reset_tool":
            if self.host is None:
                raise ValueError("Configuration repair requires the online service")
            from cyrene.platform import settings_store
            values = settings_store.get("plugin_tool_customizations", {})
            if action["target"] not in values:
                raise ValueError("Tool override has changed")
            plan["revision"] = settings_store.get_revision()
            plan["before"] = values[action["target"]]
        elif action["kind"] == "retry_memory":
            if self.host is None or self.host.service("memory") is None:
                raise ValueError("Memory Plugin is unavailable")
        else:
            raise ValueError("Unsupported action")
        self.repository.save(plan)
        report["repairs"].append(plan["id"])
        self.repository.save(report)
        return self.public_plan(plan)

    @staticmethod
    def public_plan(plan):
        return {key: redact(value) for key, value in plan.items() if key not in {"before", "plugin_plan", "rollback_plan"}} | {
            "files": [plan["action"]["target"] if value == "." else value for value in (plan.get("plugin_plan") or {}).get("replaced_files", [])],
            "bundled_files": list((plan.get("plugin_plan") or {}).get("bundled_files", [])),
            "can_rollback": bool(plan.get("rollback_plan") or plan.get("after_revision") is not None),
        }

    async def apply_repair(self, plan_id):
        async with self.lock:
            plan = self.get(plan_id)
            if plan.get("status") != "planned":
                raise ValueError("Repair plan is already applied or invalid")
            if self.host is None:
                raise ValueError("Repairs require the online service")
            if self.tasks:
                raise ValueError("Wait for diagnostic analysis to finish")
            from cyrene.platform.run_coordinator import run_coordinator_for
            from cyrene.plugins.background import maintenance_lock
            async with maintenance_lock():
                with run_coordinator_for(str(self.database)).maintenance():
                    memory = self.host.service("memory")
                    if memory is not None and memory.project_memory.has_active_learning():
                        raise ValueError("Wait for active memory learning to finish")
                    plan["status"] = "applying"
                    self.repository.save(plan)
                    try:
                        await self._apply(plan)
                        plan["status"] = "applied"
                    except Exception as exc:
                        plan["status"] = "failed"
                        plan["error"] = {"code": error_code(exc), "type": type(exc).__name__, "message": redact(str(exc))}
                    self.repository.save(plan)
            report = self.get(plan["report_id"])
            check = await self.diagnose(report["scope"], language=report["language"])
            plan["verification_report_id"] = check["id"]
            self.repository.save(plan)
            return self.public_plan(plan)

    async def _apply(self, plan):
        kind, target = plan["action"]["kind"], plan["action"]["target"]
        if kind == "restore_plugin":
            from cyrene.plugins.plugin_restore import BuiltinPluginRestorePlan, apply_builtin_plugin_restore, plan_builtin_plugin_restore
            value = dict(plan["plugin_plan"])
            value["directory"] = Path(value["directory"])
            # Do not stop the active generation for a stale file plan.
            if plan_builtin_plugin_restore(self.plugins, target).fingerprint != value["fingerprint"]:
                raise ValueError("Plugin restore plan is stale")
            await self.host._stop_pack(target)
            try:
                result = apply_builtin_plugin_restore(BuiltinPluginRestorePlan(**value))
                plan["backup_directory"] = str(result.backup_directory)
                plan["rollback_plan"] = {**asdict(plan_builtin_plugin_restore(self.plugins, target)), "directory": str(self.plugins)}
                self.repository.save(plan)
            finally:
                await self.host.reload_user_plugins(seed=False)
            plan["rollback_plan"] = {**asdict(plan_builtin_plugin_restore(self.plugins, target)), "directory": str(self.plugins)}
            if target in self.host.startup_failures or any(Path(f.path).name == target for f in self.host.load_failures):
                raise RuntimeError("Plugin restoration did not resolve loading; restart or inspect dependencies")
        elif kind == "reset_tool":
            from cyrene.platform import settings_store
            values = dict(settings_store.get("plugin_tool_customizations", {}))
            if values.get(target) != plan["before"]:
                raise ValueError("Tool override changed after planning")
            values.pop(target)
            revision, _ = settings_store.update_atomic({"plugin_tool_customizations": values}, expected_revision=plan["revision"])
            plan["after_revision"] = revision
            self.repository.save(plan)
            await self.host.reload_user_plugins(seed=False)
        elif kind == "retry_memory":
            service = self.host.service("memory")
            if service is None:
                raise ValueError("Memory Plugin is unavailable")
            result = await asyncio.wait_for(service.project_memory.retry_job(plan["scope"]["project_id"], target), 60)
            plan["job_status"] = result.get("status")
            if result.get("status") not in {"saved", "unchanged"}:
                raise RuntimeError("Learning still failed: " + str(result.get("errorType") or "internal_error"))

    async def rollback_repair(self, identifier):
        async with self.lock:
            plan = self.get(identifier)
            if plan["status"] not in {"applied", "failed"} or self.host is None:
                raise ValueError("This repair cannot be rolled back")
            if self.tasks:
                raise ValueError("Wait for analysis to complete")
            from cyrene.platform.run_coordinator import run_coordinator_for
            from cyrene.plugins.background import maintenance_lock
            async with maintenance_lock():
                with run_coordinator_for(str(self.database)).maintenance():
                    memory = self.host.service("memory")
                    if memory is not None and memory.project_memory.has_active_learning():
                        raise ValueError("Wait for active memory learning to finish")
                    if plan["action"]["kind"] == "restore_plugin" and plan.get("rollback_plan"):
                        from cyrene.plugins.plugin_restore import BuiltinPluginRestorePlan, rollback_builtin_plugin_restore
                        value = dict(plan["rollback_plan"])
                        value["directory"] = Path(value["directory"])
                        await self.host._stop_pack(plan["action"]["target"])
                        try:
                            rollback_builtin_plugin_restore(BuiltinPluginRestorePlan(**value), Path(plan["backup_directory"]))
                        finally:
                            await self.host.reload_user_plugins(seed=False)
                    elif plan["action"]["kind"] == "reset_tool" and plan.get("after_revision") is not None:
                        from cyrene.platform import settings_store
                        values = dict(settings_store.get("plugin_tool_customizations", {}))
                        values[plan["action"]["target"]] = plan["before"]
                        settings_store.update_atomic({"plugin_tool_customizations": values}, expected_revision=plan["after_revision"])
                        await self.host.reload_user_plugins(seed=False)
                    else:
                        raise ValueError("This operation has no automatic rollback")
            plan["status"] = "rolled_back"
            self.repository.save(plan)
            report = self.get(plan["report_id"])
            verification = await self.diagnose(report["scope"], language=report["language"])
            plan["verification_report_id"] = verification["id"]
            self.repository.save(plan)
            return self.public_plan(plan)
