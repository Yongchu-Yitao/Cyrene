# Changelog

[中文](CHANGELOG.md) · [English](CHANGELOG.en.md)

This English edition preserves the release history of the Chinese changelog.
The Chinese edition remains the most detailed record for older releases.

## [0.7.0b11] - 2026-08-03

This is the eleventh `0.7.0` beta and includes every change after
`v0.7.0-beta.10`. It adds project-scoped Workbench chat groups and safe
cross-session context, completes session/fork metadata handling, tightens the
embedded-browser and right-panel interactions, and fixes knowledge sync,
permission handling, model settings, welcome-page detection, and localized
error reporting.

### Release highlights

- **Project-scoped chat groups** — chats can be overlapped to create a group,
  then moved, removed, renamed, dissolved, or added to an existing group. The
  backend is authoritative and handles revisions, concurrent windows, and
  failed membership-event delivery.
- **Safe cross-session collaboration** — membership changes become hidden,
  append-only session events. The main Agent can explicitly read relevant peer
  work through a capability-checked memory tool, with peer text treated as
  untrusted evidence rather than instructions.
- **Richer session metadata** — fork/parent/source metadata, read-only legacy
  state, runtime/model/context details, recent session tabs, and stale branch
  cleanup now remain consistent across list, read, delete, and Workbench paths.
- **Workbench layout consolidation** — the top bar, chat rail, side card,
  composer, responsive lane, and glass surfaces now use one coherent layout;
  the right panel is a top-aligned floating accordion that can be resized from
  its card edge and restored after being hidden.
- **More reliable embedded browsers** — floating browsers use a calibrated
  desktop CSS viewport with CSS-to-DIP coordinate conversion, while docked
  browsers stay unzoomed and expose resize feedback without moving the native
  page.
- **Knowledge and Zotero sync fixes** — paginated imports, version tracking,
  child-item completion, deletion tombstones, and bibliographic abstract
  reconciliation prevent incomplete imports and indexing previews posing as
  paper abstracts.
- **More predictable Agent/runtime behavior** — Phase 1 now hands a bounded
  internal execution brief to Phase 2, session-scoped permission grants are
  distinct from one-shot and permanent grants, proactive control sentinels are
  filtered, and settings/errors refresh and localize consistently.

### Detailed changes and compatibility notes

#### Workbench project chat groups

- Added an authoritative project-scoped `chat_groups` store. It supports the
  Workbench database document and legacy JSON fallback/migration, while the
  public projection exposes only IDs, title, summary, language, members, and
  timestamps.
- Groups require at least two valid chats. Invalid IDs, duplicate members,
  duplicate group IDs, and chats claimed by multiple groups are normalized
  away; removing a member that leaves a singleton dissolves the group.
- The browser keeps an optimistic projection, but writes carry project and
  membership revisions. The server rebases explicit move, remove-member,
  rename, dissolve, and metadata intents against the newest state, preserving
  unrelated changes from another window and preventing stale AI metadata from
  replacing a newer roster.
- Group titles and summaries can be generated from member titles/previews in
  the requested language. A manually renamed title is locked while summaries
  may continue to refresh; generated values are bounded and written only when
  the member signature is still current.
- A committed outbox records membership changes before hidden
  `[Chat group context event]` messages are appended to added, retained, and
  revoked sessions. Events carry project/group/session IDs, member paths,
  workspace path, membership revision, and active/revoked access. Retries are
  idempotent and repair a failed append on a later write.
- Events do not rewrite the stable system prompt or cached history prefix and
  are preserved exactly during compaction. Chat deletion, group dissolution,
  and project checks revoke old membership access.
- Added unified Workbench routes for group read/write, move/remove, rename,
  dissolve, and AI metadata generation, with explicit conflict, membership,
  and generation errors.

#### Cross-session context and session metadata

- Added `memory.group_sessions.read` / `ReadChatGroupSessions`. It is available
  only to the main Agent, is absent from subagent and automatic wire schemas,
  and rechecks membership at invocation time instead of trusting historical
  events or raw paths.
- Peer snapshots contain completed message prefixes, final conclusions,
  attachments, timestamps, session IDs, logical state paths, workspace paths,
  and a running marker. Incomplete trailing requests are excluded so an active
  session is not presented as finished evidence.
- Results explicitly mark peer conversation as untrusted evidence; the group
  summary is orientation only, and peer user/assistant text is never an
  instruction. The prompt requires preserving provenance and surfacing
  conflicts rather than silently selecting a source.
- Added a safe arbitrary-session append boundary using the target session's
  lock, epoch, and stable message ID. Runtime/debug update events now target
  the correct session even when another session is active.
- Phase 1 now performs a bounded planning pass and hands the exact user request
  plus an `execution_brief` to Phase 2. The brief is a provisional internal
  handoff, not a user instruction, and must be revised when tool evidence
  contradicts it.
- Fork/parent/source metadata is kept consistent across list, read, and delete
  paths; orphaned metadata is removed when its source disappears. Legacy
  sessions that cannot safely continue are explicitly read-only.
- Session lists, recent top-bar tabs, and chat cards retain model, permission,
  token/context, task, and branch details, with copy-title, pin/remove,
  browser-resource, and file-resource actions.

#### Workbench UI, drag-and-drop, and responsive layout

- Chat dragging now supports ordinary reordering, overlap-to-group, and drop
  into the conversation area to open a target. Keyboard ordering, focus
  retention, live-region announcements, and visible feedback cover the same
  paths.
- Group cards show title, summary, member count, and expanded/collapsed state;
  they support generation status, rename, remove-member, dissolve, add-to-group,
  bilingual labels, accessibility attributes, and operation errors.
- The chat side panel is now a top-aligned floating accordion. Overview and
  Context remain stable entries; Plan, Subagents, Artifacts, Changes, Branches,
  Viewer, Map, Browser, and Side Agents appear only when relevant. Each panel
  has its own SVG icon, collapsible body, and responsive card layout.
- The right card resizes from its own edge instead of using a full-height guide.
  Hiding the side panel smoothly widens and centers the conversation lane, and
  the top bar exposes a restore action. Reduced-motion users still receive
  immediate usable state changes.
- Top-bar and rail glass masks, feathering, z-index, and spacing are unified;
  session titles support hover marquee, and resource pin/new-chat/restore
  actions align across narrow layouts.
- The composer uses a bottom glass dock while keeping the input card's clear
  background, radius, and focus treatment. Scroll-to-bottom, overlays, viewers,
  and the hidden side panel no longer cover one another.
- WebUI API errors now map through localized stable metadata. Workbench create,
  Quick Chat, browser takeover, settings, search, Codex quota, and remote error
  paths no longer expose raw exception strings as their only user message.
- Welcome-page detection waits for authoritative backend content instead of
  relying only on origin-scoped localStorage, avoiding false onboarding after a
  desktop fallback-port change.
- Bare Markdown URLs stop at CJK/full-width punctuation, preventing text such
  as `www.example.com），后文` from becoming one malformed link.

#### Electron and embedded browser behavior

- Floating Agent browsers calibrate a desktop-width CSS viewport against
  Electron zoom quantization and `innerWidth`, then convert CSS coordinates to
  device-independent pixels for accurate click, scroll, and takeover behavior.
- Right-docked browsers remain unzoomed and no longer move when the pointer
  approaches their left edge. A 2px native-page resize hint and renderer event
  provide the visual cursor state without a permanent gutter.
- Inspect/text-links scripts clear stale `data-cyrene-ref` values, and
  `visibleLinkMatches` assigns unique references to visible links so
  `click_ref` cannot select stale or duplicate targets.
- The preload/native bridge carries resize-hint and viewport state consistently;
  the browser component exports a shared icon and routes takeover failures
  through the new i18n error API.

#### Remote control, permissions, and runtime reliability

- Remote Settings now polls for newly paired devices in the background and
  incrementally upserts peers without re-entering loading state or clearing the
  current view.
- Encrypted paired-mobile `runs.events` now carries the Workbench phase,
  reasoning, tool-call, and subagent lifecycle. The loopback Control API keeps
  filtering model reasoning at its public boundary and returns only public
  execution output.
- Codex settings keep a saved model and reasoning effort visible while the
  catalog loads, accept both snake_case and camelCase effort fields, and use
  the latest persisted candidate after refresh or source switching.
- Permission answers distinguish one-shot, session, run, and permanent grants,
  while retaining compatibility with already-open legacy prompts. Proactive
  rounds filter the internal `awaiting_user` sentinel before it reaches the
  transcript or notifications.
- Remote and Workbench errors carry stable codes, i18n keys, and fallback text
  so desktop and mobile clients can localize them without losing diagnostics.
- Learning, CLI, scheduler, subagent, and runtime-wire boundaries received
  matching tests and safeguards; system-initiated elevation no longer creates
  a pending user question, and proactive completion cannot publish an empty
  public message.

#### Knowledge, Zotero, and repository maintenance

- The Literature Library knowledge bridge serializes first synchronization with
  `BEGIN IMMEDIATE`, avoids duplicate bridge rows, and repairs linked records
  immediately as knowledge documents change.
- Only explicit `abstract`/`abstractNote` source metadata is treated as a
  bibliographic abstract. Older records that copied an indexing preview are
  cleared, while genuine user/Agent edits are preserved.
- The Zotero Local API client enforces loopback URLs, paginates collections,
  items, and deletions, tracks `Last-Modified-Version`, completes collection
  imports with parent attachments/notes/annotations, and de-duplicates records.
