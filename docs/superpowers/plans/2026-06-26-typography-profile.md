# Typography Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a full-document typography profile stage that locks font sizes, line heights, paragraph spacing, and fit exceptions across a translated PDF.

**Architecture:** Add a focused `typography_profile.py` module for profile inference, override merging, serialization, and exception reporting. Thread the effective profile through `translate_pdf_parallel.py`, `translate_pdf_via_codex.py`, `layout.py`, `render_plan.py`, and `qa_visual.py` so text rendering uses document-level role styles rather than page-local font adaptation. Keep compatibility by deriving a default profile from existing `DOCUMENT_STYLES` when no profile is supplied.

**Tech Stack:** Python 3.12, unittest, existing render-plan dataclasses, JSON artifacts, existing visual QA/reporting pipeline.

---

## File Structure

- Create `typography_profile.py`
  - Owns profile dataclasses, role statistics, source inference, Chinese mapping, override merging, JSON/Markdown serialization, and exception report helpers.
- Create `tests/test_typography_profile.py`
  - Unit tests for deterministic inference, serialization, overrides, and exception reports.
- Modify `render_plan.py`
  - Extend `RenderItem` and serialized render items with typography role/target/actual/exception metadata.
- Modify `layout.py`
  - Add profile-aware style lookup and profile-aware text fit metrics while preserving existing `DOCUMENT_STYLES` fallback.
- Modify `translate_pdf_via_codex.py`
  - Accept an effective profile in render-plan construction and vector rendering.
  - Record typography exceptions during normalization and fit repairs.
- Modify `translate_pdf_parallel.py`
  - Add CLI flags and preflight profile generation/loading.
  - Include profile and exception artifacts in run summaries.
- Modify `qa_visual.py`
  - Validate typography against the document profile and surface unreported deviations.
- Modify `tests/test_render_plan.py`
  - Add integration-style render-plan tests for locked role styles and exception ladder behavior.
- Modify `tests/test_translate_pdf_parallel.py`
  - Add CLI/report tests for profile artifacts and non-strict generation with exceptions.
- Modify `tests/test_qa_visual.py`
  - Add QA report tests for typography deviations and reduced-font exceptions.

## Task 1: Profile Data Model And Serialization

**Files:**
- Create: `typography_profile.py`
- Create: `tests/test_typography_profile.py`

- [ ] **Step 1: Write failing serialization test**

Add this test module skeleton and first test:

```python
import json
import unittest

import typography_profile as typo


class TypographyProfileModuleTests(unittest.TestCase):
    def test_profile_round_trips_with_required_roles(self):
        profile = typo.TypographyProfile.default(
            source_path="/tmp/book.pdf",
            page_count=2,
            page_size=(612.0, 792.0),
        )

        payload = profile.to_json_dict()
        restored = typo.TypographyProfile.from_json_dict(json.loads(json.dumps(payload)))

        self.assertEqual(restored.schema_version, 1)
        self.assertEqual(restored.document.source_path, "/tmp/book.pdf")
        self.assertIn("body", restored.styles)
        self.assertIn("section_heading", restored.styles)
        self.assertGreater(restored.styles["section_heading"].font_size, restored.styles["body"].font_size)
        self.assertLessEqual(restored.styles["body"].min_font_size, restored.styles["body"].font_size)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=vendor PYTHONPYCACHEPREFIX=/private/tmp/translatePaper_pycache /opt/homebrew/bin/python3.12 -m unittest tests.test_typography_profile.TypographyProfileModuleTests.test_profile_round_trips_with_required_roles -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'typography_profile'`.

- [ ] **Step 3: Implement minimal profile dataclasses**

Create `typography_profile.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from layout import DOCUMENT_STYLES


SCHEMA_VERSION = 1
REQUIRED_STYLE_ROLES = (
    "body",
    "chapter_label",
    "chapter_title",
    "section_heading",
    "subsection_heading",
    "callout_heading",
    "callout_body",
    "reference",
    "footer",
)


@dataclass(frozen=True)
class TypographyStyle:
    font_size: float
    line_height_factor: float
    paragraph_spacing: float
    min_line_height_factor: float
    min_paragraph_spacing: float
    min_font_size: float

    def to_json_dict(self) -> dict[str, float]:
        return {
            "font_size": self.font_size,
            "line_height_factor": self.line_height_factor,
            "paragraph_spacing": self.paragraph_spacing,
            "min_line_height_factor": self.min_line_height_factor,
            "min_paragraph_spacing": self.min_paragraph_spacing,
            "min_font_size": self.min_font_size,
        }

    @classmethod
    def from_json_dict(cls, payload: dict[str, Any]) -> "TypographyStyle":
        return cls(
            font_size=float(payload["font_size"]),
            line_height_factor=float(payload["line_height_factor"]),
            paragraph_spacing=float(payload.get("paragraph_spacing", 0.0)),
            min_line_height_factor=float(payload["min_line_height_factor"]),
            min_paragraph_spacing=float(payload.get("min_paragraph_spacing", 0.0)),
            min_font_size=float(payload["min_font_size"]),
        )


@dataclass(frozen=True)
class TypographyDocument:
    source_path: str
    page_count: int
    page_size: tuple[float, float]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "page_count": self.page_count,
            "page_size": [self.page_size[0], self.page_size[1]],
        }

    @classmethod
    def from_json_dict(cls, payload: dict[str, Any]) -> "TypographyDocument":
        page_size = payload["page_size"]
        return cls(
            source_path=str(payload["source_path"]),
            page_count=int(payload["page_count"]),
            page_size=(float(page_size[0]), float(page_size[1])),
        )


@dataclass(frozen=True)
class TypographyProfile:
    schema_version: int
    document: TypographyDocument
    styles: dict[str, TypographyStyle]
    layout: dict[str, float] = field(default_factory=dict)
    source_analysis: dict[str, Any] = field(default_factory=dict)
    override: dict[str, Any] = field(default_factory=lambda: {"path": None, "applied": []})

    @classmethod
    def default(cls, source_path: str, page_count: int, page_size: tuple[float, float]) -> "TypographyProfile":
        body = DOCUMENT_STYLES["body"]
        heading = DOCUMENT_STYLES["heading"]
        subheading = DOCUMENT_STYLES["subheading"]
        title = DOCUMENT_STYLES["title"]
        reference = DOCUMENT_STYLES["reference"]
        footer = DOCUMENT_STYLES["footer"]
        styles = {
            "body": _style_from_document_style(body),
            "chapter_label": _style_from_document_style(heading),
            "chapter_title": _style_from_document_style(title),
            "section_heading": _style_from_document_style(heading),
            "subsection_heading": _style_from_document_style(subheading),
            "callout_heading": _style_from_document_style(heading),
            "callout_body": _style_from_document_style(body),
            "reference": _style_from_document_style(reference),
            "footer": _style_from_document_style(footer),
        }
        return cls(
            schema_version=SCHEMA_VERSION,
            document=TypographyDocument(source_path, page_count, page_size),
            styles=styles,
            layout={
                "text_column_width": max(1.0, page_size[0] - 144.0),
                "body_flow_min_gap": 3.0,
                "body_flow_target_gap": 8.0,
                "body_flow_max_gap": 12.0,
            },
        )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "document": self.document.to_json_dict(),
            "source_analysis": self.source_analysis,
            "styles": {role: style.to_json_dict() for role, style in sorted(self.styles.items())},
            "layout": dict(sorted(self.layout.items())),
            "override": self.override,
        }

    @classmethod
    def from_json_dict(cls, payload: dict[str, Any]) -> "TypographyProfile":
        if int(payload["schema_version"]) != SCHEMA_VERSION:
            raise ValueError(f"unsupported typography profile schema_version {payload['schema_version']}")
        styles = {
            role: TypographyStyle.from_json_dict(style_payload)
            for role, style_payload in payload["styles"].items()
        }
        missing = sorted(set(REQUIRED_STYLE_ROLES) - set(styles))
        if missing:
            raise ValueError(f"typography profile missing required styles: {missing}")
        return cls(
            schema_version=SCHEMA_VERSION,
            document=TypographyDocument.from_json_dict(payload["document"]),
            styles=styles,
            layout={key: float(value) for key, value in payload.get("layout", {}).items()},
            source_analysis=payload.get("source_analysis", {}),
            override=payload.get("override", {"path": None, "applied": []}),
        )


def _style_from_document_style(style) -> TypographyStyle:
    return TypographyStyle(
        font_size=float(style.font_size),
        line_height_factor=float(style.line_height_factor),
        paragraph_spacing=float(style.paragraph_spacing),
        min_line_height_factor=float(style.min_line_height_factor or style.line_height_factor),
        min_paragraph_spacing=float(style.min_paragraph_spacing if style.min_paragraph_spacing is not None else style.paragraph_spacing),
        min_font_size=max(5.0, round(float(style.font_size) * 0.92, 2)),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run the same unittest command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add typography_profile.py tests/test_typography_profile.py
git commit -m "Add typography profile data model"
```

