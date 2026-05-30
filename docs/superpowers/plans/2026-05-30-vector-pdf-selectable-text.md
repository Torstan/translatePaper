# Vector PDF Selectable Text Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make vector-mode translated PDFs keep translated prose as selectable text instead of full-page raster images, while preventing text, numbers, formulas, tables, and source image clips from overlapping.

**Architecture:** Keep visual content as explicit `original_image_clip` regions and keep normal translated prose as vector text. Fix the current strict-vector blockers at the deterministic layout layer: expand tight text boxes only when safe, cap formula/visual clips before translated prose, and relax strict QA only for source text that does not semantically require Chinese. The render plan remains the contract: coverage, ownership, layout, text fit, and visual QA must expose any fallback.

**Tech Stack:** Python `unittest`, PyMuPDF via repo `vendor`, Poppler tools (`pdfimages`, `pdftotext`, `pdftoppm`), existing modules `layout.py`, `translate_pdf_via_codex.py`, `regions.py`, `ownership.py`, `render_plan.py`, `qa_visual.py`.

---

## File Structure

- Modify `layout.py`
  - Owns vector text wrapping, fit metrics, safe vertical expansion, lane rebalancing, and final text-fit validation.
  - The fix for tight one-line translated prose should live here because text fit validation and drawing already share this module.
- Modify `translate_pdf_via_codex.py`
  - Owns render-plan construction and vector render validation order.
  - It should call `expand_text_boxes_to_fit()` and `rebalance_body_text_flows()` before `validate_plan_layout()` and `validate_plan_text_fit()`.
  - It also owns final visual region clipping helpers used by ownership construction.
- Modify `classify.py` or `translate_pdf_via_codex.py`
  - Only if strict QA still flags author lists or URL blocks as missing Chinese.
  - Prefer tightening `source_requires_chinese_translation()` in `classify.py` if the rule is generally about text semantics.
- Modify `tests/test_layout.py`
  - Focused unit tests for safe text-box expansion.
- Modify `tests/test_render_plan.py`
  - Focused fixture-style tests for formula visual clipping and strict QA metadata exemptions.
- Optional modify `tests/test_qa_visual.py`
  - Only if visual QA needs a regression around full-page image detection or vector text presence.
- Keep generated experiment data under `test/vector-smoke/`
  - This directory is for manual smoke outputs and should remain untracked unless a specific fixture is intentionally added.

---

### Task 1: Add a Failing Test for Tight Translated Body Text Expansion

**Files:**
- Modify: `tests/test_layout.py`
- Modify later: `layout.py`

- [ ] **Step 1: Write the failing test**

Append this test method to `LayoutExtractionModuleTests` in `tests/test_layout.py`:

```python
    def test_expand_text_boxes_to_fit_grows_tight_body_line_into_available_space(self):
        fitz = FakeFitz()
        plan = pdf.PageRenderPlan(page_num=2)
        plan.items.append(
            pdf.RenderItem(
                "translated_text",
                ["p002b0004"],
                (70.0, 120.0, 520.0, 127.0),
                text="该名称源自 la tortuga，这是西班牙语中表示 turtle 的词。",
                font_size=layout.BODY_FONT_SIZE,
                style_name="body",
            )
        )
        plan.items.append(
            pdf.RenderItem(
                "translated_text",
                ["p002b0005"],
                (70.0, 170.0, 520.0, 190.0),
                text="下一段中文。",
                font_size=layout.BODY_FONT_SIZE,
                style_name="body",
            )
        )

        before = plan.items[0].bbox
        layout.expand_text_boxes_to_fit(plan, page_size=(612.0, 792.0), fitz=fitz)

        expanded = plan.items[0].bbox
        self.assertEqual(expanded[:3], before[:3])
        self.assertGreater(expanded[3], before[3])
        self.assertLess(expanded[3], plan.items[1].bbox[1])
        self.assertEqual(layout.validate_plan_text_fit(plan, fitz=fitz), [])
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
PYTHONPYCACHEPREFIX=~/tmp/translatePaper_pycache python3 -m unittest tests.test_layout.LayoutExtractionModuleTests.test_expand_text_boxes_to_fit_grows_tight_body_line_into_available_space -v
```

