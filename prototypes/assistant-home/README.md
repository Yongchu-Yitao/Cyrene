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

## Intelligent UI scenarios (v2)

Open `http://127.0.0.1:4173/#intelligent`, `#research`, or `#travel`.

- Research: describe a topic → choose preloaded official-document summaries or add your own source → enter requirements → generate a report/comparison → change it via chat or the isolated report's buttons → export Markdown.
- Travel: describe Hangzhou, Shanghai, Chengdu, or Kyoto and 1–5 days → interactive Leaflet/OpenStreetMap map → select/remove/re-add a place → update days, pace, and budget → chat using the current selection → export.
- A local deterministic demo runner assembles versioned UI schemas. It does **not** call an LLM, search live sources, or generate unrestricted code. The report HTML is assembled into an isolated iframe with an allowlisted message bridge.
- The Vite development middleware supplies authoritative per-demo-session state, revision-checked/idempotent actions, and SSE updates. State is persisted in ignored `.demo-state/workspaces.json`; browser localStorage stores only the demo session ID for the new scenarios.
- Run through `npm run dev` for both the frontend and the local API. The static production bundle alone does not include this demo API. Existing Sites worker scaffolding is untouched; deployment would need a server adapter.
- All coordinates are approximate seeded places. Prices and times are illustrative, in CNY including Kyoto; intercity transport is excluded. Dashed lines show visit order, not navigable routes. Opening hours, weather, reservations and live pricing are not queried.
- OpenStreetMap attribution stays visible. Tiles load only for the current map viewport; there is no offline tile download or prefetch. `VITE_MAP_TILE_URL` can override the standard tile endpoint for local testing; retain required attribution for any substituted provider.

Focused validation: `node --test tests/intelligent-engine.test.mjs`.

## Task-generated cards and user pins

The local demo runner now emits structured card descriptions from task state. Travel produces an itinerary and, when relevant, a budget or travel tips. Research progresses from evidence selection to result and risk cards. The right rail separates **pinned by you** from **generated for the task**.

Use the pin button beside the center map, itinerary/budget, sources, report or travel notes to keep that content on the right. Agent cards also have pin buttons. Repeated pinning is deduplicated. Cards can be collapsed, unpinned or hidden; the plus menu restores hidden Agent cards and adds the personal agenda.

Try “生成出行提示卡片” or “生成地图卡片” in a travel task. The runner creates the requested supported card from that task's data. This is still a deterministic demo, not an LLM or arbitrary generated code.

Pins are keyed by task identity and content type. They update from their original task while it is active; when a new task replaces it, the old pin retains its last snapshot. In-progress regeneration keeps the last completed report until the replacement is ready. Archived pins open their own saved contents and cannot redirect to a different task.

Pin state, hidden/collapsed preferences and snapshots are now persisted with the local API session (`.demo-state/workspaces.json`) and updated over SSE. This supersedes the previous `cyrene-resident-cards-v1` localStorage-only card catalog. The legacy catalog preference is no longer read. Browser localStorage still stores the session ID.

Focused checks: `node --test tests/intelligent-engine.test.mjs tests/resident-cards.test.mjs`.
