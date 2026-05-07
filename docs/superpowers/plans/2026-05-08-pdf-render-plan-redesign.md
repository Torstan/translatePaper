# PDF Render Plan Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the PDF translation renderer so every source block is explicitly classified, planned, rendered, and QA-checked without silent content loss.

**Architecture:** Keep the existing translation cache and command-line workflow, but insert a render-plan layer between extracted blocks/translations and PDF drawing. The render plan classifies each block, creates a ledger entry, registers protected regions before text layout, and falls back to compressed source image clips when translation or layout is unsafe.

**Tech Stack:** Python 3, standard-library `dataclasses`/`unittest`, existing PyMuPDF `fitz` vendor import, Poppler CLI tools already used by the repo, existing `translate_pdf_via_codex.py`.

---

## File Structure

- Modify `translate_pdf_via_codex.py`
  - Add render-plan dataclasses near the vector-rendering helpers.
  - Add bbox line parsing helpers next to `parse_bbox`.
  - Add block classification and visual-region grouping helpers before `build_batches`.
  - Replace unsafe nested-block filtering in `write_vector_pdf` with render-plan iteration.
  - Add coverage and layout QA helpers that can be unit-tested without writing a PDF.
- Create `tests/test_render_plan.py`
  - Standard-library `unittest` tests for classification, ledger coverage, visual-region grouping, reference handling, and wrapping.
- Create `tests/test_waitfree_regression.py`
  - Data-level regression tests using `work/jobs/wait-free-synchronization/source_pages.json` and `translations.json`.
- Create `tests/__init__.py`
  - Makes test modules importable via `python3 -m unittest`.

## Task 1: Add Render-Plan Test Harness

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/test_render_plan.py`
- Modify: `translate_pdf_via_codex.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/__init__.py` as an empty file.

Create `tests/test_render_plan.py`:

```python
import unittest

import translate_pdf_via_codex as pdf


def block(block_id, page, text, x0=100, y0=100, x1=400, y1=120, preserve_image=False):
    data = {
        "id": block_id,
        "page": page,
        "block_index": int(block_id[-4:]) if block_id[-4:].isdigit() else 1,
        "xMin": x0,
        "yMin": y0,
        "xMax": x1,
        "yMax": y1,
        "text": text,
    }
    if preserve_image:
        data["preserve_image"] = True
    return data


class RenderPlanClassificationTests(unittest.TestCase):
    def test_classifies_heading_body_page_number_and_reference(self):
        blocks = [
            block("p001b0001", 1, "1. INTRODUCTION", y0=80, y1=92),
            block("p001b0002", 1, "This paper gives a wait-free implementation.", y0=100, y1=130),
            block("p001b0003", 1, "126", y0=760, y1=770),
            block("p001b0004", 1, "REFERENCES", y0=500, y1=512),
            block("p001b0005", 1, "1. LAMPORT, L. Concurrent reading and writing.", y0=520, y1=535),
        ]

        plan = pdf.build_page_render_plan(1, blocks, {}, page_size=(623, 801), bbox_lines=None)
        classes = {entry.block_id: entry.classification for entry in plan.ledger}

        self.assertEqual(classes["p001b0001"], "heading")
        self.assertEqual(classes["p001b0002"], "body")
        self.assertEqual(classes["p001b0003"], "page_number")
        self.assertEqual(classes["p001b0004"], "reference")
        self.assertEqual(classes["p001b0005"], "reference")

    def test_visual_caption_region_becomes_single_image_clip(self):
        blocks = [
            block("p003b0001", 3, "Fig. 1. Impossibility and universality hierarchy.", x0=180, y0=70, x1=420, y1=195),
            block("p003b0002", 3, "Consensus", x0=185, y0=80, x1=240, y1=90),
            block("p003b0003", 3, "Number", x0=185, y0=95, x1=220, y1=105),
            block("p003b0004", 3, "Normal body starts after the figure.", x0=126, y0=215, x1=486, y1=250),
        ]

        plan = pdf.build_page_render_plan(3, blocks, {"p003b0004": "图后正文。"}, page_size=(623, 801), bbox_lines=None)
        visual_items = [item for item in plan.items if item.kind == "original_image_clip"]
        body_items = [item for item in plan.items if item.kind == "translated_text"]

        self.assertEqual(len(visual_items), 1)
        self.assertEqual(visual_items[0].source_ids, ["p003b0001", "p003b0002", "p003b0003"])
        self.assertEqual(len(body_items), 1)
        self.assertEqual(body_items[0].source_ids, ["p003b0004"])

    def test_untranslated_body_uses_original_text_fallback(self):
        blocks = [
            block("p004b0005", 4, "(2) In(A) is a set of input events,", x0=135, y0=207, x1=282, y1=216),
        ]

        plan = pdf.build_page_render_plan(4, blocks, {}, page_size=(622, 798), bbox_lines=None)
        item = plan.items[0]
        entry = plan.ledger[0]

        self.assertEqual(item.kind, "original_selectable_text")
        self.assertEqual(item.text, "(2) In(A) is a set of input events,")
        self.assertEqual(entry.fallback_reason, "missing_translation")

    def test_formula_after_assertion_is_preserved_as_image_clip(self):
        blocks = [
            block("p015b0006", 15, "To show consistency, we use the following assertions:", x0=135, y0=257, x1=385, y1=281),
            block("p015b0007", 15, "(P) = r[P, 1] = 0 ^ r[P, 2] = 0\nQ(P) = r[P, 2] = 1", x0=237, y0=283, x1=617, y1=311),
            block("p015b0008", 15, "g(P) = C(P) A (VQ > P)(Q).", x0=240, y0=474, x1=617, y1=491),
        ]

        plan = pdf.build_page_render_plan(15, blocks, {"p015b0006": "为证明一致性，我们使用以下断言："}, page_size=(623, 801), bbox_lines=None)
        image_ids = [source_id for item in plan.items if item.kind == "original_image_clip" for source_id in item.source_ids]

        self.assertIn("p015b0007", image_ids)
        self.assertIn("p015b0008", image_ids)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest tests.test_render_plan -v
