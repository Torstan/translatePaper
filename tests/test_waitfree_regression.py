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
            reference_items = [
                item
                for item in plan.items
                if item.kind == "original_selectable_text" and item.fallback_reason == "reference_original"
            ]
            combined = "\n".join(item.text for item in reference_items)

            self.assertNotIn("载于", combined)
            self.assertRegex(combined, r"(REFERENCES|LAMPORT|ANDERSON|HERLIHY)")


if __name__ == "__main__":
    unittest.main()
