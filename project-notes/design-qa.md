# Branch Tree Design QA

[English](design-qa.md) · [简体中文](design-qa.zh-CN.md)

> This is a historical design-evidence log. Implementation status and
> architecture ownership are documented in
> [the completed architecture handoff](COMPLETED-refactor-handoff.md).

## Comparison target

- Source visual truth:
  - `/Users/syw/.codex/generated_images/019f6b63-5399-71b0-90d7-d2fbb40ee6c3/exec-5f508677-2a8e-437d-9902-46c50d083f59.png`
  - `/Users/syw/Library/Application Support/PixPin/Temp/PixPin_2026-07-17_00-22-21.png`
  - `/Users/syw/Library/Application Support/PixPin/Temp/PixPin_2026-07-17_00-25-30.png`
  - `/Users/syw/Library/Application Support/PixPin/Temp/PixPin_2026-07-17_00-25-51.png`
- Implementation screenshot: `/Users/syw/.codex/visualizations/2026/07/16/019f6b63-5399-71b0-90d7-d2fbb40ee6c3/branch-tree-qa/implementation-final.png`
- Viewport: Cyrene Electron window at 1152 × 768.
- State: dark theme, branch tab selected, four lineage rows, fork chat selected.
- Full-view comparison evidence: `/Users/syw/.codex/visualizations/2026/07/16/019f6b63-5399-71b0-90d7-d2fbb40ee6c3/branch-tree-qa/comparison-final.png`
- Focused-region evidence: `/Users/syw/.codex/visualizations/2026/07/16/019f6b63-5399-71b0-90d7-d2fbb40ee6c3/branch-tree-qa/implementation-branch-crop.png`

## Findings

- No actionable P0, P1, or P2 mismatches remain.
- Fonts and typography: existing Workbench font stack, compact weights, one-line ellipsis, and label hierarchy remain consistent with the surrounding panel. The final Electron capture is lower density than the PixPin crop, so the combined comparison includes expected resampling softness rather than an in-app font defect.
- Spacing and layout rhythm: 56px row rhythm and 44px bordered targets create the requested vertical breathing room. The deepest graph lane now sits about 16px from the bordered target, matching the corrected request for more separation.
- Colors and visual tokens: the main lane uses source-control blue, the fork lane and current label use the Workbench accent, and button surfaces reuse existing border/card tokens.
- Image quality and asset fidelity: this component contains no raster artwork, logos, illustrations, or non-standard icons. Native CSS borders and graph geometry are appropriate for this interactive source-control visualization.
- Copy and content: `起点`, `分支`, `最新`, message previews, and `当前` are present and truncated without wrapping.
- Interaction states: hover and keyboard focus are visible on the bordered target; active branch state remains announced with `aria-current` and `当前`.

## Comparison history

1. Initial implementation replaced the hint and large selected card with a compact Git-style graph. Build and frontend tests passed.
2. User feedback requested larger row spacing and button boundaries. Rows were increased to 56px, content targets to 44px, and subtle hover/focus borders were added. Post-fix Electron evidence showed the requested bordered rows.
3. User feedback identified a small, incomplete fork curve and requested a higher branch origin. The fork became a 14 × 24px elliptical turn beginning near the top of the fork row, with a continuous vertical join.
4. The final correction requested more distance between the graph and buttons. The rail allowance was increased from 18px to 30px after the deepest lane. The final combined comparison confirms the wider gap, larger curve, higher origin, and complete connection.

## Verification

- Primary interactions tested: opened the branch tab; selected the root branch row and observed the active/current state move; reopened the fork chat and confirmed the branch tab state.
- Console/runtime errors checked: no branch-tree JavaScript errors were emitted. Electron development output contained unrelated Chromium DevTools Autofill protocol warnings and authenticated source-map 401 noise.
- Automated verification: `npm run build`; `python -m pytest tests/test_workbench_frontend_logic.py -q` (96 passed); `git diff --check`.

## Follow-up polish

- P3: none required for this accepted state.

## Implementation checklist

- [x] Compact blue/magenta source-control lanes.
- [x] Larger vertical spacing and bordered row targets.
- [x] Complete, larger fork curve with a higher origin.
- [x] Wider graph-to-button separation.
- [x] Hover, focus, truncation, current-state, and click behavior retained.

