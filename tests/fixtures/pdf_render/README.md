# PDF Render Golden Fixtures

This directory stores page-level fixtures for deterministic PDF render-plan and visual QA regression tests.

Use a stable page prefix such as `wait-free/page-001` or a similarly scoped document/page identifier across fixture types. Later fixture data should keep matching page names in each directory:

- `source_pages/`: extracted source page block data.
- `translations/`: cached translations for translation-eligible source blocks.
- `expected_plans/`: expected render-plan assertions such as classifications, render kinds, styles, protected regions, and coverage requirements.
- `expected_qa/`: expected deterministic QA assertions for visual and layout checks.
- `rendered_png/`: optional rendered PNG samples used for visual QA diagnostics.

Empty directories are tracked with `.gitkeep` until real page fixtures are added.

## User-Reported Visual Defect Workflow

When a user reports a visual defect in a translated PDF, convert it into a
fixture before accepting a renderer fix. Capture the source PDF, translated PDF,
page number, and the page-level render-plan artifact path from
`work/jobs/<pdf-stem>/plans/page-NNN.render-plan.json`. If available, also keep
the visual QA report and rendered PNG that show the defect.

Create the smallest page set that reproduces the defect. Prefer one page; add
adjacent pages only when cross-page context changes classification, headers,
references, or body flow. Keep the same document/page prefix under
`source_pages/`, `translations/`, `expected_plans/`, and `expected_qa/` so the
fixture runner can compare all inputs for the same defect.

Build the failing fixture first:

1. Add extracted blocks to `source_pages/<document>/page-NNN.json`.
2. Add cached translations for translation-eligible blocks to
   `translations/<document>/page-NNN.json`.
3. Add expected classifications, render kinds, protected-region bboxes, style
   names, and coverage requirements to
   `expected_plans/<document>/page-NNN.json`.
4. Add expected QA assertions to `expected_qa/<document>/page-NNN.json`; use a
   precise failure category such as overlap, blank clip, clipped content,
   overcapture, style mismatch, or body-flow spacing.
5. Run the targeted fixture test and confirm it fails for the reported defect:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest tests.test_pdf_render_fixtures -v
```

Only after the failing fixture exists should the renderer, classifier, layout,
or QA code be changed. The fix is accepted when the new fixture passes together
with the layout guard tests:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest tests.test_pdf_render_fixture_layout tests.test_pdf_render_fixtures -v
```

## Ownership Assertions

Ownership fixtures should assert the component contract, not only the visible
render item. Use `ownership_validation_ok` on every page fixture. Use
`component_contains` when a figure, table, formula, or code component must own
internal labels or columns. Use `source_not_rendered_as` to prove visual-owned
source IDs are not drawn as translated text. Use `no_text_over_component` for
ghosting reports where a text layer previously appeared over a preserved visual
clip.

For reference continuation defects, assert both sides: reference blocks should
be `reference` components and adjacent body or appendix blocks in another
column should be `translated_text` components.