Expected: `FAIL` because the current plan construction path does not reliably expand the tight text item before validation, or because the expansion does not produce enough height with the same fit metrics.

- [ ] **Step 3: Confirm the existing overflow guard still fails**

Run:

```bash
PYTHONPYCACHEPREFIX=~/tmp/translatePaper_pycache python3 -m unittest tests.test_layout.LayoutExtractionModuleTests.test_text_fit_validation_rejects_unrecorded_font_shrinking -v
```

Expected: `OK`. This protects the invariant that unsafe or impossible overflow still fails rather than silently shrinking text or hiding it as an image.

---

### Task 2: Wire Safe Text Expansion Into Vector Render Plans

**Files:**
- Modify: `translate_pdf_via_codex.py`
- Test: `tests/test_layout.py`

- [ ] **Step 1: Add a render-plan normalization helper**

In `translate_pdf_via_codex.py`, add this helper near the existing validation helpers before `validate_document_quality()`:

```python
def normalize_vector_text_layout(plan: PageRenderPlan, page_size, fitz=None) -> None:
    """Apply deterministic text layout repairs before validating or drawing vector text."""
    expand_text_boxes_to_fit(plan, page_size, fitz=fitz)
    rebalance_body_text_flows(plan, page_size, fitz=fitz)
    expand_text_boxes_to_fit(plan, page_size, fitz=fitz)
```

- [ ] **Step 2: Use the helper in document quality validation**

In `validate_document_quality()`, immediately after `build_page_render_plan(...)` and before `validate_plan_coverage(...)`, insert:

```python
        normalize_vector_text_layout(plan, page_size, fitz=fitz)
```

- [ ] **Step 3: Use the helper in vector PDF rendering**

In `write_vector_pdf()`, immediately after `build_page_render_plan(...)` and before constructing `validation_results`, insert:

```python
        normalize_vector_text_layout(plan, (page_rect.width, page_rect.height), fitz=fitz)
```

- [ ] **Step 4: Run the tight text expansion test again**

Run:

```bash
PYTHONPYCACHEPREFIX=~/tmp/translatePaper_pycache python3 -m unittest tests.test_layout.LayoutExtractionModuleTests.test_expand_text_boxes_to_fit_grows_tight_body_line_into_available_space -v
```

Expected: `OK`.

- [ ] **Step 5: Run focused existing layout tests**

Run:

```bash
PYTHONPYCACHEPREFIX=~/tmp/translatePaper_pycache python3 -m unittest tests.test_layout -v
```

Expected: `OK`.

---

### Task 3: Add a Failing Test for Formula Clips That Overcapture Following Prose

**Files:**
- Modify: `tests/test_render_plan.py`
- Modify later: `translate_pdf_via_codex.py`

- [ ] **Step 1: Write the failing test**

Append this method to `RenderPlanClassificationTests` in `tests/test_render_plan.py`:

```python
    def test_formula_visual_clip_is_capped_before_following_translated_prose(self):
        blocks = [
            block("p003b0002", 3, "Reasoning", x0=118.262, y0=123.702, x1=180.0, y1=137.0),
            block("p003b0003", 3, "Aha Moment", x0=118.262, y0=150.0, x1=210.0, y1=164.0),
            block("p003b0004", 3, "reward = accuracy + format", x0=189.365, y0=187.588, x1=405.906, y1=212.645),
            block("p003b0005", 3, "(2)", x0=512.783, y0=195.681, x1=525.503, y1=206.557),
            block("p003b0006", 3, "where accuracy is computed by exact matching.", x0=118.262, y0=214.0, x1=490.0, y1=219.0),
            block(
                "p003b0007",
                3,
                "The training process then continues with reinforcement learning on reasoning prompts.",
                x0=70.408,
                y0=220.722,
                x1=524.408,
                y1=246.033,
            ),
        ]
        classes = {
            "p003b0002": "formula_region",
            "p003b0003": "formula_region",
            "p003b0004": "formula_region",
            "p003b0005": "formula_region",
            "p003b0006": "formula_region",
            "p003b0007": "body",
        }
        region = {
            "source_ids": ["p003b0002", "p003b0003", "p003b0004", "p003b0005", "p003b0006"],
            "bbox": (81.277, 123.038, 526.235, 283.33),
        }

        final_bbox, _mixed_body_item = pdf.final_visual_region_bbox(
            blocks,
            classes,
            region,
            page_size=(612.0, 792.0),
            page_num=3,
            visual_ids={"p003b0002", "p003b0003", "p003b0004", "p003b0005", "p003b0006"},
        )

        self.assertLessEqual(final_bbox[3], blocks[-1]["yMin"] - pdf.TEXT_PROTECTED_GAP_PT)
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
PYTHONPYCACHEPREFIX=~/tmp/translatePaper_pycache python3 -m unittest tests.test_render_plan.RenderPlanClassificationTests.test_formula_visual_clip_is_capped_before_following_translated_prose -v
```

Expected: `FAIL` with the clip bottom still extending into `p003b0007`.

---

### Task 4: Cap Formula Visual Clips Before Following Translated Text

**Files:**
- Modify: `translate_pdf_via_codex.py`
- Test: `tests/test_render_plan.py`

- [ ] **Step 1: Apply the existing following-text cap to formula-only regions**

In `final_visual_region_bbox()` in `translate_pdf_via_codex.py`, replace:

```python
    if not formula_only:
        region_bbox = cap_visual_bbox_before_following_text(
            visual_source_bbox,
            region_bbox,
            blocks,
            classes,
            visual_ids,
        )
```

with:

```python
    region_bbox = cap_visual_bbox_before_following_text(
        visual_source_bbox,
        region_bbox,
        blocks,
        classes,
        visual_ids,
    )
```

This makes formulas obey the same invariant as figures: a visual clip must not swallow nearby translated prose.

- [ ] **Step 2: Run the formula clip test**

Run:

```bash
PYTHONPYCACHEPREFIX=~/tmp/translatePaper_pycache python3 -m unittest tests.test_render_plan.RenderPlanClassificationTests.test_formula_visual_clip_is_capped_before_following_translated_prose -v
```

Expected: `OK`.

- [ ] **Step 3: Run nearby visual-region tests**

Run:

```bash
PYTHONPYCACHEPREFIX=~/tmp/translatePaper_pycache python3 -m unittest tests.test_render_plan.RenderPlanClassificationTests.test_final_visual_clip_does_not_intrude_into_adjacent_body_column tests.test_render_plan.RenderPlanClassificationTests.test_build_batches_uses_bbox_lines_for_formula_final_clip_exclusion -v
```

Expected: `OK`.

---

### Task 5: Add Strict QA Tests for Metadata That May Stay English

**Files:**
- Modify: `tests/test_render_plan.py`
- Modify later: `classify.py` or `translate_pdf_via_codex.py`

- [ ] **Step 1: Write author-list and URL QA tests**

Append these methods to `RenderPlanClassificationTests` in `tests/test_render_plan.py`:

```python
    def test_translation_quality_allows_author_list_original_selectable_text(self):
        blocks = [
            block(
                "p001b0002",
                1,
                "Timo Schick Jane Dwivedi-Yu Roberto Dessì Roberta Raileanu Maria Lomeli Eric Hambro",
                x0=113.978,
                y0=179.887,
                x1=498.524,
                y1=205.709,
            )
        ]
        plan = pdf.PageRenderPlan(page_num=1)
        plan.items.append(
            pdf.RenderItem(
                "original_selectable_text",
                ["p001b0002"],
                (113.978, 179.887, 498.524, 205.709),
                text=blocks[0]["text"],
                font_size=pdf.BODY_FONT_SIZE,
                style_name="body",
                fallback_reason="untranslated_fallback_original",
            )
        )
        plan.ledger.append(pdf.CoverageEntry("p001b0002", "body", "original_selectable_text", True, "untranslated_fallback_original"))

        self.assertEqual(pdf.validate_plan_translation_quality(1, blocks, {}, plan), [])

    def test_translation_quality_allows_url_original_selectable_text(self):
        blocks = [
            block(
                "p001b0004",
                1,
                "https://qwenlm.github.io/blog/qwen3/ https://github.com/QwenLM/Qwen3",
                x0=90.0,
                y0=150.0,
                x1=520.0,
                y1=170.0,
            )
        ]
        plan = pdf.PageRenderPlan(page_num=1)
        plan.items.append(
            pdf.RenderItem(
                "original_selectable_text",
                ["p001b0004"],
                (90.0, 150.0, 520.0, 170.0),
                text=blocks[0]["text"],
                font_size=pdf.BODY_FONT_SIZE,
                style_name="body",
                fallback_reason="untranslated_fallback_original",
            )
        )
        plan.ledger.append(pdf.CoverageEntry("p001b0004", "body", "original_selectable_text", True, "untranslated_fallback_original"))

        self.assertEqual(pdf.validate_plan_translation_quality(1, blocks, {}, plan), [])
```

- [ ] **Step 2: Run the tests to verify current behavior**

Run:

```bash
PYTHONPYCACHEPREFIX=~/tmp/translatePaper_pycache python3 -m unittest tests.test_render_plan.RenderPlanClassificationTests.test_translation_quality_allows_author_list_original_selectable_text tests.test_render_plan.RenderPlanClassificationTests.test_translation_quality_allows_url_original_selectable_text -v
```

Expected: URL may already pass; author-list may fail. If both pass, no production change is needed for this task.

- [ ] **Step 3: If the author-list test fails, make the semantic rule deterministic**

In `classify.py`, update `source_requires_chinese_translation()` so first-page-style name lists without function words do not require Chinese. Keep the logic general and not keyed to a paper:

```python
def source_requires_chinese_translation(text: str) -> bool:
    cleaned = strip_journal_footer_lines(text)
    if is_non_prose_identifier_text(cleaned):
        return False
    words = latin_words(cleaned)
    if len(words) < 4:
        return False
    if english_function_word_count(cleaned) == 0 and not re.search(r"[.!?]\s+[A-Z]", cleaned):
        return False
    return len(cleaned) >= 30 or english_function_word_count(cleaned) >= 1
```

- [ ] **Step 4: Run focused classification and QA tests**

Run:

```bash
PYTHONPYCACHEPREFIX=~/tmp/translatePaper_pycache python3 -m unittest tests.test_render_plan.RenderPlanClassificationTests.test_classify_module_translation_eligibility_matches_pipeline tests.test_render_plan.RenderPlanClassificationTests.test_translation_quality_allows_author_list_original_selectable_text tests.test_render_plan.RenderPlanClassificationTests.test_translation_quality_allows_url_original_selectable_text -v
```

Expected: `OK`.

---

### Task 6: Re-run the Three Deterministic Vector Smoke Samples

**Files:**
- Generated only: `test/vector-smoke/output*/`, `test/vector-smoke/work*/`, `test/vector-smoke/checks/`
- Do not commit generated smoke outputs unless they are intentionally promoted to fixtures.

- [ ] **Step 1: Run Toolformer full vector strict QA**

Run:

```bash
python3 /mnt/d/ginobili/code/translatePaper/translate_pdf_parallel.py \
  --source-dir /mnt/d/ginobili/code/llmPapers/English \
  --target-dir /mnt/d/ginobili/code/translatePaper/test/vector-smoke/output \
  --work-dir /mnt/d/ginobili/code/translatePaper/test/vector-smoke/work \
  --include 'NeurIPS-2023-toolformer-language-models-can-teach-themselves-to-use-tools-Paper-Conference.pdf' \
  --force --render-mode vector --document-workers 1 --page-workers 2 \
  --batch-chars 7000 --strict-qa --continue-on-error
```

