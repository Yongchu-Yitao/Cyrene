# Semantic App Use

Cyrene exposes two explicit desktop-control schemes:

- `desktop.use` is the existing visual-only scheme. Connect with `mode="visual"`; it uses window captures, calibrated coordinates, and OS input, and never reads or invokes the accessibility tree.
- `desktop.semantic.*` is accessibility-tree-only. It consists of `AppUISnapshot`, `AppUIInspect`, `AppUIClick`, `AppUIDoubleClick`, `AppUIType`, `AppUIScroll`, and `AppUIDrag`.

The schemes share target discovery, but create separate sessions and never call each other internally. There is no hybrid mode. Linux exposes only the semantic scheme through native AT-SPI2/D-Bus. macOS semantic snapshots, inspection, and actions use native `AXUIElement` traversal; Windows uses UI Automation Raw View. The macOS System Events/JXA bridge is not used for semantic tree operations.

The agent may choose either scheme on macOS or Windows. Semantic is usually the better first choice for standard labeled controls; visual is usually better for canvas or custom-rendered UI. After a definite failure that dispatched no action, disconnect and try the other scheme once. An `uncertain` result must be verified before switching so the second scheme cannot repeat an action that may already have run.

Start semantic use with `AppUISnapshot(operation="list_targets")`, connect, then take a snapshot. A snapshot returns only the current semantic layer: its scope root and meaningful direct children, collapsing inert provider wrappers. Use `AppUIInspect` on a returned node to descend one layer. A write must echo the exact `session_id`, `snapshot_id`, `revision`, `node_id`, and `action_id` returned by the tree, plus a human-readable `reason` and unique `idempotency_key`. IDs are opaque leases; native accessibility references, selectors, scripts, screenshots, focus changes, and coordinates are not accepted.

Semantic availability is a recoverable state machine: `initializing`, `available`, `partial`, `unavailable`, `permission_required`, or `provider_error`. A timeout or an early container-only tree remains `initializing`; use `reprobe` or a fresh broader snapshot. Only repeated completed container-only probes become `unavailable`. Generic actionable labels such as Group/Application do not count as meaningful coverage. On macOS and Windows, `visual_recommended=true` includes an explicit visual handoff so the agent can disconnect and switch without guessing; Linux reports the limitation but never offers its unsupported visual scheme.

Linux packages require a running AT-SPI2 accessibility bus (`at-spi2-core`). Chromium/Electron targets may need renderer accessibility enabled by their own application configuration.
