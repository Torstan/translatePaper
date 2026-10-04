import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import qa_visual
import translate_pdf_parallel as parallel
import render_pdf
import render_plan
import pipeline
import ownership


class FinalRenderPlanTests(unittest.TestCase):
    def make_page(self, root):
        source = root / "source.pdf"
        with pipeline.load_fitz().open() as doc:
            page = doc.new_page(width=200, height=220)
            page.draw_rect((20, 120, 180, 160), fill=(0, 0, 0))
            doc.save(source)
        block = {"id": "p001b0001", "page": 1, "block_index": 1,
                 "text": "A complete source paragraph for validation.",
                 "xMin": 20, "yMin": 30, "xMax": 180, "yMax": 80}
        translations = {block["id"]: "完整的正文。"}
        plan = pipeline.build_page_render_plan(1, [block], translations, (200, 220))
        return source, block, translations, plan

    def test_final_plan_entry_adapts_text_and_saves_checked_page_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, block, _, _ = self.make_page(root)
            block["yMax"] = 38
            translations = {block["id"]: "这段正文需要扩展文本框才能保持统一字号。" * 4}
            self.assertTrue(callable(getattr(pipeline, "build_final_page_plan", None)),
                            "final plan generation needs one complete entry point")
            with patch("subprocess.run", side_effect=AssertionError("planning must not invoke a model")):
                plan = pipeline.build_final_page_plan(
                    1, [block], translations, (200, 220), output_page_num=3,
                    job_paths={"plans_dir": root / "plans"},
                )
            self.assertTrue(plan.items)
            self.assertGreater(plan.items[0].bbox[3], 38)
            self.assertEqual(plan.items[0].font_size, 9.2)
            self.assertEqual(pipeline.validate_plan_text_fit(plan, pipeline.load_fitz()), [])
            artifact = json.loads((root / "plans/page-001.render-plan.json").read_text())
            self.assertEqual((artifact["page_num"], artifact["output_page_num"], artifact["page_size"]),
                             (1, 3, [200.0, 220.0]))
            self.assertEqual(artifact["render_items"], render_plan.render_plan_to_json(plan)["render_items"])
            self.assertEqual(artifact["validation_results"], {
                "ownership": {"ok": True, "issues": []}, "coverage_errors": [],
                "layout_errors": [], "text_overlap_errors": [], "style_policy_errors": [],
                "text_fit_errors": [],
            })

    def test_final_plan_entry_rejects_invalid_ownership_and_saves_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, block, translations, plan = self.make_page(root)
            plan.components.clear()
            self.assertTrue(callable(getattr(pipeline, "build_final_page_plan", None)),
                            "final plan generation must reject invalid ownership itself")
            with patch.object(pipeline, "build_page_render_plan", return_value=plan):
                with self.assertRaisesRegex(RuntimeError, "owner"):
                    pipeline.build_final_page_plan(
                        1, [block], translations, (200, 220),
                        job_paths={"plans_dir": root / "plans"},
                    )
            artifact = json.loads((root / "plans/page-001.render-plan.json").read_text())
            self.assertEqual([i["issue_code"] for i in artifact["ownership_validation"]["issues"]],
                             ["missing_owner"])
            self.assertEqual(artifact["ownership_validation"], artifact["validation_results"]["ownership"])

    def test_invalid_ownership_blocks_drawing_without_optional_qa(self):
        for defect in ("missing_owner", "duplicate_owner"):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source, block, translations, plan = self.make_page(root)
                if defect == "missing_owner":
                    plan.components.clear()
                else:
                    plan.components.append(replace(plan.components[0], component_id="duplicate"))
                output = root / "output.pdf"
                previous = source.read_bytes()
                output.write_bytes(previous)
                fitz = pipeline.load_fitz()
                opened = []
                real_open = fitz.open

                def track_open(*args, **kwargs):
                    doc = real_open(*args, **kwargs)
                    opened.append(doc)
                    return doc

                with (
                    patch.object(pipeline, "build_page_render_plan", return_value=plan),
                    patch.object(pipeline, "preserve_images_on_page") as images,
                    patch.object(pipeline, "render_plan_item") as draw,
                    patch.object(fitz, "open", side_effect=track_open),
                ):
                    with self.assertRaisesRegex(RuntimeError, "owner"):
                        pipeline.write_vector_pdf(source, output, [(1, [block])], translations, 72,
                                                  {"plans_dir": root / "plans"})
                images.assert_not_called()
                draw.assert_not_called()
                self.assertTrue(opened and all(doc.is_closed for doc in opened))
                self.assertEqual(output.read_bytes(), previous)
                artifact = json.loads((root / "plans/page-001.render-plan.json").read_text())
                self.assertEqual([i["issue_code"] for i in artifact["ownership_validation"]["issues"]], [defect])
                self.assertEqual(artifact["ownership_validation"], artifact["validation_results"]["ownership"])

    def test_final_layout_replaces_stale_layer_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, block, translations, plan = self.make_page(root)
            visual = ownership.PageComponent("figure", "visual", [], (20, 120, 180, 160),
                                             (20, 120, 180, 160), "deterministic")
            plan.components.append(visual)
            clip = render_plan.RenderItem("original_image_clip", [], (20, 30, 180, 160),
                                          component_id="figure", component_kind="visual")
            plan.items.append(clip)
            plan.ownership_validation = ownership.validate_render_layer_exclusivity(plan, plan.components)
            self.assertTrue(plan.ownership_validation.issues)

            def finish_layout(*args, **kwargs):
                clip.bbox = visual.clip_bbox

            output = root / "output.pdf"
            with (
                patch.object(pipeline, "build_page_render_plan", return_value=plan),
                patch.object(pipeline, "normalize_vector_text_layout", side_effect=finish_layout),
            ):
                result = pipeline.write_vector_pdf(source, output, [(1, [block])], translations, 72,
                                                   {"plans_dir": root / "plans"})
            self.assertEqual(result.plans[0].ownership_validation.issues, [])
            artifact = json.loads((root / "plans/page-001.render-plan.json").read_text())
            self.assertEqual(artifact["ownership_validation"], {"ok": True, "issues": []})
            self.assertEqual(qa_visual.detect_ownership_issues(artifact), [])
            self.assertEqual(pipeline.validate_document_quality([(1, [block])], translations, result.plans), [])

    def test_final_text_overlap_is_rejected_before_drawing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, block, translations, plan = self.make_page(root)
            second = dict(block, id="p001b0002", text="Another complete source paragraph.")
            plan.components.append(replace(plan.components[0], component_id="second", source_ids=[second["id"]]))
            plan.items.append(replace(plan.items[0], source_ids=[second["id"]], text="另一段正文。"))
            plan.ledger.append(replace(plan.ledger[0], block_id=second["id"]))
            with (
                patch.object(pipeline, "build_page_render_plan", return_value=plan),
                patch.object(pipeline, "normalize_vector_text_layout"),
                patch.object(pipeline, "render_plan_item") as draw,
            ):
                with self.assertRaisesRegex(RuntimeError, "overlaps text"):
                    pipeline.write_vector_pdf(source, root / "output.pdf", [(1, [block, second])], translations, 72,
                                              {"plans_dir": root / "plans"})
            draw.assert_not_called()
            artifact = json.loads((root / "plans/page-001.render-plan.json").read_text())
            self.assertTrue(artifact["validation_results"]["text_overlap_errors"])

    def test_ownership_warning_is_preserved_without_blocking_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, block, translations, plan = self.make_page(root)
            visual = ownership.PageComponent("figure", "visual", [], (20, 120, 180, 160),
                                             (20, 125, 180, 160), "conservative")
            plan.components.append(visual)
            plan.items.append(render_plan.RenderItem("original_image_clip", [], visual.clip_bbox,
                                                     component_id="figure", component_kind="visual"))
            with patch.object(pipeline, "build_page_render_plan", return_value=plan):
                result = pipeline.write_vector_pdf(source, root / "output.pdf", [(1, [block])], translations, 72,
                                                   {"plans_dir": root / "plans"})
            self.assertTrue((root / "output.pdf").exists())
            expected = [("visual_clip_undercaptures_source", "warning")]
            self.assertEqual([(i.issue_code, i.severity) for i in result.plans[0].ownership_validation.issues], expected)
            artifact = json.loads((root / "plans/page-001.render-plan.json").read_text())
            for representation in (result.plans[0], artifact):
                issues = qa_visual.detect_ownership_issues(representation)
                self.assertEqual([i.severity for i in issues], ["warning"])

    def test_translation_entry_repairs_cached_response_once_and_rendering_keeps_final_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.pdf"
            with pipeline.load_fitz().open() as doc:
                doc.new_page(width=623, height=801)
                doc.new_page(width=623, height=801)
                doc.save(source)
            selected = [(page, [{"id": f"p{page:03d}b0001", "page": page, "block_index": 1,
                                 "text": text, "xMin": 130, "yMin": y, "xMax": 486, "yMax": y + 40}])
                        for page, text, y in [(1, "The history appears sequential to each process, and", 590),
                                              (2, "of operations. Equivalently, each operation appears instantaneously.", 50)]]
            job = {"job_dir": root, "plans_dir": root / "plans", "boundary_repairs_path": root / "repairs.json",
                   "translations_path": root / "translations.json", "schema_path": root / "schema.json"}
            job["boundary_repairs_path"].write_text(json.dumps({"p001b0001->p002b0001": {
                "translation": "完整句子。", "next_prefix_translation": "的操作。"}}))
            translations = {"p001b0001": "未完成，并且", "p002b0001": "的操作。的操作。后续句子。"}
            job["translations_path"].write_text(json.dumps(translations))
            cached_response = job["translations_path"].read_bytes()
            self.assertTrue(callable(getattr(pipeline, "translate_pages", None)),
                            "translation must own cache recovery and boundary repair")
            with patch("subprocess.run", side_effect=AssertionError("cached repair must not invoke Codex")):
                final_translations, translated_blocks = pipeline.translate_pages(
                    selected, (623, 801), job, pipeline.DocumentOptions(model="test"),
                )
                result = pipeline.render_translated_pdf(
                    source, root / "output.pdf", selected, final_translations, (623, 801), 72, job,
                    render_mode="vector",
                )
                repeated = pipeline.render_translated_pdf(
                    source, root / "repeated.pdf", selected, final_translations, (623, 801), 72, job,
                    render_mode="vector",
                )
            self.assertEqual(translated_blocks, 2)
            self.assertEqual(job["translations_path"].read_bytes(), cached_response)
            self.assertEqual(result.translations["p002b0001"], "的操作。后续句子。")
            self.assertEqual(repeated.translations["p002b0001"], "的操作。后续句子。")
            self.assertTrue(any("的操作。后续句子。" in item.text for item in result.plans[1].items))
            self.assertEqual(translations["p002b0001"], "的操作。的操作。后续句子。")

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
            result = pipeline.write_vector_pdf(source, output, selected, translations, 72, job)
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
                patch.object(pipeline.qa, "choose_items_for_qa", return_value=[]),
            ):
                report = pipeline.run_qa_for_job(selected, translations, job, (200, 300), args,
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
