# Component Ownership Rendering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make PDF translation rendering structurally prevent ghosting, figure-label translation, clipped tables, and reference bleed by assigning every non-trivial source block to exactly one page component before translation or rendering.

**Architecture:** Add a deterministic `ownership.py` layer that produces `PageComponent` records from existing classification, reference-continuation, visual-region, duplicate, and protected-region decisions. Translation batching and render planning consume the same ownership context, render plans serialize ownership metadata, and visual QA reports ownership violations as hard failures in strict mode while still writing diagnostics in exploratory mode.

**Tech Stack:** Python 3, standard-library `dataclasses`/`json`/`unittest`, existing `render_plan.py`, `qa_visual.py`, `translate_pdf_via_codex.py`, `translate_pdf_parallel.py`, existing page-level PDF render fixtures.

---

## Current-State Notes

- The working tree is already dirty; do not revert unrelated changes in `classify.py`, `regions.py`, `render_plan.py`, `translate_pdf_via_codex.py`, `translate_pdf_parallel.py`, tests, `work/`, or generated PDFs.
- The current workspace contains draft untracked files `ownership.py` and `tests/test_ownership.py`. Task 1 formalizes and expands that draft. If the files are present when executing, edit them in place; if not, create them with the code shown below.
- This plan fixes the OpenSpec change `openspec/changes/enforce-component-ownership-rendering`.
- Strictness decision: ownership violations are always serialized into render-plan artifacts and visual-QA reports. `--strict-qa` makes them fail the job before success is reported; non-strict mode may still write exploratory PDFs but must report the issues.
- Unknown-content decision: non-trivial unknown content is preserved as an `unknown` component rendered as `original_image_clip` unless it is explicitly classified as a safe original selectable fallback.
- Geometry tolerance decision: ownership overlap checks use `min_overlap_ratio=0.05` and `min_overlap_height=4.0pt` for text-over-visual, and `max_visual_overcapture_ratio=0.02` for visual clips capturing unrelated translated text.

## File Structure

- Create/modify `ownership.py`
  - Owns component dataclasses, stable JSON serialization, block bbox helpers, ownership ledger helpers, ownership builder, translation filtering, and ownership/render-layer validation.
- Modify `render_plan.py`
  - Adds component metadata to `RenderItem`, `CoverageEntry`, and `PageRenderPlan`.
  - Serializes `ownership_components`, `ownership_validation`, and ownership fields in existing artifacts.
- Modify `translate_pdf_via_codex.py`
  - Adds `prepare_page_ownership()` as the single page-preparation entry point.
  - Routes `build_batches()` through ownership.
  - Routes `build_page_render_plan()` through ownership and calls hard validation before PDF writing.
- Modify `translate_pdf_parallel.py`
  - Routes `build_page_batches()` through `pipeline.prepare_page_ownership()`.
  - Includes ownership issue counts in parallel summaries.
- Modify `qa_visual.py`
  - Adds ownership QA categories and includes them in `generate_visual_qa_report()`.
- Modify `tests/pdf_render_fixture_runner.py`
  - Adds ownership fixture assertions for components, render-link exclusivity, and ownership validation status.
- Modify `tests/fixtures/pdf_render/README.md`
  - Documents the ownership-fixture workflow for future visual defect reports.
- Create/modify `tests/test_ownership.py`
  - Unit tests for serialization, validation, builder, and translation filtering.
- Modify `tests/test_render_plan.py`
  - Tests render-plan serialization, component fields, and render-layer validation.
- Modify `tests/test_translate_pdf_parallel.py`
  - Tests single-doc and parallel batch planning consume the same ownership output.
- Modify `tests/test_qa_visual.py`
  - Tests ownership QA categories and report integration.
- Create fixture files:
  - `tests/fixtures/pdf_render/source_pages/bert/page-009.json`
  - `tests/fixtures/pdf_render/source_pages/bert/page-012.json`
  - `tests/fixtures/pdf_render/source_pages/bert/page-015.json`
  - `tests/fixtures/pdf_render/translations/bert/page-009.json`
  - `tests/fixtures/pdf_render/translations/bert/page-012.json`
  - `tests/fixtures/pdf_render/translations/bert/page-015.json`
  - `tests/fixtures/pdf_render/expected_plans/bert/page-009.json`
  - `tests/fixtures/pdf_render/expected_plans/bert/page-012.json`
  - `tests/fixtures/pdf_render/expected_plans/bert/page-015.json`
  - `tests/fixtures/pdf_render/expected_qa/bert/page-009.json`
  - `tests/fixtures/pdf_render/expected_qa/bert/page-012.json`
  - `tests/fixtures/pdf_render/expected_qa/bert/page-015.json`

## Task 1: Ownership Data Model And Validation

**Files:**
- Create/modify: `ownership.py`
- Create/modify: `tests/test_ownership.py`

- [ ] **Step 1: Write failing serialization and validation tests**

Add these imports and tests to `tests/test_ownership.py`:

```python
import json
import unittest

import ownership


def block(block_id, text="Body text.", x0=10, y0=20, x1=110, y1=40):
    return {
        "id": block_id,
        "page": int(block_id[1:4]),
        "text": text,
        "xMin": float(x0),
        "yMin": float(y0),
        "xMax": float(x1),
        "yMax": float(y1),
    }


class OwnershipSerializationTests(unittest.TestCase):
    def test_page_component_serializes_deterministically(self):
        component = ownership.PageComponent(
            component_id="p001c0002",
            component_kind=ownership.COMPONENT_KIND_TRANSLATED_TEXT,
            source_ids=["p001b0002", "p001b0001"],
            source_bbox=(10.12345, 20, 30.5, 40.0),
            clip_bbox=None,
            confidence=ownership.CONFIDENCE_DETERMINISTIC,
            reason_codes=["layout_flow", "body_text"],
            render_strategy="translated_text",
        )

        self.assertEqual(
            ownership.page_component_to_json(component),
            {
                "clip_bbox": None,
                "component_id": "p001c0002",
                "component_kind": "translated_text",
                "confidence": "deterministic",
                "parent_component_id": "",
                "reason_codes": ["body_text", "layout_flow"],
                "render_strategy": "translated_text",
                "source_bbox": [10.123, 20.0, 30.5, 40.0],
                "source_ids": ["p001b0001", "p001b0002"],
            },
        )
        dumped = ownership.stable_json_dumps({"component": ownership.page_component_to_json(component)})
        self.assertEqual(dumped, json.dumps(json.loads(dumped), ensure_ascii=False, sort_keys=True, separators=(",", ":")))

    def test_validation_result_serializes_deterministically(self):
        issue = ownership.OwnershipIssue(
            issue_code="duplicate_owner",
            severity="error",
            page_num=8,
            message="source block p008b0001 has multiple non-duplicate owners",
            source_ids=["p008b0001"],
            component_ids=["p008c0002", "p008c0001"],
            bboxes=[(10, 20, 30, 40)],
        )

        self.assertEqual(
            ownership.ownership_issue_to_json(issue),
            {
                "bboxes": [[10.0, 20.0, 30.0, 40.0]],
                "component_ids": ["p008c0001", "p008c0002"],
                "issue_code": "duplicate_owner",
                "message": "source block p008b0001 has multiple non-duplicate owners",
                "page_num": 8,
                "severity": "error",
                "source_ids": ["p008b0001"],
            },
        )


class OwnershipValidationTests(unittest.TestCase):
    def test_missing_owner_is_reported(self):
        result = ownership.validate_ownership(
            page_num=7,
            blocks=[block("p007b0001", "Owned text."), block("p007b0002", "Missing owner.")],
            components=[
                ownership.PageComponent(
                    component_id="p007c0001",
                    component_kind=ownership.COMPONENT_KIND_TRANSLATED_TEXT,
                    source_ids=["p007b0001"],
                    source_bbox=(10, 20, 110, 40),
                    clip_bbox=None,
                    confidence=ownership.CONFIDENCE_DETERMINISTIC,
                    reason_codes=["body_text"],
                    render_strategy="translated_text",
                )
            ],
        )

        self.assertFalse(result.ok)
        self.assertEqual([issue.issue_code for issue in result.issues], ["missing_owner"])
        self.assertEqual(result.issues[0].source_ids, ["p007b0002"])

    def test_duplicate_non_duplicate_owner_is_reported(self):
        result = ownership.validate_ownership(
            page_num=8,
            blocks=[block("p008b0001", "Duplicated owner.")],
            components=[
                ownership.PageComponent(
                    component_id="p008c0001",
                    component_kind=ownership.COMPONENT_KIND_TRANSLATED_TEXT,
                    source_ids=["p008b0001"],
                    source_bbox=(10, 20, 110, 40),
                    clip_bbox=None,
                    confidence=ownership.CONFIDENCE_DETERMINISTIC,
                    reason_codes=["body_text"],
                    render_strategy="translated_text",
                ),
                ownership.PageComponent(
                    component_id="p008c0002",
                    component_kind=ownership.COMPONENT_KIND_REFERENCE,
                    source_ids=["p008b0001"],
                    source_bbox=(10, 20, 110, 40),
                    clip_bbox=None,
                    confidence=ownership.CONFIDENCE_INFERRED,
                    reason_codes=["reference_like"],
                    render_strategy="original_selectable_text",
                ),
            ],
        )

        self.assertFalse(result.ok)
        self.assertEqual([issue.issue_code for issue in result.issues], ["duplicate_owner"])
        self.assertEqual(result.issues[0].component_ids, ["p008c0001", "p008c0002"])

    def test_duplicate_and_skip_are_valid_explicit_owners(self):
        result = ownership.validate_ownership(
            page_num=10,
            blocks=[block("p010b0001", "Duplicate extraction."), block("p010b0002", "Skip artifact.")],
            components=[
                ownership.PageComponent(
                    component_id="p010c0001",
                    component_kind=ownership.COMPONENT_KIND_DUPLICATE,
                    source_ids=["p010b0001"],
                    source_bbox=(10, 20, 110, 40),
                    clip_bbox=None,
                    confidence=ownership.CONFIDENCE_DETERMINISTIC,
                    reason_codes=["duplicated_extraction"],
                    render_strategy="skip_explicitly",
                ),
                ownership.PageComponent(
                    component_id="p010c0002",
                    component_kind=ownership.COMPONENT_KIND_SKIP,
                    source_ids=["p010b0002"],
                    source_bbox=(10, 20, 110, 40),
                    clip_bbox=None,
                    confidence=ownership.CONFIDENCE_DETERMINISTIC,
                    reason_codes=["trivial_or_artifact"],
                    render_strategy="skip_explicitly",
                ),
            ],
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.issues, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests and confirm they fail for missing fields/functions**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest tests.test_ownership -v
```

Expected: `FAIL` or `ERROR` mentioning missing `ownership` definitions such as `PageComponent`, `clip_bbox`, `render_strategy`, or `ownership_issue_to_json`.

