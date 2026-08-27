"""Shared pytest fixtures and test-isolation helpers."""

import asyncio
import gc
import importlib
import sys
import threading
import time
from pathlib import Path

import pytest
import PIL as _REAL_PIL
import pypdf as _REAL_PYPDF

# Tests import ``cyrene`` from the in-repo ``src/`` tree; make sure it is on the
# path before any cyrene import happens (mirrors the shim at the top of
# ``tests/test_runtime_fixes.py``).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

_REAL_PIL_IMAGE = importlib.import_module("PIL.Image")

_WORKBENCH_CHAT_SOURCE_FILES = (
    "workbench-chat.jsx",
    "features/chat/core.jsx",
    "features/chat/drag-layout.jsx",
    "features/chat/agent-events.jsx",
    "features/chat/model-api.jsx",
    "features/chat/runtime-timeline.jsx",
    "features/chat/presentation.jsx",
    "features/chat/errors.jsx",
    "features/chat/icons.jsx",
    "features/chat/voice-playback.jsx",
    "features/chat/voice-input.jsx",
    "features/chat/voice-command.jsx",
    "features/chat/voice.jsx",
    "features/chat/capabilities.jsx",
    "features/chat/file-resources.jsx",
    "features/chat/runtime-page-hooks.jsx",
    "features/chat/live-event-controller.jsx",
    "features/chat/page.jsx",
    "features/chat/chat-action-controller.jsx",
    "features/chat/page-drop-controller.jsx",
    "features/chat/page-resource-controller.jsx",
    "features/chat/page-context-menu.jsx",
    "features/chat/pane-card-drag-controller.jsx",
    "features/chat/pane-detachment.jsx",
    "features/chat/pane-drop-controller.jsx",
    "features/chat/pane-layout-controller.jsx",
    "features/chat/pane-workspace.jsx",
    "features/chat/request-sequencer.jsx",
    "features/chat/rename-dialog.jsx",
    "features/chat/rail-model.jsx",
    "features/chat/rail-drop-controller.jsx",
    "features/chat/rail-ordering.jsx",
    "features/chat/rail-tasks.jsx",
    "features/chat/rail.jsx",
    "features/chat/conversation.jsx",
    "features/chat/conversation-navigator.jsx",
    "features/chat/messages.jsx",
    "features/chat/composer-attachments.jsx",
    "features/chat/composer-flow.jsx",
    "features/chat/composer-model-state.jsx",
    "features/chat/composer-voice.jsx",
    "features/chat/composer.jsx",
    "features/chat/resource-splits.jsx",
    "features/chat/split-pane.jsx",
    "features/chat/split-drag-controller.jsx",
    "features/chat/split-selection-controller.jsx",
    "features/chat/task-pane-controller.jsx",
    "features/chat/viewer.jsx",
    "features/chat/context-panel.jsx",
    "features/chat/terminal-controller.jsx",
    "features/chat/index.jsx",
)

_WORKBENCH_SHELL_SOURCE_FILES = (
    "shared/runtime/services.jsx",
    "shared/browser/overlays.jsx",
    "shared/errors.jsx",
    "shared/file-drop.jsx",
    "features/session/activity.jsx",
    "features/session/live-activity.jsx",
    "features/session/resources.jsx",
    "features/session/tabs-controller.jsx",
    "features/layout/right-panel-resizer.jsx",
    "features/shell/topbar.jsx",
    "features/shell/support.jsx",
    "features/shell/app-lifecycle.jsx",
    "features/shell/app-overlays.jsx",
    "features/shell/global-shortcuts.mjs",
    "features/shell/module-presentation.jsx",
    "features/shell/navigation-controller.jsx",
    "features/shell/project-rail-controller.jsx",
    "features/shell/resource-controller.jsx",
    "features/shell/shell-navigation.jsx",
    "features/shell/shell-composition.jsx",
    "features/task/controller.jsx",
    "features/task/presentation.jsx",
    "features/task/board.jsx",
    "features/task/context-panel.jsx",
    "features/task/index.jsx",
    "features/task/project-controller.jsx",
    "features/task/selection-controller.jsx",
    "features/task/store-merge.jsx",
    "workbench.jsx",
)

