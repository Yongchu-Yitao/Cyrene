# Research Workbench Roadmap

[中文](research-workbench-roadmap.md) ·
[English](research-workbench-roadmap.en.md)

> Status checked on 2026-07-26. This document intentionally combines shipped
> capabilities with future plans. Implemented claims were checked against
> `src/cyrene/knowledge/`, `src/route/workbench/library.py`,
> `src/workbench-webui/workbench-library.*`,
> `tests/test_workbench_library.py`, and `tests/test_library_tools.py`.

## Current implementation status

| Area | Status on 2026-07-26 |
|---|---|
| Library foundation | **Implemented:** project-isolated items, collections, tags, reading state, favorites, trash, notes, annotations, attachments, relations, statistics, and field/full-text search |
| Existing Knowledge compatibility | **Implemented:** `kb_documents` are idempotently mapped into the same project's Library while reusing files and indexes |
| Import and synchronization | **Partial:** CSL JSON, RIS, BibTeX, file/PDF import, and Zotero Local API incremental sync exist; DOI/title import and Crossref/OpenAlex/Semantic Scholar adapters do not |
| Citations and reading | **Partial:** IEEE/APA/MLA/Chicago text citations, BibTeX output, and reading-state updates exist; stable citekey conflict handling and page-anchored annotations do not |
| Experiment Runtime | **Not implemented:** Agent runs, Shell, and tasks are not reproducible scientific experiment records |
| Manuscript Studio | **Not implemented:** Markdown/PDF/report support is not a manuscript object model or Quarto/Pandoc workflow |
| Unified provenance | **Not implemented:** Library relations exist, but there is no common paper-to-experiment-to-manuscript evidence graph |

## Product direction

Workbench should become a research-project operating environment, not merely
three extra screens. Its primary chain should be:

```text
Paper → Note → Experiment → Run → Artifact → Manuscript
```

Every object needs a stable ID, project ownership, status, timestamps, source,
version or content hash, and queryable metadata. Every provenance edge needs a
relation type, source, target, author (user, Agent, or importer), timestamp, and
optional evidence. This lets a reader trace a claim to a paper or run, trace a
figure to code/data/parameters/environment, and identify manuscript content
affected by upstream changes.

The preferred approach remains local-first and file-as-source-of-truth:
CSL-JSON for citation metadata, `uv`-backed command and parameterized Notebook
runs, and `.qmd`/Markdown manuscripts compiled by Quarto or Pandoc. Zotero,
OpenAlex, Jupyter, and MLflow should be adapters, not Cyrene's internal model.

## Library

### Implemented foundation

The current Library already provides structured items and creators,
collections, tags, reading states, favorites, trash/restore, notes,
annotations, attachments, relations, statistics, filters, and search.
Existing project Knowledge documents are mapped without copying their files.
The import layer parses CSL JSON, RIS, and BibTeX, while Zotero Local API sync
handles collections, items, notes, annotations, attachments, versions, and
deletions. Citation rendering and BibTeX export are available.

### Remaining MVP work

- DOI and title-based online import;
- Crossref and OpenAlex search/enrichment, with Semantic Scholar optional;
- DOI/title/author/year deduplication and merge history;
- stable citekeys with collision handling;
- PDF notes with page and source-text anchors;
- Zotero Web API and explicit conflict semantics;
- saved searches and scheduled discovery.

Zotero should remain the primary interoperability target rather than the
internal database. OpenAlex is appropriate for discovery and relationships;
Crossref for authoritative DOI metadata; Semantic Scholar may be an optional
recommendation adapter.

## Experiment Runtime

This area is not implemented. A scientific run must record the exact command,
experiment/spec version, Git commit and dirty-diff hash, OS and hardware
summary, environment lock hash, input dataset IDs and hashes, parameters,
random seed, redacted environment variables, ordered output logs, exit status,
metrics, and an artifact manifest.

The proposed lifecycle is:

```text
draft → queued → preparing → running → succeeded
                              ├──────→ failed
                              ├──────→ cancelled
                              └──────→ orphaned → reconciled/failed
```

The MVP should support command runs and parameterized Notebooks, a durable
SQLite queue, async process-group supervision, SSE logs, cancellation,
restart reconciliation, `uv`/existing-venv environments, metric parsing, and
content-hashed artifacts. Interactive Jupyter, containers, Conda, remote
compute, and MLflow integration should follow later.

