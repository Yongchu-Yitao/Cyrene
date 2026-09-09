#!/usr/bin/env python3
"""Verify every manifest input before CI packages the desktop runtime."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def verify(assets):
    runtime = assets.resolve() / "runtime"
    subprocess.run([
        "openssl", "dgst", "-sha256", "-verify", str(runtime / "runtime-public-key.pem"),
        "-signature", str(runtime / "manifest.sig"), str(runtime / "manifest.json"),
    ], check=True)
    manifest = json.loads((runtime / "manifest.json").read_text())
    if manifest.get("desktop_backend") is not True or manifest.get("guest_arch") != "aarch64":
        raise ValueError("Expected an ARM64 desktop image")
    entries = [manifest[key] for key in ("kernel", "initramfs", "rootfs", "host_resolver")]
    entries.extend(manifest["firmware"])
    for entry in entries:
        path = (runtime / entry["file"]).resolve()
        if not path.is_relative_to(runtime):
            raise ValueError("Manifest path escapes runtime directory")
        with path.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != entry["sha256"]:
            raise ValueError(f"Digest mismatch: {entry['file']}")
    print(f"Verified {len(entries)} runtime inputs: {manifest['version']}")


if __name__ == "__main__":
    verify(Path(sys.argv[1]))
