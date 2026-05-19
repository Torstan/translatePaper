# Implementation Notes

## Summary

Implemented deterministic component ownership as the shared contract between
translation batching, render planning, and visual QA.

The core change is `ownership.py`, which defines page components, ownership
ledgers, stable JSON serialization, and validation. The pipeline now builds
ownership before translation or rendering, then projects that same ownership into
translation batches, render items, coverage ledger entries, render-plan
artifacts, and visual-QA reports.

This is intended to prevent the recurring ghosting class of defects where a
source block is both preserved as an image clip and rendered as text, or where
figure/table/reference content leaks into translation.

## Implemented Mechanisms

- Added `PageComponent` ownership for translated text, visual content,
  references, headers/footers, page numbers, unknown content, duplicates, and
  explicit skips.
- Routed single-document and parallel translation batch planning through the
  ownership result so only translated-text components enter Codex batches.
- Routed render planning through the same ownership result and serialized
  component IDs/kinds into render items and coverage ledger entries.
- Added ownership validation for missing owners, duplicate owners, invalid
  component kinds, image/text duplicate rendering, text-over-visual violations,
  visual undercapture, and visual overcapture.
- Added visual-QA reporting for ownership issues and strict-mode failure
  behavior while preserving non-strict exploratory output generation.
- Added BERT page 9, 12, and 15 ownership fixtures for table ownership,
  multi-page references, and figure-internal label preservation.
- Extended the fixture runner so future visual regressions can assert ownership
  contracts directly instead of only checking rendered text.

## Verification

Focused ownership/rendering checks passed during implementation:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest \
  tests.test_ownership tests.test_render_plan tests.test_waitfree_regression \
  tests.test_pdf_render_fixtures.PdfRenderFixtureRunnerTests.test_bert_ownership_fixtures -v
```

The full regression command from `AGENTS.md` passed:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest \
  tests.test_qa_semantic tests.test_render_pdf tests.test_layout \
  tests.test_regions tests.test_render_plan tests.test_waitfree_regression \
  tests.test_pdf_render_fixtures tests.test_pdf_render_fixture_layout \
  tests.test_translate_pdf_parallel tests.test_qa_visual -v
```

Result:

```text
Ran 299 tests in 54.067s
OK
```

Python compilation checks from `AGENTS.md` passed for touched modules and tests:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m py_compile \
  qa_semantic.py backtranslate_check.py render_pdf.py layout.py regions.py \
  classify.py render_plan.py qa_visual.py translate_pdf_via_codex.py \
  translate_pdf_parallel.py tests/test_qa_semantic.py tests/test_render_pdf.py \
  tests/test_layout.py tests/test_regions.py tests/test_render_plan.py \
  tests/test_waitfree_regression.py tests/test_pdf_render_fixtures.py \
  tests/test_pdf_render_fixture_layout.py tests/test_translate_pdf_parallel.py \
  tests/test_qa_visual.py tests/pdf_render_fixture_runner.py tests/test_ownership.py \
  ownership.py
```

Deterministic BERT pages 9, 12, and 15 render-plan plus visual-QA sample passed
without live Codex calls. The sample wrote artifacts under:

```text
/tmp/translatePaper_bert_ownership_sample/plans/page-009.render-plan.json
/tmp/translatePaper_bert_ownership_sample/plans/page-012.render-plan.json
/tmp/translatePaper_bert_ownership_sample/plans/page-015.render-plan.json
/tmp/translatePaper_bert_ownership_sample/visual_qa/visual_qa_report.json
/tmp/translatePaper_bert_ownership_sample/visual_qa/visual_qa_report.md
```

Result:

```json
{
  "issue_count": 0,
  "highest_severity": "none",
  "checked_pages": [9, 12, 15]
}
```

## Residual Risks

- The ownership builder still consumes existing classification and visual-region
  heuristics. They are now centralized behind ownership, but not all older
  heuristics are proven redundant.
- Conservative visual ownership may preserve more source English than ideal for
  ambiguous diagrams, formulas, tables, or code blocks. This is intentional to
  avoid ghosting and alignment breakage.
- Existing Wait-Free-specific heuristics remain as migration debt. They should
  only be removed after dedicated fixtures prove ownership covers the same
  behavior.
- The current BERT fixtures prove the reported page 9, 12, and 15 ownership
  outcomes, not every possible table/reference/figure extraction shape.

## Task 7.4 Status

Task 7.4 remains intentionally pending:

```text
Remove or simplify obsolete local heuristics only after ownership fixtures prove
the mechanism covers the behavior.
```

Subagent audit found no local heuristic that is both clearly obsolete and covered
tightly enough by the new ownership fixtures to remove safely. The current
fixtures prove the target BERT outcomes, but they do not prove redundancy for
visual-region growth, final visual bbox clipping, nested duplicate suppression,
TOC fallback, line-based reference reconstruction, standalone heading-number
pairing, or the reference-signature parser.

Leaving this task open is the safer state because it prevents cleanup work from
turning into another untested heuristic migration.