## Task 2: Profile Inference From Source Blocks

**Files:**
- Modify: `typography_profile.py`
- Modify: `tests/test_typography_profile.py`

- [ ] **Step 1: Write failing role inference test**

Append to `TypographyProfileModuleTests`:

```python
    def test_infer_profile_uses_document_level_role_statistics(self):
        pages = [
            [
                {"id": "p001b0001", "page": 1, "text": "Chapter 10", "xMin": 72.0, "yMin": 100.0, "xMax": 220.0, "yMax": 124.0},
                {"id": "p001b0002", "page": 1, "text": "Defining Errors Away", "xMin": 72.0, "yMin": 180.0, "xMax": 430.0, "yMax": 214.0},
                {"id": "p001b0003", "page": 1, "text": "Exceptions are one of the worst sources of complexity. They make code harder to read.", "xMin": 72.0, "yMin": 260.0, "xMax": 540.0, "yMax": 308.0},
            ],
            [
                {"id": "p002b0001", "page": 2, "text": "10.1 Why exceptions add complexity", "xMin": 72.0, "yMin": 96.0, "xMax": 430.0, "yMax": 119.0},
                {"id": "p002b0002", "page": 2, "text": "A particular piece of code may encounter exceptions in several different ways.", "xMin": 72.0, "yMin": 150.0, "xMax": 540.0, "yMax": 198.0},
                {"id": "p002b0003", "page": 2, "text": "Red Flag: Implementation Documentation", "xMin": 120.0, "yMin": 260.0, "xMax": 500.0, "yMax": 284.0},
                {"id": "p002b0004", "page": 2, "text": "This red flag occurs when interface documentation describes implementation details.", "xMin": 90.0, "yMin": 300.0, "xMax": 520.0, "yMax": 348.0},
            ],
        ]

        profile = typo.infer_typography_profile(
            source_path="/tmp/book.pdf",
            pages=pages,
            page_size=(612.0, 792.0),
        )

        self.assertEqual(profile.document.page_count, 2)
        self.assertGreater(profile.source_analysis["role_stats"]["body"]["sample_count"], 0)
        self.assertGreater(profile.styles["chapter_title"].font_size, profile.styles["body"].font_size)
        self.assertGreater(profile.styles["section_heading"].font_size, profile.styles["body"].font_size)
        self.assertEqual(profile.styles["callout_body"].font_size, profile.styles["body"].font_size)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=vendor PYTHONPYCACHEPREFIX=/private/tmp/translatePaper_pycache /opt/homebrew/bin/python3.12 -m unittest tests.test_typography_profile.TypographyProfileModuleTests.test_infer_profile_uses_document_level_role_statistics -v
```

Expected: FAIL with `AttributeError: module 'typography_profile' has no attribute 'infer_typography_profile'`.

- [ ] **Step 3: Implement deterministic inference helpers**

Add to `typography_profile.py`:

