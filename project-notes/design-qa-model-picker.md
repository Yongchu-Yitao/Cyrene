# Model picker design QA

- Source visual truth:
  - `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-188f6a10-294a-4727-b89b-20fec3ed0f83.png`
  - `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-2dc1e907-5e1b-4c74-bc46-09aca98b1362.png`
- Viewport: 1152 × 768 desktop application window.
- Source pixels: menu 484 × 126; button 263 × 76. The references are high-density crops, so they were normalized to the implementation's CSS-scale region before comparison.
- Implementation pixels: menu crop 260 × 104; button crop 230 × 58 at device scale 1.
- State: dark theme; composer idle; model picker closed, root menu open, model submenu open, and reasoning-effort submenu open.

## Full-view comparison evidence

The implementation was rendered in the running Cyrene Electron application. The control sits in the existing composer action row, uses the product's dark theme tokens, remains clear of the send control, and opens upward without clipping the composer or side panels.

## Focused region comparison evidence

The focused comparison confirmed a compact rounded panel with two aligned rows, strong left labels, muted selected values, and right chevrons. The closed pill control shows the model name, optional reasoning label, and a down chevron in a rounded neutral control.

## Findings

- Fonts and typography: passed. Existing Cyrene UI font, weight hierarchy, truncation, and muted secondary labels are preserved.
- Spacing and layout rhythm: passed. Menu width was reduced from 360px to 260px after the first review; row height and padding now match the visual density of the source.
- Colors and visual tokens: passed. The source's neutral dark gray is intentionally mapped to Cyrene's existing blue-gray card, hover, border, and text tokens.
- Image quality and asset fidelity: passed. No raster assets are required; icons use the existing Workbench icon language and remain sharp.
- Copy and content: passed. Chinese labels match the reference. Model options come from configured candidates, and reasoning options come from the selected model's reported `supportedReasoningEfforts`.
- Interaction and accessibility: passed. The trigger exposes popup and expanded state; root, model, and reasoning panels are keyboard-dismissible and click-outside dismissible.

## Comparison history

1. Initial implementation used a 360px menu. Finding: P2, visually too wide at CSS scale. Fix: reduced to 260px and tightened row grid and padding.
2. Initial reasoning menu exposed every globally known value. Finding: P1, unsupported choices could be shown for the active model. Fix: load the Codex model catalog and render only the active model's declared supported efforts; models without declared reasoning controls do not show the row. Post-fix evidence: the running `gpt-5.3-codex-spark` menu showed exactly low, medium, high, and extra-high.

## Verification

- Primary interactions tested: open/close picker, open model submenu, list every configured model, switch pending model, open reasoning submenu, and restore the current chat state.
- Relevant automated regression suite: 180 passed.
- Frontend production build: passed.

final result: passed