_WORKBENCH_STYLE_FILES = (
    "workbench.css",
    "features/task/task.css",
    "features/settings/settings.css",
    "features/settings/extensions.css",
    "features/settings/controls.css",
    "features/settings/integrations.css",
    "features/knowledge/knowledge.css",
    "features/schedule/schedule.css",
    "features/memory/memory.css",
    "features/create/create.css",
    "features/chat/chat.css",
    "features/chat/conversation.css",
    "features/chat/context.css",
    "features/chat/viewer.css",
    "features/chat/onboarding.css",
    "features/chat/quick-chat.css",
    "features/chat/settings-surfaces.css",
    "features/chat/shell.css",
    "features/chat/workspace.css",
)

_WORKBENCH_I18N_SOURCE_FILES = (
    "shared/i18n/extension-translations.jsx",
    "shared/i18n/catalog-en.jsx",
    "shared/i18n/catalog-zh.jsx",
    "shared/i18n/tool-name-aliases.jsx",
    "workbench-i18n.jsx",
)

_WORKBENCH_SETTINGS_SOURCE_FILES = (
    "features/settings/shared.jsx",
    "features/settings/remote.jsx",
    "features/settings/general.jsx",
    "settings-model-configuration.jsx",
    "features/settings/channels.jsx",
    "features/settings/agents.jsx",
    "features/settings/appearance.jsx",
    "features/settings/capabilities.jsx",
    "features/settings/data.jsx",
    "features/settings/about.jsx",
    "features/settings/custom-plugins.jsx",
    "features/settings/extensions.jsx",
    "features/settings/shortcuts.jsx",
    "features/settings/budget.jsx",
    "features/settings/media.jsx",
    "features/settings/index.jsx",
    "settings-overlay.jsx",
)


def _frontend_source(relative_files: tuple[str, ...]) -> str:
    frontend = Path(__file__).resolve().parent.parent / "src" / "webui" / "frontend"
    return "\n".join((frontend / relative).read_text(encoding="utf-8") for relative in relative_files)


def frontend_module_source(relative_file: str) -> str:
    """Read one frontend module for module-scoped contract tests."""
    return _frontend_source((relative_file,))


def workbench_shell_source() -> str:
    """Return the Workbench shell's explicit vertical module set."""
    return _frontend_source(_WORKBENCH_SHELL_SOURCE_FILES)


def workbench_style_source() -> str:
    """Return Workbench styles in their production cascade order."""
    return _frontend_source(_WORKBENCH_STYLE_FILES)


def workbench_i18n_source() -> str:
    """Return the split Workbench translation module set."""
    return _frontend_source(_WORKBENCH_I18N_SOURCE_FILES)


def workbench_settings_source() -> str:
    """Return the split Settings implementation in dependency order."""
    return _frontend_source(_WORKBENCH_SETTINGS_SOURCE_FILES)


def workbench_chat_source() -> str:
    """Return the explicit Chat module set for legacy characterization tests.

    New behavior belongs in native JavaScript tests. This adapter keeps older
    source-characterization tests useful while they are migrated incrementally.
    """
    return _frontend_source(_WORKBENCH_CHAT_SOURCE_FILES)


def workbench_chat_route_source() -> str:
    """Return Chat HTTP adapters and their delegated application implementation."""
    source_root = Path(__file__).resolve().parent.parent / "src"
    src = source_root / "route" / "workbench"
    files = (
        src / "chat.py",
        src / "chat_routes" / "context.py",
        src / "chat_routes" / "shared.py",
        src / "chat_routes" / "chats.py",
        src / "chat_routes" / "pinned_routes.py",
        src / "chat_routes" / "collection_routes.py",
        src / "chat_routes" / "voice_routes.py",
        src / "chat_routes" / "side_agents_routes.py",
        src / "chat_routes" / "detail_routes.py",
        src / "chat_routes" / "agent_config_routes.py",
        src / "chat_routes" / "groups_routes.py",
        src / "chat_routes" / "delete_routes.py",
        src / "chat_routes" / "fork_routes.py",
        src / "chat_routes" / "to_task_routes.py",
        src / "chat_routes" / "runs.py",
        src / "chat_routes" / "run_stream_routes.py",
        src / "chat_routes" / "run_send_routes.py",
        src / "chat_routes" / "run_respond_routes.py",
        src / "chat_routes" / "run_action_routes.py",
        src / "chat_routes" / "run_answer_routes.py",
        src / "chat_routes" / "conversation_context.py",
        src / "chat_routes" / "files.py",
        source_root / "cyrene" / "workbench" / "chat_external_turn_service.py",
        source_root / "cyrene" / "workbench" / "chat_reply_finalization_service.py",
    )
    return "\n".join(path.read_text(encoding="utf-8") for path in files)


