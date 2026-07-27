# Changelog

[中文](CHANGELOG.md) · [English](CHANGELOG.en.md)

This English edition preserves the release history of the Chinese changelog.
The Chinese edition remains the most detailed record for older releases.

## [0.7.0b5] - 2026-07-27

This is the fifth `0.7.0` beta and contains every change since
`v0.7.0-beta.4`. It redesigns the primary Cyrene-to-Cyrene Agent control path:
the controller Agent no longer needs to create a remote conversation, type a
natural-language instruction, and start a second Agent for ordinary work.
Instead, it discovers, describes, and invokes capabilities from tool packages
that the controlled device explicitly grants. Every invocation is approved
locally in the controller conversation against the exact device, project,
capability, and arguments, while the controlled device independently enforces
trust, project scope, package grants, schemas, idempotency, and audit.

The release also fixes successful `202 Accepted` responses being reported as
remote failures, high-frequency run polling, remotely created chats not
appearing live on the controlled computer, compatibility runs becoming stuck
at approvals, listener-port conflicts disabling all remote control, and
Workbench context-picker interaction and fingerprint exposure.

### Direct remote Harness: the new preferred control path

- **New `RemoteHarness` Agent tool** — The controller can `discover`,
  `describe`, and `invoke` against a paired device explicitly selected in the
  current chat. It reuses Cyrene's progressive tool gateway and stable
  capability IDs without creating a remote chat or starting a second Agent.
- **The Agent prefers direct invocation by default** — Main-agent guidance now
  routes ordinary remote work through `remote.harness`: inspect received
  package grants, discover relevant capabilities, describe their exact schemas,
  and invoke. `RemoteCyreneAction` and `RunRemoteCyrene` remain compatibility
  fallbacks for explicit remote conversations or targets without direct Harness
  support.
- **Exact approval happens on the controller** — Before `invoke` crosses the
  device boundary, the controller's current permission resolver receives the
  device ID, project ID, package, capability ID, and complete argument object.
  `default` can ask the user, `auto` can use the local reviewer, and discovery
  and description remain read-only.
- **The controlled device retains final enforcement** — Only the fixed
  `harness.discover`, `harness.describe`, and `harness.invoke` commands are
  accepted after paired identity, signature, E2EE envelope, directional grant,
  and shared-project checks. Package membership, capability schema, local
  enablement, and runtime availability are validated again before execution.
- **Execution is bound to the shared project workspace** — The composition root
  injects the Bot and runtime database into the remote executor. Each call gets
  an isolated `remote_harness` context, stable session/call identity, target
  project workspace, and catalog snapshot, and the binding is reset in
  `finally` so permissions cannot leak into later conversations.
- **No arbitrary execution backdoor** — The protocol exposes no arbitrary HTTP
  method or URL, Python function, database statement, raw shell RPC, or hidden
  concrete tool name. Only stable catalog capability IDs inside an explicitly
  granted package can run. `remote_tools` cannot itself be granted remotely,
  preventing recursive device-control chains.
- **Structured results and errors survive the hop** — Harness results preserve
  status, capability identity, and result text. Unsupported packages, denied
  grants, missing projects, schema or capability errors, transport failures,
  and timeouts remain distinguishable.

### Per-device remote tool-package switches

- **Compatibility capabilities and direct packages are separated** — Remote
  settings retain Chat, Run, Task, Approval, and Artifact commands while adding
  a dedicated “Directly callable tool packages” section.
- **The UI reuses Settings → Capabilities controls** — Pairing invitations and
  each trusted-device grant editor use the established field rows,
  descriptions, and standard toggles with the same names as local tool-package
  settings.
- **Authorization is stored per trusted device** — Stable
  `toolpack:<wire_name>` grants flow through signed pairing bundles,
  directional peer grants, encrypted grant synchronization, and audit.
  Changing one controller does not broaden another controller's authority.
- **No silent privilege expansion** — Direct packages start disabled during
  pairing and existing peers gain none during upgrade. The user must explicitly
  enable each package.
- **Twelve package classes are independently grantable** — Code, Browser,
  Desktop, Memory, Knowledge, Task, Entity, Map, Subagent, Delivery, Skill, and
  Integration are available. A package disabled in local
  Settings → Capabilities still cannot execute even when its remote grant is on.
- **Discovery and execution both filter grants** — An ungranted package cannot
  expose capabilities through discovery, and a known ID cannot bypass the
  package check during invoke. Both controller and controlled-device boundaries
  validate the grant.
- **Bilingual and accessible labels are complete** — Compatibility/direct
  headings, grant guidance, toggle labels, and the localized
  `remote.harness` tool-trace alias are available in Chinese and English.

### Remote runs, approvals, and live-status reliability

- **`202 Accepted` is correctly treated as success** — The remote adapter
  previously forced every FastAPI `JSONResponse` to `ok:false`, including a
  valid detached chat start with a `run_id`. It now treats HTTP 2xx as success
  unless the payload explicitly says `ok:false`.
- **New event-driven `runs.wait`** — A controller can wait for the next public
  run event with a cursor and bounded timeout. The target checks backlog,
  subscribes to `ChatRun.subscribers`, waits on its queue, and removes the
  subscription on exit instead of busy-polling `runs.events`.
- **Compatibility remote chats default to `auto`** — `RunRemoteCyrene` and
  remote `chats.send` now accept `auto/default/plan` and default to `auto`,
  preventing unattended compatibility runs from stalling in an approval mode
  the controller cannot complete.
- **Approval is no longer the main remote-control loop** — Ordinary work is
  approved once around the exact controller-side Harness invocation. The
  controller does not need to create a remote chat, inspect a pending question,
  and then seek permission for a second `approvals.respond` action.
- **Supervised Agent fallback remains available** — When the compatibility path
  is required, the controller can still read runs, guide, interrupt, answer
  questions, and download attachments or artifacts; Agent guidance prefers
  `runs.wait` and reserves `runs.events` for immediate incremental reads.

### Live chat-list synchronization on the controlled computer