- Incremental sync now handles provider/library/item keys, collection
  membership, child updates, and deletion tombstones, including cleanup of
  Cyrene-managed Zotero files and index relations.
- Removed accidentally tracked `test.db` and browser QA screenshots, ignored
  the runtime database, archived one-off design QA records under
  `project-notes/`, and refreshed the progress and architecture handoff docs.

#### Tests and release checks

- Added coverage for group metadata, membership outbox repair, stale-client
  rebasing, peer authorization, compaction preservation, and wire capability
  isolation.
- Added Electron browser-edge, narrow viewport, Remote Settings refresh,
  Codex selection persistence, permission/proactive execution, session-tab,
  context-menu, glass/side-panel, drag-group, and localized-error contract
  tests.
- All active version surfaces now use Python/UV `0.7.0b11`, Electron
  `0.7.0-beta.11`, including README badges, WebUI cache keys, documentation,
  the WeChat client, and version-contract assertions. The Git release tag is
  `v0.7.0-beta.11`.

## [0.7.0b10] - 2026-07-31

This is the tenth `0.7.0` beta and includes every change since
`v0.7.0-beta.8`. On top of the beta9 remote-control and Workbench work, this
release fixes three Windows release defects: the legacy database migration
crashed startup when its staging file was transiently locked; `openai_codex`
and `codex_cli_bin` were missing from the Windows packages, breaking model
settings and message sending with `ModuleNotFoundError`; and the
uvloop→winloop compatibility patch for simplexng had never actually applied.
CI now has multiple guard rails so a package with missing modules can no
longer be published silently.

### Feature highlights

- **Remote control covers the full workflow** — trusted controllers can
  inspect and update non-secret settings, manage models and skills, operate
  project-scoped shells, read workspace changes, and retrieve richer chat,
  context, attachment, map, and runtime state.
- **Mobile direct requests need no callback server** — a new end-to-end
  encrypted request/response transport returns the result on the original
  connection while retaining reverse delivery for existing clients.
- **Model sources are stored independently** — custom OpenAI-compatible
  candidates and the Codex OAuth candidate no longer overwrite each other;
  switching sources preserves the inactive configuration.
- **Remote shells are persistent and incrementally readable** — shells are
  bound to a shared project and paired device and support open, cursor-based
  read, write, interrupt, and close. Interrupting a command keeps the shell
  available.
- **Images render directly in conversations** — agent images use compact,
  rounded previews rather than generic file rows. Click opens the right-side
  viewer, the card remains draggable, and the footer keeps the filename plus
  Open Externally and Download.
- **Chats can be reordered** — drag-and-drop and keyboard ordering persist per
  project, and a chat can be dropped into the conversation area to open it,
  with visible drop feedback and screen-reader announcements.
- **The Workbench header is one continuous glass surface** — chat rail and
  transcript headers share one overlay with no duplicate masks or dividers;
  long-conversation navigation now originates from the right.
- **Timezone is an authoritative persisted setting** — onboarding, General
  Settings, page startup, and runtime configuration share one backend value,
  and a failed save restores the local state.

### Detailed changes and compatibility notes

#### Fix: Windows startup crash after upgrade (locked database migration staging file)

- **Symptom** — the app crashed on startup with
  `PermissionError: [WinError 32] ... being used by another process` pointing
  at `…\AppData\Roaming\cyrene\store\.cyrene.runtime.database.migration-*.tmp`.
- **Root cause** — first startup runs a one-time migration from the legacy
  `cyrene.db` to `cyrene.runtime.database`: the full SQLite snapshot
  (including committed WAL data) is written to a random-suffix staging file,
  atomically swapped over the target, and then deleted in a `finally` block.
  On Windows the file can be transiently locked (a lingering previous process
  or antivirus scanning the freshly written large file), so the `finally`
  deletion raised `PermissionError`. An exception raised in `finally` overrides
  the migration result and propagates straight into the startup path, crashing
  the app — and every subsequent launch retried and crashed again.
- **Fix** —
  - Staging cleanup is now tolerant: it retries 5 times (200ms apart) and, if
    still locked, only logs a warning instead of raising. A leftover staging
    file with a random suffix cannot break later startups.
  - Swapping the staging file over the target retries transient locks;
    persistent locks (e.g. an old instance still running) fall through to the
    normal migration-failure branch with a structured result instead of a
    crash.
  - Each migration first cleans up `migration-*.tmp` leftovers from a
    previously interrupted run (locked files are naturally skipped).

#### Fix: openai_codex missing from Windows packages (model settings 500 / ModuleNotFoundError)

- **Symptom** — the app starts, but the model settings page returns an
  internal server error and sending a message reports
  `no module named 'openai_codex'`.
- **Root cause** (five release-guard failures at once) —
  1. simplexng declares an unconditional `uvloop` dependency, which has no
     Windows wheel and whose source build rejects Windows outright; CI's
     `pip install .` therefore failed on the Windows builder and
     `openai-codex` / `openai-codex-cli-bin` were never installed.
  2. GitHub Actions PowerShell steps do not abort on a non-zero exit code of
     an external command, so the step still reported success.
  3. PyInstaller's `collect_all("openai_codex")` could not find the package,
     printed only a "not a package" warning, and continued; neither package
     entered the bundle.
  4. The build-environment import check did not include `openai_codex`.
  5. The packaged smoke test had actually failed with
     `No module named 'codex_cli_bin'`, but the PyInstaller bootloader
     swallowed the unhandled exception's exit code, so CI stayed green.
  - The incomplete package was released; macOS/Linux were unaffected (uvloop
    has wheels there).
- **Fix** —
  - Both Windows build jobs now run `pip install . --no-deps` plus an
    explicit `pip install openai-codex==0.144.4` (`openai-codex-cli-bin`
    resolves automatically; Windows wheels exist).
  - The smoke test now exits `SystemExit(1)` and writes a crash log on
    failure, so the bootloader can no longer swallow the exit code.
  - The build-environment import check on all three platforms now includes
    `openai_codex` and `codex_cli_bin`.
  - Both Windows jobs verify `_internal\openai_codex` and
    `_internal\codex_cli_bin` in the packaged output.
  - The PyInstaller spec aborts the build when the critical packages
    `openai_codex` / `codex_cli_bin` were not collected.

#### Fix: simplexng cross-platform packaging and Windows runtime

- **searx vendored submodule collection warning** — PyInstaller's
  `collect_submodules("simplexng._vendor.searx")` cannot import searx directly
  (searx uses top-level absolute imports and relies on simplexng injecting
  `_vendor` into `sys.path` first). The main analysis imports simplexng first,
  so the injection works: all 306 searx source files and 15 data files
  (including the fasttext language model `lid.176.ftz`) are confirmed inside
  the bundle and the runtime import chain was verified.
- **The Windows uvloop patch had never applied** — CI replaced
  `import uvloop\nuvloop.install()`, but the actual searx source has
  `from searx import logger` between the two lines, so the replacement was a
  silent no-op and `searx.network.client` crashed on import on Windows.
  The patch now matches the single import line and aborts the build if the
  replacement did not take effect.
- **fasttext-predict package name corrected** — the distribution installs
  the module as `fasttext` (with the `fasttext_pybind` C extension); the
  spec's incorrect `fasttext_predict` name was fixed, removing the bogus
  "Hidden import not found" errors on all three platforms.
- **Smoke test now verifies the searx runtime** — the packaged app imports
  `searx.network.client` for real, so a missing Windows patch or data files
  fails CI instead of shipping.

#### Testing and release

- **Full local build verification** — after the fixes, a complete PyInstaller
  build was run on macOS; the smoke test covers the Codex runtime and the
  searx import chain, and packaged modules and data files were checked
  individually.
- **Version numbers fully synchronized** — Python package, UV lock, Electron
  manifest/lock, README badges, web docs, WeChat client, WebUI cache keys,
  and the related contract tests all updated to Python `0.7.0b10` / Electron
  and Git tag `0.7.0-beta.10`.

---

## [0.7.0b9] - 2026-07-31

This is the ninth `0.7.0` beta and includes every change since
`v0.7.0-beta.8`. It substantially expands remote and mobile control so a
paired device can manage chats, settings, models, attachments, workspace
changes, and project shells. It also separates custom and Codex model
configuration, makes timezone persistence authoritative, and refines the
Workbench with reorderable chats, one continuous glass header, and compact
inline image cards that retain viewing, dragging, opening, and downloading.

### Feature highlights

- **Remote control now covers the full workflow** — trusted controllers can
  inspect and update non-secret settings, manage models and skills, operate
  project-scoped shells, read workspace changes, and retrieve richer chat,
  context, attachment, map, and runtime state.
- **Mobile direct requests need no callback server** — a new end-to-end
  encrypted request/response transport returns the result on the original
  connection while retaining reverse delivery for existing clients.
- **Model sources are stored independently** — custom OpenAI-compatible
  candidates and the Codex OAuth candidate no longer overwrite each other.
  Switching sources preserves the inactive configuration.
- **Remote shells are persistent and incrementally readable** — shells are
  bound to a shared project and paired device and support open, cursor-based
  read, write, interrupt, and close. Interrupting a command keeps the shell
  available for subsequent commands.
- **Images render directly in conversations** — agent images use compact,
  rounded previews rather than generic file rows. Click opens the right-side
  viewer, the full card remains draggable, and the footer contains the
  filename plus exactly Open Externally and Download.
- **Chats can be reordered** — drag-and-drop and keyboard ordering persist per
  project. A chat can also be dropped into the conversation area to open it,
  with visible drop feedback and screen-reader announcements.
