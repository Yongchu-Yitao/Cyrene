# Full performance investigation, 2026-09-20

All product imports use the current checkout. Run from the repository root. Scripts create disposable synthetic data, do not call real model APIs, and do not change product source. `/tmp` on the measured host is tmpfs; the `disk` probe explicitly uses a temporary directory on the repository's ext4 filesystem.

```sh
.venv/bin/python project-notes/experiments/performance-audit-2026-09-20/probe.py
.venv/bin/python project-notes/experiments/performance-audit-2026-09-20/probe.py disk
.venv/bin/python project-notes/experiments/performance-audit-2026-09-20/logging_probe.py
.venv/bin/python project-notes/experiments/performance-audit-2026-09-20/terminal_profile.py
.venv/bin/python project-notes/experiments/performance-audit-2026-09-20/agent_matrix.py
.venv/bin/python project-notes/experiments/performance-audit-2026-09-20/agent_matrix.py profile
node project-notes/experiments/performance-audit-2026-09-20/frontend_pipeline.mjs
node project-notes/experiments/performance-audit-2026-09-20/frontend_remaining_paths.mjs
```

Run these sequentially for performance measurements. `terminal_profile.py` records three unprofiled samples and a separate profiled diagnostic sample. Agent `profile` runs only the 40-turn case and collects input-preparation call profiles; profiling affects timing and nested cumulative entries must not be added together. The normal agent matrix has one repetition per configuration in this round.

The optional connection variants and partial index exist only inside `probe.py` temporary fixtures. They are not validated production patches. Logging's guarded wrapper likewise exists only in the experiment. The cache's 50 ms miss is intentionally synthetic: it demonstrates blocking mechanism, not real miss latency.

Browser measurements use the Browser skill against the printed localhost URL:

```sh
node project-notes/experiments/performance-audit-2026-09-20/build_browser.mjs
.venv/bin/python project-notes/experiments/performance-audit-2026-09-20/browser_server.py
```

Click **Run comprehensive browser test**, wait for PASS, and preserve `browser-results.json` as `browser-timings.json`. Then run:

```sh
node project-notes/experiments/performance-audit-2026-09-20/build_browser.mjs --count-renders
```

Reload the page and click the button again. This run adds `assistantRenderCalls` to each update. The results overwrite `browser-results.json`; keep the first timing artifact separately. The experimental bundle inserts a counter into WbcAssistantMessage, never the product file. On uninstrumented runs the counter is zero and should not be interpreted as a render count. The server exposes synthetic test fixtures and public app assets only.

The browser page mounts production transcript/message components, not the full Workbench/Electron. The i18n, voice and host boundaries are synthetic. It measures synchronous React commits and forced layout, not foreground frame rate. Validate result counts, final text and `checks` before interpreting timing.

The full research report is `project-notes/performance-full-audit-2026-09-20.zh-CN.md`. `audit-manifest.json` records source hashes and current source drift from the first Python probe. Historical benchmark comparisons are descriptive, not controlled cross-version A/B experiments.
