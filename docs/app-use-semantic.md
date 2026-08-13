# Semantic App Use

Cyrene exposes two explicit desktop-control schemes:

- `desktop.use` is the existing visual scheme. Connect with `mode="visual"`; it may capture a target window and use calibrated coordinates or foreground input.
- `desktop.semantic.*` is accessibility-tree-only. It consists of `AppUISnapshot`, `AppUIInspect`, `AppUIClick`, `AppUIDoubleClick`, `AppUIType`, `AppUIScroll`, and `AppUIDrag`.

The schemes share target discovery and session identity, but never fall back to each other implicitly. Linux exposes only the semantic scheme through AT-SPI2/D-Bus. macOS uses AX and Windows uses UI Automation.

Start semantic use with `AppUISnapshot(operation="list_targets")`, connect, then take a snapshot. A write must echo the exact `session_id`, `snapshot_id`, `revision`, `node_id`, and `action_id` returned by the tree, plus a human-readable `reason` and unique `idempotency_key`. IDs are opaque leases; native accessibility references, selectors, scripts, screenshots, focus changes, and coordinates are not accepted.

Semantic availability is a recoverable state machine: `initializing`, `available`, `partial`, `unavailable`, `permission_required`, or `provider_error`. A timeout or an early container-only tree remains `initializing`; use `reprobe` or a fresh broader snapshot. Only repeated completed container-only probes become `unavailable`.

Linux packages require a running AT-SPI2 accessibility bus (`at-spi2-core`). Chromium/Electron targets may need renderer accessibility enabled by their own application configuration.
