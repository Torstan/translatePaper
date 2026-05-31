import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

import qa_visual


class VisualQaImageTests(unittest.TestCase):
    def test_load_fitz_uses_repo_vendor_path(self):
        vendor_path = str(Path(qa_visual.__file__).resolve().parent / "vendor")
        original_path = list(sys.path)
        try:
            sys.path = [path for path in sys.path if path != vendor_path]
            try:
                qa_visual.load_fitz()
            except SystemExit:
                pass
            self.assertIn(vendor_path, sys.path)
        finally:
            sys.path = original_path

    def test_load_page_image_returns_rgb_image(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            image_path = Path(tmp_dir) / "page-001.png"
            Image.new("RGBA", (12, 10), (255, 255, 255, 128)).save(image_path)

            image = qa_visual.load_page_image(image_path)

        self.assertEqual(image.mode, "RGB")
        self.assertEqual(image.size, (12, 10))

    def test_load_page_image_pair_loads_source_and_destination(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_path = Path(tmp_dir) / "source.png"
            destination_path = Path(tmp_dir) / "destination.png"
            Image.new("RGB", (8, 6), "white").save(source_path)
            Image.new("RGB", (10, 7), "black").save(destination_path)

            pair = qa_visual.load_page_image_pair(source_path, destination_path)

        self.assertEqual(pair.source.size, (8, 6))
        self.assertEqual(pair.destination.size, (10, 7))
        self.assertEqual(pair.source_path, str(source_path))
        self.assertEqual(pair.destination_path, str(destination_path))

    def test_dark_pixel_analysis_counts_pixels_inside_pdf_bbox(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            image_path = Path(tmp_dir) / "page-001.png"
            image = Image.new("RGB", (100, 100), "white")
            draw = ImageDraw.Draw(image)
            draw.rectangle((10, 10, 19, 19), fill="black")
            image.save(image_path)

            analysis = qa_visual.analyze_dark_pixels(
                image_path,
                bbox=(10, 10, 20, 20),
                page_size=(100, 100),
            )

        self.assertEqual(analysis.dark_pixel_count, 100)
        self.assertEqual(analysis.crop_box, (10, 10, 20, 20))
        self.assertFalse(analysis.is_blank)

    def test_dark_pixel_analysis_reports_blank_empty_bbox(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            image_path = Path(tmp_dir) / "page-001.png"
            Image.new("RGB", (100, 100), "white").save(image_path)

            analysis = qa_visual.analyze_dark_pixels(
                image_path,
                bbox=(0, 0, 20, 20),
                page_size=(100, 100),
            )

        self.assertEqual(analysis.dark_pixel_count, 0)
        self.assertTrue(analysis.is_blank)

    def test_blank_image_clip_detection_reports_empty_source_clip_bbox(self):
        plan = {
            "page_num": 5,
            "render_items": [
                {
                    "kind": "original_image_clip",
                    "source_ids": ["p005b0004"],
                    "bbox": [10, 10, 30, 30],
                    "fallback_reason": "visual_region",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_png = Path(tmp_dir) / "page-005.png"
            Image.new("RGB", (100, 100), "white").save(source_png)

            issues = qa_visual.detect_blank_image_clips(
                plan,
                source_png,
                page_size=(100, 100),
            )

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].category, "blank_clip")
        self.assertEqual(issues[0].severity, "error")
        self.assertEqual(issues[0].page_num, 5)
        self.assertEqual(issues[0].source_ids, ["p005b0004"])
        self.assertEqual(issues[0].bbox, (10.0, 10.0, 30.0, 30.0))
        self.assertEqual(issues[0].render_kind, "original_image_clip")
        self.assertEqual(issues[0].artifact_paths["source_png"], str(source_png))
        self.assertIn("blank source image clip", issues[0].message)

    def test_blank_image_clip_detection_allows_non_empty_source_clip_bbox(self):
        plan = {
            "page_num": 5,
            "render_items": [
                {
                    "kind": "original_image_clip",
                    "source_ids": ["p005b0004"],
                    "bbox": [10, 10, 30, 30],
                    "fallback_reason": "visual_region",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_png = Path(tmp_dir) / "page-005.png"
            image = Image.new("RGB", (100, 100), "white")
            draw = ImageDraw.Draw(image)
            draw.rectangle((12, 12, 16, 16), fill="black")
            image.save(source_png)

            issues = qa_visual.detect_blank_image_clips(
                plan,
                source_png,
                page_size=(100, 100),
            )

        self.assertEqual(issues, [])

    def test_geometry_checks_report_text_over_protected_region(self):
        plan = {
            "page_num": 9,
            "render_items": [
                {
                    "kind": "translated_text",
                    "source_ids": ["p009b0001"],
                    "bbox": [10, 10, 80, 50],
                    "text": "正文",
                },
                {
                    "kind": "original_image_clip",
                    "source_ids": ["p009b0002"],
                    "bbox": [20, 18, 90, 55],
                },
            ],
            "protected_regions": [{"bbox": [20, 18, 90, 55]}],
        }

        issues = qa_visual.detect_geometry_issues(plan, page_size=(100, 100))

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].category, "text_protected_overlap")
        self.assertEqual(issues[0].severity, "error")
        self.assertEqual(issues[0].page_num, 9)
        self.assertEqual(issues[0].source_ids, ["p009b0001"])
        self.assertEqual(issues[0].render_kind, "translated_text")

    def test_geometry_checks_use_serialized_protected_regions_without_clip_item(self):
        plan = {
            "page_num": 9,
            "render_items": [
                {
                    "kind": "translated_text",
                    "source_ids": ["p009b0001"],
                    "bbox": [10, 10, 80, 50],
                    "text": "正文",
                }
            ],
            "protected_regions": [{"bbox": [20, 18, 90, 55]}],
        }

        issues = qa_visual.detect_geometry_issues(plan, page_size=(100, 100))

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].category, "text_protected_overlap")

    def test_geometry_checks_report_text_text_overlap(self):
        plan = {
            "page_num": 10,
            "render_items": [
                {
                    "kind": "translated_text",
                    "source_ids": ["p010b0001"],
                    "bbox": [10, 10, 70, 50],
                    "text": "第一段",
                },
                {
                    "kind": "original_selectable_text",
                    "source_ids": ["p010b0002"],
                    "bbox": [20, 16, 80, 55],
                    "text": "第二段",
                },
            ],
        }

        issues = qa_visual.detect_geometry_issues(plan, page_size=(100, 100))

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].category, "text_overlap")
        self.assertEqual(issues[0].source_ids, ["p010b0001", "p010b0002"])
        self.assertEqual(issues[0].render_kind, "text")

    def test_geometry_checks_allow_overlap_for_same_source_text_fragments(self):
        plan = {
            "page_num": 10,
            "render_items": [
                {
                    "kind": "translated_text",
                    "source_ids": ["p010b0001"],
                    "bbox": [10, 10, 70, 50],
                    "text": "第一段",
                },
                {
                    "kind": "translated_text",
                    "source_ids": ["p010b0001"],
                    "bbox": [20, 16, 80, 55],
                    "text": "同源拆分",
                },
            ],
        }

        issues = qa_visual.detect_geometry_issues(plan, page_size=(100, 100))

        self.assertEqual(issues, [])

    def test_geometry_checks_report_item_outside_page_bounds(self):
        plan = {
            "page_num": 11,
            "render_items": [
                {
                    "kind": "translated_text",
                    "source_ids": ["p011b0001"],
                    "bbox": [-12, 10, 40, 30],
                    "text": "越界",
                }
            ],
        }

        issues = qa_visual.detect_geometry_issues(plan, page_size=(100, 100))

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].category, "page_bounds")
        self.assertEqual(issues[0].source_ids, ["p011b0001"])
        self.assertEqual(issues[0].bbox, (-12.0, 10.0, 40.0, 30.0))

    def test_image_clip_boundary_checks_report_likely_clipped_content(self):
        plan = {
            "page_num": 12,
            "render_items": [
                {
                    "kind": "original_image_clip",
                    "source_ids": ["p012b0003"],
                    "bbox": [20, 20, 50, 50],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_png = Path(tmp_dir) / "page-012.png"
            image = Image.new("RGB", (100, 100), "white")
            draw = ImageDraw.Draw(image)
            draw.rectangle((19, 30, 20, 40), fill="black")
            image.save(source_png)

            issues = qa_visual.detect_image_clip_boundary_issues(
                plan,
                source_png,
                page_size=(100, 100),
            )

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].category, "clipped_content")
        self.assertEqual(issues[0].source_ids, ["p012b0003"])
        self.assertEqual(issues[0].render_kind, "original_image_clip")
        self.assertEqual(issues[0].artifact_paths["source_png"], str(source_png))

    def test_image_clip_boundary_checks_allow_dark_excess_covered_by_sibling_clip(self):
        plan = {
            "page_num": 12,
            "render_items": [
                {
                    "kind": "original_image_clip",
                    "component_id": "p012c0001",
                    "source_ids": ["p012b0003"],
                    "bbox": [20, 20, 50, 50],
                },
                {
                    "kind": "original_image_clip",
                    "component_id": "p012c0001",
                    "source_ids": ["p012b0003"],
                    "bbox": [18, 20, 50, 50],
                },
            ],
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_png = Path(tmp_dir) / "page-012.png"
            image = Image.new("RGB", (100, 100), "white")
            ImageDraw.Draw(image).rectangle((19, 30, 20, 40), fill="black")
            image.save(source_png)

            issues = qa_visual.detect_image_clip_boundary_issues(
                plan,
                source_png,
                page_size=(100, 100),
            )

        self.assertNotIn("clipped_content", {issue.category for issue in issues})

    def test_image_clip_boundary_checks_report_dark_excess_in_sibling_gap(self):
        plan = {
            "page_num": 12,
            "render_items": [
                {
                    "kind": "original_image_clip",
                    "component_id": "p012c0001",
                    "source_ids": ["p012b0003"],
                    "bbox": [20, 20, 50, 50],
                },
                {
                    "kind": "original_image_clip",
                    "component_id": "p012c0001",
                    "source_ids": ["p012b0003"],
                    "bbox": [54, 20, 80, 50],
                },
            ],
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_png = Path(tmp_dir) / "page-012.png"
            image = Image.new("RGB", (100, 100), "white")
            ImageDraw.Draw(image).rectangle((51, 30, 52, 40), fill="black")
            image.save(source_png)

            issues = qa_visual.detect_image_clip_boundary_issues(
                plan,
                source_png,
                page_size=(100, 100),
            )

        self.assertIn("clipped_content", {issue.category for issue in issues})

    def test_image_clip_boundary_checks_allow_content_away_from_clip_edges(self):
        plan = {
            "page_num": 12,
            "render_items": [
                {
                    "kind": "original_image_clip",
                    "source_ids": ["p012b0003"],
                    "bbox": [20, 20, 50, 50],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_png = Path(tmp_dir) / "page-012.png"
            image = Image.new("RGB", (100, 100), "white")
            draw = ImageDraw.Draw(image)
            draw.rectangle((30, 30, 40, 40), fill="black")
            image.save(source_png)

            issues = qa_visual.detect_image_clip_boundary_issues(
                plan,
                source_png,
                page_size=(100, 100),
            )

        self.assertEqual(issues, [])

    def test_image_clip_boundary_checks_report_body_region_overcapture(self):
        plan = {
            "page_num": 13,
            "render_items": [
                {
                    "kind": "original_image_clip",
                    "source_ids": ["p013b0003", "p013b0004"],
                    "bbox": [20, 20, 80, 80],
                }
            ],
            "coverage_ledger": [
                {
                    "block_id": "p013b0003",
                    "classification": "figure_region",
                    "render_kind": "original_image_clip",
                    "rendered": True,
                },
                {
                    "block_id": "p013b0004",
                    "classification": "body",
                    "render_kind": "original_image_clip",
                    "rendered": True,
                },
            ],
        }
        blocks = [
            {
                "id": "p013b0004",
                "text": "This is body prose that should be translated.",
                "xMin": 30,
                "yMin": 30,
                "xMax": 70,
                "yMax": 60,
            }
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_png = Path(tmp_dir) / "page-013.png"
            Image.new("RGB", (100, 100), "white").save(source_png)

            issues = qa_visual.detect_image_clip_boundary_issues(
                plan,
                source_png,
                page_size=(100, 100),
                source_blocks=blocks,
            )

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].category, "region_overcapture")
        self.assertEqual(issues[0].source_ids, ["p013b0004"])
        self.assertEqual(issues[0].bbox, (30.0, 30.0, 70.0, 60.0))

    def test_image_clip_boundary_checks_report_overcaptured_body_not_in_clip_source_ids(self):
        plan = {
            "page_num": 13,
            "render_items": [
                {
                    "kind": "original_image_clip",
                    "source_ids": ["p013b0003"],
                    "bbox": [20, 20, 80, 80],
                },
                {
                    "kind": "translated_text",
                    "source_ids": ["p013b0004"],
                    "bbox": [30, 30, 70, 60],
                    "text": "正文",
                },
            ],
            "coverage_ledger": [
                {
                    "block_id": "p013b0003",
                    "classification": "figure_region",
                    "render_kind": "original_image_clip",
                    "rendered": True,
                },
                {
                    "block_id": "p013b0004",
                    "classification": "body",
                    "render_kind": "translated_text",
                    "rendered": True,
                },
            ],
        }
        blocks = [
            {
                "id": "p013b0004",
                "text": "This body prose is geometrically inside the clip.",
                "xMin": 30,
                "yMin": 30,
                "xMax": 70,
                "yMax": 60,
            }
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_png = Path(tmp_dir) / "page-013.png"
            Image.new("RGB", (100, 100), "white").save(source_png)

            issues = qa_visual.detect_image_clip_boundary_issues(
                plan,
                source_png,
                page_size=(100, 100),
                source_blocks=blocks,
            )

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].category, "region_overcapture")
        self.assertEqual(issues[0].source_ids, ["p013b0004"])

    def test_image_clip_boundary_checks_report_visual_owned_source_block_outside_clip(self):
        plan = {
            "page_num": 13,
            "render_items": [
                {
                    "kind": "original_image_clip",
                    "source_ids": ["p013b0003", "p013b0004"],
                    "bbox": [40, 20, 80, 80],
                    "component_id": "p013c0001",
                    "component_kind": "visual",
                }
            ],
            "coverage_ledger": [
                {
                    "block_id": "p013b0003",
                    "classification": "figure_region",
                    "component_id": "p013c0001",
                    "component_kind": "visual",
                    "render_kind": "original_image_clip",
                    "rendered": True,
                },
                {
                    "block_id": "p013b0004",
                    "classification": "figure_region",
                    "component_id": "p013c0001",
                    "component_kind": "visual",
                    "render_kind": "original_image_clip",
                    "rendered": True,
                },
            ],
        }
        blocks = [
            {
                "id": "p013b0004",
                "text": "A visual label outside the clip.",
                "xMin": 10,
                "yMin": 30,
                "xMax": 30,
                "yMax": 60,
            }
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_png = Path(tmp_dir) / "page-013.png"
            image = Image.new("RGB", (100, 100), "white")
            ImageDraw.Draw(image).rectangle((12, 32, 28, 58), fill="black")
            image.save(source_png)

            issues = qa_visual.detect_image_clip_boundary_issues(
                plan,
                source_png,
                page_size=(100, 100),
                source_blocks=blocks,
            )

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].category, "visual_undercapture")
        self.assertEqual(issues[0].source_ids, ["p013b0004"])

    def test_image_clip_boundary_checks_allow_body_block_outside_clip_bbox(self):
        plan = {
            "page_num": 13,
            "render_items": [
                {
                    "kind": "original_image_clip",
                    "source_ids": ["p013b0003"],
                    "bbox": [20, 20, 50, 50],
                },
                {
                    "kind": "translated_text",
                    "source_ids": ["p013b0004"],
                    "bbox": [60, 60, 80, 80],
                    "text": "正文",
                },
            ],
            "coverage_ledger": [
                {
                    "block_id": "p013b0004",
                    "classification": "body",
                    "render_kind": "translated_text",
                    "rendered": True,
                },
            ],
        }
        blocks = [
            {
                "id": "p013b0004",
                "text": "This body prose is outside the clip.",
                "xMin": 60,
                "yMin": 60,
                "xMax": 80,
                "yMax": 80,
            }
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_png = Path(tmp_dir) / "page-013.png"
            Image.new("RGB", (100, 100), "white").save(source_png)

            issues = qa_visual.detect_image_clip_boundary_issues(
                plan,
                source_png,
                page_size=(100, 100),
                source_blocks=blocks,
            )

        self.assertEqual(issues, [])

    def test_style_checks_report_heading_style_mismatch(self):
        plan = {
            "page_num": 15,
            "render_items": [
                {
                    "kind": "translated_text",
                    "source_ids": ["p015b0001"],
                    "bbox": [10, 10, 80, 30],
                    "text": "标题",
                    "style_name": "body",
                    "font_size": 9.2,
                }
            ],
            "coverage_ledger": [
                {
                    "block_id": "p015b0001",
                    "classification": "heading",
                    "render_kind": "translated_text",
                    "rendered": True,
                }
            ],
        }

        issues = qa_visual.detect_style_issues(plan)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].category, "style_hierarchy")
        self.assertEqual(issues[0].source_ids, ["p015b0001"])
        self.assertEqual(issues[0].render_kind, "translated_text")

    def test_style_checks_allow_heading_classification_with_subheading_style(self):
        plan = {
            "page_num": 15,
            "render_items": [
                {
                    "kind": "translated_text",
                    "source_ids": ["p015b0001"],
                    "bbox": [10, 10, 80, 30],
                    "text": "3.1 子标题",
                    "style_name": "subheading",
                    "font_size": 10.2,
                }
            ],
            "coverage_ledger": [
                {
                    "block_id": "p015b0001",
                    "classification": "heading",
                    "render_kind": "translated_text",
                    "rendered": True,
                }
            ],
        }

        issues = qa_visual.detect_style_issues(plan)

        self.assertEqual(issues, [])

    def test_style_checks_report_body_font_inconsistency(self):
        plan = {
            "page_num": 16,
            "render_items": [
                {
                    "kind": "translated_text",
                    "source_ids": ["p016b0001"],
                    "bbox": [10, 10, 80, 30],
                    "text": "正文一",
                    "style_name": "body",
                    "font_size": 9.2,
                },
                {
                    "kind": "translated_text",
                    "source_ids": ["p016b0002"],
                    "bbox": [10, 40, 80, 60],
                    "text": "正文二",
                    "style_name": "body",
                    "font_size": 8.0,
                },
            ],
            "coverage_ledger": [
                {
                    "block_id": "p016b0001",
                    "classification": "body",
                    "render_kind": "translated_text",
                    "rendered": True,
                },
                {
                    "block_id": "p016b0002",
                    "classification": "body",
                    "render_kind": "translated_text",
                    "rendered": True,
                },
            ],
        }

        issues = qa_visual.detect_style_issues(plan)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].category, "body_font_consistency")
        self.assertEqual(issues[0].source_ids, ["p016b0002"])
        self.assertEqual(issues[0].bbox, (10.0, 40.0, 80.0, 60.0))

    def test_style_checks_allow_body_font_difference_with_fallback_reason(self):
        plan = {
            "page_num": 16,
            "render_items": [
                {
                    "kind": "translated_text",
                    "source_ids": ["p016b0001"],
                    "bbox": [10, 10, 80, 30],
                    "text": "正文一",
                    "style_name": "body",
                    "font_size": 9.2,
                },
                {
                    "kind": "translated_text",
                    "source_ids": ["p016b0002"],
                    "bbox": [10, 40, 80, 60],
                    "text": "正文二",
                    "style_name": "body",
                    "font_size": 8.0,
                    "fallback_reason": "fit_shrink",
                },
            ],
            "coverage_ledger": [
                {
                    "block_id": "p016b0001",
                    "classification": "body",
                    "render_kind": "translated_text",
                    "rendered": True,
                },
                {
                    "block_id": "p016b0002",
                    "classification": "body",
                    "render_kind": "translated_text",
                    "rendered": True,
                },
            ],
        }

        issues = qa_visual.detect_style_issues(plan)

        self.assertEqual(issues, [])

    def test_style_checks_do_not_use_fallback_body_item_as_baseline(self):
        plan = {
            "page_num": 16,
            "render_items": [
                {
                    "kind": "translated_text",
                    "source_ids": ["p016b0001"],
                    "bbox": [10, 10, 80, 30],
                    "text": "压缩正文",
                    "style_name": "body",
                    "font_size": 8.0,
                    "fallback_reason": "fit_shrink",
                },
                {
                    "kind": "translated_text",
                    "source_ids": ["p016b0002"],
                    "bbox": [10, 40, 80, 60],
                    "text": "正常正文一",
                    "style_name": "body",
                    "font_size": 9.2,
                },
                {
                    "kind": "translated_text",
                    "source_ids": ["p016b0003"],
                    "bbox": [10, 70, 80, 90],
                    "text": "正常正文二",
                    "style_name": "body",
                    "font_size": 9.2,
                },
            ],
            "coverage_ledger": [
                {
                    "block_id": "p016b0001",
                    "classification": "body",
                    "render_kind": "translated_text",
                    "rendered": True,
                },
                {
                    "block_id": "p016b0002",
                    "classification": "body",
                    "render_kind": "translated_text",
                    "rendered": True,
                },
                {
                    "block_id": "p016b0003",
                    "classification": "body",
                    "render_kind": "translated_text",
                    "rendered": True,
                },
            ],
        }

        issues = qa_visual.detect_style_issues(plan)

        self.assertEqual(issues, [])

    def test_body_flow_whitespace_check_reports_warning_by_default(self):
        plan = {
            "page_num": 18,
            "render_items": [
                {
                    "kind": "translated_text",
                    "source_ids": ["p018b0001"],
                    "bbox": [10, 10, 80, 30],
                    "text": "正文一",
                    "style_name": "body",
                    "font_size": 9.2,
                },
                {
                    "kind": "translated_text",
                    "source_ids": ["p018b0002"],
                    "bbox": [10, 90, 80, 110],
                    "text": "正文二",
                    "style_name": "body",
                    "font_size": 9.2,
                },
            ],
            "coverage_ledger": [
                {
                    "block_id": "p018b0001",
                    "classification": "body",
                    "render_kind": "translated_text",
                    "rendered": True,
                },
                {
                    "block_id": "p018b0002",
                    "classification": "body",
                    "render_kind": "translated_text",
                    "rendered": True,
                },
            ],
        }

        issues = qa_visual.detect_body_flow_whitespace_issues(plan)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].category, "body_flow_whitespace")
        self.assertEqual(issues[0].severity, "warning")
        self.assertEqual(issues[0].source_ids, ["p018b0001", "p018b0002"])

    def test_body_flow_whitespace_check_reports_error_when_strict(self):
        plan = {
            "page_num": 18,
            "render_items": [
                {
                    "kind": "translated_text",
                    "source_ids": ["p018b0001"],
                    "bbox": [10, 10, 80, 30],
                    "text": "正文一",
                    "style_name": "body",
                    "font_size": 9.2,
                },
                {
                    "kind": "translated_text",
                    "source_ids": ["p018b0002"],
                    "bbox": [10, 90, 80, 110],
                    "text": "正文二",
                    "style_name": "body",
                    "font_size": 9.2,
                },
            ],
            "coverage_ledger": [
                {
                    "block_id": "p018b0001",
                    "classification": "body",
                    "render_kind": "translated_text",
                    "rendered": True,
                },
                {
                    "block_id": "p018b0002",
                    "classification": "body",
                    "render_kind": "translated_text",
                    "rendered": True,
                },
            ],
        }

        issues = qa_visual.detect_body_flow_whitespace_issues(plan, strict=True)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "error")

    def test_body_flow_whitespace_check_allows_reasonable_gap(self):
        plan = {
            "page_num": 18,
            "render_items": [
                {
                    "kind": "translated_text",
                    "source_ids": ["p018b0001"],
                    "bbox": [10, 10, 80, 30],
                    "text": "正文一",
                    "style_name": "body",
                    "font_size": 9.2,
                },
                {
                    "kind": "translated_text",
                    "source_ids": ["p018b0002"],
                    "bbox": [10, 38, 80, 58],
                    "text": "正文二",
                    "style_name": "body",
                    "font_size": 9.2,
                },
            ],
            "coverage_ledger": [
                {
                    "block_id": "p018b0001",
                    "classification": "body",
                    "render_kind": "translated_text",
                    "rendered": True,
                },
                {
                    "block_id": "p018b0002",
                    "classification": "body",
                    "render_kind": "translated_text",
                    "rendered": True,
                },
            ],
        }

        issues = qa_visual.detect_body_flow_whitespace_issues(plan)

        self.assertEqual(issues, [])

    def test_body_flow_whitespace_check_allows_gap_with_protected_barrier(self):
        plan = {
            "page_num": 18,
            "render_items": [
                {
                    "kind": "translated_text",
                    "source_ids": ["p018b0001"],
                    "bbox": [10, 10, 80, 30],
                    "text": "正文一",
                    "style_name": "body",
                    "font_size": 9.2,
                },
                {
                    "kind": "original_image_clip",
                    "source_ids": ["p018b0003"],
                    "bbox": [10, 40, 80, 80],
                },
                {
                    "kind": "translated_text",
                    "source_ids": ["p018b0002"],
                    "bbox": [10, 90, 80, 110],
                    "text": "正文二",
                    "style_name": "body",
                    "font_size": 9.2,
                },
            ],
            "coverage_ledger": [
                {
                    "block_id": "p018b0001",
                    "classification": "body",
                    "render_kind": "translated_text",
                    "rendered": True,
                },
                {
                    "block_id": "p018b0002",
                    "classification": "body",
                    "render_kind": "translated_text",
                    "rendered": True,
                },
                {
                    "block_id": "p018b0003",
                    "classification": "figure_region",
                    "render_kind": "original_image_clip",
                    "rendered": True,
                },
            ],
        }

        issues = qa_visual.detect_body_flow_whitespace_issues(plan, strict=True)

        self.assertEqual(issues, [])

    def test_body_flow_whitespace_check_reports_gap_when_other_column_interleaves(self):
        plan = {
            "page_num": 18,
            "render_items": [
                {
                    "kind": "translated_text",
                    "source_ids": ["p018b0001"],
                    "bbox": [10, 10, 80, 30],
                    "text": "左栏正文一",
                    "style_name": "body",
                    "font_size": 9.2,
                },
                {
                    "kind": "translated_text",
                    "source_ids": ["p018b0002"],
                    "bbox": [120, 45, 190, 65],
                    "text": "右栏正文",
                    "style_name": "body",
                    "font_size": 9.2,
                },
                {
                    "kind": "translated_text",
                    "source_ids": ["p018b0003"],
                    "bbox": [10, 90, 80, 110],
                    "text": "左栏正文二",
                    "style_name": "body",
                    "font_size": 9.2,
                },
            ],
            "coverage_ledger": [
                {
                    "block_id": "p018b0001",
                    "classification": "body",
                    "render_kind": "translated_text",
                    "rendered": True,
                },
                {
                    "block_id": "p018b0002",
                    "classification": "body",
                    "render_kind": "translated_text",
                    "rendered": True,
                },
                {
                    "block_id": "p018b0003",
                    "classification": "body",
                    "render_kind": "translated_text",
                    "rendered": True,
                },
            ],
        }

        issues = qa_visual.detect_body_flow_whitespace_issues(plan)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].source_ids, ["p018b0001", "p018b0003"])

    def test_body_flow_whitespace_check_reports_gap_with_tiny_protected_sliver(self):
        plan = {
            "page_num": 18,
            "render_items": [
                {
                    "kind": "translated_text",
                    "source_ids": ["p018b0001"],
                    "bbox": [10, 10, 80, 30],
                    "text": "正文一",
                    "style_name": "body",
                    "font_size": 9.2,
                },
                {
                    "kind": "original_image_clip",
                    "source_ids": ["p018b0003"],
                    "bbox": [10, 40, 80, 41],
                },
                {
                    "kind": "translated_text",
                    "source_ids": ["p018b0002"],
                    "bbox": [10, 90, 80, 110],
                    "text": "正文二",
                    "style_name": "body",
                    "font_size": 9.2,
                },
            ],
            "coverage_ledger": [
                {
                    "block_id": "p018b0001",
                    "classification": "body",
                    "render_kind": "translated_text",
                    "rendered": True,
                },
                {
                    "block_id": "p018b0002",
                    "classification": "body",
                    "render_kind": "translated_text",
                    "rendered": True,
                },
                {
                    "block_id": "p018b0003",
                    "classification": "figure_region",
                    "render_kind": "original_image_clip",
                    "rendered": True,
                },
            ],
        }

        issues = qa_visual.detect_body_flow_whitespace_issues(plan)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].category, "body_flow_whitespace")

    def test_body_flow_whitespace_check_reports_gap_with_reference_anchor(self):
        plan = {
            "page_num": 18,
            "render_items": [
                {
                    "kind": "translated_text",
                    "source_ids": ["p018b0001"],
                    "bbox": [10, 10, 80, 30],
                    "text": "正文一",
                    "style_name": "body",
                    "font_size": 9.2,
                },
                {
                    "kind": "original_selectable_text",
                    "source_ids": ["p018b0003"],
                    "bbox": [15, 45, 70, 55],
                    "text": "anchor",
                    "style_name": "reference",
                    "font_size": 7.0,
                },
                {
                    "kind": "translated_text",
                    "source_ids": ["p018b0002"],
                    "bbox": [10, 90, 80, 110],
                    "text": "正文二",
                    "style_name": "body",
                    "font_size": 9.2,
                },
            ],
            "coverage_ledger": [
                {
                    "block_id": "p018b0001",
                    "classification": "body",
                    "render_kind": "translated_text",
                    "rendered": True,
                },
                {
                    "block_id": "p018b0002",
                    "classification": "body",
                    "render_kind": "translated_text",
                    "rendered": True,
                },
                {
                    "block_id": "p018b0003",
                    "classification": "reference",
                    "render_kind": "original_selectable_text",
                    "rendered": True,
                },
            ],
        }

        issues = qa_visual.detect_body_flow_whitespace_issues(plan)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].category, "body_flow_whitespace")

    def test_body_flow_whitespace_check_allows_gap_with_subheading_barrier(self):
        plan = {
            "page_num": 18,
            "render_items": [
                {
                    "kind": "translated_text",
                    "source_ids": ["p018b0001"],
                    "bbox": [10, 10, 80, 30],
                    "text": "正文一",
                    "style_name": "body",
                    "font_size": 9.2,
                },
                {
                    "kind": "translated_text",
                    "source_ids": ["p018b0003"],
                    "bbox": [10, 50, 80, 66],
                    "text": "2.1 小节",
                    "style_name": "subheading",
                    "font_size": 10.2,
                },
                {
                    "kind": "translated_text",
                    "source_ids": ["p018b0002"],
                    "bbox": [10, 90, 80, 110],
                    "text": "正文二",
                    "style_name": "body",
                    "font_size": 9.2,
                },
            ],
            "coverage_ledger": [
                {
                    "block_id": "p018b0001",
                    "classification": "body",
                    "render_kind": "translated_text",
                    "rendered": True,
                },
                {
                    "block_id": "p018b0002",
                    "classification": "body",
                    "render_kind": "translated_text",
                    "rendered": True,
                },
                {
                    "block_id": "p018b0003",
                    "classification": "subheading",
                    "render_kind": "translated_text",
                    "rendered": True,
                },
            ],
        }

        issues = qa_visual.detect_body_flow_whitespace_issues(plan)

        self.assertEqual(issues, [])

    def test_body_flow_whitespace_check_allows_serialized_protected_region_barrier(self):
        plan = {
            "page_num": 18,
            "render_items": [
                {
                    "kind": "translated_text",
                    "source_ids": ["p018b0001"],
                    "bbox": [10, 10, 80, 30],
                    "text": "正文一",
                    "style_name": "body",
                    "font_size": 9.2,
                },
                {
                    "kind": "translated_text",
                    "source_ids": ["p018b0002"],
                    "bbox": [10, 90, 80, 110],
                    "text": "正文二",
                    "style_name": "body",
                    "font_size": 9.2,
                },
            ],
            "protected_regions": [{"bbox": [10, 42, 80, 76]}],
            "coverage_ledger": [
                {
                    "block_id": "p018b0001",
                    "classification": "body",
                    "render_kind": "translated_text",
                    "rendered": True,
                },
                {
                    "block_id": "p018b0002",
                    "classification": "body",
                    "render_kind": "translated_text",
                    "rendered": True,
                },
            ],
        }

        issues = qa_visual.detect_body_flow_whitespace_issues(plan)

        self.assertEqual(issues, [])