```

Expected: failures or errors because `build_page_render_plan` and render-plan dataclasses do not exist yet.

- [ ] **Step 3: Add minimal render-plan dataclasses and initial planner**

In `translate_pdf_via_codex.py`, add `dataclass` and `field` to the imports:

```python
from dataclasses import dataclass, field
```

Then add this code after `block_overlap_area_pt`:

```python
@dataclass
class RenderItem:
    kind: str
    source_ids: list[str]
    bbox: tuple[float, float, float, float]
    text: str = ""
    font_size: float | None = None
    color: tuple[float, float, float] = VECTOR_BODY_COLOR
    fallback_reason: str = ""


@dataclass
class CoverageEntry:
    block_id: str
    classification: str
    render_kind: str
    rendered: bool
    fallback_reason: str = ""


@dataclass
class PageRenderPlan:
    page_num: int
    items: list[RenderItem] = field(default_factory=list)
    ledger: list[CoverageEntry] = field(default_factory=list)
    protected_boxes: list[tuple[float, float, float, float]] = field(default_factory=list)
```

Also add this first implementation before the existing `write_vector_pdf`:

```python
def build_page_render_plan(page_num: int, blocks, translations, page_size, bbox_lines=None) -> PageRenderPlan:
    plan = PageRenderPlan(page_num=page_num)
    for block in blocks:
        text = normalize_text(block.get("text", ""))
        if not text:
            continue
        bbox = (block["xMin"], block["yMin"], block["xMax"], block["yMax"])
        if is_page_number(text):
            plan.ledger.append(CoverageEntry(block["id"], "page_number", "skip_explicitly", True))
            continue
        translated = translations.get(block["id"])
        if translated:
            plan.items.append(RenderItem("translated_text", [block["id"]], bbox, text=translation_for_block(block, translations)))
            plan.ledger.append(CoverageEntry(block["id"], "body", "translated_text", True))
        else:
            plan.items.append(RenderItem("original_selectable_text", [block["id"]], bbox, text=text, fallback_reason="missing_translation"))
            plan.ledger.append(CoverageEntry(block["id"], "body", "original_selectable_text", True, "missing_translation"))
    return plan
```

- [ ] **Step 4: Run tests and verify the remaining failures are classification-specific**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest tests.test_render_plan -v
```

Expected: the import succeeds; failures now point to wrong classifications or missing visual grouping, not missing functions.

- [ ] **Step 5: Commit**

```bash
git add tests/__init__.py tests/test_render_plan.py translate_pdf_via_codex.py
git commit -m "test: add render plan coverage tests"
```

## Task 2: Implement Block Classification and Visual Grouping

**Files:**
- Modify: `translate_pdf_via_codex.py`
- Test: `tests/test_render_plan.py`

- [ ] **Step 1: Add classification helpers**

Replace the initial classification logic with these helpers near `build_page_render_plan`:

```python
def block_bbox(block) -> tuple[float, float, float, float]:
    return (block["xMin"], block["yMin"], block["xMax"], block["yMax"])


def bbox_union(boxes) -> tuple[float, float, float, float]:
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def bbox_intersects(a, b) -> bool:
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def bbox_center(box) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def bbox_contains_point(box, point) -> bool:
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def expanded_bbox(box, pad_x: float = 0.0, pad_y: float = 0.0):
    return (box[0] - pad_x, box[1] - pad_y, box[2] + pad_x, box[3] + pad_y)


def is_reference_heading(text: str) -> bool:
    return bool(re.fullmatch(r"(?i)references|bibliography", normalize_text(text)))


def starts_reference_item(text: str) -> bool:
    return bool(re.match(r"^\s*\d{1,3}\.\s+[A-Z]", normalize_text(text)))


def is_heading_text(text: str) -> bool:
    first = normalize_text(text).split("\n", 1)[0].strip()
    if is_reference_heading(first):
        return False
    if re.match(r"^\d+(?:\.\d+)*\.?\s+[A-Z][A-Za-z0-9 /&-]+$", first):
        return True
    if re.fullmatch(r"[A-Z][A-Z0-9 /&-]{3,80}", first) and len(first.split()) <= 8:
        return True
    return False


def is_title_block(block) -> bool:
    text = normalize_text(block.get("text", ""))
    return block.get("page") == 1 and block["yMin"] < 90 and len(text) <= 120 and "\n" not in text


def is_formula_or_code_block(text: str) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return False
    if is_formula_like(normalized):
        return True
    symbol_count = len(re.findall(r"[=^∀∃<>≤≥∧∨()[\],]", normalized))
    latin_count = len(re.findall(r"[A-Za-z]", normalized))
    return symbol_count >= 3 and latin_count <= max(30, len(normalized) * 0.8)
```

- [ ] **Step 2: Add visual region grouping**

Add:

```python
def build_visual_regions(blocks) -> list[dict]:
    regions = []
    consumed = set()
    sorted_blocks = sorted(blocks, key=lambda item: (item["yMin"], item["xMin"]))
    for block in sorted_blocks:
        if block["id"] in consumed:
            continue
        text = normalize_text(block.get("text", ""))
        if not text:
            continue
        is_visual_seed = should_preserve_as_image(block) or is_visual_caption(text) or is_formula_or_code_block(text)
        if not is_visual_seed:
            continue
        seed_box = block_bbox(block)
        if is_visual_caption(text):
            search = (seed_box[0] - 40.0, seed_box[1] - 60.0, seed_box[2] + 40.0, seed_box[3] + 16.0)
        elif is_formula_or_code_block(text):
            search = expanded_bbox(seed_box, pad_x=35.0, pad_y=18.0)
        else:
            search = expanded_bbox(seed_box, pad_x=10.0, pad_y=8.0)
        group = []
        for other in sorted_blocks:
            other_text = normalize_text(other.get("text", ""))
            if not other_text or other["id"] in consumed:
                continue
            other_box = block_bbox(other)
            if bbox_intersects(search, other_box) and bbox_contains_point(search, bbox_center(other_box)):
                short_fragment = len(other_text) <= 140
                visual_text = is_visual_caption(other_text) or is_formula_or_code_block(other_text) or should_preserve_as_image(other)
                if short_fragment or visual_text:
                    group.append(other)
        if not group:
            group = [block]
        group_ids = {item["id"] for item in group}
        consumed.update(group_ids)
        region_box = bbox_union([block_bbox(item) for item in group])
        regions.append({"source_ids": [item["id"] for item in group], "bbox": region_box})
    return regions
```

- [ ] **Step 3: Implement page classification with reference state**

Add:

```python
def classify_blocks(blocks, visual_regions) -> dict[str, str]:
    classes = {}
    visual_ids = {source_id for region in visual_regions for source_id in region["source_ids"]}
    in_references = False
    for block in sorted(blocks, key=lambda item: (item["yMin"], item["xMin"])):
        text = normalize_text(block.get("text", ""))
        if not text:
            continue
        if block["id"] in visual_ids:
            if is_formula_or_code_block(text):
                classes[block["id"]] = "formula_region"
            else:
                classes[block["id"]] = "figure_region"
            continue
        if is_page_number(text):
            classes[block["id"]] = "page_number"
            continue
        if is_reference_heading(text) or in_references or starts_reference_item(text):
            classes[block["id"]] = "reference"
            in_references = True
            continue
        if is_title_block(block):
            classes[block["id"]] = "title"
            continue
        if is_heading_text(text):
            classes[block["id"]] = "heading"
            continue
        classes[block["id"]] = "body"
    return classes
```

- [ ] **Step 4: Replace `build_page_render_plan` with classification-aware implementation**

Use:

```python
def build_page_render_plan(page_num: int, blocks, translations, page_size, bbox_lines=None) -> PageRenderPlan:
    plan = PageRenderPlan(page_num=page_num)
    visual_regions = build_visual_regions(blocks)
    classes = classify_blocks(blocks, visual_regions)
    visual_ids = {source_id for region in visual_regions for source_id in region["source_ids"]}

    for region in visual_regions:
        plan.items.append(
            RenderItem(
                kind="original_image_clip",
                source_ids=region["source_ids"],
                bbox=region["bbox"],
                fallback_reason="visual_region",
            )
        )
        plan.protected_boxes.append(region["bbox"])
        for source_id in region["source_ids"]:
            plan.ledger.append(CoverageEntry(source_id, classes.get(source_id, "figure_region"), "original_image_clip", True, "visual_region"))

    for block in blocks:
        text = normalize_text(block.get("text", ""))
        if not text or block["id"] in visual_ids:
            continue
        classification = classes.get(block["id"], "unknown")
        bbox = block_bbox(block)
        if classification == "page_number":
            plan.ledger.append(CoverageEntry(block["id"], classification, "skip_explicitly", True))
            continue
        if classification == "reference":
            plan.items.append(RenderItem("original_selectable_text", [block["id"]], bbox, text=text, fallback_reason="reference_original"))
            plan.ledger.append(CoverageEntry(block["id"], classification, "original_selectable_text", True, "reference_original"))
            continue
        if classification in {"body", "heading", "title"}:
            translated = translation_for_block(block, translations)
            if translated and block["id"] in translations:
                plan.items.append(RenderItem("translated_text", [block["id"]], bbox, text=translated, font_size=target_font_size_points_for_block(block)))
                plan.ledger.append(CoverageEntry(block["id"], classification, "translated_text", True))
            else:
                plan.items.append(RenderItem("original_selectable_text", [block["id"]], bbox, text=text, fallback_reason="missing_translation"))
                plan.ledger.append(CoverageEntry(block["id"], classification, "original_selectable_text", True, "missing_translation"))
            continue
        plan.items.append(RenderItem("original_image_clip", [block["id"]], bbox, fallback_reason="unknown"))
        plan.protected_boxes.append(bbox)
        plan.ledger.append(CoverageEntry(block["id"], classification, "original_image_clip", True, "unknown"))
    return plan
```

- [ ] **Step 5: Run tests**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest tests.test_render_plan -v
```

Expected: all tests in `tests.test_render_plan` pass.

- [ ] **Step 6: Commit**

```bash
git add translate_pdf_via_codex.py tests/test_render_plan.py
git commit -m "feat: classify blocks into render plan"
```

## Task 3: Add Coverage QA and Remove Silent Dropping

**Files:**
- Modify: `translate_pdf_via_codex.py`
- Modify: `tests/test_render_plan.py`

- [ ] **Step 1: Add failing coverage tests**

Append to `tests/test_render_plan.py`:

```python
class CoverageValidationTests(unittest.TestCase):
    def test_validate_coverage_fails_for_missing_block(self):
        blocks = [
            block("p002b0001", 2, "A source paragraph.", y0=100, y1=120),
            block("p002b0002", 2, "Another source paragraph.", y0=130, y1=150),
        ]
        plan = pdf.PageRenderPlan(page_num=2)
        plan.ledger.append(pdf.CoverageEntry("p002b0001", "body", "translated_text", True))

        errors = pdf.validate_plan_coverage(2, blocks, plan)

        self.assertIn("p002b0002", "\n".join(errors))

    def test_validate_coverage_allows_page_number_skip(self):
        blocks = [
            block("p002b0001", 2, "127", y0=760, y1=770),
        ]
        plan = pdf.build_page_render_plan(2, blocks, {}, page_size=(623, 801), bbox_lines=None)

        errors = pdf.validate_plan_coverage(2, blocks, plan)

        self.assertEqual(errors, [])
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest tests.test_render_plan -v
```

Expected: failure because `validate_plan_coverage` is missing.

- [ ] **Step 3: Implement coverage validation**

Add near `build_page_render_plan`:

```python
ALLOWED_SKIP_CLASSES = {"page_number", "header_footer"}


def nontrivial_block(block) -> bool:
    text = normalize_text(block.get("text", ""))
    return bool(text) and not is_trivial_keep(text)


def validate_plan_coverage(page_num: int, blocks, plan: PageRenderPlan) -> list[str]:
    errors = []
    ledger_by_id = {entry.block_id: entry for entry in plan.ledger}
    for block in blocks:
        if not nontrivial_block(block):
            continue
        entry = ledger_by_id.get(block["id"])
        if entry is None:
            errors.append(f"page {page_num} block {block['id']} has no coverage entry")
            continue
        if not entry.rendered:
            errors.append(f"page {page_num} block {block['id']} is marked unrendered")
            continue
        if entry.render_kind == "skip_explicitly" and entry.classification not in ALLOWED_SKIP_CLASSES:
            errors.append(f"page {page_num} block {block['id']} has illegal skip class {entry.classification}")
    return errors
```

- [ ] **Step 4: Replace unsafe nested filtering in render path**

In `write_vector_pdf`, remove use of `filter_nested_vector_blocks(blocks, translations)` for deciding what to render. The later render task will iterate `PageRenderPlan.items`; for this task, add a coverage check before the current loop:

```python
        plan = build_page_render_plan(page_num, blocks, translations, (page_rect.width, page_rect.height), bbox_lines=None)
        coverage_errors = validate_plan_coverage(page_num, blocks, plan)
        if coverage_errors:
            raise RuntimeError("\n".join(coverage_errors[:20]))
```

Keep the old block loop temporarily after this check until Task 5 replaces it.

- [ ] **Step 5: Run tests and py_compile**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest tests.test_render_plan -v
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m py_compile translate_pdf_via_codex.py
```