final result: passed

---

# Literature Library Existing-Document Compatibility QA

## Comparison target

- Source visual truth: `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-4132a1b2-107c-43e7-89e8-58874927ff19.png` (1536 × 1024 px).
- Implementation screenshot: `/Users/syw/Documents/playground/Cyrene/docs/research-workbench-report/library-existing-documents-selected-1536x1024.png` (1536 × 1024 px).
- Full-view comparison evidence: `/Users/syw/Documents/playground/Cyrene/docs/research-workbench-report/library-existing-documents-selected-comparison.png` (3072 × 1024 px, source and implementation side by side).
- Viewport: 1536 × 1024 CSS px, device scale factor 1, dark theme.
- State: real current project, 26 existing `kb_documents`, first existing document selected with its indexed summary and original attachment visible.

## Findings

- No actionable P0, P1, or P2 visual mismatch was introduced by the compatibility change.
- Fonts and typography: the requested top heading now reads `知识库` at the same size, weight, and baseline as the former `全部文献` heading.
- Spacing and layout rhythm: the mapped records continue using the existing dense table, selected-row workspace, and right inspector proportions; no header, toolbar, or pane dimensions changed.
- Colors and visual tokens: the bridge adds no new visual tokens. Existing PDF/document marks, selected-row green, dark surfaces, dividers, and tags remain consistent with the approved implementation.
- Image quality and asset fidelity: existing document attachments reuse their original file types and native Workbench icon treatment. No substitute raster, CSS drawing, or placeholder asset was added.
- Copy and content: the table now truthfully shows the active project's existing documents. The visible records come from the project's own `kb_documents`; no screenshot sample records were seeded.
- Data isolation: the bridge creates only metadata references and attachment links inside the same `kb_<project>.db`. It does not copy files or expose documents from another project.

## Full-view and focused comparison evidence

- The same-size combined image verifies the unchanged major-region proportions, dense table, lower metadata workspace, and right inspector after selecting an existing document.
- The heading, first selected row, attachment count, indexed summary, source label, and citation/relations cards are legible in the full-size implementation screenshot, so an additional crop was not required.

## Comparison history

1. The first implementation displayed only structured `library_items`, leaving older/current `kb_documents` invisible in the new UI.
2. A project-local compatibility bridge now maps unlinked knowledge documents to structured items and references their existing attachment/index records.
3. Browser QA confirmed 26 real current-project documents, the `知识库` heading, selected detail, original attachment access, indexed summary, tags, and zero console errors.

## Verification

- Primary interactions tested in the in-app Browser: reload the current project, inspect the 26-document list, select an existing document, and verify its detail workspace and right inspector.
- Browser console errors: none.
- Automated verification: focused compatibility/library tests, Ruff, JSX build, and `git diff --check`.

## Implementation checklist

- [x] Map existing project knowledge documents without copying files.
- [x] Preserve project isolation and idempotency.
- [x] Avoid duplicates for documents already linked to structured library items.
- [x] Change the top all-items heading to `知识库`.
- [x] Verify real records, selected detail, visual fidelity, and console output.

final result: passed

---

# Browser Floating Window Design QA — Interaction Polish

## Comparison target

- Source visual truth:
  - `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-7c332ac5-4107-468e-b70a-1c1ceab6fd18.png`
  - `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-ac912d21-833f-4d6e-bfad-1359169c512b.png`
  - `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-ced1d7df-fe36-4d7c-8007-eb8aad0a0c7c.png`
  - `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-ad537a95-1b3b-4954-bbfe-cfab04c2ec8e.png`
  - `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-20d6bcbb-5d96-4182-8650-24b42947f7cb.png`
  - `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-8e552ad9-0d40-48c8-b274-5fc7d72231b3.png`
  - `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-5f139147-3658-4da4-9898-653f36c1a33e.png`
  - `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-55ddfc8d-70eb-41cd-911f-f7b82f0b7a62.png`
