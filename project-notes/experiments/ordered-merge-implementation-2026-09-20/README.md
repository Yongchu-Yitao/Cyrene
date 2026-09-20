# Ordered merge production verification

`baseline-runtime-timeline.jsx` is the exact pre-change source, including existing workspace changes. `benchmark.mjs` compares it with the current production source in the same JS realm. It discards three warm-up rounds and alternates AB/BA for 21 retained samples per case. It asserts projection output equality before measuring. These are projection timings, not whole-agent latency.

Reproduce from the repository root:

```sh
node --test src/cyrene/workbench/webui/frontend/features/chat/ordered-merge.test.mjs
node project-notes/experiments/ordered-merge-implementation-2026-09-20/benchmark.mjs
node project-notes/experiments/ordered-merge-implementation-2026-09-20/audit_build.mjs /tmp/cyrene-merge-new-audit
.venv/bin/python project-notes/experiments/ordered-merge-implementation-2026-09-20/audit_server.py /tmp/cyrene-merge-new-audit
```

Open the printed localhost URL. Click **Run full conversation comparison**, wait for completion, then **Measure projection A/B**. Run `validate_browser.py` after comparison completes. The build freezes all frontend modules once, then builds baseline and candidate from that identical snapshot. `--rebuild` updates the experimental harness but deliberately retains that source snapshot.

The fixture uses real production WbcMain, messages, navigator, composer, CSS and animation definitions. Backend responses, clipboard and action callbacks are synthetic. It does not launch the full Electron shell or exercise actual model/tool APIs. Two WbcMain instances test dual-pane rendering, not the outer workspace split controller.

Focus preservation is checked synchronously across a real stream delta, then the textarea is blurred before waiting, to avoid competing iframe focus or unrelated desktop typing. Edit mounting is flushed synchronously before blur. Locale changes trigger a root chat-prop refresh. Animations are paused and sampled at five progress points through the Web Animations API; this checks keyframes, timing and computed values, not real-time FPS. Streaming and retry animations use unmodified product code. Natural wall-clock offsets in earlier experimental runs, iframe focus contention and missing fixture fields are retained in the raw log rather than silently discarded.

The raw log is compressed after the server is stopped. `browser-summary.json` identifies the final validated run. Earlier errors/differences are harness-development records, not successful checks. `source-manifest.json` identifies the source snapshot. Python failure logs concern concurrently edited session-plugin architecture and are retained explicitly.