- [ ] **Step 3: Implement the minimal ownership model**

In `ownership.py`, make sure these public names exist. If the file already exists, update it to include the fields and functions below without removing compatible existing helpers:

```python
import json
import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field


COMPONENT_KIND_TRANSLATED_TEXT = "translated_text"
COMPONENT_KIND_VISUAL = "visual"
COMPONENT_KIND_REFERENCE = "reference"
COMPONENT_KIND_HEADER_FOOTER = "header_footer"
COMPONENT_KIND_PAGE_NUMBER = "page_number"
COMPONENT_KIND_UNKNOWN = "unknown"
COMPONENT_KIND_DUPLICATE = "duplicate"
COMPONENT_KIND_SKIP = "skip"

COMPONENT_KINDS = {
    COMPONENT_KIND_TRANSLATED_TEXT,
    COMPONENT_KIND_VISUAL,
    COMPONENT_KIND_REFERENCE,
    COMPONENT_KIND_HEADER_FOOTER,
    COMPONENT_KIND_PAGE_NUMBER,
    COMPONENT_KIND_UNKNOWN,
    COMPONENT_KIND_DUPLICATE,
    COMPONENT_KIND_SKIP,
}
NON_DUPLICATE_COMPONENT_KINDS = COMPONENT_KINDS - {COMPONENT_KIND_DUPLICATE, COMPONENT_KIND_SKIP}

CONFIDENCE_DETERMINISTIC = "deterministic"
CONFIDENCE_INFERRED = "inferred"
CONFIDENCE_CONSERVATIVE = "conservative"
CONFIDENCE_LEVELS = {CONFIDENCE_DETERMINISTIC, CONFIDENCE_INFERRED, CONFIDENCE_CONSERVATIVE}

TEXT_RENDER_KINDS = {"translated_text", "original_selectable_text"}
IMAGE_RENDER_KINDS = {"original_image_clip"}


@dataclass(frozen=True)
class PageComponent:
    component_id: str
    component_kind: str
    source_ids: list[str]
    source_bbox: tuple[float, float, float, float]
    clip_bbox: tuple[float, float, float, float] | None
    confidence: str
    reason_codes: list[str] = field(default_factory=list)
    render_strategy: str = ""
    parent_component_id: str = ""


@dataclass(frozen=True)
class OwnershipLedgerEntry:
    page_number: int
    source_id: str
    component_id: str
    component_kind: str
    ownership_role: str
    confidence: str
    reason_codes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class OwnershipIssue:
    issue_code: str
    severity: str
    page_num: int
    message: str
    source_ids: list[str] = field(default_factory=list)
    component_ids: list[str] = field(default_factory=list)
    bboxes: list[tuple[float, float, float, float]] = field(default_factory=list)


@dataclass(frozen=True)
class OwnershipValidationResult:
    issues: list[OwnershipIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues


def _stable_value(value):
    if isinstance(value, Mapping):
        return {str(key): _stable_value(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, tuple):
        return [_stable_value(item) for item in value]
    if isinstance(value, list):
        return [_stable_value(item) for item in value]
    return value


def stable_json_dumps(value) -> str:
    return json.dumps(_stable_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def bbox_to_json(bbox) -> list[float]:
    return [round(float(value), 3) for value in bbox]


def block_bbox(block) -> tuple[float, float, float, float]:
    return (
        float(block.get("xMin", 0.0)),
        float(block.get("yMin", 0.0)),
        float(block.get("xMax", 0.0)),
        float(block.get("yMax", 0.0)),
    )


def bbox_union(boxes) -> tuple[float, float, float, float]:
    normalized = [tuple(float(value) for value in box) for box in boxes if box is not None]
    if not normalized:
        return (0.0, 0.0, 0.0, 0.0)
    return (
        min(box[0] for box in normalized),
        min(box[1] for box in normalized),
        max(box[2] for box in normalized),
        max(box[3] for box in normalized),
    )


def bbox_area(box) -> float:
    return max(0.0, float(box[2]) - float(box[0])) * max(0.0, float(box[3]) - float(box[1]))


def bbox_overlap_area(left, right) -> float:
    x0 = max(float(left[0]), float(right[0]))
    y0 = max(float(left[1]), float(right[1]))
    x1 = min(float(left[2]), float(right[2]))
    y1 = min(float(left[3]), float(right[3]))
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def bbox_overlap_height(left, right) -> float:
    return max(0.0, min(float(left[3]), float(right[3])) - max(float(left[1]), float(right[1])))


def is_trivial_block(block) -> bool:
    text = re.sub(r"\s+", " ", str(block.get("text", ""))).strip()
    if not text:
        return True
    if re.fullmatch(r"\d+", text):
        return True
    if re.fullmatch(r"https?://\S+", text):
        return True
    return False


def page_component_to_json(component: PageComponent) -> dict:
    return {
        "clip_bbox": None if component.clip_bbox is None else bbox_to_json(component.clip_bbox),
        "component_id": component.component_id,
        "component_kind": component.component_kind,
        "confidence": component.confidence,
        "parent_component_id": component.parent_component_id,
        "reason_codes": sorted(str(code) for code in component.reason_codes),
        "render_strategy": component.render_strategy,
        "source_bbox": bbox_to_json(component.source_bbox),
        "source_ids": sorted(str(source_id) for source_id in component.source_ids),
    }


def ownership_issue_to_json(issue: OwnershipIssue) -> dict:
    return {
        "bboxes": [bbox_to_json(box) for box in issue.bboxes],
        "component_ids": sorted(str(component_id) for component_id in issue.component_ids),
        "issue_code": issue.issue_code,
        "message": issue.message,
        "page_num": int(issue.page_num),
        "severity": issue.severity,
        "source_ids": sorted(str(source_id) for source_id in issue.source_ids),
    }


def ownership_validation_to_json(result: OwnershipValidationResult | list[OwnershipIssue]) -> dict:
    issues = result.issues if isinstance(result, OwnershipValidationResult) else list(result)
    return {"ok": not issues, "issues": [ownership_issue_to_json(issue) for issue in issues]}


def components_to_json(components: list[PageComponent]) -> list[dict]:
    return [page_component_to_json(component) for component in sorted(components, key=lambda item: item.component_id)]


def ownership_ledger_entry_to_json(entry: OwnershipLedgerEntry) -> dict:
    return {
        "component_id": entry.component_id,
        "component_kind": entry.component_kind,
        "confidence": entry.confidence,
        "ownership_role": entry.ownership_role,
        "page_number": int(entry.page_number),
        "reason_codes": sorted(str(code) for code in entry.reason_codes),
        "source_id": entry.source_id,
    }


def ownership_ledger_for_components(page_number: int, components: list[PageComponent]) -> list[OwnershipLedgerEntry]:
    entries = []
    for component in sorted(components, key=lambda item: item.component_id):
        for source_id in sorted(set(component.source_ids)):
            entries.append(
                OwnershipLedgerEntry(
                    page_number=page_number,
                    source_id=source_id,
                    component_id=component.component_id,
                    component_kind=component.component_kind,
                    ownership_role="owner",
                    confidence=component.confidence,
                    reason_codes=list(component.reason_codes),
                )
            )
    return entries


def component_by_source_id(components: list[PageComponent]) -> dict[str, PageComponent]:
    result = {}
    for component in components:
        if component.component_kind in {COMPONENT_KIND_DUPLICATE, COMPONENT_KIND_SKIP}:
            continue
        for source_id in component.source_ids:
            result[str(source_id)] = component
    return result


def validate_ownership(page_num: int, blocks, components: list[PageComponent]) -> OwnershipValidationResult:
    issues: list[OwnershipIssue] = []
    block_by_id = {str(block["id"]): block for block in blocks if not is_trivial_block(block)}
    non_duplicate_owners: dict[str, list[PageComponent]] = defaultdict(list)
    explicit_owners: dict[str, list[PageComponent]] = defaultdict(list)

    for component in components:
        if component.component_kind not in COMPONENT_KINDS:
            issues.append(
                OwnershipIssue(
                    issue_code="invalid_component_kind",
                    severity="error",
                    page_num=page_num,
                    message=f"component {component.component_id} has invalid kind {component.component_kind}",
                    component_ids=[component.component_id],
                    bboxes=[component.source_bbox],
                )
            )
            continue
        if component.confidence not in CONFIDENCE_LEVELS:
            issues.append(
                OwnershipIssue(
                    issue_code="invalid_confidence",
                    severity="error",
                    page_num=page_num,
                    message=f"component {component.component_id} has invalid confidence {component.confidence}",
                    component_ids=[component.component_id],
                    bboxes=[component.source_bbox],
                )
            )
        for source_id in sorted(set(str(source_id) for source_id in component.source_ids)):
            explicit_owners[source_id].append(component)
            if component.component_kind in NON_DUPLICATE_COMPONENT_KINDS:
                non_duplicate_owners[source_id].append(component)

    for source_id, block in sorted(block_by_id.items()):
        if not explicit_owners.get(source_id):
            issues.append(
                OwnershipIssue(
                    issue_code="missing_owner",
                    severity="error",
                    page_num=page_num,
                    message=f"source block {source_id} has no component owner",
                    source_ids=[source_id],
                    bboxes=[block_bbox(block)],
                )
            )
            continue
        owner_components = non_duplicate_owners.get(source_id, [])
        if len(owner_components) > 1:
            issues.append(
                OwnershipIssue(
                    issue_code="duplicate_owner",
                    severity="error",
                    page_num=page_num,
                    message=f"source block {source_id} has multiple non-duplicate owners",
                    source_ids=[source_id],
                    component_ids=sorted(component.component_id for component in owner_components),
                    bboxes=[block_bbox(block)],
                )
            )

    return OwnershipValidationResult(issues=issues)
```

