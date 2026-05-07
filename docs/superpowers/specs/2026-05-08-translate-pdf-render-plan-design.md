# PDF translation render-plan redesign

Date: 2026-05-08

## Goal

Redesign the PDF translation pipeline so translated output is readable, complete, and layout-safe.

The current implementation has too many implicit decisions in rendering. Blocks can be translated, skipped, preserved as images, or removed by nested-block filtering without a single page-level record of what happened. This has caused missing text, missing formulas, broken figures, references being translated, line wrapping defects, and layout overlap.

The new design makes every source block go through explicit classification, render planning, coverage validation, rendering, and post-render QA.

## Non-goals

- Do not rewrite the model-calling cache format unless needed for compatibility.
- Do not force references or figures into Chinese if preserving the original is safer.
- Do not render references as page images unless the selectable-text path fails.
- Do not accept a generated PDF when source content silently disappears.

## Required behavior

1. Body text and headings are translated to Chinese when translation is available.
2. Figures, formulas, code figures, and their captions are preserved as compressed original page image clips. Captions stay in English with the original figure/formula region.
3. References are not translated. They should be rendered as selectable/copyable English text using the original layout as a guide.
4. If content cannot be reliably translated or laid out, preserve a compressed original image clip in the translated PDF.
5. Page headers, footers, and page numbers are explicitly classified and either preserved or skipped by rule.
6. No source block may be dropped without an explicit classification and render decision.
7. Rendered content must avoid visible overlap, clipping, and obvious coordinate drift.
8. Title font sizes must follow a stable hierarchy: paper title > level-1 section > level-2 section > body > footers/references.

## Pipeline

```text
extract source blocks
-> classify every block
-> build page render plan
-> translate translatable blocks only
-> validate coverage and layout
-> render PDF
-> run post-render QA
```

## Core data structures

### SourceBlock

Represents extracted source content.

Fields:

- `id`
- `page`
- `bbox`
- `text`
- `source_kind`, when known from OCR or bbox parsing

### BlockClassification

Every non-empty source block receives exactly one classification.

Classes:

- `body`
- `heading`
- `title`
- `figure_region`
- `formula_region`
- `code_figure`
- `reference`
- `header_footer`
- `page_number`
- `unknown`

### RenderItem

Every classified block maps to one render item.

Types:

- `translated_text`: translated Chinese vector text
- `original_selectable_text`: original English vector text
- `original_image_clip`: compressed clipped image from the source page
- `skip_explicitly`: allowed only for page numbers, duplicate headers, or known decorative items

### CoverageLedger

Records every decision:

```text
source block id
-> classification
-> render item
-> validation result
-> fallback reason, if any
```

The renderer fails QA if any non-trivial block has no ledger entry.

## Classification rules

### Body and headings

Text is classified as body when it is normal prose and not inside a protected visual/reference region.

Text is classified as heading when it matches section patterns or typography:

- `1. INTRODUCTION`, `2. THE MODEL`, `2.1 I/O Automata`
- uppercase source headings near the left margin
- existing translated heading cache when available

Heading font scale is selected by level:

- paper title: largest
- level-1 heading: larger than body
- level-2 heading: between level-1 and body
- body: normal source-derived size
- references/footer: smaller

### Figures, formulas, and code figures

Figure, formula, and code-figure regions are protected visual regions. Their internal words are not sent to translation and are not independently rendered.

Detection uses multiple signals:

- caption lines such as `Fig.`, `Figure`, `Table`
- formula-like text and mathematical symbol density
- nearby short fragmented text blocks aligned like diagrams
- source blocks with `preserve_image`
- protected boxes grown from captions and formula fragments

Once a visual region is detected, the whole region including caption is rendered as a compressed original image clip. This avoids missing internal labels, broken formulas, and translated captions overwriting diagrams.

### References

Reference pages or reference regions start at a `REFERENCES` or `BIBLIOGRAPHY` heading and continue to the end of the document or until an explicit non-reference section is detected.