Expected: command exits `0`; no `text_fit_errors`; no deterministic QA report errors for author metadata.

- [ ] **Step 2: Run Qwen3 pages 1-3 vector strict QA**

Run:

```bash
python3 /mnt/d/ginobili/code/translatePaper/translate_pdf_parallel.py \
  --source-dir /mnt/d/ginobili/code/llmPapers/English \
  --target-dir /mnt/d/ginobili/code/translatePaper/test/vector-smoke/output-qwen3-p1-3 \
  --work-dir /mnt/d/ginobili/code/translatePaper/test/vector-smoke/work-qwen3-p1-3 \
  --include 'qwen3_technical_report.pdf' \
  --force --render-mode vector --document-workers 1 --page-workers 2 \
  --batch-chars 7000 --strict-qa --page-start 1 --page-end 3 --continue-on-error
```

Expected: command exits `0`; no strict QA error for URL metadata.

- [ ] **Step 3: Run DeepSeek R1 pages 1-3 vector strict QA**

Run:

```bash
python3 /mnt/d/ginobili/code/translatePaper/translate_pdf_parallel.py \
  --source-dir /mnt/d/ginobili/code/llmPapers/English \
  --target-dir /mnt/d/ginobili/code/translatePaper/test/vector-smoke/output-deepseek-p1-3 \
  --work-dir /mnt/d/ginobili/code/translatePaper/test/vector-smoke/work-deepseek-p1-3 \
  --include 'deepseek_r1.pdf' \
  --force --render-mode vector --document-workers 1 --page-workers 2 \
  --batch-chars 7000 --strict-qa --page-start 1 --page-end 3 --continue-on-error
```

Expected: command exits `0`; no `visual_clip_overcaptures_translated_component` ownership issue on page 3.

---

### Task 7: Inspect Vector Outputs for Text Selectability and Image Size

**Files:**
- Read generated PDFs in `test/vector-smoke/output*/`
- Write PNG checks to `test/vector-smoke/checks/`

- [ ] **Step 1: Confirm Toolformer is not full-page raster**

Run:

```bash
pdfimages -list /mnt/d/ginobili/code/translatePaper/test/vector-smoke/output/NeurIPS-2023-toolformer-language-models-can-teach-themselves-to-use-tools-Paper-Conference-Chinese.pdf
```

Expected: image list does not contain one image per page at full page dimensions.

- [ ] **Step 2: Confirm Toolformer Chinese text extracts**

Run:

```bash
pdftotext /mnt/d/ginobili/code/translatePaper/test/vector-smoke/output/NeurIPS-2023-toolformer-language-models-can-teach-themselves-to-use-tools-Paper-Conference-Chinese.pdf - | head -80
```

Expected: output includes readable Chinese text from title, abstract, and body.

- [ ] **Step 3: Render sample pages to PNG**

Run:

```bash
mkdir -p /mnt/d/ginobili/code/translatePaper/test/vector-smoke/checks/toolformer-full \
  /mnt/d/ginobili/code/translatePaper/test/vector-smoke/checks/qwen3-p1-3 \
  /mnt/d/ginobili/code/translatePaper/test/vector-smoke/checks/deepseek-p1-3
pdftoppm -png -f 1 -l 3 /mnt/d/ginobili/code/translatePaper/test/vector-smoke/output/NeurIPS-2023-toolformer-language-models-can-teach-themselves-to-use-tools-Paper-Conference-Chinese.pdf /mnt/d/ginobili/code/translatePaper/test/vector-smoke/checks/toolformer-full/page
pdftoppm -png -f 1 -l 3 /mnt/d/ginobili/code/translatePaper/test/vector-smoke/output-qwen3-p1-3/qwen3_technical_report-Chinese.pdf /mnt/d/ginobili/code/translatePaper/test/vector-smoke/checks/qwen3-p1-3/page
pdftoppm -png -f 1 -l 3 /mnt/d/ginobili/code/translatePaper/test/vector-smoke/output-deepseek-p1-3/deepseek_r1-Chinese.pdf /mnt/d/ginobili/code/translatePaper/test/vector-smoke/checks/deepseek-p1-3/page
```

