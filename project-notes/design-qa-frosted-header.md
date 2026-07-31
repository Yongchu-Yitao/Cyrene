# Design QA — Fading Frosted Conversation Header

- final result: passed
- source visual truth: `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-f64e83c5-cd38-463b-9db7-58552a8ad5df.png`
- readability feedback: `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-67ad243e-c438-4fbd-b075-acb0629e101d.png`
- transparency feedback: `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-842ce0dc-4018-4478-ace7-8d2c01468775.png`
- implementation screenshot: `/Users/syw/Documents/playground/Cyrene/design-qa-frosted-header-final.png`
- focused comparison: `/Users/syw/Documents/playground/Cyrene/design-qa-frosted-header-comparison.png`
- more-transparent implementation: `/Users/syw/Documents/playground/Cyrene/design-qa-frosted-header-more-transparent.png`
- transparency comparison: `/Users/syw/Documents/playground/Cyrene/design-qa-frosted-header-transparency-comparison.png`
- viewport: 1280 × 720 CSS px; browser screenshot normalized to 1280 × 720 px
- transparency comparison normalization: 1252 × 210 source crop resized to 676 × 113; implementation cropped to the same 676 × 113 pixel region
- state: light theme, `介绍自己` selected, long transcript scrolled beneath the header

## Findings

- No remaining P0/P1/P2 findings.
- The header is an absolute overlay, so transcript content can move underneath it.
- The glass is visibly more translucent at the top and middle while still diffusing underlying text into soft tonal shapes.
- Frosting uses a 46 px Gaussian blur with 165% saturation, slight contrast lift, and a late bottom-edge transparency fade.
- Surface coverage now progresses from 66% at the top to 56% through the middle and 32% near the lower fade.
- At large UI text size, the transcript begins 14.8 px below the header in its initial scroll position.

## Required fidelity surfaces

- Fonts and typography: existing Cyrene font family, scale, weights, truncation, and hierarchy are unchanged.
- Spacing and layout rhythm: header controls retain their original alignment; scroll content owns a responsive top inset matching the scaled overlay height.
- Colors and visual tokens: the glass derives from `--wb-main-bg`, preserving light and dark theme compatibility.
- Image quality and assets: no assets changed.
- Copy and content: unchanged.

## Comparison history

1. The initial 18 px blur faded across the whole header too early, allowing underlying text to create a readability-reducing ghost image (P2).
2. A 32 px blur with 98%/96% upper opacity restored readability but made the top feel too opaque and less glass-like (P2).
3. Reduced upper opacity to 88%/82%, increased Gaussian blur to 46 px, and raised saturation to 165% so content remains perceptible without readable ghost text.
4. Post-fix focused comparison shows clear title and metadata text, a more translucent top, and a smooth dissolve into the moving transcript.
5. User requested more transparency; reduced the three surface stops to 78%/70%/46% while preserving the 46 px blur.
6. Post-fix comparison shows more of the moving transcript through the glass without reducing title, metadata, or action readability.
7. User requested another transparency step; reduced the three surface stops to 66%/56%/32%, again preserving the blur and mask.

## Verification

- Frontend build: passed.
- `uv run pytest -q tests/test_workbench_frontend_logic.py`: 150 passed.
- `git diff --check`: passed.
- Runtime checks: computed 66%/56%/32% gradient, 46 px blur, mask, scroll-under behavior, and header control visibility.
- Console errors checked: none.

---

# Design QA — Borderless Workbench Surfaces

- final result: passed
- source visual truth: `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-2dcb2a3a-204f-4e3d-8712-4f2e77371b8b.png`
- composer focus feedback: `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-94fdfcbb-8d79-45af-ab32-ea4da3b75d18.png`
- implementation screenshot: `/Users/syw/Documents/playground/Cyrene/design-qa-borderless-cards-final.png`
- full-view comparison: `/Users/syw/Documents/playground/Cyrene/design-qa-borderless-cards-comparison.png`
- hidden-scrollbar interaction screenshot: `/Users/syw/Documents/playground/Cyrene/design-qa-hidden-scrollbars-final.png`
- state: light theme, `介绍自己` conversation selected and focused, Overview panel visible
- viewport: 1200 × 800 CSS px at device scale factor 1
- source pixels: 2400 × 1600; normalized to 1200 × 800 for comparison
- implementation pixels: 1200 × 800

## Findings