## Manuscript Studio

This area is not implemented. The proposed UI combines an outline/file tree,
Markdown or Quarto editor, preview, citation search, run/artifact insertion,
and Agent suggestions reviewed through the existing diff workflow.

`.qmd` should be the default source format. `@citekey` completion must use the
current project's Library and reject unresolved citations. A
`QuartoCompiler` should be preferred, with `PandocCompiler` as a fallback and
Typst as a later option. Compilation should produce actionable diagnostics and
PDF, DOCX, or HTML without making the database the only copy of a manuscript.

## Architecture boundaries

Future code must follow the current domain layout:

```text
src/cyrene/knowledge/                 # existing Library storage/import/Zotero
src/cyrene/workbench/research/        # future cross-object business services
  models.py
  provenance.py
  experiments/
  manuscripts/

src/route/workbench/
  library.py                          # existing
  experiments.py                      # future
  manuscripts.py                      # future

src/workbench-webui/
  workbench-library.jsx               # existing
  workbench-experiments.jsx           # future
  workbench-manuscripts.jsx           # future
```

Research state belongs in normalized SQLite tables, not an ever-growing
`workbench_state.payload_json`. Workspace and attachment files remain the
content source; Knowledge remains responsible for full-text indexing.
Providers, runners, and compilers should use small injectable protocols.

## Delivery phases

### Phase 0 — Research Core (not started, about 2 weeks)

Add normalized repositories, research objects, provenance edges, artifact
manifests, permission/path/audit rules, adapter protocols, and migration
rollback fixtures.

### Phase 1 — Library MVP (partially complete)

Completed: file/PDF and CSL JSON/RIS/BibTeX import, collections/tags/status,
notes/annotations/attachments/relations, Zotero Local API incremental sync,
and Agent list/search/verified metadata updates.

Remaining: DOI/title import, Crossref/OpenAlex, robust deduplication, stable
citekeys, Zotero Web API conflict handling, and page-anchored notes. Therefore
the original exit condition has not yet been met.

### Phase 2 — Experiments MVP (not started, about 4–6 weeks)

Deliver the durable queue and supervisor, command/Notebook workloads,
reproducibility fingerprints, metrics/artifacts, comparison, diagnostics, and
Agent experiment tools.

### Phase 3 — Manuscript MVP (not started, about 4–6 weeks)

Deliver source editing and preview, citation completion/validation, artifact
insertion with provenance, Quarto/Pandoc compilation, downloads, diagnostics,
and diff-reviewed Agent changes.

### Phase 4 — Closed loop and enhancements (not started, about 3–4 weeks)

Add the research overview, unified search, impact analysis, discovery alerts,
reproducibility bundles, bidirectional Zotero sync, MLflow interchange, crash
recovery, performance work, and cross-platform packaging.

## End-to-end acceptance target

The eventual golden path is: import a paper from a DOI; create a page-anchored
note; use the paper and project data to create a parameterized Notebook run;
record its code, environment, inputs, parameters, metrics, and figure; cite the
paper and insert the figure into a manuscript; compile PDF and DOCX; navigate
from citations and figures back to their sources; and validate a reproducibility
bundle in a clean environment.

This path is a target, not a statement of current functionality.

## Risks and open decisions

- Agent runs and experiment runs need different state machines and stores.
- Structured research metadata must not be hidden only in generic Knowledge
  JSON.
- Agents must only insert Library-resolved citations.
- Arbitrary code execution needs confirmation, constrained paths, redaction,
  process-group cancellation, and eventually optional containers.
- Quarto/Pandoc packaging and licensing need cross-platform decisions.
- Large artifacts belong in files or external stores; SQLite keeps metadata,
  hashes, and locations.
- The first user cohort (computational versus wet-lab research), bundled versus
  user-installed compilers, Zotero's required sync depth, collaboration, and
  remote compute remain product decisions.

## Caveats

The implementation status reflects the repository on 2026-07-26. Source and
test files were inspected locally, but this documentation update did not rerun
live external-provider acceptance tests. Estimates are prioritization aids, not
delivery commitments, and exclude user research, design staffing, binary
redistribution review, signing, and remote infrastructure.
