# Task Board Refinement Design QA

- Source visual truth:
  - `/Users/syw/Library/Containers/com.tencent.qq/Data/Library/Application Support/QQ/nt_qq_a5187743c563bd88b13838b2a26d3126/nt_data/Pic/2026-07/Ori/5054ef1ef00aa6ea63a515cf3567c9ad.png`
  - `/Users/syw/Library/Application Support/PixPin/Temp/PixPin_2026-07-11_01-15-00.png`
- Implementation screenshots:
  - `/tmp/cyrene-board-qa-2/board-dark-final.jpg`
  - `/tmp/cyrene-board-qa-2/board-hover-final.jpg`
- Combined comparisons:
  - `/tmp/cyrene-board-qa-2/comparison-dark-final.jpg`
  - `/tmp/cyrene-board-qa-2/card-hover-comparison.jpg`
- Viewport: `1280 × 720`
- State: dark theme, task board visible, first task card hovered

**Full-view comparison evidence**

The implementation uses a blue-grey `#111827` board canvas instead of the previous near-black background. The board retains five status columns, but the header toolbar now contains only sorting and new-task controls. Task cards no longer render an assignee footer.

**Focused region comparison evidence**

The focused card comparison shows the overflow menu aligned with the first title line, matching the supplied compact-card reference. The hover capture shows the first card's top border fully visible with no upward translation into the column's clipping boundary.

**Findings**

- No actionable P0, P1, or P2 differences remain for the requested refinements.
- P3: real task titles and summaries wrap differently from the small reference crop; truncation and hierarchy remain consistent.

**Required fidelity surfaces**

- Fonts and typography: existing Workbench typography is retained; title, summary, status, steps, and time preserve the intended hierarchy.
- Spacing and layout rhythm: removing the footer shortens cards, releases unused right padding, and aligns the menu within the title grid.
- Colors and visual tokens: the dark board canvas is blue-grey rather than pure black; semantic stage and status colors are unchanged.
- Image quality and asset fidelity: no new raster assets were required; existing menu icons are reused.
- Copy and content: the toolbar exposes only `排序` and `新建任务`; task content remains source-backed.

**Interaction and runtime checks**

- Dark board background computed as `rgb(17, 24, 39)`.
- Toolbar DOM contains exactly `排序：默认` and `新建任务`.
- Card assignee/footer elements are absent.
- Card menu is inside the title row and remains keyboard accessible.
- Hovered first-card border is visibly intact.
- Initialization detail receives the same `onBackToBoard` callback and renders `返回看板`.
- Frontend build completed successfully.
- `tests/test_workbench_frontend_logic.py`: 68 passed.
- Browser console errors: none observed during the board capture.

**Comparison history**

1. Previous state used a near-black board background, translated cards upward on hover, rendered a Cyrene footer, and showed four toolbar controls.
2. The background token, hover behavior, card structure, menu alignment, toolbar, and initialization navigation were updated.
3. Final full-board and hovered-card captures show no remaining P0/P1/P2 issues.

**Implementation checklist**

- [x] Blue-grey dark background.
- [x] Top hover border remains visible.
- [x] Initialization detail includes back-to-board.
- [x] Cyrene footer removed.
- [x] Menu aligned with title first line.
- [x] Toolbar reduced to sort and new task.

**Follow-up polish**

- None required for this refinement.

final result: passed
