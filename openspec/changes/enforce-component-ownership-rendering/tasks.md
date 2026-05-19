## 1. Ownership Data Model

- [x] 1.1 Add `PageComponent` and ownership ledger dataclasses with stable JSON serialization.
- [x] 1.2 Add component IDs, component kinds, source IDs, source bbox, confidence, and reason codes to ownership artifacts.
- [x] 1.3 Add ownership validation for missing owners, duplicate non-duplicate owners, and invalid component kinds.
- [x] 1.4 Add unit tests for ownership serialization determinism and basic validation failures.

## 2. Component Builder

- [x] 2.1 Build initial page components from existing visual-region, classification, reference-continuation, header/footer, page-number, and unknown-content decisions.
- [x] 2.2 Ensure visual regions become visual components that own all contained source block IDs before translation planning.
- [x] 2.3 Ensure references become reference components with per-component line bbox constraints instead of page-wide reference thresholds.
- [x] 2.4 Ensure ambiguous figure/table/formula/code clusters are conservatively assigned to visual components rather than split into translated text.
- [x] 2.5 Add tests for component ownership on synthetic body, figure, table, reference, header/footer, page-number, unknown, and duplicate blocks.

## 3. Translation Planning Integration

- [x] 3.1 Update `build_batches()` to consume component ownership and include only translated text component blocks.
- [x] 3.2 Update `translate_pdf_parallel.build_page_batches()` to consume the same ownership output as single-document rendering.
- [x] 3.3 Add tests proving visual, reference, header/footer, page-number, unknown, and duplicate components are excluded from Codex batches.
- [x] 3.4 Add tests proving batch planning and render planning use the same ownership artifact for a page.

## 4. Render Planning Integration

- [x] 4.1 Update `build_page_render_plan()` to create render items from components instead of independent block-level ownership decisions.
- [x] 4.2 Add component IDs and component kinds to coverage ledger entries and render-plan JSON artifacts.
- [x] 4.3 Add render-layer exclusivity validation that fails if a source block appears in both image and text render items.
- [x] 4.4 Add validation that text render items cannot significantly overlap visual components they do not own.
- [x] 4.5 Add validation that visual clips cannot capture translated text components beyond tolerance without an explicit split reason.

## 5. Visual QA And Diagnostics

- [x] 5.1 Add ownership validation results to deterministic QA reports and parallel translation summaries.
- [x] 5.2 Add visual QA categories for duplicate ownership, text-over-visual ownership violations, visual undercapture, and visual overcapture.
- [x] 5.3 Ensure ownership errors include page number, component IDs, source block IDs, render item kinds, bboxes, and artifact paths.
- [x] 5.4 Wire ownership failures into strict QA so strict jobs fail before reporting success.
- [x] 5.5 Preserve non-strict mode by writing ownership issues without blocking exploratory output generation.

## 6. Golden Ownership Fixtures

- [x] 6.1 Add BERT page 9 fixture asserting all table columns are visual component-owned and excluded from text render items.
- [x] 6.2 Add BERT page 12 fixture asserting reference components do not own adjacent body or appendix content in another column.
- [x] 6.3 Add BERT page 15 fixture asserting figure-internal labels are visual component-owned and no text item overlaps the figure component.
- [x] 6.4 Extend the fixture runner to assert component ownership, component-to-render-item links, and ownership validation results.
- [x] 6.5 Document the workflow for converting future user-reported ghosting defects into ownership fixtures.

## 7. Compatibility And Migration

- [x] 7.1 Keep existing public helper APIs available through compatibility wrappers while routing internals through ownership.
- [x] 7.2 Preserve existing CLI options, translation cache behavior, job directory layout, and non-strict output behavior.
- [x] 7.3 Add a temporary comparison mode or tests that verify legacy render-plan coverage agrees with component ownership projections.
- [ ] 7.4 Remove or simplify obsolete local heuristics only after ownership fixtures prove the mechanism covers the behavior.

## 8. Verification

- [x] 8.1 Run focused ownership, render-plan, visual-QA, and fixture tests after implementation.
- [x] 8.2 Run the full regression command from `AGENTS.md`.
- [x] 8.3 Run the `py_compile` command from `AGENTS.md`.
- [x] 8.4 Run deterministic BERT pages 9, 12, and 15 render-plan plus visual-QA sample without live Codex calls.
- [x] 8.5 Record verification evidence, remaining visual-QA warnings, and residual risks in implementation notes before archiving.