```python
import re
from statistics import median

from classify import normalize_text


CHINESE_OPTICAL_SCALE = 1.04
MIN_HEADING_GAP_PT = 1.0


def infer_typography_profile(
    source_path: str,
    pages: list[list[dict]],
    page_size: tuple[float, float],
) -> TypographyProfile:
    samples: dict[str, list[dict[str, float]]] = {role: [] for role in REQUIRED_STYLE_ROLES}
    for page in pages:
        for block in page:
            role = infer_typography_role(block)
            if role not in samples:
                continue
            samples[role].append(
                {
                    "line_height": source_visual_line_height(block),
                    "width": float(block["xMax"] - block["xMin"]),
                    "gap": 0.0,
                }
            )
    default_profile = TypographyProfile.default(source_path, len(pages), page_size)
    styles = dict(default_profile.styles)
    role_stats = {}
    body_size = _mapped_font_size(samples["body"], default_profile.styles["body"].font_size)
    styles["body"] = _style_with_size(default_profile.styles["body"], body_size, 1.30, 4.0)
    for role in REQUIRED_STYLE_ROLES:
        role_samples = samples[role]
        if role == "body":
            style = styles["body"]
        elif role in {"chapter_title"}:
            style = _style_with_size(default_profile.styles[role], max(body_size + 4.0, _mapped_font_size(role_samples, default_profile.styles[role].font_size)), 1.16, 4.0)
            styles[role] = style
        elif role in {"chapter_label", "section_heading", "callout_heading"}:
            style = _style_with_size(default_profile.styles[role], max(body_size + MIN_HEADING_GAP_PT, _mapped_font_size(role_samples, default_profile.styles[role].font_size)), 1.18, 3.0)
            styles[role] = style
        elif role == "subsection_heading":
            style = _style_with_size(default_profile.styles[role], max(body_size + 0.6, _mapped_font_size(role_samples, default_profile.styles[role].font_size)), 1.18, 2.5)
            styles[role] = style
        elif role == "callout_body":
            style = styles["body"]
            styles[role] = style
        else:
            style = styles[role]
        role_stats[role] = {
            "sample_count": len(role_samples),
            "source_line_height_median": round(_median_or_default([sample["line_height"] for sample in role_samples], style.font_size), 3),
            "source_block_width_median": round(_median_or_default([sample["width"] for sample in role_samples], default_profile.layout["text_column_width"]), 3),
            "confidence": "high" if len(role_samples) >= 3 else ("medium" if role_samples else "fallback"),
        }
    body_widths = [sample["width"] for sample in samples["body"]]
    layout = dict(default_profile.layout)
    if body_widths:
        layout["text_column_width"] = round(float(median(body_widths)), 3)
    return TypographyProfile(
        schema_version=SCHEMA_VERSION,
        document=TypographyDocument(source_path, len(pages), page_size),
        styles=styles,
        layout=layout,
        source_analysis={
            "role_stats": role_stats,
            "excluded_sample_counts": {"visual": 0},
            "chinese_optical_scale": CHINESE_OPTICAL_SCALE,
        },
    )


def infer_typography_role(block: dict) -> str:
    text = normalize_text(block.get("text", ""))
    if re.match(r"(?i)^chapter\s+\d+\b", text):
        return "chapter_label"
    if re.match(r"^\d+\.\d+\.\d+\b", text):
        return "subsection_heading"
    if re.match(r"^\d+\.\d+\b", text):
        return "section_heading"
    if re.match(r"(?i)^(red flag|warning|danger|pitfall|caution)\s*:", text):
        return "callout_heading"
    if len(text) > 40 and block.get("xMin", 0.0) > 80.0 and block.get("xMax", 999.0) < 550.0:
        return "callout_body"
    if len(text) <= 80 and float(block.get("yMin", 0.0)) < 240.0 and not text.endswith("."):
        return "chapter_title"
    return "body"


def source_visual_line_height(block: dict) -> float:
    text = str(block.get("text", ""))
    line_count = max(1, len([line for line in text.splitlines() if line.strip()]))
    return float(block["yMax"] - block["yMin"]) / line_count


def _mapped_font_size(samples: list[dict[str, float]], fallback: float) -> float:
    if not samples:
        return fallback
    source_line_height = _median_or_default([sample["line_height"] for sample in samples], fallback * 1.25)
    return round(max(5.0, source_line_height / 1.25 * CHINESE_OPTICAL_SCALE), 1)


def _median_or_default(values: list[float], fallback: float) -> float:
    if not values:
        return float(fallback)
    return float(median(values))


def _style_with_size(base: TypographyStyle, font_size: float, line_height_factor: float, paragraph_spacing: float) -> TypographyStyle:
    return TypographyStyle(
        font_size=round(font_size, 1),
        line_height_factor=line_height_factor,
        paragraph_spacing=paragraph_spacing,
        min_line_height_factor=max(1.05, round(line_height_factor - 0.15, 2)),
        min_paragraph_spacing=max(0.0, round(paragraph_spacing * 0.4, 2)),
        min_font_size=max(5.0, round(font_size * 0.92, 2)),
    )
```

- [ ] **Step 4: Run tests**

Run:

```bash
PYTHONPATH=vendor PYTHONPYCACHEPREFIX=/private/tmp/translatePaper_pycache /opt/homebrew/bin/python3.12 -m unittest tests.test_typography_profile -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add typography_profile.py tests/test_typography_profile.py
git commit -m "Infer document typography profile"
```

## Task 3: Override Merge And Profile Artifact Writers

**Files:**
- Modify: `typography_profile.py`
- Modify: `tests/test_typography_profile.py`

- [ ] **Step 1: Write failing override/report test**

Append:

```python
    def test_merge_override_records_applied_values_and_writes_reports(self):
        profile = typo.TypographyProfile.default("/tmp/book.pdf", 1, (612.0, 792.0))
        override = {
            "styles": {
                "body": {
                    "font_size": 11.0,
                    "min_font_size": 10.2
                }
            },
            "layout": {
                "body_flow_target_gap": 9.0
            }
        }

        merged = typo.merge_typography_override(profile, override, path="/tmp/override.json")
        markdown = typo.typography_profile_markdown(merged)

        self.assertEqual(merged.styles["body"].font_size, 11.0)
        self.assertEqual(merged.styles["body"].min_font_size, 10.2)
        self.assertEqual(merged.layout["body_flow_target_gap"], 9.0)
        self.assertEqual(len(merged.override["applied"]), 3)
        self.assertIn("body", markdown)
        self.assertIn("11", markdown)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=vendor PYTHONPYCACHEPREFIX=/private/tmp/translatePaper_pycache /opt/homebrew/bin/python3.12 -m unittest tests.test_typography_profile.TypographyProfileModuleTests.test_merge_override_records_applied_values_and_writes_reports -v
```

Expected: FAIL with missing `merge_typography_override`.

- [ ] **Step 3: Implement override merge and markdown report**

Add to `typography_profile.py`:

```python
def merge_typography_override(profile: TypographyProfile, override: dict[str, Any], path: str | None = None) -> TypographyProfile:
    styles = dict(profile.styles)
    layout = dict(profile.layout)
    applied: list[dict[str, Any]] = []
    for role, role_override in override.get("styles", {}).items():
        if role not in styles:
            raise ValueError(f"unknown typography style role {role!r}")
        style_payload = styles[role].to_json_dict()
        for key, value in role_override.items():
            if key not in style_payload:
                raise ValueError(f"unknown typography style field {role}.{key}")
            old = style_payload[key]
            style_payload[key] = float(value)
            applied.append({"path": f"styles.{role}.{key}", "auto": old, "override": float(value)})
        styles[role] = TypographyStyle.from_json_dict(style_payload)
    for key, value in override.get("layout", {}).items():
        if key not in layout:
            raise ValueError(f"unknown typography layout field {key}")
        old = layout[key]
        layout[key] = float(value)
        applied.append({"path": f"layout.{key}", "auto": old, "override": float(value)})
    merged = TypographyProfile(
        schema_version=profile.schema_version,
        document=profile.document,
        styles=styles,
        layout=layout,
        source_analysis=profile.source_analysis,
        override={"path": path, "applied": applied},
    )
    validate_typography_profile(merged)
    return merged


def validate_typography_profile(profile: TypographyProfile) -> None:
    missing = sorted(set(REQUIRED_STYLE_ROLES) - set(profile.styles))
    if missing:
        raise ValueError(f"typography profile missing required styles: {missing}")
    for role, style in profile.styles.items():
        if style.font_size <= 0.0:
            raise ValueError(f"{role}.font_size must be positive")
        if style.min_font_size <= 0.0 or style.min_font_size > style.font_size:
            raise ValueError(f"{role}.min_font_size must be positive and <= font_size")
        if style.min_line_height_factor > style.line_height_factor:
            raise ValueError(f"{role}.min_line_height_factor must be <= line_height_factor")
        if style.min_paragraph_spacing > style.paragraph_spacing:
            raise ValueError(f"{role}.min_paragraph_spacing must be <= paragraph_spacing")


def typography_profile_markdown(profile: TypographyProfile) -> str:
    lines = [
        "# Typography Profile",
        "",
        f"- Source: `{profile.document.source_path}`",
        f"- Pages: {profile.document.page_count}",
        f"- Page size: {profile.document.page_size[0]:g} x {profile.document.page_size[1]:g}",
        "",
        "## Styles",
        "",
        "| Role | Font | Line Height | Paragraph Spacing | Min Font |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for role in REQUIRED_STYLE_ROLES:
        style = profile.styles[role]
        lines.append(
            f"| `{role}` | {style.font_size:g} | {style.line_height_factor:g} | "
            f"{style.paragraph_spacing:g} | {style.min_font_size:g} |"
        )
    lines.extend(["", "## Overrides", ""])
    applied = profile.override.get("applied", [])
    if not applied:
        lines.append("No overrides applied.")
    else:
        for item in applied:
            lines.append(f"- `{item['path']}`: {item['auto']} -> {item['override']}")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run tests**

Run `python3 -m unittest tests.test_typography_profile -v` with the repository Python command above. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add typography_profile.py tests/test_typography_profile.py
git commit -m "Support typography profile overrides"
```

