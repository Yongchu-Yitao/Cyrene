# Workbench live reasoning spinner QA

**Evidence**

- Source visual truth (spinner missing after the reasoning text scrolled): `/Users/syw/Library/Application Support/PixPin/Temp/PixPin_2026-07-15_12-24-54.png`
- Actual Cyrene implementation screenshot: `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/com.openai.sky.CUAService/Electron Screenshot 2026-07-15 at 12.28.53 PM.jpeg`
- Focused implementation crop: `/tmp/cyrene-reasoning-spinner-qa/fixed.png`
- Combined source/fixed comparison: `/tmp/cyrene-reasoning-spinner-qa/source-vs-fixed.png`
- Viewport: `1152 × 768` for the actual Cyrene render.
- State: live reasoning detail open, long reasoning text scrolled to the final line.

**Findings**

- No remaining P0/P1/P2 findings.
- The spinner was originally inside the same scrolling container as the reasoning text, so auto-scroll moved it out of view.
- The card now keeps the existing spinner as a fixed flex sibling and scrolls only `.wbc-thinking-detail-text`.

**Measured result**

- Locked card height: `43.59375px`.
- Spinner size: `12 × 12px`.
- Spinner animation: `wb-spin`.
- Text scroll position: `83px` of `105px`, with a `22px` viewport (fully scrolled to the bottom).
- Spinner visible after the text reached the bottom: `true`.

**Required fidelity surfaces**

- Fonts and typography: existing Workbench font family, size, line height, and reasoning copy behavior are unchanged.
- Spacing and layout rhythm: the locked card retains its measured `43.59375px` height; the spinner remains in the original left position while text scrolls independently.
- Colors and visual tokens: the existing green spinner and card tokens are reused.
- Image quality and asset fidelity: no new raster or vector assets were introduced.
- Copy and content: live reasoning text and activity labels are unchanged.

**Interaction and verification**

- Frontend build completed successfully.
- `tests/test_workbench_frontend_logic.py`: 86 passed.
- Regression assertions cover the retained spinner, the fixed outer detail region, and the independently scrollable text region.
- Actual-app QA confirmed the spinner remains visible and animated when the reasoning text is at the bottom.

**Comparison history**

1. Before fix: auto-scroll applied to the whole reasoning detail row and scrolled the left spinner away.
2. Fix: moved the scrolling ref and `overflow-y: auto` to the reasoning text span only.
3. After fix: the spinner stays visible, uses the original `wb-spin` animation, and the card height remains unchanged.

**Implementation checklist**

- [x] Keep the left spinner visible in reasoning detail.
- [x] Preserve the existing spinner animation and visual token.
- [x] Scroll only the live reasoning text.
- [x] Preserve the locked card height.
- [x] Add regression assertions.
- [x] Build, test, and verify in the actual Cyrene UI.

**Follow-up polish**

- None required for this regression.

final result: passed
