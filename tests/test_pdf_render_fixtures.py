import unittest
from pathlib import Path
from unittest import mock

import render_plan
import translate_pdf_via_codex as pdf
from tests.pdf_render_fixture_runner import (
    assert_render_plan_fixture,
    iter_render_plan_fixtures,
    iter_wait_free_render_plan_fixtures,
)
from tests import pdf_render_fixture_runner


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "pdf_render"
PAGE_SIZE = (623, 801)


class PdfRenderFixtureRunnerTests(unittest.TestCase):
    def test_waitfree_fixtures_build_render_plans_without_codex(self):
        seen_pages = []

        with mock.patch.object(pdf, "translate_batches", side_effect=AssertionError("Codex translation was invoked")):
            for fixture in iter_wait_free_render_plan_fixtures(FIXTURE_ROOT, page_size=PAGE_SIZE):
                with self.subTest(page=fixture.page_num):
                    self.assertEqual(fixture.plan.page_num, fixture.page_num)
                    self.assertTrue(fixture.plan.items, "render plan has no items")
                    self.assertEqual(
                        render_plan.validate_plan_coverage(fixture.page_num, fixture.blocks, fixture.plan),
                        [],
                    )
                    self.assertEqual(
                        render_plan.validate_plan_layout(fixture.plan, PAGE_SIZE),
                        [],
                    )
                    self.assertTrue(fixture.expected_plan.get("assertions"))
                    self.assertEqual(fixture.expected_plan["page"], fixture.page_num)
                    seen_pages.append(fixture.page_num)

        self.assertEqual(seen_pages, [3, 15, 17, 25])

    def test_waitfree_expected_plan_assertions_are_enforced(self):
        with mock.patch.object(pdf, "translate_batches", side_effect=AssertionError("Codex translation was invoked")):
            for fixture in iter_wait_free_render_plan_fixtures(FIXTURE_ROOT, page_size=PAGE_SIZE):
                with self.subTest(page=fixture.page_num):
                    assert_render_plan_fixture(fixture)

    def test_bert_ownership_fixtures_are_enforced(self):
        with mock.patch.object(pdf, "translate_batches", side_effect=AssertionError("Codex translation was invoked")):
            fixtures = list(iter_render_plan_fixtures(FIXTURE_ROOT, "bert", page_size=PAGE_SIZE))
            self.assertEqual([fixture.page_num for fixture in fixtures], [9, 12, 15])
            for fixture in fixtures:
                with self.subTest(page=fixture.page_num):
                    assert_render_plan_fixture(fixture)

    def test_fixture_runner_rejects_extra_duplicate_split_ledger_entries(self):
        plan_json = {
            "components": [
                {
                    "component_id": "p017c0001",
                    "component_kind": "visual",
                    "parent_component_id": "",
                    "reason_codes": ["mixed_visual_body_split", "visual_region"],
                    "source_ids": ["p017b0002"],
                },
                {
                    "component_id": "p017c0002",
                    "component_kind": "translated_text",
                    "parent_component_id": "p017c0001",
                    "reason_codes": ["body", "mixed_visual_body_split"],
                    "source_ids": ["p017b0002"],
                },
            ],
            "coverage_ledger": [
                {
                    "block_id": "p017b0002",
                    "render_kind": "original_image_clip",
                    "component_id": "p017c0001",
                    "component_kind": "visual",
                },
                {
                    "block_id": "p017b0002",
                    "render_kind": "translated_text",
                    "component_id": "p017c0002",
                    "component_kind": "translated_text",
                },
                {
                    "block_id": "p017b0002",
                    "render_kind": "translated_text",
                    "component_id": "p017c0002",
                    "component_kind": "translated_text",
                },
            ],
        }

        self.assertFalse(pdf_render_fixture_runner._source_has_valid_split_ledger(plan_json, "p017b0002"))


if __name__ == "__main__":
    unittest.main()