- **New `workbench_chat_changed` SSE event** — Chat creation, run start, and
  post-settlement state publish project/chat-scoped events through the formal
  frontend event allowlist.
- **Remote chats appear immediately** — Workbench filters events to the active
  project and refreshes the list with an 80 ms debounce, eliminating manual
  page refresh after remote creation.
- **Terminal states converge** — Run `finally` publishes after durable status
  settlement, so Running/Idle state, timestamps, and previews match the backend
  after success, waiting, interruption, or error.
- **Background work never steals focus** — A new remote chat refreshes the list
  without selecting itself over the conversation the user is reading or
  editing.
- **Subscriptions clean up fully** — Page unmount removes both the SSE listener
  and any pending refresh timer.

### Automatic recovery from LAN listener-port conflicts

- **A busy default port no longer disables remote control** — When `37841`
  cannot bind, the listener searches the bounded `37841..37940` range and only
  falls back for address-in-use errors. Other socket failures remain visible.
- **The actual port persists** — A migratable `listen_port` column is added to
  `remote_settings`; runtime stores the successful port and reuses it after
  restart while retaining the `1024..65535` validation boundary.
- **Pairing displays the real address** — Settings and local pairing addresses
  use the runtime listener port. A bilingual status message identifies the
  selected fallback port instead of claiming the service is fixed at `37841`.
- **Paired devices discover a moved listener** — Delivery tries the stored
  address first and, when it belongs to the Cyrene fallback range, performs a
  bounded scan with short connect timeouts. Only an endpoint returning `202`
  with `accepted:true` is accepted as Cyrene.
- **Successful discovery repairs stored state** — The matching address is
  persisted so later requests go directly to the correct port.
- **Grant updates and responses synchronize the listener port** — Encrypted
  grant and response payloads carry the sender's live port; peers update their
  saved address only after envelope verification.
- **IPv4 and IPv6 rewriting stays safe** — Port replacement preserves ordinary
  hosts and bracketed IPv6 hosts without broadening address validation.

### Workbench remote-context interaction and privacy

- **The Add Context picker closes on outside click** — The composer anchors the
  picker with a ref and registers `pointerdown` only while open, removing it as
  soon as the picker closes or unmounts.
- **Inside selections remain usable** — `contains(event.target)` prevents the
  global listener from closing the menu before device, persona, or workspace
  toggles complete.
- **Device fingerprints leave the everyday picker** — The menu now shows the
  device name and granted-capability count only. Fingerprints remain available
  in trusted-device management and security-verification surfaces.

### Contracts, tests, documentation, and release

- **The tool-registry contract advances intentionally** — `RemoteHarness` joins
  native modules, the main-only set, resource metadata, progressive
  `remote_tools` bindings, and i18n aliases; locked registry counts and SHA-256
  contracts are updated.
- **Remote security regressions expand** — Coverage includes package-grant
  normalization, rejection of recursive `toolpack:remote_tools`, two-sided
  denial for ungranted packages, target project/workspace context, one-call
  permission binding and reset, approval only for invoke, read-only discovery,
  202 success semantics, event waiting, listener migration/fallback/discovery/
  synchronization, and real dual-gateway round trips.
- **Workbench regressions expand** — Tests cover direct-package settings,
  standard toggles and localization, fallback-port status, chat-event
  allowlisting and refresh, outside-click behavior, and fingerprint removal.
- **The architecture handoff documents direct Harness control** — Design notes
  now record the preferred invocation chain, local approval boundary, package
  grants, compatibility fallback, event waiting, and live-list semantics.
- **The local beta5 release gate passes** — The locked
  `uv sync --locked --all-extras` environment completes Python `compileall`
  and all 1,473 pytest cases with unhandled thread warnings promoted to errors.
  All 32 WebUI JSX entries rebuild with generated assets matching frontend
  sources, all 44 Electron App Use Node tests pass, and Ruff plus
  `git diff --check` are clean.
- **All active version surfaces move to beta5** — Python package metadata,
  Electron package and lock, README badges, documentation sidebar, WeChat
  channel headers, Workbench and PDF cache keys, `uv.lock`, and version tests
  now agree on `0.7.0b5` / `0.7.0-beta.5`.
- **The prerelease remains tag-driven** — `v0.7.0-beta.5` triggers the existing
  macOS DMG, Windows x64/ARM64 installer, and Linux AppImage workflow, frozen
  smoke checks, and a GitHub prerelease whose notes come from this section.

---

## [0.7.0b4] - 2026-07-27

This is the fourth `0.7.0` beta and includes every change since
`v0.7.0-beta.3`. It advances remote Cyrene control from a set of usable domain
commands into a durable end-to-end Agent workflow: remote state is isolated
from the high-write runtime database, a remote Agent can be started in one
supervised action, artifacts and chat attachments transfer in chunks without a
whole-file size ceiling, and the Workbench displays live transfer progress.
It also fixes chat interruption races, completes bilingual Memory, Schedule,
and Knowledge Base surfaces, and repairs Knowledge Base and Memory Sources
layouts across languages and enlarged UI text.

### Isolated remote-control database and upgrade migration

- **Remote state moves to a dedicated SQLite sidecar** — Pairings, peers,
  grants, replay nonces, command idempotency, and audit events now live in
  `<runtime-db>.remote-control` instead of competing with high-volume Workbench
  run events in the primary runtime database. The sidecar uses WAL, a
  30-second busy timeout, and its own connection lock.
- **Existing state migrates once and safely** — On first beta4 startup Cyrene
  detects legacy remote tables in the main database, copies only columns shared
  with the new schema, and records `split_remote_control_store_v1` in
  `remote_store_migrations`. The operation is reentrant and legacy tables stay
  in place for prerelease rollback.
- **Device identity remains stable** — Identity derivation still uses the
  original logical database path, preserving device IDs, fingerprints, and
  established trust after the storage split.
- **Default grants upgrade without broadening custom grants** — The default
  capability set now includes `approval:respond`, enabling a controller to
  answer a remote Agent's pending question. Migration adds it only when an
  existing grant exactly matches beta3's untouched default set.
