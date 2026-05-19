## Context

The current renderer has render-plan artifacts, visual QA, protected regions, and page fixtures, but ownership is still inferred indirectly from classification and visual-region heuristics. This allows a source block to be preserved inside an `original_image_clip` while also being rendered as translated or original selectable text. The visible result is ghosting: source visual content and text-layer content are drawn over each other.

The BERT failures illustrate the pattern:

- Page 9: table cells were partly preserved as source image and partly treated as body-like text, causing clipped or duplicated table content.
- Page 12: reference line preservation used a page-wide threshold and copied adjacent body content.
- Page 15: figure-internal labels were omitted from the visual component and then rendered as body text over a preserved figure clip.

The fix cannot be another list of figure-label heuristics. The pipeline needs an explicit component ownership model that is built once and consumed by translation planning, render planning, and QA.

## Goals / Non-Goals

**Goals:**

- Define `PageComponent` as the single ownership unit for visual and text rendering decisions.
- Make ownership exclusive: every non-trivial source block has exactly one owner and one render strategy.
- Make translation batches and render plans derive from the same ownership result.
- Fail before PDF rendering when duplicate ownership, text-over-visual rendering, or unsafe component overlap is detected.
- Add ownership fixture coverage for known BERT page 9, page 12, and page 15 ghosting/clipping defects.
- Reduce future heuristic growth by moving ambiguous cases to conservative component ownership and explicit validation.

**Non-Goals:**

- Do not replace text extraction, Codex translation, or the existing PDF rendering backend.
- Do not attempt perfect semantic recognition of every figure/table type.
- Do not translate figure/table/internal visual text.
- Do not remove existing visual-region heuristics immediately; wrap them behind ownership and retire them incrementally.
- Do not depend on a multimodal model for acceptance.

## Decisions

### Decision 1: Introduce `PageComponent` as a deterministic ownership boundary

Each page will produce a list of components before translation batches are built. A component records:

- `component_id`
- `component_kind`: `translated_text`, `visual`, `reference`, `header_footer`, `page_number`, `unknown`, or `duplicate`
- `source_ids`
- `source_bbox`
- optional `clip_bbox`
- `confidence`: `deterministic`, `inferred`, or `conservative`
- `reason_codes`
- parent/child relationship only when a component is explicitly split

Alternatives considered:

- Keep using only block classifications. This cannot express that a group of blocks is one visual unit and leaves duplicate layer decisions possible.
- Keep using only protected-region bboxes. This protects geometry but does not create a source-block ownership contract.

Rationale: ownership must be both source-ID based and geometry based. Source IDs prevent duplicate rendering; bboxes prevent visual overlap.

### Decision 2: Translation planning consumes ownership, not raw classification

`build_batches()` and `build_page_batches()` will include only blocks owned by `translated_text` components and classified as title, heading, subheading, or body. Blocks owned by visual, reference, header/footer, page number, unknown fallback, or duplicate components are excluded from Codex batches.

Alternatives considered:

- Keep batch planning independent and later suppress translated output during rendering. This wastes model calls and allows stale translations to mask ownership defects.
- Translate everything and choose later. This increases leakage of visual/internal text into final PDFs.

Rationale: ownership is the earliest point where duplicated rendering can be prevented.

### Decision 3: Render planning consumes the same ownership ledger

Render planning will create render items from components:

- visual component -> one `original_image_clip` or an explicit split plan
- translated text component -> translated text items or explicit text fallback
- reference component -> original selectable reference text, using component-contained line bboxes
- unknown component -> conservative image clip or original selectable fallback with reason
- duplicate component -> explicit skip entry

The render plan will persist ownership entries next to the existing coverage ledger. Coverage ledger entries will include `component_id` and `component_kind`.

Alternatives considered:

- Keep region grouping separate from render item creation. This is the current source of mismatch.
- Let render items infer ownership after creation. That detects duplicates too late and makes batch planning inconsistent.

Rationale: render items must be a projection of component ownership, not an independent decision tree.

### Decision 4: Add hard ownership validation before PDF writing

The pipeline will fail validation when:

- a non-trivial source block has no component owner
- a source block appears in more than one non-duplicate component
- a source block appears in both an image render item and a text render item
- a translated/original selectable text item significantly overlaps a visual component it does not own
- a visual component clip captures a translated text component beyond allowed tolerance
- a component is split without explicit child ownership

Alternatives considered:

- Report these as visual QA warnings. That still produces defective PDFs.
- Rely on rendered PNG diff after writing. This catches defects late and cannot always map pixels back to source IDs.

Rationale: duplicate ownership is a contract violation, not a best-effort QA concern.

### Decision 5: Treat ambiguous visual clusters conservatively

When ownership cannot confidently separate diagram/table/formula internals from nearby prose, the component builder will preserve the visual cluster and exclude it from translation. Only clearly outside prose may remain translated.

Alternatives considered:

- Keep expanding token-level recognition for diagram labels. This creates unbounded complexity.
- Always rasterize the full page. This avoids ghosting but sacrifices translated selectable text.

Rationale: preserving too much of an uncertain visual cluster is safer than ghosting, clipping, or translating internal figure text.

### Decision 6: Make BERT failures ownership fixtures

The first fixture set for this change will cover:

- BERT page 9: table visual component owns all table columns, including narrow numeric and placeholder columns.
- BERT page 12: reference components do not own adjacent body/appendix columns.
- BERT page 15: figure component owns internal diagram labels with prime marks and no translated text overlaps figure clips.

Alternatives considered:

- Only synthetic tests. They are useful for rules but miss extraction pathologies from real PDFs.
- Whole-document regression only. It is slower and harder to diagnose.

Rationale: real page fixtures make the architectural contract concrete.

## Risks / Trade-offs

- Component grouping can initially preserve more source English than desired -> Prefer conservative visual preservation for non-body content and use QA to report translation loss separately.
- Ownership validation may fail existing documents that previously produced PDFs -> Keep non-strict mode for exploration, but strict mode must fail duplicate ownership.
- Fixture data can grow large -> Store the smallest page-level source blocks, translations, expected ownership assertions, and optional rendered samples needed to reproduce the defect.
- Introducing ownership while existing functions remain in place can create dual paths -> Add compatibility wrappers that call the ownership builder and assert legacy outputs match ownership projections.
- Pixel checks may still produce false positives -> Use ownership validation as the primary ghosting gate and pixel QA as supporting evidence.

## Migration Plan

1. Add component data structures and serialization without changing output.
2. Build ownership from existing classification and visual-region results, then compare it against current render plans in tests.
3. Route translation batch planning through ownership.
4. Route render planning through ownership and add hard validation.
5. Add BERT page fixtures and make current ghosting defects fail before the fix.
6. Regenerate affected BERT outputs and inspect page 9, page 12, and page 15 PNGs.
7. Run the full regression and py_compile commands from `AGENTS.md`.

Rollback strategy: keep the existing classification and visual-region functions as inputs to the ownership builder. If ownership routing causes broad regressions, disable ownership enforcement in non-strict mode while keeping fixture tests and serialized ownership artifacts for diagnosis.

## Open Questions

- Should ownership validation be strict by default for single-document translations, or initially enabled only by `--strict-qa`?
- Should unknown components always render as image clips, or can low-risk unknown text render as original selectable text?
- What tolerance should visual component overcapture use before flagging nearby body prose as captured?