## Task 4: RenderItem Typography Metadata

**Files:**
- Modify: `render_plan.py`
- Modify: `tests/test_render_plan.py`

- [ ] **Step 1: Write failing serialization test**

Add to `tests/test_render_plan.py` near render-plan serialization tests:

```python
    def test_render_item_serializes_typography_metadata(self):
        item = pdf.RenderItem(
            kind="translated_text",
            source_ids=["p001b0001"],
            bbox=(72.0, 100.0, 540.0, 140.0),
            text="正文",
            font_size=10.8,
            style_name="body",
            typography_role="body",
            typography_target={
                "font_size": 10.8,
                "line_height_factor": 1.3,
                "paragraph_spacing": 4.0,
            },
            typography_actual={
                "font_size": 10.4,
                "line_height_factor": 1.15,
                "paragraph_spacing": 1.5,
            },
            typography_exceptions=["line_height_compacted", "font_size_reduced"],
        )

        payload = pdf.render_item_to_json(item)

        self.assertEqual(payload["typography_role"], "body")
        self.assertEqual(payload["typography_target"]["font_size"], 10.8)
        self.assertEqual(payload["typography_actual"]["font_size"], 10.4)
        self.assertEqual(payload["typography_exceptions"], ["line_height_compacted", "font_size_reduced"])
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=vendor PYTHONPYCACHEPREFIX=/private/tmp/translatePaper_pycache /opt/homebrew/bin/python3.12 -m unittest tests.test_render_plan.RenderPlanSerializationTests.test_render_item_serializes_typography_metadata -v
```

Expected: FAIL with `TypeError: RenderItem.__init__() got an unexpected keyword argument 'typography_role'`.

- [ ] **Step 3: Extend `RenderItem` and JSON serialization**

In `render_plan.py`, extend the dataclass:

```python
    typography_role: str = ""
    typography_target: dict | None = None
    typography_actual: dict | None = None
    typography_exceptions: list[str] | None = None
```

In `render_item_to_json`, include:

```python
        "typography_role": item.typography_role,
        "typography_target": item.typography_target or {},
        "typography_actual": item.typography_actual or {},
        "typography_exceptions": list(item.typography_exceptions or []),
```

- [ ] **Step 4: Run test**

Run the same test. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add render_plan.py tests/test_render_plan.py
git commit -m "Serialize typography metadata in render plans"
```

## Task 5: Profile-Aware Style Lookup In Layout

**Files:**
- Modify: `layout.py`
- Modify: `tests/test_render_plan.py`

- [ ] **Step 1: Write failing profile style test**

Add:

```python
    def test_text_style_uses_typography_profile_when_supplied(self):
        profile = pdf.TypographyProfile.default("/tmp/book.pdf", 1, (612.0, 792.0))
        profile = pdf.merge_typography_override(
            profile,
            {"styles": {"body": {"font_size": 11.0, "line_height_factor": 1.32}}},
            path="/tmp/override.json",
        )

        style = pdf.text_style("body", typography_profile=profile)

        self.assertEqual(style.font_size, 11.0)
        self.assertEqual(style.line_height_factor, 1.32)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=vendor PYTHONPYCACHEPREFIX=/private/tmp/translatePaper_pycache /opt/homebrew/bin/python3.12 -m unittest tests.test_render_plan.GlobalStyleTests.test_text_style_uses_typography_profile_when_supplied -v
```

Expected: FAIL because `text_style` does not accept `typography_profile`.

- [ ] **Step 3: Add profile-aware `text_style` overload**

In `layout.py`, import only under type-checking or inside functions to avoid cycles. Update signature:

```python
def text_style(style_name: str, typography_profile=None) -> TextStyle:
    if typography_profile is not None and style_name in typography_profile.styles:
        profile_style = typography_profile.styles[style_name]
        return TextStyle(
            font_size=profile_style.font_size,
            line_height_factor=profile_style.line_height_factor,
            paragraph_spacing=profile_style.paragraph_spacing,
            min_line_height_factor=profile_style.min_line_height_factor,
            min_paragraph_spacing=profile_style.min_paragraph_spacing,
        )
    return DOCUMENT_STYLES.get(style_name, DOCUMENT_STYLES["body"])
```

In `translate_pdf_via_codex.py`, re-export `TypographyProfile` and `merge_typography_override` for tests:

```python
from typography_profile import TypographyProfile, merge_typography_override
```

- [ ] **Step 4: Run test**

Run the same test. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add layout.py translate_pdf_via_codex.py tests/test_render_plan.py
git commit -m "Read text styles from typography profile"
```

## Task 6: Build Page Render Plans With Locked Profile Values

**Files:**
- Modify: `translate_pdf_via_codex.py`
- Modify: `tests/test_render_plan.py`

- [ ] **Step 1: Write failing cross-page consistency test**

Add:

```python
    def test_render_plan_uses_same_profile_body_font_across_pages(self):
        profile = pdf.TypographyProfile.default("/tmp/book.pdf", 2, (612.0, 792.0))
        profile = pdf.merge_typography_override(
            profile,
            {"styles": {"body": {"font_size": 11.0, "line_height_factor": 1.3, "paragraph_spacing": 4.0}}},
            path="/tmp/override.json",
        )
        pages = [
            [block("p001b0001", 1, "A short source paragraph.", x0=72, y0=100, x1=540, y1=124)],
            [block("p002b0001", 2, "A much taller source paragraph that previously could have adapted to the local source box.", x0=72, y0=100, x1=540, y1=190)],
        ]
        translations = {
            "p001b0001": "短正文。",
            "p002b0001": "较长的正文段落，用来验证同一角色跨页不会因为源框高度不同而改变字号。",
        }

        plans = [
            pdf.build_page_render_plan(page_num, page_blocks, translations, (612.0, 792.0), typography_profile=profile)
            for page_num, page_blocks in enumerate(pages, start=1)
        ]

        body_items = [
            item
            for plan in plans
            for item in plan.items
            if item.kind == "translated_text" and item.style_name == "body"
        ]
        self.assertTrue(body_items)
        self.assertEqual({item.font_size for item in body_items}, {11.0})
        self.assertEqual({item.typography_role for item in body_items}, {"body"})
        self.assertTrue(all(item.typography_target["font_size"] == 11.0 for item in body_items))
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=vendor PYTHONPYCACHEPREFIX=/private/tmp/translatePaper_pycache /opt/homebrew/bin/python3.12 -m unittest tests.test_render_plan.BodyFlowLayoutTests.test_render_plan_uses_same_profile_body_font_across_pages -v
```

