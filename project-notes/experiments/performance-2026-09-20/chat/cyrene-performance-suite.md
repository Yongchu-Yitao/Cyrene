# Cyrene performance benchmark suite

Generated: `2026-09-20T12:26:07.093902+00:00`

| Group | Scenario | Parallel | Rounds | Primary metric | Result | Cache progression | Ideal cache | Quality |
|---|---|---:|---:|---|---:|---|---:|---|
| chat | single_heavy_run | 2 | 3 | run_latency_p95_ms | 82.266 ms | 0.00% → 7.77% → 36.14% | 36.14% | pass |
| chat | dense_concurrency | 24 | 3 | run_latency_p95_ms | 531.668 ms | 0.00% → 7.77% → 36.14% | 36.14% | pass |
| chat | multi_tool_storm | 12 | 3 | run_latency_p95_ms | 966.036 ms | 0.00% → 7.77% → 36.14% | 36.14% | pass |

> Quality failures are reported independently from timing regressions. The suite performs no real LLM or network calls.
> Aggregate ideal cache hit rate: 36.14%; it is derived only from deterministic fixture reuse.
