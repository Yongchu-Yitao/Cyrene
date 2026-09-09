> 0.3.0 更新：运行时已作为 library 合入主 App。下面关于旧设备/双包的内容是历史记录；当前只构建并安装 `:app:assembleDebug` 的 APK。默认资产目录 `build/unified-assets`，或使用 `-PcyreneDesktopAssets`。

# Full desktop backend on Android (experimental integration)

This adds a **full Python backend** image and a signature-protected Binder lifecycle API.
The Android launcher now opens the existing desktop Workbench in a WebView through
`DesktopRuntimeClient` and an authenticated loopback proxy. The legacy Kotlin UI has been removed; its stored conversations are not migrated.
The proxy injects backend authentication for HTTP, SSE and WebSocket traffic; the
WebView receives a separate HttpOnly session cookie. Do not put the backend token
in URLs, JavaScript, logs or public intents. See
`project-notes/android-workbench-integration.zh-CN.md` for the verified scope.

## Implemented

- Separate ARM64 (default) or x86_64 Debian 12 / Python 3.12 image with desktop source, existing built Web
  UI, Bash, Git, Node, MCP dependencies, CPU ONNX Runtime and Playwright Chromium.
- Signed manifest, source allowlist, recorded resolved Python dependencies,
  configurable disk/memory, and existing pinned Limbo engine/firmware.
- Guest systemd backend lifecycle, authenticated health checks and transparent
  TCP forwarding (including SSE/WebSockets). Desktop auth middleware is retained.
- Binder `desktop_start`, `desktop_status`, `desktop_stop`; start returns the
  random loopback endpoint and per-VM credential only through the protected API.
- User-initiated foreground-service lifetime with a notification Stop action; no
  automatic restart after user/OS termination. Uses Android `specialUse` with a
  declared local-VM subtype; distribution review and OEM behavior need validation.
  Android 13+ notification permission must be granted for the notification to
  appear in the drawer; this change does not add a permission-prompt UI.
- Separate versioned persistent disk; the existing Alpine disk is not replaced.
- Debug-only backend/import/tool probe. No model calls or credentials required.

## Current boundaries

The engine is **QEMU 5.1.0 / TCG**, with x86_64 and AArch64 guest engines.
`install-arm64-engine.py` pins the upstream ARM APK and checks every shared JNI
library against the existing binaries before adding the AArch64 engine. The
signed manifest selects `virt`/`ttyAMA0` for ARM64 or `pc`/`ttyS0` for x86_64.
Matching the phone's architecture does not itself enable hardware virtualization:
the connected OnePlus device has no `/dev/kvm`. CPU inference replaces CUDA;
MLX is unavailable in this Linux guest. Office host integration and native mobile
audio/files still need bridges. Kotlin conversation/model configuration migration
is not implemented. Workbench chat has been verified against a remote Gemma4 model,
but background work is not guaranteed and the legacy Kotlin UI keeps its original execution path. Every image revision gets a separate disk: existing data remains on
its old disk and is **not automatically migrated**. Disk extraction keeps a template and writable disk. Zero regions are stored as
sparse holes; worst-case storage remains twice configured disk size plus
compressed assets. The ARM64 experiment used about 4.1 GiB per 8 GiB disk. Android resources must be measured on hardware.

## Build

Requires Docker Buildx with a running Linux engine and adequate disk space,
Python 3.12+, OpenSSL, JDK 17 and Android SDK 35. Build desktop Web UI first using
its normal npm build command. Base OS/dependency ranges are not a reproducible
lock: record the resulting signed assets and installed-requirements.txt for QA.

From the mobile checkout:

```sh
# Use a dedicated development key for experiments; never commit private keys.
python3 runtime-image/desktop/install-arm64-engine.py
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out /tmp/cyrene-desktop-dev.pem
python3 runtime-image/desktop/build.py \
  --arch arm64 \
  --source .. \
  --output "$PWD/build/desktop-assets" \
  --signing-key /tmp/cyrene-desktop-dev.pem
./gradlew :app:assembleDebug -PcyreneDesktopAssets="$PWD/build/desktop-assets" \
  -Dorg.gradle.jvmargs=-Xmx6g --max-workers=2 --no-daemon
```