Expected: FAIL because `build_page_render_plan` does not accept `typography_profile`.

- [ ] **Step 3: Thread profile through render-plan construction**

In `build_page_render_plan`, add parameter:

```python
    typography_profile=None,
```

Add helper in `translate_pdf_via_codex.py`:

```python
def typography_role_for_style(style_name: str, fallback_reason: str = "") -> str:
    if fallback_reason == "callout_heading":
        return "callout_heading"
    if fallback_reason == "callout_body":
        return "callout_body"
    if style_name == "title":
        return "chapter_title"
    if style_name == "heading":
        return "section_heading"
    if style_name == "subheading":
        return "subsection_heading"
    if style_name == "reference":
        return "reference"
    if style_name == "footer":
        return "footer"
    return "body"


def typography_style_for_render(style_name: str, fallback_reason: str = "", typography_profile=None):
    role = typography_role_for_style(style_name, fallback_reason)
    if typography_profile is not None and role in typography_profile.styles:
        return role, typography_profile.styles[role]
    return role, text_style(style_name)


def apply_typography_metadata(item: RenderItem, role: str, style, exceptions: list[str] | None = None) -> None:
    item.typography_role = role
    item.typography_target = {
        "font_size": style.font_size,
        "line_height_factor": style.line_height_factor,
        "paragraph_spacing": style.paragraph_spacing,
    }
    item.typography_actual = {
        "font_size": item.font_size or style.font_size,
        "line_height_factor": style.line_height_factor,
        "paragraph_spacing": style.paragraph_spacing,
    }
    item.typography_exceptions = list(exceptions or [])
```

When creating translated text, replace `text_style(style_name).font_size` and `render_font_size_and_reason` normal paths with profile values:

```python
role, profile_style = typography_style_for_render(style_name, callout_reason, typography_profile)
font_size = profile_style.font_size
font_reason = callout_reason
```

After each `RenderItem` creation for translated text, call `apply_typography_metadata(item, role, profile_style)`.

- [ ] **Step 4: Run test**

Run the failing test. Expected: PASS.

- [ ] **Step 5: Run affected module**

Run:

```bash
PYTHONPATH=vendor PYTHONPYCACHEPREFIX=/private/tmp/translatePaper_pycache /opt/homebrew/bin/python3.12 -m unittest tests.test_render_plan -v
```

Expected: PASS after updating tests whose expected behavior intentionally changes under profile usage. Preserve legacy no-profile expectations in separate tests that call `build_page_render_plan` without `typography_profile`.

- [ ] **Step 6: Commit**

```bash
git add translate_pdf_via_codex.py tests/test_render_plan.py
git commit -m "Use typography profile in render plans"
```

## Task 7: Typography Exception Ladder

**Files:**
- Modify: `translate_pdf_via_codex.py`
- Modify: `layout.py`
- Modify: `tests/test_render_plan.py`

- [ ] **Step 1: Write failing font-reduction-last test**

Add:

```python
    def test_typography_exception_ladder_reduces_font_only_after_spacing_compaction(self):
        profile = pdf.TypographyProfile.default("/tmp/book.pdf", 1, (612.0, 792.0))
        profile = pdf.merge_typography_override(
            profile,
            {
                "styles": {
                    "body": {
                        "font_size": 12.0,
                        "line_height_factor": 1.4,
                        "paragraph_spacing": 6.0,
                        "min_line_height_factor": 1.1,
                        "min_paragraph_spacing": 0.0,
                        "min_font_size": 10.5,
                    }
                }
            },
            path="/tmp/override.json",
        )
        blocks = [
            block("p001b0001", 1, "A compact source paragraph.", x0=72, y0=100, x1=250, y1=128),
        ]
        translations = {
            "p001b0001": "这是一个很长的中文段落，需要触发排版例外阶梯。它应该先压缩行距和段距，最后才允许缩小字号。",
        }

        plan = pdf.build_page_render_plan(1, blocks, translations, (612.0, 792.0), typography_profile=profile)
        pdf.normalize_vector_text_layout(plan, (612.0, 792.0), fitz=pdf.load_fitz(), typography_profile=profile)

        item = next(item for item in plan.items if item.kind == "translated_text")
        self.assertIn("line_height_compacted", item.typography_exceptions)
        self.assertIn("paragraph_spacing_compacted", item.typography_exceptions)
        self.assertIn("font_size_reduced", item.typography_exceptions)
        self.assertGreaterEqual(item.font_size, profile.styles["body"].min_font_size)
        self.assertLess(item.font_size, profile.styles["body"].font_size)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=vendor PYTHONPYCACHEPREFIX=/private/tmp/translatePaper_pycache /opt/homebrew/bin/python3.12 -m unittest tests.test_render_plan.BodyFlowLayoutTests.test_typography_exception_ladder_reduces_font_only_after_spacing_compaction -v
```

Expected: FAIL because `normalize_vector_text_layout` does not accept `typography_profile` and exceptions are not recorded.

- [ ] **Step 3: Implement profile-aware fit plan**

In `layout.py`, add:

```python
def text_box_fit_plan_with_ladder(fitz, text: str, width: float, height: float, style, min_font_size: float):
    target = text_box_fit_plan(fitz, text, width, height, style, font_size=style.font_size)
    if target is not None:
        return target, []
    compact_style = TextStyle(
        font_size=style.font_size,
        line_height_factor=style.line_height_factor,
        paragraph_spacing=style.paragraph_spacing,
        min_line_height_factor=style.min_line_height_factor,
        min_paragraph_spacing=style.min_paragraph_spacing,
    )
    lines = wrap_mixed_pdf_text(fitz, text, max(1.0, width), style.font_size)
    fit = fitted_text_spacing(lines, style.font_size, height, compact_style)
    exceptions = []
    if fit is not None:
        line_factor, paragraph_spacing = fit
        if line_factor < style.line_height_factor:
            exceptions.append("line_height_compacted")
        if paragraph_spacing < style.paragraph_spacing:
            exceptions.append("paragraph_spacing_compacted")
        return TextBoxFitPlan(lines, style.font_size, line_factor, paragraph_spacing, text_height_for_lines(lines, style.font_size, line_factor, paragraph_spacing), height), exceptions
    size = round(style.font_size - DENSE_VISUAL_BODY_ROW_FONT_STEP_PT, 2)
    while size >= min_font_size - TEXT_FIT_EPSILON_PT:
        candidate = max(min_font_size, round(size, 2))
        lines = wrap_mixed_pdf_text(fitz, text, max(1.0, width), candidate)
        fit = fitted_text_spacing(lines, candidate, height, compact_style)
        if fit is not None:
            line_factor, paragraph_spacing = fit
            exceptions = []
            if line_factor < style.line_height_factor:
                exceptions.append("line_height_compacted")
            if paragraph_spacing < style.paragraph_spacing:
                exceptions.append("paragraph_spacing_compacted")
            exceptions.append("font_size_reduced")
            return TextBoxFitPlan(lines, candidate, line_factor, paragraph_spacing, text_height_for_lines(lines, candidate, line_factor, paragraph_spacing), height), exceptions
        size -= DENSE_VISUAL_BODY_ROW_FONT_STEP_PT
    return None, ["text_fit_failed"]
```