- Implementation screenshot: unavailable for the revised drag, maximize, and minimized states; the user is performing the Electron interaction check.
- Viewport: Cyrene Electron desktop window; exact post-restart viewport depends on the user's current window size.
- State: active native browser tab shown in the conversation floating window, plus maximized and minimized variants.
- Full-view comparison evidence: blocked until the revised Electron states are captured at the same viewport.
- Focused-region comparison evidence: blocked until the SVG controls and minimized capsule are captured after restart.

## Findings

- [P1] Revised runtime drag-follow evidence is still required.
  - Location: floating browser drag and resize interaction.
  - Evidence: the latest user screenshot showed the live `WebContentsView` remaining at its previous coordinates and spilling outside the moved shell. The revised implementation hides the native view during drag/resize, displays a captured page proxy inside the shell, and reattaches the live view only after pointer release; a static source review cannot prove the temporal transition.
  - Impact: the browser content must remain visually clipped to, and move with, the floating shell.
  - Fix: user validates the live Electron interaction; capture the revised result if further timing adjustment is needed.
- [P1] Fullscreen-to-PiP compositor recovery requires renewed runtime evidence.
  - Location: native browser mode transition in Electron on macOS.
  - Evidence: user validation showed that returning from fullscreen could leave the native page white. The active `WebContentsView` is now kept attached but hidden across both directions, resized while hidden, made visible at the final bounds, and explicitly invalidated immediately and again after 80ms.
  - Impact: a white page makes the browser unusable after restoring the floating window.
  - Fix: validate PiP → fullscreen → PiP in the restarted Electron process and capture the revised states.
- Fonts and typography: the title area now uses a compact green `浏览器` pill followed by the page title, in both floating and maximized states. Post-restart visual evidence is pending.
- Spacing and layout rhythm: maximized browser chrome remains 58px tall, matching `.workbench-topbar`. The default floating browser is now 280 × 215px (half of the previous 560 × 430px), remains resizable from 240 × 180px up to the explicit conversation content region, and the minimized control remains a single 40px green pill. The movement region reuses the transcript's 18px top, 22px inline, and 8px bottom inset tokens, matching the red-box edges without clipping the floating shadow. Visual evidence is pending.
- Colors and visual tokens: the standalone green status dot was removed. The semantic running state is carried by the bordered green `浏览器` pill; the minimized restore action reuses the Workbench accent surface.
- Image quality and asset fidelity: the live browser remains a native `WebContentsView`; during manipulation its own captured PNG is used as a temporary proxy. The fullscreen restore action uses Google's Apache-2.0 Material `close_fullscreen` rounded asset; the minimized pill no longer contains a secondary icon.
- Copy and content: the persistent `浏览器` pill replaces the ambiguous dot. The minimized state removes the oversized `打开` label while preserving localized restore labels and tooltips.

## Comparison history

1. User evidence showed the native page trailing the floating window shell, text action buttons, a shorter maximized bar, and an under-polished minimized control.
2. The shell position and size are now applied synchronously during pointer movement, followed by a same-frame browser layout event. The Electron manager now updates an attached native view immediately instead of waiting for a 50ms timer.
3. Text actions were replaced with maximize, minimize, and restore SVG icons; the maximized bar was aligned to the 58px Workbench topbar; the minimized capsule was rebuilt around the supplied `状态点 + 浏览器 + 打开` composition.
4. Build and automated logic checks passed. Live temporal and visual comparison remains pending user validation.
5. User validation exposed a white native browser surface after the immediate bounds update. The cause was an unsafe Electron 35 `WebContentsView.setBounds` cadence. Bounds are now deduplicated in the renderer and coalesced to a stable 32ms cadence in the main process; the Electron app was restarted for revalidation.
6. The next user capture showed the native page still occupying its old rectangle while the shell moved. Drag and resize now use pointer capture plus a screenshot proxy: the native view is detached for the interaction, the proxy follows and clips inside the shell, and the live view is restored at the final bounds on release.
7. The standalone green dot was replaced by a green `浏览器` pill in floating and maximized headers. The minimized state was rebuilt as a smaller pill-and-icon control without the oversized `打开` block.
8. User feedback identified mismatched corner geometry, a shadow clipped by the composer boundary, and a restore glyph that read as Copy. Outer/inner radii were aligned to 20px/16px, the control was inset 24px from the lower boundary, and the minimized action changed to the existing four-corner expand icon.
9. The next reference clarified that minimized mode should contain only the clickable green `浏览器` pill. The secondary action was removed, the pill was inset to 32px, the lower shadow was tightened, and fullscreen restore adopted the rounded Material `close_fullscreen` icon.
10. User validation then exposed white content during both fullscreen entry and fullscreen-to-PiP restore. Mode changes now detach the visual surface through the existing preview transition, while Electron keeps the active native view attached-but-hidden, applies final bounds while hidden, shows it once, and forces two full repaints.
11. Follow-up validation found a short white interval after fullscreen-to-PiP restore. The renderer had removed its bitmap proxy before Electron completed the first frame at the smaller bounds. The transition IPC now stays pending while Electron pre-renders the final-size page with `capturePage`; the proxy remains mounted until that readiness promise resolves, then the live view is revealed and repainted.
12. User requested a smaller default floating browser. Its initial width and height were each halved from 560 × 430px to 280 × 215px; the resize floor was reduced to 240 × 180px while the maximum remains the full bounded conversation region.
13. The latest reference clarified that the movement boundary must match the inset conversation rectangle rather than the entire thread stage. A dedicated absolute movement region now shares the transcript's inset tokens, and all drag, resize, initial placement, and host-resize clamping resolve against that one DOM rectangle.