class VisualQaRulePairTests(unittest.TestCase):
    def assertCategoryReported(self, issues, category):
        self.assertIn(category, {issue.category for issue in issues})

    def assertCategoryAbsent(self, issues, category):
        self.assertNotIn(category, {issue.category for issue in issues})

    def test_blank_clip_rule_fails_bad_plan_and_passes_corrected_plan(self):
        plan = {
            "page_num": 20,
            "render_items": [
                {
                    "kind": "original_image_clip",
                    "source_ids": ["p020b0001"],
                    "bbox": [10, 10, 30, 30],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            bad_png = Path(tmp_dir) / "bad.png"
            corrected_png = Path(tmp_dir) / "corrected.png"
            Image.new("RGB", (100, 100), "white").save(bad_png)
            corrected = Image.new("RGB", (100, 100), "white")
            ImageDraw.Draw(corrected).rectangle((12, 12, 20, 20), fill="black")
            corrected.save(corrected_png)

            bad_issues = qa_visual.detect_blank_image_clips(plan, bad_png, page_size=(100, 100))
            corrected_issues = qa_visual.detect_blank_image_clips(
                plan,
                corrected_png,
                page_size=(100, 100),
            )

        self.assertCategoryReported(bad_issues, "blank_clip")
        self.assertCategoryAbsent(corrected_issues, "blank_clip")

    def test_text_protected_overlap_rule_fails_bad_plan_and_passes_corrected_plan(self):
        bad_plan = {
            "page_num": 21,
            "render_items": [
                {
                    "kind": "translated_text",
                    "source_ids": ["p021b0001"],
                    "bbox": [10, 10, 80, 50],
                    "text": "正文",
                },
                {
                    "kind": "original_image_clip",
                    "source_ids": ["p021b0002"],
                    "bbox": [20, 18, 90, 55],
                },
            ],
        }
        corrected_plan = {
            "page_num": 21,
            "render_items": [
                {
                    "kind": "translated_text",
                    "source_ids": ["p021b0001"],
                    "bbox": [10, 60, 80, 80],
                    "text": "正文",
                },
                {
                    "kind": "original_image_clip",
                    "source_ids": ["p021b0002"],
                    "bbox": [20, 18, 90, 55],
                },
            ],
        }

        bad_issues = qa_visual.detect_geometry_issues(bad_plan, page_size=(100, 100))
        corrected_issues = qa_visual.detect_geometry_issues(corrected_plan, page_size=(100, 100))

        self.assertCategoryReported(bad_issues, "text_protected_overlap")
        self.assertCategoryAbsent(corrected_issues, "text_protected_overlap")

    def test_text_overlap_rule_fails_bad_plan_and_passes_corrected_plan(self):
        bad_plan = {
            "page_num": 22,
            "render_items": [
                {
                    "kind": "translated_text",
                    "source_ids": ["p022b0001"],
                    "bbox": [10, 10, 70, 50],
                    "text": "第一段",
                },
                {
                    "kind": "original_selectable_text",
                    "source_ids": ["p022b0002"],
                    "bbox": [20, 16, 80, 55],
                    "text": "第二段",
                },
            ],
        }
        corrected_plan = {
            "page_num": 22,
            "render_items": [
                {
                    "kind": "translated_text",
                    "source_ids": ["p022b0001"],
                    "bbox": [10, 10, 70, 50],
                    "text": "第一段",
                },
                {
                    "kind": "original_selectable_text",
                    "source_ids": ["p022b0002"],
                    "bbox": [10, 60, 70, 80],
                    "text": "第二段",
                },
            ],
        }

        bad_issues = qa_visual.detect_geometry_issues(bad_plan, page_size=(100, 100))
        corrected_issues = qa_visual.detect_geometry_issues(corrected_plan, page_size=(100, 100))

        self.assertCategoryReported(bad_issues, "text_overlap")
        self.assertCategoryAbsent(corrected_issues, "text_overlap")

    def test_page_bounds_rule_fails_bad_plan_and_passes_corrected_plan(self):
        bad_plan = {
            "page_num": 23,
            "render_items": [
                {
                    "kind": "translated_text",
                    "source_ids": ["p023b0001"],
                    "bbox": [-12, 10, 40, 30],
                    "text": "越界",
                }
            ],
        }
        corrected_plan = {
            "page_num": 23,
            "render_items": [
                {
                    "kind": "translated_text",
                    "source_ids": ["p023b0001"],
                    "bbox": [10, 10, 40, 30],
                    "text": "正文",
                }
            ],
        }

        bad_issues = qa_visual.detect_geometry_issues(bad_plan, page_size=(100, 100))
        corrected_issues = qa_visual.detect_geometry_issues(corrected_plan, page_size=(100, 100))

        self.assertCategoryReported(bad_issues, "page_bounds")
        self.assertCategoryAbsent(corrected_issues, "page_bounds")

    def test_clipped_content_rule_passes_with_two_point_visual_padding(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_png = Path(tmp_dir) / "page-024.png"
            image = Image.new("RGB", (100, 100), "white")
            ImageDraw.Draw(image).rectangle((20, 30, 21, 40), fill="black")
            image.save(source_png)
            plan = {
                "page_num": 24,
                "render_items": [
                    {
                        "kind": "original_image_clip",
                        "source_ids": ["p024b0001"],
                        "bbox": [18, 20, 50, 50],
                    }
                ],
            }

            issues = qa_visual.detect_image_clip_boundary_issues(
                plan,
                source_png,
                page_size=(100, 100),
            )

        self.assertCategoryAbsent(issues, "clipped_content")

    def test_clipped_content_rule_fails_bad_plan_and_passes_corrected_plan(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_png = Path(tmp_dir) / "page-024.png"
            image = Image.new("RGB", (100, 100), "white")
            ImageDraw.Draw(image).rectangle((19, 30, 20, 40), fill="black")
            image.save(source_png)

            bad_plan = {
                "page_num": 24,
                "render_items": [
                    {
                        "kind": "original_image_clip",
                        "source_ids": ["p024b0001"],
                        "bbox": [20, 20, 50, 50],
                    }
                ],
            }
            corrected_plan = {
                "page_num": 24,
                "render_items": [
                    {
                        "kind": "original_image_clip",
                        "source_ids": ["p024b0001"],
                        "bbox": [16, 20, 50, 50],
                    }
                ],
            }

            bad_issues = qa_visual.detect_image_clip_boundary_issues(
                bad_plan,
                source_png,
                page_size=(100, 100),
            )
            corrected_issues = qa_visual.detect_image_clip_boundary_issues(
                corrected_plan,
                source_png,
                page_size=(100, 100),
            )

        self.assertCategoryReported(bad_issues, "clipped_content")
        self.assertCategoryAbsent(corrected_issues, "clipped_content")

    def test_clipped_content_rule_allows_dark_content_on_inside_clip_edge(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_png = Path(tmp_dir) / "page-024.png"
            image = Image.new("RGB", (100, 100), "white")
            ImageDraw.Draw(image).rectangle((20, 30, 21, 40), fill="black")
            image.save(source_png)
            plan = {
                "page_num": 24,
                "render_items": [
                    {
                        "kind": "original_image_clip",
                        "source_ids": ["p024b0001"],
                        "bbox": [20, 20, 50, 50],
                    }
                ],
            }

            issues = qa_visual.detect_image_clip_boundary_issues(
                plan,
                source_png,
                page_size=(100, 100),
            )

        self.assertCategoryAbsent(issues, "clipped_content")

    def test_region_overcapture_rule_fails_bad_plan_and_passes_corrected_plan(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_png = Path(tmp_dir) / "page-025.png"
            Image.new("RGB", (100, 100), "white").save(source_png)
            bad_plan = {
                "page_num": 25,
                "render_items": [
                    {
                        "kind": "original_image_clip",
                        "source_ids": ["p025b0001"],
                        "bbox": [20, 20, 80, 80],
                    }
                ],
                "coverage_ledger": [
                    {
                        "block_id": "p025b0002",
                        "classification": "body",
                        "render_kind": "translated_text",
                        "rendered": True,
                    }
                ],
            }
            corrected_plan = {
                "page_num": 25,
                "render_items": [
                    {
                        "kind": "original_image_clip",
                        "source_ids": ["p025b0001"],
                        "bbox": [20, 20, 50, 50],
                    }
                ],
                "coverage_ledger": bad_plan["coverage_ledger"],
            }
            bad_blocks = [
                {
                    "id": "p025b0002",
                    "text": "Body text",
                    "xMin": 30,
                    "yMin": 30,
                    "xMax": 70,
                    "yMax": 60,
                }
            ]
            corrected_blocks = [
                {
                    "id": "p025b0002",
                    "text": "Body text",
                    "xMin": 60,
                    "yMin": 60,
                    "xMax": 80,
                    "yMax": 80,
                }
            ]

            bad_issues = qa_visual.detect_image_clip_boundary_issues(
                bad_plan,
                source_png,
                page_size=(100, 100),
                source_blocks=bad_blocks,
            )
            corrected_issues = qa_visual.detect_image_clip_boundary_issues(
                corrected_plan,
                source_png,
                page_size=(100, 100),
                source_blocks=corrected_blocks,
            )

        self.assertCategoryReported(bad_issues, "region_overcapture")
        self.assertCategoryAbsent(corrected_issues, "region_overcapture")

    def test_region_overcapture_rule_uses_component_ownership_before_body_classification(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_png = Path(tmp_dir) / "page-025.png"
            Image.new("RGB", (100, 100), "white").save(source_png)
            plan = {
                "page_num": 25,
                "render_items": [
                    {
                        "kind": "original_image_clip",
                        "source_ids": ["p025b0001", "p025b0002"],
                        "bbox": [20, 20, 80, 80],
                        "component_id": "p025c0001",
                        "component_kind": "visual",
                    }
                ],
                "coverage_ledger": [
                    {
                        "block_id": "p025b0002",
                        "classification": "body",
                        "component_id": "p025c0001",
                        "component_kind": "visual",
                        "render_kind": "original_image_clip",
                        "rendered": True,
                    }
                ],
            }
            blocks = [
                {
                    "id": "p025b0002",
                    "text": "A diagram label classified as body by extraction.",
                    "xMin": 30,
                    "yMin": 30,
                    "xMax": 70,
                    "yMax": 60,
                }
            ]

            issues = qa_visual.detect_image_clip_boundary_issues(
                plan,
                source_png,
                page_size=(100, 100),
                source_blocks=blocks,
            )

        self.assertCategoryAbsent(issues, "region_overcapture")

    def test_style_hierarchy_rule_fails_bad_plan_and_passes_corrected_plan(self):
        bad_plan = {
            "page_num": 26,
            "render_items": [
                {
                    "kind": "translated_text",
                    "source_ids": ["p026b0001"],
                    "bbox": [10, 10, 80, 30],
                    "text": "标题",
                    "style_name": "body",
                    "font_size": 9.2,
                }
            ],
            "coverage_ledger": [
                {
                    "block_id": "p026b0001",
                    "classification": "heading",
                    "render_kind": "translated_text",
                    "rendered": True,
                }
            ],
        }
        corrected_plan = {
            **bad_plan,
            "render_items": [
                {
                    **bad_plan["render_items"][0],
                    "style_name": "heading",
                    "font_size": 12.0,
                }
            ],
        }

        bad_issues = qa_visual.detect_style_issues(bad_plan)
        corrected_issues = qa_visual.detect_style_issues(corrected_plan)

        self.assertCategoryReported(bad_issues, "style_hierarchy")
        self.assertCategoryAbsent(corrected_issues, "style_hierarchy")

    def test_body_font_consistency_rule_fails_bad_plan_and_passes_corrected_plan(self):
        bad_plan = {
            "page_num": 27,
            "render_items": [
                {
                    "kind": "translated_text",
                    "source_ids": ["p027b0001"],
                    "bbox": [10, 10, 80, 30],
                    "text": "正文一",
                    "style_name": "body",
                    "font_size": 9.2,
                },
                {
                    "kind": "translated_text",
                    "source_ids": ["p027b0002"],
                    "bbox": [10, 40, 80, 60],
                    "text": "正文二",
                    "style_name": "body",
                    "font_size": 8.0,
                },
            ],
            "coverage_ledger": [
                {
                    "block_id": "p027b0001",
                    "classification": "body",
                    "render_kind": "translated_text",
                    "rendered": True,
                },
                {
                    "block_id": "p027b0002",
                    "classification": "body",
                    "render_kind": "translated_text",
                    "rendered": True,
                },
            ],
        }
        corrected_plan = {
            **bad_plan,
            "render_items": [
                bad_plan["render_items"][0],
                {
                    **bad_plan["render_items"][1],
                    "font_size": 9.2,
                },
            ],
        }

        bad_issues = qa_visual.detect_style_issues(bad_plan)
        corrected_issues = qa_visual.detect_style_issues(corrected_plan)

        self.assertCategoryReported(bad_issues, "body_font_consistency")
        self.assertCategoryAbsent(corrected_issues, "body_font_consistency")

    def test_body_flow_whitespace_rule_fails_bad_plan_and_passes_corrected_plan(self):
        bad_plan = {
            "page_num": 28,
            "render_items": [
                {
                    "kind": "translated_text",
                    "source_ids": ["p028b0001"],
                    "bbox": [10, 10, 80, 30],
                    "text": "正文一",
                    "style_name": "body",
                    "font_size": 9.2,
                },
                {
                    "kind": "translated_text",
                    "source_ids": ["p028b0002"],
                    "bbox": [10, 90, 80, 110],
                    "text": "正文二",
                    "style_name": "body",
                    "font_size": 9.2,
                },
            ],
            "coverage_ledger": [
                {
                    "block_id": "p028b0001",
                    "classification": "body",
                    "render_kind": "translated_text",
                    "rendered": True,
                },
                {
                    "block_id": "p028b0002",
                    "classification": "body",
                    "render_kind": "translated_text",
                    "rendered": True,
                },
            ],
        }
        corrected_plan = {
            **bad_plan,
            "render_items": [
                bad_plan["render_items"][0],
                {
                    **bad_plan["render_items"][1],
                    "bbox": [10, 38, 80, 58],
                },
            ],
        }

        bad_issues = qa_visual.detect_body_flow_whitespace_issues(bad_plan)
        corrected_issues = qa_visual.detect_body_flow_whitespace_issues(corrected_plan)

        self.assertCategoryReported(bad_issues, "body_flow_whitespace")
        self.assertCategoryAbsent(corrected_issues, "body_flow_whitespace")


class VisualQaRenderTests(unittest.TestCase):
    def test_render_pdf_pages_to_png_uses_fitz_and_reports_paths(self):
        class FakePixmap:
            def save(self, path):
                Path(path).write_bytes(b"png")

        class FakePage:
            def get_pixmap(self, matrix, alpha):
                self.matrix = matrix
                self.alpha = alpha
                return FakePixmap()

        class FakeDoc:
            page_count = 2

            def __init__(self):
                self.loaded_pages = []
                self.closed = False

            def load_page(self, index):
                self.loaded_pages.append(index)
                return FakePage()

            def close(self):
                self.closed = True

        class FakeFitz:
            def __init__(self):
                self.doc = FakeDoc()

            def Matrix(self, x_scale, y_scale):
                return (x_scale, y_scale)

            def open(self, pdf_path):
                self.pdf_path = pdf_path
                return self.doc

        with tempfile.TemporaryDirectory() as tmp_dir:
            pdf_path = Path(tmp_dir) / "source.pdf"
            output_dir = Path(tmp_dir) / "rendered"
            fake_fitz = FakeFitz()

            with patch.object(qa_visual, "load_fitz", return_value=fake_fitz):
                paths = qa_visual.render_pdf_pages_to_png(
                    pdf_path,
                    output_dir,
                    pages=[2, 1],
                    dpi=72,
                )

            self.assertEqual([path.name for path in paths], ["page-001.png", "page-002.png"])
            self.assertTrue(all(path.exists() for path in paths))
            self.assertEqual(fake_fitz.doc.loaded_pages, [0, 1])
            self.assertTrue(fake_fitz.doc.closed)


class VisualQaReportTests(unittest.TestCase):
    def test_detect_ownership_issues_maps_validation_codes(self):
        plan = {
            "page_num": 21,
            "ownership_validation": {
                "ok": False,
                "issues": [
                    {
                        "issue_code": "duplicate_owner",
                        "severity": "error",
                        "message": "source block p021b0001 has multiple owners",
                        "source_ids": ["p021b0001"],
                        "component_ids": ["p021c0001", "p021c0002"],
                        "bboxes": [[10, 20, 30, 40]],
                    },
                    {
                        "issue_code": "text_over_visual_component",
                        "severity": "warning",
                        "message": "text overlaps visual component",
                        "source_ids": ["p021b0002"],
                        "component_ids": ["p021c0003", "p021c0002"],
                        "bboxes": [[11, 21, 31, 41]],
                    },
                    {
                        "issue_code": "visual_overcapture",
                        "severity": "error",
                        "message": "visual clip captures unrelated text",
                        "source_ids": ["p021b0003"],
                        "component_ids": ["p021c0004"],
                        "bboxes": [[12, 22, 32, 42]],
                    },
                    {
                        "issue_code": "missing_owner",
                        "severity": "error",
                        "message": "source block p021b0004 has no owner",
                        "source_ids": ["p021b0004"],
                    },
                    {
                        "issue_code": "invalid_owner",
                        "severity": "warning",
                        "message": "source block p021b0005 has invalid owner",
                        "source_ids": ["p021b0005"],
                    },
                    {
                        "issue_code": "ownership_violation",
                        "severity": "error",
                        "message": "ownership validation failed",
                        "source_ids": ["p021b0006"],
                    },
                ],
            },
        }

        issues = qa_visual.detect_ownership_issues(plan)

        self.assertEqual(
            [issue.category for issue in issues],
            [
                "duplicate_ownership",
                "text_over_visual",
                "visual_overcapture",
                "missing_ownership",
                "invalid_ownership",
                "ownership_violation",
            ],
        )
        self.assertEqual([issue.render_kind for issue in issues], ["ownership"] * 6)
        self.assertEqual(issues[0].source_ids, ["p021b0001"])
        self.assertEqual(issues[0].bbox, (10.0, 20.0, 30.0, 40.0))
        self.assertEqual(issues[1].severity, "warning")
        self.assertEqual(issues[1].artifact_paths, {"component_ids": "p021c0002,p021c0003"})
        self.assertEqual(issues[3].artifact_paths, {})

    def test_generate_visual_qa_report_includes_ownership_issues_when_plan_loaded(self):
        plan = {
            "page_num": 22,
            "render_items": [],
            "coverage_ledger": [],
            "protected_regions": [],
            "validation_results": [],
            "ownership_validation": {
                "ok": False,
                "issues": [
                    {
                        "issue_code": "duplicate_owner",
                        "severity": "error",
                        "message": "source block p022b0001 has multiple owners",
                        "source_ids": ["p022b0001"],
                        "component_ids": ["p022c0001", "p022c0002"],
                        "bboxes": [[10, 20, 30, 40]],
                    }
                ],
            },
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            plan_path = tmp_path / "page-022.render-plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")

            report = qa_visual.generate_visual_qa_report(
                [plan_path],
                output_dir=tmp_path,
            )
            report_json = json.loads(report.json_path.read_text(encoding="utf-8"))

        self.assertEqual(report_json["issue_count"], 1)
        self.assertEqual(report_json["issues"][0]["category"], "duplicate_ownership")
        self.assertEqual(report_json["issues"][0]["render_kind"], "ownership")

    def test_generate_visual_qa_report_writes_deterministic_json_and_markdown(self):
        issues = [
            qa_visual.VisualQaIssue(
                category="blank_clip",
                severity="error",
                page_num=2,
                message="blank image clip",
                source_ids=["p002b0003"],
                bbox=(1, 2, 3, 4),
                render_kind="original_image_clip",
                artifact_paths={"source_png": "/tmp/source/page-002.png"},
            ),
            qa_visual.VisualQaIssue(
                category="style",
                severity="warning",
                page_num=1,
                message="heading style mismatch",
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir)

            report = qa_visual.write_visual_qa_report(
                issues,
                output_dir,
                checked_pages=[2, 1],
                png_paths={2: "/tmp/rendered/page-002.png", 1: "/tmp/rendered/page-001.png"},
            )

            report_json = json.loads(report.json_path.read_text(encoding="utf-8"))
            report_md = report.markdown_path.read_text(encoding="utf-8")

        self.assertEqual(report.issue_count, 2)
        self.assertEqual(report.error_count, 1)
        self.assertEqual(report.warning_count, 1)
        self.assertEqual(report_json["checked_pages"], [1, 2])
        self.assertEqual(report_json["highest_severity"], "error")
        self.assertEqual(report_json["issue_count"], 2)
        self.assertEqual(report_json["issues"][0]["category"], "style")
        self.assertEqual(report_json["issues"][1]["source_ids"], ["p002b0003"])
        self.assertIn("# Visual QA Report", report_md)
        self.assertIn("blank_clip", report_md)

    def test_generate_visual_qa_report_is_stable_for_same_inputs(self):
        issues = [
            qa_visual.VisualQaIssue(
                category="bounds",
                severity="error",
                page_num=3,
                message="outside page bounds",
                bbox=(3, 2, 1, 0),
            )
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir)

            first = qa_visual.visual_qa_report_json(
                issues,
                checked_pages=[3],
                png_paths={3: output_dir / "page-003.png"},
            )
            second = qa_visual.visual_qa_report_json(
                issues,
                checked_pages=[3],
                png_paths={3: output_dir / "page-003.png"},
            )

        self.assertEqual(first, second)

    def test_generate_visual_qa_report_loads_plan_artifact_pages(self):
        plan = {
            "page_num": 7,
            "render_items": [],
            "coverage_ledger": [],
            "protected_regions": [],
            "validation_results": [],
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            plan_path = Path(tmp_dir) / "page-007.render-plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")

            report = qa_visual.generate_visual_qa_report(
                [plan_path],
                output_dir=Path(tmp_dir),
            )

            report_json = json.loads(report.json_path.read_text(encoding="utf-8"))

        self.assertEqual(report_json["checked_pages"], [7])
        self.assertEqual(report_json["plan_artifact_paths"], [str(plan_path)])

    def test_generate_visual_qa_report_invokes_png_rendering_when_pdf_given(self):
        plan = {
            "page_num": 4,
            "render_items": [],
            "coverage_ledger": [],
            "protected_regions": [],
            "validation_results": [],
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            plan_path = tmp_path / "page-004.render-plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            pdf_path = tmp_path / "translated.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n")

            with patch.object(
                qa_visual,
                "render_pdf_pages_to_png",
                return_value=[tmp_path / "rendered" / "page-004.png"],
            ) as render_mock:
                report = qa_visual.generate_visual_qa_report(
                    [plan_path],
                    output_dir=tmp_path,
                    translated_pdf_path=pdf_path,
                )
                report_json = json.loads(report.json_path.read_text(encoding="utf-8"))

        render_mock.assert_called_once()
        self.assertEqual(report_json["rendered_png_paths"]["4"], str(tmp_path / "rendered" / "page-004.png"))

    def test_generate_visual_qa_report_includes_blank_clip_issues_when_source_pngs_given(self):
        plan = {
            "page_num": 6,
            "render_items": [
                {
                    "kind": "original_image_clip",
                    "source_ids": ["p006b0002"],
                    "bbox": [10, 10, 30, 30],
                    "fallback_reason": "visual_region",
                }
            ],
            "coverage_ledger": [],
            "protected_regions": [],
            "validation_results": [],
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            plan_path = tmp_path / "page-006.render-plan.json"
            source_png = tmp_path / "page-006.png"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            Image.new("RGB", (100, 100), "white").save(source_png)

            report = qa_visual.generate_visual_qa_report(
                [plan_path],
                output_dir=tmp_path,
                source_png_paths={6: source_png},
                page_size=(100, 100),
            )
            report_json = json.loads(report.json_path.read_text(encoding="utf-8"))

        self.assertEqual(report_json["issue_count"], 1)
        self.assertEqual(report_json["issues"][0]["category"], "blank_clip")
        self.assertEqual(report_json["issues"][0]["source_ids"], ["p006b0002"])
        self.assertEqual(report_json["issues"][0]["bbox"], [10.0, 10.0, 30.0, 30.0])
        self.assertEqual(report_json["issues"][0]["render_kind"], "original_image_clip")
        self.assertEqual(report_json["issues"][0]["artifact_paths"]["source_png"], str(source_png))

    def test_generate_visual_qa_report_includes_geometry_issues_when_page_size_given(self):
        plan = {
            "page_num": 8,
            "render_items": [
                {
                    "kind": "translated_text",
                    "source_ids": ["p008b0001"],
                    "bbox": [-12, 10, 40, 30],
                    "text": "越界",
                }
            ],
            "coverage_ledger": [],
            "protected_regions": [],
            "validation_results": [],
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            plan_path = tmp_path / "page-008.render-plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")

            report = qa_visual.generate_visual_qa_report(
                [plan_path],
                output_dir=tmp_path,
                page_size=(100, 140),
            )
            report_json = json.loads(report.json_path.read_text(encoding="utf-8"))

        self.assertEqual(report_json["issue_count"], 1)
        self.assertEqual(report_json["issues"][0]["category"], "page_bounds")
        self.assertEqual(report_json["issues"][0]["bbox"], [-12.0, 10.0, 40.0, 30.0])

    def test_generate_visual_qa_report_includes_image_clip_boundary_issues(self):
        plan = {
            "page_num": 14,
            "render_items": [
                {
                    "kind": "original_image_clip",
                    "source_ids": ["p014b0003", "p014b0004"],
                    "bbox": [20, 20, 50, 50],
                }
            ],
            "coverage_ledger": [
                {
                    "block_id": "p014b0003",
                    "classification": "figure_region",
                    "render_kind": "original_image_clip",
                    "rendered": True,
                },
                {
                    "block_id": "p014b0004",
                    "classification": "body",
                    "render_kind": "original_image_clip",
                    "rendered": True,
                },
            ],
            "protected_regions": [],
            "validation_results": [],
        }
        blocks = [
            {
                "id": "p014b0004",
                "text": "Body text",
                "xMin": 30,
                "yMin": 30,
                "xMax": 45,
                "yMax": 40,
            }
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            plan_path = tmp_path / "page-014.render-plan.json"
            source_png = tmp_path / "page-014.png"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            image = Image.new("RGB", (100, 100), "white")
            draw = ImageDraw.Draw(image)
            draw.rectangle((19, 30, 20, 40), fill="black")
            image.save(source_png)

            report = qa_visual.generate_visual_qa_report(
                [plan_path],
                output_dir=tmp_path,
                source_png_paths={14: source_png},
                source_blocks_by_page={14: blocks},
                page_size=(100, 100),
            )
            report_json = json.loads(report.json_path.read_text(encoding="utf-8"))

        self.assertEqual(report_json["issue_count"], 2)
        self.assertEqual(
            {issue["category"] for issue in report_json["issues"]},
            {"clipped_content", "region_overcapture"},
        )

    def test_generate_visual_qa_report_includes_style_issues_when_page_size_given(self):
        plan = {
            "page_num": 17,
            "render_items": [
                {
                    "kind": "translated_text",
                    "source_ids": ["p017b0001"],
                    "bbox": [10, 10, 80, 30],
                    "text": "标题",
                    "style_name": "body",
                    "font_size": 9.2,
                }
            ],
            "coverage_ledger": [
                {
                    "block_id": "p017b0001",
                    "classification": "heading",
                    "render_kind": "translated_text",
                    "rendered": True,
                }
            ],
            "protected_regions": [],
            "validation_results": [],
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            plan_path = tmp_path / "page-017.render-plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")

            report = qa_visual.generate_visual_qa_report(
                [plan_path],
                output_dir=tmp_path,
                page_size=(100, 140),
            )
            report_json = json.loads(report.json_path.read_text(encoding="utf-8"))

        self.assertEqual(report_json["issue_count"], 1)
        self.assertEqual(report_json["issues"][0]["category"], "style_hierarchy")

    def test_generate_visual_qa_report_includes_body_flow_warning_by_default(self):
        plan = {
            "page_num": 18,
            "render_items": [
                {
                    "kind": "translated_text",
                    "source_ids": ["p018b0001"],
                    "bbox": [10, 10, 80, 30],
                    "text": "正文一",
                    "style_name": "body",
                    "font_size": 9.2,
                },
                {
                    "kind": "translated_text",
                    "source_ids": ["p018b0002"],
                    "bbox": [10, 90, 80, 110],
                    "text": "正文二",
                    "style_name": "body",
                    "font_size": 9.2,
                },
            ],
            "coverage_ledger": [
                {
                    "block_id": "p018b0001",
                    "classification": "body",
                    "render_kind": "translated_text",
                    "rendered": True,
                },
                {
                    "block_id": "p018b0002",
                    "classification": "body",
                    "render_kind": "translated_text",
                    "rendered": True,
                },
            ],
            "protected_regions": [],
            "validation_results": [],
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            plan_path = tmp_path / "page-018.render-plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")

            report = qa_visual.generate_visual_qa_report(
                [plan_path],
                output_dir=tmp_path,
                page_size=(100, 140),
            )
            report_json = json.loads(report.json_path.read_text(encoding="utf-8"))

        self.assertEqual(report_json["issue_count"], 1)
        self.assertEqual(report_json["warning_count"], 1)
        self.assertEqual(report_json["issues"][0]["category"], "body_flow_whitespace")
        self.assertEqual(report_json["issues"][0]["severity"], "warning")

    def test_generate_visual_qa_report_includes_body_flow_error_when_strict(self):
        plan = {
            "page_num": 18,
            "render_items": [
                {
                    "kind": "translated_text",
                    "source_ids": ["p018b0001"],
                    "bbox": [10, 10, 80, 30],
                    "text": "正文一",
                    "style_name": "body",
                    "font_size": 9.2,
                },
                {
                    "kind": "translated_text",
                    "source_ids": ["p018b0002"],
                    "bbox": [10, 90, 80, 110],
                    "text": "正文二",
                    "style_name": "body",
                    "font_size": 9.2,
                },
            ],
            "coverage_ledger": [
                {
                    "block_id": "p018b0001",
                    "classification": "body",
                    "render_kind": "translated_text",
                    "rendered": True,
                },
                {
                    "block_id": "p018b0002",
                    "classification": "body",
                    "render_kind": "translated_text",
                    "rendered": True,
                },
            ],
            "protected_regions": [],
            "validation_results": [],
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            plan_path = tmp_path / "page-018.render-plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")

            report = qa_visual.generate_visual_qa_report(
                [plan_path],
                output_dir=tmp_path,
                page_size=(100, 140),
                strict_body_flow=True,
            )
            report_json = json.loads(report.json_path.read_text(encoding="utf-8"))

        self.assertEqual(report_json["issue_count"], 1)
        self.assertEqual(report_json["error_count"], 1)
        self.assertEqual(report_json["issues"][0]["severity"], "error")


if __name__ == "__main__":
    unittest.main()