Output must be a new directory. Build context excludes databases, developer
workspaces, .env and node_modules; signing happens outside Docker. The resulting main APK includes the runtime library. The image
signing key is independent of the APK signing identity. A stock build continues to use
the existing signed Alpine image.

## Device probe

First validate the complete main-app/Binder/foreground-service path with both
debug APKs installed (same Android signing identity):

```sh
adb shell am start -n ai.cyrene.mobile/.DesktopBackendProbeActivity
adb shell run-as ai.cyrene.mobile cat files/desktop-backend-probe.json
```

Wait for Activity completion before reading the result. It starts and stops the
backend and never exports the token. Run this from a visible user action, not a
background broadcast. The initial acceptance target is an 8 GiB Android device
with a 2 GiB guest; no 8 GiB-device performance claim is made yet.

With the desktop Runtime APK installed and no other Runtime session running
(the main app must release its Binder connection before this separate-process
probe can acquire the VM disk lock):

```sh
adb shell am start -n ai.cyrene.mobile.runtime/.DesktopRuntimeProbeActivity
# Wait for Activity completion; probe may need several minutes under emulation.
adb shell run-as ai.cyrene.mobile.runtime cat files/desktop-runtime-probe.json
```

The runtime probe verifies filesystem/SQLite, Bash/Git/Node/uv, an actual stdio
MCP tool call, and an offline Chromium page click/screenshot. It does **not** prove
real model execution, Subagent orchestration, task recovery, or performance. Those
remain device acceptance work. Never publish an old probe result as a new run.

## Local verification

```sh
uv run --project .. pytest runtime-image/desktop/test_build.py -q
./gradlew :runtime-app:testDebugUnitTest :app:assembleDebug
```

The build host now has a dedicated Colima Docker engine. A OnePlus PLZ110
(Android 16, about 12 GB physical RAM) was used before the user disconnected it; the existing Alpine guest
passed its device regression probe after the Android changes. This is not an
8 GB hardware benchmark. Full desktop image acceptance is still pending and
must pass before enabling this backend as the default local execution mode.

For amd64, the image installer downloads the Playwright-selected Chrome for Testing
revision from Google's official storage origin, bypassing a stalled CDN. Its
temporary installer URL-path adjustment is restored after installation.

ARM64 uses Playwright's normal ARM64 browser artifacts. As of the latest user
request, device testing has stopped and validation is moving to the dedicated
`Cyrene_ARM64_8G` Android 35 / ARM64 AVD (8 GB RAM, 16 GB data partition).

For slow software-emulation diagnostics only, both desktop debug probe Activities
accept `--el timeout_ms 900000` (bounded to 5–15 minutes). Production startup
budgets are unchanged. Do not treat an extended diagnostic pass as acceptable
interactive startup performance. ARM64 currently uses `cortex-a72` with TCG.
The native executor uses a process-lifetime worker to avoid invoking QEMU TLS
destructors after Limbo unloads the library; repeated-session stability still
requires acceptance testing.

The generic debug `RuntimeProbeActivity` also accepts `--el timeout_ms 900000`
for bounded guest commands supplied through `command_b64`. Its default remains
180 seconds. This entry point writes `files/probe-result.json`, including the
guest command exit code; a successful transport result alone is not a passed
guest command. Authentication health requests allow up to five seconds each,
within the caller's startup deadline.

Latest ARM64 AVD acceptance: main-app Binder startup and HTTP 401/200 checks
passed once; basic files/SQLite/Bash/Git/Node/uv/Codex version checks passed.
The final combined tool probe crashed the native AArch64 QEMU worker with
SIGILL, so MCP/Chromium acceptance remains incomplete. Repeated startup and
graceful Python shutdown also need work. See
`project-notes/android-desktop-backend-device-validation.zh-CN.md` for evidence.