- **Audit and errors identify their origin** — Remote Gateway records command
  completion and failure. Tool errors carry stable `code`, `error_origin`, and
  `retryable` fields so controller database contention, controller permission
  errors, transport failures, timeouts, and remote domain errors remain
  distinguishable.
- **Local fixtures follow the sidecar contract** — Root-level
  `.remote-control`, WAL, and SHM files are ignored, and regression fixtures
  and the checked test database are synchronized with the new storage model.

### Complete remote Agent workflow

- **New `RunRemoteCyrene` Agent tool** — In one supervised action, Cyrene
  resolves a trusted device explicitly selected by the current chat, creates a
  remote chat inside a shared project, sends a user-level instruction to start
  the remote Agent, and returns chat ID, run ID, cursor, state, and idempotency
  metadata.
- **The remote device retains its complete local harness** — Its Agent may use
  locally installed and authorized models, tools, skills, browser and computer
  use, files, and integrations. The controller gains no arbitrary HTTP, shell,
  or raw-tool bypass and cannot skip the remote sandbox, credentials,
  permissions, or approvals.
- **Remote execution modes remain bounded** — Only `default` and `plan` are
  accepted; cross-device requests cannot demand `auto` or `full_access`.
  Derived idempotency keys separately protect chat creation and run start.
- **Typed remote actions are documented precisely** — `RemoteCyreneAction`
  describes Chat, Run, Task, and Approval payloads and directs full workflows
  through chat creation, `chats.send`, `runs.events`, `runs.guide`, and
  `approvals.respond` rather than arbitrary commands.
- **Progressive tool registration is complete** — The new tool is wired into
  native modules, catalog, main-only restrictions, resource keys, and the
  `remote_control` capability package, while disclosure remains limited to
  chats with an explicitly selected remote device.
- **Architecture documentation is current** — The remote-control design now
  covers sidecar storage, all four remote Agent tools, full remote-harness
  semantics, approval loops, chunked files, permission boundaries, and current
  route and OpenAPI contracts.

### Unlimited whole-file artifact and attachment transfers

- **The old 10 MiB whole-file cap is removed** — `artifacts.read` now uses an
  offset-based protocol with 512 KiB default chunks and a 1 MiB maximum remote
  chunk. Responses include offset, chunk size, next offset, total size, EOF,
  progress, and Base64 chunk data while the complete file can be any size.
- **New `attachments.read` remote command** — It reads an attachment explicitly
  referenced by a target chat and preserves filename, media type, kind, width,
  height, and size metadata under the existing `artifact:read` capability.
- **New Control API attachment endpoint** —
  `GET /v1/control/chats/{chat_id}/attachments/{attachment_id}` returns the
  referenced file, while chat details expose a `download_url` for each valid
  attachment. OpenAPI, operation lists, schemas, and route contracts are
  updated.
- **Reads are bound to transcript references** — An attachment ID must occur in
  the target chat. Managed upload and export URLs remain confined to managed
  roots; an explicitly referenced local absolute path is transferable, but the
  endpoint cannot probe unrelated files.
- **The controller assembles streams automatically** —
  `RemoteCyreneStatus` requests consecutive chunks, verifies monotonic offsets,
  assembles a temporary file under `remote_transfers`, and registers the final
  result as a normal generated attachment. Partial and intermediate files are
  cleaned on success or failure.
- **Base64 stays out of model context** — The final tool result contains a local
  attachment descriptor, filename, and size rather than chunk or whole-file
  Base64, protecting context capacity.
- **Transfers report live progress** — The executor publishes
  `tool_call_progress` events with current and total bytes, ratio, and filename.
  Workbench trace cards render an accent progress bar and percentage while
  lifecycle merging preserves the richer resolved tool identity.

### Workbench chat reliability and context menus

- **Interruption no longer races persisted state** — `/api/chat/interrupt`
  waits for the Workbench chat record to settle from running to idle before
  responding. The frontend detaches its event stream only after the server
  accepts the interruption, preventing a resync from reading stale running
  state.
- **Interrupted chats become idle immediately** — A dedicated
  `onInterrupted` callback clears the live runtime and refreshes chat data.
  Session info uses the actual runtime as the sole Replying signal instead of
  trusting a stale `chat.status`.
- **Interrupt failures are visible** — The model checks HTTP status, runtime
  error feedback receives failures, and stream cleanup still runs safely.
- **Tool lifecycle merging preserves richer entries** — Empty nonterminal
  updates no longer overwrite a resolved tool name; progress, start, and finish
  events merge into one stable trace entry.
- **The Add Context menu stays inside narrow windows** — The entire context
  chip row is now the positioning container, the add-button anchor is static,
  and min/normal/max width constraints keep long Chinese and English labels
  visible without clipping.

### Memory, Schedule, and Knowledge Base localization

- **Memory is fully connected to Workbench i18n** — Titles, categories,
  sources, overview statistics, search, sorting, empty states, details,
  citations, relations, history, editing, deletion, and relative times now use
  shared translation keys with natural English date and relative-time output.
- **Memory Sources uses a resilient card layout** — The donut is centered above
  a full-width dot/label/percentage legend. Chinese, English, narrow sidebars,
  and enlarged UI text no longer produce percentage overlap, one-character
  English wrapping, or vertical labels.
- **Schedule dates switch with language** — Day, month, range, all-day event,
  and event-detail formatting uses Chinese or `en-US` month and weekday forms,
  and the page subscribes to language changes.
- **The real Knowledge Base route is localized** — The active Workbench route
  uses `workbench-library.jsx`; beta4 localizes that actual entry instead of
  only the unused fallback Knowledge page. Core sidebar, toolbar, filters,
  sorting, table, card, empty-state, batch, metadata, note, and tag text now
  switches language.
- **The blank Knowledge Base result area is repaired** — Header, Add menu, Sort
  menu, and batch-action JSX nesting is restored so toolbar, result table or
  card grid, workspace, and right detail panel are siblings again and existing
  items render normally.