If referencing `DENSE_VISUAL_BODY_ROW_FONT_STEP_PT` from `layout.py` causes ordering issues, define a local `TYPOGRAPHY_FONT_STEP_PT = 0.1` in `layout.py`.

- [ ] **Step 4: Record exceptions in normalization**

Update `normalize_vector_text_layout(plan: PageRenderPlan, page_size, fitz=None, typography_profile=None)` and the downstream fit helpers to pass the profile. When a profile-aware fit chooses compact spacing or reduced font size:

```python
item.font_size = fit_plan.font_size
item.typography_actual = {
    "font_size": fit_plan.font_size,
    "line_height_factor": fit_plan.line_height_factor,
    "paragraph_spacing": fit_plan.paragraph_spacing,
}
item.typography_exceptions = merge_exception_steps(item.typography_exceptions, exceptions)
```

Add helper:

```python
TYPOGRAPHY_EXCEPTION_ORDER = [
    "box_expanded",
    "flow_rebalanced",
    "line_height_compacted",
    "paragraph_spacing_compacted",
    "font_size_reduced",
    "text_fit_failed",
]


def merge_exception_steps(existing, added):
    seen = set(existing or [])
    seen.update(added or [])
    return [step for step in TYPOGRAPHY_EXCEPTION_ORDER if step in seen]
```

- [ ] **Step 5: Run test**

Run the failing test. Expected: PASS.

- [ ] **Step 6: Add no-silent-deviation test**

Add:

```python
    def test_profile_font_deviation_requires_typography_exception(self):
        profile = pdf.TypographyProfile.default("/tmp/book.pdf", 1, (612.0, 792.0))
        plan = pdf.PageRenderPlan(page_num=1)
        item = pdf.RenderItem(
            "translated_text",
            ["p001b0001"],
            (72.0, 100.0, 540.0, 130.0),
            text="正文",
            font_size=9.5,
            style_name="body",
            typography_role="body",
            typography_target={"font_size": profile.styles["body"].font_size, "line_height_factor": profile.styles["body"].line_height_factor, "paragraph_spacing": profile.styles["body"].paragraph_spacing},
            typography_actual={"font_size": 9.5, "line_height_factor": profile.styles["body"].line_height_factor, "paragraph_spacing": profile.styles["body"].paragraph_spacing},
            typography_exceptions=[],
        )
        plan.items.append(item)

        errors = pdf.validate_plan_typography_policy(plan, profile)

        self.assertTrue(any("unreported typography deviation" in error for error in errors))
```

- [ ] **Step 7: Implement `validate_plan_typography_policy`**

In `translate_pdf_via_codex.py` or `layout.py`:

```python
def validate_plan_typography_policy(plan: PageRenderPlan, typography_profile=None) -> list[str]:
    if typography_profile is None:
        return []
    errors = []
    for item in plan.items:
        if item.kind not in {"translated_text", "original_selectable_text"}:
            continue
        role = item.typography_role
        if not role:
            errors.append(f"page {plan.page_num} item {item.source_ids} missing typography role")
            continue
        if role not in typography_profile.styles:
            errors.append(f"page {plan.page_num} item {item.source_ids} unknown typography role {role}")
            continue
        target = typography_profile.styles[role]
        actual_font = item.font_size or target.font_size
        deviates = abs(actual_font - target.font_size) > TEXT_FIT_EPSILON_PT
        if deviates and not item.typography_exceptions:
            errors.append(f"page {plan.page_num} item {item.source_ids} has unreported typography deviation")
        if actual_font < target.min_font_size - TEXT_FIT_EPSILON_PT:
            errors.append(f"page {plan.page_num} item {item.source_ids} font size {actual_font} below minimum {target.min_font_size}")
    return errors
```

- [ ] **Step 8: Run tests**

Run:

```bash
PYTHONPATH=vendor PYTHONPYCACHEPREFIX=/private/tmp/translatePaper_pycache /opt/homebrew/bin/python3.12 -m unittest tests.test_render_plan -v
```

Expected: PASS after intentional expected-value updates.

- [ ] **Step 9: Commit**

```bash
git add layout.py translate_pdf_via_codex.py tests/test_render_plan.py
git commit -m "Record typography fit exceptions"
```

## Task 8: Typography Exception Reports

**Files:**
- Modify: `typography_profile.py`
- Modify: `tests/test_typography_profile.py`

- [ ] **Step 1: Write failing exception report test**

Append:

```python
    def test_typography_exception_report_lists_page_role_and_actual_values(self):
        profile = typo.TypographyProfile.default("/tmp/book.pdf", 1, (612.0, 792.0))
        items = [
            {
                "page": 3,
                "source_ids": ["p003b0004"],
                "typography_role": "body",
                "typography_target": {"font_size": 10.8, "line_height_factor": 1.3, "paragraph_spacing": 4.0},
                "typography_actual": {"font_size": 10.2, "line_height_factor": 1.15, "paragraph_spacing": 1.5},
                "typography_exceptions": ["line_height_compacted", "paragraph_spacing_compacted", "font_size_reduced"],
            }
        ]

        report = typo.build_typography_exception_report(profile, items)
        markdown = typo.typography_exception_markdown(report)

        self.assertEqual(report["summary"]["exception_count"], 1)
        self.assertIn("page 3", markdown)
        self.assertIn("font_size_reduced", markdown)
        self.assertIn("10.2", markdown)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=vendor PYTHONPYCACHEPREFIX=/private/tmp/translatePaper_pycache /opt/homebrew/bin/python3.12 -m unittest tests.test_typography_profile.TypographyProfileModuleTests.test_typography_exception_report_lists_page_role_and_actual_values -v
```

Expected: FAIL with missing report function.

- [ ] **Step 3: Implement exception report helpers**

Add:

```python
def build_typography_exception_report(profile: TypographyProfile, serialized_items: list[dict[str, Any]]) -> dict[str, Any]:
    exceptions = []
    for item in serialized_items:
        steps = list(item.get("typography_exceptions") or [])
        if not steps:
            continue
        exceptions.append(
            {
                "page": int(item["page"]),
                "source_ids": list(item.get("source_ids") or []),
                "role": item.get("typography_role", ""),
                "target": dict(item.get("typography_target") or {}),
                "actual": dict(item.get("typography_actual") or {}),
                "steps": steps,
                "severity": "error" if "text_fit_failed" in steps else ("warning" if "font_size_reduced" in steps else "info"),
            }
        )
    return {
        "document": profile.document.to_json_dict(),
        "summary": {
            "exception_count": len(exceptions),
            "font_size_reduction_count": sum(1 for item in exceptions if "font_size_reduced" in item["steps"]),
            "failed_count": sum(1 for item in exceptions if "text_fit_failed" in item["steps"]),
        },
        "exceptions": sorted(exceptions, key=lambda item: (item["page"], item["source_ids"])),
    }


def typography_exception_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Typography Exceptions",
        "",
        f"- Exceptions: {report['summary']['exception_count']}",
        f"- Font size reductions: {report['summary']['font_size_reduction_count']}",
        f"- Failed fits: {report['summary']['failed_count']}",
        "",
    ]
    if not report["exceptions"]:
        lines.append("No typography exceptions.")
        return "\n".join(lines) + "\n"
    for item in report["exceptions"]:
        lines.append(f"## page {item['page']} `{item['role']}` {item['severity']}")
        lines.append("")
        lines.append(f"- source_ids: `{', '.join(item['source_ids'])}`")
        lines.append(f"- steps: `{', '.join(item['steps'])}`")
        lines.append(f"- target: `{item['target']}`")
        lines.append(f"- actual: `{item['actual']}`")
        lines.append("")
    return "\n".join(lines)
```

- [ ] **Step 4: Run test**

Run the failing test. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add typography_profile.py tests/test_typography_profile.py
git commit -m "Report typography exceptions"
```

## Task 9: Parallel CLI Preflight And Artifacts

**Files:**
- Modify: `translate_pdf_parallel.py`
- Modify: `translate_pdf_via_codex.py`
- Modify: `tests/test_translate_pdf_parallel.py`

- [ ] **Step 1: Write failing CLI/report-only test**

Add to `tests/test_translate_pdf_parallel.py`:

```python
    def test_typography_report_only_writes_profile_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            job_paths = parallel.pipeline.build_job_paths(tmp_path / "book.pdf", "book")
            job_paths["job_dir"].mkdir(parents=True)
            source_pages = [
                [{"id": "p001b0001", "page": 1, "text": "A body paragraph.", "xMin": 72, "yMin": 100, "xMax": 540, "yMax": 124}]
            ]

            profile = parallel.build_or_load_typography_profile(
                source_path=tmp_path / "book.pdf",
                source_pages=source_pages,
                page_size=(612.0, 792.0),
                job_paths=job_paths,
                profile_path=None,
                override_path=None,
            )

            self.assertTrue((job_paths["job_dir"] / "typography-profile.auto.json").exists())
            self.assertTrue((job_paths["job_dir"] / "typography-profile.effective.json").exists())
            self.assertTrue((job_paths["job_dir"] / "typography-profile.md").exists())
            self.assertIn("body", profile.styles)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=vendor PYTHONPYCACHEPREFIX=/private/tmp/translatePaper_pycache /opt/homebrew/bin/python3.12 -m unittest tests.test_translate_pdf_parallel.ParallelArtifactReportTests.test_typography_report_only_writes_profile_artifacts -v
