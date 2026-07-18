# Branch Tree Design QA

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