- [ ] **Step 4: Run the ownership tests and confirm they pass**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest tests.test_ownership -v
```

Expected: all tests in `tests.test_ownership` pass.

- [ ] **Step 5: Commit Task 1**

Run:

```bash
git add ownership.py tests/test_ownership.py
git commit -m "feat: add page component ownership model"
```

## Task 2: Ownership Builder From Existing Page Decisions

**Files:**
- Modify: `ownership.py`
- Modify: `tests/test_ownership.py`

- [ ] **Step 1: Write failing builder tests**

Append these tests to `tests/test_ownership.py`:

```python
class OwnershipBuilderTests(unittest.TestCase):
    def test_builder_assigns_core_component_kinds_once(self):
        blocks = [
            block("p011b0001", "1. Introduction", 72, 80, 190, 96),
            block("p011b0002", "Body paragraph for translation.", 72, 110, 300, 145),
            block("p011b0003", "Smith, J. 2019. Reference title. In ACL.", 72, 620, 300, 645),
            block("p011b0004", "11", 300, 760, 320, 772),
            block("p011b0005", "Conference footer", 72, 780, 250, 790),
        ]
        classes = {
            "p011b0001": "heading",
            "p011b0002": "body",
            "p011b0003": "reference",
            "p011b0004": "page_number",
            "p011b0005": "header_footer",
        }

        components = ownership.build_page_components(
            page_num=11,
            blocks=blocks,
            classes=classes,
            visual_regions=[],
            visual_covered_text_ids=set(),
            duplicate_ids=set(),
        )
        owner_by_id = ownership.component_by_source_id(components)

        self.assertEqual(owner_by_id["p011b0001"].component_kind, ownership.COMPONENT_KIND_TRANSLATED_TEXT)
        self.assertEqual(owner_by_id["p011b0002"].component_kind, ownership.COMPONENT_KIND_TRANSLATED_TEXT)
        self.assertEqual(owner_by_id["p011b0003"].component_kind, ownership.COMPONENT_KIND_REFERENCE)
        self.assertEqual(owner_by_id["p011b0004"].component_kind, ownership.COMPONENT_KIND_PAGE_NUMBER)
        self.assertEqual(owner_by_id["p011b0005"].component_kind, ownership.COMPONENT_KIND_HEADER_FOOTER)
        self.assertTrue(ownership.validate_ownership(11, blocks, components).ok)

    def test_visual_component_owns_region_and_covered_internal_labels(self):
        blocks = [
            block("p015b0009", "T 1 '", 221, 98, 229, 106),
            block("p015b0010", "T M '", 257, 98, 265, 106),
            block("p015b0097", "Body after figure.", 72, 488, 290, 552),
        ]
        components = ownership.build_page_components(
            page_num=15,
            blocks=blocks,
            classes={"p015b0009": "body", "p015b0010": "body", "p015b0097": "body"},
            visual_regions=[
                {
                    "source_ids": ["p015b0009"],
                    "bbox": (111.0, 65.0, 484.0, 402.0),
                    "kind": "figure_region",
                }
            ],
            visual_covered_text_ids={"p015b0010"},
            duplicate_ids=set(),
        )

        visual_components = [component for component in components if component.component_kind == ownership.COMPONENT_KIND_VISUAL]
        self.assertEqual(len(visual_components), 1)
        self.assertEqual(set(visual_components[0].source_ids), {"p015b0009", "p015b0010"})
        self.assertEqual(ownership.component_by_source_id(components)["p015b0097"].component_kind, ownership.COMPONENT_KIND_TRANSLATED_TEXT)

    def test_reference_components_do_not_capture_adjacent_body_column(self):
        blocks = [
            block("p012b0002", "for natural language understanding. In Proceedings", 82, 67, 292, 109),
            block("p012b0001", "Additional details are presented in Appendix B.", 320, 66, 526, 90),
        ]
        components = ownership.build_page_components(
            page_num=12,
            blocks=blocks,
            classes={"p012b0002": "reference", "p012b0001": "body"},
            visual_regions=[],
            visual_covered_text_ids=set(),
            duplicate_ids=set(),
        )
        owner_by_id = ownership.component_by_source_id(components)

        self.assertEqual(owner_by_id["p012b0002"].component_kind, ownership.COMPONENT_KIND_REFERENCE)
        self.assertEqual(owner_by_id["p012b0001"].component_kind, ownership.COMPONENT_KIND_TRANSLATED_TEXT)
        self.assertLess(owner_by_id["p012b0002"].source_bbox[2], owner_by_id["p012b0001"].source_bbox[0])

    def test_unknown_nontrivial_content_is_preserved_not_dropped(self):
        blocks = [block("p004b0008", "x := y + z", 120, 220, 240, 245)]
        components = ownership.build_page_components(
            page_num=4,
            blocks=blocks,
            classes={"p004b0008": "unknown"},
            visual_regions=[],
            visual_covered_text_ids=set(),
            duplicate_ids=set(),
        )

        owner = ownership.component_by_source_id(components)["p004b0008"]
        self.assertEqual(owner.component_kind, ownership.COMPONENT_KIND_UNKNOWN)
        self.assertEqual(owner.render_strategy, "original_image_clip")
```

- [ ] **Step 2: Run the builder tests and confirm they fail**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest tests.test_ownership.OwnershipBuilderTests -v
```

Expected: `ERROR` mentioning missing `build_page_components`.

- [ ] **Step 3: Implement deterministic component building**

Append this implementation to `ownership.py`:

```python
NORMAL_TRANSLATED_CLASSES = {"title", "heading", "subheading", "body"}
VISUAL_CLASSES = {"figure_region", "table_region", "formula_region", "code_region"}
HEADER_FOOTER_CLASSES = {"header_footer", "journal_footer"}


def _next_component_id(page_num: int, index: int) -> str:
    return f"p{int(page_num):03d}c{int(index):04d}"


def _make_component(
    page_num: int,
    index: int,
    component_kind: str,
    source_ids: list[str],
    block_by_id: dict[str, dict],
    *,
    clip_bbox=None,
    confidence=CONFIDENCE_INFERRED,
    reason_codes=None,
    render_strategy="",
) -> PageComponent:
    unique_ids = sorted({str(source_id) for source_id in source_ids if str(source_id) in block_by_id})
    source_bbox = bbox_union(block_bbox(block_by_id[source_id]) for source_id in unique_ids)
    return PageComponent(
        component_id=_next_component_id(page_num, index),
        component_kind=component_kind,
        source_ids=unique_ids,
        source_bbox=source_bbox,
        clip_bbox=clip_bbox,
        confidence=confidence,
        reason_codes=sorted(str(code) for code in (reason_codes or [])),
        render_strategy=render_strategy,
    )


def _best_visual_region_for_block(block, visual_regions):
    block_box = block_bbox(block)
    best_index = None
    best_overlap = 0.0
    for index, region in enumerate(visual_regions):
        region_box = tuple(region.get("bbox") or block_box)
        overlap = bbox_overlap_area(block_box, region_box)
        if overlap > best_overlap:
            best_index = index
            best_overlap = overlap
    return best_index


def build_page_components(
    *,
    page_num: int,
    blocks,
    classes: dict[str, str],
    visual_regions: list[dict],
    visual_covered_text_ids: set[str],
    duplicate_ids: set[str],
) -> list[PageComponent]:
    block_by_id = {str(block["id"]): block for block in blocks}
    assigned: set[str] = set()
    components: list[PageComponent] = []
    component_index = 1

    for region_index, region in enumerate(visual_regions):
        source_ids = {str(source_id) for source_id in region.get("source_ids", [])}
        for source_id in sorted(str(source_id) for source_id in visual_covered_text_ids):
            block = block_by_id.get(source_id)
            if block is None:
                continue
            best_region_index = _best_visual_region_for_block(block, visual_regions)
            if best_region_index == region_index:
                source_ids.add(source_id)
        source_ids = sorted(source_ids - set(duplicate_ids))
        if not source_ids:
            continue
        clip_bbox = tuple(region.get("bbox")) if region.get("bbox") else bbox_union(block_bbox(block_by_id[source_id]) for source_id in source_ids)
        components.append(
            _make_component(
                page_num,
                component_index,
                COMPONENT_KIND_VISUAL,
                source_ids,
                block_by_id,
                clip_bbox=clip_bbox,
                confidence=CONFIDENCE_CONSERVATIVE,
                reason_codes=[str(region.get("kind") or "visual_region"), "visual_region"],
                render_strategy="original_image_clip",
            )
        )
        component_index += 1
        assigned.update(source_ids)

    for block in blocks:
        source_id = str(block["id"])
        if source_id in assigned:
            continue
        classification = classes.get(source_id, "unknown")
        if source_id in duplicate_ids:
            component_kind = COMPONENT_KIND_DUPLICATE
            confidence = CONFIDENCE_DETERMINISTIC
            render_strategy = "skip_explicitly"
            reason_codes = ["duplicated_extraction"]
        elif classification == "page_number":
            component_kind = COMPONENT_KIND_PAGE_NUMBER
            confidence = CONFIDENCE_DETERMINISTIC
            render_strategy = "skip_explicitly"
            reason_codes = ["page_number"]
        elif classification in HEADER_FOOTER_CLASSES:
            component_kind = COMPONENT_KIND_HEADER_FOOTER
            confidence = CONFIDENCE_DETERMINISTIC
            render_strategy = "skip_explicitly" if classification == "header_footer" else "original_selectable_text"
            reason_codes = [classification]
        elif classification == "reference":
            component_kind = COMPONENT_KIND_REFERENCE
            confidence = CONFIDENCE_INFERRED
            render_strategy = "original_selectable_text"
            reason_codes = ["reference"]
        elif classification in NORMAL_TRANSLATED_CLASSES:
            component_kind = COMPONENT_KIND_TRANSLATED_TEXT
            confidence = CONFIDENCE_INFERRED
            render_strategy = "translated_text"
            reason_codes = [classification]
        elif classification in VISUAL_CLASSES:
            component_kind = COMPONENT_KIND_VISUAL
            confidence = CONFIDENCE_CONSERVATIVE
            render_strategy = "original_image_clip"
            reason_codes = [classification, "visual_classification"]
        elif is_trivial_block(block):
            component_kind = COMPONENT_KIND_SKIP
            confidence = CONFIDENCE_DETERMINISTIC
            render_strategy = "skip_explicitly"
            reason_codes = ["trivial_block"]
        else:
            component_kind = COMPONENT_KIND_UNKNOWN
            confidence = CONFIDENCE_CONSERVATIVE
            render_strategy = "original_image_clip"
            reason_codes = [classification, "unknown_preserve"]

        components.append(
            _make_component(
                page_num,
                component_index,
                component_kind,
                [source_id],
                block_by_id,
                clip_bbox=None,
                confidence=confidence,
                reason_codes=reason_codes,
                render_strategy=render_strategy,
            )
        )
        component_index += 1
        assigned.add(source_id)

    return components
```