## Verification

- Automated verification: `npm run build`; `pytest -q tests/test_workbench_frontend_logic.py` (100 passed); `pytest -q tests/test_browser_session.py` (58 passed); `node --check electron/main.js`; `node --check electron/preload.js`; `git diff --check`.
- Runtime: Electron development app restarted successfully; backend is listening on `127.0.0.1:4242`.
- Primary interactions pending: drag, resize, maximize, restore, minimize, and reopen in the latest Electron process.
- Console/runtime errors checked: startup completed; the known unsigned development keychain warning does not block the app.

## Implementation checklist

- [x] Replace native-view dragging with a shell-bound screenshot proxy and restore the live view on release.
- [x] Replace text window actions with SVG icons and accessible labels.
- [x] Match maximized browser bar height to the 58px Workbench topbar.
- [x] Replace the green status dot with a green `浏览器` pill in floating and maximized states.
- [x] Restyle the minimized browser control as a compact pill-and-icon capsule.
- [x] Align minimized outer/inner corner radii and preserve the full lower shadow.
- [x] Reduce minimized mode to one clickable green `浏览器` pill.
- [x] Replace the fullscreen Copy-like restore glyph with the rounded Material close-fullscreen icon.
- [x] Keep the native view attached-but-hidden through both mode directions and force repaint after restore.
- [x] Keep the bitmap proxy visible until Electron confirms a rendered frame at the final bounds.
- [x] Halve the default PiP dimensions while preserving bounded user resizing.
- [x] Constrain PiP drag and resize to the exact inset conversation region shown by the red box.
- [ ] Capture and compare the revised Electron interaction states after user validation.

final result: blocked

---

# Launch Screen Design QA

## Comparison target

- Source visual truth: `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-a852e1b0-05c7-42cd-9810-8b22cbf35252.png` (1487 × 1058 px), normalized to 1440 × 1024 for comparison.
- Implementation screenshot: `/Users/syw/.codex/visualizations/2026/07/22/019f88a6-a228-7693-bc9f-1f94e0c734b9/launch-screen-qa/qa-launch-light.png`.
- Full-view comparison: `/Users/syw/.codex/visualizations/2026/07/22/019f88a6-a228-7693-bc9f-1f94e0c734b9/launch-screen-qa/qa-launch-comparison.png`.
- Focused-region comparison: `/Users/syw/.codex/visualizations/2026/07/22/019f88a6-a228-7693-bc9f-1f94e0c734b9/launch-screen-qa/qa-launch-focus-comparison.png`.
- Viewport: 1440 × 1024 CSS px, device scale factor 1; implementation screenshot is 1440 × 1024 px.
- State: first-load launch overlay, light theme. Dark-theme styles were inspected during the same reload at full opacity before removal.

