import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import qa_visual
import translate_pdf_parallel as parallel
import render_pdf
import render_plan
import translate_pdf_via_codex as pipeline


class FinalRenderPlanTests(unittest.TestCase):
    def test_subset_qa_checks_the_drawn_plan_with_each_pages_actual_dimensions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.pdf"
            output = root / "output.pdf"
            fitz = render_pdf.load_fitz()
            with fitz.open() as doc:
                for width in (200, 420, 300):
                    doc.new_page(width=width, height=300)
                doc.save(source)
            block = {
                "id": "p002b0001", "page": 2, "block_index": 1,
                "text": "This paragraph verifies the actual width of the translated page.",
                "xMin": 240, "yMin": 70, "xMax": 390, "yMax": 150,
            }
            selected = [(2, [block]), (3, [])]
            translations = {block["id"]: "这一段正文用于验证译文页面的实际宽度。"}
            job = {"job_dir": root, "plans_dir": root / "plans"}
            result = pipeline.write_vector_pdf(source, output, selected, translations, (200, 300), 72, job)
            self.assertIsNotNone(result, "drawing must return its actual final plans")
            self.assertEqual([p.page_size for p in result.plans], [(420, 300), (300, 300)])
            self.assertEqual([p.output_page_num for p in result.plans], [1, 2])
            before = [render_plan.render_plan_json_dumps(p) for p in result.plans]
            args = SimpleNamespace(render_mode="vector", strict_qa=True, qa_mode="sample",
                                   qa_sample_size=0, qa_batch_chars=7000, model="test", reasoning_effort="low", retries=1)
            with (
                patch.object(pipeline, "build_page_render_plan", side_effect=AssertionError("QA rebuilt a plan")),
                patch.object(pipeline, "normalize_vector_text_layout", side_effect=AssertionError("QA rewrote a plan")),
                patch.object(qa_visual, "_load_plan_artifact", side_effect=AssertionError("QA reloaded a plan")),
                patch.object(parallel.qa, "choose_items_for_qa", return_value=[]),
            ):
                report = parallel.run_qa_for_job(selected, translations, job, (200, 300), args,
                                                 output_pdf_path=output, render_result=result)
            self.assertEqual([render_plan.render_plan_json_dumps(p) for p in result.plans], before)
            payload = json.loads(Path(report["visual_report_json"]).read_text())
            self.assertEqual(payload["checked_pages"], [2, 3])
            self.assertEqual(set(payload["rendered_png_paths"]), {"2", "3"})
            self.assertEqual(payload["error_count"], 0)
            self.assertEqual(report["deterministic_issue_count"], 0)

    def test_visual_qa_does_not_silently_skip_missing_output_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "one.pdf"
            with render_pdf.load_fitz().open() as doc:
                doc.new_page()
                doc.save(output)
            plan = root / "page-005.render-plan.json"
            plan.write_text(json.dumps({"page_num": 5, "output_page_num": 2, "render_items": []}))
            with self.assertRaisesRegex(ValueError, "output page"):
                qa_visual.generate_visual_qa_report([plan], output_dir=root / "qa", translated_pdf_path=output)


if __name__ == "__main__":
    unittest.main()