- [ ] **Step 4: Run ownership tests**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest tests.test_ownership -v
```

Expected: all ownership tests pass.

- [ ] **Step 5: Commit Task 2**

Run:

```bash
git add ownership.py tests/test_ownership.py
git commit -m "feat: build page components from page classification"
```

## Task 3: Ownership-Driven Translation Batches

**Files:**
- Modify: `ownership.py`
- Modify: `translate_pdf_via_codex.py`
- Modify: `translate_pdf_parallel.py`
- Modify: `tests/test_ownership.py`
- Modify: `tests/test_render_plan.py`
- Modify: `tests/test_translate_pdf_parallel.py`

- [ ] **Step 1: Write failing translation-filter tests**

Append this test to `tests/test_ownership.py`:

```python
class OwnershipTranslationFilterTests(unittest.TestCase):
    def test_only_translated_text_components_enter_translation_batches(self):
        blocks = [
            block("p020b0001", "Heading", 72, 80, 160, 96),
            block("p020b0002", "Body paragraph.", 72, 110, 300, 145),
            block("p020b0003", "Reference. 2020. Title.", 72, 620, 300, 645),
            block("p020b0004", "Figure label", 160, 200, 240, 220),
            block("p020b0005", "x := y + z", 160, 240, 260, 260),
        ]
        components = ownership.build_page_components(
            page_num=20,
            blocks=blocks,
            classes={
                "p020b0001": "heading",
                "p020b0002": "body",
                "p020b0003": "reference",
                "p020b0004": "figure_region",
                "p020b0005": "unknown",
            },
            visual_regions=[{"source_ids": ["p020b0004"], "bbox": (150, 190, 250, 230)}],
            visual_covered_text_ids=set(),
            duplicate_ids=set(),
        )

        items = ownership.translation_items_from_components(blocks, components, classes={"p020b0001": "heading", "p020b0002": "body"})

        self.assertEqual(items, [{"id": "p020b0001", "text": "Heading"}, {"id": "p020b0002", "text": "Body paragraph."}])
```

In `tests/test_translate_pdf_parallel.py`, add this test:

```python
    def test_build_page_batches_uses_pipeline_ownership_context(self):
        blocks = [
            {"id": "p001b0001", "text": "Body text.", "xMin": 72.0, "yMin": 80.0, "xMax": 260.0, "yMax": 100.0},
            {"id": "p001b0002", "text": "Figure label", "xMin": 100.0, "yMin": 120.0, "xMax": 180.0, "yMax": 140.0},
        ]

        with (
            patch.object(parallel.pipeline, "build_visual_regions", return_value=[{"source_ids": ["p001b0002"], "bbox": (90, 110, 190, 150)}]),
            patch.object(parallel.pipeline, "classify_blocks", return_value={"p001b0001": "body", "p001b0002": "body"}),
        ):
            batches = parallel.build_page_batches([(1, blocks)], max_chars=7000, page_size=(400, 400))

        self.assertEqual([[item["id"] for item in batch.items] for batch in batches], [["p001b0001"]])
```

- [ ] **Step 2: Run focused tests and confirm failures**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest \
  tests.test_ownership.OwnershipTranslationFilterTests \
  tests.test_translate_pdf_parallel.ParallelBatchPlanningTests.test_build_page_batches_uses_pipeline_ownership_context -v
```

Expected: ownership filter test fails with missing `translation_items_from_components`; parallel test fails until batch planning uses ownership.

- [ ] **Step 3: Add translation filtering to `ownership.py`**

Append this function:

```python
def translation_items_from_components(blocks, components: list[PageComponent], *, classes: dict[str, str]) -> list[dict]:
    block_by_id = {str(block["id"]): block for block in blocks}
    items = []
    for component in sorted(components, key=lambda item: (item.source_bbox[1], item.source_bbox[0], item.component_id)):
        if component.component_kind != COMPONENT_KIND_TRANSLATED_TEXT:
            continue
        for source_id in component.source_ids:
            block = block_by_id.get(str(source_id))
            if block is None:
                continue
            if classes.get(str(source_id), "unknown") not in NORMAL_TRANSLATED_CLASSES:
                continue
            text = str(block.get("text", "")).strip()
            if text and not is_trivial_block(block):
                items.append({"id": str(source_id), "text": text})
    return items
```

- [ ] **Step 4: Add `prepare_page_ownership()` in `translate_pdf_via_codex.py`**

At the import section, add:

```python
import ownership
```

Add this dataclass near the batching helpers:

```python
@dataclass(frozen=True)
class PageOwnershipContext:
    page_num: int
    blocks: list[dict]
    classes: dict[str, str]
    visual_regions: list[dict]
    visual_covered_text_ids: set[str]
    duplicate_ids: set[str]
    components: list[ownership.PageComponent]
    validation: ownership.OwnershipValidationResult
```

Add this helper before `build_batches()`:

```python
def prepare_page_ownership(
    page_num: int,
    page_blocks,
    *,
    page_size=None,
    job_paths=None,
    bbox_lines=None,
    in_reference_section: bool = False,
) -> tuple[PageOwnershipContext, bool]:
    blocks = list(page_blocks)
    visual_regions = build_visual_regions(blocks)
    classes = classify_blocks(blocks, visual_regions)
    classes, next_in_reference_section, _ = apply_reference_continuation(blocks, classes, in_reference_section)
    source_image = source_page_image_path(job_paths, page_num) if job_paths else None
    visual_covered_text_ids = visual_translation_protected_ids(
        blocks,
        classes,
        visual_regions,
        page_size=page_size,
        page_num=page_num,
        source_image_path=source_image,
        bbox_lines=bbox_lines,
    )
    duplicate_ids = {
        block_id
        for block_id in nested_duplicate_block_ids(blocks)
        if classes.get(block_id) in {"body", "heading", "title"}
    }
    duplicate_ids.update(contained_standalone_label_ids(blocks, classes))
    components = ownership.build_page_components(
        page_num=page_num,
        blocks=blocks,
        classes=classes,
        visual_regions=visual_regions,
        visual_covered_text_ids=set(visual_covered_text_ids),
        duplicate_ids=set(duplicate_ids),
    )
    validation = ownership.validate_ownership(page_num, blocks, components)
    return (
        PageOwnershipContext(
            page_num=page_num,
            blocks=blocks,
            classes=classes,
            visual_regions=visual_regions,
            visual_covered_text_ids=set(visual_covered_text_ids),
            duplicate_ids=set(duplicate_ids),
            components=components,
            validation=validation,
        ),
        next_in_reference_section,
    )
```

- [ ] **Step 5: Route `build_batches()` through ownership**

In `translate_pdf_via_codex.py`, replace the per-page classification/filtering body inside `build_batches()` with:

```python
        context, in_reference_section = prepare_page_ownership(
            page_num,
            page,
            page_size=page_size,
            job_paths=job_paths,
            bbox_lines=lines_by_page.get(page_num),
            in_reference_section=in_reference_section,
        )
        page_items = ownership.translation_items_from_components(context.blocks, context.components, classes=context.classes)
        for item in page_items:
            block = next(block for block in context.blocks if block["id"] == item["id"])
            if should_preserve_first_page_metadata_as_image(block):
                continue
            if should_preserve_as_image(block):
                continue
            blocks.append(block)
```

In `translate_pdf_parallel.py`, replace the page loop filter inside `build_page_batches()` with:

```python
        context, in_reference_section = pipeline.prepare_page_ownership(
            page_num,
            page_blocks,
            page_size=page_size,
            job_paths=job_paths,
            bbox_lines=lines_by_page.get(page_num),
            in_reference_section=in_reference_section,
        )
        page_items = pipeline.ownership.translation_items_from_components(
            context.blocks,
            context.components,
            classes=context.classes,
        )
        current = []
        current_chars = 0
        chunk_idx = 1
        for item in page_items:
            item_chars = len(item["text"])
            if current and current_chars + item_chars > max_chars:
                batches.append(PageBatch(page_num=page_num, chunk_idx=chunk_idx, items=current))
                chunk_idx += 1
                current = []
                current_chars = 0
            current.append(item)
            current_chars += item_chars
        if current:
            batches.append(PageBatch(page_num=page_num, chunk_idx=chunk_idx, items=current))
```

- [ ] **Step 6: Run focused translation-batch tests**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest \
  tests.test_ownership.OwnershipTranslationFilterTests \
  tests.test_translate_pdf_parallel -v
```

Expected: ownership translation-filter tests and all parallel batch-planning tests pass.

- [ ] **Step 7: Commit Task 3**

Run:

```bash
git add ownership.py translate_pdf_via_codex.py translate_pdf_parallel.py tests/test_ownership.py tests/test_translate_pdf_parallel.py
git commit -m "feat: build translation batches from component ownership"
```

## Task 4: Render Plan Ownership Serialization

**Files:**
- Modify: `render_plan.py`
- Modify: `tests/test_render_plan.py`

- [ ] **Step 1: Write failing render-plan serialization tests**

Append these tests to `tests/test_render_plan.py`:

```python
class RenderPlanOwnershipSerializationTests(unittest.TestCase):
    def test_render_plan_serializes_components_and_coverage_component_fields(self):
        component = pdf.ownership.PageComponent(
            component_id="p003c0001",
            component_kind=pdf.ownership.COMPONENT_KIND_VISUAL,
            source_ids=["p003b0002"],
            source_bbox=(100, 100, 180, 140),
            clip_bbox=(96, 96, 184, 144),
            confidence=pdf.ownership.CONFIDENCE_CONSERVATIVE,
            reason_codes=["figure_region"],
            render_strategy="original_image_clip",
        )
        plan = pdf.PageRenderPlan(page_num=3)
        plan.components.append(component)
        plan.ownership_validation = pdf.ownership.OwnershipValidationResult([])
        plan.items.append(
            pdf.RenderItem(
                "original_image_clip",
                ["p003b0002"],
                (96, 96, 184, 144),
                fallback_reason="visual_region",
                component_id="p003c0001",
                component_kind="visual",
            )
        )
        plan.ledger.append(
            pdf.CoverageEntry(
                "p003b0002",
                "figure_region",
                "original_image_clip",
                True,
                "visual_region",
                component_id="p003c0001",
                component_kind="visual",
            )
        )

        payload = pdf.render_plan_to_json(plan)

        self.assertEqual(payload["render_items"][0]["component_id"], "p003c0001")
        self.assertEqual(payload["coverage_ledger"][0]["component_kind"], "visual")
        self.assertEqual(payload["ownership_components"][0]["source_ids"], ["p003b0002"])
        self.assertEqual(payload["ownership_validation"], {"issues": [], "ok": True})
```

- [ ] **Step 2: Run the serialization test and confirm failure**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest \
  tests.test_render_plan.RenderPlanOwnershipSerializationTests -v
```

Expected: failure because `RenderItem`, `CoverageEntry`, or `PageRenderPlan` lacks ownership fields.

- [ ] **Step 3: Extend `render_plan.py` dataclasses and serializers**

In `render_plan.py`, add:

```python
import ownership
```

Update `RenderItem`:

```python
@dataclass
class RenderItem:
    kind: str
    source_ids: list[str]
    bbox: tuple[float, float, float, float]
    text: str = ""
    font_size: float | None = None
    style_name: str = ""
    color: tuple[float, float, float] = VECTOR_BODY_COLOR
    fallback_reason: str = ""
    component_id: str = ""
    component_kind: str = ""
```

Update `CoverageEntry`:

```python
@dataclass
class CoverageEntry:
    block_id: str
    classification: str
    render_kind: str
    rendered: bool
    fallback_reason: str = ""
    reference_signature: dict | None = None
    component_id: str = ""
    component_kind: str = ""
```