- **The Workbench header is one continuous glass surface** — chat rail and
  transcript headers share one overlay with tighter dimensions and no
  duplicate masks or divider. Long-conversation navigation now originates
  from the right.
- **Timezone is an authoritative persisted setting** — onboarding, General
  Settings, frontend bootstrap, and runtime configuration share the backend
  value, with local rollback if saving fails.

### Detailed changes and compatibility notes

#### Remote and mobile control protocol

- Added `settings.read` and `settings.update` with stable groups for Agent,
  Context, Execution, Discussion, Channels, Updates, Budget, Models, Skills,
  and tool packages. Responses include bilingual labels, descriptions, types,
  ranges, and enum metadata for native mobile settings UIs.
- API keys are never returned. Model candidates expose only
  `api_key_configured`, and an omitted key preserves the stored credential
  when another field is edited remotely.
- Server-side validation covers booleans, numeric ranges, enums, reasoning
  effort, model counts, base URLs, tool package IDs, and skill IDs. Invalid
  writes fail without partially persisting state.
- Remote model management now handles custom primary candidates, the Codex
  OAuth candidate, vision candidates, the secondary model, and active source.
  Codex remains primary-only, and custom endpoints receive safe URL checks.
- Added a shared `set_skill_enabled` registry path so local and remote skill
  toggles use one persistent state.
- Added `shell.open`, `shell.read`, `shell.write`, `shell.interrupt`, and
  `shell.close`, gated by `toolpack:code_tools`, restricted to shared projects,
  and isolated by pairing-device ownership.
- Every shell output and prompt record has a monotonic `seq`; snapshots return
  `nextCursor` so clients can fetch only new terminal data.
- Added `changes.read` for project workspace changes.
- Enriched chat lists and `chats.read` with parent/fork metadata, model,
  permission mode, token usage, context metrics and blocks, inbox snapshots,
  used tool packages, workspace changes, and map pins/routes.
- Added `chats.update` for renaming a chat inside its shared project and
  `chats.delete` through the canonical Workbench deletion path, with matching
  capability declarations, remote tool schema, auditing, and side-effect flags.
- Remote chat creation and sending accept up to five Base64 attachments with
  an 8 MB aggregate limit. They enter the regular chat-upload lifecycle, and
  partial files are removed if a request fails.
- Attachment reads support original and bounded thumbnail variants. Generated
  thumbnails remain inside the managed data directory, with explicit errors
  for unsupported media, missing originals, or failed generation.
- Model, usage, and context information can be recovered from messages and run
  records when older chat metadata is incomplete.
- Settings capabilities are device-scoped rather than project-scoped. Shell
  and change operations remain project-scoped, and mutating shell operations
  are explicitly marked as side effects.

#### Direct transport, pairing, and security

- Added `/v1/control/request`, which accepts an encrypted envelope, dispatches
  through the Remote Gateway, and returns the encrypted response in the same
  HTTP request. A mobile controller no longer needs an inbound listener.
- Pairing metadata distinguishes `request_response` and legacy
  `reverse_delivery`, preserving compatibility with already paired clients.
- Inline requests retain device trust, capability, project-scope, nonce replay,
  envelope-size, and audit checks; removing the callback hop does not weaken
  authorization boundaries.
- DirectPairingServer now has an explicit inline request receiver lifecycle
  connected to RemoteGateway, including deterministic startup, shutdown, and
  error response behavior.
- Envelope validation accommodates richer attachment, thumbnail, context, and
  settings payloads while rejecting oversized input before decryption/parsing.

#### Persistent shells and runtime isolation

- Shell startup accepts an explicit `workspace_root`; relative paths resolve
  only beneath that root, and traversal or cross-project reuse is rejected.
- Interactive and non-interactive launches resolve a supported platform shell.
  Inherited `npm_config_prefix` is removed to prevent misleading nvm warnings.
- Interrupt sends SIGINT to the process group on Unix and CTRL_BREAK on
  Windows. Persistent remote shells install a safe interrupt path so the child
  command stops while the shell remains usable.
- Prompt, stdout, and stderr share one sequence stream, preventing duplicate or
  missing lines during concurrent cursor-based reads.

#### Model sources, settings, and timezone

- Added `custom_models`, `codex_model`, and `model_source` storage instead of
  forcing both authentication sources through one primary-candidate list.
- Legacy configurations migrate by safely inferring source and candidates from
  the prior ordering while preserving API keys, base URLs, vision, and
  secondary model settings.
- Custom OpenAI onboarding updates only custom candidates and activates
  `custom`; Codex OAuth onboarding updates only the independent Codex candidate
  and activates `codex`.
- `/api/settings/models` now returns `custom_models`, `codex_model`,
  `primary_source`, and active candidates, while accepting the validated new
  structure alongside compatible existing fields.
- Codex OAuth is constrained to Primary. Custom and Vision candidates cannot
  masquerade as OAuth, and selecting Codex requires a valid Codex candidate.
- The settings overlay keeps Custom and Codex form state independently, so
  switching source does not discard inactive values. Required-model and source
  errors are reported before submission.
- Timezone has a Config Store default and validated Settings route, and the
  Workbench Runtime exposes the resulting value to frontend and task execution.
- Frontend bootstrap fetches the saved timezone before `/api/ui-data` and
  synchronizes local storage. A failed settings write restores the prior local
  value.

#### Workbench chat ordering and navigation

- Chat cards use the dedicated `application/x-cyrene-chat+json` drag payload.
  Dropped ordering is persisted per project, and new chats normalize to
  newest-first.
- Focused cards support `Alt+ArrowUp` and `Alt+ArrowDown`; a live region
  announces the resulting position.
- The transcript accepts chat payloads, shows a clear drop state, and opens the
  dropped chat. Existing current-card and resource drag behavior is preserved.
- `.wbc-top-glass` spans the chat rail and transcript header but excludes the
  right panel. Duplicate pseudo-glass layers, the vertical divider, and
  inconsistent shadows are removed.
- Rail and chat headers now share compact height and spacing with responsive
  side width, preventing narrow layouts from crowding title and primary action.
- The long-conversation navigator moved from the left origin to the right-side
  panel, avoiding overlap with the rail and message content.

#### Inline images and attachment experience

- Recognized agent image attachments render as dedicated inline image cards;
  non-image artifacts retain the established generic file card.
- Preview width is capped at 280 px with a square cover crop and fully rounded
  clipping, eliminating oversized media and letterbox side bars.
- Clicking the preview opens the existing right-side viewer, while the footer
  action can still open the original in the system application.
- The entire image card reuses the existing resource drag payload, retaining
  drag-to-composer, library, and other supported workflows.
- The filename/action footer uses the same `--wb-card-bg` and control shadow as
  the composer, removes the hard bottom border, and compresses to 34 px.
- Open and Download are both 28×28 controls with the same 24 px viewBox,
  1.8 stroke, and centering rules. Explicit link normalization fixes the
  download icon's previous vertical and size mismatch.
- Image load failure falls back to the generic attachment card, preserving open
  and download access instead of leaving an empty preview.
- Regression coverage ensures an attachment-only turn still reaches native
  vision input with no artificial public placeholder text.

#### Remote sharing settings and usability

- Pairing Sharing Settings now use a collapsible `<details>` section with
  chevron, focus, hover, and open states to reduce default page height.
- Labels and hints clearly distinguish compatibility, direct tool packages, and
  shared projects, with complete English and Chinese controller guidance.
- Invite defaults initialize once from saved `remote_tool_packages` and
  projects; rerenders no longer overwrite user selections.
- Browser, Code, Delivery, Desktop, Entity, Integration, Knowledge, Map,
  Memory, Remote, Skill, Subagent, and Task packages now have mobile-facing
  names and descriptions.

#### Testing, design QA, and release

- Remote protocol tests cover no-listener direct transport, trust and replay
  enforcement, project/device shell ownership, settings and model validation,
  attachment limits, thumbnails, and failure cleanup.
- Config migration and frontend contract tests cover Custom/Codex independent
  persistence, legacy migration, timezone synchronization, source switching,
  chat ordering, inline image structure, and both image actions.
- `design-qa.md`, a dedicated remote-sharing QA record, and comparison/final
  captures for unified chat glass and inline images are committed with the
  implementation for future visual regression review.
- Python package, UV lock, Electron manifest/lock, README badges, web docs,
  WeChat client, WebUI cache keys, and contract tests are synchronized to
  Python `0.7.0b9` and Electron/Git tag `0.7.0-beta.9`.

---

## [0.7.0b8] - 2026-07-30

This is the eighth `0.7.0` beta and contains every change since
`v0.7.0-beta.7`. It focuses on long-running Agent execution and broad
OpenAI-compatible tool-call interoperability, reorganizes the Workbench
Library, context actions, side agents, and card layout, and adds isolated image
generation for **OpenAI Codex OAuth** models. Image generation uses the current
OAuth account without another API key. Existing custom OpenAI API and
OpenAI-compatible endpoint authentication, tool catalogs, and wire contracts
remain unchanged.

### Feature highlights

- **Generate images directly with OpenAI OAuth** — when the primary model uses
  `codex_oauth`, the Agent can call `GenerateImage` through the signed-in
  OpenAI/Codex account and deliver the result as an attachment, with no second
  API key.
- **Custom OpenAI APIs remain strictly isolated** — the image tool is absent
  from custom OpenAI-compatible catalogs and model requests. A forged call is
  rejected before any network operation.
- **High-quality generation no longer hits the fixed 180-second limit** — High
  gets a 300-second internal generation window and a 420-second tool envelope.
  Medium, Low, Auto, and every unrelated tool keep the existing 180-second
  limit, with no automatic retry that could consume quota twice.
