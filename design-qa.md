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
