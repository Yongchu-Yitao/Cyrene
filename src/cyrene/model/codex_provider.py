"""Codex OAuth provider backed by OpenAI's pinned Codex SDK runtime.

The app-server owns browser login, credential refresh, model discovery, and
ChatGPT-plan rate limits. Cyrene never reads ``~/.codex/auth.json`` or handles
OAuth access/refresh tokens directly.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import tempfile
import time
import uuid
from typing import Any, Awaitable, Callable

from openai_codex import CodexConfig
from openai_codex.async_client import AsyncCodexClient
from openai_codex.generated.v2_all import (
    GetAccountRateLimitsResponse,
    ModelProviderCapabilitiesReadResponse,
)
from pydantic import BaseModel

from cyrene.core.plugin.validation import (
    PluginInputValidationError,
    PluginSchemaError,
    normalize_plugin_arguments,
    validate_plugin_arguments,
)
from cyrene.model import codex_cli

logger = logging.getLogger(__name__)

CODEX_PROVIDER = "codex_oauth"
CODEX_BASE_URL = "codex://oauth"
_FIRST_UPSTREAM_SIGNAL_TIMEOUT_SECONDS = 35.0
_TRANSPORT_ERROR_KEYS = frozenset(
    {
        "httpConnectionFailed",
        "responseStreamConnectionFailed",
        "responseStreamDisconnected",
        "responseTooManyFailedAttempts",
    }
)
CODEX_QUOTA_EXHAUSTED = "quota_exhausted"
CODEX_AUTHENTICATION_EXPIRED = "authentication_expired"
CODEX_MODEL_UNAVAILABLE = "model_unavailable"
CODEX_CLI_REQUIRED = "cli_required"
_ISOLATED_CODEX_WORKSPACE: tempfile.TemporaryDirectory[str] | None = None


class CodexTransportError(RuntimeError):
    """A Codex upstream transport failed before a usable model response."""


class CodexProtocolError(RuntimeError):
    """Codex returned an invalid Cyrene action envelope."""


class CodexAvailabilityError(RuntimeError):
    """A user-actionable Codex availability failure."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = str(kind or "")


def _model_dump(value: Any) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        dumped = value.model_dump(
            by_alias=True,
            exclude_none=True,
            mode="json",
        )
        return dumped if isinstance(dumped, dict) else {}
    return value if isinstance(value, dict) else {}


