"""Persistent onboarding helpers for Web UI first-run setup."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cyrene.core.plugin import application_plugin_service
from cyrene.config import DATA_DIR, DB_PATH

logger = logging.getLogger(__name__)


def _memory_service():
    service = application_plugin_service("memory")
    if service is None:
        raise RuntimeError("memory Plugin is not available")
    return service


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _onboarding_state_path() -> Path:
    return DATA_DIR / "onboarding_state.json"


def _setup_flag_path() -> Path:
    return DATA_DIR / ".setup_done"


def _normalize_state(raw: Any) -> dict[str, Any]:
    state = raw if isinstance(raw, dict) else {}
    llm = state.get("llm") if isinstance(state.get("llm"), dict) else {}
    return {
        "version": 1,
        "completed_at": str(state.get("completed_at") or "").strip(),
        "llm": {
            "completed_at": str(llm.get("completed_at") or "").strip(),
            "source": str(llm.get("source") or "").strip(),
        },
    }


def load_onboarding_state() -> dict[str, Any]:
    path = _onboarding_state_path()
    if not path.exists():
        return _normalize_state({})
    try:
        return _normalize_state(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        logger.warning("Failed to read onboarding state, treating as empty")
        return _normalize_state({})


def save_onboarding_state(state: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_state(state)
    path = _onboarding_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    return normalized


def reset_onboarding_state() -> None:
    """Remove persisted onboarding markers so setup appears as fresh again."""
    for path in (_onboarding_state_path(), _setup_flag_path()):
        try:
            if path.exists():
                path.unlink()
        except Exception:
            logger.exception("Failed to remove onboarding state file: %s", path)


def _primary_model() -> dict[str, Any]:
    """Return the canonical primary-route model, including its secret."""

    try:
        service = application_plugin_service("model_configuration")
        candidates = service.candidates_for_route("primary") if service is not None else []
    except Exception:
        logger.warning("Failed to resolve the primary model route", exc_info=True)
        return {}
    return dict(candidates[0]) if candidates else {}


def _model_configured() -> bool:
    return bool(_primary_model())


def _provider_id(candidate: dict[str, Any]) -> str:
    options = candidate.get("options")
    preset = options.get("provider_preset") if isinstance(options, dict) else ""
    return str(preset or candidate.get("adapter") or candidate.get("provider") or "").strip()


def _personality_status() -> dict[str, Any]:
    """Return an optional onboarding step contributed by the Soul Plugin."""

    service = application_plugin_service("soul_onboarding")
    status = getattr(service, "status", None)
    if not callable(status):
        return {
            "available": False,
            "configured": False,
            "completedAt": "",
            "mode": "",
            "label": "",
            "isDefaultSoul": False,
            "path": "",
            "currentContent": "",
            "source": "",
            "pristine": True,
        }
    try:
        value = status()
    except Exception:
        logger.warning("Optional Soul onboarding status is unavailable", exc_info=True)
        return {
            "available": False,
            "configured": False,
            "completedAt": "",
            "mode": "",
            "label": "",
            "isDefaultSoul": False,
            "path": "",
            "currentContent": "",
            "source": "",
            "pristine": True,
        }
    if not isinstance(value, dict):
        raise RuntimeError("Soul onboarding contribution returned invalid status")
    return {"available": True, **value}


def _has_runtime_activity() -> bool:
    try:
        return _memory_service().has_existing_data()
    except RuntimeError:
        return False


def _has_existing_data() -> bool:
    """True when the install already holds user content from prior use — any
    signal that this is not a brand-new first run.

    Covers both the Agent runtime and Workbench chat threads. Memory-owned
    persistence is queried through the memory Plugin. The first-run
    onboarding takeover is suppressed whenever this is True, so an existing user
    is never dragged back into setup just because their SOUL.md is still the
    default. Only a full "reset app data" — which clears every source below —
    returns the app to a genuine fresh start.
    """
    if _has_runtime_activity():
        return True

    # Workbench chat threads. Memory documents are intentionally not inspected
    # here; their storage layout belongs to the memory Plugin.
    try:
        from cyrene.workbench.persistence.store import has_document_data

        if DB_PATH.exists() and has_document_data(DB_PATH, "chats"):
            return True
    except Exception:
        logger.warning("Failed to detect existing workbench data; user may be misjudged as new install", exc_info=True)

    # Note: projects are excluded on purpose — an empty default project is
    # auto-created and is not evidence of prior use.
    return False


def _is_absolute_fresh_start(
    *,
    personality: dict[str, Any] | None = None,
    existing_data: bool | None = None,
) -> bool:
    """A pristine first run with no model, activity, or configured Plugin step."""

    personality = personality or _personality_status()
    return (
        not _onboarding_state_path().exists()
        and not _setup_flag_path().exists()
        and not _model_configured()
        and (not personality["available"] or bool(personality.get("pristine", True)))
        and not (_has_existing_data() if existing_data is None else existing_data)
    )


def _merge_inferred_state(
    state: dict[str, Any],
    personality: dict[str, Any] | None = None,
) -> dict[str, Any]:
    merged = _normalize_state(state)
    llm_configured = _model_configured()
    personality = personality or _personality_status()
    personality_satisfied = not personality["available"] or bool(
        personality.get("configured")
    )
    if llm_configured and personality_satisfied and not merged["completed_at"]:
        merged["completed_at"] = _now_iso()
        merged = save_onboarding_state(merged)
    return merged


def get_onboarding_status() -> dict[str, Any]:
    personality = _personality_status()
    state = _merge_inferred_state(load_onboarding_state(), personality)
    primary = _primary_model()
    llm_configured = bool(primary)
    personality_available = bool(personality.get("available"))
    personality_configured = bool(personality.get("configured"))
    all_configured = llm_configured and (
        not personality_available or personality_configured
    )

    existing_data = _has_existing_data()
    fresh_start = _is_absolute_fresh_start(
        personality=personality,
        existing_data=existing_data,
    )
    # The wizard is mid-flow only when a step was completed through the wizard
    # and setup isn't finished — e.g. the model step is saved but personality
    # selection is still pending.
    wizard_in_progress = not all_configured and (
        state["llm"].get("source") == "wizard"
        or personality.get("source") == "wizard"
    )
    # Only take over with first-run onboarding for a genuine fresh start, or to
    # finish a wizard already in progress. An existing install — any prior data
    # or LLM config — is never forced back into onboarding even if its SOUL.md is
    # still the default; only a full data reset returns it to a fresh start.
    needs_onboarding = not all_configured and (fresh_start or wizard_in_progress)
    active_step = "done"
    if not llm_configured:
        active_step = "llm"
    elif personality_available and not personality_configured:
        active_step = "personality"

    return {
        "needsOnboarding": needs_onboarding,
        "isAbsoluteFreshStart": fresh_start,
        "hasExistingData": existing_data,
        "activeStep": active_step,
        "completedAt": state.get("completed_at", ""),
        "llm": {
            "configured": llm_configured,
            "hasApiKey": bool(str(primary.get("api_key") or "").strip()),
            "provider": _provider_id(primary),
            "baseUrl": str(primary.get("base_url") or ""),
            "model": str(primary.get("model") or ""),
            "reasoningEffort": str(primary.get("reasoning_effort") or ""),
            "completedAt": state["llm"].get("completed_at", ""),
        },
        "personality": personality,
    }


async def test_llm_connection(api_key: str, base_url: str, model: str) -> str:
    """Public onboarding API for a non-persisting text connectivity probe."""
    service = application_plugin_service("model_probe")
    if service is None:
        raise RuntimeError("model Plugin is not available")
    return await service.test_connection(api_key, base_url, model)


async def test_llm_vision_capability(
    api_key: str, base_url: str, model: str
) -> dict[str, Any]:
    """Public onboarding API for a non-blocking image capability probe."""
    service = application_plugin_service("model_probe")
    if service is None:
        raise RuntimeError("model Plugin is not available")
    return await service.probe_vision(api_key, base_url, model)


async def save_and_test_llm_setup(api_key: str, base_url: str, model: str) -> dict[str, Any]:
    clean_base_url = base_url.strip()
    clean_model = model.strip()
    clean_api_key = api_key.strip()
    if not clean_base_url:
        raise ValueError("LLM endpoint is required")
    if not clean_model:
        raise ValueError("Model name is required")

    preview = await test_llm_connection(clean_api_key, clean_base_url, clean_model)
    vision_capability = await test_llm_vision_capability(
        clean_api_key, clean_base_url, clean_model
    )
    _save_primary_model(
        connection_id="onboarding-openai-compatible",
        profile_id="onboarding-primary",
        connection={
            "name": "OpenAI Compatible",
            "adapter": "openai_compatible",
            "enabled": True,
            "use_proxy": False,
            "base_url": clean_base_url,
            "api_key": clean_api_key,
            "clear_api_key": not bool(clean_api_key),
            "options": {"provider_preset": "openai_compatible"},
        },
        profile={
            "model": clean_model,
            "name": clean_model,
            "enabled": True,
            "capabilities": [
                "chat",
                *(["vision"] if vision_capability.get("vision_capable") is True else []),
            ],
            "context_limit": 0,
            "ctx": "",
            "dimensions": 0,
            "reasoning_effort": "",
            "description": "",
            "price": "",
            "max_concurrency": 0,
            "options": {},
        },
    )

    state = load_onboarding_state()
    state["llm"] = {
        "completed_at": _now_iso(),
        "source": "wizard",
    }
    save_onboarding_state(state)
    return {
        "ok": True,
        "preview": preview,
        "onboarding": get_onboarding_status(),
    }


async def save_codex_oauth_setup(
    model: str,
    reasoning_effort: str = "",
) -> dict[str, Any]:
    """Persist a logged-in Codex model as the primary onboarding candidate."""
    from cyrene.core.plugin import application_plugin_service

    model_service = application_plugin_service("model_configuration")
    if model_service is None:
        raise RuntimeError("model Plugin is not available")
    clean_model = model.strip()
    clean_effort = reasoning_effort.strip().lower()
    if not clean_model:
        raise ValueError("Codex model is required")

    provider = model_service.oauth_provider()
    account_result, available_items = await asyncio.gather(
        provider.account(),
        provider.models(),
    )
    account = account_result.get("account")
    if not (
        isinstance(account, dict)
        and account.get("type") == "chatgpt"
    ):
        raise ValueError("OpenAI OAuth login is required")

    selected = next(
        (
            item
            for item in available_items
            if str(item.get("model") or item.get("id") or "").strip()
            == clean_model
        ),
        None,
    )
    if selected is None:
        raise ValueError("Selected Codex model is unavailable")

    supported_efforts = {
        str(
            item.get("reasoningEffort")
            or item.get("reasoning_effort")
            or item.get("effort")
            or ""
        ).strip().lower()
        if isinstance(item, dict)
        else str(item).strip().lower()
        for item in (selected.get("supportedReasoningEfforts") or [])
        if isinstance(item, (str, dict))
    }
    supported_efforts.discard("")
    if clean_effort and supported_efforts and clean_effort not in supported_efforts:
        raise ValueError("Selected reasoning effort is unavailable for this model")

    _save_primary_model(
        connection_id="onboarding-codex-oauth",
        profile_id="onboarding-codex-primary",
        connection={
            "name": "OpenAI Codex OAuth",
            "adapter": "codex_oauth",
            "enabled": True,
            "use_proxy": False,
            "base_url": model_service.oauth_base_url(),
            "api_key": "",
            "options": {"provider_preset": "codex_oauth"},
        },
        profile={
            "model": clean_model,
            "name": clean_model,
            "enabled": True,
            "capabilities": ["chat", "vision", "tools", "reasoning"],
            "context_limit": 0,
            "ctx": "",
            "dimensions": 0,
            "reasoning_effort": clean_effort,
            "description": "OpenAI OAuth",
            "price": "Codex quota",
            "max_concurrency": 0,
            "options": {},
        },
    )

    state = load_onboarding_state()
    state["llm"] = {
        "completed_at": _now_iso(),
        "source": "wizard",
    }
    save_onboarding_state(state)
    return {
        "ok": True,
        "onboarding": get_onboarding_status(),
    }


def _save_primary_model(
    *,
    connection_id: str,
    profile_id: str,
    connection: dict[str, Any],
    profile: dict[str, Any],
) -> None:
    """Upsert the onboarding model directly into the canonical model graph."""

    model_service = application_plugin_service("model_configuration")
    if model_service is None:
        raise RuntimeError("model Plugin is not available")
    configuration = model_service.get_model_configuration()
    connections = [dict(item) for item in configuration.get("connections") or []]
    profiles = [dict(item) for item in configuration.get("profiles") or []]

    normalized_connection = {"id": connection_id, **connection}
    if any(item.get("id") == connection_id for item in connections):
        connections = [
            normalized_connection if item.get("id") == connection_id else item
            for item in connections
        ]
    else:
        connections.append(normalized_connection)

    normalized_profile = {
        "id": profile_id,
        "connection_id": connection_id,
        **profile,
    }
    if any(item.get("id") == profile_id for item in profiles):
        profiles = [
            normalized_profile if item.get("id") == profile_id else item
            for item in profiles
        ]
    else:
        profiles.append(normalized_profile)

    routes = {
        name: list((configuration.get("routes") or {}).get(name) or [])
        for name in ("primary", "secondary", "vision", "embedding")
    }
    # Onboarding selects one definitive primary Provider. Cross-provider
    # fallback is not implicit in the new Plugin protocol.
    routes["primary"] = [profile_id]
    model_service.save_model_configuration({
        "version": configuration.get("version", 1),
        "connections": connections,
        "profiles": profiles,
        "routes": routes,
    })
