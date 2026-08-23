"""Stable compatibility entry point for the split Workbench services.

The historical module is kept as an alias, rather than a copied export table,
so integrations that patch a runtime seam keep observing the exact same module
state. New code should import the Project, Task, Planning, Artifact, or Chat
application service directly.
"""

from __future__ import annotations

import sys
from types import ModuleType

from cyrene.workbench import runtime_implementation as _implementation


class _RuntimeCompatibilityModule(ModuleType):
    """Forward historical persistence state patches to the owning repository."""

    _repository_state = {
        "_CONFIGURED_WORKBENCH_STORE",
        "_WORKBENCH_STORE",
        "_WORKBENCH_STORE_LOCK",
        "_db_path",
    }

    _initialization_seams = {
        "_workbench_classify_plan_routing",
        "_workbench_run_explore_agent",
    }

    _presentation_state = {
        "CONVERSATIONS_DIR",
        "DATA_DIR",
        "STATE_FILE",
        "WORKSPACE_DIR",
        "_SERVER_STARTED_AT",
        "_build_sessions",
        "_model_pricing",
        "_resolve_local_username",
        "get_live_rounds",
    }

    def __setattr__(self, name: str, value: object) -> None:
        if name in self._repository_state:
            from cyrene.workbench import project_repository

            setattr(project_repository, name, value)
        elif name == "_call_llm":
            from cyrene.workbench import generation_gateway

            generation_gateway.call_llm = value
        elif name in self._initialization_seams:
            from cyrene.workbench import task_initialization_runtime

            setattr(task_initialization_runtime, name, value)
        elif name == "_EXPORTS_DIR":
            from cyrene.workbench import artifact_runtime

            artifact_runtime._EXPORTS_DIR = value
        elif name == "_read_workbench_store_lightweight":
            from cyrene.workbench import project_repository

            project_repository._read_workbench_store_lightweight = value
        elif name in self._presentation_state:
            from cyrene.workbench import presentation_runtime

            setattr(presentation_runtime, name, value)
            if name == "WORKSPACE_DIR":
                from cyrene.workbench import artifact_runtime, project_runtime

                artifact_runtime.WORKSPACE_DIR = value
                project_runtime.WORKSPACE_DIR = value
                from cyrene.runtime import data_reset

                data_reset.WORKSPACE_DIR = value
        super().__setattr__(name, value)

# Preserve the historical module identity and mutable compatibility seams.
_implementation.__class__ = _RuntimeCompatibilityModule
sys.modules[__name__] = _implementation
