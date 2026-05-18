import json
import unittest
from pathlib import Path
from unittest import mock

import translate_pdf_via_codex as pdf
from tests.pdf_render_fixture_runner import (
    assert_render_plan_fixture,
    iter_wait_free_render_plan_fixtures,
)


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
                        pdf.validate_plan_coverage(fixture.page_num, fixture.blocks, fixture.plan),
                        [],
                    )
                    self.assertEqual(
                        pdf.validate_plan_layout(fixture.plan, PAGE_SIZE),
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


if __name__ == "__main__":
    unittest.main()
