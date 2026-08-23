"""Version contract shared by the local Office gateway and agent toolkit."""

from __future__ import annotations

import hashlib
from pathlib import Path


PROTOCOL_VERSION = 4
KIT_VERSION = "1.3.0"
SCHEMA_HASH = hashlib.sha256(
    b"cyrene-ppt-kit:v4:camelcase:live-revision-selection:typed-batch:slide-spec:progressive"
).hexdigest()[:16]

READ_ONLY_METHODS = frozenset({
    "ppt.get_context",
    "ppt.inspect",
    "ppt.list_slides",
    "ppt.get_slide",
    "ppt.list_shapes",
    "ppt.get_shape",
    "ppt.read_text",
    "ppt.get_selection",
    "ppt.get_master",
    "ppt.get_theme",
    "ppt.render_slide",
    "ppt.verify_slide",
    "ppt.check_overflow",
    "ppt.check_overlap",
    "ppt.check_contrast",
    "ppt.compare_before_after",
})


def static_build_hash(static_dir: Path | None = None) -> str:
    directory = static_dir or Path(__file__).with_name("static")
    digest = hashlib.sha256()
    for name in ("taskpane.html", "taskpane.css", "taskpane.js"):
        path = directory / name
        digest.update(name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def expected_handshake(static_dir: Path | None = None) -> dict[str, object]:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "kitVersion": KIT_VERSION,
        "schemaHash": SCHEMA_HASH,
        "buildHash": static_build_hash(static_dir),
    }


__all__ = [
    "KIT_VERSION",
    "PROTOCOL_VERSION",
    "READ_ONLY_METHODS",
    "SCHEMA_HASH",
    "expected_handshake",
    "static_build_hash",
]
