"""Run pip's consistency check with the intentional Windows substitutions."""

from __future__ import annotations

import re
import subprocess
import sys


_ALLOWED_WINDOWS_CONFLICTS = (
    re.compile(r"^simplexng \S+ requires uvloop, which is not installed\.$"),
    # openai-codex's metadata requires openai-codex-cli-bin, but the CLI
    # binary is downloaded on demand by cyrene.model_runtime.codex_cli and
    # is intentionally not installed in the build environment.
    re.compile(r"^openai-codex \S+ requires openai-codex-cli-bin, which is not installed\.$"),
    # The build swaps opencv-python for opencv-python-headless on purpose:
    # cv2 is excluded from the bundle (OCR downloads the full wheel on
    # demand) and the GUI wheel only adds build-environment deps.  pip
    # check still reports rapidocr's metadata requirement on
    # opencv-python even though the headless wheel provides the same cv2.
    re.compile(r"^rapidocr \S+ requires opencv-python, which is not installed\.$"),
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
            line.startswith("cyrene ") and f" requires {package}, which is not installed." in line
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
