# Cyrene personal assistant demo

An isolated interactive React/Vite prototype. The original left sidebar is retained; the center and right side follow the reviewed design direction. Production files are untouched.

## Preview

`npm run dev -- --host 127.0.0.1 --port 4173 --strictPort`

## Included interactions

- Today overview, task filters and task details
- Explicit simulated browser connection, pause/resume and completion
- File previews and Markdown downloads
- Schedule create/edit/complete/delete (fixed demo date: 2026-11-06)
- Knowledge search, memory create/edit/enable/delete
- Chat suggestions, local canned responses, attachment metadata and task creation
- Global search (Cmd/Ctrl+K), theme switch, collapsible panels
- Local browser storage and reset in demo settings

No model, browser integration, external calendar, or production backend is called. File attachment content is not uploaded or read; only metadata is used in this demo. Document contents are illustrative. Data is scoped to the browser origin and localStorage key `cyrene-assistant-demo-v1`.

## Build

`npm run build`