Expected: unit tests pass; py_compile exits 0.

- [ ] **Step 6: Commit**

```bash
git add translate_pdf_via_codex.py tests/test_render_plan.py
git commit -m "feat: validate render plan coverage"
```

## Task 4: Parse Source BBox Lines for Selectable References

**Files:**
- Modify: `translate_pdf_via_codex.py`
- Modify: `tests/test_render_plan.py`

- [ ] **Step 1: Add failing bbox-line parser test**

Append to `tests/test_render_plan.py`:

```python
class BBoxLineParserTests(unittest.TestCase):
    def test_parse_bbox_lines_extracts_words_and_coordinates(self):
        html = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <doc>
      <page width="623.000000" height="801.000000">
        <block xMin="131.4" yMin="234.6" xMax="184.5" yMax="244.6">
          <line xMin="131.4" yMin="234.6" xMax="184.5" yMax="244.6">
            <word xMin="131.4" yMin="234.6" xMax="184.5" yMax="244.6">REFERENCES</word>
          </line>
        </block>
      </page>
    </doc>
  </body>
</html>
"""
        lines = pdf.parse_bbox_lines_from_text(html)

        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["page"], 1)
        self.assertEqual(lines[0]["text"], "REFERENCES")
        self.assertEqual(lines[0]["bbox"], (131.4, 234.6, 184.5, 244.6))
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest tests.test_render_plan.BBoxLineParserTests -v
```

Expected: failure because `parse_bbox_lines_from_text` is missing.

- [ ] **Step 3: Implement bbox line parsing**

Add after `parse_bbox`:

```python
def parse_bbox_lines_from_text(raw_text: str) -> list[dict]:
    raw_text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", raw_text)
    root = ET.fromstring(raw_text)
    ns = {"x": "http://www.w3.org/1999/xhtml"}
    page_nodes = root.findall(".//page")
    if not page_nodes:
        page_nodes = root.findall(".//x:page", ns)
    lines = []
    for page_idx, page in enumerate(page_nodes, start=1):
        line_nodes = page.findall(".//line")
        if not line_nodes:
            line_nodes = page.findall(".//x:line", ns)
        for line in line_nodes:
            word_nodes = line.findall("./word")
            if not word_nodes:
                word_nodes = line.findall("./x:word", ns)
            words = [word.text or "" for word in word_nodes]
            text = normalize_text(" ".join(words))
            if not text:
                continue
            lines.append(
                {
                    "page": page_idx,
                    "text": text,
                    "bbox": (
                        float(line.attrib["xMin"]),
                        float(line.attrib["yMin"]),
                        float(line.attrib["xMax"]),
                        float(line.attrib["yMax"]),
                    ),
                }
            )
    return lines


def parse_bbox_lines(bbox_path: Path) -> list[dict]:
    return parse_bbox_lines_from_text(bbox_path.read_text(encoding="utf-8", errors="replace"))
```

- [ ] **Step 4: Pass bbox lines into vector rendering**

Change `write_vector_pdf` signature:

```python
def write_vector_pdf(pdf_path: Path, pdf_output: Path, selected_pages, translations, pdf_size_pt, dpi: int, job_paths=None):
```

At the start of `write_vector_pdf`, after opening `src_doc`, add:

```python
    bbox_lines_by_page = {}
    if job_paths and job_paths.get("bbox_path") and job_paths["bbox_path"].exists():
        for line in parse_bbox_lines(job_paths["bbox_path"]):
            bbox_lines_by_page.setdefault(line["page"], []).append(line)
```

Change the plan creation call:

```python
        plan = build_page_render_plan(
            page_num,
            blocks,
            translations,
            (page_rect.width, page_rect.height),
            bbox_lines=bbox_lines_by_page.get(page_num),
        )
```

In `main`, pass `job_paths`:

```python
        write_vector_pdf(
            pdf_path,
            Path(args.pdf_output),
            selected_pages,
            translations,
            pdf_size_pt,
            args.dpi,
            job_paths=job_paths,
        )
```