## Findings

- No actionable P0, P1, or P2 mismatches remain.
- Fonts and typography: the horizontal `Cyrene` wordmark uses the Workbench Manrope/Inter/system stack at 34px and weight 720. The smaller scale than the generated reference is intentional, following the user's explicit request that the logo not be too large.
- Spacing and layout rhythm: the 72 × 72px logo, 20px horizontal gap, and optically centered flex row preserve scheme 3's compact lockup with ample negative space.
- Colors and visual tokens: light uses Workbench surface `#f5f6f8` with `#171c22`; browser inspection confirmed dark uses `#1a2230` with `#edf2f8` at opacity 1.
- Image quality and asset fidelity: the existing project `logo-mark.png` is used directly at native aspect ratio. It remains sharp with no crop, generated substitute, or transparency halo.
- Copy and content: only the exact title `Cyrene` is visible; there is no progress text, subtitle, skeleton, or other application content.
- Loading behavior: the static overlay renders before the React bundles. After the Workbench signals readiness it continues tracking all first-screen fetches, requires a 300ms network-quiet window, and only then removes itself with a 140ms fade. A 20-second safety deadline prevents a failed request from trapping the user. The overlay was absent after readiness and no console errors were present.

## Comparison history

1. The first implementation used a vertical logo-and-title stack.
2. The user selected scheme 3. The implementation changed to the exact horizontal lockup while retaining the requested smaller logo and Workbench theme adaptation.
3. Browser QA captured the launch state at 1440 × 1024, verified dark computed styles, and confirmed the overlay is removed after initial content readiness.
4. Follow-up testing found that nested chat/context requests could begin after the project list was ready. The launch gate now waits for all first-screen fetches to settle and remain quiet for 300ms before revealing the Workbench.

## Verification

- Primary flow tested: opened a fresh Cyrene instance, observed the launch-only state, waited for initial data, and confirmed the onboarding/workbench content replaced the launch overlay.
- Themes tested: light launch screenshot; dark launch computed style and post-load dark state.
- Console errors checked: none.
- Automated verification: `npm run build`; `pytest -q tests/test_launch_screen.py tests/test_workbench_frontend_logic.py` (109 passed); `git diff --check`.

## Implementation checklist

- [x] Scheme 3 horizontal logo/title composition.
- [x] Small 72px logo and concise 34px title.
- [x] Workbench light/dark surface and text colors.
- [x] Static pre-React rendering with no content flash.
- [x] Removal only after initial content loading settles.
- [x] Reduced-motion support and accessible status label.

final result: passed

---

# Literature Library Design QA

## Comparison target

