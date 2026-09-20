# Cyrene performance benchmark suite

Generated: `2026-09-20T12:29:23.861713+00:00`

| Group | Scenario | Parallel | Rounds | Primary metric | Result | Cache progression | Ideal cache | Quality |
|---|---|---:|---:|---|---:|---|---:|---|
| search | simplexng_search_and_fetch | 6 | 3 | median_ms | 213.888 ms | 0.00% → 50.00% → 66.67% | 66.67% | pass |

> Quality failures are reported independently from timing regressions. The suite performs no real LLM or network calls.
> Aggregate ideal cache hit rate: 66.67%; it is derived only from deterministic fixture reuse.
