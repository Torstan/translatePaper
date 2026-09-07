# translatePaper

Translates English PDF papers to Chinese PDFs. It extracts text, uses Codex CLI, and renders Chinese back to pages. Supports batch jobs, resume, and QA. It keeps the same page id.

## Scripts

- `translate_pdf_parallel.py`: batch entry.
- `translate_pdf_via_codex.py`: single-PDF pipeline.
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

`render_translated_pdf` applies boundary repairs once and returns the translations
used for rendering and QA. The lower-level `write_vector_pdf` draws the supplied
final translations without applying cached repairs again. Layout planning and
visual QA share the same protected-region overlap predicate (over 6pt vertically
and over 5% of the smaller box's area).

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
JSON plans remain available for offline visual QA. Raster diagnostics check the
source layout and do not represent the raster drawing's final layout.

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
batch; strict mode fails jobs with coverage, layout, visual, clipping, overlap,
or style errors instead of reporting a defective PDF as translated.

Drawing and offline visual QA enforce the same style roles, absolute font sizes,
and explicit style exceptions. An arbitrary fallback reason does not exempt text
from the style policy.

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
