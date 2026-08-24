"""ComfyUI local/cloud adapter over the configured official MCP server."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import mimetypes
from pathlib import Path
import re
import shutil
import tempfile
import time
from typing import Any

from cyrene.config import TEMP_DIR
from cyrene.media.models import MediaProviderError, MediaProviderResult
from cyrene.media.providers.base import MediaProvider, ProgressCallback, emit_progress
from cyrene.media.providers.helpers import (
    artifact_from_bytes,
    artifact_from_url,
    bounded_float,
    configured_download_limit,
    first_string,
    parse_json_text,
    request_references,
    request_value,
)


_SUCCESS = frozenset({"success", "succeeded", "completed", "done"})
_FAILURE = frozenset(
    {
        "failed",
        "fail",
        "error",
        "execution_error",
        "server_died",
        "interrupted",
        "cancelled",
        "canceled",
        "expired",
    }
)
_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+")
_JOB_PATTERN = re.compile(r"(?:prompt|job|task)[_ -]?id\s*[:=]\s*([A-Za-z0-9._:-]+)", re.IGNORECASE)
_MCP_MARKER = "cyrene.mcp-content.v1"
_PLACEHOLDER = re.compile(r"\{\{([A-Za-z0-9_.-]+)\}\}")
_CONTROL_PARAMETERS = frozenset(
    {
        "confirm_spend",
        "generation_timeout_seconds",
    }
)


def _unwrap(value: Any) -> tuple[Any, list[dict[str, Any]]]:
    parsed = parse_json_text(value)
    artifacts: list[dict[str, Any]] = []
    if isinstance(parsed, dict) and parsed.get("_cyrene_mcp_raw") is True:
        artifacts = [item for item in parsed.get("content") or [] if isinstance(item, dict)]
        structured = parsed.get("structured_content")
        text = "\n".join(str(item.get("text") or "") for item in artifacts if item.get("type") == "text" and item.get("text"))
        parsed = structured if isinstance(structured, (dict, list)) and structured else parse_json_text(text)
    if isinstance(parsed, dict) and parsed.get("_cyrene_mcp_content") == _MCP_MARKER:
        artifacts = [item for item in parsed.get("artifacts") or [] if isinstance(item, dict)]
        parsed = parse_json_text(parsed.get("text"))
    return parsed, artifacts


def _job_id(value: Any) -> str:
    parsed, _ = _unwrap(value)
    result = first_string(parsed, ("prompt_id", "job_id", "task_id", "promptId", "jobId", "id"))
    if result:
        return result
    match = _JOB_PATTERN.search(str(value or ""))
    return match.group(1) if match else ""


def _status(value: Any) -> str:
    parsed, _ = _unwrap(value)

    def visit(item: Any) -> str:
        if isinstance(item, dict):
            if item.get("completed") is True:
                return "completed"
            for key in ("status_str", "state", "status"):
                child = item.get(key)
                if isinstance(child, str) and child.strip():
                    return child.strip().lower()
            for child in item.values():
                result = visit(child)
                if result != "unknown":
                    return result
        elif isinstance(item, (list, tuple)):
            for child in item:
                result = visit(child)
                if result != "unknown":
                    return result
        return "unknown"

    return visit(parsed)


def _output_urls(value: Any) -> list[str]:
    parsed, content = _unwrap(value)
    source_value = {"result": parsed, "content": content} if content else parsed
    source = json.dumps(source_value, ensure_ascii=False) if isinstance(source_value, (dict, list)) else str(source_value or "")
    results: list[str] = []
    for match in _URL_PATTERN.findall(source):
        clean = match.rstrip("),.;]}")
        if clean not in results:
            results.append(clean)
    return results


def _mcp_retryable(exc: Exception) -> bool:
    message = str(exc or "").lower()
    return any(marker in message for marker in ("timeout", "closed", "disconnected", "temporarily", "busy", "rate", "unavailable"))


async def _execute(server: str, tool: str, arguments: dict[str, Any]) -> Any:
    from cyrene.tooling.backends.mcp_manager import get_manager

    manager = get_manager()
    execute_raw = getattr(manager, "execute_tool_raw_on", None)
    execute = getattr(manager, "execute_tool_on", None)
    if not callable(execute_raw) and not callable(execute):
        raise MediaProviderError(
            "Cyrene's MCP manager does not support server-scoped tool calls.",
            code="comfyui_mcp_scope_unavailable",
        )
    try:
        if callable(execute_raw):
            result = await execute_raw(server, tool, arguments)
            if bool(result.get("is_error")):
                text = "\n".join(str(item.get("text") or "") for item in result.get("content") or [] if isinstance(item, dict) and item.get("type") == "text")
                raise RuntimeError(text or f"ComfyUI MCP tool {tool} returned an error")
            return {
                "_cyrene_mcp_raw": True,
                "content": list(result.get("content") or []),
                "structured_content": result.get("structured_content") or {},
            }
        return await execute(server, tool, arguments)
    except MediaProviderError:
        raise
    except Exception as exc:
        raise MediaProviderError(
            f"ComfyUI MCP tool {tool} failed: {str(exc)[:1600]}",
            retryable=_mcp_retryable(exc),
            code="comfyui_mcp_error",
        ) from exc


async def _execute_with_timeout(
    server: str,
    tool: str,
    arguments: dict[str, Any],
    *,
    timeout_seconds: float,
    phase: str,
) -> Any:
    try:
        return await asyncio.wait_for(
            _execute(server, tool, arguments),
            timeout=max(0.1, float(timeout_seconds)),
        )
    except asyncio.TimeoutError as exc:
        raise MediaProviderError(
            f"ComfyUI MCP {phase} timed out.",
            retryable=True,
            code=f"comfyui_{phase}_timeout",
        ) from exc


def _workflow_path(request: dict[str, Any], settings: dict[str, Any], kind: str) -> Path:
    # Workflows can execute custom nodes and therefore arbitrary code. Their
    # path is operator configuration, never a model-controlled request value.
    raw = settings.get(f"{kind}_workflow")
    if not raw:
        raise MediaProviderError(f"No ComfyUI {kind} workflow is configured.", code="comfyui_missing_workflow")
    path = Path(str(raw)).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != ".json":
        raise MediaProviderError("ComfyUI workflow must be an existing JSON file.", code="comfyui_invalid_workflow")
    if path.stat().st_size <= 0 or path.stat().st_size > 10 * 1024 * 1024:
        raise MediaProviderError("ComfyUI workflow must be between 1 byte and 10 MiB.", code="comfyui_invalid_workflow")
    return path


def _read_workflow(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise MediaProviderError("ComfyUI workflow contains invalid JSON.", code="comfyui_invalid_workflow") from exc
    if not isinstance(value, dict) or not value:
        raise MediaProviderError("ComfyUI workflow must contain a workflow object.", code="comfyui_invalid_workflow")
    return value


def _replace_placeholders(
    value: Any,
    replacements: dict[str, Any],
    used: set[str],
) -> Any:
    if isinstance(value, dict):
        return {key: _replace_placeholders(child, replacements, used) for key, child in value.items()}
    if isinstance(value, list):
        return [_replace_placeholders(child, replacements, used) for child in value]
    if not isinstance(value, str):
        return value
    exact = _PLACEHOLDER.fullmatch(value)
    if exact:
        name = exact.group(1)
        if name not in replacements:
            raise MediaProviderError(
                f"ComfyUI workflow contains an unknown placeholder: {name}",
                code="comfyui_unknown_placeholder",
            )
        used.add(name)
        return replacements[name]

    def substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in replacements:
            raise MediaProviderError(
                f"ComfyUI workflow contains an unknown placeholder: {name}",
                code="comfyui_unknown_placeholder",
            )
        used.add(name)
        replacement = replacements[name]
        if isinstance(replacement, (dict, list)):
            return json.dumps(replacement, ensure_ascii=False, separators=(",", ":"))
        return str(replacement)

    return _PLACEHOLDER.sub(substitute, value)


def _render_workflow(
    workflow: dict[str, Any],
    request: dict[str, Any],
    *,
    staged_references: list[str],
    staged_mask: str,
) -> dict[str, Any]:
    replacements: dict[str, Any] = {
        "prompt": str(request.get("prompt") or ""),
        "negative_prompt": str(request.get("negative_prompt") or ""),
        "lyrics": str(request.get("lyrics") or ""),
    }
    required: set[str] = set()
    for name in (
        "prompt",
        "negative_prompt",
        "lyrics",
        "seed",
        "duration",
        "number_of_outputs",
        "aspect_ratio",
        "resolution",
        "size",
        "quality",
        "output_format",
    ):
        value = request.get(name)
        if value is not None and value != "":
            replacements[name] = value
            required.add(name)
    raw_parameters = request.get("parameters")
    if isinstance(raw_parameters, dict):
        for name, value in raw_parameters.items():
            if name in _CONTROL_PARAMETERS or value is None:
                continue
            key = f"parameter.{name}"
            replacements[key] = value
            required.add(key)
    for index, reference in enumerate(staged_references, 1):
        key = f"reference_{index}"
        replacements[key] = reference
        required.add(key)
    if staged_mask:
        replacements["mask"] = staged_mask
        required.add("mask")

    used: set[str] = set()
    rendered = _replace_placeholders(workflow, replacements, used)
    missing = sorted(required - used)
    if missing:
        raise MediaProviderError(
            "ComfyUI workflow does not bind requested inputs: " + ", ".join(missing),
            code="comfyui_missing_binding",
        )
    if not used:
        raise MediaProviderError(
            "ComfyUI workflow must contain Cyrene placeholders such as {{prompt}}.",
            code="comfyui_missing_binding",
        )
    return rendered


async def _stage_local_inputs(
    server: str,
    upload_tool: str,
    request: dict[str, Any],
    directory: Path,
    *,
    timeout_seconds: float,
) -> tuple[list[str], str]:
    raw_references = request_references(request)
    mask_value = str(request.get("mask_path") or "").strip()
    mask = Path(mask_value).expanduser().resolve() if mask_value else None
    if not raw_references and mask is None:
        return [], ""
    staged_paths: list[Path] = []
    reference_names: list[str] = [""] * len(raw_references)
    mask_name = ""
    local_sources: list[tuple[int, Path, bool]] = []
    for index, value in enumerate(raw_references):
        if isinstance(value, dict):
            raw = str(value.get("url") or value.get("uri") or value.get("path") or "").strip()
        else:
            raw = str(value or "").strip()
        if raw.startswith("https://"):
            reference_names[index] = raw
            continue
        if raw.startswith(("http://", "data:")):
            raise MediaProviderError(
                "ComfyUI workflow references must be local files or public HTTPS URLs.",
                code="comfyui_unsupported_reference",
            )
        local_sources.append((index, Path(raw).expanduser().resolve(), False))
    if mask is not None:
        local_sources.append((len(raw_references), mask, True))
    for upload_index, (reference_index, source, is_mask) in enumerate(
        local_sources,
        1,
    ):
        if not source.is_file():
            raise MediaProviderError(
                "ComfyUI reference input is unavailable.",
                code="missing_reference",
            )
        size = source.stat().st_size
        if size <= 0 or size > 64 * 1024 * 1024:
            raise MediaProviderError(
                "ComfyUI reference inputs must be between 1 byte and 64 MiB.",
                code="invalid_reference_size",
            )
        digest_builder = hashlib.sha256()
        with source.open("rb") as input_stream:
            for chunk in iter(lambda: input_stream.read(1024 * 1024), b""):
                digest_builder.update(chunk)
        digest = digest_builder.hexdigest()[:16]
        suffix = source.suffix.lower() or ".bin"
        staged = directory / f"cyrene-{digest}-{upload_index}{suffix}"
        shutil.copyfile(source, staged)
        staged_paths.append(staged)
        if is_mask:
            mask_name = staged.name
        else:
            reference_names[reference_index] = staged.name
    if staged_paths:
        await _execute_with_timeout(
            server,
            upload_tool,
            {"paths": [str(path) for path in staged_paths], "overwrite": True},
            timeout_seconds=timeout_seconds,
            phase="input_upload",
        )
    return reference_names, mask_name


def _directory_artifacts(directory: Path, *, kind: str, maximum: int) -> list[Any]:
    root = directory.resolve()
    artifacts = []
    total = 0
    allowed_prefix = {"image": "image/", "video": "video/", "music": "audio/"}[kind]
    candidates: list[Path] = []
    for item in directory.rglob("*"):
        try:
            if item.is_symlink():
                continue
            path = item.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if path == root or root not in path.parents or not path.is_file():
            continue
        candidates.append(path)
    for index, path in enumerate(sorted(candidates), 1):
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if not content_type.startswith(allowed_prefix):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        total += size
        if total > maximum:
            raise MediaProviderError("ComfyUI outputs exceed the configured download limit.", code="output_too_large")
        data = path.read_bytes()
        artifacts.append(artifact_from_bytes(data, prefix=f"comfyui-{kind}", index=index, content_type=content_type, filename=path.name))
        if len(artifacts) >= 32:
            break
    return artifacts


def _inline_artifacts(entries: list[dict[str, Any]], *, kind: str, maximum: int) -> list[Any]:
    root = (TEMP_DIR / "mcp-content").resolve()
    artifacts = []
    total = 0
    allowed_prefix = {"image": "image/", "video": "video/", "music": "audio/"}[kind]
    for index, entry in enumerate(entries, 1):
        block_type = str(entry.get("type") or "")
        if block_type in {"image", "audio"} and entry.get("data"):
            content_type = str(entry.get("mimeType") or entry.get("mime_type") or ("image/png" if block_type == "image" else "audio/mpeg"))
            if not content_type.startswith(allowed_prefix):
                continue
            encoded = str(entry.get("data") or "")
            if len(encoded) > ((maximum - total + 2) // 3) * 4 + 4:
                raise MediaProviderError("ComfyUI outputs exceed the configured download limit.", code="output_too_large")
            try:
                data = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error):
                continue
            total += len(data)
            if total > maximum:
                raise MediaProviderError("ComfyUI outputs exceed the configured download limit.", code="output_too_large")
            artifacts.append(
                artifact_from_bytes(
                    data,
                    prefix=f"comfyui-{kind}",
                    index=index,
                    content_type=content_type,
                )
            )
            continue
        resource = entry.get("resource") if block_type == "resource" and isinstance(entry.get("resource"), dict) else None
        if resource and resource.get("blob"):
            content_type = str(resource.get("mimeType") or resource.get("mime_type") or "application/octet-stream")
            if not content_type.startswith(allowed_prefix):
                continue
            encoded = str(resource.get("blob") or "")
            if len(encoded) > ((maximum - total + 2) // 3) * 4 + 4:
                raise MediaProviderError("ComfyUI outputs exceed the configured download limit.", code="output_too_large")
            try:
                data = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error):
                continue
            total += len(data)
            if total > maximum:
                raise MediaProviderError("ComfyUI outputs exceed the configured download limit.", code="output_too_large")
            artifacts.append(artifact_from_bytes(data, prefix="comfyui-output", index=index, content_type=content_type))
            continue
        try:
            unresolved_path = Path(str(entry.get("path") or "")).expanduser()
            if unresolved_path.is_symlink():
                continue
            path = unresolved_path.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if path == root or root not in path.parents or not path.is_file():
            continue
        content_type = str(entry.get("mime_type") or mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        if not content_type.startswith(allowed_prefix):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        total += size
        if total > maximum:
            raise MediaProviderError("ComfyUI outputs exceed the configured download limit.", code="output_too_large")
        data = path.read_bytes()
        artifacts.append(artifact_from_bytes(data, prefix="comfyui-image", index=index, content_type=content_type, filename=path.name))
    return artifacts


class ComfyUIProvider(MediaProvider):
    name = "comfyui"
    supported_kinds = frozenset({"image", "video", "music"})

    async def generate(
        self,
        request: dict[str, Any],
        provider_settings: dict[str, Any],
        progress: ProgressCallback,
    ) -> MediaProviderResult:
        kind = str(request.get("kind") or "").strip().lower()
        if kind not in self.supported_kinds:
            raise MediaProviderError("ComfyUI media kind is unsupported.", code="unsupported_kind")
        prompt_id = str(request.get("_resume_provider_job_id") or "").strip()
        resume_state = request.get("_resume_provider_state")
        if not isinstance(resume_state, dict):
            resume_state = {}
        server = str((resume_state.get("mcp_server") if prompt_id else "") or provider_settings.get("mcp_server") or "comfyui").strip()
        if not server:
            raise MediaProviderError("ComfyUI MCP server name is not configured.", code="comfyui_missing_server")
        mode = str((resume_state.get("mode") if prompt_id else "") or provider_settings.get("mode") or "local").strip().lower()
        if mode not in {"local", "cloud"}:
            raise MediaProviderError("ComfyUI mode must be local or cloud.", code="comfyui_invalid_mode")
        configured_workflow = str((resume_state.get("workflow") if prompt_id else "") or provider_settings.get(f"{kind}_workflow") or "").strip()
        workflow_name = Path(configured_workflow).name if configured_workflow else ""
        if mode == "cloud":
            default_submit, default_status, default_output = "submit_workflow", "get_job_status", "get_output"
        else:
            default_submit, default_status, default_output = "run_workflow", "job", "fetch_outputs"
        submit_tool = str(provider_settings.get("submit_tool") or default_submit)
        status_tool = str((resume_state.get("status_tool") if prompt_id else "") or provider_settings.get("status_tool") or default_status)
        output_tool = str((resume_state.get("output_tool") if prompt_id else "") or provider_settings.get("output_tool") or default_output)
        upload_tool = str(provider_settings.get("upload_tool") or "upload_file")
        job_id_argument = str((resume_state.get("job_id_argument") if prompt_id else "") or provider_settings.get("job_id_argument") or "prompt_id")
        # The defaults in older saved settings describe local mode. Selecting
        # cloud mode should still choose the official cloud tool names.
        if mode == "cloud" and (submit_tool, status_tool, output_tool) == ("run_workflow", "job", "fetch_outputs"):
            submit_tool, status_tool, output_tool = default_submit, default_status, default_output
        state_context = {
            "mcp_server": server,
            "mode": mode,
            "status_tool": status_tool,
            "output_tool": output_tool,
            "job_id_argument": job_id_argument,
            "workflow": workflow_name,
        }
        submitted: Any = {}
        request_timeout = bounded_float(provider_settings.get("request_timeout_seconds"), 120.0, 15.0, 300.0)
        if prompt_id:
            await emit_progress(
                progress,
                "Resuming ComfyUI workflow",
                provider_job_id=prompt_id,
                state={**state_context, "status": "resuming"},
            )
        else:
            # Recovery only needs the server-scoped prompt ID. Do not make an
            # already-running job depend on the original workflow file still
            # existing on disk.
            workflow_path = _workflow_path(request, provider_settings, kind)
            workflow_name = workflow_path.name
            state_context["workflow"] = workflow_name
            references = request_references(request)
            if mode == "cloud" and request.get("mask_path"):
                raise MediaProviderError(
                    "Comfy Cloud mask inputs require its upload_file asset protocol; use a local MCP profile for local masks.",
                    code="comfyui_cloud_reference_upload_required",
                )
            TEMP_DIR.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(
                prefix="cyrene-comfy-workflow-",
                dir=TEMP_DIR,
            ) as preparation_dir:
                preparation_path = Path(preparation_dir)
                if mode == "local":
                    staged_references, staged_mask = await _stage_local_inputs(
                        server,
                        upload_tool,
                        request,
                        preparation_path,
                        timeout_seconds=request_timeout,
                    )
                else:
                    staged_references = []
                    for reference in references:
                        raw = (
                            str(reference.get("url") or reference.get("uri") or reference.get("path") or "").strip()
                            if isinstance(reference, dict)
                            else str(reference or "").strip()
                        )
                        if not raw.startswith("https://"):
                            raise MediaProviderError(
                                "Comfy Cloud local references require its upload_file asset protocol; configure a public HTTPS reference or use the local MCP profile.",
                                code="comfyui_cloud_reference_upload_required",
                            )
                        staged_references.append(raw)
                    staged_mask = ""
                rendered_workflow = _render_workflow(
                    _read_workflow(workflow_path),
                    request,
                    staged_references=staged_references,
                    staged_mask=staged_mask,
                )
                rendered_path = preparation_path / "workflow.json"
                rendered_path.write_text(
                    json.dumps(rendered_workflow, ensure_ascii=False),
                    encoding="utf-8",
                )
                if mode == "cloud":
                    workflow_argument = str(provider_settings.get("workflow_argument") or "workflow")
                    submit_args = {workflow_argument: rendered_workflow}
                else:
                    submit_args = {
                        "workflow_path": str(rendered_path),
                        "wait": False,
                        # Only the operator-owned provider setting may opt in
                        # to a workflow containing paid partner nodes.
                        "confirm_spend": bool(provider_settings.get("confirm_spend", False)),
                    }
                await emit_progress(progress, f"Submitting ComfyUI {kind} workflow")
                submitted = await _execute_with_timeout(
                    server,
                    submit_tool,
                    submit_args,
                    timeout_seconds=request_timeout,
                    phase="submission",
                )
            prompt_id = _job_id(submitted)
            if not prompt_id:
                raise MediaProviderError("ComfyUI MCP returned no prompt ID.", retryable=True, code="comfyui_missing_prompt_id")
            await emit_progress(
                progress,
                "ComfyUI workflow queued",
                provider_job_id=prompt_id,
                state={**state_context, "status": "queued"},
            )

        total_timeout = bounded_float(
            request_value(request, "generation_timeout_seconds", provider_settings.get("generation_timeout_seconds")),
            1800.0,
            60.0,
            7200.0,
        )
        poll_seconds = bounded_float(provider_settings.get("poll_interval_seconds"), 5.0, 2.0, 60.0)
        deadline = time.monotonic() + total_timeout
        previous = ""
        final_status: Any = submitted
        while time.monotonic() < deadline:
            if mode == "cloud":
                status_args = {job_id_argument: prompt_id}
            elif status_tool == "job":
                status_args = {"action": "wait", "prompt_id": prompt_id, "timeout_seconds": min(25.0, max(1.0, deadline - time.monotonic()))}
            else:
                status_args = {job_id_argument: prompt_id}
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MediaProviderError("ComfyUI workflow timed out.", retryable=True, code="comfyui_timeout")
            final_status = await _execute_with_timeout(
                server,
                status_tool,
                status_args,
                timeout_seconds=min(request_timeout, remaining),
                phase="status",
            )
            current = _status(final_status)
            if current != previous:
                await emit_progress(
                    progress,
                    f"ComfyUI workflow: {current}",
                    provider_job_id=prompt_id,
                    state={**state_context, "status": current},
                )
                previous = current
            if current in _SUCCESS:
                break
            if current in _FAILURE:
                parsed, _ = _unwrap(final_status)
                message = first_string(parsed, ("error_message", "exception_message", "message", "reason")) or current
                # Retrying would resume the same terminal prompt ID and cannot
                # repair its graph or runtime failure.
                raise MediaProviderError(f"ComfyUI workflow failed: {message}", code=f"comfyui_{current}")
            await asyncio.sleep(min(poll_seconds, max(0.1, deadline - time.monotonic())))
        else:
            raise MediaProviderError("ComfyUI workflow timed out.", retryable=True, code="comfyui_timeout")

        maximum = configured_download_limit(provider_settings)
        with tempfile.TemporaryDirectory(prefix="cyrene-comfy-output-") as output_dir:
            directory = Path(output_dir)
            if mode == "cloud":
                output_args = {
                    job_id_argument: prompt_id,
                    "description": str(request.get("name") or f"cyrene-{kind}")[:120],
                }
            else:
                output_args = {
                    "prompt_id": prompt_id,
                    "out_dir": str(directory),
                    "url_only": False,
                    "inline_images": kind == "image",
                }
            output = await _execute_with_timeout(
                server,
                output_tool,
                output_args,
                timeout_seconds=bounded_float(provider_settings.get("output_timeout_seconds"), 300.0, 30.0, 1800.0),
                phase="output",
            )
            _parsed, inline_entries = _unwrap(output)
            artifacts = _directory_artifacts(directory, kind=kind, maximum=maximum)
            if not artifacts:
                artifacts.extend(_inline_artifacts(inline_entries, kind=kind, maximum=maximum))
            if not artifacts:
                for index, url in enumerate(_output_urls(output), 1):
                    artifacts.append(
                        await artifact_from_url(
                            url,
                            prefix=f"comfyui-{kind}",
                            index=index,
                            max_bytes=maximum,
                            timeout_seconds=300.0,
                        )
                    )
        if not artifacts:
            raise MediaProviderError("ComfyUI completed without output media.", retryable=True, code="comfyui_empty_output")
        await emit_progress(
            progress,
            f"Collected {len(artifacts)} ComfyUI output(s)",
            provider_job_id=prompt_id,
            state={**state_context, "status": "completed"},
        )
        return MediaProviderResult(
            artifacts=artifacts,
            provider_job_id=prompt_id,
            metadata={"provider": self.name, "mode": mode, "mcp_server": server, "workflow": workflow_name},
        )


__all__ = ["ComfyUIProvider"]
