"""Explicit public support API for native tool implementations.

The implementation remains in ``runtime_support`` during migration.  Tool
modules import only the names declared here, so private implementation helpers
can be replaced without another cross-package migration.
"""

from __future__ import annotations

import asyncio as asyncio
from datetime import datetime as datetime, timezone as timezone
import json as json
import logging
import re as re
import time as time

import httpx as httpx

from cyrene.learning.skills import (
    build_skills,
    install_skill_from_path,
    uninstall_skill,
)
from cyrene.model_runtime.messages import truncate
from cyrene.runtime import database as db
from cyrene.runtime.attachments import (
    analyze_attachment,
    build_public_attachment_payload,
    register_generated_attachment,
)
from cyrene.runtime.inbox import send_message as send_inbox
from cyrene.runtime.schedule_spec import compute_next_run
from cyrene.tooling import runtime_support as _implementation
from cyrene.tooling.backends.search import deep_search
from cyrene.workbench.context import resolve_project_data_key_for_session

logger = logging.getLogger("cyrene.tooling.runtime_support")

classify_destructive_shell_command = _implementation._classify_destructive_shell_command
command_is_file_deletion = _implementation._command_is_file_deletion
guard_nonbash_shell_command = _implementation._guard_nonbash_shell_command
guard_shell_command_workspace_write = _implementation._guard_shell_command_workspace_write
is_dangerous_subshell = _implementation._is_dangerous_subshell
json_result = _implementation._json_result
request_delete_confirmation = _implementation._request_delete_confirmation
request_destructive_confirmation = _implementation._request_destructive_confirmation
request_external_delivery_confirmation = _implementation._request_external_delivery_confirmation
request_external_upload_confirmation = _implementation._request_external_upload_confirmation
request_read_elevation = _implementation._request_read_elevation
request_self_configuration_confirmation = _implementation._request_self_configuration_confirmation
request_host_lifecycle_confirmation = _implementation._request_host_lifecycle_confirmation
request_scope_elevation = _implementation._request_scope_elevation
request_write_elevation = _implementation._request_write_elevation
resolve_exportable_path = _implementation._resolve_exportable_path
resolve_tool_path = _implementation._resolve_tool_path
resolve_workspace_path = _implementation._resolve_workspace_path
resolve_workspace_write_target = _implementation._resolve_workspace_write_target
shell_command_requires_write_guard = _implementation._shell_command_requires_write_guard


async def register_subagent(*args, **kwargs):
    return await _implementation._reg_subagent(*args, **kwargs)


async def can_receive(*args, **kwargs):
    return await _implementation.can_receive(*args, **kwargs)


async def run_subagent(*args, **kwargs):
    return await _implementation._run_subagent(*args, **kwargs)


def spawn_subagent_task(*args, **kwargs):
    return _implementation._spawn_subagent_task(*args, **kwargs)


__all__ = [
    "analyze_attachment",
    "asyncio",
    "build_public_attachment_payload",
    "build_skills",
    "can_receive",
    "classify_destructive_shell_command",
    "command_is_file_deletion",
    "compute_next_run",
    "datetime",
    "db",
    "deep_search",
    "guard_nonbash_shell_command",
    "guard_shell_command_workspace_write",
    "httpx",
    "install_skill_from_path",
    "is_dangerous_subshell",
    "json",
    "json_result",
    "logger",
    "re",
    "register_generated_attachment",
    "register_subagent",
    "request_delete_confirmation",
    "request_destructive_confirmation",
    "request_external_delivery_confirmation",
    "request_external_upload_confirmation",
    "request_read_elevation",
    "request_self_configuration_confirmation",
    "request_host_lifecycle_confirmation",
    "request_scope_elevation",
    "request_write_elevation",
    "resolve_exportable_path",
    "resolve_project_data_key_for_session",
    "resolve_tool_path",
    "resolve_workspace_path",
    "resolve_workspace_write_target",
    "run_subagent",
    "send_inbox",
    "shell_command_requires_write_guard",
    "spawn_subagent_task",
    "time",
    "timezone",
    "truncate",
    "uninstall_skill",
]