Expected: PNGs exist for pages 1-3 where available. Inspect the generated PNGs for text/image overlap, formula clipping, author/title scale, and table preservation.

---

### Task 8: Run Required Regression and Compilation Checks

**Files:**
- No source edits.
- Generated `__pycache__` must be redirected by `PYTHONPYCACHEPREFIX`.

- [ ] **Step 1: Run the required full regression suite**

Run:

```bash
PYTHONPYCACHEPREFIX=~/tmp/translatePaper_pycache python3 -m unittest \
  tests.test_qa_semantic tests.test_render_pdf tests.test_layout \
  tests.test_regions tests.test_render_plan tests.test_waitfree_regression \
  tests.test_pdf_render_fixtures tests.test_pdf_render_fixture_layout \
  tests.test_translate_pdf_parallel tests.test_qa_visual -v
```

Expected: `OK`. If any test is skipped because optional cached fixtures are absent, record the skip count and reason.

- [ ] **Step 2: Run Python compilation checks**

Run:

```bash
PYTHONPYCACHEPREFIX=~/tmp/translatePaper_pycache python3 -m py_compile \
  qa_semantic.py backtranslate_check.py render_pdf.py layout.py regions.py \
  classify.py render_plan.py qa_visual.py translate_pdf_via_codex.py \
  translate_pdf_parallel.py tests/test_qa_semantic.py tests/test_render_pdf.py \
  tests/test_layout.py tests/test_regions.py tests/test_render_plan.py \
  tests/test_waitfree_regression.py tests/test_pdf_render_fixtures.py \
  tests/test_pdf_render_fixture_layout.py tests/test_translate_pdf_parallel.py \
  tests/test_qa_visual.py tests/pdf_render_fixture_runner.py
```

Expected: exit code `0`.

---

### Task 9: Review Diff and Commit Only Intended Source/Test Changes

**Files:**
- Commit source and tests only.
- Leave `test/vector-smoke/`, `work/`, `output/`, `tmp/`, `vendor/`, `.vscode/`, `.codex/`, and unrelated untracked files unstaged unless explicitly requested.

- [ ] **Step 1: Review status**

Run:

```bash
git status --short
```

Expected: source/test files changed plus existing unrelated untracked directories.

- [ ] **Step 2: Review source diff**

Run:

```bash
git diff -- layout.py translate_pdf_via_codex.py classify.py tests/test_layout.py tests/test_render_plan.py tests/test_qa_visual.py
```

Expected: diff only includes the deterministic vector text/layout fixes and focused tests from this plan.

- [ ] **Step 3: Stage intended files**

Run:

```bash
git add layout.py translate_pdf_via_codex.py classify.py tests/test_layout.py tests/test_render_plan.py tests/test_qa_visual.py docs/superpowers/plans/2026-05-30-vector-pdf-selectable-text.md
```

If a listed file was not changed, `git add` is still safe.

- [ ] **Step 4: Commit**

Run:

```bash
git commit -m "Fix vector PDF text layout and visual clipping"
```

Expected: commit succeeds. If regression commands did not pass, do not commit; report the exact failing command and first failing error.

---

## Self-Review

**Spec coverage:** The plan covers the user requirement to stop producing one full-page image per page by preserving normal prose as vector text, while keeping formulas/tables/images as protected clips. It includes the three observed blockers: Toolformer tight text fit, DeepSeek formula overcapture, and strict QA metadata false positives. It also includes deterministic smoke checks for file size/image inventory and extracted text.

**Placeholder scan:** No task uses TBD/TODO/fill-in steps. Each code-changing step includes the exact target file, code snippet, command, and expected result.

**Type consistency:** All snippets use existing repo types and functions: `PageRenderPlan`, `RenderItem`, `CoverageEntry`, `expand_text_boxes_to_fit`, `rebalance_body_text_flows`, `validate_plan_text_fit`, `validate_plan_translation_quality`, and `final_visual_region_bbox`.
