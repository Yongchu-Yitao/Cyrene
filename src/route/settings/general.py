"""Onboarding, context, settings, budget, and MCP routes."""

# ruff: noqa: F403,F405

from cyrene.workbench.runtime import *


def register_settings_routes(router: APIRouter, bot: Any, db_path: str) -> None:
    global _bot, _db_path
    _bot = bot
    _db_path = db_path

    async def _publish_settings_changed(
        namespace: str,
        revision: int | None,
        changed: list[str],
    ) -> None:
        from cyrene.observability import debug

        if {"external_agent_proxy_enabled", "external_agent_proxy_port"}.intersection(changed):
            # ACP transports are process-scoped and retain their spawn env.
            # Recycle them after a proxy change so the next turn applies the
            # new setting without requiring a full Cyrene restart.
            from cyrene.agent_runtime.process_manager import get_process_manager

            await get_process_manager().close_all()
        await debug.publish_event({
            "type": "settings_changed",
            "namespace": namespace,
            "revision": revision,
            "changed": list(changed),
        })

    # ---- Settings API ----

    @router.get("/api/onboarding")
    async def api_get_onboarding():
        return {"onboarding": get_onboarding_status()}

    @router.post("/api/onboarding/llm")
    async def api_onboarding_llm(request: Request):
        body = await request.json()
        try:
            return await save_and_test_llm_setup(
                str(body.get("api_key") or ""),
                str(body.get("base_url") or ""),
                str(body.get("model") or ""),
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except httpx.TimeoutException as exc:
            return JSONResponse(
                {"error": "upstream model timed out", "detail": str(exc)},
                status_code=504,
            )
        except httpx.HTTPError as exc:
            return JSONResponse(
                {"error": "upstream model request failed", "detail": format_httpx_error(exc)},
                status_code=502,
            )

    @router.post("/api/onboarding/openai-oauth")
    async def api_onboarding_openai_oauth(request: Request):
        body = await request.json()
        try:
            return await save_codex_oauth_setup(
                str(body.get("model") or ""),
                str(body.get("reasoning_effort") or ""),
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except (RuntimeError, OSError, TimeoutError) as exc:
            return JSONResponse(
                {"error": "Codex model validation failed", "detail": str(exc)},
                status_code=503,
            )

    @router.post("/api/onboarding/personality")
    async def api_onboarding_personality(request: Request):
        body = await request.json()
        try:
            return await save_personality_setup(
                str(body.get("mode") or ""),
                name=str(body.get("name") or ""),
                content=str(body.get("content") or ""),
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except httpx.TimeoutException as exc:
            return JSONResponse(
                {"error": "upstream model timed out", "detail": str(exc)},
                status_code=504,
            )
        except httpx.HTTPError as exc:
            return JSONResponse(
                {"error": "upstream model request failed", "detail": format_httpx_error(exc)},
                status_code=502,
            )

    # ---- Context management (SOUL.md / workspace chips) ----

    @router.get("/api/context/state")
    async def api_context_state():
        from cyrene.runtime.settings_store import is_workspace_active, is_soul_active, get_workspace_history

        def _load_context_state():
            # Context chips need persisted project metadata only. The full store
            # reader also performs artifact repair and walks every project
            # workspace once per task session, which can block all API requests
            # for seconds. Keep both SQLite/config I/O and metadata decoding off
            # the event loop.
            workspace_dir = str(WORKSPACE_DIR)
            try:
                store = _read_workbench_store_lightweight()
                active_id = str(store.get("activeProjectId") or "").strip()
                if active_id:
                    project = _workbench_find_project(store, active_id)
                    if project:
                        project_ws = str(project.get("workspacePath") or "").strip()
                        if project_ws:
                            workspace_dir = project_ws
            except Exception:
                pass
            return {
                "soul_active": is_soul_active(),
                "workspace_active": is_workspace_active(),
                "workspace_dir": workspace_dir,
                "workspace_history": get_workspace_history(),
            }

        return await asyncio.to_thread(_load_context_state)

    @router.post("/api/context/remove-soul")
    async def api_remove_soul():
        from cyrene.runtime.settings_store import set_soul_active
        set_soul_active(False)
        return {"ok": True}

    @router.post("/api/context/add-soul")
    async def api_add_soul():
        from cyrene.runtime.settings_store import set_soul_active
        set_soul_active(True)
        return {"ok": True}

    @router.post("/api/context/remove-workspace")
    async def api_remove_workspace():
        from cyrene.runtime.settings_store import set_workspace_active
        await asyncio.to_thread(set_workspace_active, False)
        return {"ok": True}

    @router.post("/api/context/add-workspace")
    async def api_add_workspace(request: Request):
        from cyrene.runtime.settings_store import activate_workspace
        body = await request.json()
        path = str(body.get("path", "")).strip()
        await asyncio.to_thread(activate_workspace, path)
        return {"ok": True}

    @router.post("/api/context/pick-directory")
    async def api_pick_directory():
        import platform
        import subprocess
        system = platform.system()
        if system == "Darwin":
            try:
                result = await asyncio.to_thread(
                    subprocess.run,
                    ['osascript', '-e', 'POSIX path of (choose folder with prompt "Select workspace directory")'],
                    capture_output=True, text=True, timeout=30,
                )
            except subprocess.TimeoutExpired:
                return {"path": "", "error": "Directory picker timed out"}
            path = result.stdout.strip()
            if path:
                return {"path": path}
            return {"path": "", "cancelled": True}
        return {"path": "", "error": f"Directory picker not supported on {system}"}

    @router.get("/api/settings/soul")
    async def api_get_soul():
        return {"content": _read_soul()}

    @router.put("/api/settings/soul")
    async def api_update_soul(request: Request):
        body = await request.json()
        SOUL_PATH.write_text(body.get("content", ""), encoding="utf-8")
        return {"ok": True}

    @router.get("/api/settings/keys")
    async def api_get_keys():
        from cyrene.config import get_env_keys_meta
        return {"keys": get_env_keys_meta()}

    @router.put("/api/settings/keys")
    async def api_update_keys(request: Request):
        from cyrene.config import write_env_keys, _EDITABLE_KEYS
        body = await request.json()
        updates = {}
        for key, meta in _EDITABLE_KEYS.items():
            value = body.get(key, "")
            if not value:
                continue
            # 跳过未修改的 masked 值（全为 • 或太短）
            if meta["masked"] and (value.startswith("••") or len(value) <= 8):
                continue
            updates[key] = value
        if not updates:
            return JSONResponse({"error": "no valid keys provided"}, status_code=400)
        write_env_keys(updates)
        return {"ok": True, "updated": list(updates.keys())}

    async def _codex_oauth_snapshot(
        *,
        include_limits: bool = True,
        include_models: bool = True,
        stale_limits: bool = False,
    ) -> dict[str, Any]:
        from cyrene.model_runtime.codex_provider import get_codex_provider
        from cyrene.runtime.settings_store import get as get_setting

        snapshot = await get_codex_provider().snapshot(
            include_limits=include_limits,
            include_models=include_models,
            stale_limits=stale_limits,
        )
        snapshot["quota_enabled"] = bool(
            get_setting("codex_budget_enabled", True)
        )
        return snapshot

    @router.get("/api/settings/openai-oauth")
    async def api_get_openai_oauth():
        try:
            # Login state + model choices are the interactive path. Quota has a
            # separate endpoint/panel and must not delay showing "connected".
            return await _codex_oauth_snapshot(include_limits=False)
        except (RuntimeError, OSError, TimeoutError) as exc:
            return {
                "available": False, "connected": False, "models": [],
                "limits": {}, "quota_enabled": True, "error": str(exc),
            }

    @router.post("/api/settings/openai-oauth/login")
    async def api_start_openai_oauth_login():
        from cyrene.model_runtime.codex_provider import get_codex_provider
        from cyrene.runtime.settings_store import set_ as set_setting

        set_setting("codex_budget_enabled", True)
        try:
            return await get_codex_provider().start_login()
        except (RuntimeError, OSError, TimeoutError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)

    @router.post("/api/settings/openai-oauth/logout")
    async def api_openai_oauth_logout():
        from cyrene.model_runtime.codex_provider import get_codex_provider

        try:
            await get_codex_provider().logout()
        except (RuntimeError, OSError, TimeoutError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)
        return {"ok": True}

    @router.get("/api/settings/openai-oauth/cli")
    async def api_get_codex_cli_status():
        from cyrene.model_runtime import codex_cli

        return codex_cli.status()

    @router.post("/api/settings/openai-oauth/cli/download")
    async def api_start_codex_cli_download(request: Request):
        """Start the Codex CLI download.

        Contract: the response mirrors codex_cli.status(). A JSON body with
        ``force=true`` (sent by the settings UI when the snapshot reports
        ``cli.broken``) is the reinstall path for a broken-but-installed
        runtime: the current install is wiped and the SDK-pinned version —
        the one known to speak the SDK's protocol — is downloaded.
        """
        from cyrene.model_runtime import codex_cli

        try:
            body = await request.json()
        except ValueError:
            body = {}
        try:
            return codex_cli.start_download(force=bool(body.get("force")))
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)

    @router.get("/api/settings/openai-oauth/limits")
    async def api_get_openai_oauth_limits():
        try:
            # This surface only needs account + quota data. Reuse the latest
            # snapshot immediately and refresh old limits in the background;
            # model discovery belongs to the separate model-settings endpoint.
            snapshot = await _codex_oauth_snapshot(
                include_models=False,
                stale_limits=True,
            )
            return {
                "available": snapshot.get("available", True),
                "connected": snapshot.get("connected", False),
                "account": snapshot.get("account"),
                "limits": snapshot.get("limits") or {},
                "quota_enabled": snapshot.get("quota_enabled", True),
            }
        except (RuntimeError, OSError, TimeoutError) as exc:
            return {
                "available": False, "connected": False, "limits": {},
                "quota_enabled": True, "error": str(exc),
            }

    @router.get("/api/settings/models")
    async def api_get_models():
        from cyrene.runtime.settings_store import (
            get_codex_model,
            get_custom_models,
            get_model_source,
            get_models,
            get_secondary_model,
            get_vision_models,
        )
        from cyrene.config import OPENAI_API_KEY, DEFAULT_OPENAI_BASE_URL, read_env_file
        from cyrene.model_runtime.pricing import price_hint as _price_hint

        def _normalize_candidates(raw_items: list[dict[str, Any]] | None, fallback_api_key: str, fallback_base_url: str) -> list[dict[str, Any]]:
            from cyrene.model_runtime.codex_provider import CODEX_BASE_URL, CODEX_PROVIDER

            normalized_items: list[dict[str, Any]] = []
            for index, model in enumerate(raw_items or []):
                model_identifier = str(
                    model.get("model")
                    or model.get("name")
                    or model.get("id")
                    or ""
                ).strip()
                if not model_identifier:
                    continue
                provider = str(model.get("provider") or "openai_compatible").strip()
                model_base_url = (
                    CODEX_BASE_URL
                    if provider == CODEX_PROVIDER
                    else str(model.get("base_url") or fallback_base_url or DEFAULT_OPENAI_BASE_URL).strip() or DEFAULT_OPENAI_BASE_URL
                )
                raw_model_api_key = strip_wrapping_quotes(str(model.get("api_key") or "").strip())
                if provider == CODEX_PROVIDER:
                    model_api_key = ""
                elif raw_model_api_key:
                    model_api_key = raw_model_api_key
                elif model_base_url.rstrip("/") == (fallback_base_url or DEFAULT_OPENAI_BASE_URL).rstrip("/"):
                    model_api_key = fallback_api_key
                else:
                    model_api_key = ""
                user_price = str(model.get("price") or "").strip()
                is_deepseek = "deepseek" in model_identifier.lower()
                normalized_items.append(
                    {
                        "id": str(model.get("id") or f"candidate-{index + 1}").strip() or f"candidate-{index + 1}",
                        "name": str(model.get("name") or model_identifier).strip() or model_identifier,
                        "model": model_identifier,
                        "provider": provider,
                        "reasoning_effort": str(model.get("reasoning_effort") or "").strip().lower(),
                        "supported_reasoning_efforts": ["high", "max"] if is_deepseek else [],
                        "default_reasoning_effort": "high" if is_deepseek else "",
                        "desc": str(model.get("desc") or "").strip(),
                        "ctx": str(model.get("ctx") or "").strip(),
                        "price": user_price,
                        "priceHint": _price_hint(model_identifier) if not user_price else "",
                        "api_key": model_api_key,
                        "base_url": model_base_url,
                        "vision_capable": model.get("vision_capable") is True,
                        "vision_checked_at": str(model.get("vision_checked_at") or ""),
                        "vision_check_error": str(model.get("vision_check_error") or ""),
                    }
                )
            return normalized_items

        raw_models = get_models()
        raw_custom_models = get_custom_models()
        raw_codex_model = get_codex_model()
        model_source = get_model_source()
        raw_vision_models = get_vision_models()
        raw_secondary = get_secondary_model()
        active_model_name, base_url = _live_llm_config()
        env_keys = read_env_file()
        active_api_key = strip_wrapping_quotes(str(env_keys.get("OPENAI_API_KEY") or OPENAI_API_KEY or "").strip())
        normalized = _normalize_candidates(raw_models, active_api_key, base_url)
        normalized_custom = _normalize_candidates(
            raw_custom_models, active_api_key, base_url
        )
        normalized_codex_items = _normalize_candidates(
            [raw_codex_model] if raw_codex_model else [],
            active_api_key,
            base_url,
        )
        normalized_codex = (
            normalized_codex_items[0] if normalized_codex_items else None
        )
        normalized_vision = _normalize_candidates(raw_vision_models, active_api_key, base_url)

        # Normalize secondary model (single item)
        sec_model = str(raw_secondary.get("model") or "").strip()
        ctx_limit = int(raw_secondary.get("ctx_limit") or 0)
        max_concurrency = int(raw_secondary.get("max_concurrency") or 0)
        if sec_model:
            sec_base_url = str(raw_secondary.get("base_url") or base_url or DEFAULT_OPENAI_BASE_URL).strip() or DEFAULT_OPENAI_BASE_URL
            sec_raw_api_key = strip_wrapping_quotes(str(raw_secondary.get("api_key") or "").strip())
            if sec_raw_api_key:
                sec_api_key = sec_raw_api_key
            elif sec_base_url.rstrip("/") == (base_url or DEFAULT_OPENAI_BASE_URL).rstrip("/"):
                sec_api_key = active_api_key
            else:
                sec_api_key = ""
            normalized_secondary = {
                "id": "secondary",
                "name": str(raw_secondary.get("name") or sec_model).strip(),
                "model": sec_model,
                "desc": "",
                "ctx": "",
                "price": "",
                "api_key": sec_api_key,
                "base_url": sec_base_url,
                "ctx_limit": ctx_limit,
                "max_concurrency": max_concurrency,
            }
        else:
            normalized_secondary = {
                "id": "secondary",
                "name": "",
                "model": "",
                "desc": "",
                "ctx": "",
                "price": "",
                "api_key": "",
                "base_url": base_url or DEFAULT_OPENAI_BASE_URL,
                "ctx_limit": 0,
                "max_concurrency": 0,
            }

        if not normalized:
            fallback_model = active_model_name or "deepseek-v4-flash"
            normalized = [
                {
                    "id": "candidate-1",
                    "name": fallback_model,
                    "model": fallback_model,
                    "desc": "",
                    "ctx": "",
                    "price": "",
                    "priceHint": _price_hint(fallback_model),
                    "api_key": active_api_key,
                    "base_url": base_url or DEFAULT_OPENAI_BASE_URL,
                }
            ]
        if not normalized_custom:
            normalized_custom = [
                model
                for model in normalized
                if model.get("provider") != "codex_oauth"
            ]
        if model_source == "codex" and normalized_codex:
            normalized = [normalized_codex]
        elif normalized_custom:
            model_source = "custom"
            normalized = normalized_custom
        if not normalized_vision:
            normalized_vision = [
                {
                    "id": "vision-candidate-1",
                    "name": normalized[0]["model"],
                    "model": normalized[0]["model"],
                    "desc": "",
                    "ctx": "",
                    "price": "",
                    "priceHint": _price_hint(normalized[0]["model"]),
                    "api_key": normalized[0]["api_key"],
                    "base_url": normalized[0]["base_url"],
                }
            ]

        active_model_id = next(
            (
                str(model.get("id") or "").strip()
                for model in normalized
                if str(model.get("model") or "").strip() == active_model_name
                or str(model.get("name") or "").strip() == active_model_name
                or str(model.get("id") or "").strip() == active_model_name
            ),
            str(normalized[0].get("id") or "candidate-1"),
        )
        return {
            "models": normalized,
            "primary_candidates": normalized,
            "custom_models": normalized_custom,
            "codex_model": normalized_codex,
            "primary_source": model_source,
            "vision_models": normalized_vision,
            "vision_candidates": normalized_vision,
            "secondary_model": normalized_secondary,
            "active": active_model_id,
            "active_model_name": active_model_name,
            "base_url": base_url,
        }

    @router.put("/api/settings/models")
    async def api_update_models(request: Request):
        from cyrene.runtime.settings_store import (
            get_secondary_model,
            save_codex_model,
            save_custom_models,
            save_model_source,
            save_models,
            save_secondary_model,
            save_vision_models,
        )
        from cyrene.config import DEFAULT_OPENAI_BASE_URL, write_env_keys
        from cyrene.model_runtime.pricing import price_hint as _price_hint
        from cyrene.runtime.onboarding import _test_llm_connection, _test_llm_vision_capability
        body = await request.json()
        raw_models = body.get("models")
        raw_custom_models = body.get("custom_models")
        raw_codex_model = body.get("codex_model")
        uses_parallel_model_settings = (
            "custom_models" in body
            or "codex_model" in body
            or "primary_source" in body
        )
        raw_vision_models = body.get("vision_models")
        raw_secondary = body.get("secondary_model")
        if not isinstance(raw_models, list) or len(raw_models) == 0:
            return JSONResponse({"error": "models must be a non-empty list"}, status_code=400)
        if (
            not uses_parallel_model_settings
            and any(
                str(candidate.get("provider") or "") == "codex_oauth"
                for candidate in raw_models[1:]
            )
        ):
            return JSONResponse(
                {"error": "Codex OAuth can only be used as the primary model"},
                status_code=400,
            )
        if raw_vision_models is not None and (not isinstance(raw_vision_models, list) or len(raw_vision_models) == 0):
            return JSONResponse({"error": "vision_models must be a non-empty list"}, status_code=400)

        def _normalize_candidates(raw_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            from cyrene.model_runtime.codex_provider import CODEX_BASE_URL, CODEX_PROVIDER

            normalized_items: list[dict[str, Any]] = []
            for index, model in enumerate(raw_items):
                model_identifier = str(
                    model.get("model")
                    or model.get("name")
                    or model.get("id")
                    or ""
                ).strip()
                if not model_identifier:
                    continue
                provider = str(model.get("provider") or "openai_compatible").strip()
                normalized_items.append(
                    {
                        "id": str(model.get("id") or f"candidate-{index + 1}").strip() or f"candidate-{index + 1}",
                        "name": model_identifier,
                        "model": model_identifier,
                        "provider": provider,
                        "reasoning_effort": str(model.get("reasoning_effort") or "").strip().lower(),
                        "desc": str(model.get("desc") or "").strip(),
                        "ctx": str(model.get("ctx") or "").strip(),
                        "price": str(model.get("price") or "").strip(),
                        "api_key": (
                            ""
                            if provider == CODEX_PROVIDER
                            else strip_wrapping_quotes(str(model.get("api_key") or "").strip())
                        ),
                        "base_url": (
                            CODEX_BASE_URL
                            if provider == CODEX_PROVIDER
                            else str(model.get("base_url") or DEFAULT_OPENAI_BASE_URL).strip() or DEFAULT_OPENAI_BASE_URL
                        ),
                    }
                )
            return normalized_items

        primary_source = str(body.get("primary_source") or "").strip().lower()
        if primary_source not in {"custom", "codex"}:
            primary_source = (
                "codex"
                if raw_models
                and str(raw_models[0].get("provider") or "") == "codex_oauth"
                else "custom"
            )
        if not isinstance(raw_custom_models, list):
            raw_custom_models = [
                candidate
                for candidate in raw_models
                if str(candidate.get("provider") or "") != "codex_oauth"
            ]
        if not isinstance(raw_codex_model, dict):
            raw_codex_model = next(
                (
                    candidate
                    for candidate in raw_models
                    if str(candidate.get("provider") or "") == "codex_oauth"
                ),
                None,
            )

        normalized_custom = _normalize_candidates(raw_custom_models)
        normalized_codex_items = _normalize_candidates(
            [raw_codex_model] if raw_codex_model else []
        )
        normalized_codex = (
            normalized_codex_items[0] if normalized_codex_items else None
        )
        if primary_source == "codex" and not normalized_codex:
            return JSONResponse(
                {"error": "Codex model is required when OpenAI OAuth is active"},
                status_code=400,
            )
        normalized = (
            [normalized_codex]
            if primary_source == "codex" and normalized_codex
            else normalized_custom
        )
        normalized_vision = _normalize_candidates(raw_vision_models if isinstance(raw_vision_models, list) else [])

        if not normalized:
            return JSONResponse({"error": "models must contain at least one valid model"}, status_code=400)
        if any(
            candidate.get("provider") == "codex_oauth"
            for candidate in normalized_custom
        ):
            return JSONResponse(
                {"error": "Custom model candidates cannot use Codex OAuth"},
                status_code=400,
            )
        if (
            normalized_codex
            and normalized_codex.get("provider") != "codex_oauth"
        ):
            return JSONResponse(
                {"error": "Codex model must use OpenAI OAuth"},
                status_code=400,
            )
        if raw_vision_models is not None and not normalized_vision:
            return JSONResponse({"error": "vision_models must contain at least one valid model"}, status_code=400)
        if any(
            candidate.get("provider") == "codex_oauth"
            for candidate in normalized_vision
        ):
            return JSONResponse(
                {"error": "Codex OAuth cannot be used as a vision model"},
                status_code=400,
            )
        if (
            isinstance(raw_secondary, dict)
            and str(raw_secondary.get("provider") or "") == "codex_oauth"
        ):
            return JSONResponse(
                {"error": "Codex OAuth cannot be used as the secondary model"},
                status_code=400,
            )

        primary = normalized[0]
        primary_model = str(primary.get("model") or "").strip()
        primary_base_url = str(primary.get("base_url") or DEFAULT_OPENAI_BASE_URL).strip() or DEFAULT_OPENAI_BASE_URL
        primary_api_key = strip_wrapping_quotes(str(primary.get("api_key") or "").strip())

        try:
            if primary.get("provider") == "codex_oauth":
                from cyrene.model_runtime.codex_provider import get_codex_provider

                codex_provider = get_codex_provider()
                account_result, codex_models = await asyncio.gather(
                    codex_provider.account(),
                    codex_provider.models(),
                )
                account = account_result.get("account")
                if not (
                    isinstance(account, dict)
                    and account.get("type") == "chatgpt"
                ):
                    raise ValueError("OpenAI OAuth login is required")
                available_models = {
                    str(item.get("model") or item.get("id") or "").strip()
                    for item in codex_models
                }
                if primary_model not in available_models:
                    raise ValueError("Selected Codex model is unavailable")
            else:
                await _test_llm_connection(primary_api_key, primary_base_url, primary_model)
        except httpx.TimeoutException as exc:
            return JSONResponse(
                {"error": "upstream model timed out", "detail": str(exc)},
                status_code=504,
            )
        except httpx.HTTPError as exc:
            return JSONResponse(
                {"error": "upstream model request failed", "detail": format_httpx_error(exc)},
                status_code=502,
            )
        except (RuntimeError, OSError) as exc:
            return JSONResponse(
                {"error": "Codex model validation failed", "detail": str(exc)},
                status_code=503,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

        # Check every model being saved, but de-duplicate identical endpoint
        # configurations so the default primary/vision mirror costs one probe.
        vision_checks: dict[tuple[str, str, str], dict[str, Any]] = {}
        for candidate in [*normalized_custom, *normalized_vision]:
            if candidate.get("provider") == "codex_oauth":
                candidate.update(
                    {
                        # The current app-server adapter sends text-only turns.
                        # Do not advertise multimodal support until image inputs
                        # are forwarded as native Codex turn input items.
                        "vision_capable": False,
                        "vision_checked_at": "",
                        "vision_check_error": "Codex OAuth image input is not supported by this adapter",
                    }
                )
                continue
            check_key = (
                str(candidate.get("model") or ""),
                str(candidate.get("base_url") or "").rstrip("/"),
                str(candidate.get("api_key") or ""),
            )
            capability = vision_checks.get(check_key)
            if capability is None:
                capability = await _test_llm_vision_capability(
                    str(candidate.get("api_key") or ""),
                    str(candidate.get("base_url") or ""),
                    str(candidate.get("model") or ""),
                )
                vision_checks[check_key] = capability
            candidate.update(capability)

        save_custom_models(normalized_custom)
        if normalized_codex:
            save_codex_model(normalized_codex)
        save_model_source(primary_source)
        save_models(normalized)
        if raw_vision_models is not None:
            save_vision_models(normalized_vision)
        if isinstance(raw_secondary, dict):
            save_secondary_model(raw_secondary)
        env_updates = {"OPENAI_MODEL": primary_model}
        if primary.get("provider") != "codex_oauth":
            env_updates.update(
                {
                    "OPENAI_BASE_URL": primary_base_url,
                    "OPENAI_API_KEY": primary_api_key,
                }
            )
        write_env_keys(env_updates)
        # Settings are live without a backend restart. Drop affinities and
        # cooldowns derived from the previous configuration before the first
        # conversation uses the newly saved model.
        from cyrene.model_runtime.client import invalidate_model_configuration
        invalidate_model_configuration()
        saved_secondary = get_secondary_model()
        sec_model = str(saved_secondary.get("model") or "").strip()
        ctx_limit = int(saved_secondary.get("ctx_limit") or 0)
        max_concurrency = int(saved_secondary.get("max_concurrency") or 0)
        if sec_model:
            normalized_secondary = {
                "id": "secondary",
                "name": str(saved_secondary.get("name") or sec_model).strip(),
                "model": sec_model,
                "desc": "",
                "ctx": "",
                "price": "",
                "api_key": strip_wrapping_quotes(str(saved_secondary.get("api_key") or "").strip()),
                "base_url": str(saved_secondary.get("base_url") or DEFAULT_OPENAI_BASE_URL).strip() or DEFAULT_OPENAI_BASE_URL,
                "ctx_limit": ctx_limit,
                "max_concurrency": max_concurrency,
            }
        else:
            normalized_secondary = {
                "id": "secondary",
                "name": "",
                "model": "",
                "desc": "",
                "ctx": "",
                "price": "",
                "api_key": "",
                "base_url": DEFAULT_OPENAI_BASE_URL,
                "ctx_limit": 0,
                "max_concurrency": 0,
            }

        def _with_price_hints(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [
                {
                    **model,
                    "priceHint": (
                        _price_hint(str(model.get("model") or ""))
                        if not str(model.get("price") or "").strip()
                        else ""
                    ),
                }
                for model in items
            ]

        response_models = _with_price_hints(normalized)
        response_custom_models = _with_price_hints(normalized_custom)
        response_codex_model = (
            _with_price_hints([normalized_codex])[0]
            if normalized_codex
            else None
        )
        response_vision_models = _with_price_hints(normalized_vision)
        return {
            "ok": True,
            "models": response_models,
            "primary_candidates": response_models,
            "custom_models": response_custom_models,
            "codex_model": response_codex_model,
            "primary_source": primary_source,
            "vision_models": response_vision_models if raw_vision_models is not None else None,
            "vision_candidates": response_vision_models if raw_vision_models is not None else None,
            "secondary_model": normalized_secondary,
            "active": str(primary.get("id") or "candidate-1"),
            "active_model_name": primary_model,
            "base_url": primary_base_url,
        }

    @router.get("/api/settings/tools")
    async def api_get_tools():
        from cyrene.runtime.settings_store import (
            get_enabled_tool_packs,
            get_enabled_tools,
            is_tool_pack_enabled,
        )
        from cyrene.tooling.catalog import TOOL_DEFS
        from cyrene.tooling.packs import (
            CAPABILITY_BINDINGS,
            PACKS,
            WIRE_NAME_BY_CONCRETE_TOOL,
        )

        enabled = get_enabled_tools()
        enabled_packs = get_enabled_tool_packs()
        tools = []
        for td in TOOL_DEFS:
            name = td["function"]["name"]
            tools.append({
                "name": name,
                "desc": td["function"]["description"],
                "enabled": enabled.get(name, True),
                "configured_enabled": enabled.get(name, True),
                "effective_enabled": (
                    is_tool_pack_enabled(
                        WIRE_NAME_BY_CONCRETE_TOOL.get(name, "")
                    )
                    if name in WIRE_NAME_BY_CONCRETE_TOOL
                    else True
                ),
                "package_id": WIRE_NAME_BY_CONCRETE_TOOL.get(
                    name,
                    "direct_tools",
                ),
                "locked": name == "quit",
            })
        # Include MCP tools from connected servers
        try:
            from cyrene.tooling.backends.mcp_manager import get_manager as _get_mcp_mgr
            manager = _get_mcp_mgr()
            for mcp_td in manager.get_tool_defs():
                name = mcp_td["function"]["name"]
                tools.append({
                    "name": name,
                    "desc": mcp_td["function"]["description"],
                    "enabled": enabled.get(name, True),
                    "configured_enabled": enabled.get(name, True),
                    "effective_enabled": is_tool_pack_enabled(
                        "integration_tools"
                    ),
                    "package_id": "integration_tools",
                    "source": "mcp",
                })
        except Exception:
            pass

        package_tools = {
            wire_name: [
                concrete_name
                for _capability_id, concrete_name in bindings
            ]
            for wire_name, bindings in CAPABILITY_BINDINGS.items()
        }
        package_tools["integration_tools"] = [
            item["name"]
            for item in tools
            if item.get("package_id") == "integration_tools"
        ]
        packages = []
        for pack in PACKS:
            member_names = set(package_tools.get(pack.wire_name, ()))
            members = [
                item
                for item in tools
                if item["name"] in member_names
            ]
            package_enabled = enabled_packs.get(pack.wire_name, True)
            packages.append({
                "id": pack.wire_name,
                "wire_name": pack.wire_name,
                "description": pack.description,
                "enabled": package_enabled,
                "enabled_count": sum(
                    1
                    for item in members
                    if package_enabled
                ),
                "configured_enabled_count": sum(
                    1 for item in members if item["enabled"]
                ),
                "tool_count": len(members),
                "source": "integration"
                if pack.wire_name == "integration_tools"
                else "native",
            })
        tool_groups = [
            {**package, "kind": "package"}
            for package in packages
        ]
        return {
            "tools": tools,
            "packages": packages,
            "tool_groups": tool_groups,
        }

    @router.put("/api/settings/tools")
    async def api_update_tools(request: Request):
        from cyrene.runtime import config_store
        from cyrene.runtime.settings_store import (
            get_enabled_tool_packs,
        )
        from cyrene.runtime.settings_service import (
            SettingsServiceError,
            update as update_settings,
        )
        from cyrene.tooling.packs import PACK_BY_WIRE_NAME

        body = await request.json()
        tool_updates = body.get("tools")
        package_updates = body.get("packages")
        has_tools = isinstance(tool_updates, dict) and bool(tool_updates)
        has_packages = (
            isinstance(package_updates, dict)
            and bool(package_updates)
        )
        if not has_tools and not has_packages:
            return JSONResponse(
                {
                    "error": (
                        "tools or packages must be a non-empty dict"
                    )
                },
                status_code=400,
            )
        if tool_updates is not None and not isinstance(tool_updates, dict):
            return JSONResponse(
                {"error": "tools must be a dict"},
                status_code=400,
            )
        if (
            package_updates is not None
            and not isinstance(package_updates, dict)
        ):
            return JSONResponse(
                {"error": "packages must be a dict"},
                status_code=400,
            )
        invalid_tool_values = [
            str(name)
            for name, value in (tool_updates or {}).items()
            if not isinstance(value, bool)
        ]
        if invalid_tool_values:
            return JSONResponse(
                {
                    "error": (
                        "tool values must be booleans: "
                        + ", ".join(sorted(invalid_tool_values))
                    )
                },
                status_code=400,
            )
        invalid_package_values = [
            str(name)
            for name, value in (package_updates or {}).items()
            if not isinstance(value, bool)
        ]
        if invalid_package_values:
            return JSONResponse(
                {
                    "error": (
                        "package values must be booleans: "
                        + ", ".join(sorted(invalid_package_values))
                    )
                },
                status_code=400,
            )
        unknown_packages = sorted(
            set(package_updates or {}) - set(PACK_BY_WIRE_NAME)
        )
        if unknown_packages:
            return JSONResponse(
                {
                    "error": (
                        "unknown tool package(s): "
                        + ", ".join(unknown_packages)
                    )
                },
                status_code=400,
            )

        changes = {}
        if has_tools:
            changes["enabled_tools"] = {
                str(name): value for name, value in tool_updates.items()
                if str(name) != "quit"
            }
        if has_packages:
            next_packages = get_enabled_tool_packs()
            next_packages.update({
                str(name): value
                for name, value in package_updates.items()
            })
            changes["enabled_tool_packs"] = next_packages
        try:
            result = update_settings(
                "runtime",
                changes,
                actor="ui",
                expected_revision=body.get("expected_revision"),
            )
        except config_store.SettingsRevisionConflict as exc:
            return JSONResponse(
                {"error": str(exc), "revision": exc.actual},
                status_code=409,
            )
        except SettingsServiceError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        await _publish_settings_changed(
            "runtime", result["revision"], list(changes),
        )
        return {
            "ok": True,
            "updated": list(tool_updates or {}),
            "updated_packages": list(package_updates or {}),
            "revision": result["revision"],
        }

    @router.get("/api/settings/config")
    async def api_get_config():
        return _build_config()

    @router.get("/api/settings/storage")
    async def api_get_storage():
        from cyrene.runtime.storage import scan_storage

        return await asyncio.to_thread(scan_storage)

    @router.get("/api/settings/namespaces/{namespace}")
    async def api_get_settings_namespace(namespace: str):
        from cyrene.runtime.host_bridge import HostBridgeError, call_host
        from cyrene.runtime.settings_service import SettingsServiceError, read_public
        try:
            if namespace == "desktop":
                result = await call_host("desktop.settings.get", {})
                if result.get("ok") is False:
                    return JSONResponse(result, status_code=409)
                settings = dict(result.get("settings") or {})
                revision = settings.pop("settingsRevision", None)
                return {"revision": revision, "values": settings}
            return read_public(namespace)
        except HostBridgeError as exc:
            return JSONResponse({"error": exc.code}, status_code=503)
        except SettingsServiceError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @router.put("/api/settings/namespaces/{namespace}")
    async def api_update_settings_namespace(namespace: str, request: Request):
        from cyrene.runtime import config_store
        from cyrene.runtime.host_bridge import HostBridgeError, call_host
        from cyrene.runtime.settings_service import SettingsServiceError, update as update_settings, validate_changes
        body = await request.json()
        changes = body.get("changes")
        try:
            if namespace == "desktop":
                normalized, _specs = validate_changes("desktop", changes, actor="ui")
                result = await call_host(
                    "desktop.settings.update",
                    {"changes": normalized, "expectedRevision": body.get("expected_revision")},
                )
                if result.get("ok") is False:
                    return JSONResponse(
                        result,
                        status_code=409 if result.get("error") == "revision_conflict" else 400,
                    )
                settings = result.get("settings") or {}
                await _publish_settings_changed(
                    "desktop", settings.get("settingsRevision"), list(normalized),
                )
                return result
            result = update_settings(
                namespace,
                changes,
                actor="ui",
                expected_revision=body.get("expected_revision"),
            )
            await _publish_settings_changed(
                namespace, result["revision"], result["changed"],
            )
            return {
                "ok": True,
                **result,
            }
        except config_store.SettingsRevisionConflict as exc:
            return JSONResponse({"error": str(exc), "revision": exc.actual}, status_code=409)
        except HostBridgeError as exc:
            return JSONResponse({"error": exc.code}, status_code=503)
        except SettingsServiceError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @router.put("/api/settings/config")
    async def api_update_config(request: Request):
        from cyrene.runtime import config_store
        from cyrene.runtime.settings_service import (
            SettingsServiceError,
            update as update_settings,
        )
        body = await request.json()
        expected_revision = body.pop("expected_revision", None)
        try:
            result = update_settings(
                "runtime",
                body,
                actor="ui",
                expected_revision=expected_revision,
            )
        except config_store.SettingsRevisionConflict as exc:
            return JSONResponse(
                {"error": str(exc), "revision": exc.actual},
                status_code=409,
            )
        except SettingsServiceError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        await _publish_settings_changed(
            "runtime", result["revision"], result["changed"],
        )
        return {"ok": True, **result}

    @router.get("/api/settings/integrations")
    async def api_get_integration_settings():
        """Return Zotero/embedding settings without exposing stored secrets."""
        from cyrene.runtime.integration_settings import public_settings

        return public_settings()

    @router.get("/api/settings/local-models/status")
    async def api_local_models_status():
        from cyrene.knowledge.local_models import status

        return status()

    @router.post("/api/settings/local-models/ocr-runtime/download")
    async def api_download_ocr_runtime():
        from cyrene.model_runtime import opencv_runtime

        try:
            return {"ok": True, **opencv_runtime.start_download()}
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)

    @router.post("/api/settings/local-models/{model_id}/download")
    async def api_download_local_model(model_id: str):
        from cyrene.knowledge.local_models import start_download

        try:
            return {"ok": True, **start_download(model_id)}
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @router.delete("/api/settings/local-models/{model_id}")
    async def api_delete_local_model(model_id: str):
        from cyrene.knowledge.local_models import delete_model

        try:
            return {"ok": True, **(await delete_model(model_id))}
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @router.put("/api/settings/integrations")
    async def api_update_integration_settings(request: Request):
        from cyrene.runtime.integration_settings import update_settings

        body = await request.json()
        if not isinstance(body, dict) or not ({"zotero", "embedding"} & set(body)):
            return JSONResponse(
                {"error": "zotero or embedding settings are required"}, status_code=400
            )
        try:
            payload = update_settings(body)
        except (TypeError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"ok": True, **payload}

    @router.post("/api/settings/integrations/test")
    async def api_test_integration(request: Request):
        """Probe unsaved integration settings and return only safe metadata."""
        from cyrene.runtime.integration_settings import (
            merged_test_config,
            test_embedding,
            test_zotero,
        )

        body = await request.json()
        service = str(body.get("service") or "").strip().lower() if isinstance(body, dict) else ""
        draft = body.get("config", {}) if isinstance(body, dict) else {}
        try:
            integration_config = merged_test_config(service, draft)
            if service == "zotero":
                return await test_zotero(integration_config)
            if service == "embedding":
                return await test_embedding(integration_config)
            raise ValueError("unknown integration service")
        except (TypeError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            return JSONResponse(
                {"error": f"remote service returned HTTP {status}"}, status_code=502
            )
        except httpx.RequestError:
            return JSONResponse(
                {"error": "could not reach the configured service"}, status_code=503
            )
        except Exception as exc:
            logger.info("Integration connectivity test failed", exc_info=True)
            if (
                service == "embedding"
                and isinstance(draft, dict)
                and str(draft.get("provider") or "").strip().lower().replace("-", "_")
                == "local_onnx"
            ):
                detail = str(exc).strip() or "unknown local inference error"
                return JSONResponse(
                    {"error": f"local embedding test failed: {detail[:500]}"},
                    status_code=502,
                )
            return JSONResponse({"error": "connection test failed"}, status_code=502)

    @router.put("/api/profile")
    async def api_update_profile(request: Request):
        """Persist the user's custom identity (name / avatar / bio)."""
        from cyrene.runtime import config_store
        from cyrene.runtime.settings_service import (
            SettingsServiceError,
            update as update_settings,
        )
        body = await request.json()
        key_map = {
            "name": "profile_name",
            "bio": "profile_bio",
            "avatar": "profile_avatar",
            "avatar_emoji": "profile_avatar_emoji",
            "avatar_color": "profile_avatar_color",
        }
        changes = {
            setting_key: body[public_key]
            for public_key, setting_key in key_map.items()
            if public_key in body
        }
        try:
            result = update_settings(
                "profile",
                changes,
                actor="ui",
                expected_revision=body.get("expected_revision"),
            )
        except config_store.SettingsRevisionConflict as exc:
            return JSONResponse(
                {"error": str(exc), "revision": exc.actual}, status_code=409,
            )
        except SettingsServiceError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        await _publish_settings_changed(
            "profile", result["revision"], result["changed"],
        )
        return {
            "ok": True,
            "changed": [key for key in key_map if key in body],
            "revision": result["revision"],
            "user": _build_user(),
        }

    @router.post("/api/settings/reset-data")
    async def api_reset_data(request: Request):
        try:
            body = await request.json()
        except (ValueError, json.JSONDecodeError):
            body = {}
        if not isinstance(body, dict) or body.get("confirmation") != "RESET CYRENE DATA":
            return JSONResponse(
                {
                    "error": "explicit reset confirmation is required",
                    "code": "reset_confirmation_required",
                },
                status_code=400,
            )
        try:
            return await _reset_app_data()
        except Exception as exc:
            logger.exception("Application data reset failed")
            return JSONResponse(
                {
                    "error": "application data reset failed",
                    "detail": str(exc) or exc.__class__.__name__,
                    "code": "reset_failed",
                },
                status_code=500,
            )

    @router.get("/api/settings/search")
    async def api_get_search():
        return {"search": _build_search_config()}

    @router.put("/api/settings/search")
    async def api_update_search(request: Request):
        from cyrene.runtime.settings_store import set_ as set_setting
        await request.json()
        set_setting("search_mode", "builtin")
        set_setting("search_external_url", "")
        return {"ok": True, "changed": ["search_mode", "search_external_url"]}

    # ---- Budget Stats API ----

    @router.get("/api/settings/budget/stats")
    async def api_budget_stats():
        import calendar
        from datetime import datetime, timezone
        from cyrene.runtime.database import get_token_usage_stats as _usage_stats
        from cyrene.model_runtime.pricing import cost_from_cny as _cost_from_cny
        from cyrene.runtime.settings_store import get_all as _gsett

        currency = str(_gsett().get("budget_currency") or "CNY").upper()
        start_day = int(_gsett().get("budget_start_day") or 1)
        now = datetime.now(timezone.utc)
        _, days_in_month = calendar.monthrange(now.year, now.month)
        period_start_day = min(start_day, days_in_month)
        period_start = datetime(now.year, now.month, period_start_day, tzinfo=timezone.utc)
        if now < period_start:
            pm = now.month - 1 if now.month > 1 else 12
            py = now.year if now.month > 1 else now.year - 1
            _, days_in_prev = calendar.monthrange(py, pm)
            period_start = datetime(py, pm, min(start_day, days_in_prev), tzinfo=timezone.utc)
        try:
            # Use the exact billing-period boundary. Converting it to an
            # integer day count creates a rolling window that can include
            # usage from before the configured start date.
            stats = await _usage_stats(
                str(_db_path or DB_PATH),
                since=period_start,
            )
            by_model = stats.get("by_model", [])
            total = stats.get("total", {})
        except Exception:
            by_model = []
            total = {}
        total_requests = int(total.get("requests", 0))

        rows = []
        for m in by_model:
            # Aggregated database costs are canonical CNY. The currency chosen
            # beside Monthly Budget controls both budget comparison and display.
            cost = round(_cost_from_cny(float(m.get("cost", 0)), currency), 4)
            rows.append({
                "model": m.get("model", ""),
                "requests": int(m.get("requests", 0)),
                "prompt_tokens": int(m.get("prompt_tokens", 0)),
                "completion_tokens": int(m.get("completion_tokens", 0)),
                "cost": cost,
            })
        rows.sort(key=lambda r: r["cost"], reverse=True)

        return {
            "models": rows,
            "total_cost": round(sum(r["cost"] for r in rows), 4),
            "total_requests": total_requests,
        }

    @router.get("/api/budget/status")
    async def api_budget_status():
        """Return current budget state (weekly + 5-hour block)."""
        from cyrene.runtime.settings_store import get_all as _get_sett
        from cyrene.agent.budget import get_budget_state as _budget_state

        sett = _get_sett()
        state = await _budget_state(
            str(_db_path or DB_PATH),
            monthly=float(sett.get("budget_monthly") or 0),
            enabled=bool(sett.get("budget_enabled", False)),
        )
        return state

    # ---- MCP Servers API ----

    @router.get("/api/settings/mcp")
    async def api_get_mcp_servers():
        from cyrene.tooling.backends.mcp_manager import get_manager as _get_mcp_mgr, get_mcp_servers as _get_servers, redact_mcp_servers as _redact_servers
        manager = _get_mcp_mgr()
        return {
            "servers": manager.get_server_status(),
            "configs": _redact_servers(_get_servers()),
        }

    @router.put("/api/settings/mcp")
    async def api_update_mcp_servers(request: Request):
        from cyrene.tooling.backends.mcp_manager import get_mcp_servers as _get_servers, merge_redacted_mcp_servers as _merge_servers, save_mcp_servers as _save_servers, restart_mcp as _restart_mcp
        body = await request.json()
        servers = body.get("servers", [])
        try:
            _save_servers(_merge_servers(_get_servers(), servers))
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        await _restart_mcp()
        return {"ok": True}