Update `PageRenderPlan`:

```python
@dataclass
class PageRenderPlan:
    page_num: int
    items: list[RenderItem] = field(default_factory=list)
    ledger: list[CoverageEntry] = field(default_factory=list)
    protected_boxes: list[tuple[float, float, float, float]] = field(default_factory=list)
    components: list[ownership.PageComponent] = field(default_factory=list)
    ownership_validation: ownership.OwnershipValidationResult = field(default_factory=ownership.OwnershipValidationResult)
```

Add ownership fields in `render_item_to_json()`:

```python
        "component_id": item.component_id,
        "component_kind": item.component_kind,
```

Add ownership fields in `coverage_entry_to_json()`:

```python
        "component_id": entry.component_id,
        "component_kind": entry.component_kind,
```

Update `render_plan_to_json()`:

```python
def render_plan_to_json(plan: PageRenderPlan, validation_results: dict | list | None = None) -> dict:
    return {
        "page_num": int(plan.page_num),
        "render_items": [render_item_to_json(item) for item in plan.items],
        "coverage_ledger": [coverage_entry_to_json(entry) for entry in plan.ledger],
        "protected_regions": [{"bbox": bbox_to_json(box)} for box in plan.protected_boxes],
        "ownership_components": ownership.components_to_json(plan.components),
        "ownership_validation": ownership.ownership_validation_to_json(plan.ownership_validation),
        "validation_results": _stable_json_value(validation_results) if validation_results is not None else [],
    }
```

- [ ] **Step 4: Run render-plan serialization tests**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest \
  tests.test_render_plan.RenderPlanOwnershipSerializationTests -v
```

Expected: test passes.

- [ ] **Step 5: Commit Task 4**

Run:

```bash
git add render_plan.py tests/test_render_plan.py
git commit -m "feat: serialize ownership in render plans"
```

## Task 5: Render-Layer Exclusivity Validation

**Files:**
- Modify: `ownership.py`
- Modify: `tests/test_ownership.py`
- Modify: `render_plan.py`
- Modify: `tests/test_render_plan.py`

- [ ] **Step 1: Write failing validation tests**

Append these tests to `tests/test_ownership.py`:

```python
class RenderLayerOwnershipValidationTests(unittest.TestCase):
    def test_source_id_cannot_render_as_image_and_text(self):
        plan = type("Plan", (), {})()
        plan.page_num = 9
        plan.items = [
            type("Item", (), {"kind": "original_image_clip", "source_ids": ["p009b0005"], "bbox": (80, 600, 306, 656), "component_id": "p009c0001", "component_kind": "visual"})(),
            type("Item", (), {"kind": "translated_text", "source_ids": ["p009b0005"], "bbox": (85, 610, 120, 630), "component_id": "p009c0002", "component_kind": "translated_text"})(),
        ]
        components = [
            ownership.PageComponent("p009c0001", "visual", ["p009b0005"], (80, 600, 306, 656), (80, 600, 306, 656), "conservative", ["table_region"], "original_image_clip"),
            ownership.PageComponent("p009c0002", "translated_text", ["p009b0005"], (85, 610, 120, 630), None, "inferred", ["body"], "translated_text"),
        ]

        result = ownership.validate_render_layer_exclusivity(plan, components)

        self.assertFalse(result.ok)
        self.assertEqual(result.issues[0].issue_code, "source_rendered_as_image_and_text")

    def test_text_cannot_overlap_unrelated_visual_component(self):
        plan = type("Plan", (), {})()
        plan.page_num = 15
        plan.items = [
            type("Item", (), {"kind": "original_image_clip", "source_ids": ["p015b0009"], "bbox": (111, 65, 484, 402), "component_id": "p015c0001", "component_kind": "visual"})(),
            type("Item", (), {"kind": "translated_text", "source_ids": ["p015b0097"], "bbox": (120, 100, 250, 140), "component_id": "p015c0002", "component_kind": "translated_text"})(),
        ]
        components = [
            ownership.PageComponent("p015c0001", "visual", ["p015b0009"], (111, 65, 484, 402), (111, 65, 484, 402), "conservative", ["figure_region"], "original_image_clip"),
            ownership.PageComponent("p015c0002", "translated_text", ["p015b0097"], (120, 100, 250, 140), None, "inferred", ["body"], "translated_text"),
        ]

        result = ownership.validate_render_layer_exclusivity(plan, components)

        self.assertFalse(result.ok)
        self.assertEqual(result.issues[0].issue_code, "text_over_visual_component")

    def test_visual_clip_cannot_capture_translated_component(self):
        plan = type("Plan", (), {})()
        plan.page_num = 12
        plan.items = [
            type("Item", (), {"kind": "original_image_clip", "source_ids": ["p012b0002"], "bbox": (70, 60, 526, 130), "component_id": "p012c0001", "component_kind": "visual"})(),
            type("Item", (), {"kind": "translated_text", "source_ids": ["p012b0001"], "bbox": (320, 66, 526, 90), "component_id": "p012c0002", "component_kind": "translated_text"})(),
        ]
        components = [
            ownership.PageComponent("p012c0001", "visual", ["p012b0002"], (82, 67, 292, 109), (70, 60, 526, 130), "conservative", ["reference_like"], "original_image_clip"),
            ownership.PageComponent("p012c0002", "translated_text", ["p012b0001"], (320, 66, 526, 90), None, "inferred", ["body"], "translated_text"),
        ]

        result = ownership.validate_render_layer_exclusivity(plan, components)

        self.assertFalse(result.ok)
        self.assertEqual(result.issues[0].issue_code, "visual_clip_overcaptures_translated_component")
```

- [ ] **Step 2: Run validation tests and confirm failure**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest \
  tests.test_ownership.RenderLayerOwnershipValidationTests -v
```

Expected: `ERROR` for missing `validate_render_layer_exclusivity`.

- [ ] **Step 3: Implement render-layer validation in `ownership.py`**

Append:

```python
def _item_attr(item, name, default=None):
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _item_bbox(item):
    return tuple(float(value) for value in _item_attr(item, "bbox", (0, 0, 0, 0)))


def _item_source_ids(item) -> list[str]:
    return [str(source_id) for source_id in (_item_attr(item, "source_ids", []) or [])]


def _item_component_id(item) -> str:
    return str(_item_attr(item, "component_id", "") or "")


def _significant_overlap(left, right, *, min_overlap_ratio: float, min_overlap_height: float) -> bool:
    if bbox_overlap_height(left, right) < min_overlap_height:
        return False
    overlap = bbox_overlap_area(left, right)
    if overlap <= 0:
        return False
    return overlap > min(bbox_area(left), bbox_area(right)) * min_overlap_ratio


def validate_render_layer_exclusivity(
    plan,
    components: list[PageComponent],
    *,
    min_overlap_ratio: float = 0.05,
    min_overlap_height: float = 4.0,
    max_visual_overcapture_ratio: float = 0.02,
) -> OwnershipValidationResult:
    page_num = int(getattr(plan, "page_num", 0))
    issues: list[OwnershipIssue] = []
    items = list(getattr(plan, "items", []))
    image_items = [item for item in items if _item_attr(item, "kind") in IMAGE_RENDER_KINDS]
    text_items = [item for item in items if _item_attr(item, "kind") in TEXT_RENDER_KINDS]
    image_ids = {source_id for item in image_items for source_id in _item_source_ids(item)}
    text_ids = {source_id for item in text_items for source_id in _item_source_ids(item)}

    for source_id in sorted(image_ids & text_ids):
        issues.append(
            OwnershipIssue(
                issue_code="source_rendered_as_image_and_text",
                severity="error",
                page_num=page_num,
                message=f"source block {source_id} is rendered by both image and text layers",
                source_ids=[source_id],
            )
        )

    visual_components = [component for component in components if component.component_kind == COMPONENT_KIND_VISUAL]
    translated_components = [component for component in components if component.component_kind == COMPONENT_KIND_TRANSLATED_TEXT]
    component_by_id = {component.component_id: component for component in components}

    for text_item in text_items:
        text_box = _item_bbox(text_item)
        text_component_id = _item_component_id(text_item)
        for visual_component in visual_components:
            if visual_component.component_id == text_component_id:
                continue
            visual_box = visual_component.clip_bbox or visual_component.source_bbox
            if _significant_overlap(text_box, visual_box, min_overlap_ratio=min_overlap_ratio, min_overlap_height=min_overlap_height):
                issues.append(
                    OwnershipIssue(
                        issue_code="text_over_visual_component",
                        severity="error",
                        page_num=page_num,
                        message=f"text item overlaps unrelated visual component {visual_component.component_id}",
                        source_ids=_item_source_ids(text_item),
                        component_ids=[text_component_id, visual_component.component_id],
                        bboxes=[text_box, visual_box],
                    )
                )

    for image_item in image_items:
        image_component_id = _item_component_id(image_item)
        image_component = component_by_id.get(image_component_id)
        if image_component is None or image_component.component_kind != COMPONENT_KIND_VISUAL:
            continue
        image_box = _item_bbox(image_item)
        for translated_component in translated_components:
            if translated_component.component_id == image_component_id:
                continue
            overlap = bbox_overlap_area(image_box, translated_component.source_bbox)
            if overlap > bbox_area(translated_component.source_bbox) * max_visual_overcapture_ratio:
                issues.append(
                    OwnershipIssue(
                        issue_code="visual_clip_overcaptures_translated_component",
                        severity="error",
                        page_num=page_num,
                        message=f"visual component {image_component_id} captures translated component {translated_component.component_id}",
                        source_ids=translated_component.source_ids,
                        component_ids=[image_component_id, translated_component.component_id],
                        bboxes=[image_box, translated_component.source_bbox],
                    )
                )

    return OwnershipValidationResult(issues=issues)
```

- [ ] **Step 4: Run validation tests**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest \
  tests.test_ownership.RenderLayerOwnershipValidationTests -v
```

Expected: tests pass.

- [ ] **Step 5: Wire validation into `render_plan.validate_plan_layout()`**

In `render_plan.py`, extend `validate_plan_layout()` after page-bound checks:

```python
    ownership_errors = ownership.validate_render_layer_exclusivity(plan, plan.components)
    errors.extend(issue.message for issue in ownership_errors.issues)
```

- [ ] **Step 6: Run render-plan tests**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest tests.test_render_plan -v
```

Expected: render-plan tests pass.

- [ ] **Step 7: Commit Task 5**

Run:

```bash
git add ownership.py render_plan.py tests/test_ownership.py tests/test_render_plan.py
git commit -m "feat: validate render layer ownership exclusivity"
```

## Task 6: Ownership-Driven Render Planning

**Files:**
- Modify: `translate_pdf_via_codex.py`
- Modify: `render_plan.py`
- Modify: `tests/test_render_plan.py`