- **Agents continue until the task is actually complete** — the fixed tool
  round ceiling is removed. Long jobs continue until explicit completion,
  cancellation, or an unrecoverable failure.
- **More local models can call tools reliably** — Cyrene accepts structured
  arguments, legacy `function_call`, Hermes and Qwen XML, streamed object
  arguments, fenced JSON, trailing commas, and safe bare-JSON actions, while
  repairing missing call IDs and common gateway envelopes.
- **Library is the single knowledge workspace** — groups, collections, tags,
  inline tag editing, Markdown content, explicit pagination, starring, and
  aligned selection replace the duplicated legacy Knowledge page.
- **Side agents support multiple persistent tabs** — selected text can start an
  independent side agent without leaking the hidden quote into the public
  question. Every tab preserves messages, composer, live state, and deletion.
- **Context actions cover more of the workspace** — composer menus close on
  outside interaction, chat blank space reuses quick actions, and knowledge,
  memory, schedules, and native browser tabs expose relevant context menus.
- **Workbench surfaces share one visual hierarchy** — borderless cards,
  restrained shadows, functional frosted headers, and hidden-but-scrollable
  rails now cover Chat, Overview, Library, and Memory.
- **Tab terminology is consistent** — English shortcut labels use “tab” and
  Chinese labels use “标签页,” replacing mixed “Session / Session Tab” wording.
- **Releases and frontend builds are more reproducible** — Node.js 22.12,
  Electron, electron-builder, esbuild, React, and Ruff use pinned compatible
  baselines, and WebUI builds derive every asset cache key from the Python
  package version.

### Detailed changes and compatibility notes

#### OpenAI Codex OAuth image generation and isolation

- **The tool is registered only for the OAuth primary model** —
  `GenerateImage` enters the Agent catalog and wire definitions only when the
  current primary candidate provider is `codex_oauth`. Custom
  OpenAI-compatible candidates, API keys, base URLs, fallback, secondary, and
  vision settings are not changed.
- **The provider is checked again at execution time** — the tool layer reads
  the active source instead of trusting model visibility. Non-OAuth calls fail
  before an SDK client, capability read, or network connection can start, so a
  forged tool call cannot bypass catalog isolation.
- **The existing OpenAI sign-in is reused** — Codex SDK/App Server performs the
  request with the current OAuth session. Cyrene asks for no image API key and
  does not read or persist access or refresh tokens.
- **A dedicated least-privilege image client is used** — each generation runs
  in an isolated ephemeral client with `features.image_generation=true` while
  plugins, apps, shell, unified exec, browser, computer use, multi-agent, web,
  view-image, and host skills are disabled. The sandbox is read-only and
  approval policy is `never`.
- **Capability is verified at call time** —
  `modelProvider/capabilities/read` confirms image support. Exhausted quota,
  expired authentication, model unavailability, and missing capability become
  actionable errors instead of falling through to a custom endpoint.
- **All supported result events are collected** — both `imageGeneration` and
  `image_generation_call` are handled, including revised prompt, Base64 image
  data, and SDK-saved paths, then normalized into a Cyrene attachment.
- **Inputs and outputs are validated** — prompt, size, quality, output format,
  maximum 30 MB size, and actual image bytes are checked before delivery.
- **Temporary files have a bounded lifecycle** — output is written to a
  controlled temporary path, delivered through the registered `send_file`
  channel, and removed after either success or failure.
- **High quality uses layered timeouts** — High allows up to 300 seconds inside
  the SDK and 420 seconds around the tool. Other image qualities and all
  non-image tools stay at 180 seconds. Timeout does not trigger an automatic
  duplicate generation.
- **Native OAuth image input remains available** — uploaded images still become
  native Codex image turn input. This release adds image output without
  changing existing vision input or OpenAI-compatible image-input behavior.

#### Agent tool protocols, gateway repair, and continuous execution

- **The fixed tool-round ceiling is gone** — runtime no longer reads or applies
  `MAX_TOOL_ROUNDS`. Migration purges old values, restore drops the field, and
  settings reject attempts to reintroduce it.
- **Runs end on explicit outcomes** — the Agent loop keeps processing tool
  results, guidance, and model turns until a normal final reply/`quit`, user
  cancellation, or an unrecoverable error, rather than an arbitrary counter.
- **Structured argument objects are accepted** — OpenAI-compatible providers
  handle JSON strings and already-parsed objects. Stable IDs are synthesized
  when a provider omits the tool-call ID.
- **Legacy `function_call` works in both modes** — non-streaming and streaming
  responses are promoted to standard `tool_calls`, including fragmented names,
  arguments, and IDs.
- **Common local-model formats are parsed** — Hermes `<tool_call>` JSON, Qwen
  XML functions and parameters, fenced JSON, and trailing commas are
  supported. A bare JSON action is executable only when its action name is an
  available tool, so ordinary JSON prose is not misclassified.
- **Streaming argument assembly is safer** — string fragments, object fragments,
  and single complete objects follow separate paths, preventing invalid string
  concatenation or overwriting received data.
- **Gateway aliases remain hidden** — deferred capability IDs can execute
  directly without expanding into model wire definitions. Schemas and package
  guards remain enforced, and discovery explains capability IDs and gateway
  invocation.
- **Nested invocation envelopes are repaired** — double-nested, fully nested,
  and commonly malformed wrappers are normalized. Required fields are
  projected through wrapper layers and invalid values can be reconstructed
  from schema-valid inputs.
- **DeepSeek reasoning effort is normalized** — UI and session values map to
  provider-supported API values rather than sending presentation labels.
- **Attachment-only turns preserve their meaning** — a turn containing only an
  image keeps an empty public message while the native image content reaches
  the model, avoiding invented visible placeholder text.
- **Onboarding uses canonical settings** — only supported time zones are saved
  through the general time-zone path, and the welcome import panel exposes
  exactly the three supported choices.

#### Workbench Library, side agents, and context actions

- **Library replaces the duplicated Knowledge page** — Workbench Library is the
  sole knowledge-management surface and the old `workbench-knowledge`
  implementation is removed.
- **Grouping and filtering are complete** — groups, collections, tags, inline
  tag editing, starring, aligned selection controls, and explicit pagination
  are available with localized labels and empty states.
- **Content reading uses the shared Markdown renderer** — the Content tab
  renders complete document bodies while list/detail selection remains stable,
  and the star control precedes the aligned selector.
- **Side-agent sessions persist independently** — multiple side agents can be
  maintained without appearing in the main chat list; their tabs, messages,
  running state, and composer survive workspace switches.
- **Quoted context does not pollute the public question** — selected text is
  supplied as hidden context, while the visible user message contains only the
  user's actual prompt. Each selected-text agent owns its own composer, live
  state, and delete confirmation.
- **Users control right-column card order** — Chat and Overview cards are
  sortable and persisted. Pinned sessions are not removed by the three-recent
  limit.
- **The composer remains stable at narrow widths** — the model picker compacts
  without covering Send, and menus close correctly on outside pointer-down,
  Escape, and scene changes.
- **Chat blank-space actions are reused** — quick actions share existing
  command and permission entry points, while Quick Rename uses the standard
  dialog instead of native browser `prompt`.
- **Knowledge and Memory reuse established operations** — knowledge items can
  Reveal in Folder and memory items keep their existing actions without a
  second inconsistent menu.
- **Schedule actions work across calendar views** — context operations are no
  longer limited to one list presentation.
- **Native browser tab actions are complete** — Reload, Mute, and Close are
  available. Cyrene snapshots and hides the native content surface before the
  menu appears, then restores it, preventing browser content from covering the
  overlay.
- **Shortcut wording is consistent** — Open, Next, Previous, and Remove tab
  actions use “tab” in English and “标签页” in Chinese throughout labels and
  descriptions.

#### Workbench visual and interaction polish

- **Cards use a borderless surface hierarchy** — conversation and Overview
  cards drop hard borders and focus outlines; active state uses a subtle tint.
  Search, composer, share, and card surfaces use restrained two-layer shadows.
- **Focus no longer recolors the whole surface** — search and composer
  backgrounds remain stable, avoiding visual jumps during typing.
- **The conversation header is a functional frosted overlay** — it is
  positioned above scrolling content, using 46 px blur, 165% saturation, and a
  staged gradient derived from `--wb-main-bg` in light and dark themes.
- **Chat rail and Overview share the same glass language** — blur, fade, and
  control readability are consistent instead of being tuned as unrelated
  regions.
- **Library and Memory overlays participate in scrolling** — redundant top
  spacing is removed, sidebar overlays are shorter, and content visibly passes
  under the glass treatment.
- **Scrollbars are hidden without disabling scroll** — conversation transcript,
  rail, Overview, Library, and Memory continue to support mouse, trackpad, and
  keyboard scrolling without visually heavy scrollbars.
- **Design regression evidence is checked in** — `design-qa.md` and screenshots
  record light, dark, scroll, and overlay iterations for later verification.

#### Build, dependency, quality, and release changes

- **Node.js 22.12 is the shared baseline** — CI, release workflows, and source
  build documentation now require the same minimum version.
- **The desktop toolchain is pinned** — Electron is `43.2.0` and
  electron-builder is `26.15.7`, with trusted build scripts configured. The
  Electron 43 `console-message` details API is reflected in the main process
  and smoke tests.
- **WebUI ships production React assets** — React and ReactDOM are pinned to
  `18.3.1` and production bundles are copied during build; obsolete development
  assets are removed.
