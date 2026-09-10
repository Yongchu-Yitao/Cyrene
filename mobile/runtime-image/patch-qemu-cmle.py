#!/usr/bin/env python3
"""Backport QEMU's AArch64 CMLE-zero encoding fix to pinned Limbo 6.0.1.

The prebuilt engines keep the instruction in a read-only opcode table. Correct
that constant, not generated code or signal handling. Refuse unknown binaries;
already patched binaries are accepted so asset generation is repeatable.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import struct

ENGINES = {
    "libqemu-system-aarch64.so": (
        "eced782f45dc56431e45a478d407148b84b8aeaaafa91c3b74020e663af951c5",
        0x5D7550,
    ),
    "libqemu-system-x86_64.so": (
        "a45356a2bb868722571a91064fa865903e343062094f9f3c019adf4a9b1410c3",
        0x527880,
    ),
}
OLD = struct.pack("<I", 0x2E20A800)
NEW = struct.pack("<I", 0x2E209800)


def patched_data(name: str, data: bytes) -> bytes:
    expected, offset = ENGINES[name]
    original = data[:offset] + OLD + data[offset + 4:]
    if (data[offset:offset + 4] not in (OLD, NEW)
            or hashlib.sha256(original).hexdigest() != expected):
        raise ValueError(f"Unrecognized Limbo engine: {name}")
    return data[:offset] + NEW + data[offset + 4:]


def patch_engine(path: Path) -> None:
    data = path.read_bytes()
    fixed = patched_data(path.name, data)
    if fixed != data:
        path.write_bytes(fixed)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("jni_dir", type=Path)
    args = parser.parse_args()
    engines = [args.jni_dir / "arm64-v8a" / name for name in ENGINES]
    engines = [path for path in engines if path.is_file()]
    if not engines:
        parser.error("No ARM64-host QEMU engines found")
    for path in engines:
        patch_engine(path)