- [ ] **Step 1: Write failing render-planning tests**

Append to `tests/test_render_plan.py`:

```python
class OwnershipDrivenRenderPlanTests(unittest.TestCase):
    def test_visual_owned_block_is_not_rendered_as_text(self):
        blocks = [
            block("p015b0009", 15, "T 1 '", x0=221, y0=98, x1=229, y1=106),
            block("p015b0010", 15, "T M '", x0=257, y0=98, x1=265, y1=106),
            block("p015b0097", 15, "Body after figure.", x0=72, y0=488, x1=290, y1=552),
        ]
        visual_regions = [{"source_ids": ["p015b0009"], "bbox": (111, 65, 484, 402)}]

        with (
            patch.object(pdf, "build_visual_regions", return_value=visual_regions),
            patch.object(pdf, "classify_blocks", return_value={"p015b0009": "body", "p015b0010": "body", "p015b0097": "body"}),
            patch.object(pdf, "visual_translation_protected_ids", return_value={"p015b0010"}),
        ):
            plan = pdf.build_page_render_plan(
                15,
                blocks,
                {"p015b0097": "图后的正文。", "p015b0009": "错误译文", "p015b0010": "错误译文"},
                page_size=(623, 801),
                bbox_lines=None,
            )

        text_source_ids = {source_id for item in plan.items if item.kind in {"translated_text", "original_selectable_text"} for source_id in item.source_ids}
        image_source_ids = {source_id for item in plan.items if item.kind == "original_image_clip" for source_id in item.source_ids}

        self.assertIn("p015b0009", image_source_ids)
        self.assertIn("p015b0010", image_source_ids)
        self.assertNotIn("p015b0009", text_source_ids)
        self.assertNotIn("p015b0010", text_source_ids)
        self.assertTrue(plan.ownership_validation.ok)

    def test_reference_owned_blocks_remain_original_and_body_column_is_translated(self):
        blocks = [
            block("p012b0002", 12, "for natural language understanding. In Proceedings", x0=82, y0=67, x1=292, y1=109),
            block("p012b0001", 12, "Additional details are presented in Appendix B.", x0=320, y0=66, x1=526, y1=90),
        ]

        plan = pdf.build_page_render_plan(
            12,
            blocks,
            {"p012b0001": "更多细节见附录 B。"},
            page_size=(623, 801),
            bbox_lines=None,
            force_reference=True,
        )
        reference_entries = [entry for entry in plan.ledger if entry.block_id == "p012b0002"]
        body_entries = [entry for entry in plan.ledger if entry.block_id == "p012b0001"]

        self.assertEqual(reference_entries[0].component_kind, "reference")
        self.assertEqual(reference_entries[0].render_kind, "original_selectable_text")
        self.assertEqual(body_entries[0].component_kind, "translated_text")
        self.assertEqual(body_entries[0].render_kind, "translated_text")
```

- [ ] **Step 2: Run render-planning tests and confirm failure**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest \
  tests.test_render_plan.OwnershipDrivenRenderPlanTests -v
```

Expected: failure because render items and ledger entries do not consistently carry ownership, or visual-covered labels still enter text rendering.

- [ ] **Step 3: Initialize ownership context at the top of `build_page_render_plan()`**

In `translate_pdf_via_codex.py`, replace the initial `visual_regions = ...` / `classes = ...` block in `build_page_render_plan()` with:

```python
    context, _ = prepare_page_ownership(
        page_num,
        blocks,
        page_size=page_size,
        job_paths=None,
        bbox_lines=bbox_lines,
        in_reference_section=force_reference,
    )
    visual_regions = context.visual_regions
    classes = dict(context.classes)
    visual_covered_text_ids = set(context.visual_covered_text_ids)
    duplicate_ids = set(context.duplicate_ids)
    plan.components = list(context.components)
    plan.ownership_validation = context.validation
    component_by_source_id = ownership.component_by_source_id(plan.components)
```

Keep the existing `force_reference` behavior by passing `in_reference_section=force_reference` as shown above and deleting the old local `if force_reference: classes, _, _ = ...` branch.

- [ ] **Step 4: Add ownership helpers in `translate_pdf_via_codex.py`**

Add these helpers near `reference_coverage_entry()`:

```python
def component_for_source_id(component_by_source_id: dict[str, ownership.PageComponent], source_id: str) -> ownership.PageComponent | None:
    return component_by_source_id.get(str(source_id))


def render_item_with_component(kind, source_ids, bbox, *, component, **kwargs) -> RenderItem:
    return RenderItem(
        kind,
        list(source_ids),
        bbox,
        component_id="" if component is None else component.component_id,
        component_kind="" if component is None else component.component_kind,
        **kwargs,
    )


def coverage_entry_with_component(block_id, classification, render_kind, rendered, fallback_reason="", *, component, reference_signature=None):
    return CoverageEntry(
        block_id,
        classification,
        render_kind,
        rendered,
        fallback_reason,
        reference_signature=reference_signature,
        component_id="" if component is None else component.component_id,
        component_kind="" if component is None else component.component_kind,
    )
```

- [ ] **Step 5: Update visual-region render items and ledger entries**

In the visual-region loop, replace the `RenderItem(...)` creation with:

```python
        component = next(
            (
                candidate
                for candidate in plan.components
                if candidate.component_kind == ownership.COMPONENT_KIND_VISUAL
                and set(region["source_ids"]) & set(candidate.source_ids)
            ),
            None,
        )
        visual_source_ids = component.source_ids if component is not None else region["source_ids"]
        plan.items.append(
            render_item_with_component(
                "original_image_clip",
                visual_source_ids,
                region_bbox,
                component=component,
                fallback_reason="visual_region",
            )
        )
```

Replace each visual coverage entry append with:

```python
            source_component = component_for_source_id(component_by_source_id, source_id)
            plan.ledger.append(
                coverage_entry_with_component(
                    source_id,
                    classes.get(source_id, "figure_region"),
                    "original_image_clip",
                    True,
                    "visual_region",
                    component=source_component,
                )
            )
```

- [ ] **Step 6: Enforce ownership in the block loop**

At the top of the `for block in blocks:` loop, after `classification = ...`, add:

```python
        component = component_for_source_id(component_by_source_id, block["id"])
        if component is not None and component.component_kind == ownership.COMPONENT_KIND_VISUAL:
            if not any(block["id"] in item.source_ids for item in plan.items if item.kind == "original_image_clip"):
                clip_bbox = component.clip_bbox or component.source_bbox
                plan.items.append(
                    render_item_with_component(
                        "original_image_clip",
                        component.source_ids,
                        clip_bbox,
                        component=component,
                        fallback_reason="visual_component",
                    )
                )
                plan.protected_boxes.append(clip_bbox)
            if not any(entry.block_id == block["id"] for entry in plan.ledger):
                plan.ledger.append(
                    coverage_entry_with_component(
                        block["id"],
                        classification,
                        "original_image_clip",
                        True,
                        "visual_component",
                        component=component,
                    )
                )
            continue
```

For all remaining `CoverageEntry(...)` calls in `build_page_render_plan()`, replace them with `coverage_entry_with_component(..., component=component)`. For all `RenderItem(...)` calls that directly render one block, replace them with `render_item_with_component(..., component=component)`. For split items created by helper functions that cannot yet accept component metadata, immediately after extending `plan.items`, set:

```python
                    for item in plan.items[-len(paragraph_items):]:
                        item.component_id = "" if component is None else component.component_id
                        item.component_kind = "" if component is None else component.component_kind
```

Apply the same post-extend assignment for `title_metadata_items`, `embedded_heading_items`, and `paired_heading` split items.

- [ ] **Step 7: Append render-layer validation before returning the plan**

At the end of `build_page_render_plan()`, before `return plan`, add:

```python
    layer_validation = ownership.validate_render_layer_exclusivity(plan, plan.components)
    if layer_validation.issues:
        plan.ownership_validation = ownership.OwnershipValidationResult(
            issues=list(plan.ownership_validation.issues) + list(layer_validation.issues)
        )
```

- [ ] **Step 8: Run render-planning tests**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest \
  tests.test_render_plan.OwnershipDrivenRenderPlanTests \
  tests.test_render_plan.RenderPlanOwnershipSerializationTests -v
```

Expected: tests pass.

- [ ] **Step 9: Commit Task 6**

Run:

```bash
git add translate_pdf_via_codex.py render_plan.py tests/test_render_plan.py
git commit -m "feat: drive render plans from component ownership"
```

## Task 7: Ownership Visual QA Categories

**Files:**
- Modify: `qa_visual.py`
- Modify: `tests/test_qa_visual.py`
- Modify: `translate_pdf_parallel.py`

- [ ] **Step 1: Write failing QA tests**

Append to `tests/test_qa_visual.py`:

```python
    def test_ownership_qa_reports_duplicate_ownership_from_render_plan(self):
        plan = {
            "page_num": 9,
            "ownership_components": [
                {"component_id": "p009c0001", "component_kind": "visual", "source_ids": ["p009b0005"], "source_bbox": [80, 600, 306, 656], "clip_bbox": [80, 600, 306, 656]},
                {"component_id": "p009c0002", "component_kind": "translated_text", "source_ids": ["p009b0005"], "source_bbox": [85, 610, 120, 630], "clip_bbox": None},
            ],
            "ownership_validation": {
                "ok": False,
                "issues": [
                    {
                        "issue_code": "duplicate_owner",
                        "severity": "error",
                        "page_num": 9,
                        "message": "source block p009b0005 has multiple non-duplicate owners",
                        "source_ids": ["p009b0005"],
                        "component_ids": ["p009c0001", "p009c0002"],
                        "bboxes": [[80, 600, 306, 656]],
                    }
                ],
            },
            "render_items": [],
        }

        issues = qa_visual.detect_ownership_issues(plan)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].category, "duplicate_ownership")
        self.assertEqual(issues[0].severity, "error")
        self.assertEqual(issues[0].source_ids, ["p009b0005"])

    def test_ownership_qa_reports_text_over_visual_category(self):
        plan = {
            "page_num": 15,
            "ownership_validation": {
                "ok": False,
                "issues": [
                    {
                        "issue_code": "text_over_visual_component",
                        "severity": "error",
                        "page_num": 15,
                        "message": "text item overlaps unrelated visual component p015c0001",
                        "source_ids": ["p015b0097"],
                        "component_ids": ["p015c0001", "p015c0002"],
                        "bboxes": [[120, 100, 250, 140], [111, 65, 484, 402]],
                    }
                ],
            },
            "render_items": [],
        }

        issues = qa_visual.detect_ownership_issues(plan)

        self.assertEqual(issues[0].category, "text_over_visual")
        self.assertEqual(issues[0].artifact_paths["component_ids"], "p015c0001,p015c0002")
```