- **Frontend compilation is reproducible** — esbuild is pinned to `0.28.1`.
  The build reads `pyproject.toml` and rewrites cache keys for CSS, JavaScript,
  PDF.js, and its worker from one version source.
- **Dynamic routes do not carry stale versions** — PDF asset routes use runtime
  `get_version()` instead of a historical hard-coded cache key.
- **Ruff is an explicit CI contract** — the development dependency is bounded
  to `>=0.15,<1`, CI runs locked `ruff check src tests`, and only compatibility
  facades and deliberate test-time imports receive narrow exceptions.
- **Release installation commands are shell-safe** — pip specifiers containing
  version comparisons are quoted so shells cannot interpret them as
  redirections.
- **Runtime databases are excluded from distributions** — wheels and Git ignore
  SQLite database, SHM, and WAL files to keep local state out of source and
  packages.
- **Contracts and packaging tests cover the new behavior** — OpenAPI, routes,
  WebUI assets, Electron runtime, OAuth image isolation, tool protocols,
  timeouts, and localized shortcut wording are updated. This release is
  versioned as Python `0.7.0b8` and Electron/Git `0.7.0-beta.8`.

---

## [0.7.0b7] - 2026-07-29

This is the seventh `0.7.0` beta and contains every change since
`v0.7.0-beta.6`. It brings OpenAI Codex OAuth into model setup, onboarding,
Workbench conversations, and task execution; adds per-session model and
reasoning-effort selectors; and systematically strengthens Agent streaming,
termination semantics, tool routing, model fallback, and the interactive CLI.
The provider layer now uses a pinned Codex SDK without asking Cyrene to hold
OAuth tokens, and Codex quota is presented separately from currency budgets.

### Feature highlights

- **Use Codex models from your OpenAI account directly** — sign in during
  onboarding or under Settings → Models. Cyrene lists the models available to
  that account without requiring an API key or storing the OAuth token itself.
- **Choose a model before sending** — both chat and task composers now have a
  compact model button that lists every configured model and lets you choose
  the reasoning effort for the current one.
- **Different conversations can use different models** — changing the model in
  one Chat or Task does not alter other conversations or the global default.
  The choice is restored after reopening, refreshing, or forking a chat.
- **Reasoning choices stay realistic** — Codex shows the efforts supported by
  that exact model. Custom models without capability metadata offer only
  Low/Medium/High rather than presenting unsupported extreme levels.
- **The task selector appears immediately** — the control renders as soon as
  configured models load and fills in detailed Codex capabilities afterward,
  instead of leaving a temporary empty space.
- **Codex OAuth models understand images directly** — Workbench converts
  uploads to native Codex App Server image turn inputs. Capability flags saved
  as unsupported by older versions are upgraded at runtime without requiring
  users to remove and re-add the model.
- **Codex quota is visible where you need it** — Settings and the account menu
  show the five-hour or weekly windows actually provided by the account,
  including remaining percentage and reset time, separately from API spending.
- **Model problems are actionable** — Workbench distinguishes exhausted quota,
  expired sign-in, and unavailable models, explains what to do, and continues
  to a configured fallback instead of appearing frozen.
- **Long replies and tool-heavy tasks run more smoothly** — streaming saves
  with less overhead, while cancellation, recovery, parallel tools, and new
  instructions arriving near completion are handled more reliably.
- **Reasoning is easier to follow** — Workbench separates request understanding
  from later tool execution and shows total processing time. Codex no longer
  presents an expandable internal-detail area that does not apply to it.
- **The terminal app feels closer to the desktop app** — localized model,
  project, workspace, input, and permission context; clearer session cards;
  left/right settings categories; and a temporary `Ctrl+O` reasoning viewer are
  now available without polluting shell scrollback.
- **Small Workbench layouts are steadier** — clicking an already visible work
  tab no longer reshuffles the topbar, and compact Memory tabs plus long-content
  alignment are improved.
- **A fresh install can open Knowledge immediately** — startup creates the
  required knowledge-data directories automatically, so chats, tasks, and
  Knowledge initialization no longer depend on files left by an earlier Cyrene
  run.
- **Ubuntu packages no longer crash on sandbox permissions** — `.deb` and
  `.rpm` installs configure Chromium's sandbox correctly, while AppImage or
  manually copied builds use a compatible fallback when the helper is
  unavailable instead of exiting with `SIGTRAP` or “crashed unexpectedly.”
- **A missing local configuration key no longer blocks startup** — if an
  upgrade, copied data directory, or partial cleanup leaves `config.enc`
  without its installation-local key, Cyrene preserves the unreadable file and
  starts with default settings instead of terminating the desktop backend.
- **Installed builds can sign in to OpenAI** — desktop packages now include the
  pinned Codex App Server executable and all companion resources, and the
  release smoke test executes that runtime so the OpenAI login button is not
  disabled by a missing OAuth backend.
- **Browser content no longer covers overlays** — the embedded native browser
  temporarily yields while model/reasoning menus, confirmation prompts, or
  topbar tab context menus are open, then restores automatically.
- **Common items expose their actions on right-click** — project, task, chat,
  and knowledge cards can now open their existing action menus directly,
  without first finding the small overflow button.

### Detailed changes and compatibility notes

#### OpenAI Codex OAuth, model discovery, and quota

- **The official Codex SDK runtime is pinned** — the core dependency adds
  `openai-codex==0.144.4`; `uv.lock` pins both the Python adapter and
  platform-specific `openai-codex-cli-bin`, keeping development, installation,
  and release builds on the same protocol version.
- **OAuth credentials remain owned by the Codex App Server** — Cyrene performs
  login, logout, account, model discovery, turns, and rate-limit calls through
  the SDK/App Server. It neither reads `~/.codex/auth.json` nor directly stores
  access or refresh tokens.
- **A complete settings API is available** —
  `/api/settings/openai-oauth` reports connection, account, and models;
  login/logout routes manage authentication; a separate `/limits` route keeps
  slow quota requests from delaying connected state and model choices.
- **Onboarding supports OpenAI sign-in** — setup can choose between a custom
  OpenAI-compatible endpoint and OpenAI OAuth. Saving validates login, model
  availability, and reasoning effort before writing the canonical candidate
  and onboarding state.
- **The Codex adapter forwards images natively** — OpenAI-compatible
  `image_url` content is converted to Codex App Server `image` turn input,
  while conversation replay keeps only a matching placeholder instead of
  embedding Base64 image data as ordinary JSON text.
- **OAuth capability flags are backward compatible** — Codex OAuth candidates
  are consistently treated as vision-capable. Workbench and attachment
  analysis use the active OAuth model even when an older configuration
  persisted `vision_capable: false`.
- **Custom and OAuth models coexist** — existing OpenAI-compatible candidates,
  endpoints, API keys, fallback, secondary, and vision flows remain intact,
  while candidates gain `provider` and `reasoning_effort` metadata.
- **The primary source switcher is streamlined** — one compact menu switches
  between Custom and OpenAI OAuth. Selected, hover, focus, Escape, and
  click-outside states follow the existing settings visual language without
  redundant nested cards.
- **Model layout is denser without losing editability** — Primary stays
  directly editable; fallback, secondary, and vision move into summarized
  collapsible sections; Save and Apply remains in document flow and the fixed
  settings height, responsive labels, and accessible states are preserved.
- **Reasoning efforts come from the selected model's capabilities** — Low,
  Medium, High, Extra High, and other values are filtered from
  `supportedReasoningEfforts` instead of exposing one global hard-coded set.
- **Quota windows share one parser** — a 300-minute window is the five-hour
  quota and 10080 minutes is weekly. Missing windows are omitted, while
  remaining percentage, progress, and reset time are normalized once for both
  Settings and the account menu.
- **Quota reads use stale-while-revalidate** — fresh cache is returned directly;
  stale usable data returns immediately while refreshing in the background.
  Temporary query failure does not disable a model, but a cached exhausted
  state remains conservatively enforced to prevent retry storms bypassing quota.
- **The existing account menu structure is preserved** — the Codex summary
  appears above existing actions only when the primary provider is
  `codex_oauth` and the account is connected; spacing, icons, radius, actions,
  and footer remain unchanged.
- **Codex quota monitoring is persisted independently** —
  `codex_budget_enabled` is separate from currency budgets. Login enables plan
  quota monitoring without mutating ordinary API budget settings.

#### Codex provider, tool routing, and recoverable fallback

- **The provider uses an isolated SDK client** — Codex turns run in ephemeral
  threads with a read-only sandbox and `approvalPolicy=never`. Cyrene's own
  Agent loop still owns permissions, tool execution, and user confirmation;
  host tools are not directly exposed to the Codex App Server.
- **Conversation replay is thread-correct** — system/developer instructions are
  assembled separately from conversation messages without duplicating the
  system prompt. Concurrent sessions use independent thread/turn notification
  queues and cannot cross-stream events.
- **Transport honors the system proxy and fails early** — connection failure,
  provider stop, or a long absence of upstream signals interrupts the turn and
  advances fallback instead of waiting for a generic request timeout.
- **Reasoning summaries and usage are preserved** — public reasoning, effort,
  input/output/total tokens, and prompt-cache hits flow to CLI, Workbench, and
  budget/diagnostic consumers.
- **Host plugins and skills cannot contaminate provider behavior** — provider
  startup isolates its working directory and disables host plugins, apps,
  Browser, Computer Use, Image Generation, Shell, Unified Exec, Web Search,
  multi-agent, and discovered skills.
- **Actions use a structured contract** — JSON Schema carries visible content
  and one or more tool calls; strict `arguments_json` validation rejects
  malformed arguments, unknown tools, and tool markup leaking into user text.
