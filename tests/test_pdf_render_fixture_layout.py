import json
import unittest
from pathlib import Path


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "pdf_render"
REQUIRED_SUBDIRECTORIES = (
    "source_pages",
    "translations",
    "expected_plans",
    "expected_qa",
    "rendered_png",
)
REQUIRED_WAIT_FREE_CATEGORIES = {
    "headings",
    "formulas",
    "figures",
    "references",
    "running_headers",
    "body_flow_spacing",
}


def _json_files(fixture_type):
    return sorted((FIXTURE_ROOT / fixture_type / "wait-free").glob("page-*.json"))


class PdfRenderFixtureLayoutTests(unittest.TestCase):
    def test_fixture_root_contains_required_tracked_subdirectories(self):
        missing_dirs = [
            name
            for name in REQUIRED_SUBDIRECTORIES
            if not (FIXTURE_ROOT / name).is_dir()
        ]
        self.assertEqual(missing_dirs, [])

        missing_placeholders = [
            name
            for name in REQUIRED_SUBDIRECTORIES
            if not (FIXTURE_ROOT / name / ".gitkeep").is_file()
        ]
        self.assertEqual(missing_placeholders, [])

    def test_readme_documents_page_level_fixture_convention(self):
        readme_path = FIXTURE_ROOT / "README.md"
        self.assertTrue(readme_path.is_file(), f"missing {readme_path}")

        readme = readme_path.read_text(encoding="utf-8")
        for expected_text in (
            "source_pages",
            "translations",
            "expected_plans",
            "expected_qa",
            "rendered_png",
            "page-001",
        ):
            self.assertIn(expected_text, readme)

    def test_readme_documents_user_reported_defect_fixture_workflow(self):
        readme_path = FIXTURE_ROOT / "README.md"
        self.assertTrue(readme_path.is_file(), f"missing {readme_path}")

        readme = readme_path.read_text(encoding="utf-8")
        for expected_text in (
            "User-Reported Visual Defect Workflow",
            "page number",
            "source PDF",
            "translated PDF",
            "render-plan artifact",
            "smallest page set",
            "failing fixture",
            "expected_plans",
            "expected_qa",
            "tests.test_pdf_render_fixtures",
        ):
            self.assertIn(expected_text, readme)

    def test_waitfree_expected_plan_fixtures_cover_required_categories(self):
        plan_files = _json_files("expected_plans")
        self.assertTrue(plan_files, "missing Wait-free expected plan fixture JSON files")

        covered_categories = set()
        fixture_pages = set()
        for path in plan_files:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["document"], "wait-free-synchronization")
            self.assertEqual(path.name, f"page-{data['page']:03d}.json")
            self.assertTrue(data.get("assertions"), f"{path} has no plan assertions")
            assertion_categories = {
                assertion["category"] for assertion in data["assertions"]
            }
            self.assertEqual(set(data.get("categories", [])), assertion_categories)
            covered_categories.update(assertion_categories)
            fixture_pages.add(data["page"])

        self.assertEqual(REQUIRED_WAIT_FREE_CATEGORIES - covered_categories, set())

        for fixture_type in ("source_pages", "translations", "expected_qa"):
            companion_pages = set()
            for path in _json_files(fixture_type):
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(data["document"], "wait-free-synchronization")
                self.assertEqual(path.name, f"page-{data['page']:03d}.json")
                if fixture_type == "source_pages":
                    self.assertTrue(data.get("blocks"), f"{path} has no source blocks")
                elif fixture_type == "translations":
                    self.assertTrue(data.get("translations"), f"{path} has no translations")
                else:
                    self.assertTrue(data.get("assertions"), f"{path} has no QA assertions")
                companion_pages.add(data["page"])
            self.assertEqual(
                companion_pages,
                fixture_pages,
                f"{fixture_type} fixtures do not match expected plan pages",
            )


if __name__ == "__main__":
    unittest.main()
