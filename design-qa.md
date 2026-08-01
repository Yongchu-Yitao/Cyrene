# Design QA

## Comparison target

- Source visual truth: `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-14ed3d86-23aa-41b3-b3c6-f0d960b11d6e.png` — fixed bottom strip obscuring the end of the conversation list.
- Browser-rendered implementation: `/Users/syw/Documents/playground/Cyrene/implementation-chat-list-bottom-bar-removed.png`.
- Combined comparison inputs:
  - `/Users/syw/Documents/playground/Cyrene/design-qa-bottom-bar-full-comparison.png`
  - `/Users/syw/Documents/playground/Cyrene/design-qa-bottom-bar-focused-comparison.png`

## Viewport and normalization

- Browser viewport: 1280 × 720 CSS px, device scale factor 1.
- Implementation capture: 1280 × 720 px.
- Source capture: 454 × 62 px at approximately 2× density.
- The implementation’s 230 × 31 CSS-pixel rail-bottom crop was normalized to 454 × 62 px for a direct focused comparison.
- State: light theme, populated and scrollable conversation list at the viewport bottom.

## Findings

- No remaining P0/P1/P2 findings.
- Fonts and typography: conversation-title and timestamp typography are unchanged.
- Spacing and layout rhythm: removed only the rail container’s fixed 14 px bottom padding. The list’s internal 8 px scroll-content padding remains, preserving comfortable spacing without creating a fixed obstruction.
- Colors and visual tokens: glass material, feathering, card colors, shadows, and active state remain unchanged.
- Image quality and assets: no images or icons were modified.
- Copy and content: no copy changed.
- Bottom reach: the conversation list now ends at the same y-coordinate as the rail (`720px`), leaving a measured fixed bottom gap of `0px`.

## Interaction and runtime checks

- Verified computed rail bottom padding is `0px`.
- Verified the list remains independently scrollable and retains `8px` internal bottom padding.
- Verified rail and list bounding boxes both end at y = 720 in the browser viewport.
- Browser warning/error log check returned no entries.

## Comparison history

- Earlier pass: P2 fixed 14 px rail padding created an opaque horizontal strip and visually clipped the final conversation card.
- Fix: changed `.wbc-rail` padding from `0 12px 14px` to `0 12px` while preserving the list’s scrollable content padding.
- Post-fix evidence: `implementation-chat-list-bottom-bar-removed.png` and both combined comparison images. No actionable P0/P1/P2 findings remain.

## Focused comparison evidence

- Overall rendered state: `design-qa-bottom-bar-full-comparison.png`.
- Exact rail-bottom region before and after: `design-qa-bottom-bar-focused-comparison.png`.

## Follow-up polish

- None required for the requested scope.

archived result: passed

---

# Design QA — chat sidebar centering

## Comparison target

- Source visual truth: `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-bd72287d-6f97-4d31-a4e7-d9e8031a1428.png`.
- Browser-rendered open state: `/Users/syw/Documents/playground/Cyrene/implementation-chat-sidebar-open.png`.
- Browser-rendered collapsed state: `/Users/syw/Documents/playground/Cyrene/implementation-chat-sidebar-closed-centered.png`.
- Browser-rendered reopened state: `/Users/syw/Documents/playground/Cyrene/implementation-chat-sidebar-reopened.png`.
- Combined comparison input: `/Users/syw/Documents/playground/Cyrene/design-qa-chat-sidebar-centering-comparison.png`.

## Viewport and normalization

- Browser viewport: 1920 × 1280 CSS px, light theme, populated conversation.
- Browser captures: 1726 × 1280 px; source capture: 2414 × 1602 px.
- The source and implementation have different host-window crops, so exact layout behavior was validated with browser geometry in CSS pixels rather than inferred from raster scaling.
- State sequence: right sidebar open → collapsed → reopened.

## Findings

- No remaining P0/P1/P2 findings.
- Fonts and typography: unchanged from the existing Cyrene conversation UI.
- Spacing and layout rhythm: the 1276 px conversation stage and composer expand by a controlled 96 px to 1372 px. The resulting lane leaves equal 127 px margins inside the 1626 px main area, closely matching the user-marked target region.
- Colors and visual tokens: unchanged; existing glass, surfaces, shadows, and semantic colors are preserved.
- Image quality and assets: no assets were added, replaced, scaled, or recropped.
- Copy and content: unchanged.
- Motion: the sidebar grid track now animates continuously between 350 px and 0 px while both content lanes share a 420 ms overshooting cubic-bezier transition for coordinated translation and stretch. Reduced-motion users receive no transition.

## Interaction and runtime checks

- Sidebar collapse and restore controls were both exercised in the rendered app.
- Open geometry: main, transcript, and composer width = 1276 px; transcript/composer x = 294 px.
- Collapsed geometry: main width = 1626 px; transcript/composer width = 1372 px; transcript/composer x = 421 px, leaving 127 px on each side.
- Reopened geometry returned exactly to the original 1276 px width and x = 294 px position.
- Close-animation sample: the side track measured 0.77 px before reaching 0 px; transcript/composer measured 1369.08 px before reaching 1372 px.
- Open-animation sample: the side track measured 348.66 px before reaching 350 px; transcript/composer measured 1280.14 px before reaching 1276 px. Its right edge remained within the animated content lane instead of jumping to the 1920 px window edge.
- Browser warning/error log check returned no entries.
- Automated frontend suite: 164 tests passed.

## Comparison history

- Initial issue: removing the right sidebar let the middle grid track grow by 350 px, stretching messages and the composer across the reclaimed area.
- Fix: animate the sidebar's grid track instead of removing it with `display: none`; use 96 px of the reclaimed space to widen the lane, centre the remaining width symmetrically, and animate width plus translation with a lightly overshooting easing curve.
- Post-fix evidence: the collapsed screenshot and measured geometry show the wider lane centred with equal 127 px margins. The reopened state returns to the original geometry with no drift.

## Focused comparison evidence

- The combined comparison shows the source visual, implementation with the sidebar open, and implementation after collapse in one artifact.
- No additional crop was needed because the affected message and composer edges are clearly visible in the full-height comparison, and the browser geometry provides exact edge measurements.

## Follow-up polish

- None required for the requested scope.

final result: passed
