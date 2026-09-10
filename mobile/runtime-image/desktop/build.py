#!/usr/bin/env python3
"""Build a separate signed desktop asset directory; never replace Alpine assets."""
import argparse
import hashlib
import json
import re
from pathlib import Path
import shutil
import subprocess
import tempfile

NODE_URL = "https://nodejs.org/dist/v22.22.0/node-v22.22.0-linux-x64.tar.xz"
NODE_SHA256 = "9aa8e9d2298ab68c600bd6fb86a6c13bce11a4eca1ba9b39d79fa021755d7c37"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(*args):
    subprocess.run(list(map(str, args)), check=True)


def copy_source(source, target):
    # Explicit allowlist: do not send databases, workspace, credentials, .git,
    # node_modules, or the developer's Python environment to the Docker daemon.
    target.mkdir()
    for name in ("pyproject.toml", "LICENSE", "SOUL.default.md"):
        shutil.copy2(source / name, target / name)
    shutil.copytree(source / "src/cyrene", target / "src/cyrene", ignore=shutil.ignore_patterns(
        "__pycache__", "*.pyc", "*.sqlite3*", "*.db", "node_modules", ".env", ".DS_Store",
    ))
    if not (target / "src/cyrene/workbench/webui/static/app").is_dir():
        raise ValueError("Build the desktop Workbench before packaging")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--arch", choices=("amd64", "arm64"), default="arm64")
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[3],
                        help="Cyrene checkout (defaults to the enclosing monorepo)")
    parser.add_argument("--output", type=Path, required=True, help="New Android assets root; runtime/ is created inside it")
    parser.add_argument("--signing-key", type=Path, required=True)
    parser.add_argument("--disk-mib", type=int, default=8192)
    parser.add_argument("--memory-mib", type=int, default=4096)
    parser.add_argument("--build-arg", action="append", default=[], help="Additional Docker build argument, e.g. HTTPS_PROXY")
    parser.add_argument("--node-archive", type=Path, help="Optional offline copy of the pinned Node archive")
    args = parser.parse_args()
    if not 4096 <= args.disk_mib <= 32768 or not 512 <= args.memory_mib <= 4096:
        parser.error("disk must be 4096..32768 MiB; memory must be 512..4096 MiB")
    if args.output.exists():
        parser.error("output must not exist (existing images are never overwritten)")
    if not args.signing_key.is_file():
        parser.error("signing key is missing")
    guest_arch = "aarch64" if args.arch == "arm64" else "x86_64"
    console = "ttyAMA0" if args.arch == "arm64" else "ttyS0"
    node_url = NODE_URL.replace("linux-x64", "linux-arm64") if args.arch == "arm64" else NODE_URL
    node_sha256 = "1bf1eb9ee63ffc4e5d324c0b9b62cf4a289f44332dfef9607cea1a0d9596ba6f" if args.arch == "arm64" else NODE_SHA256
    here = Path(__file__).resolve().parent
    legacy = here.parent.parent / "runtime-app/src/main/assets/runtime"
    if shutil.which("docker") is None:
        parser.error("Docker with a running Linux engine is required")
    with tempfile.TemporaryDirectory(prefix="cyrene-desktop-build-") as temp:
        context = Path(temp)
        copy_source(args.source.resolve(), context / "source")
        shutil.copy2(here / "Dockerfile", context / "Dockerfile")
        shutil.copytree(here / "guest", context / "guest", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        node = context / "node.tar.xz"
        if args.node_archive:
            shutil.copy2(args.node_archive, node)
        else:
            run("curl", "-fL", "--retry", "3", "--retry-all-errors", "--connect-timeout", "30",
                "--max-time", "300", node_url, "-o", node)
        if digest(node) != node_sha256:
            raise ValueError("Node archive SHA-256 mismatch")
        output = context / "result"
        run("docker", "buildx", "build", "--platform", f"linux/{args.arch}", "--target", "bundle",
            "--build-arg", f"DISK_MIB={args.disk_mib}",
            "--build-arg", f"NODE_SHA256={node_sha256}",
            *[part for value in args.build_arg for part in ("--build-arg", value)],
            "--output", f"type=local,dest={output}", context)
        manifest = json.loads((legacy / "manifest.json").read_text())
        # Verify the pinned inputs before carrying firmware into a new signed bundle.
        run("openssl", "dgst", "-sha256", "-verify", legacy / "runtime-public-key.pem",
            "-signature", legacy / "manifest.sig", legacy / "manifest.json")
        for entry in [manifest["host_resolver"], *manifest["firmware"]]:
            if digest(legacy / entry["file"]) != entry["sha256"]:
                raise ValueError("Legacy firmware digest mismatch")
            shutil.copy2(legacy / entry["file"], output / entry["file"])
        version = f"debian12-desktop-{guest_arch}-v1-" + digest(output / "rootfs-template.ext4.gzip")[:12]
        manifest.update(version=version, guest_arch=guest_arch, desktop_backend=True, memory_mib=args.memory_mib,
                        kernel_append=f"console={console} root=/dev/vda rw net.ifnames=0 panic=-1 loglevel=4")
        for key in ("kernel", "initramfs", "rootfs"):
            manifest[key]["sha256"] = digest(output / manifest[key]["file"])
        manifest["rootfs"]["unpacked_sha256"] = (output / "rootfs.sha256").read_text().split()[0]
        manifest["rootfs"]["size"] = int((output / "rootfs.size").read_text())
        (output / "manifest.json").write_text(json.dumps(manifest, sort_keys=True) + "\n")
        run("openssl", "dgst", "-sha256", "-sign", args.signing_key.resolve(),
            "-out", output / "manifest.sig", output / "manifest.json")
        run("openssl", "pkey", "-in", args.signing_key.resolve(), "-pubout",
            "-out", output / "runtime-public-key.pem")
        shutil.copy2(legacy / "LIMBO-GPL-2.0.txt", output / "LIMBO-GPL-2.0.txt")
        (output / "NOTICE.txt").write_text(
            "Cyrene desktop guest: Debian 12, Python 3.12, CPU ONNX Runtime.\n"
            "Engine: Limbo 6.0.1 / QEMU 5.1.0, GPL-2.0.\n"
            "Engine source: https://github.com/limboemu/limbo/tree/v6.0.1-LimboEmulator\n"
            "Package versions: installed-requirements.txt; guest /usr/share/doc/*/copyright.\n")
        args.output.mkdir(parents=True)
        shutil.copytree(output, args.output / "runtime")
        # Same source snapshot as the guest, never a separately maintained UI.
        # Only exact matching versioned URLs may bypass the guest HTTP server.
        frontend = context / "source/src/cyrene/workbench/webui/static/app"
        match = re.search(r'\?v=([A-Za-z0-9._-]+)', (frontend / "index.html").read_text())
        if match:
            shutil.copytree(frontend, args.output / "workbench/app")
            (args.output / "workbench/version.txt").write_text(match.group(1) + "\n")
    print(f"Signed desktop assets: {args.output.resolve()}")


if __name__ == "__main__":
    main()
