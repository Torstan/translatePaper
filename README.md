# translatePaper

Translates English PDF papers to Chinese PDFs. It extracts text, uses Codex CLI, and renders Chinese back to pages. Supports batch jobs, resume, and QA. It keeps the same page id.

## Scripts

- `translate_pdf_parallel.py`: batch CLI; selects documents, schedules jobs and writes summaries.
- `translate_pdf_via_codex.py`: single-PDF CLI.
- `pipeline.py`: shared `translate_document()` workflow, source analysis, rendering and QA orchestration.
- `translation_batch.py`: fixed batch execution, resume, incremental cache saves and model retries.
- `backtranslate_check.py` / `qa_directory_backtranslation.py`: QA.

## Requirements

Python 3.10+, Codex CLI, Poppler, `PyMuPDF`, `Pillow` and a Chinese font. Default: `/usr/share/fonts/truetype/arphic/uming.ttc`.

Both vector rendering and raster PDF assembly use PyMuPDF. Raster mode assembles
the rendered page images directly; XeLaTeX and `.tex` files are no longer used.

Serial and parallel entry points both generate missing cross-page sentence
repairs before vector or raster rendering. Translation batches and sentence
repairs share command execution and retries: failed commands, stale output,
invalid fields, and missing, extra, or duplicate IDs/keys cannot enter the cache.
An empty repair prefix is valid; an empty repaired sentence is not.

Both CLIs call `pipeline.translate_document()` with explicit `DocumentOptions`.
Both group within each page. The single-PDF CLI defaults to 10,000 characters
and does not run optional QA; the batch CLI defaults to 7,000 characters and
runs QA unless `--no-qa` is given. Worker count
controls execution concurrency without changing the prepared batch boundaries.

Both CLIs reuse only validated responses whose actual request matches the cache
key, including source text, context, prompt/schema, model and reasoning effort.
They atomically save each successfully collected batch result. ID overlap alone
does not establish freshness; legacy responses without request keys are rerun.
An empty page selection fails before model execution or PDF drawing.

Python consumers import the implementation from `pipeline`, not the CLI scripts.
The existing command names and flags remain available.

`translate_pages` owns grouping, cache recovery, response validation, boundary
repairs and deterministic text cleanup, returning final translations and the
requested source-block count. The response cache stays unmodified by repairs
and cleanup so resuming cannot strip a sentence prefix twice.
`render_translated_pdf` consumes those final translations; it no longer accepts
`model`, `reasoning_effort` or `retries`. Python callers that previously relied on
rendering to repair raw translations must call `translate_pages` first, or use
the complete `translate_document` workflow. Offline fixtures containing raw
responses use `finalize_translations` before planning.