- Source visual truth: `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-4132a1b2-107c-43e7-89e8-58874927ff19.png` (1536 × 1024 px).
- Normalized source: `/Users/syw/Documents/playground/Cyrene/docs/research-workbench-report/library-ui-reference-1313x768.png`.
- Final dark implementation: `/Users/syw/Documents/playground/Cyrene/docs/research-workbench-report/library-ui-dark-final-1313x768.png`.
- Full-view comparison: `/Users/syw/Documents/playground/Cyrene/docs/research-workbench-report/library-ui-dark-comparison-final.png`.
- Final light implementation: `/Users/syw/Documents/playground/Cyrene/docs/research-workbench-report/library-ui-light-final-1313x768.png`.
- Final General settings: `/Users/syw/Documents/playground/Cyrene/docs/research-workbench-report/library-settings-final-1313x768.png`.
- Citation alignment source: `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-f4d589e5-ac49-4dc9-aeaa-f7ef715c2d67.png`.
- Final citation alignment capture: `/Users/syw/Documents/playground/Cyrene/docs/research-workbench-report/library-citation-aligned-1152x768.jpg`.
- Focused citation comparison: `/Users/syw/Documents/playground/Cyrene/docs/research-workbench-report/library-citation-alignment-comparison.png`.
- Citation-border source: `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-f69e743e-ac04-41d6-b716-d6d024249b28.png`.
- Final citation-border capture: `/Users/syw/Documents/playground/Cyrene/docs/research-workbench-report/library-citation-accent-final-1152x768.png`.
- Citation-border comparison: `/Users/syw/Documents/playground/Cyrene/docs/research-workbench-report/library-citation-accent-comparison.png`.
- Independent-detail source: `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-c2ef03ab-4433-4727-ba00-b658a98d0024.png`.
- Final independent-detail capture: `/Users/syw/Documents/playground/Cyrene/docs/research-workbench-report/library-info-split-final-1152x768.png`.
- Independent-detail comparison: `/Users/syw/Documents/playground/Cyrene/docs/research-workbench-report/library-info-split-comparison.png`.
- Sidebar-width source: `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-213ee9e1-de8a-459a-bcb7-e02c925b6c2b.png`.
- Final sidebar capture: `/Users/syw/Documents/playground/Cyrene/docs/research-workbench-report/library-sidebar-final-1152x768.png`.
- Sidebar-width comparison: `/Users/syw/Documents/playground/Cyrene/docs/research-workbench-report/library-sidebar-width-comparison.png`.
- Selected-state source: `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-af9be6f3-aea4-4070-9227-a5c67df992f2.png`.
- Final selected-state capture: `/Users/syw/Documents/playground/Cyrene/docs/research-workbench-report/library-selection-final-1152x768.png`.
- Selected-state comparison: `/Users/syw/Documents/playground/Cyrene/docs/research-workbench-report/library-selection-comparison.png`.
- List-scroll source: `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-3e13a162-92b0-47d5-ab8f-c09a01d187ee.png`.
- Final list-scroll capture: `/Users/syw/Documents/playground/Cyrene/docs/research-workbench-report/library-list-scroll-final-1152x768.png`.
- List-scroll comparison: `/Users/syw/Documents/playground/Cyrene/docs/research-workbench-report/library-list-scroll-comparison.png`.
- Media-content source: `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-aa810695-9afb-47fc-a6a7-8c326b091f9e.png`.
- Final image-content capture: `/Users/syw/Documents/playground/Cyrene/docs/research-workbench-report/library-content-image-final-1152x768.png`.
- Image-content comparison: `/Users/syw/Documents/playground/Cyrene/docs/research-workbench-report/library-content-image-comparison.png`.
- Markdown-content source: `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-d0313a01-e21f-4db4-8c4f-6b72e987ffd8.png`.
- Final Markdown-content capture: `/Users/syw/Documents/playground/Cyrene/docs/research-workbench-report/library-content-markdown-final-1152x768.png`.
- Markdown-content comparison: `/Users/syw/Documents/playground/Cyrene/docs/research-workbench-report/library-content-markdown-comparison.png`.
- Viewport: Cyrene Electron at 1313 × 768 px. The dark selected-item state is used for source comparison; the light state verifies theme correctness.
- Data state: the current project's 26 real knowledge documents. Reference sample records were intentionally not copied.

## Findings