```

Expected: FAIL with missing `build_or_load_typography_profile`.

- [ ] **Step 3: Implement profile load/build artifacts**

In `translate_pdf_parallel.py`, import:

```python
import json
import typography_profile as typography
```

Add:

```python
def build_or_load_typography_profile(
    source_path: Path,
    source_pages: list[list[dict]],
    page_size: tuple[float, float],
    job_paths: dict,
    profile_path: Path | None,
    override_path: Path | None,
):
    job_dir = job_paths["job_dir"]
    job_dir.mkdir(parents=True, exist_ok=True)
    if profile_path is not None:
        profile = typography.TypographyProfile.from_json_dict(json.loads(profile_path.read_text(encoding="utf-8")))
        auto_profile = profile
    else:
        auto_profile = typography.infer_typography_profile(str(source_path), source_pages, page_size)
        profile = auto_profile
    (job_dir / "typography-profile.auto.json").write_text(
        json.dumps(auto_profile.to_json_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if override_path is not None:
        override_payload = json.loads(override_path.read_text(encoding="utf-8"))
        profile = typography.merge_typography_override(profile, override_payload, path=str(override_path))
    (job_dir / "typography-profile.effective.json").write_text(
        json.dumps(profile.to_json_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (job_dir / "typography-profile.md").write_text(typography.typography_profile_markdown(profile), encoding="utf-8")
    return profile
```

- [ ] **Step 4: Add CLI flags**

In argument parser:

```python
parser.add_argument("--typography-profile", type=Path, default=None)
parser.add_argument("--typography-override", type=Path, default=None)
parser.add_argument("--typography-report-only", action="store_true")
parser.add_argument("--no-typography-profile", action="store_true")
```

After source extraction and before translation batches:

```python
typography_profile = None
if not args.no_typography_profile:
    typography_profile = build_or_load_typography_profile(
        source_path=pdf_path,
        source_pages=source_pages,
        page_size=page_size,
        job_paths=job_paths,
        profile_path=args.typography_profile,
        override_path=args.typography_override,
    )
    if args.typography_report_only:
        return 0
```

Thread `typography_profile` into calls that eventually invoke `build_page_render_plan` and `write_vector_pdf`.

- [ ] **Step 5: Run test**

Run the failing test. Expected: PASS.

- [ ] **Step 6: Run translate parallel tests**

Run:

```bash
PYTHONPATH=vendor PYTHONPYCACHEPREFIX=/private/tmp/translatePaper_pycache /opt/homebrew/bin/python3.12 -m unittest tests.test_translate_pdf_parallel -v
```

Expected: all tests pass except the known environment-only `xelatex` test if `xelatex` is still unavailable. Do not hide that failure.

- [ ] **Step 7: Commit**

```bash
git add translate_pdf_parallel.py translate_pdf_via_codex.py tests/test_translate_pdf_parallel.py
git commit -m "Add typography profile CLI preflight"
```

## Task 10: Visual QA Typography Policy

**Files:**
- Modify: `qa_visual.py`
- Modify: `tests/test_qa_visual.py`

- [ ] **Step 1: Write failing QA test**

Add:

```python
    def test_visual_qa_reports_untracked_typography_deviation(self):
        plan = {
            "page": 1,
            "render_items": [
                {
                    "kind": "translated_text",
                    "source_ids": ["p001b0001"],
                    "bbox": [72, 100, 540, 130],
                    "text": "正文",
                    "font_size": 9.5,
                    "style_name": "body",
                    "typography_role": "body",
                    "typography_target": {"font_size": 10.8, "line_height_factor": 1.3, "paragraph_spacing": 4.0},
                    "typography_actual": {"font_size": 9.5, "line_height_factor": 1.3, "paragraph_spacing": 4.0},
                    "typography_exceptions": [],
                }
            ],
        }

        issues = qa_visual.detect_typography_issues(plan)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].issue_type, "typography_deviation")
        self.assertEqual(issues[0].severity, "error")
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=vendor PYTHONPYCACHEPREFIX=/private/tmp/translatePaper_pycache /opt/homebrew/bin/python3.12 -m unittest tests.test_qa_visual.VisualQaImageTests.test_visual_qa_reports_untracked_typography_deviation -v
```

Expected: FAIL with missing `detect_typography_issues`.

- [ ] **Step 3: Implement QA detector**

In `qa_visual.py` add:

```python
def detect_typography_issues(plan) -> list[VisualIssue]:
    issues = []
    page_num = _plan_page(plan)
    for item in _plan_items(plan):
        if _item_value(item, "kind") not in {"translated_text", "original_selectable_text"}:
            continue
        target = _item_value(item, "typography_target", {}) or {}
        actual = _item_value(item, "typography_actual", {}) or {}
        exceptions = list(_item_value(item, "typography_exceptions", []) or [])
        if not target or not actual:
            continue
        target_font = float(target.get("font_size", 0.0))
        actual_font = float(actual.get("font_size", target_font))
        if abs(actual_font - target_font) > 0.01 and not exceptions:
            issues.append(
                VisualIssue(
                    page=page_num,
                    issue_type="typography_deviation",
                    severity="error",
                    message=f"unreported typography deviation: font size {actual_font:g} differs from target {target_font:g}",
                    source_ids=list(_item_value(item, "source_ids", []) or []),
                    bbox=_item_value(item, "bbox"),
                )
            )
        if "font_size_reduced" in exceptions:
            issues.append(
                VisualIssue(
                    page=page_num,
                    issue_type="typography_font_reduced",
                    severity="warning",
                    message=f"font size reduced from target {target_font:g} to {actual_font:g}",
                    source_ids=list(_item_value(item, "source_ids", []) or []),
                    bbox=_item_value(item, "bbox"),
                )
            )
    return issues
```

Add `detect_typography_issues(plan)` to `generate_visual_qa_report` issue collection.

- [ ] **Step 4: Run test**

Run the failing test. Expected: PASS.

- [ ] **Step 5: Run QA module**

Run:

```bash
PYTHONPATH=vendor PYTHONPYCACHEPREFIX=/private/tmp/translatePaper_pycache /opt/homebrew/bin/python3.12 -m unittest tests.test_qa_visual -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add qa_visual.py tests/test_qa_visual.py
git commit -m "Report typography deviations in visual QA"
```

## Task 11: Final Integration And Key-Page Verification

**Files:**
- Modify: `translate_pdf_parallel.py`
- Modify: `translate_pdf_via_codex.py`
- Modify: `layout.py`
- Modify: `qa_visual.py`
- Modify: `tests/test_typography_profile.py`
- Modify: `tests/test_render_plan.py`
- Modify: `tests/test_translate_pdf_parallel.py`
- Modify: `tests/test_qa_visual.py`

- [ ] **Step 1: Run focused profile/report-only path**

Run:

```bash
PYTHONPATH=vendor PYTHONPYCACHEPREFIX=/private/tmp/translatePaper_pycache /opt/homebrew/bin/python3.12 translate_pdf_parallel.py --source-dir /Users/ginobili/study/paper/english --target-dir /Users/ginobili/study/paper/chinese --work-dir work/a-philosophy-software-design-profile-check --include "A Philosophy of Software Design - John Ousterhout.pdf" --typography-report-only
```

Expected:

- command exits 0,
- job directory contains `typography-profile.auto.json`,
- job directory contains `typography-profile.effective.json`,
- job directory contains `typography-profile.md`.

- [ ] **Step 2: Inspect generated profile**

Run:

```bash
jq '.styles.body, .styles.section_heading, .styles.chapter_title, .source_analysis.role_stats.body' work/a-philosophy-software-design-profile-check/jobs/a-philosophy-of-software-design---john-ousterhout/typography-profile.effective.json
```

Expected:

- body font values are present,
- heading font values are greater than body,
- body role stats include a nonzero sample count.

- [ ] **Step 3: Run focused unit suites**

Run:

```bash
PYTHONPATH=vendor PYTHONPYCACHEPREFIX=/private/tmp/translatePaper_pycache /opt/homebrew/bin/python3.12 -m unittest tests.test_typography_profile tests.test_render_plan tests.test_translate_pdf_parallel tests.test_qa_visual -v
```

Expected: PASS except for the known `xelatex` error if the environment still lacks `xelatex`.

- [ ] **Step 4: Run full AGENTS regression command**

Run:

```bash
PYTHONPYCACHEPREFIX=~/tmp/translatePaper_pycache python3 -m unittest \
  tests.test_qa_semantic tests.test_render_pdf tests.test_layout \
  tests.test_regions tests.test_render_plan tests.test_waitfree_regression \
  tests.test_pdf_render_fixtures tests.test_pdf_render_fixture_layout \
  tests.test_translate_pdf_parallel tests.test_qa_visual -v
```

Expected:

- If `xelatex` is installed: full suite passes.
- If `xelatex` is not installed: exactly the raster LaTeX assembly test fails with `FileNotFoundError: xelatex`; document this as an environment gap.

- [ ] **Step 5: Run py_compile**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/translatePaper_pycache python3 -m py_compile \
  qa_semantic.py backtranslate_check.py render_pdf.py layout.py regions.py \
  classify.py render_plan.py qa_visual.py translate_pdf_via_codex.py \
  translate_pdf_parallel.py typography_profile.py tests/test_qa_semantic.py \
  tests/test_render_pdf.py tests/test_layout.py tests/test_regions.py \
  tests/test_render_plan.py tests/test_waitfree_regression.py \
  tests/test_pdf_render_fixtures.py tests/test_pdf_render_fixture_layout.py \
  tests/test_translate_pdf_parallel.py tests/test_qa_visual.py \
  tests/test_typography_profile.py tests/pdf_render_fixture_runner.py
```

Expected: PASS.

- [ ] **Step 6: Commit final integration fixes**

If Step 1-5 required additional fixes:

```bash
git add typography_profile.py render_plan.py layout.py translate_pdf_via_codex.py translate_pdf_parallel.py qa_visual.py tests/test_typography_profile.py tests/test_render_plan.py tests/test_translate_pdf_parallel.py tests/test_qa_visual.py
git commit -m "Integrate typography profile pipeline"
```

If no files changed after earlier task commits, skip this commit.

## Self-Review Checklist

- [ ] The plan covers all spec requirements: full-document inference, locked rendering, overrides, exception ladder, reports, CLI, QA, tests.
- [ ] The plan has no unresolved filler instructions or shortcut references.
- [ ] The function names introduced in early tasks match later tasks.
- [ ] Every production code change has a failing test before implementation.
- [ ] Each task can be committed independently.
- [ ] The final verification includes both focused tests and AGENTS.md required commands.