- **Knowledge metadata labels are consistent** — Bibliography and file types,
  reading status, untitled fallbacks, author overflow, table columns,
  attachments, abstracts, notes, and tags share the `library.*` namespace.

### Settings, subagents, and compatibility

- **Internal subagent fuses leave the ordinary settings form** — Agent settings
  no longer expose execution-safety and discussion-limit implementation
  controls as everyday behavior preferences. Existing configuration remains
  compatible.
- **Cost-fuse currency is corrected** — Execution worker prompts display `¥`,
  and estimated USD cost is multiplied by 7.25 before comparison with the CNY
  ceiling, aligning presentation and enforcement.
- **All active version surfaces move to beta4** — Python package metadata,
  Electron package and lock, README badges, documentation sidebar, WeChat
  headers, WebUI and PDF asset cache keys, `uv.lock`, and version-contract tests
  now agree on `0.7.0b4` / `0.7.0-beta.4`.
- **README limitations move into dedicated documents** — The English and
  Chinese READMEs retain concise entry links, while operator and security
  boundaries, model and data requirements, API lifecycle, missing features,
  Windows source constraints, and release/manual gates now live in
  `docs/limitations.md` and `docs/limitations.zh-CN.md`.

### Tests and release gates

- **Remote storage regressions** cover legacy migration, the migration marker,
  exact default-grant upgrades, successful commands while the runtime database
  holds a write lock, audit completion, and error origins.
- **Remote Agent regressions** use two gateways to verify chat creation, Agent
  start, permission and language propagation, idempotency, and returned run
  metadata.
- **Transfer regressions** cover Control attachment downloads, chat-reference
  enforcement, referenced external files above 10 MiB, first and final chunks,
  continuous offsets, local assembly, attachment registration, Base64
  isolation, and progress events.
- **Workbench contract regressions** cover server-settled interruption, idle
  persistence, trace progress, tool identity merging, context-menu bounds,
  Memory/Schedule/Knowledge localization, Knowledge component hierarchy, and
  Memory Sources layout.
- **The local beta4 release gate passes** — The locked Python environment runs
  all 1,466 pytest cases with unhandled thread warnings promoted to errors,
  alongside 44 Electron App Use Node tests, rebuilding all 32 WebUI JSX
  entries, Python `compileall`, version consistency, and `git diff --check`.
- **The release workflow remains tag-driven** —
  `v0.7.0-beta.4` triggers the existing macOS, Windows x64/ARM64, and Linux
  PyInstaller plus Electron builds, frozen smoke tests, and a GitHub
  prerelease whose notes are extracted from this section.

---

## [0.7.0b3] - 2026-07-27

This is the third `0.7.0` beta. It completes the direct Cyrene-device control
work that followed `v0.7.0-beta.2`: Tailscale addresses are now first-class
direct-pairing targets, successful trust survives restarts, Connection settings
save automatically, and short-key copy, connection events, error feedback,
localization, and visual hierarchy have been comprehensively refined.

### Tailscale direct connections and address boundaries

- **Tailscale IPv4 addresses can pair directly** — Direct-address validation
  now explicitly accepts the `100.64.0.0/10` shared-address range, so Tailnet
  addresses in that range are no longer mistaken for public Internet
  addresses. Omitting the port still selects the Cyrene LAN listener default,
  `37841`.
- **The allowlist remains deliberately narrow** — Only Tailscale's
  `100.64.0.0/10` range was added to the existing loopback, private, and
  link-local rules. `100.63.255.255`, `100.128.0.1`, ordinary public
  addresses, URL-form inputs, invalid ports, and ports outside `1024..65535`
  remain rejected, so the pairing endpoint cannot become a general network
  request primitive.
- **Tailscale retains the complete Cyrene security protocol** — Address
  acceptance only permits the TCP/HTTP connection attempt. It does not bypass
  the one-time short key, Ed25519 device identity, X25519 key exchange,
  ChaCha20-Poly1305 E2EE, capabilities, project scopes, nonces, timestamps,
  replay protection, revocation, or auditing.
- **Old remote-version rejection is detected** — If the controller reaches
  another Cyrene over Tailscale but the remote beta2 build rejects pairing
  completion with its old local-network check, the controller returns the
  stable `remote_pairing_peer_update_required` code. Workbench now asks the
  user to update and restart the remote Cyrene and generate a new key instead
  of presenting the remote `409` as a misleading local-address error.
- **The two-sided upgrade requirement is explicit** — Pairing completion saves
  the controller's Tailnet source address on the controlled device, so both
  endpoints need beta3 or newer. If an older controlled device already claimed
  a key before rejecting completion, that key must not be reused; generate a
  fresh key after upgrading it.

### Trusted-device persistence and direct reuse

- **Successful pairing automatically adds a trusted device** — There is no
  extra Save or confirmation step. Once the bidirectional public-key proof
  completes, Device ID, display name, signing and exchange public keys,
  fingerprint, LAN/Tailscale address, directional capabilities, and project
  scopes are atomically stored in `remote_peers`.
- **Later use does not require another short key** — The short key only
  bootstraps first trust. When an Agent selects the device from Add Context,
  Remote Gateway reads the persisted peer identity, grant, and address and
  sends E2EE commands directly. A new key is only required after revocation,
  identity replacement, or intentional re-pairing.
- **Trust survives Cyrene restarts** — A new regression test reopens both the
  controller and controlled `RemoteControlStore` databases and verifies that
  the devices, saved addresses, `chat:read`/`chat:send` capabilities, and
  project scopes remain available rather than living only in process memory.
- **The real direct round trip remains covered** — The local two-instance test
  uses isolated SQLite databases, two real listeners, and bidirectional Remote
  Gateways to pair with a short key, send `chats.send`, execute on the
  controlled side, and return an encrypted response. Persistence assertions
  run after both listeners have been stopped.

### Automatic Connection settings and pairing interaction

- **The Save and Apply button is removed** — The remote-access switch saves
  immediately. Device-name edits save after a `600ms` idle debounce and flush
  pending drafts on blur, so users no longer have to infer whether a change
  has taken effect.