`build_final_page_plan` combines planning, layout adaptation, mandatory validation
and diagnostic saving. It returns the existing `PageRenderPlan` or raises;
when `job_paths` requests artifacts, the failed plan is recorded as well.
`write_vector_pdf` uses this entry before drawing;
ordinary callers do not sequence layout repair functions or validation results.
Draft builders and individual repairs remain implementation/testing helpers.
Layout planning and
visual QA share the same protected-region overlap predicate (over 6pt vertically
and over 5% of the smaller box's area) and text-overlap predicate. Shared source
IDs do not exempt overlapping text; disjoint fragments remain valid. Complete
body flows also check text against the ordered source IDs after merging/splitting.
This is a local flow check, not proof of arbitrary document reading order.
Flows sharing partial sources with other items retain aggregate per-source
conservation checks; they cannot require a full translation in every fragment.

Native source images are planned before text layout, using occurrence IDs such
as `p001i0000` in render items and coverage entries. `source_image_xref` selects
a native resource (positive) or PDF page crop (zero); absent means the existing
visual-region crop behavior. Masks, grouped rows and overlapping images use
page crops. Off-page images preserve their visible intersection using a crop.
Existing visual clips share image coverage rather than drawing the
same area twice. Final validation checks each original image obligation after
layout; drawing no longer copies images outside the plan. The existing full-page
background and small edge-icon filters remain unchanged.

Rendering no longer completes sentences from hardcoded source phrases: complete
translations remain intact, and incomplete translations must be checked through
semantic QA rather than silently rewritten during drawing. Coverage validation
uses the document classifier's trivial-content rule in every entry point.
Vector drawing uses the planned font size and reports overflow; font-size
adjustments belong to layout planning.

## Usage

```bash
cd /mnt/d/ginobili/code/translatePaper
python3 translate_pdf_parallel.py --source-dir /path/to/pdfs --target-dir /path/to/output --document-workers 1 --page-workers 2 --continue-on-error
```

Output: `<original-name>-Chinese.pdf`.

Test two pages by adding `--include paper.pdf --page-start 1 --page-end 2 --no-qa`.

Common options: `--force`, `--retranslate`, `--refresh-source`, `--no-qa`,
`--strict-qa`, `--qa-mode all`, and `--qa-sample-size N`.

Intermediate files: `work/jobs/<pdf-stem>/`. Summary: `work/parallel_translation_summary.md`.

## QA Artifacts

Vector rendering writes page render plans under:

```text
work/jobs/<pdf-stem>/plans/page-NNN.render-plan.json
```

Each plan records its source `page_num`, one-based `output_page_num`, and actual
`page_size` in PDF points. Ownership components use the single `components` field.
During vector rendering, drawing and QA share the final in-memory plans; saved
JSON plans remain available for offline visual QA and are structurally checked
when loaded; missing `render_items` is an error, while an explicit empty list is
valid. Raster plans record the actual pixel layout, lines and page dimensions;
vector-only checks do not validate raster drawing.

Vector rendering validates the final layout before drawing each page, including
source ownership, coverage, protected geometry, text overlap, style and fit.
These checks run even with optional QA disabled. Ownership diagnostics are
recomputed after layout; warnings retain their severity in both plans and QA.
Failed validation writes all check results, closes PDF resources, and leaves an
existing output PDF unchanged. A report-write failure does not replace the
validation error.

When QA is enabled, deterministic quality reports are written to:

```text
work/jobs/<pdf-stem>/deterministic_quality_report.json
work/jobs/<pdf-stem>/deterministic_quality_report.md
```

Visual QA writes rendered PNGs and reports to:

```text
work/jobs/<pdf-stem>/visual_qa/rendered_png/
work/jobs/<pdf-stem>/visual_qa/visual_qa_report.json
work/jobs/<pdf-stem>/visual_qa/visual_qa_report.md
```

Use non-strict QA while exploring defects. Use `--strict-qa` before accepting a
batch to fail on additional deterministic content and visual QA errors. QA runs
after PDF writing; rejection by optional QA does not yet remove or roll back
that PDF.

Drawing and offline visual QA enforce the same style roles and font bounds.
Render items record `layout_role` for flow/split relationships and `font_policy`
for document, source-adapted, compact-flow, or dense-row sizing. These fields
control layout and validation; `fallback_reason` is diagnostic text.
Role splits still check their permitted styles and font sizes. Source-adapted
sizes have per-style limits; compact body text has the existing 5pt minimum.
Older JSON plans without these fields receive ordinary document-style checks;
regenerate historical special plans to record their explicit permissions.

Embedded running headers are removed only with source evidence: a repeated margin
line across pages or separated rows in the extracted PDF geometry. Cleanup happens
before translation lines are joined. If a translated line cannot be matched to the
source header, it is preserved; paper titles and author names are not deletion rules.

## Fixture Workflow

Page-level visual regression fixtures live under `tests/fixtures/pdf_render/`.
For a user-reported visual defect, add the smallest page fixture that reproduces
the issue:

```text
source_pages/<document>/page-NNN.json
translations/<document>/page-NNN.json
expected_plans/<document>/page-NNN.json
expected_qa/<document>/page-NNN.json
```

Then run:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest \
  tests.test_pdf_render_fixtures tests.test_pdf_render_fixture_layout -v
```

Render representative output pages to PNG with Poppler when layout changes:

```bash
pdftoppm -png -f 1 -l 1 translated.pdf output/page
```
