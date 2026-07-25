"""Behavior-learning and evolution routes."""

# ruff: noqa: F403,F405

from cyrene.workbench_runtime import *


def register_learning_routes(router: APIRouter, bot: Any, db_path: str) -> None:
    global _bot, _db_path
    _bot = bot
    _db_path = db_path

    # ---- Evolution API ----

    def _learning_project_id(project: str = "") -> str:
        raw = str(project or "").strip()
        if not raw:
            return ""
        # Workbench project ids are canonical and already encode the storage
        # identity.  Do not reread and normalize the multi-megabyte project
        # document for the common case.
        if re.fullmatch(r"project_[A-Za-z0-9]+", raw):
            return raw
        try:
            store = _read_workbench_store()
            for item in store.get("projects") or []:
                if str(item.get("id") or "") == raw or _workbench_project_data_key(item) == raw:
                    return str(item.get("id") or raw)
        except Exception:
            pass
        return raw

    def _learning_project_ids(project: str = "") -> list[str]:
        """Return all project id values that should match this project.

        Returns [resolved_uuid, original_raw] so queries can match both
        new-style (UUID) and legacy (dataKey / empty) project_id values.
        """
        raw = str(project or "").strip()
        if not raw:
            return []
        resolved = _learning_project_id(raw)
        ids = [resolved] if resolved else []
        if raw and raw != resolved:
            ids.append(raw)
        return ids

    _LEARNING_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
    _LEARNING_FILE_PATH_RE = re.compile(
        r"(?P<path>(?:/[^\s\"'<>]+|[A-Za-z]:\\[^\s\"'<>]+)\.(?:png|jpg|jpeg|webp|gif|pdf|md|txt|json|csv|tsv|xlsx|docx|pptx|py|js|jsx|ts|tsx|css|html))",
        re.IGNORECASE,
    )

    def _learning_extract_paths(value: Any) -> list[str]:
        paths: list[str] = []
        if isinstance(value, dict):
            for item in value.values():
                paths.extend(_learning_extract_paths(item))
            return paths
        if isinstance(value, list):
            for item in value:
                paths.extend(_learning_extract_paths(item))
            return paths
        text = str(value or "")
        for match in _LEARNING_FILE_PATH_RE.finditer(text):
            path = match.group("path").rstrip(".,);]")
            if path and path not in paths:
                paths.append(path)
        return paths

    def _learning_enrich_tool_chains(chains: list[dict[str, Any]]) -> list[dict[str, Any]]:
        enriched: list[dict[str, Any]] = []
        for chain in chains:
            if not isinstance(chain, dict):
                continue
            summary = chain.get("summary") if isinstance(chain.get("summary"), dict) else {}
            if int(summary.get("total_steps") or 0) <= 0:
                continue
            screenshots: list[dict[str, Any]] = []
            files: list[dict[str, Any]] = []
            seen_paths: set[str] = set()
            for step in chain.get("chain") or []:
                if not isinstance(step, dict):
                    continue
                tool_name = str(step.get("tool") or "")
                candidates: list[str] = []
                candidates.extend(_learning_extract_paths(step.get("args") or {}))
                candidates.extend(_learning_extract_paths(step.get("input_summary") or ""))
                candidates.extend(_learning_extract_paths(step.get("output_summary") or ""))
                for raw_path in candidates:
                    if raw_path in seen_paths:
                        continue
                    seen_paths.add(raw_path)
                    target = Path(raw_path).expanduser()
                    suffix = target.suffix.lower()
                    item = {
                        "path": raw_path,
                        "name": target.name or raw_path,
                        "tool": tool_name,
                    }
                    if suffix in _LEARNING_IMAGE_EXTS and target.exists() and target.is_file():
                        screenshots.append({
                            **item,
                            "url": "/api/tool-chain-media?path=" + quote(str(target.resolve())),
                        })
                    else:
                        files.append(item)
            chain = dict(chain)
            chain["screenshots"] = screenshots
            chain["files"] = files
            enriched.append(chain)
        return enriched

    async def _learning_is_known_media_path(target: Path) -> bool:
        try:
            from cyrene import pattern as _pattern

            chains = await _pattern.list_tool_chains("", 500)
        except Exception:
            return False
        for chain in chains:
            for step in chain.get("chain") or []:
                if not isinstance(step, dict):
                    continue
                candidates: list[str] = []
                candidates.extend(_learning_extract_paths(step.get("args") or {}))
                candidates.extend(_learning_extract_paths(step.get("input_summary") or ""))
                candidates.extend(_learning_extract_paths(step.get("output_summary") or ""))
                for raw_path in candidates:
                    try:
                        if Path(raw_path).expanduser().resolve() == target:
                            return True
                    except Exception:
                        continue
        return False

    @router.get("/api/tool-chain-media")
    async def api_tool_chain_media(path: str = ""):
        target = Path(str(path or "")).expanduser().resolve()
        if target.suffix.lower() not in _LEARNING_IMAGE_EXTS:
            return JSONResponse({"error": "unsupported media type"}, status_code=400)
        if not target.exists() or not target.is_file():
            return JSONResponse({"error": "media not found"}, status_code=404)
        if not await _learning_is_known_media_path(target):
            return JSONResponse({"error": "media not found"}, status_code=404)
        media_type = mimetypes.guess_type(str(target))[0] or "image/png"
        return FileResponse(target, media_type=media_type)

    @router.get("/api/evolution")
    async def api_evolution(project: str = "", compact: bool = False):
        """Aggregated data for the Evolution page."""
        from cyrene import pattern as _pattern
        project_id = _learning_project_id(project)
        raw_project = str(project or "").strip()
        project_ids = [project_id] if project_id else []
        if raw_project and raw_project != project_id:
            project_ids.append(raw_project)
        status, learned_skills, tool_chains, candidates = await asyncio.gather(
            _build_status(),
            _pattern.list_learned_skills(project_id),
            _pattern.list_tool_chains(project_ids),
            _pattern.list_skill_candidates(project_id),
        )
        return {
            "phase": status.get("phase", ""),
            "state": status.get("state", ""),
            "learned_skills": learned_skills,
            "skill_candidates": candidates,
            "tool_chains": _learning_enrich_tool_chains(tool_chains),
            # Claude Code transcript analysis is intentionally not part of the
            # aggregate learning payload.  It is an expensive, unrelated
            # operation and must not delay the skill-learning workbench.
            "cc_learning": None,
        }

    @router.get("/api/learned-skills")
    async def api_learned_skills(project: str = ""):
        from cyrene import pattern as _pattern
        return {"skills": await _pattern.list_learned_skills(_learning_project_id(project))}

    @router.get("/api/tool-chains")
    async def api_tool_chains(project: str = "", limit: int = 80):
        from cyrene import pattern as _pattern
        return {"tool_chains": _learning_enrich_tool_chains(await _pattern.list_tool_chains(_learning_project_ids(project), limit))}

    @router.get("/api/skill-candidates")
    async def api_skill_candidates(project: str = "", status: str = "all"):
        from cyrene import pattern as _pattern
        return {"candidates": await _pattern.list_skill_candidates(_learning_project_id(project), status)}

    @router.post("/api/skill-candidates/{candidate_id}/decision")
    async def api_skill_candidate_decision(candidate_id: str, request: Request):
        from cyrene import pattern as _pattern
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        result = await _pattern.decide_skill_candidate(candidate_id, str((payload or {}).get("decision") or ""))
        if not result.get("ok"):
            return JSONResponse(result, status_code=400)
        return result

    @router.get("/api/learned-skills/{skill_id}")
    async def api_learned_skill_detail(skill_id: str):
        from cyrene import pattern as _pattern
        skill = await _pattern.get_learned_skill(skill_id)
        if skill is None:
            return JSONResponse({"error": "skill not found"}, status_code=404)
        return {"skill": skill}

    @router.get("/api/learned-skills/{skill_id}/versions")
    async def api_learned_skill_versions(skill_id: str):
        from cyrene import pattern as _pattern
        return {"versions": await _pattern.list_learned_skill_versions(skill_id)}

    @router.get("/api/learned-skills/{skill_id}/patches")
    async def api_learned_skill_patches(skill_id: str, status: str = "all"):
        from cyrene import pattern as _pattern
        return {"patches": await _pattern.list_learned_skill_patches(skill_id, status)}

    @router.get("/api/learned-skills/{skill_id}/runs")
    async def api_learned_skill_runs(skill_id: str, limit: int = 50):
        from cyrene import pattern as _pattern
        return {"runs": await _pattern.list_learned_skill_runs(skill_id, limit)}

    @router.post("/api/learned-skills/{skill_id}/update")
    async def api_update_learned_skill(skill_id: str, request: Request):
        from cyrene import pattern as _pattern

        payload = await request.json()
        updates = payload.get("updates") if isinstance(payload, dict) else None
        reason = str((payload or {}).get("reason") or "Manual skill edit.")
        result = await _pattern.update_learned_skill(skill_id, updates if isinstance(updates, dict) else {}, reason=reason)
        if result is None:
            return JSONResponse({"error": "skill not found or invalid payload"}, status_code=404)
        return {"ok": True, "skill": result}

    @router.post("/api/learned-skills/{skill_id}/rollback")
    async def api_rollback_learned_skill(skill_id: str, request: Request):
        from cyrene import pattern as _pattern

        payload = await request.json()
        version = int((payload or {}).get("version") or 0)
        result = await _pattern.rollback_learned_skill(skill_id, version)
        if not result.get("ok"):
            return JSONResponse(result, status_code=404)
        return result

    @router.post("/api/learned-skills/{skill_id}/patches/{patch_id}/apply")
    async def api_apply_learned_skill_patch(skill_id: str, patch_id: str):
        from cyrene import pattern as _pattern
        result = await _pattern.apply_skill_patch(skill_id, patch_id)
        if not result.get("ok"):
            return JSONResponse(result, status_code=400)
        return result

    @router.post("/api/learned-skills/{skill_id}/patches/{patch_id}/reject")
    async def api_reject_learned_skill_patch(skill_id: str, patch_id: str):
        from cyrene import pattern as _pattern
        ok = await _pattern.reject_skill_patch(skill_id, patch_id)
        if not ok:
            return JSONResponse({"error": "patch not found"}, status_code=404)
        return {"ok": True}

    @router.post("/api/learned-skills/{skill_id}/activate")
    async def api_activate_learned_skill(skill_id: str):
        from cyrene import pattern as _pattern
        ok = await _pattern.activate_learned_skill(skill_id)
        return {"ok": ok}

    @router.post("/api/learned-skills/{skill_id}/deprecate")
    async def api_deprecate_learned_skill(skill_id: str):
        from cyrene import pattern as _pattern
        ok = await _pattern.deprecate_learned_skill(skill_id)
        return {"ok": ok}

    @router.post("/api/learned-skills/{skill_id}/delete")
    async def api_delete_learned_skill(skill_id: str):
        from cyrene import pattern as _pattern
        ok = await _pattern.delete_learned_skill(skill_id)
        return {"ok": ok}

    @router.post("/api/learned-skills/{skill_id}/run")
    async def api_run_learned_skill(skill_id: str):
        from cyrene import pattern as _pattern
        result = await _pattern.run_learned_skill(skill_id)
        ok = not str(result).startswith("Learned skill")
        return {"ok": ok, "result": result}

    @router.post("/api/learning/process")
    async def api_learning_process(project: str = "", turn_id: str = ""):
        from cyrene import pattern as _pattern

        tid = turn_id.strip() if turn_id else ""
        if tid:
            stats = await _pattern.learn_from_turn(tid)
            # Use the turn's actual project for the response so the UI sees the new skill.
            from cyrene import behavior_learning as _bl
            scope = await _bl._project_scope_for_turn(tid)
            project_id = scope["project_id"]
        else:
            project_id = _learning_project_id(project)
            stats = await _pattern.scan_for_manual_learn(project_id)
        project_ids = _learning_project_ids(project_id or project)
        return {
            "ok": True,
            "stats": stats,
            "learned_skills": await _pattern.list_learned_skills(project_id),
            "skill_candidates": await _pattern.list_skill_candidates(project_id),
            "tool_chains": _learning_enrich_tool_chains(await _pattern.list_tool_chains(project_ids)),
        }

    @router.post("/api/learning/rebuild")
    async def api_learning_rebuild(project: str = ""):
        from cyrene import pattern as _pattern

        project_id = _learning_project_id(project)
        project_ids = _learning_project_ids(project)
        result = await _pattern.rebuild_learning_state(reprocess_all_turns=True, project_id=project_id)
        return {
            "ok": True,
            "result": result,
            "learned_skills": await _pattern.list_learned_skills(project_id),
            "skill_candidates": await _pattern.list_skill_candidates(project_id),
            "tool_chains": _learning_enrich_tool_chains(await _pattern.list_tool_chains(project_ids)),
        }