def _normalized_effort(value: str) -> str:
    effort = str(value or "").strip().lower()
    return (
        effort
        if effort
        in {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
        else ""
    )


def _transport_error_kind(error: dict[str, Any]) -> str:
    info = error.get("codexErrorInfo")
    if not isinstance(info, dict):
        return ""
    return next((key for key in _TRANSPORT_ERROR_KEYS if key in info), "")


def _codex_error_info(error: dict[str, Any]) -> Any:
    return (
        error.get("codexErrorInfo")
        if "codexErrorInfo" in error
        else error.get("codex_error_info")
    )


def _codex_http_status(info: Any) -> int | None:
    if not isinstance(info, dict):
        return None
    for value in info.values():
        if not isinstance(value, dict):
            continue
        status = value.get("httpStatusCode")
        if status is None:
            status = value.get("http_status_code")
        try:
            return int(status) if status is not None else None
        except (TypeError, ValueError):
            continue
    return None


def codex_availability_error(
    error_or_exception: dict[str, Any] | BaseException,
    *,
    message: str = "",
) -> CodexAvailabilityError | None:
    """Normalize the SDK's structured and legacy textual availability errors."""
    if isinstance(error_or_exception, CodexAvailabilityError):
        return error_or_exception

    if isinstance(error_or_exception, dict):
        error = error_or_exception
    else:
        raw_data = getattr(error_or_exception, "data", None)
        error = raw_data if isinstance(raw_data, dict) else {}
        if not message:
            message = str(
                getattr(error_or_exception, "message", "")
                or error_or_exception
            )

    info = _codex_error_info(error)
    normalized_info = str(info or "").strip().lower()
    detail = str(message or error.get("message") or "").strip()
    lowered = detail.lower()
    status = _codex_http_status(info)

    if normalized_info in {
        "usagelimitexceeded",
        "sessionbudgetexceeded",
    } or any(
        token in lowered
        for token in (
            "usage limit",
            "quota exceeded",
            "quota exhausted",
            "insufficient_quota",
            "rate limit reached",
            "credits depleted",
            "credit balance",
            "no credit",
        )
    ):
        return CodexAvailabilityError(
            CODEX_QUOTA_EXHAUSTED,
            detail or "Codex quota is exhausted",
        )

    model_error = (
        normalized_info
        in {
            "modelnotfound",
            "modelunavailable",
            "unsupportedmodel",
        }
        or "model_not_found" in lowered
        or "unsupported model" in lowered
        or "unknown model" in lowered
        or "invalid model" in lowered
        or (
            "model" in lowered
            and any(
                token in lowered
                for token in (
                    "not found",
                    "not available",
                    "unavailable",
                    "no longer available",
                    "does not exist",
                    "is not supported",
                    "access denied",
                    "permission",
                )
            )
        )
    )
    if model_error:
        return CodexAvailabilityError(
            CODEX_MODEL_UNAVAILABLE,
            detail or "The selected Codex model is unavailable",
        )

    if normalized_info == "unauthorized" or status == 401 or any(
        token in lowered
        for token in (
            "unauthorized",
            "authentication expired",
            "token expired",
            "refresh token",
            "please log in",
            "login required",
            "not logged in",
        )
    ):
        return CodexAvailabilityError(
            CODEX_AUTHENTICATION_EXPIRED,
            detail or "Codex authentication has expired",
        )

    return None


def codex_error_should_cooldown(error: BaseException) -> bool:
    """Whether a Codex failure indicates a temporarily unusable candidate."""
    if isinstance(error, CodexProtocolError):
        return False
    availability = codex_availability_error(error)
    if availability is not None:
        return availability.kind in {
            CODEX_QUOTA_EXHAUSTED,
            CODEX_MODEL_UNAVAILABLE,
        }
    return True


def _first_signal_timeout(request_timeout: float) -> float:
    raw = str(
        os.environ.get(
            "CYRENE_CODEX_FIRST_SIGNAL_TIMEOUT_SECONDS",
            _FIRST_UPSTREAM_SIGNAL_TIMEOUT_SECONDS,
        )
    ).strip()
    try:
        configured = float(raw)
    except ValueError:
        configured = _FIRST_UPSTREAM_SIGNAL_TIMEOUT_SECONDS
    return min(float(request_timeout), max(5.0, configured))


def _codex_isolation_workspace() -> Path:
    """Return an empty provider-owned cwd with no project instructions."""
    global _ISOLATED_CODEX_WORKSPACE
    if _ISOLATED_CODEX_WORKSPACE is None:
        _ISOLATED_CODEX_WORKSPACE = tempfile.TemporaryDirectory(
            prefix="cyrene-codex-provider-"
        )
        instruction_file = (
            Path(_ISOLATED_CODEX_WORKSPACE.name) / "CYRENE_PROVIDER.md"
        )
        instruction_file.write_text(
            "This directory belongs to Cyrene's isolated Codex provider.\n",
            encoding="utf-8",
        )
    return Path(_ISOLATED_CODEX_WORKSPACE.name)


def _disabled_host_skills_override() -> str:
    """Build a non-persistent command-line override for every host skill."""
    codex_home = Path(
        os.environ.get("CODEX_HOME") or (Path.home() / ".codex")
    ).expanduser()
    skills_root = codex_home / "skills"
    if not skills_root.is_dir():
        return ""
    skill_files = sorted(
        {
            path.resolve()
            for path in skills_root.rglob("SKILL.md")
            if path.is_file()
        },
        key=str,
    )
    if not skill_files:
        return ""
    entries = ",".join(
        f"{{path={json.dumps(str(path))},enabled=false}}"
        for path in skill_files
    )
    return f"skills.config=[{entries}]"


def _codex_sdk_config(cli_path: Path) -> CodexConfig:
    isolation_root = _codex_isolation_workspace()
    overrides = [
        "features.respect_system_proxy=true",
        # This app-server process is a model transport, not an agent runtime.
        # Disable every Codex-hosted action surface and instruction bundle.
        "features.plugins=false",
        "features.apps=false",
        "features.shell_tool=false",
        "features.unified_exec=false",
        "features.browser_use=false",
        "features.computer_use=false",
        "features.image_generation=false",
        "features.multi_agent=false",
        "tools.web_search=false",
        "include_permissions_instructions=false",
        "include_apps_instructions=false",
        "include_collaboration_mode_instructions=false",
        "include_environment_context=false",
        (
            "model_instructions_file="
            + json.dumps(str(isolation_root / "CYRENE_PROVIDER.md"))
        ),
    ]
    skills_override = _disabled_host_skills_override()
    if skills_override:
        overrides.append(skills_override)
    return CodexConfig(
        # Cyrene owns the Codex runtime: a downloaded, verified binary whose
        # version is managed independently of PATH/ChatGPT.app installs.
        codex_bin=str(cli_path),
        config_overrides=tuple(overrides),
        cwd=str(isolation_root),
        client_name="cyrene",
        client_title="Cyrene",
        client_version="1",
        experimental_api=True,
    )


def _codex_image_sdk_config(cli_path: Path) -> CodexConfig:
    """Return an isolated SDK runtime that may use only image generation."""
    isolation_root = _codex_isolation_workspace()
    overrides = [
        "features.respect_system_proxy=true",
        "features.plugins=false",
        "features.apps=false",
        "features.shell_tool=false",
        "features.unified_exec=false",
        "features.browser_use=false",
        "features.computer_use=false",
        "features.image_generation=true",
        "features.multi_agent=false",
        "tools.view_image=false",
        "tools.web_search=false",
        "include_permissions_instructions=false",
        "include_apps_instructions=false",
        "include_collaboration_mode_instructions=false",
        "include_environment_context=false",
        (
            "model_instructions_file="
            + json.dumps(str(isolation_root / "CYRENE_PROVIDER.md"))
        ),
    ]
    skills_override = _disabled_host_skills_override()
    if skills_override:
        overrides.append(skills_override)
    return CodexConfig(
        codex_bin=str(cli_path),
        config_overrides=tuple(overrides),
        cwd=str(isolation_root),
        client_name="cyrene-image-generation",
        client_title="Cyrene Image Generation",
        client_version="1",
        experimental_api=True,
    )


def _require_cli() -> Path:
    """Return the installed Codex CLI or raise a user-actionable error."""
    try:
        return codex_cli.ensure_cli()
    except codex_cli.CodexCliMissingError as exc:
        raise CodexAvailabilityError(
            CODEX_CLI_REQUIRED,
            str(exc) or "Codex CLI runtime is not downloaded",
        ) from exc


# Explicit app-server/SDK version-clash wording.  The SDK surfaces app-server
# JSON-RPC errors as "JSON-RPC error {code}: {message}"; genuine clashes name
# the protocol version, the method surface, or the app-server/SDK pair.  Bare
# words like "protocol" or "incompatible" are NOT enough: transport errors and
# TLS alerts ("tlsv1 alert protocol version") also use them.
_VERSION_CLASH_TOKENS = (
    "protocol version",
    "protocol mismatch",
    "app-server version",
    "app-server protocol",
    "version mismatch",
    "incompatible sdk",
    "method not found",
    "unknown method",
)
# Transport-layer noise that shares wording with clash messages; never treated
# as an SDK/CLI version clash.
_TRANSPORT_NOISE_TOKENS = (
    "tls",
    "ssl",
    "handshake",
    "certificate",
    "proxy",
    "connection",
    "dns",
)


def _is_cli_protocol_mismatch(error: BaseException) -> bool:
    """Whether a failed spawn/initialize looks like an SDK/CLI version clash."""
    lowered = str(error).lower()
    if any(token in lowered for token in _TRANSPORT_NOISE_TOKENS):
        return False
    return any(token in lowered for token in _VERSION_CLASH_TOKENS)


async def _recover_with_pinned_cli(error: BaseException) -> bool:
    """Swap the installed CLI for the SDK-pinned version and report success.

    Only invoked when the installed runtime is a newer (unpinned) version and
    the SDK/CLI exchange failed in a way that suggests a protocol clash.  A
    failed fallback download keeps the original error, never masks it.
    """
    if not _is_cli_protocol_mismatch(error):
        return False
    pinned = codex_cli.sdk_pinned_version()
    installed = codex_cli.installed_version()
    if not pinned or installed == pinned:
        return False
    logger.warning(
        "Codex CLI %s spoke an incompatible protocol to the openai-codex "
        "SDK %s; installing the pinned runtime %s",
        installed or "?",
        pinned,
        pinned,
    )
    try:
        await codex_cli.download_and_wait(pinned)
        return True
    except Exception as exc:
        logger.warning("Codex CLI pinned-runtime fallback download failed: %s", exc)
        return False


class CodexAppServer:
    """Async facade over OpenAI's official pinned Codex Python SDK."""

    def __init__(self) -> None:
        self._client: AsyncCodexClient | None = None
        self._client_loop: asyncio.AbstractEventLoop | None = None
        self._start_lock = asyncio.Lock()
        self._image_client: AsyncCodexClient | None = None
        self._image_client_loop: asyncio.AbstractEventLoop | None = None
        self._image_start_lock = asyncio.Lock()
        self._limits_cache: tuple[float, dict[str, Any]] | None = None
        self._limits_refresh_task: asyncio.Task[dict[str, Any]] | None = None
        # Set when the latest _start_client attempt failed with an installed
        # runtime; snapshot() reports cli.broken to offer a forced reinstall.
        self._client_start_error: str | None = None

    async def _start_client(
        self,
        config_factory: Callable[[Path], CodexConfig],
    ) -> AsyncCodexClient:
        """Spawn the Codex app-server with a verified CLI.

        When the installed (latest) CLI fails to speak the SDK's protocol, the
        SDK-pinned runtime is downloaded and the spawn retried exactly once.
        Callers run this outside _start_lock so the fallback download never
        holds the lock across its multi-minute duration.
        """
        cli_path = _require_cli()
        for attempt in range(2):
            client = AsyncCodexClient(config_factory(cli_path))
            try:
                await asyncio.wait_for(client.start(), timeout=15)
                await asyncio.wait_for(client.initialize(), timeout=15)
            except BaseException as exc:
                await client.close()
                if attempt == 0 and await _recover_with_pinned_cli(exc):
                    cli_path = codex_cli.installed_cli_path() or cli_path
                    continue
                raise
            return client

    async def _ensure_started(self) -> None:
        current_loop = asyncio.get_running_loop()
        if self._client is not None and self._client_loop is current_loop:
            return
        if self._client is not None:
            await self.close()
        # A first-run or pinned-fallback download can take minutes. Probing
        # and downloading happen outside _start_lock; concurrent starts join
        # the single in-flight download, and the lock only guards assignment
        # (the losing client is discarded).
        await codex_cli.wait_for_inflight_download()
        try:
            client = await self._start_client(_codex_sdk_config)
        except Exception as exc:
            self._client_start_error = str(exc)
            raise
        self._client_start_error = None
        async with self._start_lock:
            if self._client is not None and self._client_loop is current_loop:
                await client.close()
                return
            self._client = client
            self._client_loop = current_loop
            logger.info("Codex SDK runtime started [system_proxy=true]")

    async def _ready_client(self) -> AsyncCodexClient:
        await self._ensure_started()
        if self._client is None:
            raise RuntimeError("Codex SDK client is unavailable")
        return self._client

    async def _ensure_image_started(self) -> None:
        current_loop = asyncio.get_running_loop()
        if (
            self._image_client is not None
            and self._image_client_loop is current_loop
        ):
            return
        if self._image_client is not None:
            await self._close_image_client()
        # Same lock discipline as _ensure_started: probing and any pinned
        # fallback download run outside _image_start_lock.
        await codex_cli.wait_for_inflight_download()
        try:
            client = await self._start_client(_codex_image_sdk_config)
        except Exception as exc:
            self._client_start_error = str(exc)
            raise
        self._client_start_error = None
        async with self._image_start_lock:
            if (
                self._image_client is not None
                and self._image_client_loop is current_loop
            ):
                await client.close()
                return
            self._image_client = client
            self._image_client_loop = current_loop
            logger.info("Codex image-generation SDK runtime started")

    async def _ready_image_client(self) -> AsyncCodexClient:
        await self._ensure_image_started()
        if self._image_client is None:
            raise RuntimeError("Codex image-generation SDK client is unavailable")
        return self._image_client

    async def _close_image_client(self) -> None:
        client = self._image_client
        self._image_client = None
        self._image_client_loop = None
        if client is not None:
            try:
                await client.close()
            except RuntimeError:
                logger.debug(
                    "Codex image-generation SDK client close crossed event loops"
                )

    async def account(self, *, refresh: bool = False) -> dict[str, Any]:
        client = await self._ready_client()
        result = await asyncio.wait_for(
            client.account_read({"refreshToken": bool(refresh)}),
            timeout=20,
        )
        return _model_dump(result)

    async def start_login(self) -> dict[str, Any]:
        client = await self._ready_client()
        result = await asyncio.wait_for(
            client.account_login_start(
                {
                    "type": "chatgpt",
                    "appBrand": "codex",
                    "codexStreamlinedLogin": True,
                    "useHostedLoginSuccessPage": True,
                }
            ),
            timeout=20,
        )
        return _model_dump(result)

    async def logout(self) -> None:
        client = await self._ready_client()
        await asyncio.wait_for(client.account_logout(), timeout=20)

    async def models(self) -> list[dict[str, Any]]:
        client = await self._ready_client()
        result = await asyncio.wait_for(
            client.model_list(include_hidden=False),
            timeout=30,
        )
        payload = _model_dump(result)
        return [
            item
            for item in payload.get("data") or []
            if isinstance(item, dict) and not item.get("hidden")
        ]

    async def image_generation_capability(self) -> bool:
        """Whether the connected account/provider exposes image generation."""
        client = await self._ready_image_client()
        result = await asyncio.wait_for(
            client.request(
                "modelProvider/capabilities/read",
                {},
                response_model=ModelProviderCapabilitiesReadResponse,
            ),
            timeout=30,
        )
        return bool(
            _model_dump(result).get("imageGeneration")
            or getattr(result, "image_generation", False)
        )

    async def generate_image(
        self,
        *,
        prompt: str,
        model: str,
        size: str = "1024x1024",
        quality: str = "medium",
        output_format: str = "png",
        timeout: float = 180,
    ) -> dict[str, Any]:
        """Run one isolated Codex turn that may only generate an image."""
        client = await self._ready_image_client()
        thread_result = await asyncio.wait_for(
            client.thread_start(
                {
                    "model": model,
                    "baseInstructions": (
                        "Generate exactly one image that satisfies the user's "
                        "request. Use the image-generation capability. Do not "
                        "run shell commands, browse, use plugins, or perform "
                        "any unrelated action."
                    ),
                    "developerInstructions": (
                        "This thread is an isolated Cyrene image-generation "
                        "backend. The only permitted hosted action is image "
                        "generation."
                    ),
                    "ephemeral": True,
                    "approvalPolicy": "never",
                    "sandbox": "read-only",
                    "cwd": str(_codex_isolation_workspace()),
                }
            ),
            timeout=min(timeout, 30),
        )
        thread = _model_dump(thread_result).get("thread") or {}
        thread_id = str(thread.get("id") or "")
        if not thread_id:
            raise RuntimeError("Codex did not create an image-generation thread")

        request_text = (
            f"{prompt}\n\n"
            "Output requirements:\n"
            f"- size: {size}\n"
            f"- quality: {quality}\n"
            f"- format: {output_format}\n"
            "- generate exactly one image"
        )
        turn_id = ""
        notification_task: asyncio.Task[Any] | None = None

        async def interrupt_turn() -> None:
            if not turn_id:
                return
            try:
                await asyncio.wait_for(
                    client.turn_interrupt(thread_id, turn_id),
                    timeout=5,
                )
            except Exception:
                logger.debug(
                    "Failed to interrupt Codex image-generation turn %s",
                    turn_id,
                    exc_info=True,
                )

        async def settle_notification_wait() -> None:
            nonlocal notification_task
            task = notification_task
            notification_task = None
            if task is None:
                return
            if not task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=3)
                except TimeoutError:
                    if isinstance(client, AsyncCodexClient):
                        if self._image_client is client:
                            self._image_client = None
                            self._image_client_loop = None
                        try:
                            await client.close()
                        except Exception:
                            logger.debug(
                                "Failed to retire a stalled Codex image client",
                                exc_info=True,
                            )
                    else:
                        task.cancel()
                except BaseException:
                    pass
            if task.done():
                try:
                    task.result()
                except BaseException:
                    pass

        try:
            turn_result = await asyncio.wait_for(
                client.turn_start(
                    thread_id,
                    [{"type": "text", "text": request_text}],
                    {
                        "threadId": thread_id,
                        "input": [{"type": "text", "text": request_text}],
                        "model": model,
                        "summary": "none",
                    },
                ),
                timeout=min(timeout, 30),
            )
            turn = _model_dump(turn_result).get("turn") or {}
            turn_id = str(turn.get("id") or "")
            if not turn_id:
                raise RuntimeError(
                    "Codex did not start an image-generation turn"
                )

            generated: dict[str, Any] | None = None
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    await interrupt_turn()
                    raise TimeoutError("Codex image generation timed out")
                notification_task = asyncio.create_task(
                    client.next_turn_notification(turn_id)
                )
                completed, _ = await asyncio.wait(
                    {notification_task},
                    timeout=remaining,
                )
                if not completed:
                    await interrupt_turn()
                    await settle_notification_wait()
                    raise TimeoutError("Codex image generation timed out")
                notification = notification_task.result()
                notification_task = None

                method = str(notification.method or "")
                params = _model_dump(notification.payload)
                if method == "error":
                    error = params.get("error") or {}
                    message = str(
                        (error if isinstance(error, dict) else {}).get("message")
                        or "Codex image generation failed"
                    )
                    availability_error = codex_availability_error(
                        error if isinstance(error, dict) else {},
                        message=message,
                    )
                    if availability_error is not None:
                        raise availability_error
                    if not params.get("willRetry"):
                        raise RuntimeError(message)
                elif method == "item/completed":
                    item = params.get("item") or {}
                    if isinstance(item, dict) and item.get("type") in {
                        "imageGeneration",
                        "image_generation_call",
                    }:
                        generated = dict(item)
                elif method == "turn/completed":
                    completed_turn = params.get("turn") or {}
                    if str(completed_turn.get("id") or "") != turn_id:
                        continue
                    if completed_turn.get("status") == "failed":
                        error = completed_turn.get("error") or {}
                        message = str(
                            (error if isinstance(error, dict) else {}).get(
                                "message"
                            )
                            or "Codex image generation failed"
                        )
                        raise RuntimeError(message)
                    for item in completed_turn.get("items") or []:
                        if (
                            isinstance(item, dict)
                            and item.get("type")
                            in {"imageGeneration", "image_generation_call"}
                        ):
                            generated = dict(item)
                    break
            if not generated:
                raise RuntimeError(
                    "Codex completed without an image-generation result"
                )
            if str(generated.get("status") or "completed") not in {
                "completed",
                "success",
            }:
                raise RuntimeError(
                    "Codex returned an incomplete image-generation result"
                )
            return generated
        except asyncio.CancelledError:
            await interrupt_turn()
            await settle_notification_wait()
            raise
        finally:
            await settle_notification_wait()
            if turn_id:
                client.unregister_turn_notifications(turn_id)

    async def rate_limits(self) -> dict[str, Any]:
        client = await self._ready_client()
        result = await asyncio.wait_for(
            client.request(
                "account/rateLimits/read",
                {},
                response_model=GetAccountRateLimitsResponse,
            ),
            timeout=30,
        )
        normalized = _model_dump(result)
        self._limits_cache = (time.monotonic(), normalized)
        return normalized

    async def rate_limits_cached(self, *, max_age: float = 30) -> dict[str, Any]:
        cached = self._limits_cache
        if cached is not None and time.monotonic() - cached[0] <= max_age:
            return cached[1]
        return await self.rate_limits()

    async def rate_limits_stale_first(
        self, *, refresh_after: float = 30
    ) -> dict[str, Any]:
        """Return cached limits immediately and refresh an old snapshot in the background."""
        cached = self._limits_cache
        if cached is None:
            return await self.rate_limits()
        if time.monotonic() - cached[0] > refresh_after:
            self._schedule_rate_limits_refresh()
        return cached[1]

    def _schedule_rate_limits_refresh(self) -> None:
        task = self._limits_refresh_task
        if task is not None and not task.done():
            return
        task = asyncio.create_task(self.rate_limits())
        self._limits_refresh_task = task

        def _settled(done: asyncio.Task[dict[str, Any]]) -> None:
            if self._limits_refresh_task is done:
                self._limits_refresh_task = None
            try:
                done.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                # Quota telemetry is advisory; the provider still owns the hard
                # enforcement path for the model request.
                logger.warning(
                    "Codex quota refresh failed in the background: %s",
                    exc,
                )

        task.add_done_callback(_settled)

    @staticmethod
    def _quota_available_from_limits(limits: dict[str, Any]) -> bool:
        buckets = limits.get("rateLimitsByLimitId") or {}
        if isinstance(buckets, dict):
            codex_limit = buckets.get("codex")
            candidates = [codex_limit] if isinstance(codex_limit, dict) else []
        else:
            candidates = []
        if not candidates and isinstance(limits.get("rateLimits"), dict):
            candidates = [limits["rateLimits"]]
        for bucket in candidates:
            if bucket.get("rateLimitReachedType"):
                return False
            windows = [bucket.get("primary"), bucket.get("secondary")]
            if any(
                isinstance(window, dict)
                and float(window.get("usedPercent") or 0) >= 100
                for window in windows
                if window is not None
            ):
                return False
        return True

    async def quota_available(self) -> bool:
        """Return quota state without blocking on refresh when stale data exists."""
        cached = self._limits_cache
        if cached is not None:
            age = time.monotonic() - cached[0]
            if age > 30:
                self._schedule_rate_limits_refresh()
            return self._quota_available_from_limits(cached[1])
        try:
            limits = await self.rate_limits_cached()
        except (RuntimeError, OSError, TimeoutError) as exc:
            # Quota telemetry is advisory. A transient failure from the usage
            # endpoint must not make a healthy Codex model look unavailable.
            # Prefer even a stale snapshot when one exists; otherwise let the
            # model request proceed and allow the provider to enforce its own
            # hard quota.
            cached = self._limits_cache
            if cached is None:
                logger.warning(
                    "Codex quota check unavailable; proceeding without a local "
                    "quota gate: %s",
                    exc,
                )
                return True
            limits = cached[1]
            logger.warning(
                "Codex quota check unavailable; using the last cached limits: %s",
                exc,
            )
        return self._quota_available_from_limits(limits)

    async def snapshot(
        self,
        *,
        include_limits: bool = True,
        include_models: bool = True,
        stale_limits: bool = False,
    ) -> dict[str, Any]:
        try:
            account = await self.account()
        except CodexAvailabilityError as exc:
            if exc.kind == CODEX_CLI_REQUIRED:
                return {
                    "available": False,
                    "connected": False,
                    "models": [],
                    "limits": {},
                    "cli": codex_cli.status(),
                    "error": str(exc),
                }
            raise
        except Exception:
            # An installed CLI that fails to start (corrupt binary, unfixable
            # protocol clash) is a UI dead end without a reinstall path.
            # Report it as broken; the settings UI reads cli.broken and offers
            # a forced re-download (POST .../cli/download with force=true).
            start_error = self._client_start_error
            if start_error is not None:
                cli_status = codex_cli.status()
                return {
                    "available": False,
                    "connected": False,
                    "models": [],
                    "limits": {},
                    "cli": {
                        "installed": True,
                        "broken": True,
                        "version": cli_status.get("version") or "",
                        "error": start_error,
                    },
                    "error": start_error,
                }
            raise
        account_data = account.get("account")
        connected = (
            isinstance(account_data, dict)
            and account_data.get("type") == "chatgpt"
        )
        models: list[dict[str, Any]] = []
        limits: dict[str, Any] = {}
        errors: dict[str, str] = {}
        if connected:
            model_request = self.models() if include_models else None
            limit_request = (
                self.rate_limits_stale_first()
                if include_limits and stale_limits
                else self.rate_limits()
                if include_limits
                else None
            )
            requests = [
                request
                for request in (model_request, limit_request)
                if request is not None
            ]
            results = (
                await asyncio.gather(*requests, return_exceptions=True)
                if requests
                else []
            )
            result_index = 0
            if include_models:
                model_result = results[result_index]
                result_index += 1
                if isinstance(model_result, BaseException):
                    errors["models"] = str(model_result)
                else:
                    models = model_result
            if include_limits:
                limit_result = results[result_index]
                if isinstance(limit_result, BaseException):
                    errors["limits"] = str(limit_result)
                else:
                    limits = limit_result
        snapshot = {
            "available": True,
            "connected": connected,
            "account": account_data if isinstance(account_data, dict) else None,
            "models": models,
            "limits": limits,
        }
        if errors:
            snapshot["errors"] = errors
        return snapshot

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str,
        timeout: float,
        phase: str = "",
        reasoning_effort: str = "",
        stream_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        transport_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """Run one provider turn without exposing Codex's own host tools.

        Cyrene's tool loop remains authoritative. When tools are available,
        Codex returns a schema-constrained action envelope which this adapter
        converts to the same OpenAI-style tool calls used by other providers.
        """
        client = await self._ready_client()
        request_material = provider_request_cache_material(
            messages=messages,
            tools=tools,
            model=model,
            phase=phase,
            reasoning_effort=reasoning_effort,
        )
        action_tools = request_material["action_tools"]
        action_schema = request_material["action_schema"]
        effort = request_material["effort"]
        thread_result = await asyncio.wait_for(
            client.thread_start(request_material["thread_params"]),
            timeout=min(timeout, 30),
        )
        thread = _model_dump(thread_result).get("thread") or {}
        thread_id = str(thread.get("id") or "")
        if not thread_id:
            raise RuntimeError("Codex did not create a provider thread")

        turn_id = ""
        notification_task: asyncio.Task[Any] | None = None

        async def emit_transport(
            status: str,
            *,
            message: str = "",
            kind: str = "",
            will_retry: bool = False,
        ) -> None:
            event = {
                "type": "provider_transport",
                "provider": CODEX_PROVIDER,
                "transport": "responses",
                "status": status,
                "message": message,
                "error_kind": kind,
                "will_retry": bool(will_retry),
                "thread_id": thread_id,
                "turn_id": turn_id,
                "reasoning_effort": effort,
            }
            if transport_callback is not None:
                try:
                    await transport_callback(event)
                except Exception:
                    logger.debug(
                        "Failed to publish Codex transport telemetry",
                        exc_info=True,
                    )

        async def interrupt_turn() -> None:
            if not turn_id:
                return
            try:
                await asyncio.wait_for(
                    client.turn_interrupt(thread_id, turn_id),
                    timeout=3,
                )
            except Exception:
                logger.debug(
                    "Failed to interrupt Codex provider turn %s",
                    turn_id,
                    exc_info=True,
                )

        async def settle_notification_wait() -> None:
            """Wake the SDK's blocking notification worker before loop shutdown."""
            nonlocal notification_task
            task = notification_task
            notification_task = None
            if task is None:
                return
            if not task.done():
                try:
                    # An interrupt normally produces turn/completed, which
                    # releases the SDK's thread-backed queue wait.
                    await asyncio.wait_for(asyncio.shield(task), timeout=3)
                except TimeoutError:
                    # The transport itself may be gone, so no completion can
                    # arrive. Closing the official client terminates app-server
                    # and makes its router wake every blocked waiter.
                    if isinstance(client, AsyncCodexClient):
                        if self._client is client:
                            self._client = None
                            self._client_loop = None
                        try:
                            await client.close()
                        except Exception:
                            logger.debug(
                                "Failed to retire a stalled Codex SDK client",
                                exc_info=True,
                            )
                    else:
                        task.cancel()
                except BaseException:
                    pass
            if not task.done():
                task.cancel()
            try:
                await task
            except BaseException:
                pass

        try:
            turn_input = request_material["turn_input"]
            turn_params: dict[str, Any] = {
                **request_material["turn_params"],
                "threadId": thread_id,
            }
            logger.info(
                "Starting Codex turn [model=%s effort=%s proxy=system]",
                model,
                effort or "model-default",
            )
            turn_result = await asyncio.wait_for(
                client.turn_start(
                    thread_id,
                    turn_input,
                    turn_params,
                ),
                timeout=min(timeout, 30),
            )
            turn = _model_dump(turn_result).get("turn") or {}
            turn_id = str(turn.get("id") or "")
            if not turn_id:
                raise RuntimeError("Codex did not start a provider turn")

            text_parts: list[str] = []
            reasoning_parts: list[str] = []
            final_text = ""
            usage: dict[str, Any] = {}
            reasoning_started = False
            upstream_signal_seen = False
            if stream_callback and action_schema is None:
                await stream_callback({"type": "reply_start"})
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            first_signal_timeout = _first_signal_timeout(timeout)
            first_signal_deadline = loop.time() + first_signal_timeout
            while True:
                now = loop.time()
                remaining = deadline - now
                if remaining <= 0:
                    await interrupt_turn()
                    await emit_transport(
                        "timed_out",
                        message=f"Codex request exceeded {timeout:.0f}s",
                    )
                    raise TimeoutError("Codex model request timed out")
                notification_timeout = remaining
                if not upstream_signal_seen:
                    first_signal_remaining = first_signal_deadline - now
                    if first_signal_remaining <= 0:
                        await interrupt_turn()
                        message = (
                            "Codex produced no upstream model signal within "
                            f"{first_signal_timeout:.0f}s"
                        )
                        logger.warning(message)
                        await emit_transport("timed_out", message=message)
                        raise CodexTransportError(message)
                    notification_timeout = min(
                        notification_timeout,
                        first_signal_remaining,
                    )
                notification_task = asyncio.create_task(
                    client.next_turn_notification(turn_id)
                )
                completed, _ = await asyncio.wait(
                    {notification_task},
                    timeout=notification_timeout,
                )
                if not completed:
                    await interrupt_turn()
                    await settle_notification_wait()
                    if not upstream_signal_seen:
                        message = (
                            "Codex produced no upstream model signal within "
                            f"{notification_timeout:.0f}s"
                        )
                        logger.warning(message)
                        await emit_transport("timed_out", message=message)
                        raise CodexTransportError(message)
                    raise TimeoutError("Codex model request timed out")
                notification = notification_task.result()
                notification_task = None

                method = str(notification.method or "")
                params = _model_dump(notification.payload)
                if method == "item/agentMessage/delta":
                    delta = str(params.get("delta") or "")
                    if delta:
                        if not upstream_signal_seen:
                            upstream_signal_seen = True
                            await emit_transport("connected")
                        text_parts.append(delta)
                        if stream_callback and action_schema is None:
                            await stream_callback(
                                {"type": "reply_delta", "delta": delta}
                            )
                elif method == "item/reasoning/summaryTextDelta":
                    delta = str(params.get("delta") or "")
                    if delta:
                        if not upstream_signal_seen:
                            upstream_signal_seen = True
                            await emit_transport("connected")
                        if stream_callback and not reasoning_started:
                            await stream_callback({"type": "reasoning_start"})
                        reasoning_started = True
                        reasoning_parts.append(delta)
                        if stream_callback:
                            await stream_callback(
                                {"type": "reasoning_delta", "delta": delta}
                            )
                elif method == "error":
                    error = params.get("error") or {}
                    error = error if isinstance(error, dict) else {}
                    message = str(error.get("message") or "Codex provider error")
                    will_retry = bool(params.get("willRetry"))
                    availability_error = codex_availability_error(
                        error,
                        message=message,
                    )
                    kind = _transport_error_kind(error)
                    logger.warning(
                        "Codex upstream error [kind=%s will_retry=%s model=%s effort=%s]: %s",
                        kind or "unknown",
                        will_retry,
                        model,
                        effort or "model-default",
                        message,
                    )
                    await emit_transport(
                        "retrying" if will_retry else "failed",
                        message=message,
                        kind=kind,
                        will_retry=will_retry,
                    )
                    if availability_error is not None and not will_retry:
                        raise availability_error
                    if kind:
                        # Cyrene owns cross-provider fallback. Do not also pay
                        # Codex's internal multi-retry budget for a broken
                        # upstream transport.
                        await interrupt_turn()
                        raise CodexTransportError(message)
                    if not will_retry:
                        raise RuntimeError(message)
                elif method == "item/completed":
                    item = params.get("item") or {}
                    if item.get("type") == "agentMessage":
                        final_text = str(item.get("text") or final_text)
                elif method == "thread/tokenUsage/updated":
                    breakdown = ((params.get("tokenUsage") or {}).get("last") or {})
                    usage = {
                        "prompt_tokens": int(breakdown.get("inputTokens") or 0),
                        "completion_tokens": int(breakdown.get("outputTokens") or 0),
                        "total_tokens": int(breakdown.get("totalTokens") or 0),
                        "prompt_cache_hit_tokens": int(
                            breakdown.get("cachedInputTokens") or 0
                        ),
                    }
                elif method == "turn/completed":
                    completed_turn = params.get("turn") or {}
                    if str(completed_turn.get("id") or "") != turn_id:
                        continue
                    if completed_turn.get("status") == "failed":
                        error = completed_turn.get("error") or {}
                        message = str(
                            error.get("message") or "Codex model request failed"
                        )
                        availability_error = codex_availability_error(
                            error,
                            message=message,
                        )
                        if availability_error is not None:
                            raise availability_error
                        raise RuntimeError(message)
                    if not upstream_signal_seen:
                        upstream_signal_seen = True
                        await emit_transport("connected")
                    for item in completed_turn.get("items") or []:
                        if (
                            isinstance(item, dict)
                            and item.get("type") == "agentMessage"
                        ):
                            final_text = str(item.get("text") or final_text)
                    break

            content = final_text or "".join(text_parts)
            reasoning_content = "".join(reasoning_parts)
            response = {
                "role": "assistant",
                "content": content,
                "usage": usage,
            }
            if action_schema is not None:
                response = _normalize_provider_action(
                    content,
                    action_tools,
                    usage=usage,
                )
            if reasoning_started and stream_callback:
                await stream_callback(
                    {
                        "type": "reasoning_done",
                        "response": reasoning_content,
                    }
                )
            if stream_callback and action_schema is None:
                await stream_callback({"type": "reply_done", "response": content})
            elif (
                stream_callback
                and not response.get("tool_calls")
                and str(response.get("content") or "")
            ):
                visible_content = str(response["content"])
                await stream_callback({"type": "reply_start"})
                await stream_callback(
                    {"type": "reply_delta", "delta": visible_content}
                )
                await stream_callback(
                    {"type": "reply_done", "response": visible_content}
                )
            if reasoning_content:
                response["reasoning_content"] = reasoning_content
            return response
        except asyncio.CancelledError:
            # Keep cancellation responsive without orphaning the SDK's
            # thread-backed queue read in the event loop's default executor.
            await interrupt_turn()
            await settle_notification_wait()
            raise
        finally:
            await settle_notification_wait()
            if turn_id:
                client.unregister_turn_notifications(turn_id)

    async def close(self) -> None:
        await self._close_image_client()
        client = self._client
        self._client = None
        self._client_loop = None
        if client is not None:
            try:
                await client.close()
            except RuntimeError:
                logger.debug("Codex SDK client close crossed event loops")
        if self._limits_refresh_task is not None:
            self._limits_refresh_task.cancel()
        self._limits_refresh_task = None