- **Automatic writes are serialized** — Rapid typing, immediately toggling the
  switch, or a failed preceding request cannot reorder durable settings. A
  version guard ignores stale responses so an older server response cannot
  overwrite a newer local draft; only the newest operation owns busy and error
  state.
- **Successful saves stay quiet; failures remain visible** — Routine automatic
  saves no longer create large success banners. Failures use the shared
  Feedback Service for a non-blocking error toast while retaining the backend's
  diagnostic text.
- **The short-key tile copies directly** — The key itself is an accessible
  button. Electron first uses the native clipboard exposed by Preload, normal
  browsers use the asynchronous Clipboard API, and unsupported contexts fall
  back to a hidden textarea plus `execCommand("copy")`. Both success and
  failure produce explicit toast feedback.
- **Pairing success explains future behavior** — The success message now states
  that the device was added to Trusted devices and can be selected in future
  conversations without entering another short key.

### Connection events, localization, and visual polish

- **Remote audit is renamed Connection events** — The settings page now
  presents gateway start/stop, setting updates, short-key claims, invitation
  acceptance, pairing completion, grant sync, revocation, command send/
  completion, and envelope rejection as a user-readable event stream instead
  of exposing internal snake-case names.
- **Event names and outcomes are fully bilingual** — English and Simplified
  Chinese labels cover the current 16 remote event types and 17 outcomes.
  Unknown values are still safely humanized. An absent outcome is displayed as
  Recorded rather than as a lone bullet.
- **Timestamps follow the local locale** — ISO 8601 UTC values are converted to
  the system's local date and time, with a diagnostic fallback for invalid or
  missing values. Command names and peer device IDs remain available as
  secondary metadata.
- **The outcome column is actually centered** — Green and red outcomes use a
  dedicated fixed column with horizontal and vertical Flex centering,
  wrapping, and consistent line height, avoiding clipped or drifting text.
- **Event typography is deliberately quieter** — Titles, timestamps, and
  outcomes are reduced to `12px`, `10px`, and `9.5px`; weight, line height,
  padding, gap, and column width are tightened so connection history reads as
  secondary information rather than competing with trusted-device and pairing
  actions.
- **The large pink settings notice is removed** — Invitation, copy, pairing,
  grant, revocation, and error feedback now share the standard Workbench toast.
  The sticky `remote-notice` node and styling have been deleted.
- **Frontend contracts cover the refined behavior** — Source-level regressions
  now lock debounce and blur flush, the absence of a Save button, Electron
  clipboard use, accessible labels, toasts, Event/Outcome localization, local
  time formatting, outcome centering, and removal of the legacy notice.

### Workbench chat renaming

- **Renaming no longer invokes the browser's native prompt** — The Conversation
  Rail menu opens a Workbench-native modal that follows the existing theme,
  radii, buttons, focus rings, and light/dark behavior, avoiding an
  uncontrollable system dialog that can also be blocked by Electron.
- **Validation and saving state are complete** — The dialog starts with the
  current title selected and enforces the 60-character limit. Empty,
  whitespace-only, unchanged, and currently-saving submissions are disabled;
  the value is trimmed before persistence.
- **Keyboard and accessibility behavior are explicit** — The modal supplies
  `role="dialog"`, `aria-modal="true"`, a labelled title and input, Escape and
  scrim-click dismissal, save-time close protection, and an accessible name
  for the close button.
- **Errors and success follow Workbench feedback conventions** — API errors
  remain inline with alert semantics and clear when editing resumes. Success
  uses the shared toast and closes the modal instead of losing failures behind
  a `window.prompt` call chain.
- **Backend persistence has a regression test** —
  `PATCH /api/workbench/chats/{chat_id}` stores the trimmed title, advances
  `updatedAt`, and writes the Workbench chat store. The frontend contract also
  locks out any return of `window.prompt`.

### Compatibility and verification

- **Control API and command scope are unchanged** — beta3 does not add arbitrary
  HTTP, shell, tool, or remote-desktop authority. The existing 23 Control API
  operations, 22 fixed remote commands, explicit capability/project grants,
  and Agent context selection remain the beta2 contract.
- **Existing LAN inputs remain compatible** — `127.0.0.1`, RFC 1918 IPv4,
  link-local, and existing local IPv6 behavior are unchanged. Tailscale IPv6
  unique-local addresses continue to pass through the existing private-IPv6
  rule.
- **Focused regression coverage was expanded** — Tests now cover Tailscale
  allowlist boundaries, in-range addresses, adjacent-address rejection,
  trusted-device persistence across store reopen, and the complete Connection
  settings interaction contract. The local beta3 release gate
  passed all `1,456` pytest cases, `44` Electron Node tests, rebuilding all 32
  WebUI JSX entries, Python `compileall`, version consistency, and
  `git diff --check`. Platform installers and frozen smoke tests remain the
  responsibility of the beta3-tagged GitHub Release workflow.
- **Windows ARM64 attachment is more reliable** — The post-release uploader
  for the experimental ARM64 installer now identifies the GitHub repository
  explicitly instead of depending on a checked-out working tree, so it can
  reliably attach the artifact after the primary release is created.

---

## [0.7.0b2] - 2026-07-27

The second 0.7.0 beta includes every change since `v0.7.0-beta.1`: terminal
wake-ups, completion-driven subagents, exact-scope permission review, end-to-end
remote device control, runtime, route, and Workbench consolidation, safe data
migration, release-pipeline improvements, arbitrary theme colors, and the
simplified source startup command.

### Remote Cyrene, device pairing, and control APIs

- Added an end-to-end remote-control protocol with per-device identities,
  Ed25519 signatures, X25519 key exchange, and ChaCha20-Poly1305 encryption.
  Envelopes bind sender, recipient, message ID, timestamp, and nonce; receivers
  enforce clock-skew limits, replay protection, signature verification, and
  authenticated headers. Relays route ciphertext without seeing command data.
- Added expiring short-code LAN pairing and WSS relay pairing. Invitations bind
  capabilities and project scopes, while identities, peers, grants,
  revocations, nonces, and audit events are persisted. Private material prefers
  the OS keyring and uses a protected local fallback when the keyring is not
  available.
