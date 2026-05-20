import unittest
from unittest.mock import patch

import layout
import translate_pdf_via_codex as pdf


class FakeFitz:
    def get_text_length(self, text, fontname="helv", fontsize=10.0):
        return len(text) * fontsize * 0.5


class LayoutExtractionModuleTests(unittest.TestCase):
    def test_style_api_direct_and_compatibility_identity(self):
        self.assertIs(pdf.TextStyle, layout.TextStyle)
        self.assertIs(pdf.DOCUMENT_STYLES, layout.DOCUMENT_STYLES)
        self.assertIs(pdf.text_style, layout.text_style)
        self.assertEqual(layout.text_style("body").font_size, layout.BODY_FONT_SIZE)
        self.assertEqual(layout.text_style("missing").font_size, layout.BODY_FONT_SIZE)

    def test_fit_spacing_direct_api_matches_pipeline_compatibility(self):
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
        self.assertIs(pdf.fitted_text_spacing, layout.fitted_text_spacing)
        self.assertIs(pdf.text_box_fit_plan, layout.text_box_fit_plan)

    def test_text_item_fit_metrics_uses_shared_text_box_fit_plan(self):
        fitz = FakeFitz()
        item = pdf.RenderItem(
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
        item = pdf.RenderItem(
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
        self.assertIs(pdf.wrap_mixed_pdf_text, layout.wrap_mixed_pdf_text)

    def test_protected_region_split_direct_api_matches_pipeline_compatibility(self):
        protected = [pdf.RenderItem("original_image_clip", ["fig"], (100.0, 100.0, 180.0, 160.0))]

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
        self.assertIs(pdf.shifted_boxes_around_protected, layout.shifted_boxes_around_protected)
        self.assertIs(pdf.text_segments_around_protected, layout.text_segments_around_protected)
        self.assertIs(pdf.vertical_expansion_limits, layout.vertical_expansion_limits)

    def test_split_translated_text_around_protected_preserves_ledger_update(self):
        plan = pdf.PageRenderPlan(page_num=7)
        plan.items.append(pdf.RenderItem("original_image_clip", ["fig"], (100.0, 100.0, 180.0, 160.0)))
        plan.items.append(
            pdf.RenderItem(
                "translated_text",
                ["body"],
                (90.0, 90.0, 220.0, 180.0),
                text="第一句。第二句。",
                font_size=layout.BODY_FONT_SIZE,
                style_name="body",
            )
        )
        plan.ledger.append(pdf.CoverageEntry("body", "body", "translated_text", True, None))

        layout.split_translated_text_around_protected(plan, page_size=(300.0, 300.0), fitz=FakeFitz())

        body_items = [item for item in plan.items if item.kind == "translated_text"]
        self.assertTrue(body_items)
        self.assertTrue(all(item.fallback_reason == "split_around_visual" for item in body_items))
        self.assertEqual(plan.ledger[0].fallback_reason, "split_around_visual")
        self.assertIs(pdf.split_translated_text_around_protected, layout.split_translated_text_around_protected)

    def test_split_translated_text_around_protected_keeps_item_when_segments_get_no_text(self):
        plan = pdf.PageRenderPlan(page_num=7)
        plan.items.append(pdf.RenderItem("original_image_clip", ["fig"], (100.0, 100.0, 180.0, 160.0)))
        original = pdf.RenderItem(
            "translated_text",
            ["body"],
            (90.0, 90.0, 220.0, 180.0),
            text="第一句。第二句。",
            font_size=layout.BODY_FONT_SIZE,
            style_name="body",
        )
        plan.items.append(original)
        plan.ledger.append(pdf.CoverageEntry("body", "body", "translated_text", True, None))

        def empty_segments(_text, segments, _style=None, fitz=None):
            return ["" for _segment in segments]

        with patch.object(layout, "distribute_text_across_segments", side_effect=empty_segments):
            layout.split_translated_text_around_protected(plan, page_size=(300.0, 300.0), fitz=FakeFitz())

        self.assertIn(original, plan.items)
        self.assertEqual(plan.ledger[0].render_kind, "translated_text")
        self.assertIsNone(plan.ledger[0].fallback_reason)

    def test_text_fit_validation_rejects_unrecorded_font_shrinking(self):
        plan = pdf.PageRenderPlan(page_num=8)
        plan.items.append(
            pdf.RenderItem(
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


if __name__ == "__main__":
    unittest.main()