- **Phase 1 and execution tools have explicit boundaries** — the understanding
  phase receives only enter-execution/finish control actions. Authorized Cyrene
  catalog tools are exposed after execution begins, reducing premature or
  impossible tool choices.
- **Tool discovery understands more natural intent** — capability search ranks
  detailed Browser intents more effectively and falls back to the package
  catalog for Chinese shorthand or unknown terms instead of returning an empty
  discovery result.
- **Codex failures become actionable states** — quota exhausted,
  authentication expired, and model unavailable are conservatively classified
  from SDK ErrorInfo, HTTP context, and messages. Ambiguous 401/403 values are
  not guessed, and explicit model errors override status-code heuristics.
- **Workbench surfaces provider-specific warnings** — failures publish phase
  events carrying provider, failure kind, translation key, and model
  parameters, so the UI can explain the corrective action before continuing to
  the next candidate.
- **Cooldown is limited to persistent failures** — quota exhaustion may
  temporarily suppress a candidate, while immediately recoverable
  authentication or model-selection errors do not incorrectly keep it cold.
- **Stop and cancellation cleanup is explicit** — provider-stop notifications,
  turn interruption, reader termination, pending requests, and notification
  queues are settled so canceled turns cannot leak work into the next run.
- **Model events retain provider identity** — LLM start/delta/done and failure
  events carry provider, model, phase, and usage, allowing Workbench to render
  Codex and OpenAI-compatible activity correctly.

#### Agent streaming, termination semantics, and run recovery

- **Delta persistence is batched** — `reasoning_delta` and `reply_delta` are
  grouped for up to 50ms/128 events per SQLite transaction while retaining
  every sequence number and cursor. Replay semantics remain unchanged while
  token-stream database backpressure drops.
- **Terminal states force persistence** — finalize, interrupt, error, and run
  completion flush pending batches first. Writes canceled while in a worker
  thread are safely re-queued using idempotent `(run_id, seq)` keys.
- **The reasoning lifecycle is complete** — Workbench and CLI consume
  `reasoning_start/delta/done`; Phase 1 reasoning merges into the matching LLM
  activity instead of creating flickering or duplicate cards.
- **Execution cards distinguish understanding from tools** — Phase 1 shows
  “Understanding/Understood the request” with a compact reasoning preview.
  Later summaries use the real tool-call count, and Codex does not expose an
  irrelevant expandable internal trace.
- **Replies show total processing duration** — one formatter covers subsecond,
  second, minute, and hour values without excluding reasoning, tools, or queue
  time.
- **`quit` is an irreversible terminal signal** — the complete answer belongs
  in normal assistant content and quit is control-only. If a batch mixes quit
  with other tools, all siblings are skipped and the run cannot re-enter
  execution.
- **Post-termination reply recovery is safe** — empty, placeholder, or
  tool-markup replies can only be repaired through the no-tool final-reply
  path; completed tool work is never followed by a fabricated answer the model
  did not write.
- **Legacy/DSML tool markup no longer leaks** — terminal replies and Workbench
  messages suppress old tool blocks, DSML markers, and pseudo-replies hidden in
  quit arguments; only normal assistant content is accepted, and empty Enter
  does not create a meaningless turn.
- **Late guidance is preserved** — finalization waits for active tools and then
  checks the Inbox. New guidance creates a continuation rather than reviving a
  terminated tool batch.
- **Hidden session-naming work is removed** — chat runs no longer schedule
  invisible title-generation calls. The compatibility label refresh is a no-op,
  avoiding extra model calls, crossed events, and completion latency.
- **Agent prompts use one lifecycle contract** — main agent, subagent, deep
  reflection, and runtime guidance now agree on tool entry, progress updates,
  and termination, fixing quit/tool-result pairing, search, and stop regressions.

#### Composer model selection and session preferences

- **Chat Composer gains a compact model button** — it shows a friendly model
  name, current effort, and chevron. Root/model/effort menus support Escape,
  click-outside, ARIA expanded state, and checked current items.
- **Task Composer uses the same component language** — it lists every
  configured candidate with the same menu width, row density, typography,
  icons, interaction, and bilingual text as Chat.
- **Light and dark states are calibrated separately** — light normal uses
  `#eaf0f4` with clearer hover/active states; dark normal is lighter than the
  previous attempt while hover/active retain hierarchy without a teal cast.
- **Menu density matches the reference UI** — the panel is tightened to about
  `260px`, long model names truncate safely, secondary values and chevrons
  align, and the button sits closer to the composer edge without crowding Send.
- **Efforts are normalized per model** — Codex uses the current model's declared
  capabilities ordered Low → Medium → High → Extra High → Max → Ultra. Custom
  models without a capability catalog fall back only to Low/Medium/High rather
  than exposing undeclared Max/Ultra levels.
- **The Task selector no longer appears late** — the button renders as soon as
  `/api/settings/models` returns; the slower OAuth capability catalog enriches
  it asynchronously instead of blocking the entire control.
- **Request contracts carry model preferences** — Chat/Task bodies accept
  optional `model` and `reasoningEffort`; routes validate the candidate,
  normalize effort, and set session preferences before the Agent starts.
- **Runtime resolves candidates per session** — each session may override
  candidate ID, model, base URL, and effort while unset sessions continue using
  global ordering, preventing one conversation's choice from changing others.
- **Session payloads restore the choice** — Chat/Task responses include
  `modelSelectionId` and `reasoningEffort`, preserving composer state across
  refreshes, switches, and forks.
- **Authentication fallback does not create a long cooldown** — selecting a
  Codex model that needs reauthentication may fall back to the next candidate
  while remaining immediately retryable after sign-in.

#### Interactive CLI, Workbench tabs, and UI details

- **The CLI header carries complete context** — model, project,
  workspace/Git branch, and version appear below a single-line brand mark, with
  terminal-display-width-aware clipping for long paths.
- **The input area is fully localized** — placeholders, bottom toolbar,
  permission mode, exit hint, settings labels, and value previews follow the
  selected language.
- **Resume uses two-line session cards** — title/project appear on the first
  line and preview on the second, with blank lines between cards and safe
  clipping for both CJK and Latin display widths.
- **`/config` has two-axis navigation** — Left/Right moves across General,
  Models, Tools, Connections, Data, and About; Up/Down selects a setting and
  Enter opens it. General and CLI-preference field lists use the same keyboard
  model.
- **Reasoning details use a temporary viewer** — `Ctrl+O` opens a full-screen,
  scrollable public-reasoning view; Ctrl+O, Escape, Q, or Ctrl+C closes it,
  restores the prompt, and leaves no reasoning text in shell scrollback.
- **Thinking activity reuses the app phrase pool** — a different localized
  phrase is selected about every four seconds without consecutive repetition,
  while completion retains the compact “Thought for Ns” summary.
- **Ctrl+C and Escape have distinct jobs** — Ctrl+C keeps the global
  double-confirm exit behavior, while Escape cancels the current modal/editor;
  closing Settings or the viewer cannot accidentally stop the CLI or run.
- **Recent Workbench tab ordering is stable** — selecting an already visible
  session no longer reorders the entire topbar. Only opening a hidden session
  updates the recent list, which keeps up to 20 stable keys.
- **Memory compact labels are complete** — Related/History use shorter English
  and Chinese labels on narrow layouts, with improved grid, padding, and
  long-content alignment in the detail hero.

#### Contracts, tests, versioning, and beta7 publication

- **The OpenAPI contract includes model fields** — the schema SHA256 is
  intentionally refreshed after adding optional Chat/Task fields, while the
  operation count and locked FastAPI/Pydantic generator versions remain fixed.
- **Codex provider regression coverage expands** — login, account, models,
  limits, turn streaming, usage, tool actions, cancellation, provider stop,
  host isolation, quota/auth/model classification, and cooldown are covered.
- **Model selection is tested end to end** — frontend menus, session
  preferences, chat forks, task dispatch, route validation, candidate override,
  reasoning effort, and asynchronous capability enrichment have regression
  coverage.
- **Agent and CLI regressions are locked down** — durable delta batches, cursor
  replay, phase activity, total duration, mixed quit batches, late guidance,
  no-tool reply recovery, CLI localization, viewer behavior, config navigation,
  and session cards are covered.
- **Clean CI now matches local results** — the Python job builds WebUI fixtures
  before contract tests, while CLI help, temporary knowledge databases, and
  local-time tests no longer depend on a developer machine's existing setup,
  data directories, or timezone. Strict thread-warning mode is stable.
- **Linux packages receive a real installation smoke test** — the release gate
  now installs the `.deb` on an Ubuntu runner, verifies root/4755 ownership for
  `chrome-sandbox`, and launches the app from its installed location, covering
  the same executable used by the desktop entry. The test also seeds an
  encrypted config without its local key and verifies that the backend
  preserves it and still starts successfully.
- **The complete local suite passes** — all `1,611` pytest tests pass in the
  project `.venv` with no pre-existing Cyrene data directories; the beta7
  frontend production build, OpenAPI contract, focused Codex/Workbench
  regressions, and `git diff --check` also pass.
- **Every version surface moves to beta7** — Python package/`uv.lock` use
  `0.7.0b7`; Electron package/lock use `0.7.0-beta.7`; README badges, docs
  sidebar, WeChat header, Workbench/PDF cache keys, and version-contract tests
  agree.
- **Tag-driven prerelease** — `v0.7.0-beta.7` triggers the existing release
  workflow to build macOS DMG, Windows x64/ARM64 installers, and Linux
  AppImage/deb/rpm, run frozen and real-desktop smoke tests, and extract this
  section as GitHub prerelease notes.