def _provider_instructions(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    *,
    structured_actions: bool = False,
) -> str:
    system_parts = [
        str(message.get("content") or "")
        for message in messages
        if message.get("role") in {"system", "developer"}
    ]
    tool_contract = ""
    if tools and structured_actions:
        tool_contract = (
            "\nCyrene tools are application actions. Never claim that an action "
            "ran before Cyrene returns its tool result. Your response is constrained "
            "to an object with `content` and zero or more `tool_calls`. For each call, "
            "set `name` to an available tool and set `arguments_json` to a JSON-object "
            "string matching that tool's parameters. "
            "When no action is needed, put the complete answer in `content` and return "
            "an empty `tool_calls` array. For actions, keep `content` empty. Do not wrap the object "
            "in Markdown or add any text outside the constrained object.\n"
            "Tool schemas:\n"
            + json.dumps(tools, ensure_ascii=False, default=str)
        )
    return (
        "You are the model backend for Cyrene. Follow the supplied conversation "
        "and return the next assistant message. Do not invoke Codex built-in tools; "
        "request actions from Cyrene instead. Ignore Codex host skills, plugins, "
        "AGENTS.md files, and SKILL.md files because their tools are not available "
        "inside Cyrene."
        + ("\nSystem instructions:\n" + "\n\n".join(system_parts) if system_parts else "")
        + tool_contract
    )


