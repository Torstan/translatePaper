# AGENTS.md

This file applies to the whole repository. Follow it whenever you modify code,
tests, fixtures in this project.

## Engineering Principles

- Prefer general design improvements over case-by-case patches. A reported PDF
  defect should normally become a classification rule, layout invariant, QA
  check, fixture, or regression test that protects a class of documents.
- Do not add one-off exceptions keyed to a paper title, page number, exact text
  snippet, or temporary output artifact unless the behavior is explicitly
  documented as data-specific and covered by a test explaining why it is safe.
- Keep complexity visible. If a change adds branching, fallback paths, or new
  heuristics, document the invariant it enforces and the failure mode it avoids.
- Preserve existing behavior unless user request clearly requires changing it. 
  When behavior changes, update tests and notes to make the new contract explicit.
- Use deterministic code for deterministic work. Do not ask Codex or another
  model to make decisions that can be handled by parser logic, geometry checks,
  layout rules, or tests.

## Before Editing

- Read the relevant modules and immediate callers before changing code.
- For user-reported visual issues, first turn the issue into a fixture or
  failing regression where practical. The expected fix should be observable
  without relying on live Codex calls.

## Translation Architecture Boundaries

The A–H flow in
[the module-boundaries design](docs/superpowers/specs/2026-10-04-translation-module-boundaries-design.md)
describes responsibilities, not required Python files or public stage APIs.
Apply these boundaries when changing the translation pipeline; the design also
records current gaps and unresolved QA policy.

- Source analysis owns content roles, component ownership, source identity,
  reading relationships, source regions, and per-page dimensions. Translation
  results, model choice, and worker count must not redefine those source facts.
  Keep source page numbers distinct from output page numbers.
- Translation owns task construction, response validation, and final text
  repairs. Downstream rendering receives final translations, not raw responses
  or repair steps. Worker count controls execution concurrency, not task content.
- Rendering planning chooses how each source item is represented and records
  its source mapping and coverage obligation. Layout chooses geometry and font
  fit within that decision; it must not rewrite translations or reclassify
  source content. Diagnostic reasons such as `fallback_reason` do not grant
  layout or font permissions.
- After layout, validate the final vector plan's ownership, coverage, geometry,
  text overlap, style, and fit before drawing. Recompute final diagnostics
  instead of trusting an earlier snapshot. Structural errors block drawing
  regardless of optional QA settings; warnings keep their own severity.
- PDF drawing consumes the checked plan without inventing new content,
  fallbacks, or geometry. Reports identify the plan or actual PDF they check;
  raster output must not be described as validated by vector-only checks. Keep
  any existing plan-free source-image copying explicit until it is planned.
- Ordinary callers use the complete document entry; internal orchestration
  uses complete translation and final-plan operations rather than sequencing
  their internal stages. Reuse existing modules and records; add files, stage
  types, or registries only for demonstrated responsibilities.
- Cache extraction, OCR, and model calls when their actual inputs justify
  reuse. Keep final plans and QA reports as traceable artifacts; add automatic
  reuse only when there is evidence of a performance need. Optional QA output
  acceptance remains a policy decision, separate from mandatory checks.

## Translation And Rendering Rules

- Titles, headings, subheadings, body text, references, headers/footers, figures,
  formulas, tables, code blocks, and unknown content must remain distinguishable
  in render plans and coverage ledgers.
- Text fit validation and final PDF drawing must use the same wrapping, font
  selection, font-size, and line-height rules.
- Unknown or unsupported non-trivial source content must be preserved and
  reported. It must not be silently dropped.
- Visual regions should protect source figures, formulas, tables, code, and
  other non-translated content without swallowing nearby body prose.
- Fallbacks must be explicit in render items, coverage ledgers, and QA reports.
  Avoid hidden fallbacks that make generated PDFs look successful while losing
  source content.

## Tests And Fixtures

- Every bug fix should add or update a focused test unless the change is purely
  documentation or the existing test already fails for the reported problem.
- Prefer fixture-based tests for rendering defects. They should encode the page
  data, translations, expected render-plan assertions, and expected visual-QA
  assertions needed to reproduce the issue.
- Tests should verify intent, not only implementation details. A future change
  that reintroduces overlap, clipping, style drift, missing coverage, or dropped
  content should fail clearly.
- Keep generated caches and local output artifacts out of commits unless they
  are intentional fixtures.

## Required Verification Before Commit

Run the full regression suite before committing code changes:

```bash
PYTHONPYCACHEPREFIX=~/tmp/translatePaper_pycache python3 -m unittest \
  tests.test_qa_semantic tests.test_render_pdf tests.test_layout \
  tests.test_regions tests.test_render_plan tests.test_waitfree_regression \
  tests.test_pdf_render_fixtures tests.test_pdf_render_fixture_layout \
  tests.test_translate_pdf_parallel tests.test_qa_visual \
  tests.test_translation_batch tests.test_document_pipeline \
  tests.test_final_render_plan tests.test_layout_policy -v
```

Run Python compilation checks for touched Python modules and tests:

```bash
PYTHONPYCACHEPREFIX=~/tmp/translatePaper_pycache python3 -m py_compile \
  qa_semantic.py backtranslate_check.py render_pdf.py layout.py regions.py \
  classify.py render_plan.py qa_visual.py pipeline.py translation_batch.py \
  translate_pdf_via_codex.py \
  translate_pdf_parallel.py tests/test_qa_semantic.py tests/test_render_pdf.py \
  tests/test_layout.py tests/test_regions.py tests/test_render_plan.py \
  tests/test_waitfree_regression.py tests/test_pdf_render_fixtures.py \
  tests/test_pdf_render_fixture_layout.py tests/test_translate_pdf_parallel.py \
  tests/test_qa_visual.py tests/pdf_render_fixture_runner.py \
  tests/test_translation_batch.py tests/test_document_pipeline.py \
  tests/test_final_render_plan.py tests/test_layout_policy.py ownership.py
```

For rendering or visual-QA changes, run at least one deterministic sample or
fixture path that writes render plans and visual-QA reports. Inspect the report
for issue counts and artifact paths before claiming the change is complete.

## Commit Discipline

- Do not commit unless the regression commands above pass, or clearly state why
  a command could not be run.
- Review `git status --short` before staging. Separate intended source, test,
  spec, and fixture changes from generated files such as `__pycache__/`, `tmp/`,
  `work/`, and `output/`.
- Do not revert unrelated user changes. If unrelated dirty files exist, leave
  them untouched and mention the dirty state when handing off.