def workbench_runtime_source() -> str:
    """Return the explicit Workbench application-service implementation set."""
    workbench = Path(__file__).resolve().parent.parent / "src" / "cyrene" / "workbench"
    files = sorted(path for path in workbench.glob("*.py") if path.name not in {"__init__.py", "runtime.py"})
    return "\n".join(path.read_text(encoding="utf-8") for path in files)


@pytest.fixture
def real_pillow_modules():
    """Temporarily undo legacy module-level Pillow shims for image tests."""
    previous_modules = {name: sys.modules.get(name) for name in ("PIL", "PIL.Image")}
    previous_image_attr = getattr(_REAL_PIL, "Image", None)
    sys.modules["PIL"] = _REAL_PIL
    sys.modules["PIL.Image"] = _REAL_PIL_IMAGE
    _REAL_PIL.Image = _REAL_PIL_IMAGE
    from agent.plugin import mcp_content

    previous_mcp_image = mcp_content.Image
    mcp_content.Image = _REAL_PIL_IMAGE
    try:
        yield _REAL_PIL_IMAGE
    finally:
        mcp_content.Image = previous_mcp_image
        _REAL_PIL.Image = previous_image_attr
        for name, previous in previous_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


@pytest.fixture
def real_pypdf_module():
    """Temporarily undo legacy module-level pypdf shims for PDF tests."""
    previous = sys.modules.get("pypdf")
    sys.modules["pypdf"] = _REAL_PYPDF
    try:
        yield _REAL_PYPDF
    finally:
        if previous is None:
            sys.modules.pop("pypdf", None)
        else:
            sys.modules["pypdf"] = previous


@pytest.hookimpl(hookwrapper=True, trylast=True)
def pytest_runtest_call(item):
    """Drain runtime work immediately after a test's call phase.

    This must remain a hook instead of an async autouse fixture.  Several
    knowledge-base fixtures call ``asyncio.run()`` during setup.  An async
    autouse fixture makes pytest-asyncio install the test loop *before* those
    fixtures run, and ``asyncio.run()`` then clears that loop before the test
    coroutine starts.  The call hook runs after the coroutine but before
    pytest-asyncio tears its loop down, which is the safe window for exercising
    the production shutdown aggregator.
    """
    yield

    # pytest-asyncio 0.26+ gives collector-scoped event-loop fixtures dynamic
    # names instead of exposing them consistently as ``event_loop``.  Locate
    # the fixture by value so teardown still runs across supported versions.
    loop = item.funcargs.get("event_loop")
    if loop is None:
        loop = next(
            (value for value in item.funcargs.values() if isinstance(value, asyncio.AbstractEventLoop)),
            None,
        )
    if loop is None:
        return
    if loop.is_closed() or loop.is_running():
        return

    from cyrene.runtime.lifecycle import shutdown_background_work
    from cyrene.runtime.task_lifecycle import cancel_and_wait

    loop.run_until_complete(shutdown_background_work())
    # Some tests intentionally exercise detached work that is outside the
    # production registries drained above.  Await its cancellation while the
    # pytest-owned loop is still alive so aiosqlite worker threads cannot post
    # completion callbacks into a loop that the next fixture has already
    # closed.
    pending = {task for task in asyncio.all_tasks(loop) if not task.done()}
    if pending:
        loop.run_until_complete(cancel_and_wait(pending, timeout=5.0))

    # aiosqlite resolves queued operations from a native worker thread.  An
    # awaited close can therefore finish on the event loop a few instructions
    # before that worker has consumed its stop sentinel.  Keep the loop alive
    # until every such worker has exited, so a late completion can never target
    # the closed per-test loop and get attributed to an unrelated later test.
    workers = [
        thread
        for thread in threading.enumerate()
        if "(_connection_worker_thread)" in thread.name
    ]
    if workers:
        gc.collect()
        deadline = time.monotonic() + 5.0
        while workers and time.monotonic() < deadline:
            loop.run_until_complete(asyncio.sleep(0))
            for worker in workers:
                worker.join(timeout=0.01)
            workers = [worker for worker in workers if worker.is_alive()]
        if workers:
            names = ", ".join(worker.name for worker in workers)
            raise RuntimeError(f"aiosqlite workers did not stop during test teardown: {names}")
