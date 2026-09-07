import copy
import unittest
from collections import Counter
from pathlib import Path

import translate_pdf_via_codex as pipeline
from tests.pdf_render_fixture_runner import assert_render_plan_fixture, iter_render_plan_fixtures


def visible_text(plan):
    return Counter(char for item in plan.items if item.kind == "translated_text"
                   for char in item.text if not char.isspace())


class LayoutStageTests(unittest.TestCase):
    def test_arrangement_preserves_text_and_coverage_across_columns_and_visual_barrier(self):
        plan = pipeline.PageRenderPlan(page_num=2, page_size=(420, 300))
        blocks = []
        for index, (box, text) in enumerate([
            ((20, 30, 180, 75), "左栏前段。"),
            ((230, 30, 390, 75), "右栏前段。"),
            ((20, 140, 180, 185), "左栏后段。"),
            ((230, 140, 390, 185), "右栏后段。"),
        ], 1):
            block_id = f"p002b{index:04d}"
            blocks.append({"id": block_id, "page": 2,
                           "text": "This is a complete source paragraph for the layout preservation test.",
                           "xMin": box[0], "yMin": box[1], "xMax": box[2], "yMax": box[3]})
            plan.items.append(pipeline.RenderItem("translated_text", [block_id], box, text=text,
                                                  font_size=pipeline.BODY_FONT_SIZE, style_name="body"))
            plan.ledger.append(pipeline.CoverageEntry(block_id, "body", "translated_text", True))
        protected = (10, 95, 400, 120)
        plan.items.append(pipeline.RenderItem("original_image_clip", [], protected))
        plan.protected_boxes.append(protected)
        before = visible_text(plan)
        pipeline.arrange_page_render_items(plan, blocks, plan.page_size, [])
        pipeline.normalize_vector_text_layout(plan, plan.page_size)
        self.assertEqual(visible_text(plan), before)
        self.assertEqual(pipeline.validate_plan_coverage(2, blocks, plan), [])
        self.assertEqual(pipeline.validate_plan_layout(plan, plan.page_size), [])
        self.assertEqual(pipeline.validate_plan_text_fit(plan), [])
        by_id = {source_id: item for item in plan.items for source_id in item.source_ids}
        self.assertEqual(set(by_id), {block["id"] for block in blocks})
        self.assertLess(by_id["p002b0001"].bbox[2], by_id["p002b0002"].bbox[0])
        self.assertLessEqual(by_id["p002b0001"].bbox[3], protected[1])
        self.assertGreaterEqual(by_id["p002b0003"].bbox[1], protected[3])

    def test_font_fit_stage_preserves_content_and_is_stable_on_committed_fixtures(self):
        root = Path(__file__).parent / "fixtures" / "pdf_render"
        for document in ("wait-free", "bert"):
            for fixture in iter_render_plan_fixtures(root, document):
                with self.subTest(document=document, page=fixture.page_num):
                    assert_render_plan_fixture(fixture)
                    plan = fixture.plan
                    original_text = visible_text(plan)
                    original_coverage = copy.deepcopy(plan.ledger)
                    pipeline.normalize_vector_text_layout(plan, plan.page_size)
                    once = pipeline.render_plan_json_dumps(plan)
                    pipeline.normalize_vector_text_layout(plan, plan.page_size)
                    self.assertEqual(pipeline.render_plan_json_dumps(plan), once)
                    self.assertEqual(visible_text(plan), original_text)
                    self.assertEqual([e.block_id for e in plan.ledger], [e.block_id for e in original_coverage])
                    self.assertEqual(pipeline.validate_plan_coverage(fixture.page_num, fixture.blocks, plan), [])


if __name__ == "__main__":
    unittest.main()
