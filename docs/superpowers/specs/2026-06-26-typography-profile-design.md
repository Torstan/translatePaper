# Typography Profile Design

Date: 2026-06-26

## Context

The current PDF translation renderer can produce readable pages, but typography is not globally stable. Some pages adapt font sizes from local source boxes or compact text independently, so body text, headings, and paragraph rhythm can vary across pages. This creates a book that reads like many individually fitted pages rather than one designed document.

The new requirement is to add a full-document typography step before rendering:

- Scan the entire source PDF first.
- Infer one unified typography profile for the whole book.
- Use that profile for every page and every role.
- Allow manual overrides.
- If a page cannot fit with the profile, use a controlled exception ladder.
- After translation, report every typography exception.

## Confirmed Decisions

- Approach: full-document `Typography Profile` plus locked rendering and exception reporting.
- Scope: strict whole-book consistency. A role such as `body` or `section_heading` has one font size across the entire book.
- Source of defaults: infer from the English source PDF, then apply a document-wide Chinese optical correction.
- Override support: automatic profile values can be overridden by a user-provided file.
- Fit policy: keep target font sizes first. If text does not fit, expand layout space and compact spacing before reducing font size.
- Reporting: every deviation from the target profile must appear in the final report.

## Goals

1. Establish a single effective typography profile before page rendering begins.
2. Make font size, line height, paragraph spacing, and related rhythm values role-based rather than page-based.
3. Make local deviations explicit and auditable.
4. Preserve current render-plan and QA architecture where possible.
5. Keep source figures, code, formulas, tables, and visual clips outside translated typography decisions.

## Non-Goals

- Do not redesign the whole renderer as a continuous book layout engine.
- Do not change page count or flow normal prose across PDF pages.
- Do not introduce model-based decisions for deterministic typography inference.
- Do not add document-specific exceptions keyed to this book, title, or page numbers.

## Pipeline

The pipeline gains a preflight typography phase after source extraction and before translation/render planning.

1. Extract source pages, source blocks, bbox lines, and page size.
2. Run `typography_profile` over the full document.
3. Classify typography roles from source geometry and text patterns.
4. Infer source role statistics and convert them to Chinese target values.
5. Write `typography-profile.auto.json` and `typography-profile.md`.
6. Merge an optional override file.
7. Write `typography-profile.effective.json`.
8. Pass the effective profile into page batching and render-plan construction.
9. Render each page using profile values.
10. Write `typography-exceptions.json` and `typography-exceptions.md`.

## Typography Profile

The effective profile is the single source of truth for translated text styling.

```json
{
  "schema_version": 1,
  "document": {
    "source_path": "/Users/ginobili/study/paper/english/A Philosophy of Software Design - John Ousterhout.pdf",
    "page_count": 207,
    "page_size": [612.0, 792.0]
  },
  "source_analysis": {
    "role_stats": {
      "body": {
        "sample_count": 1200,
        "source_line_height_median": 11.8,
        "source_block_width_median": 468.0,
        "confidence": "high"
      }
    },
    "excluded_sample_counts": {
      "visual": 80,
      "reference": 120
    }
  },
  "styles": {
    "body": {
      "font_size": 10.8,
      "line_height_factor": 1.30,
      "paragraph_spacing": 4.0,
      "min_line_height_factor": 1.15,
      "min_paragraph_spacing": 1.5,
      "min_font_size": 10.0
    },
    "chapter_label": {},
    "chapter_title": {},
    "section_heading": {},
    "subsection_heading": {},
    "callout_heading": {},
    "callout_body": {},
    "reference": {},
    "footer": {}
  },
  "layout": {
    "text_column_width": 468.0,
    "body_flow_min_gap": 3.0,
    "body_flow_target_gap": 8.0,
    "body_flow_max_gap": 12.0
  },
  "override": {
    "path": null,
    "applied": []
  }
}
```

Profile values have three roles:

- Target values: normal rendering uses these.
- Spacing lower bounds: line height and paragraph spacing can compact to these before font size changes.
- Font lower bounds: font size reduction is allowed only as the last fit exception and only down to this value.

## Role Inference

The profile builder uses deterministic source signals:

- `chapter_label`: short standalone chapter labels near chapter-page top regions.
- `chapter_title`: large short title text following a chapter label.
- `section_heading`: numbered headings such as `10.1 Why exceptions add complexity`.
- `subsection_heading`: deeper numbered headings such as `10.1.1 Returning special values`.
- `body`: prose blocks with normal text density and main-column widths.
- `callout_heading`: callout seeds such as `Red Flag:` and equivalent warning-like labels.
- `callout_body`: prose associated with a callout block.
- `reference`: existing reference classification.
- `footer`: page numbers, headers, footers, and journal footer text.

Code, formulas, figures, tables, and unknown protected visual regions are excluded from typography inference for translated prose.

## Source Statistics And Chinese Mapping

The profile builder should not rely only on PDF font metadata. Source PDFs often expose inconsistent font names and sizes. Instead it should prefer geometry-derived statistics:

- line height from bbox lines,
- block height divided by source line count,
- block width for layout column inference,
- vertical gaps between adjacent prose blocks for paragraph rhythm.

For each role, take robust statistics over the whole document:

- median or mode-like cluster for source line height,
- median source block width,
- interquartile range for outlier filtering,
- sample count and confidence.

Then map to Chinese target values with one document-wide optical correction:

- Chinese body font size is derived from the source body visual size multiplied by a single correction factor.
- Heading sizes preserve the source role ratios relative to body, with minimum visible hierarchy gaps.
- Chinese body line height targets a comfortable range, typically around 1.25 to 1.35.
- Heading line height is slightly tighter than body.
- Paragraph spacing is inferred from source rhythm, then normalized to one role-level target and lower bound.

The generated markdown report must show enough evidence to explain the values:

- sample counts,
- source medians,
- correction factor,
- final target values,
- excluded outlier counts.

## Rendering Contract

Render-plan construction receives the effective profile. A text item must identify its typography role and use the role values from the profile.

Normal rendering:

- uses `font_size`,
- uses `line_height_factor`,
- uses `paragraph_spacing`,
- records the profile role in the render item and ledger.

The previous local source-adapted font behavior becomes an exception path, not a normal success path. If a page uses a value different from the profile target, the render item and ledger must record a `typography_exception`.

Style validation changes from checking `DOCUMENT_STYLES` to checking the effective profile.

Visual QA changes from comparing body text only to a page-local baseline to comparing text items to the document profile. Page-local consistency may still be useful as a secondary diagnostic, but it cannot replace profile validation.

## Fit Exception Ladder

When translated text does not fit with the target profile, the renderer tries these steps in order:

1. `box_expanded`: expand the text box into safe whitespace without crossing protected regions.
2. `flow_rebalanced`: rebalance adjacent body flow boxes while preserving reading order.
3. `line_height_compacted`: reduce line height down to the role's `min_line_height_factor`.
4. `paragraph_spacing_compacted`: reduce paragraph spacing down to the role's `min_paragraph_spacing`.
5. `font_size_reduced`: reduce font size down to the role's `min_font_size`.
6. `text_fit_failed`: report failure if text still cannot fit.

All steps that change the target profile rendering must be recorded. A page may still be generated when exceptions occur, but the final reports must make the deviations visible.

## Reports

The typography phase writes these artifacts in the job directory:

- `typography-profile.auto.json`
- `typography-profile.effective.json`
- `typography-profile.md`
- `typography-exceptions.json`
- `typography-exceptions.md`

`typography-exceptions.md` groups exceptions by severity and page. Each item includes:

- page number,
- source ids,
- typography role,
- target font size, line height, and paragraph spacing,
- actual font size, line height, and paragraph spacing,
- exception steps used,
- final status.

Font size reduction is always highlighted because it changes the visual hierarchy more than spacing compaction.

## CLI

The parallel translation entrypoint should support:

- default behavior: build and use a typography profile,
- `--typography-profile PATH`: use an existing effective profile,
- `--typography-override PATH`: merge user overrides into the auto profile,
- `--typography-report-only`: generate profile artifacts without translating or rendering,
- `--no-typography-profile`: keep legacy behavior for debugging only.

## Override File

Overrides may set any style or layout field in the profile. The merger records each applied override with:

- JSON path,
- automatic value,
- override value.

An override cannot remove required roles. Invalid values fail early before rendering.

## QA And Validation

Required validation:

- Every translated text item has a typography role.
- Every role exists in the effective profile.
- Normal text items match profile target values.
- Any deviation has a `typography_exception`.
- Exception steps follow the ladder order.
- Font size reduction does not go below role `min_font_size`.
- Text fit validation and final drawing use the same profile values.
- Final reports include all exceptions.

Visual QA should surface:

- inconsistent role font sizes across pages,
- unreported typography deviations,
- body text rendered with a page-local size instead of the profile target,
- font size reductions.

## Tests

Add focused tests before implementation:

- Profile inference fixture with multiple pages and stable role values.
- Override merge test that records automatic and override values.
- Render-plan test proving body font size is identical across pages.
- Render-plan test proving heading font size is identical across pages.
- Exception ladder tests for box expansion, flow rebalance, line-height compaction, paragraph-spacing compaction, font-size reduction, and final failure.
- QA report test proving typography exceptions appear in markdown and JSON.
- Regression fixture covering pages like chapter pages, normal body pages, callout pages, code/comment pages, and bottom headings.

Existing rendering fixtures should be updated only where the new typography contract intentionally changes behavior.

## Compatibility

`DOCUMENT_STYLES` may remain as a fallback default and test helper, but production rendering should use the effective profile once it exists.

Legacy behavior remains available through `--no-typography-profile` for debugging. It should not be the default path.

## Risks

- Role inference may misclassify unusual headings. The profile report must expose low-confidence roles and sample counts.
- A strict whole-book profile can reveal more fit failures than the current per-page adaptation. This is expected and should be reported rather than hidden.
- Override files can create bad typography. Schema validation and early fit diagnostics should catch impossible values.
- Existing tests that assumed `DOCUMENT_STYLES` as the only style source will need intentional updates.

## Success Criteria

- A full book run produces a profile before rendering.
- Body text uses one font size across the book unless an exception is recorded.
- Heading roles use stable font sizes across the book.
- Pages that need local deviation list it in typography exception reports.
- The rendered book no longer has page-to-page font drift for the same text role.