- No remaining P0/P1/P2 findings.
- Conversation cards have no border or focus outline. The active card remains identifiable through its tinted background.
- Conversation cards, Overview cards, search, and composer fields share one restrained two-layer shadow token.
- Search and composer fields remain borderless. Focusing the composer no longer changes the whole container background.
- The conversation list and Overview panel retain native scrolling while hiding their scrollbar tracks and thumbs.

## Required fidelity surfaces

- Fonts and typography: unchanged from the existing Cyrene design system.
- Spacing and layout rhythm: card padding, radius, list gap, columns, and panel widths remain unchanged.
- Colors and visual tokens: existing theme tokens remain in use; all four surface types share `--wbc-control-shadow`.
- Image quality and assets: no image or icon assets changed.
- Copy and content: unchanged.

## Focused evidence

- A separate crop was unnecessary because the full-view comparison keeps the left conversation rail and right overview cards readable.
- Conversation cards, Overview cards, search, and composer controls all declare `border: 0`.
- The shared light-theme shadow is `0 1px 2px rgba(15, 23, 42, 0.028), 0 5px 14px rgba(15, 23, 42, 0.02)`.
- The composer has no `:focus-within` background override, so clicking it does not recolor the container.
- Both scroll containers report `scrollbar-width: none` and a `0px` WebKit scrollbar width.

## Comparison history

1. The first borderless pass removed card borders and shadows, but browser focus still produced a yellow 1 px outline on the selected conversation card (P2).
2. Added explicit focus styling that removes the outline and uses the existing hover/active background tokens.
3. Removed search and composer borders and replaced their focus border with the existing hover background token.
4. Hid the left conversation-list and right Overview-panel scrollbars without changing `overflow-y: auto`.
5. Added one shared, very low-opacity shadow to conversation cards, Overview cards, search, and composer surfaces.
6. Removed the composer focus background change after user review.

## Verification

- Frontend build: passed.
- `uv run pytest -q tests/test_workbench_frontend_logic.py`: 150 passed.
- `git diff --check`: passed.
- Primary interactions tested: selecting the `介绍自己` conversation, focusing both inputs, and independently scrolling the left and right panels.
- Console errors checked: none.

---

# Design QA — Settings / Theme Color

- final result: passed
- viewport: 1152 × 768
- source reference: `/Users/syw/.codex/generated_images/019fa1d2-08ab-7b41-856a-a7af36ba541d/call_Pt1U0T3E2Z9tszVVksHL5nJA.png`
- implementation screenshot: `/Users/syw/Documents/playground/Cyrene/implementation-theme-color.png`
- combined comparison: `/Users/syw/Documents/playground/Cyrene/design-qa-comparison.png`
- implementation:
  - `/Users/syw/Documents/playground/Cyrene/src/webui/frontend/settings-overlay.jsx`
  - `/Users/syw/Documents/playground/Cyrene/src/webui/frontend/workbench.css`
  - `/Users/syw/Documents/playground/Cyrene/src/webui/frontend/workbench-i18n.jsx`

## Validation

- Preset colors use compact, evenly spaced circular controls.
- Selected state uses a white check, one accent ring, and a restrained halo without overlapping adjacent colors.
- The custom-color entry is a single transparent circular control aligned with the presets.
- The custom picker is compact, anchored to the custom control, and remains within the settings panel.
- Hue selection uses a centered vertical color strip with a small white indicator and no native dark outline.
- HEX and native color controls are compact and aligned.
- Current/new previews, reset, cancel, apply, and arbitrary HEX/native color input remain functional.
- Light-theme runtime inspection found no clipped content, broken layout, or unintended dark borders.

## Iteration history

1. Replaced the original preset-only theme color row with presets plus arbitrary color selection.
2. Redesigned selection feedback to a white check, single ring, and soft halo.
3. Reduced the picker footprint and removed heavy native control outlines.
4. Merged the custom controls into one preset-sized transparent circle.
5. Reduced swatch size and spacing to prevent selected-state overlap.
6. Rebuilt the hue control as a centered vertical color strip.
7. Centered the swatch row vertically and reduced the HEX/color field sizes.

## Automated checks

- Frontend JSX build: passed
- `tests/test_webui_consolidation_contract.py`: passed
- `tests/test_workbench_frontend_logic.py`: passed
- Total: 135 passed

---

# Model settings redesign QA

Status: **Passed**

## Visual source

- Original UI: `.codex/audits/model-settings/01-model-overview.jpeg`
- Refactored UI: `.codex/audits/model-settings/04-refactored-overview.jpeg`
- Side-by-side comparison: `.codex/audits/model-settings/qa-comparison.png`

## Acceptance checks

