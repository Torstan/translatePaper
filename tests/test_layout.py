import unittest
from unittest.mock import patch

import layout
import ownership
import render_pdf
import render_plan
import translate_pdf_via_codex as pdf


class FakeFitz:
    def get_text_length(self, text, fontname="helv", fontsize=10.0):
        return len(text) * fontsize * 0.5


class LayoutExtractionModuleTests(unittest.TestCase):
    def test_style_lookup_defaults_to_body(self):
        self.assertEqual(layout.text_style("body").font_size, layout.BODY_FONT_SIZE)
        self.assertEqual(layout.text_style("missing").font_size, layout.BODY_FONT_SIZE)

    def test_spacing_compacts_to_available_height(self):
        style = layout.TextStyle(
            font_size=10.0,
            line_height_factor=1.2,
            paragraph_spacing=3.0,
            min_line_height_factor=1.0,
            min_paragraph_spacing=0.0,
        )
        lines = [["第一段"], [], ["第二段"]]

        self.assertEqual(layout.text_height_for_lines(lines, 10.0, 1.0, 0.0), 30.0)
        self.assertEqual(layout.fitted_text_spacing(lines, 10.0, 31.0, style), (1.0, 0.0))

    def test_text_item_fit_metrics_uses_shared_text_box_fit_plan(self):
        fitz = FakeFitz()
        item = render_plan.RenderItem(
            "translated_text",
            ["body"],
            (10.0, 10.0, 70.0, 40.0),
            text="第一段。\n第二段。",
            font_size=layout.BODY_FONT_SIZE,
            style_name="body",
        )
        style = layout.text_style("body")
        plan = layout.text_box_fit_plan(
            fitz,
            item.text,
            item.bbox[2] - item.bbox[0],
            item.bbox[3] - item.bbox[1],
            style,
            font_size=item.font_size,
        )

        fit, _required, available = layout.text_item_fit_metrics(item, fitz)

        self.assertIsNotNone(plan)
        self.assertEqual(fit, (plan.line_height_factor, plan.paragraph_spacing))
        self.assertEqual(available, plan.available_height)

    def test_reference_fit_metrics_allow_compact_line_height(self):
        fitz = FakeFitz()
        item = render_plan.RenderItem(
            "original_selectable_text",
            ["ref"],
            (0.0, 0.0, 30.0, 42.0),
            text="referenceone referencetwo referencethree referencefour referencefive referencesix referenceseven",
            font_size=layout.DOCUMENT_STYLES["reference"].font_size,
            style_name="reference",
        )

        fit, _required, _available = layout.text_item_fit_metrics(item, fitz)

        self.assertEqual(fit, (1.0, 0.0))

    def test_wrap_mixed_pdf_text_direct_api_uses_deterministic_tokens(self):
        fitz = FakeFitz()

        self.assertEqual(
            layout.wrap_mixed_pdf_text(fitz, "alpha beta 中文", max_width=30.0, font_size=10.0),
            [["alpha"], ["beta", " ", "中", "文"]],
        )

    def test_math_tokens_do_not_use_unregistered_font_without_font_file(self):
        fitz = FakeFitz()
        with patch.object(layout, "MATH_VECTOR_FONT_PATHS", ("/missing/STIXMath-Regular.otf",)):
            layout._MATH_FITZ_FONT = None
            self.assertEqual(layout.pdf_token_font("≤"), "helv")
            self.assertGreater(layout.pdf_token_width(fitz, "≤", 10.0), 0.0)

    def test_raster_font_skips_missing_candidates(self):
        existing_font = layout.raster_font_path()
        if existing_font is None:
            self.skipTest("no raster CJK font available on this host")

        with patch.object(layout, "RASTER_FONT_PATHS", ("/missing/uming.ttc", existing_font)):
            font = layout.raster_image_font(12)

        self.assertGreater(font.getmetrics()[0], 0)

    def test_raster_font_preferences_put_regular_weight_before_medium(self):
        self.assertLess(
            layout.RASTER_FONT_PATHS.index("/System/Library/Fonts/Hiragino Sans GB.ttc"),
            layout.RASTER_FONT_PATHS.index("/System/Library/Fonts/STHeiti Medium.ttc"),
        )
        self.assertLess(
            layout.RASTER_FONT_PATHS.index("/System/Library/Fonts/STHeiti Light.ttc"),
            layout.RASTER_FONT_PATHS.index("/System/Library/Fonts/STHeiti Medium.ttc"),
        )
        self.assertEqual(
            layout.RASTER_FONT_FACE_INDEXES["/System/Library/Fonts/Supplemental/Songti.ttc"],
            3,
        )

    def test_raster_font_uses_configured_face_index(self):
        loaded_font = object()
        with (
            patch.object(layout, "RASTER_FONT_PATHS", ("font.ttc",)),
            patch.object(layout, "RASTER_FONT_FACE_INDEXES", {"font.ttc": 3}, create=True),
            patch.object(layout.Path, "exists", return_value=True),
            patch.object(layout.ImageFont, "truetype", return_value=loaded_font) as load_font,
        ):
            font = layout.raster_image_font(12)

        self.assertIs(font, loaded_font)
        load_font.assert_called_once_with("font.ttc", 12, index=3)

    def test_raster_font_fit_uses_expanded_line_height(self):
        class FakeRasterFont:
            def __init__(self, size):
                self.size = size

            def getmetrics(self):
                return self.size, 0

        with (
            patch.object(layout, "raster_image_font", side_effect=FakeRasterFont),
            patch.object(layout, "wrap_text", return_value=["第一行", "第二行", "第三行"]),
        ):
            font_size, lines = layout.fit_font_and_lines("ignored", 200, 90, vertical=False)

        self.assertEqual(font_size, 20)
        self.assertEqual(lines, ["第一行", "第二行", "第三行"])

    def test_raster_text_required_height_uses_expanded_line_height(self):
        class FakeRasterFont:
            def getmetrics(self):
                return 10, 10

        with (
            patch.object(layout, "raster_image_font", return_value=FakeRasterFont()),
            patch.object(layout, "wrap_text", return_value=["第一行", "第二行", "第三行"]),
        ):
            height = layout.text_required_height("ignored", 200, 12)

        self.assertEqual(height, int((10 + 10) * 1.5) * 3 + 4)

    def test_raster_source_line_height_targets_expanded_spacing(self):
        block = {
            "text": "line one\nline two\nline three",
            "yMin": 0.0,
            "yMax": 150.0,
        }

        expected = max(10, int(round((150.0 / 3.0) / 1.5 * layout.SOURCE_FONT_SCALE)))

        self.assertEqual(layout.target_font_size_for_block(block, dpi=72, vertical=False), expected)

    def test_protected_region_split_leaves_usable_segments(self):
        protected = [render_plan.RenderItem("original_image_clip", ["fig"], (100.0, 100.0, 180.0, 160.0))]

        shifted = layout.shifted_boxes_around_protected(
            (120.0, 110.0, 220.0, 150.0),
            protected,
            (300.0, 300.0),
        )
        segments = layout.text_segments_around_protected(
            (90.0, 90.0, 220.0, 180.0),
            protected,
        )

        self.assertIn((180.75, 110.0, 280.75, 150.0), shifted)
        self.assertIn((90.0, 160.75, 220.0, 180.0), segments)

    def test_split_translated_text_around_protected_preserves_ledger_update(self):
        plan = render_plan.PageRenderPlan(page_num=7)
        plan.items.append(render_plan.RenderItem("original_image_clip", ["fig"], (100.0, 100.0, 180.0, 160.0)))
        plan.items.append(
            render_plan.RenderItem(
                "translated_text",
                ["body"],
                (90.0, 90.0, 220.0, 180.0),
                text="第一句。第二句。",
                font_size=layout.BODY_FONT_SIZE,
                style_name="body",
            )
        )
        plan.ledger.append(render_plan.CoverageEntry("body", "body", "translated_text", True, None))

        layout.split_translated_text_around_protected(plan, page_size=(300.0, 300.0), fitz=FakeFitz())

        body_items = [item for item in plan.items if item.kind == "translated_text"]
        self.assertTrue(body_items)
        self.assertTrue(all(item.fallback_reason == "split_around_visual" for item in body_items))
        self.assertEqual(plan.ledger[0].fallback_reason, "split_around_visual")

    def test_split_translated_text_around_protected_keeps_item_when_segments_get_no_text(self):
        plan = render_plan.PageRenderPlan(page_num=7)
        plan.items.append(render_plan.RenderItem("original_image_clip", ["fig"], (100.0, 100.0, 180.0, 160.0)))
        original = render_plan.RenderItem(
            "translated_text",
            ["body"],
            (90.0, 90.0, 220.0, 180.0),
            text="第一句。第二句。",
            font_size=layout.BODY_FONT_SIZE,
            style_name="body",
        )
        plan.items.append(original)
        plan.ledger.append(render_plan.CoverageEntry("body", "body", "translated_text", True, None))

        def empty_segments(_text, segments, _style=None, fitz=None):
            return ["" for _segment in segments]

        with patch.object(layout, "distribute_text_across_segments", side_effect=empty_segments):
            layout.split_translated_text_around_protected(plan, page_size=(300.0, 300.0), fitz=FakeFitz())

        self.assertIn(original, plan.items)
        self.assertEqual(plan.ledger[0].render_kind, "translated_text")
        self.assertIsNone(plan.ledger[0].fallback_reason)

    def test_text_fit_validation_rejects_unrecorded_font_shrinking(self):
        plan = render_plan.PageRenderPlan(page_num=8)
        plan.items.append(
            render_plan.RenderItem(
                "translated_text",
                ["body"],
                (10.0, 10.0, 40.0, 21.0),
                text="一二三四五六七八九十甲乙丙丁",
                font_size=layout.BODY_FONT_SIZE,
                style_name="body",
            )
        )

        errors = layout.validate_plan_text_fit(plan, fitz=FakeFitz())

        self.assertTrue(errors)

    def test_expand_text_boxes_to_fit_grows_tight_body_line_into_available_space(self):
        fitz = FakeFitz()
        plan = render_plan.PageRenderPlan(page_num=2)
        plan.items.append(
            render_plan.RenderItem(
                "translated_text",
                ["p002b0004"],
                (70.0, 120.0, 520.0, 127.0),
                text="该名称源自 la tortuga，这是西班牙语中表示 turtle 的词。",
                font_size=layout.BODY_FONT_SIZE,
                style_name="body",
            )
        )
        plan.items.append(
            render_plan.RenderItem(
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

    def test_normalize_vector_text_layout_releases_toolformer_visual_overcapture(self):
        fitz = FakeFitz()
        plan = render_plan.PageRenderPlan(page_num=2)
        plan.components.append(
            ownership.PageComponent(
                "p002c0002",
                ownership.COMPONENT_KIND_VISUAL,
                ["p002b0005", "p002b0006", "p002b0007", "p002b0008"],
                (107.641, 152.980688, 505.242628, 226.300922),
                (106.14, 137.909818, 506.22, 250.98),
                ownership.CONFIDENCE_CONSERVATIVE,
                ["visual_region", "visual_region"],
                "original_image_clip",
            )
        )
        plan.items.append(
            render_plan.RenderItem(
                "original_image_clip",
                ["p002b0005", "p002b0006", "p002b0007", "p002b0008"],
                (106.14, 137.909818, 506.22, 250.98),
                fallback_reason="visual_region",
                component_id="p002c0002",
                component_kind=ownership.COMPONENT_KIND_VISUAL,
            )
        )
        plan.protected_boxes.append(plan.items[0].bbox)
        plan.items.append(
            render_plan.RenderItem(
                "translated_text",
                ["p002b0003"],
                (114.360956, 108.497817, 382.92205, 128.869817),
                text="在 1400 名参与者中，有 400 人（即 [Calculator(400 / 1400) → 0.29] 29%）通过了测试。",
                font_size=layout.BODY_FONT_SIZE,
                style_name="body",
            )
        )
        plan.items.append(
            render_plan.RenderItem(
                "translated_text",
                ["p002b0004"],
                (114.361822, 130.869817, 383.581771, 137.909818),
                text="该名称源自 “la tortuga”，这是西班牙语中表示 [MT(“tortuga”) → turtle] turtle 的词。",
                font_size=layout.BODY_FONT_SIZE,
                style_name="body",
            )
        )

        self.assertTrue(layout.validate_plan_text_fit(plan, fitz=fitz))

        pdf.normalize_vector_text_layout(plan, page_size=(612.0, 792.0), fitz=fitz)

        visual = next(item for item in plan.items if item.kind == "original_image_clip")
        body = next(item for item in plan.items if item.source_ids == ["p002b0004"])
        self.assertEqual(layout.validate_plan_text_fit(plan, fitz=fitz), [])
        self.assertGreaterEqual(visual.bbox[1], plan.components[0].source_bbox[1])
        self.assertLess(body.bbox[3], visual.bbox[1])
        self.assertEqual(plan.protected_boxes, [visual.bbox])

    def test_normalize_vector_text_layout_backfills_prose_before_visual_clip(self):
        fitz = render_pdf.load_fitz()
        plan = render_plan.PageRenderPlan(page_num=3)
        visual_bbox = (106.08, 100.375998, 505.92, 125.765999)
        plan.components.append(
            ownership.PageComponent(
                "p003c0001",
                ownership.COMPONENT_KIND_VISUAL,
                ["p003b0003", "p003b0004"],
                (112.718, 100.375998, 499.283, 125.765999),
                visual_bbox,
                ownership.CONFIDENCE_CONSERVATIVE,
                ["visual_region"],
                "original_image_clip",
            )
        )
        plan.items.append(
            render_plan.RenderItem(
                "translated_text",
                ["p003b0001"],
                (114.748011, 77.539998, 494.253315, 87.485998),
                text="你的任务是在一段文本中添加对问答 API 的调用。这些问题应帮助你获取所需的信息。",
                font_size=layout.BODY_FONT_SIZE,
                style_name="body",
            )
        )
        plan.items.append(
            render_plan.RenderItem(
                "translated_text",
                ["p003b0002"],
                (114.748011, 89.485998, 494.3392, 96.525998),
                text="以完成该文本。你可以通过写入“[QA(question)]”来调用该 API，其中“question”是你想提出的问题。以下是。",
                font_size=layout.BODY_FONT_SIZE,
                style_name="body",
            )
        )
        plan.items.append(
            render_plan.RenderItem(
                "original_image_clip",
                ["p003b0003", "p003b0004"],
                visual_bbox,
                fallback_reason="visual_region",
                component_id="p003c0001",
                component_kind=ownership.COMPONENT_KIND_VISUAL,
            )
        )
        plan.protected_boxes.append(visual_bbox)

        before = {
            tuple(item.source_ids): item.bbox
            for item in plan.items
            if item.kind == "translated_text"
        }
        self.assertIn("p003b0002", "\n".join(layout.validate_plan_text_fit(plan, fitz=fitz)))

        pdf.normalize_vector_text_layout(plan, page_size=(612.0, 792.0), fitz=fitz)

        visual = next(item for item in plan.items if item.kind == "original_image_clip")
        body = next(item for item in plan.items if item.source_ids == ["p003b0002"])
        self.assertEqual(layout.validate_plan_text_fit(plan, fitz=fitz), [])
        self.assertEqual(visual.bbox, visual_bbox)
        self.assertEqual(plan.protected_boxes, [visual_bbox])
        self.assertEqual(body.kind, "translated_text")
        self.assertLess(body.bbox[1], before[("p003b0002",)][1])
        self.assertLessEqual(body.bbox[3], visual.bbox[1] - layout.TEXT_PROTECTED_GAP_PT)


if __name__ == "__main__":
    unittest.main()
