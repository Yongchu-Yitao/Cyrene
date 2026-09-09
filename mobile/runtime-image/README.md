# Cyrene Mobile Linux Runtime

`runtime-app` bundles a real QEMU 5.1.0 system emulator and a signed Alpine
Linux 3.24 image. It does not execute model-provided commands with Android's
`/system/bin/sh`.

## Runtime model

- One QEMU VM is owned by the Runtime service and shared by every local chat.
- A 512 MiB ext4 disk is the persistent Alpine root filesystem. `/workspace`,
  files written by any session, and packages installed with `apk add` survive
  session changes and Runtime process restarts.
- The fixed Binder surface is `Read`, `Write`, `Edit`, `Glob`, `Grep`, and
  `Bash`; all operations execute inside the guest and use `/workspace` as cwd.
- The companion APK has a separate UID. It has `INTERNET` only so QEMU slirp
  can give the guest NAT access. Model configuration and API keys remain in the
  main app and are never included in Runtime IPC or guest state.
- The main app can bind through a signature-level permission only.

## Image integrity and provenance

`build-runtime-bundle.sh` pins SHA-256 digests for the Limbo/QEMU APK, Alpine
minirootfs, and Alpine kernel package. It creates the kernel, bootstrap
initramfs, persistent rootfs template, firmware set, license notice, and a
manifest containing every asset digest. `RuntimeImageVerifier` verifies the RSA
manifest signature and asset hashes before QEMU starts.

Limbo/QEMU is GPL-2.0. The corresponding source URL and version are recorded in
the bundle `NOTICE.txt` and build script. Release builds must use a protected
release signing key; the repository contains only the public key and signed
artifacts, never the private key.

## Build

Requirements: `curl`, `unzip`, `bsdtar`, `cpio`, OpenSSL, and `mkfs.ext4` from
e2fsprogs.

```bash
export CYRENE_RUNTIME_SIGNING_KEY=/secure/path/runtime-signing-key.pem
./runtime-image/build-runtime-bundle.sh
```

For cached/offline inputs, set `CYRENE_LIMBO_URL`,
`CYRENE_MINIROOTFS_URL`, and `CYRENE_KERNEL_URL` to pinned local `file://`
URLs. Set `MKFS_EXT4` when it is not on `PATH`.

Debug builds expose two emulator probes:

- `ai.cyrene.mobile.runtime.DEBUG_PROBE` validates the image, QEMU guest,
  network, package installation, persistence, and two-session sharing.
- `ai.cyrene.mobile.DEBUG_RUNTIME_PROBE` validates the full main-app → signed
  Binder → Runtime service → QEMU guest path.