- Remote authorization now checks direction, capability, project scope, and
  command type. Project-scoped commands fail closed without a project ID, grant
  changes synchronize continuously, revocations propagate immediately, and
  side-effecting requests require idempotency keys.
- Added commands for project discovery; chat listing, creation, reading, and
  sending; run state, cursor-addressable events, guidance, and interruption;
  task listing, creation, reading, dispatch, plan approval, step execution,
  pause, resume, and cancellation; approval responses; and bounded artifact
  listing and reads.
- Added a versioned `/api/control` surface with strict request/response schemas,
  run-event cursors, project ownership checks, task-transition validation,
  artifact path/size boundaries, and stable error responses. Remote commands
  reuse these Workbench domain boundaries instead of bypassing them.
- Added progressive remote Agent tools for listing explicitly paired devices,
  reading connection status, and invoking an action on a user-selected device.
  Catalog, package disclosure, runtime support, and metadata are integrated
  without exposing remote tools to every run by default.
- Added the standalone `cyrene-relay` console command. The WebSocket relay
  verifies registrations, routes online peers, returns delivery receipts,
  applies message-size limits, reconnects with backoff, and works across source
  installs on macOS, Windows, and Linux.
- Added a Connections settings panel for device identity, fingerprint, local
  addresses, relay state, paired peers, bidirectional grants, project scopes,
  last-seen status, and audit history. Chat composers can select one or more
  remote devices, and the selection persists per chat across reloads, forks,
  and deletes.

### Durable runs, remote recovery, and portable data

- Workbench run metadata and sequence-numbered events are now retained in
  SQLite for seven days. Reconnects can recover by run or chat ID and resume
  from a cursor; completed runs remain replayable. Startup marks unfinished
  runs as `process_restarted` and appends a durable terminal error event.
- The composer remains editable while an Agent runs. Empty input still
  interrupts; non-empty input guides the current run. The Control API and
  remote command layer use the same inbox and run-ID semantics.
- Managed attachment, Knowledge, Library, and learned-script paths can be
  relocated after backup restore or an app-data-root change. Resolution remains
  inside managed roots, so deletion and deduplication cannot escape into
  arbitrary filesystem locations.
- Backup and configuration recovery now cover unavailable keyrings, encryption
  fallback keys, old configuration migration, database and attachment restore,
  learned-script references, and cross-platform path formats while retaining
  diagnosable errors for corrupt state.
- Chat deletion now uses optimistic UI with full rollback of active selection,
  list position, fork-source metadata, and active-chat state when the request
  fails.

### Appearance, search, and settings experience

- Theme colors now support arbitrary values in addition to eight compact
  presets. The custom picker includes a saturation/value plane, vertical hue
  strip, HEX field, native color input, current/new previews, reset, cancel,
  and apply actions.
- Selection feedback now uses a white check, one accent ring, and a restrained
  halo. Swatch size and spacing prevent overlap; the transparent custom entry
  matches preset dimensions and is vertically centered. The popover, HEX field,
  and preview were compacted, and native dark focus/range outlines were removed.
- Added a contracted shared Search Overlay stylesheet and completed bilingual
  Settings copy, focus behavior, narrow-layout handling, and failed-form-state
  retention for Remote, Appearance, shortcuts, and long configuration forms.

### Agents, subagents, and long-running work

- `StartShell(wake_on_exit=true)` can now outlive the current Agent turn. Cyrene
  persists the shell/chat/project/run relationship and starts a fresh Workbench
  turn with the exit code and bounded terminal tail. Busy chats queue the wake
  until their current run finalizes.
- Subagents now have explicit execution and discussion modes. Execution workers
  are governed by success criteria and evidence instead of the main Agent's
  normal round limit; discussion agents use separate round, per-agent message,
  total message, message-length, tool, wall-time, and information-gain budgets.
- Execution workers gained renewable leases, evidence checkpoints, repeated
  no-progress detection, and wide tool-call, wall-time, cost, and context safety
  fuses. Incomplete work retains partial results with an explicit outcome.
- Duplicate active Agent IDs are rejected, discussion budgets are isolated by
  discussion ID, cancellation/timeout/incomplete states are distinct, lifetime
  metrics survive reactivation, and the parent monitor has a bounded deadline.
- `send_message` is now a stable direct tool independent of the Delivery
  package. Tool lifecycle events always pair started calls with completed,
  failed, or cancelled terminal events, including progressive gateway calls.
- Added `memory.list` for a complete, countable cross-session and project memory
  inventory while preserving recall/search/save/retire boundaries.

### Permissions, safety, and auditability

- Automatic approval no longer grants full access to the rest of a run. Each
  approval creates a one-shot fingerprint bound to the exact tool, operation,
  permission kind, canonical path, command or external arguments, and reason.
- Shell and external MCP execution now elevate according to actual scope.
  Default mode keeps simple workspace-local commands smooth, while external
  working directories/paths, command substitution, opaque interpreters,
  network executables, auto-mode process execution, and MCP calls require exact
  review. Unknown MCP tools fail closed.
- A new `permission_decisions` table records session, round, source, tool,
  operation, path, result, rationale, and fingerprint. Workbench renders the
  scoped approval or denial; string values such as `"false"` cannot be treated
  as approval.
- Permission mode is normalized and persisted across retry, fork, replay, and
  recovery. Invalid modes fail closed. Forks retain attachments and state
  boundaries, and failed retries preserve the old public reply until a
  replacement succeeds.

### Performance, scheduling, and Workbench workflows

- Scheduled-task polling, proactive heartbeat, behavior learning, SOUL
  stewardship, and short-term cleanup now have independent coalesced cadences.
  Heavy maintenance is no longer coupled to every due-task poll, and the
  steward default/minimum interval is one hour.
- Behavior learning coalesces completed turns into one quiet-period job when no
  scheduler exists and avoids a second per-turn job in normal server runtimes.
  Single-tool, low-information turns skip the learning LLM.
