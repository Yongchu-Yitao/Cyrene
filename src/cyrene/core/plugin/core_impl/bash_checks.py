"""Explicit filesystem postconditions, independent of shell output or wording."""

import os
from pathlib import Path
import stat


FILE_CHECKS_SCHEMA = {
    "type": "array", "minItems": 1, "maxItems": 20,
    "description": "Check exact paths after the command in the same call. Paths are relative to the initial workspace, unaffected by shell cd; no shell variables or glob expansion. Checks use lstat, so a dangling symlink exists and has type symlink. A failed check does not mean the command did not execute: inspect state before retrying mutations.",
    "items": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "path": {"type": "string", "minLength": 1},
            "expected": {"type": "string", "enum": ["absent", "exists", "file", "directory", "symlink"]},
        },
        "required": ["path", "expected"],
    },
}


def prepare_file_checks(arguments, context):
    """Validate before executing any shell side effect and fix the initial cwd."""
    if "file_checks" not in arguments:
        return []
    raw = arguments["file_checks"]
    if not isinstance(raw, list) or not 1 <= len(raw) <= 20:
        raise ValueError("file_checks must contain between 1 and 20 checks")
    workspace = Path(context.workspace).expanduser().resolve() if context.workspace is not None else Path.cwd()
    checks = []
    for check in raw:
        if not isinstance(check, dict) or set(check) != {"path", "expected"}:
            raise ValueError("Each file check requires path and expected")
        path, expected = check["path"], check["expected"]
        if not isinstance(path, str) or not path.strip() or "\0" in path:
            raise ValueError("File check path must be nonempty and contain no NUL")
        if expected not in ("absent", "exists", "file", "directory", "symlink"):
            raise ValueError("Unknown file check expectation")
        candidate = Path(path).expanduser()
        # Preserve the last component: checking a link is not checking its target.
        absolute = Path(os.path.abspath(candidate if candidate.is_absolute() else workspace / candidate))
        checks.append((absolute, expected))
    return checks


def inspect_file_checks(checks):
    results = []
    for path, expected in checks:
        result = {"path": str(path), "expected": expected}
        try:
            mode = path.lstat().st_mode
            actual = ("symlink" if stat.S_ISLNK(mode) else "file" if stat.S_ISREG(mode)
                      else "directory" if stat.S_ISDIR(mode) else "other")
        except (FileNotFoundError, NotADirectoryError):
            actual = "absent"
        except OSError as exc:
            results.append({**result, "status": "error", "error": str(exc), "errno": exc.errno})
            continue
        passed = actual != "absent" if expected == "exists" else actual == expected
        results.append({**result, "actual": actual, "status": "passed" if passed else "failed"})
    return {
        "verification_status": "passed" if all(r["status"] == "passed" for r in results) else "failed",
        "file_checks": results,
    }
