"""Run pip's consistency check with the one intentional Windows substitution."""

from __future__ import annotations

import re
import subprocess
import sys


_ALLOWED_WINDOWS_CONFLICTS = (
    re.compile(r"^simplexng \S+ requires uvloop, which is not installed\.$"),
)


def main() -> int:
    result = subprocess.run(
        [sys.executable, "-m", "pip", "check"],
        check=False,
        capture_output=True,
        text=True,
    )
    lines = [
        line.strip()
        for line in (result.stdout + "\n" + result.stderr).splitlines()
        if line.strip()
    ]
    expected_missing_for_native_woa = {
        "simplexng", "rapidocr",
    } if __import__("platform").machine().lower() in {"arm64", "aarch64"} else set()
    unexpected = [
        line
        for line in lines
        if line != "No broken requirements found."
        and not any(pattern.fullmatch(line) for pattern in _ALLOWED_WINDOWS_CONFLICTS)
        and not any(
            line.startswith(f"cyrene ") and f" requires {package}, which is not installed." in line
            for package in expected_missing_for_native_woa
        )
    ]
    for line in lines:
        print(line)
    if unexpected:
        print("Unexpected Windows dependency conflicts:", file=sys.stderr)
        for line in unexpected:
            print(f"- {line}", file=sys.stderr)
        return result.returncode or 1
    print("Windows dependency check passed; uvloop is replaced by winloop.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