---

## [0.7.0b6] - 2026-07-28

This is the sixth `0.7.0` beta and includes every change since
`v0.7.0-beta.5`. It adds an interactive CLI, Workbench work tabs, and a pinned
resource shelf; improves memory, configuration, and notifications; and fixes
the Linux AppImage white-window issue. Linux prereleases now include AppImage,
Debian `.deb`, and Red Hat/Fedora-family `.rpm` packages.

### Features

- **Interactive CLI** — run `cyrene` to create, select, and resume Workbench
  conversations from the terminal, including attachments, context, settings,
  permission prompts, and run recovery.
- **Upgraded Workbench topbar** — keep up to three task/chat work tabs and pin
  files, knowledge items, selected text, or Browser pages in a durable resource
  shelf for reuse across conversations.
- **More reliable memory and configuration** — improved project memory,
  entity capture, search scope, and history compatibility; installation-local
  encryption keys prevent development and packaged apps from losing access to
  the same configuration.
- **Better notifications and UI behavior** — notifications return to the
  relevant project, chat, task, or resource, with improved keyboard access,
  localization, fallback progress, background efficiency, and memory-detail
  wrapping for long text.
- **Linux AppImage white-window fix** — Linux now uses the more compatible
  software-rendering path by default and includes window diagnostics plus a
  real UI smoke test.
- **More Linux packages** — prereleases now ship AppImage, Debian `.deb`, and
  Red Hat/Fedora-family `.rpm` packages together.

### Technical details

#### First-class interactive CLI shared with Workbench

- **Bare `cyrene` is the recommended terminal entry point** — it discovers a
  healthy daemon, starts and waits for one when necessary, then enters chat.
  `cyrene chat` is the equivalent explicit entry point; existing
  `start/status/stop/do/session` commands remain compatible.
- **Real Workbench conversations are reused** — the CLI creates, lists,
  selects, and resumes durable chats with project names. It does not create a
  third Agent loop or an isolated session, so Web, Electron, and terminal share
  messages, tasks, memory, and run state.
- **Per-run NDJSON replaces global-event competition** — the Workbench route
  streams public run-start, phase, tool, plan, reasoning, reply,
  pending-question, finalizing, interrupt, and error events for one run. The
  CLI neither competes with Web clients for the global SSE queue nor receives
  unrelated session events.
- **Cursor-based reconnect is durable** — `cyrene chat --chat <id> --resume
  --cursor <n>` continues a current or recent run from an event sequence;
  `--list` exposes resumable conversations, and one-shot calls target an actual
  chat instead of only appearing to support arbitrary legacy sessions.
- **The line-oriented Rich UI shows the full public lifecycle** — randomized,
  non-repeating `✶ ✸ ✹ ✺ ✷ ◌` activity marks accompany phase transitions, tool
  start/progress/finish, plans, steps, streaming replies, and final elapsed
  time while retaining normal shell scrollback instead of using a full-screen
  TUI.
- **Reasoning is compact by default** — public reasoning displays as “Thought
  for Ns”; `Ctrl+O` toggles the current turn, and `/config` persists
  `thinking=compact|expanded`. Hidden reasoning, credentials, and unredacted
  tool arguments remain excluded.
- **Permissions and questions complete in the terminal** — pending questions
  pause dynamic status, show choices or a text prompt, and continue through the
  canonical answer route. The CLI never auto-approves; non-interactive mode
  returns a clear machine-readable failure when input is required.
- **Attachment drafts are manageable** — `/attach`, `/attachments`, and
  `/detach` queue, inspect, and remove files through the Workbench attachment
  contract. Size errors, invalid files, and server rejection remain visible.
- **The interactive command surface is complete** — `/new`, `/resume`, `/mode`,
  `/status`, `/deep-reflect`, `/deep-research`, `/context`, `/config`, `/mcp`,
  `/help`, and `/exit` are supported. The conversation CLI uses `/new` rather
  than inheriting the legacy in-process REPL's `/clear` semantics.
- **`/context` matches the app Context card** — it reads the same composition
  data and blocks, showing tokens, a semantic color bar, and System Prefix,
  Ephemeral Injection, and Conversation Message groups with consistent user,
  assistant, tool, and system indentation.
- **`/config` covers common administration** — backend settings, models,
  capability packages/tools, keys, SOUL, integrations, MCP, skills, remote
  control, profile, budget, data, and CLI preferences can be inspected or
  updated without opening a browser for basic setup.
- **Terminal behavior remains safe and asynchronous** — Prompt Toolkit provides
  history, completion, arrow-key selection, and `Alt/Esc+Enter` multiline
  input. The first `Ctrl+C` arms an exit warning and a second press within two
  seconds exits; leaving the CLI does not kill a daemon-owned background run.
- **Automation is supported** — `cyrene chat --json <text>` emits stable public
  events one per line, non-TTY mode avoids ANSI Live output, and the decoder
  handles split records, multiple records per chunk, and a final record without
  a newline.
- **Electron and CLI reuse one backend** — Electron atomically publishes URL,
  token, Electron PID, and backend PID in an isolated temp directory with Unix
  mode `0600`, then removes the capability when its owning process exits. The
  CLI no longer starts a second service that competes for the runtime database,
  scheduler, or port.
- **Authentication is connected automatically** — standalone daemon clients
  use explicit `CYRENE_AUTH_TOKEN`; Electron clients use the local connection
  capability. Both send `X-Cyrene-Token`, and authentication errors explain
  how to connect instead of returning only a generic HTTP failure.
- **Terminal dependencies are locked product dependencies** —
  `prompt-toolkit>=3.0.52` and `rich>=15.0.0` are in the core package and
  `uv.lock`, keeping regular installs, PyInstaller, and development consistent.

#### Workbench topbar work set and pinned resources

- **The breadcrumb becomes up to three live task/chat work tabs** — opening,
  creating, or switching a session updates the MRU. Refreshes preserve the
  current selection and mixed task/conversation ordering.
- **Tabs can be pinned or removed without changing underlying work** — the
  context menu supports pin/unpin, title copy, browser/file inspection, and
  topbar removal. Removal neither deletes a chat nor stops a run or task.
- **A separate Pinned Resource Shelf accepts multiple sources** — chat file
  cards, Knowledge/Library rows and cards, native macOS selected text, and
  floating or minimized Electron Browsers can be pinned.
- **Resources stay compact and keyboard-accessible** — file/browser chips are
  SVG-only until hover or focus reveals a label. Search is reduced to `168px`,
  a `10px` action gap is preserved, and the empty `+` has a hover hint.
- **Selected text and attachment-free knowledge become Markdown** — new exports
  use ASCII storage keys while routes retain compatibility with old Unicode
  export names.
- **Files and text can be delivered to another chat draft** without automatic
  send or source mutation.
- **Browsers can be copied across conversations** — dropping a PiP, minimized
  favicon, or pinned Browser on another tab creates the same URL in that
  session's independent `BrowserTabManager`. Login partition is shared; page
  state and control are not.
- **Native-view drag hit testing is reliable** — Browser pinning compares
  directly with the shelf rectangle instead of depending on
  `elementFromPoint` beneath a `WebContentsView`; a body-level proxy crosses
  titlebar and transcript clipping boundaries.
- **Minimized Browser becomes a favicon-only round button** with immediate
  Browser-SVG fallback. Click restores; threshold dragging moves, pins, or
  delivers it while retaining PiP transcript avoidance.
- **Pinned files enter later Agent context as compact global indexes** and load
  content only on demand.
- **Pinned Browsers enforce ownership in the execution layer** — only the owner
  has full control; other sessions are limited to snapshot/screenshot and
  cannot navigate, click, type, reload, upload, or take over the page.
- **The resource registry is durable and deduplicated** — removal, Library
  source metadata, selected-text materialization, global file context, and
  Browser read-only resolution have dedicated storage and APIs.
- **Keyboard operation is complete** — arrows/Home/End traverse,
  Enter/Space opens, Delete/Backspace removes, `Cmd/Ctrl+1…3` opens a work tab,
  `Ctrl+Tab` cycles, and `Cmd/Ctrl+W` removes the current tab. Project moves to
  `Cmd/Ctrl+Shift+1`.

#### Memory capture, entities, and configuration reliability

- **Run completion carries verified evidence** — memory capture now receives
  session/chat, user language, and current successful tool evidence. Failed,
  stale, and incomplete tool results are excluded before long-term facts are
  written.
- **Project memory follows the user's language** — `SaveProjectMemory` requires
  the target language, English-dominant mixed content is normalized, and
  neutral paths/identifiers avoid pointless translation.
- **Default-project scope no longer aliases global short-term memory** — the
  resolver distinguishes default project, explicit workspace, and global
  storage.
- **Memory search is more accurate and bounded** — multiple terms use OR
  retrieval, stale items are excluded, large results have row/character limits,
  external search hides internal history, and Workbench hides task reports.
- **Citation and history backfill safely** — old records derive created/updated
  history from timestamps without a destructive migration.
- **Steward reads both legacy and Workbench archives** — it scans default and
  project session Markdown with file-count, per-file, and total-character
  bounds, while legacy daily archives remain on their existing path.
- **Foreground entity extraction takes priority** — definite entities are
  captured during the current turn, with Steward retained as a safety net.
  Source, confidence, update, and deduplication behavior are tested.
- **Capture scheduling accepts evidence, language, and session metadata**
  through compatible keyword arguments.