- [ ] **Step 5: Run tests and py_compile**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest tests.test_render_plan -v
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m py_compile translate_pdf_via_codex.py
```

Expected: all tests pass; py_compile exits 0.

- [ ] **Step 6: Commit**

```bash
git add translate_pdf_via_codex.py tests/test_render_plan.py
git commit -m "feat: parse bbox lines for reference rendering"
```

## Task 5: Render Plan Items in Vector PDF

**Files:**
- Modify: `translate_pdf_via_codex.py`
- Test: `tests/test_render_plan.py`

- [ ] **Step 1: Add layout-overlap tests**

Append to `tests/test_render_plan.py`:

```python
class LayoutValidationTests(unittest.TestCase):
    def test_overlap_validation_detects_body_over_image_clip(self):
        plan = pdf.PageRenderPlan(page_num=3)
        plan.items.append(pdf.RenderItem("original_image_clip", ["fig"], (100, 100, 300, 200)))
        plan.items.append(pdf.RenderItem("translated_text", ["body"], (150, 120, 350, 220), text="正文"))

        errors = pdf.validate_plan_layout(plan, page_size=(623, 801))

        self.assertTrue(any("overlaps protected" in error for error in errors))

    def test_overlap_validation_allows_nonoverlapping_items(self):
        plan = pdf.PageRenderPlan(page_num=3)
        plan.items.append(pdf.RenderItem("original_image_clip", ["fig"], (100, 100, 300, 200)))
        plan.items.append(pdf.RenderItem("translated_text", ["body"], (100, 220, 350, 260), text="正文"))

        errors = pdf.validate_plan_layout(plan, page_size=(623, 801))

        self.assertEqual(errors, [])
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest tests.test_render_plan.LayoutValidationTests -v
```

Expected: failure because `validate_plan_layout` is missing.

- [ ] **Step 3: Implement layout validation**

Add near coverage validation:

```python
def bbox_area(box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def bbox_overlap_area(a, b) -> float:
    x0 = max(a[0], b[0])
    y0 = max(a[1], b[1])
    x1 = min(a[2], b[2])
    y1 = min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def validate_plan_layout(plan: PageRenderPlan, page_size) -> list[str]:
    errors = []
    width, height = page_size
    protected = [item for item in plan.items if item.kind == "original_image_clip"]
    for item in plan.items:
        x0, y0, x1, y1 = item.bbox
        if x0 < -0.5 or y0 < -0.5 or x1 > width + 0.5 or y1 > height + 0.5:
            errors.append(f"page {plan.page_num} item {item.source_ids} outside page bounds")
        if item.kind != "translated_text":
            continue
        for protected_item in protected:
            if bbox_overlap_area(item.bbox, protected_item.bbox) > min(bbox_area(item.bbox), bbox_area(protected_item.bbox)) * 0.05:
                errors.append(f"page {plan.page_num} text {item.source_ids} overlaps protected {protected_item.source_ids}")
    return errors
```

- [ ] **Step 4: Add image clip insertion helper**

Add after `preserve_images_on_page`:

```python
def insert_source_clip(src_page, out_page, fitz, bbox, dpi: int):
    rect = fitz.Rect(bbox)
    if rect.is_empty:
        return
    matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    pix = src_page.get_pixmap(matrix=matrix, clip=rect, alpha=False)
    out_page.insert_image(rect, pixmap=pix, keep_proportion=False)
```

- [ ] **Step 5: Add render item insertion helper**

Add before `write_vector_pdf`:

```python
def render_plan_item(out_page, src_page, fitz, item: RenderItem, dpi: int):
    rect = fitz.Rect(item.bbox)
    if item.kind == "translated_text":
        insert_vector_textbox(out_page, fitz, rect, item.text, item.font_size or 7.0, item.color)
        return
    if item.kind == "original_selectable_text":
        insert_vector_textbox(out_page, fitz, rect, item.text, item.font_size or 6.5, VECTOR_BODY_COLOR)
        return
    if item.kind == "original_image_clip":
        insert_source_clip(src_page, out_page, fitz, item.bbox, dpi)
        return
```

- [ ] **Step 6: Replace old block rendering loop**

In `write_vector_pdf`, replace:

```python
        for block in filter_nested_vector_blocks(blocks, translations):
            ...
```

with:

```python
        layout_errors = validate_plan_layout(plan, (page_rect.width, page_rect.height))
        if layout_errors:
            raise RuntimeError("\n".join(layout_errors[:20]))
        for item in plan.items:
            render_plan_item(out_page, src_page, fitz, item, dpi)
```

- [ ] **Step 7: Run tests and py_compile**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest tests.test_render_plan -v
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m py_compile translate_pdf_via_codex.py
```

Expected: all tests pass; py_compile exits 0.

- [ ] **Step 8: Commit**

```bash
git add translate_pdf_via_codex.py tests/test_render_plan.py
git commit -m "feat: render vector pdf from page plans"
```

## Task 6: Add Wait-free Data Regression Tests

**Files:**
- Create: `tests/test_waitfree_regression.py`
- Modify: `translate_pdf_via_codex.py`

- [ ] **Step 1: Write failing regression tests**

Create `tests/test_waitfree_regression.py`:

```python
import json
import unittest
from pathlib import Path

import translate_pdf_via_codex as pdf


JOB = Path("work/jobs/wait-free-synchronization")


@unittest.skipUnless((JOB / "source_pages.json").exists(), "Wait-free cached source pages not present")
class WaitFreeRenderPlanRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pages = json.loads((JOB / "source_pages.json").read_text(encoding="utf-8"))
        cls.translations = json.loads((JOB / "translations.json").read_text(encoding="utf-8"))

    def plan_for_page(self, page_num):
        return pdf.build_page_render_plan(
            page_num,
            self.pages[page_num - 1],
            self.translations,
            page_size=(623, 801),
            bbox_lines=None,
        )

    def test_page4_input_events_block_has_fallback_or_translation(self):
        plan = self.plan_for_page(4)
        entries = {entry.block_id: entry for entry in plan.ledger}

        self.assertIn("p004b0005", entries)
        self.assertIn(entries["p004b0005"].render_kind, {"translated_text", "original_selectable_text", "original_image_clip"})

    def test_page15_assertion_formulas_are_image_clips(self):
        plan = self.plan_for_page(15)
        image_ids = {source_id for item in plan.items if item.kind == "original_image_clip" for source_id in item.source_ids}

        self.assertIn("p015b0007", image_ids)
        self.assertIn("p015b0011", image_ids)

    def test_page3_and_page9_figures_are_image_clips(self):
        page3 = self.plan_for_page(3)
        page9 = self.plan_for_page(9)
        page3_images = {source_id for item in page3.items if item.kind == "original_image_clip" for source_id in item.source_ids}
        page9_images = {source_id for item in page9.items if item.kind == "original_image_clip" for source_id in item.source_ids}

        self.assertIn("p003b0004", page3_images)
        self.assertIn("p009b0001", page9_images)
        self.assertIn("p009b0004", page9_images)

    def test_pages25_26_references_are_not_translated(self):
        for page_num in (25, 26):
            plan = self.plan_for_page(page_num)
            reference_items = [item for item in plan.items if item.kind == "original_selectable_text" and item.fallback_reason == "reference_original"]
            combined = "\n".join(item.text for item in reference_items)

            self.assertNotIn("载于", combined)
            self.assertRegex(combined, r"(REFERENCES|LAMPORT|ANDERSON|HERLIHY)")
```

- [ ] **Step 2: Run tests and verify failures point to real regressions**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest tests.test_waitfree_regression -v
```

Expected: failures identify any Wait-free-specific classification gaps.

- [ ] **Step 3: Tighten figure/formula grouping until regressions pass**

Adjust `build_visual_regions` with these concrete rules:

```python
        if block.get("preserve_image"):
            search = expanded_bbox(seed_box, pad_x=45.0, pad_y=45.0)
        if text.startswith("Fig.") or text.startswith("Figure"):
            search = (seed_box[0] - 50.0, seed_box[1] - 70.0, seed_box[2] + 50.0, seed_box[3] + 16.0)
        if "following assertions" in text.lower():
            search = expanded_bbox(seed_box, pad_x=40.0, pad_y=60.0)
```

Ensure formula fragments that overlap this search box are grouped as `original_image_clip`.

- [ ] **Step 4: Run unit and regression tests**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest tests.test_render_plan tests.test_waitfree_regression -v
```

Expected: all tests pass or Wait-free suite skips only if cache files are unavailable.

- [ ] **Step 5: Commit**

```bash
git add translate_pdf_via_codex.py tests/test_waitfree_regression.py
git commit -m "test: lock wait-free render plan regressions"
```

## Task 7: Regenerate and Visually QA Wait-free Sample

**Files:**
- Modify: `translate_pdf_via_codex.py`
- Output: `output/pdf/Wait-free synchronization-Chinese-rewrapped.pdf`

- [ ] **Step 1: Run full verification before regeneration**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest tests.test_render_plan tests.test_waitfree_regression -v
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m py_compile translate_pdf_via_codex.py translate_pdf_parallel.py
```

Expected: tests pass; py_compile exits 0.

- [ ] **Step 2: Regenerate Wait-free PDF using cached pages and translations**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 - <<'PY'
import json
from pathlib import Path
import translate_pdf_via_codex as p

source_pdf = Path("output/pdf/Wait-free synchronization-Chinese.pdf")
out_pdf = Path("output/pdf/Wait-free synchronization-Chinese-rewrapped.pdf")
job_paths = {
    "bbox_path": Path("work/jobs/wait-free-synchronization/source_bbox.html"),
}
pages = json.loads(Path("work/jobs/wait-free-synchronization/source_pages.json").read_text(encoding="utf-8"))
translations = json.loads(Path("work/jobs/wait-free-synchronization/translations.json").read_text(encoding="utf-8"))
fitz = p.load_fitz()
doc = fitz.open(source_pdf)
selected = [(idx, page) for idx, page in enumerate(pages, start=1)]
p.write_vector_pdf(source_pdf, out_pdf, selected, translations, (doc[0].rect.width, doc[0].rect.height), 160, job_paths=job_paths)
doc.close()
print(out_pdf)
PY
```

Expected: command prints `output/pdf/Wait-free synchronization-Chinese-rewrapped.pdf`.

- [ ] **Step 3: Render QA pages to PNG**

Run:

```bash
pdftoppm -png -f 1 -l 1 "output/pdf/Wait-free synchronization-Chinese-rewrapped.pdf" /tmp/waitfree-rp-p01
pdftoppm -png -f 2 -l 4 "output/pdf/Wait-free synchronization-Chinese-rewrapped.pdf" /tmp/waitfree-rp-p02-04
pdftoppm -png -f 9 -l 9 "output/pdf/Wait-free synchronization-Chinese-rewrapped.pdf" /tmp/waitfree-rp-p09
pdftoppm -png -f 15 -l 15 "output/pdf/Wait-free synchronization-Chinese-rewrapped.pdf" /tmp/waitfree-rp-p15
pdftoppm -png -f 25 -l 26 "output/pdf/Wait-free synchronization-Chinese-rewrapped.pdf" /tmp/waitfree-rp-p25-26
```

Expected: PNG files exist under `/tmp` for pages 1, 2, 3, 4, 9, 15, 25, and 26.

- [ ] **Step 4: Run text extraction checks**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 - <<'PY'
from pathlib import Path
import sys
sys.path.insert(0, str(Path.cwd() / "vendor"))
import fitz

pdf = Path("output/pdf/Wait-free synchronization-Chinese-rewrapped.pdf")
doc = fitz.open(pdf)
page4 = doc[3].get_text("text")
refs = doc[24].get_text("text") + "\n" + doc[25].get_text("text")
assert "In(A) is a set of input events" in page4 or "输入事件" in page4
assert "载于" not in refs
assert any(token in refs for token in ["REFERENCES", "LAMPORT", "ANDERSON", "HERLIHY"])
print("text checks passed")
doc.close()
PY
```

Expected: prints `text checks passed`.

- [ ] **Step 5: Inspect rendered PNGs**

Open these files with the local image viewer or `functions.view_image`:

```text
/tmp/waitfree-rp-p01-1.png
/tmp/waitfree-rp-p02-04-2.png
/tmp/waitfree-rp-p02-04-3.png
/tmp/waitfree-rp-p02-04-4.png
/tmp/waitfree-rp-p09-9.png
/tmp/waitfree-rp-p15-15.png
/tmp/waitfree-rp-p25-26-25.png
/tmp/waitfree-rp-p25-26-26.png
```

Expected visual results:

- page 1 title hierarchy is clear and no metadata text overlaps
- page 2 greedy line wrapping has no large avoidable right-side blanks
- page 3 Fig. 1 is complete and caption remains English
- page 4 input-events content is present
- page 9 Fig. 3 and Fig. 4 are complete and captions remain English
- page 15 assertion formulas after the consistency sentence are present
- pages 25 and 26 references are English and aligned to original margins

- [ ] **Step 6: Commit source changes**

Commit code and tests only:

```bash
git add translate_pdf_via_codex.py tests
git commit -m "feat: preserve untranslatable pdf regions safely"
```

Do not add generated PDFs unless the user explicitly asks to track outputs.

## Task 8: Final QA and Handoff

**Files:**
- Modify: none unless a regression is found

- [ ] **Step 1: Run final automated checks**

Run:

```bash
git diff --check
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest tests.test_render_plan tests.test_waitfree_regression -v
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m py_compile translate_pdf_via_codex.py translate_pdf_parallel.py
```

Expected:

- `git diff --check` prints no whitespace errors
- unit tests pass
- py_compile exits 0

- [ ] **Step 2: Confirm output sizes**

Run:

```bash
ls -lh "output/pdf/Wait-free synchronization-Chinese.pdf" "output/pdf/Wait-free synchronization-Chinese-rewrapped.pdf"
```

Expected: rewrapped PDF may be larger than pure vector output because it contains figure/formula clips, but it should remain far smaller than full-page raster output.

- [ ] **Step 3: Summarize QA evidence**

Prepare final handoff with:

```text
Changed:
- render-plan classification and coverage ledger
- figure/formula/code fallback to compressed image clips
- references retained as original English selectable text
- coverage/layout tests and Wait-free regression tests

Verified:
- unit tests
- py_compile
- Wait-free text extraction checks
- visual PNG review for pages 1, 2, 3, 4, 9, 15, 25, 26
```

- [ ] **Step 4: Report any residual risk**

Include residual risks only if observed:

```text
Residual risk:
- some references may fall back to image clips if bbox reconstruction fails
- documents with unusual multi-column figures may need visual-region threshold tuning
```

## Self-Review Against Spec

- Coverage ledger: Task 1, Task 3.
- Explicit classification: Task 2.
- Figures/formulas/code figures preserved as original clips with captions: Task 2, Task 5, Task 6.
- References not translated and kept selectable where possible: Task 4, Task 6, Task 7.
- Missing translation fallback: Task 1, Task 2.
- Avoid overlap/clipping: Task 5, Task 7.
- Title hierarchy: Task 2 establishes classification; Task 7 verifies page 1 visual output.
- Greedy wrapping: existing wrapper is retained; Task 7 verifies page 2 regression.
- Wait-free target regressions: Task 6, Task 7.