- [ ] **Step 2: Run QA tests and confirm failure**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest \
  tests.test_qa_visual.VisualQaImageTests.test_ownership_qa_reports_duplicate_ownership_from_render_plan \
  tests.test_qa_visual.VisualQaImageTests.test_ownership_qa_reports_text_over_visual_category -v
```

Expected: `ERROR` for missing `detect_ownership_issues`.

- [ ] **Step 3: Add ownership issue mapping to `qa_visual.py`**

Add this map near the geometry checks:

```python
OWNERSHIP_CATEGORY_BY_CODE = {
    "duplicate_owner": "duplicate_ownership",
    "source_rendered_as_image_and_text": "duplicate_ownership",
    "text_over_visual_component": "text_over_visual",
    "visual_clip_overcaptures_translated_component": "visual_overcapture",
    "missing_owner": "missing_ownership",
    "invalid_component_kind": "invalid_ownership",
    "invalid_confidence": "invalid_ownership",
}
```

Add:

```python
def detect_ownership_issues(plan) -> list[VisualQaIssue]:
    page_num = _plan_page_num(plan)
    validation = _item_value(plan, "ownership_validation", {}) or {}
    raw_issues = validation.get("issues", []) if isinstance(validation, dict) else []
    issues = []
    for raw_issue in raw_issues:
        issue_code = str(raw_issue.get("issue_code", "ownership_violation"))
        category = OWNERSHIP_CATEGORY_BY_CODE.get(issue_code, "ownership_violation")
        bboxes = raw_issue.get("bboxes") or []
        bbox = _bbox_tuple(bboxes[0]) if bboxes else None
        component_ids = [str(component_id) for component_id in raw_issue.get("component_ids", [])]
        issues.append(
            VisualQaIssue(
                category=category,
                severity=str(raw_issue.get("severity", "error")),
                page_num=page_num,
                message=str(raw_issue.get("message", issue_code)),
                source_ids=[str(source_id) for source_id in raw_issue.get("source_ids", [])],
                bbox=bbox,
                render_kind="ownership",
                artifact_paths={"component_ids": ",".join(sorted(component_ids))} if component_ids else {},
            )
        )
    return issues
```

In `generate_visual_qa_report()`, inside the `if page_size is not None:` plan loop, add before geometry checks:

```python
            report_issues.extend(detect_ownership_issues(plan))
```

- [ ] **Step 4: Include ownership issue counts in parallel summaries**

In `translate_pdf_parallel.py`, where visual QA report data is summarized, add these fields to each document summary payload:

```python
        "ownership_issue_count": sum(1 for issue in getattr(report, "issues", []) if getattr(issue, "render_kind", "") == "ownership"),
        "ownership_error_count": sum(1 for issue in getattr(report, "issues", []) if getattr(issue, "render_kind", "") == "ownership" and getattr(issue, "severity", "") == "error"),
```

If the summary code stores only `report.issue_count`, load `report.json_path` and compute counts from `issues` entries whose `render_kind` is `ownership`.

- [ ] **Step 5: Run QA tests**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest tests.test_qa_visual -v
```

Expected: all QA tests pass.

- [ ] **Step 6: Commit Task 7**

Run:

```bash
git add qa_visual.py translate_pdf_parallel.py tests/test_qa_visual.py
git commit -m "feat: report ownership violations in visual QA"
```

## Task 8: Ownership Fixture Runner And BERT Fixtures

**Files:**
- Modify: `tests/pdf_render_fixture_runner.py`
- Modify: `tests/test_pdf_render_fixtures.py`
- Modify: `tests/fixtures/pdf_render/README.md`
- Create: BERT fixture files listed in File Structure

- [ ] **Step 1: Extend the fixture runner with ownership assertions**

In `tests/pdf_render_fixture_runner.py`, add these branches in `assert_render_plan_fixture()`:

```python
        elif expect == "ownership_validation_ok":
            _assert_ownership_validation_ok(fixture, plan_json, assertion)
        elif expect == "component_contains":
            _assert_component_contains(fixture, plan_json, assertion)
        elif expect == "component_kind_for_source":
            _assert_component_kind_for_source(fixture, plan_json, assertion)
        elif expect == "source_not_rendered_as":
            _assert_source_not_rendered_as(fixture, plan_json, assertion)
        elif expect == "no_text_over_component":
            _assert_no_text_over_component(fixture, plan_json, assertion)
```

Append these helpers:

```python
def _ownership_components(plan_json: dict) -> list[dict]:
    return list(plan_json.get("ownership_components") or [])


def _assert_ownership_validation_ok(fixture: RenderPlanFixture, plan_json: dict, assertion: dict) -> None:
    validation = plan_json.get("ownership_validation") or {}
    if validation.get("ok") is not True:
        _fail(fixture, assertion, f"ownership validation is not ok: {validation.get('issues')}")


def _assert_component_contains(fixture: RenderPlanFixture, plan_json: dict, assertion: dict) -> None:
    source_ids = set(_source_ids(assertion))
    component_kind = assertion.get("component_kind")
    matches = [
        component
        for component in _ownership_components(plan_json)
        if source_ids <= set(component.get("source_ids", []))
        and (component_kind is None or component.get("component_kind") == component_kind)
    ]
    if not matches:
        _fail(fixture, assertion, f"no {component_kind!r} component contains {sorted(source_ids)}")


def _assert_component_kind_for_source(fixture: RenderPlanFixture, plan_json: dict, assertion: dict) -> None:
    source_id = assertion["source_id"]
    expected_kind = assertion["component_kind"]
    actual = [
        component.get("component_kind")
        for component in _ownership_components(plan_json)
        if source_id in component.get("source_ids", [])
    ]
    if actual != [expected_kind]:
        _fail(fixture, assertion, f"{source_id} component kinds are {actual}, expected exactly {[expected_kind]}")


def _assert_source_not_rendered_as(fixture: RenderPlanFixture, plan_json: dict, assertion: dict) -> None:
    source_id = assertion["source_id"]
    forbidden_kind = assertion["render_kind"]
    matching = _render_items_for_source(plan_json, source_id, kind=forbidden_kind)
    if matching:
        _fail(fixture, assertion, f"{source_id} is unexpectedly rendered as {forbidden_kind}")


def _assert_no_text_over_component(fixture: RenderPlanFixture, plan_json: dict, assertion: dict) -> None:
    source_ids = set(_source_ids(assertion))
    components = [
        component
        for component in _ownership_components(plan_json)
        if source_ids <= set(component.get("source_ids", []))
    ]
    if not components:
        _fail(fixture, assertion, f"no component contains {sorted(source_ids)}")
    component_bbox = components[0].get("clip_bbox") or components[0].get("source_bbox")
    text_items = [item for item in plan_json["render_items"] if item["kind"] in {"translated_text", "original_selectable_text"}]
    offenders = []
    for item in text_items:
        if set(item.get("source_ids", [])) & source_ids:
            continue
        if pdf.ownership.bbox_overlap_area(item["bbox"], component_bbox) > min(
            pdf.ownership.bbox_area(item["bbox"]),
            pdf.ownership.bbox_area(component_bbox),
        ) * 0.05:
            offenders.append(item.get("source_ids", []))
    if offenders:
        _fail(fixture, assertion, f"text items overlap component {sorted(source_ids)}: {offenders}")
```

- [ ] **Step 2: Generate BERT source and translation fixtures from existing deterministic work artifacts**

Run this command from repo root:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 - <<'PY'
import json
from pathlib import Path

job = Path("work/llm_top_cited_papers/jobs/02_bert_pretraining_bidirectional_transformers")
fixture_root = Path("tests/fixtures/pdf_render")
slug = "bert"
pages = {9, 12, 15}

source_pages = json.loads((job / "source_pages.json").read_text(encoding="utf-8"))
translations = json.loads((job / "translations.json").read_text(encoding="utf-8"))
page_blocks = {int(page[0]["page"]): page for page in source_pages if page}

expected_assertions = {
    9: [
        {"expect": "coverage_ledger_complete", "summary": "every source block has coverage"},
        {"expect": "ownership_validation_ok", "summary": "page 9 ownership is exclusive"},
        {"expect": "component_contains", "component_kind": "visual", "source_ids": ["p009b0006", "p009b0007", "p009b0008", "p009b0009", "p009b0010", "p009b0011", "p009b0012", "p009b0013", "p009b0014", "p009b0022", "p009b0023", "p009b0026"], "summary": "top table owns all columns"},
        {"expect": "component_contains", "component_kind": "visual", "source_ids": ["p009b0005", "p009b0019", "p009b0020", "p009b0021", "p009b0025", "p009b0027", "p009b0028", "p009b0029", "p009b0030", "p009b0031"], "summary": "bottom table owns all columns"},
        {"expect": "source_not_rendered_as", "source_id": "p009b0005", "render_kind": "translated_text", "summary": "table label is not translated"},
        {"expect": "source_not_rendered_as", "source_id": "p009b0027", "render_kind": "translated_text", "summary": "numeric table column is not translated"},
        {"expect": "component_kind_for_source", "source_id": "p009b0032", "component_kind": "translated_text", "summary": "body after table remains translated"},
    ],
    12: [
        {"expect": "coverage_ledger_complete", "summary": "every source block has coverage"},
        {"expect": "ownership_validation_ok", "summary": "page 12 ownership is exclusive"},
        {"expect": "component_kind_for_source", "source_id": "p012b0002", "component_kind": "reference", "summary": "reference continuation stays reference"},
        {"expect": "component_kind_for_source", "source_id": "p012b0001", "component_kind": "translated_text", "summary": "right-column appendix bullet is translated"},
        {"expect": "component_kind_for_source", "source_id": "p012b0012", "component_kind": "translated_text", "summary": "appendix heading is translated"},
        {"expect": "reference_original_text", "text_contains": "for natural language understanding", "summary": "reference text preserved"},
        {"expect": "translated_text", "source_id": "p012b0012", "text_contains": "BERT", "summary": "appendix title remains in translated text layer"},
    ],
    15: [
        {"expect": "coverage_ledger_complete", "summary": "every source block has coverage"},
        {"expect": "ownership_validation_ok", "summary": "page 15 ownership is exclusive"},
        {"expect": "component_contains", "component_kind": "visual", "source_ids": ["p015b0009", "p015b0010", "p015b0023", "p015b0025"], "summary": "figure-internal prime labels are visual-owned"},
        {"expect": "component_contains", "component_kind": "visual", "source_ids": ["p015b0093", "p015b0094", "p015b0095", "p015b0096"], "summary": "figure caption band is visual-owned"},
        {"expect": "source_not_rendered_as", "source_id": "p015b0009", "render_kind": "translated_text", "summary": "diagram label T 1 prime is not translated"},
        {"expect": "source_not_rendered_as", "source_id": "p015b0023", "render_kind": "translated_text", "summary": "diagram label E 1 prime is not translated"},
        {"expect": "no_text_over_component", "source_ids": ["p015b0009", "p015b0010", "p015b0023", "p015b0025"], "summary": "no translated text overlaps main figure"},
        {"expect": "component_kind_for_source", "source_id": "p015b0097", "component_kind": "translated_text", "summary": "body after figure remains translated"},
    ],
}

