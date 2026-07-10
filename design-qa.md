# Task Board Design QA

- Source visual truth: `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-7d326822-23ec-41dc-bddd-1415910c5a9a.png`
- Implementation screenshot: `/tmp/cyrene-board-qa/implementation-light.png`
- Combined comparison: `/tmp/cyrene-board-qa/comparison-light.png`
- Viewport: `1635 × 962`
- State: workbench task entry, project selected, board visible, light theme, all tasks filter

**Full-view comparison evidence**

The source and implementation were combined side by side at their original 1635 × 962 resolution. Both show a five-stage horizontal Kanban, compact task cards, colored semantic columns, an empty blocked state, toolbar controls, and a completed-task strip. The implementation intentionally retains Cyrene's existing collapsed project rail and top navigation.

**Focused region comparison evidence**

A separate crop was not required: at the original desktop resolution, the column headers, task-card typography, status chips, card metadata, empty states, and completed strip were all readable in the full comparison. The live browser DOM was also checked for each named column and control.

**Findings**

- No actionable P0, P1, or P2 visual mismatches remain.
- P3, app shell: the implementation reserves 64 px for Cyrene's collapsed project rail, while the reference is a standalone full-width board. This is intentional because the board is integrated into the existing workbench navigation.
- P3, content density: card heights vary with real task summaries and step counts rather than the reference's fixed demo content. Truncation keeps the same compact hierarchy.

**Required fidelity surfaces**

- Fonts and typography: existing Cyrene system font, weights, line heights, two-line summaries, and small metadata remain consistent with the workbench design system; no clipping was observed.
- Spacing and layout rhythm: five equal columns, 12 px gutters, compact cards, matched radii, subtle elevation, and the completed strip preserve the source composition. Horizontal overflow is available below narrower desktop widths.
- Colors and visual tokens: planning, executing, review, completed, and blocked stages use existing workbench semantic tokens in light and dark themes.
- Image quality and asset fidelity: the reference contains no required product imagery. Existing workbench icons are reused; no placeholder image assets were introduced.
- Copy and content: Chinese stage labels, status text, filter/sort controls, empty states, and the back-to-board action are localized, while card content comes from real project tasks.

**Interaction and runtime checks**

- Card click opens the selected task detail.
- Detail view has no task-list column and includes a unique `返回看板` button.
- Returning restores the board without losing the selected project.
- Filter reduced the board to the completed task set and reset successfully.
- Sort toggled between default and recently updated ordering.
- Board status synchronization uses event-triggered refresh plus a visibility-aware four-second summary poll.
- Browser console errors checked: none.
- Frontend build completed and `tests/test_workbench_frontend_logic.py` passed (68 tests).

**Comparison history**

1. Initial browser capture found a P2 presentation issue: after mouse navigation, the collapsed project rail retained focus and expanded over the first board column.
2. The rail navigation handler was updated to release pointer focus while preserving keyboard focus behavior.
3. Post-fix evidence at `/tmp/cyrene-board-qa/implementation-v3.png` shows the rail collapsed to 64 px and all five columns fully visible.
4. A light-theme capture was compared against the light reference; no P0/P1/P2 issues remained.

**Implementation checklist**

- [x] Board is the default task entry.
- [x] Five status groups render from live session status.
- [x] Card selection opens task detail.
- [x] Detail removes the task list.
- [x] Back-to-board action works.
- [x] Responsive horizontal board behavior is present.
- [x] Light/dark themes and Chinese/English labels are supported.

**Follow-up polish**

- P3: add drag-and-drop only if task-state mutations are later designed to support manual stage changes; current stage movement remains status-driven as requested.

final result: passed
