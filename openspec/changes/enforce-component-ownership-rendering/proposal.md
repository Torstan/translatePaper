## Why

Visual defects such as ghost text, white blocks over figures, clipped tables, and text/image overlap keep recurring because the renderer permits the same source content to be owned by multiple layers. The system needs a component ownership contract that makes duplicate rendering structurally impossible instead of relying on more figure/table token heuristics.

## What Changes

- Introduce page-level `PageComponent` ownership before translation planning and render planning.
- Assign each non-trivial source block to exactly one component owner: translated text, preserved visual, reference, header/footer, page number, unknown fallback, or duplicate/skip.
- Treat figures, tables, formulas, code listings, diagrams, and uncertain visual clusters as component-level source clips that own all blocks and pixels inside their component unless explicitly split.
- Add pre-render validation that fails when a source block appears in more than one render layer, when text render items overlap preserved visual components, or when an image clip captures translated text not owned by that component.
- Add visual QA rules and fixture assertions for ghosting class defects: duplicate source ownership, translated text over preserved visuals, visual component undercapture, and visual component overcapture.
- Convert known BERT page 9, page 12, and page 15 defects into ownership fixtures so future changes must prove they preserve the ownership contract.
- Keep existing CLI and translation cache behavior while routing batch planning and render planning through ownership outputs.

## Capabilities

### New Capabilities

- `component-ownership-rendering`: Defines page component ownership, unique source block ownership, render-layer exclusivity, and validation gates that prevent duplicate rendering.

### Modified Capabilities

- None. This follow-up change builds on the prior render-plan and visual-QA work but defines a new ownership contract rather than changing an archived base spec.

## Impact

- Affected code: `regions.py`, `classify.py`, `render_plan.py`, `layout.py`, `qa_visual.py`, `translate_pdf_via_codex.py`, and `translate_pdf_parallel.py`.
- Affected tests: render-plan tests, visual QA tests, fixture runner tests, and BERT page-level regression fixtures.
- Affected artifacts: page render plans will include component IDs, ownership ledger entries, ownership validation results, and component-level diagnostic bboxes.
- No new model dependency is introduced. The mechanism is deterministic and uses existing extraction geometry, pixel analysis, render-plan artifacts, and source/destination PNG checks.