- **Configuration no longer breaks across process identities** — development
  and packaged processes can see different OS-keyring identities while sharing
  `DATA_DIR`. Cyrene now uses an installation-local Fernet key beside the
  config with mode `0600` and exclusive creation for first-launch races.
- **Decrypt failures preserve current data** — a missing/invalid key or
  `InvalidToken` fails explicitly; no replacement key is generated and no
  stale legacy backup overwrites `config.enc`. Portable backups still export a
  logical snapshot and re-encrypt at the destination.
- **Unused keyring packaging is removed** from Python dependencies, `uv.lock`,
  and PyInstaller, eliminating headless Linux Secret Service tracebacks.

#### Notifications, Agent responsiveness, and smaller UI fixes

- **Notifications return to their precise context** — project, chat, task, or
  resource metadata navigates to the associated workspace/session instead of
  only marking a row read; workspace display names use a shared helper.
- **Notification accessibility is complete** — bilingual action text,
  hover/focus states, clickable semantics, and keyboard paths remain clear at
  high contrast and larger text.
- **Agent waiting prefers Inbox wakeups** — prompts and subagent monitoring
  avoid fixed sleeps and busy polling, reducing idle work and latency.
- **Learned skills retain progressive disclosure** — they are not injected as
  an automatic router; package/member metadata stays out of model context and
  each turn's catalog snapshot remains frozen.
- **Background Electron renderers remain throttled**, and model-fallback
  progress is localized in English and Chinese.
- **Long memory details no longer widen or clip the panel** — detail, tabs,
  metadata, body text, citations, and footer buttons combine `min-width: 0`,
  horizontal-overflow isolation, and anywhere wrapping. Long URLs, paths,
  identifiers, and larger text remain inside the panel.
- **Project rail “New project” is shortened to “New”** for narrow layouts, and
  README links to the canonical Current Limitations document are restored.

#### Electron Browser views and Linux white-window fix

- **Native Browser viewport converges after PiP restoration** — Electron 35 can
  accept hidden `WebContentsView.setBounds()` without resizing Chromium. The
  old behavior left a PiP-sized page in a full-size shell and exposed a white
  transition surface.
- **Transitions are verified against `window.innerWidth/Height`** — a miss
  triggers a one-pixel geometry pulse, invalidation, and bounded retries, then
  another check after attachment. A final miss emits a concrete warning.
- **The renderer bitmap proxy stays until a real final-size frame exists** so
  the native compositor cannot reveal a white intermediate frame.
- **Linux defaults to software rendering** — the AppImage white-window root
  cause is Chromium's GPU compositor failing on some Wayland/Mesa,
  virtual-GPU, and older-driver stacks. Known-good machines can opt back in
  with `CYRENE_ENABLE_HARDWARE_ACCELERATION=1`.
- **Main-window failures are diagnosable** — `did-fail-load`,
  `render-process-gone`, and `unresponsive` write `cyrene_error.log`; cache
  clearing and `loadURL` are awaited, and failure shows the log location.
- **A new `--desktop-smoke-test` waits for the React root**, requires the launch
  screen to disappear, captures the page, and rejects empty or white surfaces.
  It uses an isolated Electron profile and exits nonzero on failure.
- **Linux CI runs that test against the final AppImage** under `xvfb-run` with
  `--appimage-extract-and-run`, not only against the inner Python binary.
  Because temporary extraction cannot preserve the root-owned SUID sandbox,
  only this isolated CI smoke test uses `--no-sandbox`; normal AppImage launch
  arguments remain unchanged.

#### Linux installers and release path

- **Portable x64 AppImage remains available** across distributions.
- **Debian `.deb` is now actually published** — it was already generated by
  `electron-builder`, but the old artifact step selected only AppImage and
  silently discarded it.
- **New x64 RPM target** supports Fedora, RHEL, CentOS Stream, Rocky Linux, and
  AlmaLinux package-management installation.
- **One required `linux-packages` artifact carries AppImage, deb, and rpm**;
  missing output fails artifact matching or the release gate instead of
  producing an incomplete beta.
- **Bilingual installation docs include copyable commands** for executable
  AppImage, `apt install ./...deb`, and `dnf install ./...rpm`, plus the Linux
  software-rendering override.

#### Contracts, tests, documentation, and beta6 publication

- **CLI protocol coverage** includes NDJSON chunking, authentication, parsing,
  one-shot/interactive modes, chat/project selection, cursor resume,
  attachments, pending questions, Ctrl+C/Ctrl+O, spinner behavior, context,
  config, and bare-`cyrene` startup.
- **Topbar/resource permission coverage** includes MRU merge/cap, pin/remove,
  context menu, cross-chat drafts, Browser copy, keyboard control, durable file
  context, Browser owner/read-only boundaries, deduplication, Library metadata,
  and selected-text Markdown.
- **Memory/config tests** cover verified evidence, language normalization,
  scope, search bounds, citation/history, Workbench archives, foreground
  entities, `0600` keys, missing/invalid keys, and concurrent first creation.
- **Linux packaging tests lock AppImage/deb/rpm targets**, artifact paths, the
  real-AppImage UI test, software-rendering switch, and renderer diagnostics.
- **Bilingual docs now match the implementation** across Architecture, Usage,
  Development, Browser Live View, Limitations, project progress, CLI/topbar
  handoffs, and Design QA; the prototype and comparison image remain as audit
  artifacts.
- **The local prerelease gate passes** — all `1,540` pytest tests, Electron
  `node --check`, `44` App Use Node tests, Ruff across Python files changed
  since beta5, workflow YAML parsing, and `git diff --check` pass. The desktop
  smoke test mounted Workbench, removed the launch screen, captured `2,063,466`
  non-white pixels, and exited cleanly.
- **Every version surface moves to beta6** — Python package/`uv.lock` use
  `0.7.0b6`; Electron package/lock use `0.7.0-beta.6`; README badges, docs
  sidebar, WeChat header, Workbench/PDF cache keys, and contract tests agree.
- **Tag-driven prerelease** — `v0.7.0-beta.6` builds macOS DMG, Windows
  x64/ARM64 installers, and Linux AppImage/deb/rpm, runs frozen and real-desktop
  smoke tests, and uses this section as GitHub prerelease notes.

---

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

This beta5 reissue additionally fixes a direct-Harness authorization mismatch
found in an installed-app conversation. The device list returned
`toolpack:<wire_name>` grants while the first controller implementation
accepted only bare wire names, prepended the prefix again, and rejected grants
that had actually been saved and synchronized. The reissue also makes
compatibility capabilities permanently enabled at the protocol layer, persists
tool-package checkbox defaults, and repairs rounded-corner clipping and Browser
tool naming in Remote Settings.

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
- **Tool-package grant names are normalized compatibly** — `RemoteHarness`
  accepts both bare catalog wire names such as `browser_tools` and full grants
  such as `toolpack:browser_tools`, normalizing them before controller
  authorization and remote payload delivery. A valid grant can no longer turn
  into `toolpack:toolpack:<wire_name>` and be falsely denied.

### Per-device remote tool-package switches

- **Compatibility capabilities are permanently enabled by protocol** — Fixed
  Chat, Run, Task, Approval, and Artifact commands no longer expose individual
  switches and cannot be disabled by ordinary settings updates. Pairing, grant
  updates, received-grant synchronization, and historical-peer migration all
  merge the complete compatibility set.
- **Tool packages use compact checkbox lists** — Pairing invitations and each
  trusted-device editor now use the original compatibility grid's two-column
  checkboxes instead of tall field-row toggles, while retaining localized
  names, accessible labels, and hover descriptions.
- **Rounded-corner clipping is fixed** — Safe grid padding keeps the Code tools
  checkbox in the upper-left and Skill tools checkbox in the lower-left from
  being clipped by the scroll container's rounded `overflow` boundary.
- **Browser naming is consistent** — “Browser automation tools” is shortened
  to “Browser tools” in both local capability settings and remote grants.
- **Authorization is stored per trusted device** — Stable
  `toolpack:<wire_name>` grants flow through signed pairing bundles,
  directional peer grants, encrypted grant synchronization, and audit.
  Changing one controller does not broaden another controller's authority.
- **Pairing package defaults now persist** — A migratable
  `default_tool_packs_json` setting is written immediately and serially when
  checkboxes change, so closing and reopening Settings no longer resets them.
  Stable refs and functional state updates prevent rapid clicks from dropping
  selections through stale React closures.
- **No silent direct-package expansion** — Direct packages still start
  disabled and existing peers receive no new `toolpack:*` grants. Upgrades add
  only the permanently enabled compatibility command set.
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
  prefixed/bare package-name compatibility, required compatibility grants,
  persisted package defaults, 202 success semantics, event waiting, listener
  migration/fallback/discovery/synchronization, and real dual-gateway round
  trips.
- **Workbench regressions expand** — Tests cover direct-package settings,
  checkbox persistence and rounded-edge padding, Browser tool naming,
  localization, fallback-port status, chat-event allowlisting and refresh,
  outside-click behavior, and fingerprint removal.
- **The architecture handoff documents direct Harness control** — Design notes
  now record the preferred invocation chain, local approval boundary, package
  grants, compatibility fallback, event waiting, and live-list semantics.
- **The local beta5 release gate passes** — The locked
  `uv sync --locked --all-extras` environment completes Python `compileall`
  and all 1,474 pytest cases with unhandled thread warnings promoted to errors.
  All 32 WebUI JSX entries rebuild with generated assets matching frontend
  sources, all 44 Electron App Use Node tests pass, modified Python surfaces
  pass Ruff, and `git diff --check` is clean.
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