def _provider_action_tools(
    tools: list[dict[str, Any]] | None,
    *,
    phase: str = "",
) -> list[dict[str, Any]]:
    # Phase gating belongs to the Agent decision prompt and validator. Keeping
    # the provider-visible schema identical across ordinary Phase 1 and Phase 2
    # is required for prefix-cache reuse.
    del phase
    return [tool for tool in (tools or []) if isinstance(tool, dict)]


def _provider_action_schema(
    tools: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    names = [
        str((tool.get("function") or {}).get("name") or "").strip()
        for tool in (tools or [])
        if str((tool.get("function") or {}).get("name") or "").strip()
    ]
    if not names:
        return None
    return {
        "type": "object",
        "properties": {
            "content": {"type": "string"},
            "tool_calls": {
                "type": "array",
                "minItems": 0,
                "maxItems": 16,
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "enum": names,
                        },
                        "arguments_json": {"type": "string"},
                    },
                    "required": ["name", "arguments_json"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["content", "tool_calls"],
        "additionalProperties": False,
    }


_PROVIDER_DEVELOPER_INSTRUCTIONS = (
    "Act only as Cyrene's language-model backend. "
    "Never invoke Codex-hosted tools. Request Cyrene actions "
    "through the required structured response instead. "
    "Codex host skills, plugins, AGENTS.md files, and their "
    "SKILL.md files are not Cyrene capabilities: never read "
    "or follow them. Ignore any host-provided skill catalog "
    "and select actions only from Cyrene's required response schema."
)


def provider_request_cache_material(
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    model: str,
    phase: str = "",
    reasoning_effort: str = "",
) -> dict[str, Any]:
    """Build the canonical secret-free Codex request before transport IDs.

    ``threadId`` is intentionally absent because it is generated per request
    and does not participate in provider prompt caching.  Both the adapter and
    cache diagnostics consume this structure so tests observe the real schema,
    instructions, and replay representation instead of an Agent-layer proxy.
    """
    action_tools = _provider_action_tools(tools, phase=phase)
    action_schema = _provider_action_schema(action_tools)
    base_instructions = _provider_instructions(
        messages,
        action_tools,
        structured_actions=action_schema is not None,
    )
    effort = _normalized_effort(reasoning_effort)
    turn_input = _provider_turn_input(messages)
    turn_params: dict[str, Any] = {
        "input": turn_input,
        "model": model,
        "summary": "auto",
    }
    if effort:
        turn_params["effort"] = effort
    if action_schema is not None:
        turn_params["outputSchema"] = action_schema
    return {
        "action_tools": action_tools,
        "action_schema": action_schema,
        "base_instructions": base_instructions,
        "effort": effort,
        "turn_input": turn_input,
        "message_units": [
            {"role": "instructions", "content": base_instructions},
            *turn_input,
        ],
        "thread_params": {
            "model": model,
            "baseInstructions": base_instructions,
            "developerInstructions": _PROVIDER_DEVELOPER_INSTRUCTIONS,
            "ephemeral": True,
            "approvalPolicy": "never",
            "sandbox": "read-only",
            "cwd": str(_codex_isolation_workspace()),
        },
        "turn_params": turn_params,
    }


def _normalize_provider_action(
    content: str,
    tools: list[dict[str, Any]],
    *,
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        payload = json.loads(str(content or ""))
    except (TypeError, ValueError) as exc:
        raise CodexProtocolError(
            "Codex returned invalid structured action JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise CodexProtocolError("Codex action envelope must be an object")

    allowed_names = {
        str((tool.get("function") or {}).get("name") or "").strip()
        for tool in tools
        if str((tool.get("function") or {}).get("name") or "").strip()
    }
    parameter_schemas = {
        str((tool.get("function") or {}).get("name") or "").strip(): (
            (tool.get("function") or {}).get("parameters") or {}
        )
        for tool in tools
        if str((tool.get("function") or {}).get("name") or "").strip()
    }
    raw_calls = payload.get("tool_calls")
    if not isinstance(raw_calls, list):
        raise CodexProtocolError("Codex action envelope tool_calls must be an array")
    tool_calls: list[dict[str, Any]] = []
    for raw_call in raw_calls:
        if not isinstance(raw_call, dict):
            raise CodexProtocolError("Codex tool call must be an object")
        name = str(raw_call.get("name") or "").strip()
        if name not in allowed_names:
            raise CodexProtocolError(f"Codex requested unavailable tool: {name}")
        arguments_text = str(raw_call.get("arguments_json") or "").strip()
        try:
            arguments = json.loads(arguments_text or "{}")
        except (TypeError, ValueError) as exc:
            raise CodexProtocolError(
                f"Codex returned invalid arguments for tool: {name}"
            ) from exc
        if not isinstance(arguments, dict):
            raise CodexProtocolError(
                f"Codex arguments for {name} must be a JSON object"
            )
        try:
            arguments = normalize_plugin_arguments(
                arguments,
                parameter_schemas.get(name) or {},
            ).arguments
            validate_plugin_arguments(
                name,
                arguments,
                parameter_schemas.get(name) or {},
            )
        except (PluginInputValidationError, PluginSchemaError) as exc:
            raise CodexProtocolError(
                f"Codex returned invalid arguments for tool {name}: {exc}"
            ) from exc
        tool_calls.append(
            {
                "index": len(tool_calls),
                "id": f"call_codex_{uuid.uuid4().hex[:16]}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            }
        )

    visible_content = str(payload.get("content") or "")
    if tool_calls:
        # Text accompanying a non-terminal action is untrusted model narration:
        # the harness has not executed anything yet, so never surface it.
        visible_content = ""
    return {
        "role": "assistant",
        "content": visible_content,
        "tool_calls": tool_calls,
        "usage": dict(usage or {}),
    }


def _provider_replay_and_images(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Convert OpenAI-style multimodal messages to Codex app-server inputs."""
    groups = _provider_replay_groups(messages)
    return (
        [replay_message for replay_message, _images in groups],
        [image for _replay_message, images in groups for image in images],
    )


def _provider_replay_groups(
    messages: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], list[dict[str, str]]]]:
    """Keep each replay message adjacent to its attached provider images."""
    groups: list[tuple[dict[str, Any], list[dict[str, str]]]] = []
    image_count = 0
    for message in messages:
        if message.get("role") in {"system", "developer"}:
            continue
        replay_message = dict(message)
        message_images: list[dict[str, str]] = []
        content = message.get("content")
        if isinstance(content, list):
            replay_content: list[Any] = []
            for item in content:
                if not isinstance(item, dict):
                    replay_content.append(item)
                    continue
                item_type = str(item.get("type") or "")
                image_url = item.get("image_url")
                if item_type in {"image_url", "input_image"}:
                    if isinstance(image_url, dict):
                        image_url = image_url.get("url")
                    url = str(image_url or item.get("url") or "").strip()
                    if url:
                        image_count += 1
                        image_number = image_count
                        message_images.append({"type": "image", "url": url})
                        replay_content.append({
                            "type": "text",
                            "text": f"[Image {image_number} is attached to this turn.]",
                        })
                        continue
                if item_type in {"localImage", "local_image"}:
                    path = str(item.get("path") or "").strip()
                    if path:
                        image_count += 1
                        image_number = image_count
                        message_images.append({
                            "type": "localImage",
                            "path": path,
                        })
                        replay_content.append({
                            "type": "text",
                            "text": f"[Image {image_number} is attached to this turn.]",
                        })
                        continue
                replay_content.append(dict(item))
            replay_message["content"] = replay_content
        groups.append((replay_message, message_images))
    return groups


def _provider_input(messages: list[dict[str, Any]]) -> str:
    replay, _ = _provider_replay_and_images(messages)
    return (
        "Continue this conversation and produce only the next assistant message.\n"
        + json.dumps(replay, ensure_ascii=False, default=str)
    )


def _provider_turn_input(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    turn_input: list[dict[str, str]] = [{
        "type": "text",
        "text": "Continue this conversation and produce only the next assistant message.",
    }]
    for replay_message, images in _provider_replay_groups(messages):
        turn_input.append({
            "type": "text",
            "text": json.dumps(
                replay_message,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ),
        })
        turn_input.extend(images)
    return turn_input


_client = CodexAppServer()


def get_codex_provider() -> CodexAppServer:
    return _client