- Usage and latency writes share database batches. Workspace finalization
  reuses unchanged snapshots based on mtime/ctime/size and marks change sets as
  exclusive or overlapping with the relevant run IDs.
- Electron gained a dedicated trusted browser-input module with React-compatible
  native setters, keyboard/input events, per-session tabs, shared login state,
  stale closed-tab protection, user-event learning telemetry, and background
  renderer throttling.
- Workbench now completes tool activities in place, shows finalization before
  workspace persistence, reconnects without resubmitting messages, truncates
  retries only after durable terminal events, and keeps LLM, plan, inbox,
  browser, and tool traces independent and localized.
- Additional Workbench hardening covers last-message retry, manual context
  compaction, project/user workspace selection, long paths, customizable
  shortcuts, clipboard/pasted files, drag-and-drop, narrow rails, native Linux
  framing and directory selection, durable settings forms, and actionable
  Knowledge, Memory, and learned-skill views.
- Literature Library regressions now cover project isolation, CRUD/statistics,
  trash and permanent batch deletion, file-type filtering, existing Knowledge
  bridging, source abstracts, idempotent Zotero sync, inline media, unique read
  events, and selection cleanup after filtering.

### Architecture, compatibility, and data

- All FastAPI adapters are centralized under `src/route/`, with registry-based
  assembly for Agent, Workbench, Settings, Tasks, Knowledge, Memory, Learning,
  Maps, Channels, System, and Code. Domain services no longer depend on WebUI.
- Canonical source ownership is organized under `agent/`, `workbench/`,
  `model_runtime/`, `learning/`, `runtime/`, `observability/`, `knowledge/`,
  `channels/`, `tooling/`, and `tool_impl/`. Stable public facades remain for
  `call_llm`, browser, subagent, memory, and tools.
- Historical imports resolve lazily to the same canonical module objects,
  preserving monkeypatch behavior, metadata, and executable aliases. Frozen
  smoke tests verify every legacy alias.
- The legacy `store/cyrene.db` migrates only when the new
  `store/cyrene.runtime.database` is empty. Migration uses SQLite backup
  semantics including WAL state, runs `quick_check`, writes an idempotent
  marker, switches atomically, and retains the source for rollback.
- Web, CLI, Electron, PyInstaller, and daemon modes now share runtime context,
  bootstrap, service, scheduler, update, and shielded shutdown ownership.
  Electron still launches the physical `local_cli.py`, which selects the
  checkout virtual environment.
- The canonical source command is now `uv run python -m cyrene`. The no-argument
  module entry starts the sole Workbench UI, `--workbench` remains compatible,
  Telegram requires `--telegram`, and `cyrene start` uses the same default.

### Single Workbench, build, and documentation

- WebUI now has one source tree under `src/webui/frontend`, organized into
  entry, platform, shared, and Workbench features, with one committed build
  output under `src/webui/static/app`. The classic UI, duplicate
  `workbench-webui`, legacy selector, redundant assets, and dead preload API
  were removed.
- Bootstrap readiness, navigation, SSE events, API/data storage, theme, i18n,
  Markdown/math/highlighting, diff, PDF, search, feedback, and browser view are
  shared infrastructure. Electron always loads this Workbench from the dynamic
  Python backend port.
- Regular CI now compiles Python and runs the complete locked all-extras pytest
  suite, rebuilds and checks committed WebUI output, and runs Electron App Use
  tests. Platform packaging and frozen smoke tests remain release gates.
- The release workflow now caches Python and npm dependencies from lockfiles,
  uses `npm ci --prefer-offline` for Electron and WebUI, and disables redundant
  artifact compression. macOS, Windows x64, and Linux publish the prerelease
  immediately; experimental Windows ARM64 runs outside the critical path and
  attaches its installer later with a clobber-safe release upload.
- Windows ARM64 now persists static OpenSSL packages through vcpkg's files
  binary provider and retains pip's locally built cryptography wheel. Cache
  keys include platform, architecture, lockfiles, and workflow inputs while
  preserving the frozen application's static-OpenSSL requirement.
- The strict 259-operation OpenAPI baseline was recaptured after reviewing ten
  generator-level schema deltas and now pins FastAPI 0.136.1 and Pydantic
  2.13.4; no schema fields are ignored.
- English and Chinese README, installation, usage, configuration, architecture,
  development, browser, project-note, handoff, roadmap, and design-QA material
  now match the single Workbench, package ownership, database name, managed
  processes, Literature/Zotero scope, WeChat QR setup, budget/backup/keyring
  boundaries, and Windows SimpleXNG limitation. Obsolete local QA screenshots
  were removed.
- The local beta2 release baseline passed all 1,449 pytest tests in the locked
  Python 3.12 environment, 49 Electron App Use/browser-input Node tests, the
  complete 32-entry WebUI rebuild, Python compilation, version consistency, and
  `git diff --check`. Platform packages and frozen smoke tests remain owned by
  the tag-triggered release workflow.

---

## [0.7.0b1] - 2026-07-23

The first 0.7.0 beta combined the post-0.6.17 Literature Library, proactive
Agent behavior changes, progressive tool-package disclosure, and cache/terminal
reply hardening.

- Added a project-isolated Literature Library with collections, tags,
  table/card views, search/filtering, a resizable detail workspace, metadata,
  notes, attachments, relations, reading status, themes, and responsive states.
- Idempotently mapped existing `kb_documents` into each project's Library
  without copying files or crossing project boundaries.
- Added file/PDF and CSL JSON/RIS/BibTeX import, Zotero Local API incremental
  sync, citation rendering, BibTeX export, and attachment-aware reading.
- Added Agent-facing structured Library listing, hybrid evidence search, and
  bounded updates of verified metadata.
- Replaced the full model-facing schema list with stable direct tools plus up
  to 12 package gateways using `discover → describe → invoke`.
- Kept Phase 1 and Phase 2 wire arrays byte-stable while enforcing phase policy
  at runtime; package switches now control schemas, prompts, and permissions.
- Showed actually used tool packages in Context rather than every enabled
  package.
