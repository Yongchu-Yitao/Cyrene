"""Application-wide assistant identity and project-free execution scope.

The scope adapts the existing chat kernel; it is never stored in the project list.
"""

ASSISTANT_ID = "assistant_global"
ASSISTANT_SCOPE_ID = "__personal_assistant__"


def assistant_scope():
    from cyrene.config import WORKSPACE_DIR

    return {"id": ASSISTANT_SCOPE_ID, "name": "Cyrene", "workspacePath": str(WORKSPACE_DIR),
            "workspacePathSource": "user", "context": {}, "model": ""}
