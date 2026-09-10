#!/usr/bin/env python3
"""Add pinned upstream AArch64 engines without replacing shared JNI libraries."""
import hashlib
from pathlib import Path
import subprocess
import tempfile
import zipfile
import runpy

cmle_patch = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'patch-qemu-cmle.py'))

URL = 'https://github.com/limboemu/limbo/releases/download/v6.0.1-LimboEmulator/limbo-android-arm-6.0.1-qemu-5.1.0.apk'
SHA256 = 'ae22305b5f79815f738b752a6cfa0b00e6bdd80334ff3df8fb986ed4c1d1442b'
root = Path(__file__).resolve().parents[2] / 'runtime-app/src/main/jniLibs'
with tempfile.TemporaryDirectory(prefix='cyrene-arm-engine-') as temp:
    apk = Path(temp) / 'limbo.apk'
    subprocess.run(['curl', '-fL', '--retry', '3', '--max-time', '300', URL, '-o', str(apk)], check=True)
    assert hashlib.sha256(apk.read_bytes()).hexdigest() == SHA256, 'Upstream APK digest mismatch'
    with zipfile.ZipFile(apk) as archive:
        outputs = []
        for abi in ('arm64-v8a', 'x86_64'):
            for name in archive.namelist():
                if not name.startswith(f'lib/{abi}/') or not name.endswith('.so'):
                    continue
                destination = root / abi / Path(name).name
                data = archive.read(name)
                if abi == 'arm64-v8a' and destination.name in cmle_patch['ENGINES']:
                    data = cmle_patch['patched_data'](destination.name, data)
                if destination.exists():
                    existing = destination.read_bytes()
                    if abi == 'arm64-v8a' and destination.name in cmle_patch['ENGINES']:
                        existing = cmle_patch['patched_data'](destination.name, existing)
                        if existing == data:
                            outputs.append((destination, data))
                    assert existing == data, f'JNI compatibility mismatch: {destination}'
                elif destination.name == 'libqemu-system-aarch64.so':
                    outputs.append((destination, data))
                else:
                    raise RuntimeError(f'Missing shared JNI dependency: {destination}')
        for destination, data in outputs:
            destination.write_bytes(data)
            print(destination)