- The settings overlay retains its existing `540px` height.
- The primary model remains immediately editable and is visually identified as the active model.
- Fallback, secondary, and vision configuration are compact collapsible sections with visible status summaries.
- Redundant nested card framing around the primary model has been removed.
- Draft-model inputs use visible labels and remain responsive at narrower widths.
- “Save and apply” is in the normal document flow at the end of the model settings content; it is not sticky or floating.
- Existing light-theme typography, spacing, controls, border treatment, navigation, and accent color remain consistent.

## Verification

- Frontend build: passed.
- Frontend logic tests: `134 passed`.
- `git diff --check`: passed.

---

# OpenAI OAuth model selector QA

Status: **Passed**

## Visual source

- User references: the supplied nested-border, source-menu, hover-state, and reasoning-effort screenshots.
- Final implementation: `.codex/audits/model-settings/09-hover-and-reasoning.png`
- Combined comparison: `.codex/audits/model-settings/qa-oauth-menu-comparison.png`

## Acceptance checks

- The primary-model header owns one compact source switcher for custom models or OpenAI OAuth.
- The source menu has one visible emphasis state at a time; hovering temporarily replaces the selected-row background.
- Clicking outside the source switcher, or pressing Escape, closes the menu.
- Connected OpenAI accounts expose only the selected Codex model's supported reasoning-effort values.
- Reasoning effort is stored with the Codex candidate and sent to the Codex turn as `effort`.
- Codex quota control is independent from the currency budget and is hidden while OpenAI OAuth is disconnected.
- Fallback, secondary, and vision sections use a single outer section border without nested card frames.
- The settings overlay remains `540px` high and the save button stays in normal document flow.

## Verification

- Frontend build: passed.
- Frontend and Codex OAuth tests: `137 passed`.
- Python compilation: passed.
- `git diff --check`: passed.

---

# Account menu Codex quota QA

Status: **Passed**

## Visual source

- User reference: `codex-clipboard-8eaf0179-d462-42f8-bb43-2e2d19fa2c13.png`
- Runtime comparison: account menu captured in the in-app browser at 1280 × 720.

## Acceptance checks

- The existing account-menu actions, spacing, icon alignment, border, radius, and account footer remain unchanged.
- The Codex quota summary appears above the existing actions without overlapping or clipping them.
- The summary is shown only when the first configured model uses `codex_oauth` and the OAuth account is connected.
- A 300-minute window is labeled as the 5-hour quota; a 10080-minute window is labeled as the weekly quota.
- Missing windows are omitted, so the current account correctly shows only the weekly quota.
- Remaining percentage, progress, and reset time reuse the same normalized data as the settings quota panel.
- Codex OAuth is rejected as a fallback, vision, or secondary model.

## Verification

- Frontend JSX build: passed.
- Synthetic 5-hour + weekly parser check: passed.
- Targeted OAuth, onboarding, and frontend tests: `151 passed`.
- In-app browser interaction and visual inspection: passed.
- `git diff --check`: passed.

---

# Composer model picker QA

- Detailed report: `design-qa-model-picker.md`
- Runtime verification: passed.
- Automated checks: `180 passed`.
- final result: passed

---

# Remote sharing settings QA

- Detailed report: `design-qa-remote-sharing.md`
- Runtime verification: passed.
- Frontend build and targeted frontend logic test: passed.
- final result: passed

---

# Conversation top glass alignment QA

- source visual truth: `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-911eace5-5c37-44bd-9283-18be5f0dd31f.png`
- implementation screenshot: `/Users/syw/Documents/playground/Cyrene/design-qa-chat-glass-final.png`
- full-view and focused comparison: `/Users/syw/Documents/playground/Cyrene/design-qa-chat-glass-comparison.png`
- viewport: 1200 × 720 CSS px, device pixel ratio 2; browser output normalized to 1200 × 720 px
- comparison normalization: source resized from 1722 × 290 px to 1200 × 202 px; implementation cropped to the same 1200 × 202 px region
- state: dark theme, conversation rail and transcript header visible

## Findings

- No remaining P0/P1/P2 findings.
- The rail and transcript now share one `.wbc-top-glass` layer spanning both columns.
- The two former pseudo-element glass backgrounds are disabled, so there is only one backdrop-filter surface.
- The vertical rail divider is removed entirely; runtime reports a `0px` border and no `::after` content.
- Runtime geometry reports identical 101.52 px shared-glass and rail-header overlay heights at the active UI scale.

## Required fidelity surfaces

