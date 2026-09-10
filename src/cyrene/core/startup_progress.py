"""Optional, atomic startup telemetry shared by core startup components."""
from __future__ import annotations

import json
import os
from pathlib import Path


def report_startup_progress(stage: str, completed: int = 0, total: int = 0) -> None:
    destination = os.environ.get("CYRENE_STARTUP_PROGRESS_PATH", "")
    if not destination:
        return
    path = Path(destination)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps({
            "stage": stage, "completed": completed, "total": total,
        }), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        # Progress must never prevent startup (e.g. read-only/full filesystem).
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