References are rendered as original English selectable text. The implementation should use `source_bbox.html` word or line coordinates where possible, instead of translating the reference block. If selectable reconstruction fails, the fallback is a compressed original image clip and the QA report marks this page as degraded.

### Unknown content

Unknown content is never silently discarded. Fallback order:

```text
translated vector text, when safe
-> original selectable text
-> compressed original image clip
-> QA failure
```

## Translation rules

Only `body`, `heading`, and `title` blocks enter translation batches.

Blocks classified as `figure_region`, `formula_region`, `code_figure`, `reference`, `header_footer`, `page_number`, and `unknown` do not enter the translation prompt by default.

If a block needs translation but has no cached translation, the renderer must not drop it. It either:

1. requests translation in the normal batch workflow, or
2. uses original selectable text/image fallback and records the reason in the ledger.

## Layout rules

### Text wrapping

Use simple greedy wrapping:

For each candidate token, append it to the current line if the measured width remains within the allowed range. Otherwise, break before that token.

The measurement path and drawing path must share the same tokenization and font choice so line widths match rendered output.

### Protected regions

Protected regions are registered before body text layout:

- figures
- formulas
- code figures
- reference regions when reconstructed separately
- image clips

Translated body text must not overlap protected regions. If a text block cannot fit without overlap, try in order:

1. reduce font size within allowed bounds
2. expand only inside available page area
3. split or rewrap within the same source region
4. fall back to compressed original image clip

### Overlap and clipping

Before saving a page, validate render item boxes:

- no significant overlap between unrelated render items
- no text outside page bounds
- no line extends beyond its target box except within configured tolerance
- no body text drawn over protected visual regions

## Rendering rules

### Vector text

Use vector text for translated body and headings. Preserve mixed Latin/CJK font handling, but keep the wrapper simple and deterministic.

### Image clips

Use compressed image clips for figures, formulas, code figures, and unreliable regions.

Recommended behavior:

- render the source page clip at bounded DPI
- compress with JPEG or deflated image stream when acceptable
- keep the clip bbox aligned to source coordinates
- preserve aspect ratio

### References

References use original English text coordinates from `source_bbox.html`. They should be selectable and copyable. Maintain original indentation and line breaks as much as possible.

## QA checks

### Coverage QA

Fail if:

- a non-trivial source block has no classification
- a non-trivial source block has no render item
- a non-trivial source block is skipped without an explicit allowed skip reason
- a translation block is missing and no fallback was used

### Content QA

Sample checks for Wait-free Synchronization:

- Page 4 must retain the content corresponding to `In(A) is a set of input events`.
- Page 15 must preserve the formula/assertion region after `To show consistency, we use the following assertions:`.
- Page 3 Fig. 1 must be visually complete and keep its English caption.
- Page 9 Fig. 3 and Fig. 4 must be visually complete and keep English captions.
- Pages 25 and 26 references must remain English and must not contain Chinese translation fragments such as `载于`.

### Layout QA

Render PNG samples for pages 1, 2, 3, 4, 9, 15, 25, and 26. Check:

- no content overlap
- no obvious clipping
- no shifted figure/formula clips
- title and section font hierarchy is correct
- body lines are filled greedily without large avoidable right-side blanks
- references align to original margins and are copyable where possible

## Implementation impact

Primary file:

- `translate_pdf_via_codex.py`

Likely changes:

- add block classification and render-plan generation
- replace unsafe nested-block filtering with ledger-aware filtering
- add visual-region grouping
- add reference reconstruction from `source_bbox.html`
- make `write_vector_pdf` consume a render plan
- add coverage and layout QA helpers
- keep existing translation cache format compatible

## Acceptance criteria

The redesign is accepted when:

1. Wait-free Synchronization renders with no missing content in the sampled pages.
2. Figures/formulas/captions are preserved as original image clips.
3. References are not translated and remain selectable where reconstruction succeeds.
4. The generated PDF has outlines where source headings can be inferred.
5. QA reports coverage decisions for all non-trivial source blocks.
6. Page sample renders show no visible overlap or severe misalignment.