- Fonts and typography: existing Cyrene font family, weights, scale, truncation, and hierarchy remain unchanged.
- Spacing and layout rhythm: the left title row, search field, and inset were compacted to fit the same top-glass height as the transcript header.
- Colors and visual tokens: the unified glass derives from `--wb-main-bg`; the existing gradient, blur, saturation, contrast, and mask are preserved.
- Image quality and assets: no production image or icon assets changed.
- Copy and content: unchanged.

## Comparison history

1. The initial implementation only matched the two overlay heights; separate rail and header pseudo-elements still rendered two glass surfaces (P2).
2. Replaced both surfaces with one page-level glass layer and initially moved the rail divider below it.
3. User requested a fully borderless join, so the remaining divider was removed from the rail structure.
4. Post-fix combined comparison shows a continuous surface with no vertical seam above or below the glass.

## Verification

- Frontend build: passed.
- `uv run pytest tests/test_workbench_frontend_logic.py -q`: 156 passed.
- `git diff --check`: passed.
- Search input focus/value interaction: passed.
- Console errors checked: none.
- final result: passed

---

# Inline conversation image QA

- source visual truth:
  - `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-1ef71bbf-1b09-4b72-ad4d-0bc482a7aebc.png`
  - `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-4f5d09b0-48ce-42bd-87c9-ff5624da3066.png`
  - `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-dcb0ba95-2830-41cb-b1c8-119c87dbc71b.png`
  - `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-39f1523b-d003-47b9-be64-983b5172ba32.png`
  - `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-0fafb035-b4d7-4784-90fd-6559206eac08.png`
- implementation screenshot: `/Users/syw/Documents/playground/Cyrene/design-qa-inline-image-final.png`
- full-view comparison: `/Users/syw/Documents/playground/Cyrene/design-qa-inline-image-comparison.png`
- focused action comparison: `/Users/syw/Documents/playground/Cyrene/design-qa-inline-image-focused-comparison.png`
- viewport: 1280 × 720 CSS px
- source pixels: 1192 × 1082; implementation pixels: 1280 × 720
- density normalization: source full view normalized to 720 px height; focused button comparison normalized to 98 px height
- state: dark theme, generated-image conversation open, image card visible

## Findings

- No remaining P0/P1/P2 findings.
- Generated images now render directly in the transcript as a compact 280 px card.
- The image fills a square fully rounded preview with `object-fit: cover`, removing the previous side bars.
- The compact, borderless footer uses the same `--wb-card-bg` surface and control shadow as the message input, and shows the filename plus exactly two actions.
- Both action icons use the same 24 px view box, 1.8 px stroke, round caps/joins, button size, and color treatment.
- The complete card remains draggable and keeps the existing Cyrene resource drag payload.

## Required fidelity surfaces

- Fonts and typography: existing Cyrene type family and message hierarchy are unchanged; the filename uses the existing attachment weight and truncation behavior.
- Spacing and layout rhythm: the final card is 280 px wide with a 34 px footer, 12 px image corner radii, 28 px action buttons, and consistent 7 px action spacing.
- Colors and visual tokens: the footer matches the composer through `--wb-card-bg` and `--wbc-control-shadow`; actions are borderless with the composer hover color.
- Image quality and asset fidelity: the original generated image URL is displayed directly; cover cropping removes side bars without introducing a replacement asset.
- Copy and content: the filename is preserved; action labels remain localized through existing translation keys.

## Comparison history

1. Initial implementation rendered the image at 532 px wide and retained a dark preview background (P2: too large and visually heavy).
2. Reduced the card to 380 px, added square rounded cover cropping, and replaced the text arrow with a matching stroke icon.
3. User requested a further size reduction; the final implementation is 280 px wide.
4. User requested a shorter, borderless lower area; removed the enclosing card border and gave the image four fully rounded corners.
5. Matched the footer to the composer surface and reduced it to 34 px with 28 px actions.
6. Cleared inherited button padding, line-height, gap, and sizing so the download SVG is strictly centered.
7. Final full-view and focused comparisons show a compact rounded image, no side bars, a composer-matched footer, and centered matched action icons.

## Verification

- Frontend build: passed.
- `uv run pytest tests/test_workbench_frontend_logic.py tests/test_workbench_artifact_download.py -q`: 164 passed.
- `git diff --check`: passed.
- Clicking the image opens the right-side viewer: passed.
- Draggable card contract (`draggable="true"`): passed.
- Both footer actions present with matching icon geometry: passed.
- Console errors checked: none.
- final result: passed