- No actionable P0, P1, or P2 visual mismatch remains.
- Fonts and typography: the implementation keeps the Workbench font stack with a readable 13–24px hierarchy rather than shrinking type to force the layout. Table labels, metadata, actions, tabs, and inspector copy remain legible at the Electron viewport.
- Spacing and layout rhythm: the layout now tracks the source's compact multi-pane structure. The library rail is 196px and the inspector is 260px at this viewport, leaving the center list fluid. The center table has no horizontal scroll, and the selected-item workspace uses compact left-aligned tabs plus an overflow action.
- Header treatment: the all-items main title is intentionally omitted per the latest direction, leaving only the item count. The Zotero toolbar action and the horizontal separators under the left and main headers were removed.
- Colors and visual tokens: the deep navy palette is scoped to dark theme only. Light theme uses neutral Workbench surfaces rather than pink/theme-color washes. Sidebar, table, and detail tags use transparent outlined chips instead of solid gray pills; magenta is limited to primary/action and active indicators.
- Sidebar behavior: `我的知识库`, `我的收藏夹`, and `标签云` use disclosure controls. Collapse/expand was exercised in Electron and preserves the current scope.
- Settings consistency: Zotero and Embedding use the same flat section and field-row treatment as the rest of General settings; the extra bordered card styling was removed. Zotero import is available only in General settings and imports into the active project.
- Content fidelity and isolation: no reference sample content is present. Counts, folders, tags, attachments, extracted text, and citations come from the active project's isolated knowledge database.
- Bibliographic semantics: only an explicit source-metadata abstract is displayed as the document abstract. Generated descriptions and indexed body text remain available to retrieval and the Content tab without being mislabeled as a source abstract.
- Detail workspace: the lower panel sizes to its content, can be resized vertically by the user, is capped at 50% of the center pane, and scrolls internally only when metadata requires more space. A neutral divider separates the metadata and summary/card columns; both columns scroll independently. Complete metadata editing now lives at the bottom of the right inspector, while the delete action sits at the far right of the inspector tabs.
- List scrolling: the table header remains fixed while the table body owns an independent vertical scroll viewport. With the lower detail workspace open, the body can still reach and fully expose the final project item without the detail workspace covering it. Card view uses the same independent vertical-scroll behavior.
- Sidebar and selection treatment: tag-cloud typography is one step smaller, count badges share the tag-card outline treatment, and the sidebar is slightly wider. Sidebar choices, table rows, and cards use the same soft accent fill and thin accent outline without the previous orange browser-focus ring or a left-edge stripe.
- Citation controls: the heading and copy dropdown share one centered baseline, the trigger has no theme-colored focus/active ring, and the menu exposes plain-text and BibTeX copy actions. The citation item uses a complete one-pixel theme-color outline with no separate left stripe.
- Content rendering: the right `内容` tab now prioritizes the original attachment over indexed/OCR text. Images render inline, video and audio use native playable controls, PDFs render inline, and unsupported binary files receive an explicit open-file fallback. Markdown is parsed into headings, paragraphs, lists, code, tables, links, and images, then sanitized before insertion.
- Content navigation: switching the selected item or entering the `内容` tab resets the inspector to its top, so a newly selected document never opens halfway through the previous document's content.
- Image quality and assets: the screen uses existing Workbench/Electron icons and original document types; no generated raster replacements, CSS-drawn sample art, or external image assets were introduced.
- Copy: the visible toolbar contains only `添加条目`, `导入`, and `导出`; the all-items state shows `共 26 个知识`; Zotero import copy identifies the active project.

## Full-view and focused comparison evidence

- The normalized source and final dark Electron capture are combined at identical 1313 × 768 dimensions. The comparison verifies the global rail, classification rail, table, lower workspace, and inspector proportions.
- The final light capture verifies that the library remains neutral in light mode and that the tag cloud no longer uses gray fill.
- The General settings capture verifies the removal of the integration-card shell. Accessibility inspection confirmed the active-project Zotero import action plus the complete Embedding provider, URL, key, model, dimensions, test, and save controls.

## Comparison history

1. The complete project-isolated library, document compatibility bridge, Zotero integration, Embedding configuration, and Agent retrieval tools were implemented and tested without seeding reference data.
2. The first visual pass was too spacious, used smaller type, and allowed the center list to scroll horizontally. Pane widths, type hierarchy, table columns, and overflow behavior were corrected.
3. The first dark-source adaptation incorrectly forced dark styling in light mode. The navy palette was scoped to `data-theme="dark"` and light surfaces were restored.
4. User feedback removed theme-color washes from the classification rail and inspector, added collapsible classification groups, and removed the all-items main title and the header separators.
5. The Zotero toolbar modal was removed. General settings now owns the explicit active-project import action.
6. Final polish replaced gray tag pills with transparent outlined chips, equalized workspace-tab spacing, and flattened Zotero/Embedding groups to match standard General settings rows.
7. The selected-item workspace was rebuilt around complete editable bibliographic metadata, source-only abstracts, count-free equal-width tabs, and a content-sized panel capped at half-height.
8. The citation heading inherited an 8px bottom margin that placed it below the copy trigger. The scoped heading margin was reset, and the trigger focus/active border was forced back to the neutral line token. The focused before/after comparison now shows a shared vertical centerline.
9. The lower tabs were compacted to the source's left-aligned rhythm, the workspace gained a persistent vertical resize handle, metadata editing moved to the right inspector, and deletion moved to the inspector tab row.
10. The citation item now has a full theme-color outline, while relation, attachment, citation, note, and tag content retain readable text sizes.
11. The information workspace was divided into independently scrollable metadata and summary/card columns, with a compact responsive single-column fallback.
12. The classification rail was widened, tag-cloud type was reduced, counts adopted the tag-card outline, and all selected states were aligned to the Workbench soft-accent treatment without orange focus outlines.
13. The literature table body was separated from the fixed header and lower detail workspace. Electron wheel-scroll verification reached the final item while the detail workspace remained open and stationary.
14. The `内容` tab was extended from plain indexed text to attachment-aware presentation. Real project images now display inline; video/audio use native playback controls; PDFs embed inline; unsupported files remain openable.
15. Markdown attachments now render as sanitized semantic HTML instead of showing source markers such as `#`, `##`, and `**`. Electron comparison evidence confirms the heading hierarchy and the automatic scroll-to-top behavior.