for page_num in pages:
    blocks = page_blocks[page_num]
    block_ids = {block["id"] for block in blocks}
    page_translations = {block_id: translations[block_id] for block_id in sorted(block_ids & set(translations))}
    for fixture_type in ("source_pages", "translations", "expected_plans", "expected_qa"):
        (fixture_root / fixture_type / slug).mkdir(parents=True, exist_ok=True)
    (fixture_root / "source_pages" / slug / f"page-{page_num:03d}.json").write_text(
        json.dumps({"document": slug, "page": page_num, "blocks": blocks}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (fixture_root / "translations" / slug / f"page-{page_num:03d}.json").write_text(
        json.dumps({"document": slug, "page": page_num, "translations": page_translations}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (fixture_root / "expected_plans" / slug / f"page-{page_num:03d}.json").write_text(
        json.dumps({"document": slug, "page": page_num, "assertions": expected_assertions[page_num]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (fixture_root / "expected_qa" / slug / f"page-{page_num:03d}.json").write_text(
        json.dumps({"document": slug, "page": page_num, "assertions": [{"expect": "no_errors"}]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
PY
```

Expected: the twelve BERT fixture JSON files are created under `tests/fixtures/pdf_render`.

- [ ] **Step 3: Add BERT fixtures to fixture tests**

In `tests/test_pdf_render_fixtures.py`, add:

```python
    def test_bert_ownership_fixtures(self):
        fixtures = list(
            runner.iter_render_plan_fixtures(
                self.fixture_root,
                "bert",
                page_size=(623, 801),
            )
        )
        self.assertEqual([fixture.page_num for fixture in fixtures], [9, 12, 15])
        for fixture in fixtures:
            with self.subTest(page=fixture.page_num):
                runner.assert_render_plan_fixture(fixture)
```

- [ ] **Step 4: Document the ownership fixture workflow**

Append to `tests/fixtures/pdf_render/README.md`:

```markdown
## Ownership Assertions

Ownership fixtures should assert the component contract, not only the visible
render item. Use `ownership_validation_ok` on every page fixture. Use
`component_contains` when a figure, table, formula, or code component must own
internal labels or columns. Use `source_not_rendered_as` to prove visual-owned
source IDs are not drawn as translated text. Use `no_text_over_component` for
ghosting reports where a text layer previously appeared over a preserved visual
clip.

For reference continuation defects, assert both sides: reference blocks should
be `reference` components and adjacent body or appendix blocks in another column
should be `translated_text` components.
```

- [ ] **Step 5: Run fixture tests and confirm new fixtures pass**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest \
  tests.test_pdf_render_fixtures tests.test_pdf_render_fixture_layout -v
```

Expected: fixture tests pass. If a BERT fixture fails, fix the ownership builder or render-plan integration; do not weaken fixture assertions unless the assertion contradicts the OpenSpec requirement.

- [ ] **Step 6: Commit Task 8**

Run:

```bash
git add tests/pdf_render_fixture_runner.py tests/test_pdf_render_fixtures.py tests/fixtures/pdf_render/README.md tests/fixtures/pdf_render/source_pages/bert tests/fixtures/pdf_render/translations/bert tests/fixtures/pdf_render/expected_plans/bert tests/fixtures/pdf_render/expected_qa/bert
git commit -m "test: add ownership fixtures for BERT visual defects"
```

## Task 9: Strict QA Gate And PDF-Writing Integration

**Files:**
- Modify: `translate_pdf_via_codex.py`
- Modify: `translate_pdf_parallel.py`
- Modify: `tests/test_render_plan.py`
- Modify: `tests/test_translate_pdf_parallel.py`

- [ ] **Step 1: Write failing strict-gate tests**

Add to `tests/test_render_plan.py`:

```python
class OwnershipStrictGateTests(unittest.TestCase):
    def test_validate_plan_quality_reports_ownership_violations(self):
        plan = pdf.PageRenderPlan(page_num=9)
        plan.ownership_validation = pdf.ownership.OwnershipValidationResult(
            [
                pdf.ownership.OwnershipIssue(
                    issue_code="source_rendered_as_image_and_text",
                    severity="error",
                    page_num=9,
                    message="source block p009b0005 is rendered by both image and text layers",
                    source_ids=["p009b0005"],
                )
            ]
        )

        errors = pdf.validate_plan_quality(9, [], {}, plan)

        self.assertTrue(any("p009b0005" in error for error in errors))
```

- [ ] **Step 2: Run strict-gate test and confirm failure**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest \
  tests.test_render_plan.OwnershipStrictGateTests -v
```

Expected: failure because `validate_plan_quality()` does not include ownership validation errors.

- [ ] **Step 3: Include ownership issues in `validate_plan_quality()`**

In `translate_pdf_via_codex.py`, inside `validate_plan_quality()`, append:

```python
    errors.extend(issue.message for issue in plan.ownership_validation.issues if issue.severity == "error")
```

Place it near the existing coverage/layout/text-overlap checks so strict validation sees ownership errors before PDF writing is accepted.

- [ ] **Step 4: Make strict mode fail on ownership errors and non-strict mode report them**

Find the code path that writes render-plan artifacts and aggregates `validation_results`. Ensure it includes:

```python
        validation_results["ownership"] = ownership.ownership_validation_to_json(plan.ownership_validation)
```

Find the strict-QA decision in `translate_pdf_parallel.py`. Add ownership errors to the same blocking condition used for visual QA errors:

```python
ownership_error_count = int(document_summary.get("ownership_error_count", 0))
if strict_qa and ownership_error_count:
    document_summary["status"] = "failed"
    document_summary["error"] = f"strict QA failed with {ownership_error_count} ownership errors"
```

- [ ] **Step 5: Run focused strict-gate and parallel tests**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest \
  tests.test_render_plan.OwnershipStrictGateTests tests.test_translate_pdf_parallel -v
```

Expected: tests pass.

- [ ] **Step 6: Commit Task 9**

Run:

```bash
git add translate_pdf_via_codex.py translate_pdf_parallel.py tests/test_render_plan.py tests/test_translate_pdf_parallel.py
git commit -m "feat: gate strict QA on ownership violations"
```

## Task 10: Deterministic Verification And Regression Run

**Files:**
- No source files should be modified in this task except regenerated fixture artifacts if Task 8 assertions required deterministic fixture correction.

- [ ] **Step 1: Run focused ownership/render/QA tests**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest \
  tests.test_ownership tests.test_render_plan tests.test_translate_pdf_parallel \
  tests.test_qa_visual tests.test_pdf_render_fixtures tests.test_pdf_render_fixture_layout -v
```

Expected: all listed tests pass.

- [ ] **Step 2: Run full regression suite from `AGENTS.md`**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest \
  tests.test_qa_semantic tests.test_render_pdf tests.test_layout \
  tests.test_regions tests.test_render_plan \
  tests.test_waitfree_regression tests.test_pdf_render_fixtures \
  tests.test_pdf_render_fixture_layout tests.test_translate_pdf_parallel \
  tests.test_qa_visual -v
```

Expected: full regression passes.

- [ ] **Step 3: Run Python compilation checks from `AGENTS.md` plus ownership tests**

Run:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m py_compile \
  ownership.py qa_semantic.py backtranslate_check.py render_pdf.py layout.py regions.py \
  classify.py render_plan.py qa_visual.py translate_pdf_via_codex.py \
  translate_pdf_parallel.py tests/test_ownership.py tests/test_qa_semantic.py \
  tests/test_render_pdf.py tests/test_layout.py tests/test_regions.py \
  tests/test_render_plan.py tests/test_waitfree_regression.py \
  tests/test_pdf_render_fixtures.py tests/test_pdf_render_fixture_layout.py \
  tests/test_translate_pdf_parallel.py tests/test_qa_visual.py tests/pdf_render_fixture_runner.py
```

Expected: command exits successfully with no output.

- [ ] **Step 4: Run deterministic BERT sample pages and inspect artifacts**

Run a deterministic translation/render pass for BERT using cached translations and no live model calls if the CLI supports cache-only operation. If cache-only is unavailable, run the existing command with `--continue-on-error --no-qa` only after confirming it will reuse `work/llm_top_cited_papers/jobs/02_bert_pretraining_bidirectional_transformers/translations.json`.

Then inspect:

```bash
PYTHONPYCACHEPREFIX=/tmp/translatePaper_pycache python3 -m unittest tests.test_pdf_render_fixtures.PdfRenderFixtureTests.test_bert_ownership_fixtures -v
```

Expected: BERT fixtures pass and `page-009.render-plan.json`, `page-012.render-plan.json`, and `page-015.render-plan.json` contain `ownership_components` and `"ownership_validation": {"ok": true, "issues": []}`.

- [ ] **Step 5: Review git status**

Run:

```bash
git status --short
```

Expected: only intended source, test, fixture, OpenSpec, and plan files are modified or untracked. Generated directories such as `__pycache__/`, `tmp/`, `work/`, and `output/` are not staged.

- [ ] **Step 6: Commit final verification notes if needed**

If Task 10 required only verification and no file changes, skip this commit. If deterministic fixture assertions or docs were corrected, run:

```bash
git add tests/fixtures/pdf_render tests/fixtures/pdf_render/README.md
git commit -m "test: finalize ownership rendering fixtures"
```

## Self-Review

Spec coverage:

- Page component ownership model: Tasks 1, 2, 4, and 6.
- Unique source block owner: Tasks 1, 2, and 5.
- Ownership-driven translation batches: Task 3.
- Ownership-driven render plan: Tasks 4 and 6.
- Render layer exclusivity: Task 5 and Task 9.
- Conservative ambiguous visual ownership: Task 2 and BERT page 15 fixture in Task 8.
- Ownership visual QA: Task 7.
- Ownership regression fixtures: Task 8.
- Verification and commit discipline: Task 10.

Placeholder scan:

- The plan contains concrete file paths, function names, code snippets, commands, and expected outcomes.
- The plan does not rely on one-off paper-title/page-number runtime exceptions; BERT page numbers appear only in regression fixture data.

Type consistency:

- `PageComponent` fields used by tests, render-plan serialization, QA, and fixture assertions are consistent: `component_id`, `component_kind`, `source_ids`, `source_bbox`, `clip_bbox`, `confidence`, `reason_codes`, `render_strategy`, `parent_component_id`.
- `OwnershipValidationResult` consistently exposes `ok` and `issues`.
- Render items and coverage ledger entries consistently use `component_id` and `component_kind`.
