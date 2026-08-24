# Cyrene performance benchmark suite

The suite measures Cyrene-owned latency without including external model or
network time. All model replies, tools and remote search responses are fixed
fixtures; databases and files are created in temporary directories.

Every group executes at least two sessions/workers in parallel, and every
session/worker completes at least two ordered turns/rounds. A turn in the
conversation benchmarks reuses the same session and carries the previous
messages forward. `--repeats` only controls independent timing samples; it is
not counted as conversation history or cache reuse.

## Coverage

| Group | What it measures |
|---|---|
| `agent` | Pure chat, tool loops, long history, large tool results and background contention |
| `chat` | Real chat run manager, NDJSON stream, SQLite event log and dense multi-tool concurrency |
| `search` | Search-and-fetch orchestration with deterministic delayed network boundaries |
| `features` | Event fanout, terminal output, knowledge FTS/read-write concurrency, database initialization, scheduled-task CRUD and file hashing |

The terminal also has a platform benchmark outside the deterministic suite. It
launches real children through POSIX PTY or Windows ConPTY, covers plain text,
ANSI compilation logs, and TUI repaint traffic with and without a loopback
WebSocket subscriber, and reports event-loop delay, RSS, process disk writes,
segmented-scrollback write amplification, and WebSocket delivery latency.

## Run

```bash
uv run python -m cyrene.observability.performance_suite \
  --repeats 3 \
  --output-dir output/performance
```

Run selected groups:

```bash
uv run python -m cyrene.observability.performance_suite \
  --groups chat,features \
  --output-dir output/performance
```

Run the real terminal matrix on each target platform (the default writes 24
MiB per case so it crosses the 16 MiB retention limit):

```bash
uv run python -m cyrene.observability.terminal_performance_benchmark \
  --json output/performance/terminal-real.json
```

The JSON records `ptyBackend` as `posix_pty` or `conpty`, so macOS/Linux and
Windows results can be compared without maintaining different benchmark code.

The command writes `cyrene-performance-suite.json` for automation and
`cyrene-performance-suite.md` for review. Report schema version 2 records the
effective parallelism and round count and uses input-unit cache accounting.

## Ideal cache hit rate

Every benchmark case records an `ideal_cache` object. It describes only the
theoretical reuse available inside one parallel, multi-round workload:

```text
hit_rate = reusable input units / total input units
```

For agent and chat cases, reusable units are the longest exact context prefix
seen earlier in the same mock conversation. For search and local-feature cases,
they are repeated deterministic operation keys in later rounds of the same
worker. The first round is therefore cold, while later rounds can reuse only
what actually repeats. This metric does not inspect a runtime cache, provider
prompt cache, credentials, network response, or LLM call. Independent timing
repeats never increase it. Case-level metrics are aggregated by input units in
the unified suite.

Each case also records a `series` array. Every point contains the current
turn/round `input_units`, `hit_units`, `hit_rate` and their cumulative forms.
Conversation cases identify the dimension as `turn`; other cases use `round`.
Reports render cumulative values as `0.00% → 20.00% → 35.00%`. The reusable
prefix normally grows with conversation history, but rates are not forced to be
monotonic because a turn may introduce a disproportionately large new tool
result or message.

## Compare with a baseline

Keep a known-good JSON report, then pass it back to the suite:

```bash
uv run python -m cyrene.observability.performance_suite \
  --baseline output/performance/baseline.json \
  --regression-threshold-percent 20 \
  --output-dir output/performance
```

Timing regression and quality failure are separate signals. Machine-dependent
timings are compared only with matching case IDs from the supplied baseline.
Use `--fail-on-quality` when a CI job should fail if events, tool results or
other fixture outputs are lost.

## Interpreting results

- `primary_ms` is the stable comparison metric selected for each case.
- `parallel_workers` and `rounds_per_worker` describe the effective workload.
- `ideal_cache_hit_rate` is the LLM-independent theoretical fixture reuse rate.
- `ideal_cache_progression` preserves the cumulative per-turn/per-round curve.
- `quality_preserved` verifies exact fixture behavior independently of speed.
- Feature reports retain throughput, p50/p95 latency and stage-level details.
- A fast failed run is not a performance success; inspect quality failures first.