## Verification

- Primary Electron interactions tested with Computer Use: reload the latest build, select a real document, open the lower workspace and inspector, scroll the independent table body to its final item, collapse and expand the classification group, switch dark/light/system themes, open General settings, verify the active-project Zotero import control, display a real project image in `内容`, and render a real Markdown document after switching items.
- Runtime: the Electron application remains running on `127.0.0.1:4242`; the final asset and API requests returned 200. No new frontend exception or application crash was observed.
- Automated verification: `npm run build`; focused library, settings, and Agent-tool tests (20 passed); the raw-video endpoint test confirms inline `video/mp4` delivery for native playback and seeking; Ruff (all checks passed); `git diff --check`.
- Theme handoff: the application was restored to `跟随系统`, currently rendering light.

## Implementation checklist

- [x] Project-isolated API client and reset behavior.
- [x] Collections/tags rail, dense table and card view, inspector, and right detail panel.
- [x] Search, filters, sorting, pagination, loading, empty, error, and responsive states.
- [x] Manual add, bibliography/PDF import, JSON export, notes, tags, citations, and Zotero sync.
- [x] Zotero import only in General settings; no knowledge-page Zotero button.
- [x] Zotero and Embedding controls use standard General settings styling.
- [x] Agent-facing structured library listing and hybrid evidence retrieval.
- [x] Real existing knowledge documents appear in the active project.
- [x] Collapsible classification groups, neutral outlined tags, compact workspace tabs, and no center-table horizontal scroll.
- [x] Source-only abstracts, complete right-inspector metadata editing, and a resizable lower detail workspace capped at 50%.
- [x] Plain-text/BibTeX copy dropdown with aligned heading, neutral focus/active treatment, and a full theme-color citation outline.
- [x] Independent left/right detail scrolling with a neutral divider and compact narrow-screen fallback.
- [x] Wider classification rail, smaller tag cloud, outlined count badges, and consistent soft-accent selected states.
- [x] Theme-correct light and dark states with final same-viewport comparison evidence.
- [x] Refined list state: ordinary rows are continuous and divider-free; only the selected row has a rounded outline.
- [x] Sidebar categories use a restrained 520 weight with 500-weight counts, while tag-cloud labels and counts remain regular 400 weight.
- [x] Final sidebar-weight evidence: `docs/research-workbench-report/library-sidebar-weight-final-1152x768.png`.
- [x] Final tag-cloud weight comparison: `docs/research-workbench-report/library-sidebar-weight-comparison.png`.
- [x] Fixed table header with an independently scrollable list body that reaches the final item while the detail workspace is open.
- [x] Final list-scroll evidence: `docs/research-workbench-report/library-list-scroll-final-1152x768.png`.
- [x] Final list-scroll comparison: `docs/research-workbench-report/library-list-scroll-comparison.png`.
- [x] Attachment-aware content rendering for images, playable video/audio, inline PDFs, and unsupported-file fallback.
- [x] Sanitized Markdown rendering with semantic typography and automatic inspector scroll reset.
- [x] Final image-content comparison: `docs/research-workbench-report/library-content-image-comparison.png`.
- [x] Final Markdown-content comparison: `docs/research-workbench-report/library-content-markdown-comparison.png`.

final result: passed
