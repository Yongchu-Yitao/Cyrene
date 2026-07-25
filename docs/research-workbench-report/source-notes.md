# Research Workbench report source notes

## Reporting job

- Question: how to turn the current Workbench into an all-in-one research tool, focusing on literature, experiments, and manuscript authoring.
- Audience: product stakeholders.
- Decision: choose product boundaries, technical architecture, and implementation order.
- Baseline: Cyrene v0.6.16+fix repository state on 2026-07-23.
- Success criterion: a roadmap that reuses current capabilities while producing a traceable Paper → Run → Manuscript workflow.

## Required structure mapping

- Title: report title block.
- Executive Summary: dedicated first section after title.
- Key findings with evidence: current fit, research graph, Library, Experiments, Manuscripts, data model, and information architecture.
- Recommended next steps: dedicated section.
- Further questions: dedicated section.
- Caveats and assumptions: dedicated final section.

## Evidence inventory

- Repository: `README.md`, `docs/architecture.md`, `src/cyrene/db.py`, `src/cyrene/report_export.py`, `src/cyrene/knowledge/`, `src/route/workbench/knowledge.py`, `src/workbench-webui/workbench.jsx`, and `src/workbench-webui/workbench-model.jsx`.
- Official external documentation: Zotero Web API v3, Crossref REST API, OpenAlex API, Semantic Scholar Academic Graph API, Jupyter Server REST API, uv project locking, MLflow Tracking, Quarto Manuscripts, Pandoc citeproc, and Typst bibliography.

## Omission and QA notes

- Chart map: the roadmap section asks how implementation effort differs by phase; a grouped `bar` chart compares `min_weeks` and `max_weeks` across five phases. The data is an explicitly labeled planning estimate from `docs/research-workbench-roadmap.md`, not measured delivery performance. Palette policy is a restrained two-root comparison with series labels supplied by the artifact renderer.
- The timeline is an engineering-order estimate for prioritization, not a data-backed commitment.
- No user interviews, telemetry, market sizing, or implementation prototype was available; these remain explicit caveats.
- MCP report rendering is not callable in this desktop tool context, so the selected delivery mode is portable HTML.
