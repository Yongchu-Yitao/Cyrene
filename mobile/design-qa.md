# Terminal mobile design QA

**Source visual truth**

- `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-f982302b-a5fa-477e-9529-a1cdb27cb82c.png`
- `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-f940b900-d4a5-4fa9-898d-9b05ce45a47a.png`
- `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-952765fc-ca83-4659-8097-61831ae4e14e.png`
- `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-589de75b-c8b7-4e5f-b5a1-9e6ca35bc363.png`
- `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-9e5b5567-d984-4a6f-a88b-7982e3c7d7e4.png`
- `/var/folders/zm/qgh_rgw903j0b9t01yg2l0mc0000gn/T/codex-clipboard-284e7cc2-be92-4d9c-90a6-2678652f8f83.png`

**Implementation evidence**

- Full screen: `/Users/syw/Documents/playground/Cyrene-mobile/design-qa-implementation.png`
- Project menu: `/Users/syw/Documents/playground/Cyrene-mobile/design-qa-project-menu.png`
- Prompt comparison: `/Users/syw/Documents/playground/Cyrene-mobile/design-qa-prompt-comparison.png`
- Header comparison: `/Users/syw/Documents/playground/Cyrene-mobile/design-qa-header-comparison.png`
- Device viewport: 1080 x 2400 px at 420 dpi (approximately 411 x 914 dp), portrait.
- Source pixels: 692 x 246, 692 x 78, 700 x 166, 307 x 96, 192 x 74, and 700 x 186.
- Implementation pixels: 1080 x 2400. Focused prompt evidence was normalized to 700 px width; header evidence was cropped and normalized to comparable scale.
- State: connected remote terminal, switched from `信用之巅` to `澳大利亚旅游攻略`, long unsent command entered, dark theme.

**Findings**

- No remaining P0/P1/P2 fidelity issues.
- Fonts and typography: app and source both use a clear monospace terminal face; prompt, live input, cursor, and output are white with consistent line height.
- Spacing and layout rhythm: the editable prompt now sits inside the terminal content region; the shortcut strip remains the persistent bottom terminal control. The hint row and separate bottom composer are gone.
- Colors and visual tokens: terminal background, chrome, divider, white foreground, muted controls, green connected state, and red Ctrl-C state match the supplied references.
- Image quality and asset fidelity: no raster assets are required for this terminal UI; existing Material icons remain sharp and consistent.
- Copy and content: the keyboard hint and `Shell · .` subtitle are removed. The header shows the selected project name with a compact switch affordance.
- Interaction: the project dropdown opened, listed available remote projects, and successfully switched the terminal to `澳大利亚旅游攻略`. Long live input wrapped across multiple lines without inserting a newline into the command. IME Send, command history, paste, tab, interrupt, clear, and exit handlers remain connected.

**Comparison history**

1. P1: prompt/input was below the shortcut bar and could be covered by the keyboard. Fixed by moving the editable prompt into the output list and applying IME bottom insets to the terminal layout.
2. P2: prompt was green, the hint row remained, and the placeholder could wrap vertically. Fixed by using white prompt/input/cursor, removing the hint and placeholder, and removing the redundant send icon.
3. P2: the header showed a Shell path and was not interactive. Fixed by keeping only the project name and adding the project dropdown.
4. P2: prompt and input were separate layout children, preventing natural full-width wrapping. Fixed with a prompt visual transformation so prompt and live input share one wrapping text flow.

**Implementation checklist**

- [x] Prompt and live input are inside terminal content.
- [x] Prompt/input/cursor are white.
- [x] Keyboard hint is removed.
- [x] Header contains only the project name plus project-switch affordance.
- [x] Project switching works and reconnects the terminal state.
- [x] Long live commands wrap at the viewport edge.
- [x] Shortcut strip consumes IME inset and remains above a docked keyboard.
- [x] Debug Kotlin compilation and debug unit tests pass.

**Follow-up polish**

- None required for the requested scope.

final result: passed
