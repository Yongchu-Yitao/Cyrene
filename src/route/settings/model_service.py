"""Application service for the legacy-compatible model settings payload."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi.responses import JSONResponse

from cyrene import config
from cyrene.model_runtime import client as model_client
from cyrene.model_runtime import codex_provider
from cyrene.model_runtime.errors import format_httpx_error
from cyrene.model_runtime.pricing import price_hint
from cyrene.runtime import model_configuration, settings_store
from cyrene.runtime.model_probe_service import ModelProbePort


def _public_candidate(model: dict[str, Any], index: int) -> dict[str, Any] | None:
    identifier = str(model.get("model") or model.get("name") or model.get("id") or "").strip()
    if not identifier:
        return None
    provider = str(model.get("provider") or "openai_compatible").strip()
    raw_key = config.strip_wrapping_quotes(str(model.get("api_key") or "").strip())
    user_price = str(model.get("price") or "").strip()
    is_deepseek = "deepseek" in identifier.lower()
    return {
        "id": str(model.get("id") or f"candidate-{index + 1}").strip() or f"candidate-{index + 1}",
        "name": str(model.get("name") or identifier).strip() or identifier,
        "model": identifier,
        "provider": provider,
        "reasoning_effort": str(model.get("reasoning_effort") or "").strip().lower(),
        "supported_reasoning_efforts": ["high", "max"] if is_deepseek else [],
        "default_reasoning_effort": "high" if is_deepseek else "",
        "desc": str(model.get("desc") or "").strip(),
        "ctx": str(model.get("ctx") or "").strip(),
        "price": user_price,
        "priceHint": price_hint(identifier) if not user_price else "",
        "api_key": "" if provider == codex_provider.CODEX_PROVIDER else raw_key,
        "base_url": codex_provider.CODEX_BASE_URL if provider == codex_provider.CODEX_PROVIDER else str(model.get("base_url") or "").strip(),
        "vision_capable": model.get("vision_capable") is True,
        "vision_checked_at": str(model.get("vision_checked_at") or ""),
        "vision_check_error": str(model.get("vision_check_error") or ""),
    }


def _public_candidates(items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [candidate for index, item in enumerate(items or []) if (candidate := _public_candidate(item, index))]


def _selectable_models() -> list[dict[str, Any]]:
    result = []
    for candidate in model_configuration.selectable_model_candidates():
        identifier = str(candidate.get("model") or "").strip()
        is_deepseek = "deepseek" in identifier.lower()
        capabilities = list(candidate.get("capabilities") or [])
        result.append({
            "id": str(candidate.get("id") or "").strip(),
            "profile_id": str(candidate.get("profile_id") or candidate.get("id") or "").strip(),
            "connection_id": str(candidate.get("connection_id") or "").strip(),
            "name": str(candidate.get("name") or identifier).strip() or identifier,
            "model": identifier,
            "provider": str(candidate.get("provider") or "openai_compatible").strip(),
            "adapter": str(candidate.get("adapter") or "openai_compatible").strip(),
            "capabilities": capabilities,
            "reasoning_effort": str(candidate.get("reasoning_effort") or "").strip().lower(),
            "supported_reasoning_efforts": ["high", "max"] if is_deepseek else [],
            "default_reasoning_effort": "high" if is_deepseek else "",
            "desc": str(candidate.get("desc") or "").strip(),
            "ctx": str(candidate.get("ctx") or "").strip(),
            "ctx_limit": int(candidate.get("ctx_limit") or candidate.get("context_limit") or 0),
            "context_limit": int(candidate.get("context_limit") or candidate.get("ctx_limit") or 0),
            "price": str(candidate.get("price") or "").strip(),
            "priceHint": price_hint(identifier),
            "vision_capable": "vision" in capabilities,
        })
    return result


def _secondary_payload(raw: dict[str, Any], base_url: str) -> dict[str, Any]:
    model = str(raw.get("model") or "").strip()
    if not model:
        return {"id": "secondary", "name": "", "model": "", "desc": "", "ctx": "", "price": "", "api_key": "", "base_url": base_url or config.DEFAULT_OPENAI_BASE_URL, "ctx_limit": 0, "max_concurrency": 0}
    return {
        "id": "secondary", "name": str(raw.get("name") or model).strip(), "model": model,
        "desc": "", "ctx": "", "price": "",
        "api_key": config.strip_wrapping_quotes(str(raw.get("api_key") or "").strip()),
        "base_url": str(raw.get("base_url") or base_url or config.DEFAULT_OPENAI_BASE_URL).strip() or config.DEFAULT_OPENAI_BASE_URL,
        "ctx_limit": int(raw.get("ctx_limit") or 0),
        "max_concurrency": int(raw.get("max_concurrency") or 0),
    }


def get_model_settings() -> dict[str, Any]:
    raw_models = settings_store.get_models()
    raw_custom = settings_store.get_custom_models()
    raw_codex = settings_store.get_codex_model()
    model_source = settings_store.get_model_source()
    raw_vision = settings_store.get_vision_models()
    raw_secondary = settings_store.get_secondary_model()
    normalized = _public_candidates(raw_models)
    normalized_custom = _public_candidates(raw_custom)
    codex_items = _public_candidates([raw_codex] if raw_codex else [])
    normalized_vision = _public_candidates(raw_vision)
    active_name = str((raw_models[0] if raw_models else {}).get("model") or "").strip()
    base_url = str((raw_models[0] if raw_models else {}).get("base_url") or config.DEFAULT_OPENAI_BASE_URL).strip()
    active_id = next((str(item.get("id") or "").strip() for item in normalized if active_name in {str(item.get("model") or "").strip(), str(item.get("name") or "").strip(), str(item.get("id") or "").strip()}), str((normalized[0] if normalized else {}).get("id") or ""))
    return {
        "models": normalized, "selectable_models": _selectable_models(), "primary_candidates": normalized,
        "custom_models": normalized_custom, "codex_model": codex_items[0] if codex_items else None,
        "primary_source": model_source, "vision_models": normalized_vision,
        "vision_candidates": normalized_vision,
        "secondary_model": _secondary_payload(raw_secondary, base_url),
        "active": active_id, "active_model_name": active_name, "base_url": base_url,
    }


def _stored_candidate(model: dict[str, Any], index: int) -> dict[str, Any] | None:
    identifier = str(model.get("model") or model.get("name") or model.get("id") or "").strip()
    if not identifier:
        return None
    provider = str(model.get("provider") or "openai_compatible").strip()
    return {
        "id": str(model.get("id") or f"candidate-{index + 1}").strip() or f"candidate-{index + 1}",
        "name": identifier, "model": identifier, "provider": provider,
        "reasoning_effort": str(model.get("reasoning_effort") or "").strip().lower(),
        "desc": str(model.get("desc") or "").strip(), "ctx": str(model.get("ctx") or "").strip(),
        "price": str(model.get("price") or "").strip(),
        "api_key": "" if provider == codex_provider.CODEX_PROVIDER else config.strip_wrapping_quotes(str(model.get("api_key") or "").strip()),
        "base_url": codex_provider.CODEX_BASE_URL if provider == codex_provider.CODEX_PROVIDER else str(model.get("base_url") or config.DEFAULT_OPENAI_BASE_URL).strip() or config.DEFAULT_OPENAI_BASE_URL,
    }


def _stored_candidates(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [candidate for index, item in enumerate(items) if (candidate := _stored_candidate(item, index))]


@dataclass(slots=True)
class PreparedModels:
    primary_source: str
    custom: list[dict[str, Any]]
    codex: dict[str, Any] | None
    active: list[dict[str, Any]]
    vision: list[dict[str, Any]]
    raw_vision: Any
    raw_secondary: Any


def _prepare_models(body: dict[str, Any]) -> PreparedModels | JSONResponse:
    raw_models = body.get("models")
    raw_custom = body.get("custom_models")
    raw_codex = body.get("codex_model")
    raw_vision = body.get("vision_models")
    raw_secondary = body.get("secondary_model")
    parallel = any(key in body for key in ("custom_models", "codex_model", "primary_source"))
    if not isinstance(raw_models, list) or not raw_models:
        return JSONResponse({"error": "models must be a non-empty list"}, status_code=400)
    if not parallel and any(str(item.get("provider") or "") == "codex_oauth" for item in raw_models[1:]):
        return JSONResponse({"error": "Codex OAuth can only be used as the primary model"}, status_code=400)
    if raw_vision is not None and (not isinstance(raw_vision, list) or not raw_vision):
        return JSONResponse({"error": "vision_models must be a non-empty list"}, status_code=400)
    source = str(body.get("primary_source") or "").strip().lower()
    if source not in {"custom", "codex"}:
        source = "codex" if raw_models and str(raw_models[0].get("provider") or "") == "codex_oauth" else "custom"
    if not isinstance(raw_custom, list):
        raw_custom = [item for item in raw_models if str(item.get("provider") or "") != "codex_oauth"]
    if not isinstance(raw_codex, dict):
        raw_codex = next((item for item in raw_models if str(item.get("provider") or "") == "codex_oauth"), None)
    custom = _stored_candidates(raw_custom)
    codex_items = _stored_candidates([raw_codex] if raw_codex else [])
    codex = codex_items[0] if codex_items else None
    active = [codex] if source == "codex" and codex else custom
    vision = _stored_candidates(raw_vision if isinstance(raw_vision, list) else [])
    prepared = PreparedModels(source, custom, codex, active, vision, raw_vision, raw_secondary)
    return _validate_prepared(prepared)


def _validate_prepared(models: PreparedModels) -> PreparedModels | JSONResponse:
    if models.primary_source == "codex" and not models.codex:
        return JSONResponse({"error": "Codex model is required when OpenAI OAuth is active"}, status_code=400)
    if not models.active:
        return JSONResponse({"error": "models must contain at least one valid model"}, status_code=400)
    if any(item.get("provider") == "codex_oauth" for item in models.custom):
        return JSONResponse({"error": "Custom model candidates cannot use Codex OAuth"}, status_code=400)
    if models.codex and models.codex.get("provider") != "codex_oauth":
        return JSONResponse({"error": "Codex model must use OpenAI OAuth"}, status_code=400)
    if models.raw_vision is not None and not models.vision:
        return JSONResponse({"error": "vision_models must contain at least one valid model"}, status_code=400)
    if any(item.get("provider") == "codex_oauth" for item in models.vision):
        return JSONResponse({"error": "Codex OAuth cannot be used as a vision model"}, status_code=400)
    if isinstance(models.raw_secondary, dict) and str(models.raw_secondary.get("provider") or "") == "codex_oauth":
        return JSONResponse({"error": "Codex OAuth cannot be used as the secondary model"}, status_code=400)
    return models


async def _validate_primary(
    primary: dict[str, Any], probe: ModelProbePort
) -> JSONResponse | None:
    model = str(primary.get("model") or "").strip()
    base_url = str(primary.get("base_url") or config.DEFAULT_OPENAI_BASE_URL).strip() or config.DEFAULT_OPENAI_BASE_URL
    api_key = config.strip_wrapping_quotes(str(primary.get("api_key") or "").strip())
    try:
        if primary.get("provider") == "codex_oauth":
            provider = codex_provider.get_codex_provider()
            account_result, available = await asyncio.gather(provider.account(), provider.models())
            account = account_result.get("account")
            if not isinstance(account, dict) or account.get("type") != "chatgpt":
                raise ValueError("OpenAI OAuth login is required")
            if model not in {str(item.get("model") or item.get("id") or "").strip() for item in available}:
                raise ValueError("Selected Codex model is unavailable")
        else:
            await probe.test_connection(api_key, base_url, model)
    except httpx.TimeoutException as exc:
        return JSONResponse({"error": "upstream model timed out", "detail": str(exc)}, status_code=504)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": "upstream model request failed", "detail": format_httpx_error(exc)}, status_code=502)
    except (RuntimeError, OSError) as exc:
        return JSONResponse({"error": "Codex model validation failed", "detail": str(exc)}, status_code=503)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return None


async def _probe_vision(models: PreparedModels, probe: ModelProbePort) -> None:
    checks: dict[tuple[str, str, str], dict[str, Any]] = {}
    for candidate in [*models.custom, *models.vision]:
        if candidate.get("provider") == "codex_oauth":
            candidate.update({"vision_capable": False, "vision_checked_at": "", "vision_check_error": "Codex OAuth image input is not supported by this adapter"})
            continue
        key = (str(candidate.get("model") or ""), str(candidate.get("base_url") or "").rstrip("/"), str(candidate.get("api_key") or ""))
        capability = checks.get(key)
        if capability is None:
            capability = await probe.probe_vision(key[2], key[1], key[0])
            checks[key] = capability
        candidate.update(capability)


def _persist_models(models: PreparedModels) -> None:
    primary = models.active[0]
    settings_store.save_custom_models(models.custom)
    if models.codex:
        settings_store.save_codex_model(models.codex)
    settings_store.save_model_source(models.primary_source)
    if models.raw_vision is not None:
        settings_store.save_vision_models(models.vision)
    if isinstance(models.raw_secondary, dict):
        settings_store.save_secondary_model(models.raw_secondary)
    settings_store.save_models(models.active)
    env = {"OPENAI_MODEL": str(primary.get("model") or "").strip()}
    if primary.get("provider") != "codex_oauth":
        env.update({"OPENAI_BASE_URL": str(primary.get("base_url") or config.DEFAULT_OPENAI_BASE_URL).strip() or config.DEFAULT_OPENAI_BASE_URL, "OPENAI_API_KEY": config.strip_wrapping_quotes(str(primary.get("api_key") or "").strip())})
    config.write_env_keys(env)
    model_client.invalidate_model_configuration()


def _with_price_hints(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**item, "priceHint": price_hint(str(item.get("model") or "")) if not str(item.get("price") or "").strip() else ""} for item in items]


def _update_response(models: PreparedModels) -> dict[str, Any]:
    primary = models.active[0]
    response_models = _with_price_hints(models.active)
    response_custom = _with_price_hints(models.custom)
    response_codex = _with_price_hints([models.codex])[0] if models.codex else None
    response_vision = _with_price_hints(models.vision)
    base_url = str(primary.get("base_url") or config.DEFAULT_OPENAI_BASE_URL).strip() or config.DEFAULT_OPENAI_BASE_URL
    return {
        "ok": True, "models": response_models, "primary_candidates": response_models,
        "custom_models": response_custom, "codex_model": response_codex,
        "primary_source": models.primary_source,
        "vision_models": response_vision if models.raw_vision is not None else None,
        "vision_candidates": response_vision if models.raw_vision is not None else None,
        "secondary_model": _secondary_payload(settings_store.get_secondary_model(), config.DEFAULT_OPENAI_BASE_URL),
        "active": str(primary.get("id") or "candidate-1"),
        "active_model_name": str(primary.get("model") or "").strip(), "base_url": base_url,
    }


class ModelSettingsApplicationService:
    def __init__(self, probe: ModelProbePort):
        self.probe = probe

    def get_settings(self) -> dict[str, Any]:
        return get_model_settings()

    async def update_settings(self, body: dict[str, Any]) -> dict[str, Any] | JSONResponse:
        prepared = _prepare_models(body)
        if isinstance(prepared, JSONResponse):
            return prepared
        validation_error = await _validate_primary(prepared.active[0], self.probe)
        if validation_error is not None:
            return validation_error
        await _probe_vision(prepared, self.probe)
        _persist_models(prepared)
        return _update_response(prepared)
