"""Regression guard for the host instruction that crashed Android Chromium."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "qemu_cmle_patch", ROOT / "mobile/runtime-image/patch-qemu-cmle.py"
)
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)


@pytest.mark.parametrize("name", patch.ENGINES)
def test_pinned_engine_backport_and_idempotence(tmp_path, name):
    installed = ROOT / "mobile/runtime-app/src/main/jniLibs/arm64-v8a" / name
    data = installed.read_bytes()
    _, offset = patch.ENGINES[name]
    assert data[offset:offset + 4] == patch.NEW
    # Restore the exact upstream input, then verify that only the opcode changes.
    target = tmp_path / name
    target.write_bytes(data[:offset] + patch.OLD + data[offset + 4:])
    patch.patch_engine(target)
    assert target.read_bytes() == data
    patch.patch_engine(target)
    assert target.read_bytes() == data
    damaged = bytearray(data)
    damaged[-1] ^= 1
    target.write_bytes(damaged)
    with pytest.raises(ValueError, match="Unrecognized"):
        patch.patch_engine(target)
    assert target.read_bytes() == damaged
