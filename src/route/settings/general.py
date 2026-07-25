"""Onboarding, context, settings, budget, and MCP routes."""

# ruff: noqa: F403,F405

from cyrene.workbench_runtime import *


def register_settings_routes(router: APIRouter, bot: Any, db_path: str) -> None:
    global _bot, _db_path
    _bot = bot
    _db_path = db_path

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
        from cyrene.settings_store import is_workspace_active, is_soul_active, get_workspace_history
        # When inside a Workbench project, reflect the project's actual workspace
        # path instead of the global fallback so the UI chip and context-picker
        # defaults match the project the user is working in.
        workspace_dir = str(WORKSPACE_DIR)
        try:
            store = _read_workbench_store()
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

    @router.post("/api/context/remove-soul")
    async def api_remove_soul():
        from cyrene.settings_store import set_soul_active
        set_soul_active(False)
        return {"ok": True}

    @router.post("/api/context/add-soul")
    async def api_add_soul():
        from cyrene.settings_store import set_soul_active
        set_soul_active(True)
        return {"ok": True}

    @router.post("/api/context/remove-workspace")
    async def api_remove_workspace():
        from cyrene.settings_store import set_workspace_active
        set_workspace_active(False)
        return {"ok": True}

    @router.post("/api/context/add-workspace")
    async def api_add_workspace(request: Request):
        from cyrene.settings_store import set_workspace_active, add_workspace_to_history
        body = await request.json()
        path = str(body.get("path", "")).strip()
        set_workspace_active(True)
        if path:
            add_workspace_to_history(path)
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

    @router.get("/api/settings/models")
    async def api_get_models():
        from cyrene.settings_store import get_models, get_vision_models, get_secondary_model
        from cyrene.config import OPENAI_API_KEY, DEFAULT_OPENAI_BASE_URL, read_env_file
        from cyrene.model_prices import price_hint as _price_hint

        def _normalize_candidates(raw_items: list[dict[str, Any]] | None, fallback_api_key: str, fallback_base_url: str) -> list[dict[str, Any]]:
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
                model_base_url = str(model.get("base_url") or fallback_base_url or DEFAULT_OPENAI_BASE_URL).strip() or DEFAULT_OPENAI_BASE_URL
                raw_model_api_key = _strip_wrapping_quotes(str(model.get("api_key") or "").strip())
                if raw_model_api_key:
                    model_api_key = raw_model_api_key
                elif model_base_url.rstrip("/") == (fallback_base_url or DEFAULT_OPENAI_BASE_URL).rstrip("/"):
                    model_api_key = fallback_api_key
                else:
                    model_api_key = ""
                user_price = str(model.get("price") or "").strip()
                normalized_items.append(
                    {
                        "id": str(model.get("id") or f"candidate-{index + 1}").strip() or f"candidate-{index + 1}",
                        "name": str(model.get("name") or model_identifier).strip() or model_identifier,
                        "model": model_identifier,
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
        raw_vision_models = get_vision_models()
        raw_secondary = get_secondary_model()
        active_model_name, base_url = _live_llm_config()
        env_keys = read_env_file()
        active_api_key = _strip_wrapping_quotes(str(env_keys.get("OPENAI_API_KEY") or OPENAI_API_KEY or "").strip())
        normalized = _normalize_candidates(raw_models, active_api_key, base_url)
        normalized_vision = _normalize_candidates(raw_vision_models, active_api_key, base_url)

        # Normalize secondary model (single item)
        sec_model = str(raw_secondary.get("model") or "").strip()
        ctx_limit = int(raw_secondary.get("ctx_limit") or 0)
        max_concurrency = int(raw_secondary.get("max_concurrency") or 0)
        if sec_model:
            sec_base_url = str(raw_secondary.get("base_url") or base_url or DEFAULT_OPENAI_BASE_URL).strip() or DEFAULT_OPENAI_BASE_URL
            sec_raw_api_key = _strip_wrapping_quotes(str(raw_secondary.get("api_key") or "").strip())
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
            "vision_models": normalized_vision,
            "vision_candidates": normalized_vision,
            "secondary_model": normalized_secondary,
            "active": active_model_id,
            "active_model_name": active_model_name,
            "base_url": base_url,
        }

    @router.put("/api/settings/models")
    async def api_update_models(request: Request):
        from cyrene.settings_store import save_models, save_vision_models, save_secondary_model, get_secondary_model
        from cyrene.config import DEFAULT_OPENAI_BASE_URL, write_env_keys
        from cyrene.model_prices import price_hint as _price_hint
        from cyrene.onboarding import _test_llm_connection, _test_llm_vision_capability
        body = await request.json()
        raw_models = body.get("models")
        raw_vision_models = body.get("vision_models")
        raw_secondary = body.get("secondary_model")
        if not isinstance(raw_models, list) or len(raw_models) == 0:
            return JSONResponse({"error": "models must be a non-empty list"}, status_code=400)
        if raw_vision_models is not None and (not isinstance(raw_vision_models, list) or len(raw_vision_models) == 0):
            return JSONResponse({"error": "vision_models must be a non-empty list"}, status_code=400)

        def _normalize_candidates(raw_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
                normalized_items.append(
                    {
                        "id": str(model.get("id") or f"candidate-{index + 1}").strip() or f"candidate-{index + 1}",
                        "name": model_identifier,
                        "model": model_identifier,
                        "desc": str(model.get("desc") or "").strip(),
                        "ctx": str(model.get("ctx") or "").strip(),
                        "price": str(model.get("price") or "").strip(),
                        "api_key": _strip_wrapping_quotes(str(model.get("api_key") or "").strip()),
                        "base_url": str(model.get("base_url") or DEFAULT_OPENAI_BASE_URL).strip() or DEFAULT_OPENAI_BASE_URL,
                    }
                )
            return normalized_items

        normalized = _normalize_candidates(raw_models)
        normalized_vision = _normalize_candidates(raw_vision_models if isinstance(raw_vision_models, list) else [])

        if not normalized:
            return JSONResponse({"error": "models must contain at least one valid model"}, status_code=400)
        if raw_vision_models is not None and not normalized_vision:
            return JSONResponse({"error": "vision_models must contain at least one valid model"}, status_code=400)

        primary = normalized[0]
        primary_model = str(primary.get("model") or "").strip()
        primary_base_url = str(primary.get("base_url") or DEFAULT_OPENAI_BASE_URL).strip() or DEFAULT_OPENAI_BASE_URL
        primary_api_key = _strip_wrapping_quotes(str(primary.get("api_key") or "").strip())

        try:
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
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

        # Check every model being saved, but de-duplicate identical endpoint
        # configurations so the default primary/vision mirror costs one probe.
        vision_checks: dict[tuple[str, str, str], dict[str, Any]] = {}
        for candidate in [*normalized, *normalized_vision]:
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

        save_models(normalized)
        if raw_vision_models is not None:
            save_vision_models(normalized_vision)
        if isinstance(raw_secondary, dict):
            save_secondary_model(raw_secondary)
        write_env_keys(
            {
                "OPENAI_MODEL": primary_model,
                "OPENAI_BASE_URL": primary_base_url,
                "OPENAI_API_KEY": primary_api_key,
            }
        )
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
                "api_key": _strip_wrapping_quotes(str(saved_secondary.get("api_key") or "").strip()),
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
        response_vision_models = _with_price_hints(normalized_vision)
        return {
            "ok": True,
            "models": response_models,
            "primary_candidates": response_models,
            "vision_models": response_vision_models if raw_vision_models is not None else None,
            "vision_candidates": response_vision_models if raw_vision_models is not None else None,
            "secondary_model": normalized_secondary,
            "active": str(primary.get("id") or "candidate-1"),
            "active_model_name": primary_model,
            "base_url": primary_base_url,
        }

    @router.get("/api/settings/tools")
    async def api_get_tools():
        from cyrene.settings_store import (
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
            from cyrene.mcp_manager import get_manager as _get_mcp_mgr
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
        from cyrene.settings_store import (
            get_enabled_tool_packs,
            save_enabled_tool_packs,
            save_enabled_tools,
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

        if has_tools:
            save_enabled_tools({
                str(name): value
                for name, value in tool_updates.items()
            })
        updated_packages = []
        if has_packages:
            next_packages = get_enabled_tool_packs()
            next_packages.update({
                str(name): value
                for name, value in package_updates.items()
            })
            save_enabled_tool_packs(next_packages)
            updated_packages = list(package_updates)
        return {
            "ok": True,
            "updated": list(tool_updates or {}),
            "updated_packages": updated_packages,
        }

    @router.get("/api/settings/config")
    async def api_get_config():
        return _build_config()

    @router.put("/api/settings/config")
    async def api_update_config(request: Request):
        from cyrene.settings_store import set_ as set_setting
        body = await request.json()
        changed = []
        if "spawn_policy" in body:
            value = str(body.get("spawn_policy") or "").strip().lower()
            if value not in {"aggressive", "conservative", "off"}:
                return JSONResponse({"error": "invalid spawn_policy"}, status_code=400)
            set_setting("spawn_policy", value)
            changed.append("spawn_policy")
        if "heartbeat_interval" in body:
            value = int(body.get("heartbeat_interval") or 0)
            if value < 60:
                return JSONResponse({"error": "heartbeat_interval must be at least 60"}, status_code=400)
            set_setting("heartbeat_interval", value)
            changed.append("heartbeat_interval")
        if "agent_proactive" in body:
            set_setting("agent_proactive", bool(body["agent_proactive"]))
            changed.append("agent_proactive")
        if "app_language" in body:
            value = str(body.get("app_language") or "").strip().lower()
            if value not in {"", "en", "zh"}:
                return JSONResponse({"error": "invalid app_language"}, status_code=400)
            set_setting("app_language", value)
            changed.append("app_language")
        if "max_tool_rounds" in body:
            value = int(body.get("max_tool_rounds") or 15)
            if value < 5 or value > 200:
                return JSONResponse({"error": "max_tool_rounds must be between 5 and 200"}, status_code=400)
            set_setting("max_tool_rounds", value)
            changed.append("max_tool_rounds")
        subagent_integer_settings = {
            "subagent_execution_max_tool_calls": (1, 5000),
            "subagent_execution_max_wall_seconds": (30, 86400),
            "subagent_execution_no_progress_turns": (1, 20),
            "subagent_execution_checkpoint_calls": (1, 500),
            "subagent_execution_max_context_tokens": (0, 4000000),
            "subagent_discussion_max_rounds": (1, 50),
            "subagent_discussion_max_messages_per_agent": (1, 50),
            "subagent_discussion_max_total_messages": (1, 500),
            "subagent_discussion_max_message_chars": (100, 20000),
            "subagent_discussion_max_wall_seconds": (30, 86400),
            "subagent_discussion_max_tool_calls": (1, 1000),
            "subagent_discussion_no_new_info_rounds": (1, 20),
        }
        for key, (minimum, maximum) in subagent_integer_settings.items():
            if key not in body:
                continue
            try:
                value = int(body.get(key))
            except (TypeError, ValueError):
                return JSONResponse({"error": f"{key} must be an integer"}, status_code=400)
            if value < minimum or value > maximum:
                return JSONResponse(
                    {"error": f"{key} must be between {minimum} and {maximum}"},
                    status_code=400,
                )
            set_setting(key, value)
            changed.append(key)
        if "subagent_execution_max_cost_usd" in body:
            try:
                value = float(body.get("subagent_execution_max_cost_usd"))
            except (TypeError, ValueError):
                return JSONResponse(
                    {"error": "subagent_execution_max_cost_usd must be a number"},
                    status_code=400,
                )
            if not math.isfinite(value) or value < 0 or value > 1000:
                return JSONResponse(
                    {"error": "subagent_execution_max_cost_usd must be between 0 and 1000"},
                    status_code=400,
                )
            set_setting("subagent_execution_max_cost_usd", value)
            changed.append("subagent_execution_max_cost_usd")
        if "notify_telegram" in body:
            set_setting("notify_telegram", bool(body["notify_telegram"]))
            changed.append("notify_telegram")
        if "notify_wechat" in body:
            set_setting("notify_wechat", bool(body["notify_wechat"]))
            changed.append("notify_wechat")
        if "redact_secrets" in body:
            set_setting("redact_secrets", bool(body["redact_secrets"]))
            changed.append("redact_secrets")
        if "beta_updates" in body:
            set_setting("beta_updates", bool(body["beta_updates"]))
            changed.append("beta_updates")
        if "auto_update" in body:
            set_setting("auto_update", bool(body["auto_update"]))
            changed.append("auto_update")
        if "budget_enabled" in body:
            set_setting("budget_enabled", bool(body["budget_enabled"]))
            changed.append("budget_enabled")
        if "budget_monthly" in body:
            value = float(body.get("budget_monthly") or 0)
            if not math.isfinite(value) or value < 0:
                return JSONResponse({"error": "budget_monthly must be a non-negative number"}, status_code=400)
            set_setting("budget_monthly", value)
            changed.append("budget_monthly")
        if "budget_currency" in body:
            value = str(body.get("budget_currency") or "").strip().upper()
            if value not in {"CNY", "USD"}:
                return JSONResponse({"error": "invalid budget_currency"}, status_code=400)
            set_setting("budget_currency", value)
            changed.append("budget_currency")
        if "budget_action" in body:
            value = str(body.get("budget_action") or "").strip().lower()
            if value not in {"warn", "block"}:
                return JSONResponse({"error": "invalid budget_action"}, status_code=400)
            set_setting("budget_action", value)
            changed.append("budget_action")
        if "budget_mode" in body:
            value = str(body.get("budget_mode") or "").strip().lower()
            if value not in {"economy", "normal"}:
                return JSONResponse({"error": "invalid budget_mode"}, status_code=400)
            set_setting("budget_mode", value)
            changed.append("budget_mode")
        if "budget_start_day" in body:
            value = int(body.get("budget_start_day") or 1)
            if value < 1 or value > 28:
                return JSONResponse({"error": "budget_start_day must be between 1 and 28"}, status_code=400)
            set_setting("budget_start_day", value)
            changed.append("budget_start_day")
        return {"ok": True, "changed": changed}

    @router.get("/api/settings/integrations")
    async def api_get_integration_settings():
        """Return Zotero/embedding settings without exposing stored secrets."""
        from cyrene.integration_settings import public_settings

        return public_settings()

    @router.put("/api/settings/integrations")
    async def api_update_integration_settings(request: Request):
        from cyrene.integration_settings import update_settings

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
        from cyrene.integration_settings import (
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
        except Exception:
            logger.info("Integration connectivity test failed", exc_info=True)
            return JSONResponse({"error": "connection test failed"}, status_code=502)

    @router.put("/api/profile")
    async def api_update_profile(request: Request):
        """Persist the user's custom identity (name / avatar / bio)."""
        from cyrene.settings_store import set_ as set_setting
        body = await request.json()
        changed: list[str] = []
        if "name" in body:
            set_setting("profile_name", str(body.get("name") or "").strip()[:60])
            changed.append("name")
        if "bio" in body:
            set_setting("profile_bio", str(body.get("bio") or "").strip()[:120])
            changed.append("bio")
        if "avatar" in body:
            avatar = str(body.get("avatar") or "").strip()
            if avatar and not avatar.startswith("data:image/"):
                return JSONResponse({"error": "avatar must be a data:image/ URL"}, status_code=400)
            if len(avatar) > 700_000:
                return JSONResponse({"error": "avatar too large (max ~512KB)"}, status_code=400)
            set_setting("profile_avatar", avatar)
            changed.append("avatar")
        if "avatar_emoji" in body:
            set_setting("profile_avatar_emoji", str(body.get("avatar_emoji") or "").strip()[:8])
            changed.append("avatar_emoji")
        if "avatar_color" in body:
            color = str(body.get("avatar_color") or "").strip()
            if color and not re.match(r"^#[0-9a-fA-F]{6}$", color):
                return JSONResponse({"error": "avatar_color must be #rrggbb"}, status_code=400)
            set_setting("profile_avatar_color", color)
            changed.append("avatar_color")
        return {"ok": True, "changed": changed, "user": _build_user()}

    @router.post("/api/settings/reset-data")
    async def api_reset_data():
        return await _reset_app_data()

    @router.get("/api/settings/search")
    async def api_get_search():
        return {"search": _build_search_config()}

    @router.put("/api/settings/search")
    async def api_update_search(request: Request):
        from cyrene.settings_store import set_ as set_setting
        await request.json()
        set_setting("search_mode", "builtin")
        set_setting("search_external_url", "")
        return {"ok": True, "changed": ["search_mode", "search_external_url"]}

    # ---- Budget Stats API ----

    @router.get("/api/settings/budget/stats")
    async def api_budget_stats():
        import calendar
        from datetime import datetime, timezone
        from cyrene.db import get_token_usage_stats as _usage_stats
        from cyrene.model_prices import CNY_PER_USD as _CNY2USD
        from cyrene.settings_store import get_all as _gsett

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
        days_since_period_start = max(int((now - period_start).total_seconds() / 86400) + 1, 1)

        try:
            stats = await _usage_stats(str(_db_path or DB_PATH), days=days_since_period_start)
            by_model = stats.get("by_model", [])
            total = stats.get("total", {})
        except Exception:
            by_model = []
            total = {}
        by_model = stats.get("by_model", [])
        total = stats.get("total", {})
        total_requests = int(total.get("requests", 0))

        rows = []
        for m in by_model:
            cost = round(float(m.get("cost", 0)), 4)
            # get_token_usage_stats returns cost in USD; convert to CNY for CNY users
            if currency == "CNY":
                cost = round(cost * _CNY2USD, 4)
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
        from cyrene.settings_store import get_all as _get_sett
        from cyrene.budget import get_budget_state as _budget_state

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
        from cyrene.mcp_manager import get_manager as _get_mcp_mgr, get_mcp_servers as _get_servers
        manager = _get_mcp_mgr()
        return {
            "servers": manager.get_server_status(),
            "configs": _get_servers(),
        }

    @router.put("/api/settings/mcp")
    async def api_update_mcp_servers(request: Request):
        from cyrene.mcp_manager import save_mcp_servers as _save_servers, restart_mcp as _restart_mcp
        body = await request.json()
        servers = body.get("servers", [])
        _save_servers(servers)
        await _restart_mcp()
        return {"ok": True}