- Removed an unnecessary full-history wrap-up when Phase 2 already produced a
  valid final answer, and hardened every final/stream/persistence path against
  DSML leakage.
- Made `npm run dev` launch Electron directly through `local_cli.py`.
- Constrained proactive work to one evidence-backed incremental task without
  modifying or deleting existing files.

Validation included 1,227 pytest tests, Ruff, Python compilation, Workbench and
PDF.js builds, 44 Node App Use tests, Electron syntax checks, Python
wheel/sdist, the macOS Electron package, frozen smoke tests, and version/lock
consistency.

---

## [0.6.17] - 2026-07-23

- Added compact navigation for long conversations and accurate summaries for
  attachment-only messages.
- Added a return-to-latest control when reading older messages.
- Made browser PiP avoid only vertically intersecting messages, improved
  drag/resize/reload stability, and deferred avoidance reflow during scrolling.
- Hardened guarded navigation credentials and takeover of links that open new
  tabs; improved interactive-element snapshots and removed empty live cards.

## [0.6.16-fix] - 2026-07-22

- Prevented browser PiP from covering the messages being read and stabilized
  layout during movement, streaming, and resize.
- Required short-lived snapshot-bound credentials for otherwise unreachable
  direct navigation, blocked duplicate navigation, and automatically adopted
  newly opened tabs.
- Improved snapshot relevance and avoided blank in-progress message bubbles.

## [0.6.16] - 2026-07-22

- Added per-run workspace change sets and reviewable diffs without requiring
  Git.
- Made PDF analysis select relevant pages across a document and moved PDF.js to
  its compatible legacy build.
- Added a launch screen that waits for first-screen readiness.
- Required Agents to prefer visible page links, improved fullscreen video and
  browser guidance, unified upload icons, and hid the disconnected sign-out UI.

## [0.6.15] - 2026-07-22

- Added an Agent composer and visible progress to maximized browser mode.
- Improved native/fallback browser transitions and Context polling efficiency.
- Stabilized context thresholds and strengthened visible-page-first navigation.

## [0.6.14] - 2026-07-18

- Made `browser_scroll` target real nested scroll regions using trusted wheel
  input, optional element refs or coordinates, and measured deltas.
- Added accurate root-versus-container user scroll telemetry and matching
  Electron/Playwright behavior.

## [0.6.13] - 2026-07-18

- Added draggable/resizable in-conversation browser PiP and platform-correct
  fullscreen video.
- Added guarded browser file upload, verified backup/restore with rollback,
  faster global search, safer App Use scrolling, and persistent `quit` replies.

## [0.6.12] - 2026-07-17

- Removed bundled Playwright/Chromium by default and unified browser tooling on
  Electron's `WebContentsView`.
- Added the live Context inbox, durable deduplicated mid-run guidance, and a
  compact Git-style branch tree.

## [0.6.11] - 2026-07-16

- Added per-session model affinity and failure cooldown.
- Returned clickable references from browser navigation.
- Gated App Use capabilities by platform and fixed live/persisted timeline
  ordering.

## [0.6.10] - 2026-07-15

- Simplified learned skills around explicit purpose and script synthesis.
- Calculated cost from the actual response model and cache usage.
- Kept Workbench surfaces mounted to remove page-switch flashes and improved
  reasoning activity timelines and context-window fallback behavior.

## [0.6.9] - 2026-07-14

- Made App Use coordinate operations auditable with screenshots and measured
  points.
- Made project activation an atomic lightweight update and added guarded
  project/chat caches.

## [0.6.8] - 2026-07-14

- Required fresh, bounds-checked App Use coordinates and explicit outcome
  confidence.
- Automated repeated-workflow skill learning and added bounded tool timeouts,
  non-blocking file I/O, and safer Workbench persistence retries.

## [0.6.7] - 2026-07-13

- Added the complete desktop pointer action set, atomic key sequences, and text
  selection.
- Fixed packaged App Use provider paths and added pasted files/images plus
  optimistic messages and guidance.

## [0.6.6] - 2026-07-13

- Shipped macOS and Windows desktop-app automation through a unified App Use
  gateway.
- Reduced latency by waking the Agent before asynchronous telemetry/database
  persistence and moving chat reads off the event loop.

## [0.6.5] - 2026-07-13

- Added durable mid-run Agent guidance, visible model failover, resource-keyed
  tool scheduling, and direct visual analysis of image attachments.

## [0.6.4] - 2026-07-11

- Added managed background-task shutdown and an embedded PDF viewer.
- Reworked project-isolated behavior learning and automatically completed goals
  whose acceptance criteria passed.

## [0.6.3] - 2026-07-07

- Added learned skills, auditable browser behavior capture, Chinese/English UI,
  paginated Knowledge, and adaptive context budgeting.

## [0.6.2] - 2026-07-05

- Added budget/economy controls, a localized native macOS menu, explicit
  send-file confirmation, mixed memory injection, and summary project queries.

## [0.6.1] - 2026-07-01

- Completed browser click/input/wait/network/tab tooling and added the persistent
  Electron Quick Chat tray workflow.

## [0.6.0] - 2026-06-29

- Introduced the project-centric Workbench, global Quick Chat, three-layer
  memory, live browser takeover, prompt-cache optimization, and Windows ARM64
  packages.
- Added transactional SQLite storage, workspace path safety, editable/forkable
  chats, artifact download validation, context compaction, honest task planning,
  durable runs, and independent subagent status.

## [0.5.0] - 2026-06-07

- Added live browser takeover, deep reflection, desktop authentication backed by
  the OS keyring, SSRF protection, PDF viewing, permission snapshots, and
  confirmation for high-risk tools.

## [0.4.7] - 2026-05-24

- Added directory/archive skill installation, portable update paths, compact
  learned arguments, executable-path handoff, and the flat-surface UI refresh.

## [0.4.2] - 2026-05-24

- Fixed terminal true color and control-sequence handling, cached Shell previews
  across conversations, and made terminal layout responsive.

## [0.4.1] - 2026-05-23

- Added section-by-section long Deep Research reports with a requested length
  handshake and CJK-capable PDF export.
