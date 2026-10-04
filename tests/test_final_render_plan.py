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
import translation_batch
from PIL import Image


class FinalRenderPlanTests(unittest.TestCase):
    def test_translation_exit_finalizes_text_and_rendering_consumes_it_without_cleanup(self):
        block = {"id": "b", "page": 2, "block_index": 1,
                 "text": "Concurrent Data Structures\nThe algorithm protects every operation.",
                 "running_header": "Concurrent Data Structures",
                 "xMin": 20, "yMin": 50, "xMax": 180, "yMax": 110}
        raw = {"b": "Concurrent Data Structures\n算法保护每一个操作，\n并保证所有线程都能够继续运行。"}
        expected = {"b": "算法保护每一个操作，并保证所有线程都能够继续运行。"}
        with tempfile.TemporaryDirectory() as tmp:
            job = {"job_dir": Path(tmp)}
            with patch.object(translation_batch, "run_batches", return_value=raw):
                final, _ = pipeline.translate_pages([(2, [block])], (200, 220), job,
                                                     pipeline.DocumentOptions())
            self.assertEqual(final, expected)
            self.assertIn("Concurrent Data Structures", raw["b"])
            with (patch.object(pipeline, "clean_render_text", side_effect=AssertionError("render cleaned translation")),
                  patch.object(pipeline, "prepare_render_translation", side_effect=AssertionError("render normalized translation"))):
                plan = pipeline.build_final_page_plan(2, [block], final, (200, 220))
            self.assertEqual([item.text for item in plan.items if item.kind == "translated_text"], list(expected.values()))

    def test_final_validation_rejects_same_source_duplicate_but_allows_disjoint_fragments(self):
        block = {"id": "body", "text": "A complete source paragraph about the design.",
                 "xMin": 20, "yMin": 20, "xMax": 180, "yMax": 80}
        text = "首先读取文件。然后处理数据。"
        item = render_plan.RenderItem("translated_text", ["body"], (20, 20, 180, 60),
                                      text=text, font_size=9.2, style_name="body")
        plan = render_plan.PageRenderPlan(
            1, items=[item, replace(item)], page_size=(200, 220),
            coverage=[render_plan.CoverageSource("body", "body")],
            components=ownership.build_page_components(1, [block], {"body": "body"}, visual_regions=[]),
        )
        checks, errors = pipeline.validate_final_page_plan(plan, [block], pipeline.load_fitz(), {"body": text})
        self.assertTrue(checks["text_overlap_errors"], errors)
        plan.items = [replace(item, bbox=(20, 20, 180, 40), text="首先读取文件。"),
                      replace(item, bbox=(20, 45, 180, 65), text="然后处理数据。")]
        _, errors = pipeline.validate_final_page_plan(plan, [block], pipeline.load_fitz(), {"body": text})
        self.assertEqual(errors, [])
        self.assertEqual(qa_visual.detect_geometry_issues(plan, plan.page_size), [])

    def test_body_flow_checks_source_order_after_merge_and_split(self):
        blocks = [{"id": key, "text": "A complete source paragraph."} for key in ("a", "b")]
        translations = {"a": "首先读取文件。", "b": "然后处理数据。"}
        item = render_plan.RenderItem("translated_text", ["a", "b"], (20, 20, 180, 80),
                                      text="然后处理数据。首先读取文件。", layout_role="body_flow")
        plan = render_plan.PageRenderPlan(1, items=[item], coverage=[
            render_plan.CoverageSource(key, "body") for key in translations])
        self.assertTrue(pipeline.validate_plan_text_content(1, blocks, translations, plan))
        plan.items = [replace(item, bbox=(20, 20, 180, 40), text="首先读取文件。"),
                      replace(item, bbox=(20, 50, 180, 70), text="然后处理数据。")]
        self.assertEqual(pipeline.validate_plan_text_content(1, blocks, translations, plan), [])
        translations["b"] = translations["a"]
        plan.items[1].text = translations["b"]
        self.assertEqual(pipeline.validate_plan_text_content(1, blocks, translations, plan), [])
        plan.items.pop()
        self.assertTrue(pipeline.validate_plan_text_content(1, blocks, translations, plan))

    def test_source_ownership_is_independent_of_translation_state(self):
        blocks = [
            {"id": "p001b0001", "page": 1, "block_index": 1, "text": "Table 1: Results",
             "xMin": 10, "yMin": 10, "xMax": 100, "yMax": 90},
            {"id": "p001b0002", "page": 1, "block_index": 2,
             "text": "The experiment compares the two methods in detail.",
             "xMin": 20, "yMin": 40, "xMax": 90, "yMax": 60},
        ]
        regions = [{"source_ids": ["p001b0001"], "bbox": (10, 10, 100, 90)}]
        classes = {"p001b0001": "figure_region", "p001b0002": "body"}
        with (
            patch.object(pipeline, "build_visual_regions", return_value=regions),
            patch.object(pipeline, "classify_blocks", return_value=classes),
        ):
            missing = pipeline.build_page_render_plan(1, blocks, {}, (200, 220))
            translated = pipeline.build_page_render_plan(
                1, blocks, {"p001b0002": "实验详细比较了这两种方法。"}, (200, 220),
            )
        self.assertEqual(ownership.components_to_json(missing.components),
                         ownership.components_to_json(translated.components))

    def test_continuation_then_merge_keeps_each_source_once_in_final_flow(self):
        blocks = [
            dict(id="a", page=2, text="(1) First read the", xMin=20, yMin=20, xMax=180, yMax=60),
            dict(id="b", page=2, text="complete files.\n(2) Then process the data.",
                 xMin=20, yMin=65, xMax=180, yMax=115),
        ]
        translations = {"a": "(1) 首先读取", "b": "全部文件。\n(2) 然后处理数据。"}
        plan = pipeline.build_final_page_plan(2, blocks, translations, (200, 240))
        flows = [item for item in plan.items if item.kind == "translated_text"]
        self.assertEqual(len(flows), 1)
        self.assertEqual(flows[0].source_ids, ["a", "b"])
        self.assertEqual("".join(flows[0].text.split()), "(1)首先读取全部文件。(2)然后处理数据。")

    def test_partial_source_remerged_below_visual_barrier_does_not_require_prefix_twice(self):
        translations = {"a": "首先读取文件。随后校验输入。", "b": "最后处理数据。"}
        plan = render_plan.PageRenderPlan(2, items=[
            render_plan.RenderItem("translated_text", ["a"], (20, 20, 180, 40),
                                   text="首先读取文件。", style_name="body", font_size=9.2),
            render_plan.RenderItem("original_image_clip", [], (20, 45, 180, 75)),
            render_plan.RenderItem("translated_text", ["a"], (20, 80, 180, 100),
                                   text="随后校验输入。", style_name="body", font_size=9.2),
            render_plan.RenderItem("translated_text", ["b"], (20, 105, 180, 130),
                                   text="最后处理数据。", style_name="body", font_size=9.2),
        ], coverage=[render_plan.CoverageSource(key, "body") for key in translations])
        blocks = [{"id": key} for key in translations]
        pipeline.merge_adjacent_body_text_flows(plan)
        self.assertEqual(plan.items[-1].source_ids, ["a", "b"])
        self.assertEqual(pipeline.validate_plan_text_content(2, blocks, translations, plan), [])
        plan.items[0].text = "读取文件。"
        self.assertTrue(pipeline.validate_plan_text_content(2, blocks, translations, plan))

    def test_batching_and_rendering_consume_the_same_source_analysis(self):
        blocks = [{"id": "p001b0001", "page": 1, "block_index": 1,
                   "text": "The experiment compares the two methods in detail.",
                   "xMin": 20, "yMin": 40, "xMax": 180, "yMax": 60}]
        analysis = pipeline.build_translation_page_components(1, blocks, page_size=(200, 220))
        with patch.object(pipeline, "build_translation_page_components",
                          side_effect=AssertionError("source analysis must be reused")):
            batches = pipeline.build_translation_batches(
                [(1, blocks)], 7000, page_size=(200, 220), source_analysis_by_page={1: analysis},
            )
            plan = pipeline.build_page_render_plan(
                1, blocks, {"p001b0001": "实验详细比较了这两种方法。"},
                (200, 220), source_analysis=analysis,
            )
        self.assertEqual([item["id"] for batch in batches for item in batch.items], ["p001b0001"])
        self.assertEqual(ownership.components_to_json(plan.components),
                         ownership.components_to_json(analysis.components))

    def test_source_analysis_uses_each_selected_pages_dimensions(self):
        sizes = []
        original = pipeline.build_translation_page_components

        def record(page_num, blocks, **kwargs):
            sizes.append((page_num, kwargs["page_size"]))
            return original(page_num, blocks, **kwargs)

        with patch.object(pipeline, "build_translation_page_components", side_effect=record):
            pipeline.analyze_selected_pages([(2, []), (3, [])], (200, 300),
                                            page_sizes_by_page={2: (420, 300), 3: (300, 400)})
        self.assertEqual(sizes, [(2, (420, 300)), (3, (300, 400))])

    def test_coverage_rejects_ledger_without_a_matching_draw_item(self):
        block = {"id": "p001b0001", "text": "A complete source paragraph."}
        plan = render_plan.PageRenderPlan(
            page_num=1,
            coverage=[render_plan.CoverageSource("p001b0001", "body")],
        )
        self.assertIn("p001b0001", "\n".join(render_plan.validate_plan_coverage(1, [block], plan)))

    def test_coverage_rejects_visual_ledger_for_a_missing_clip_fragment(self):
        blocks = [{"id": "p001b0001", "text": "Figure part one."},
                  {"id": "p001b0002", "text": "Figure part two."}]
        plan = render_plan.PageRenderPlan(
            page_num=1,
            items=[render_plan.RenderItem("original_image_clip", ["p001b0001"], (0, 0, 100, 100))],
            coverage=[render_plan.CoverageSource(block["id"], "figure_region")
                    for block in blocks],
        )
        self.assertIn("p001b0002", "\n".join(render_plan.validate_plan_coverage(1, blocks, plan)))

    def test_visual_component_requires_clip_when_item_changes_to_text(self):
        block = {"id": "figure", "text": "Figure 1. Measurements and results.",
                 "xMin": 20, "yMin": 20, "xMax": 180, "yMax": 80}
        component = ownership.PageComponent(
            "visual", ownership.COMPONENT_KIND_VISUAL, ["figure"], (20, 20, 180, 80),
            (20, 20, 180, 80), ownership.CONFIDENCE_CONSERVATIVE,
        )
        plan = render_plan.PageRenderPlan(
            1, page_size=(200, 200), components=[component],
            coverage=[render_plan.CoverageSource(
                "figure", "figure_region", component_id="visual", component_kind=ownership.COMPONENT_KIND_VISUAL,
            )],
            items=[render_plan.RenderItem(
                "original_image_clip", ["figure"], (20, 20, 180, 80),
                component_id="visual", component_kind=ownership.COMPONENT_KIND_VISUAL,
            )],
        )
        _, errors = pipeline.validate_final_page_plan(plan, [block], pipeline.load_fitz(), {})
        self.assertEqual(errors, [])
        plan.items[0] = replace(plan.items[0], kind="translated_text", text="图示结果",
                                font_size=9.2, style_name="body")
        checks, errors = pipeline.validate_final_page_plan(plan, [block], pipeline.load_fitz(), {})
        self.assertTrue(checks["coverage_errors"], errors)
        self.assertTrue(errors)

    def test_visual_component_requires_clip_for_trivial_ocr_text(self):
        plan = render_plan.PageRenderPlan(
            1,
            coverage=[render_plan.CoverageSource(
                "figure", "figure_region", component_id="visual", component_kind=ownership.COMPONENT_KIND_VISUAL,
            )],
            items=[render_plan.RenderItem(
                "translated_text", ["figure"], (20, 20, 180, 80), text="一",
                component_id="visual", component_kind=ownership.COMPONENT_KIND_VISUAL,
            )],
        )
        errors = render_plan.validate_plan_coverage(1, [{"id": "figure", "text": "1"}], plan)
        self.assertTrue(errors)

    def test_coverage_rejects_clip_that_does_not_cover_its_source(self):
        block = {"id": "p001b0001", "text": "Figure content.",
                 "xMin": 50, "yMin": 50, "xMax": 100, "yMax": 100}
        for coordinate_space, clip, raster_size in (
            ("points", (120, 120, 170, 170), None),
            ("pixels", (240, 240, 340, 340), (400, 400)),
        ):
            with self.subTest(coordinate_space=coordinate_space):
                plan = render_plan.PageRenderPlan(
                    page_num=1, page_size=(200, 200), coordinate_space=coordinate_space,
                    raster_size=raster_size,
                    items=[render_plan.RenderItem("original_image_clip", [block["id"]], clip)],
                    coverage=[render_plan.CoverageSource(block["id"], "figure_region")],
                )
                self.assertTrue(any("outside" in issue for issue in
                                    render_plan.validate_plan_coverage(1, [block], plan)))

    def test_translation_quality_rejects_missing_tail_and_reordered_fragments(self):
        block = {"id": "p001b0001", "text": "A detailed explanation of the complete process."}
        full_text = "这是完整译文的前半部分以及必须保留的后半部分。"
        plan = render_plan.PageRenderPlan(
            page_num=1,
            coverage=[render_plan.CoverageSource(block["id"], "body")],
        )
        for fragments in (
            [full_text[:12]],
            [full_text[12:], full_text[:12]],
        ):
            with self.subTest(fragments=fragments):
                plan.items = [render_plan.RenderItem("translated_text", [block["id"]],
                                                     (0, index * 20, 100, (index + 1) * 20), text=part)
                              for index, part in enumerate(fragments)]
                issues = pipeline.validate_plan_translation_quality(
                    1, [block], {block["id"]: full_text}, plan,
                )
                self.assertTrue(any("content" in issue for issue in issues), issues)

    def test_final_plan_blocks_dropped_translation_content_before_drawing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, block, _, plan = self.make_page(root)
            full_text = "这是完整译文的前半部分以及必须保留的后半部分。"
            plan.items[0].text = full_text[:12]
            with (patch.object(pipeline, "build_page_render_plan", return_value=plan),
                  patch.object(pipeline, "normalize_vector_text_layout")):
                with self.assertRaisesRegex(RuntimeError, "translated content"):
                    pipeline.build_final_page_plan(1, [block], {block["id"]: full_text},
                                                   (200, 220), job_paths={"plans_dir": root / "plans"})
            artifact = json.loads((root / "plans/page-001.render-plan.json").read_text())
            self.assertTrue(artifact["validation_results"]["text_content_errors"])

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
                "text_content_errors": [],
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
                    patch.object(pipeline, "render_plan_item") as draw,
                    patch.object(fitz, "open", side_effect=track_open),
                ):
                    with self.assertRaisesRegex(RuntimeError, "owner"):
                        pipeline.write_vector_pdf(source, output, [(1, [block])], translations, 72,
                                                  {"plans_dir": root / "plans"})
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
            self.assertEqual(qa_visual.detect_ownership_issues(render_plan.render_plan_from_json(artifact)), [])
            self.assertEqual(pipeline.validate_document_quality([(1, [block])], translations, result.plans), [])

    def test_final_text_overlap_is_rejected_before_drawing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, block, translations, plan = self.make_page(root)
            second = dict(block, id="p001b0002", text="Another complete source paragraph.")
            plan.components.append(replace(plan.components[0], component_id="second", source_ids=[second["id"]]))
            plan.items.append(replace(plan.items[0], source_ids=[second["id"]], text="另一段正文。"))
            plan.coverage.append(replace(plan.coverage[0], block_id=second["id"]))
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
            for representation in (result.plans[0], render_plan.render_plan_from_json(artifact)):
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
            translations = {"p001b0001": "未完成，并且", "p002b0001": "的操作。的操作。后续句子。"}
            candidates = pipeline.detect_cross_page_sentence_splits(selected, bbox_lines_by_page={})
            job["boundary_repairs_path"].write_text(json.dumps({
                "request_key": pipeline.boundary_repair_request_key(candidates, translations, "test", "low"),
                "repairs": {"p001b0001->p002b0001": {
                    "translation": "完整句子。", "next_prefix_translation": "的操作。"}},
            }))
            job["translations_path"].write_text(json.dumps(translations))
            batches = pipeline.build_translation_batches(selected, 10000, page_size=(623, 801))
            job["translations_path"].with_name("translations.cache.json").write_text(json.dumps({
                translation_batch.translation_request_key(batch, "test", "low"): {
                    item["id"]: translations[item["id"]] for item in batch.items
                }
                for batch in batches
            }))
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
            self.assertEqual(json.loads(job["translations_path"].read_text()), translations)
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
                patch.object(qa_visual, "load_render_plan_artifact", side_effect=AssertionError("QA reloaded a plan")),
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

    def test_raster_uses_actual_pixel_plan_and_page_dimensions_for_qa(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.pdf"
            output = root / "output.pdf"
            job = {"job_dir": root, "pages_dir": root / "pages",
                   "translated_pages_dir": root / "translated_pages", "plans_dir": root / "plans"}
            job["pages_dir"].mkdir()
            job["translated_pages_dir"].mkdir()
            with render_pdf.load_fitz().open() as doc:
                doc.new_page(width=200, height=220)
                doc.new_page(width=260, height=300)
                doc.save(source)
            Image.new("RGB", (200, 220), "white").save(job["pages_dir"] / "page-001.png")
            Image.new("RGB", (260, 300), "white").save(job["pages_dir"] / "page-002.png")
            block = {"id": "p002b0001", "page": 2, "block_index": 1,
                     "text": "A complete explanation of the method.",
                     "xMin": 20, "yMin": 30, "xMax": 230, "yMax": 90}
            selected = [(1, []), (2, [block])]
            translations = {block["id"]: "这个方法的完整说明。"}
            result = pipeline.render_translated_pdf(source, output, selected, translations,
                                                    (200, 220), 72, job, render_mode="raster")
            self.assertEqual([plan.coordinate_space for plan in result.plans], ["pixels", "pixels"])
            self.assertEqual([plan.raster_size for plan in result.plans], [(200, 220), (260, 300)])
            item = next(item for item in result.plans[1].items if item.kind == "translated_text")
            self.assertTrue(item.raster_lines)
            self.assertIsNotNone(item.font_size)
            artifact = json.loads((job["plans_dir"] / "page-002.render-plan.json").read_text())
            self.assertEqual(artifact["coordinate_space"], "pixels")
            self.assertEqual(artifact["render_items"][0]["raster_lines"], item.raster_lines)
            self.assertEqual(artifact["validation_results"]["errors"], [])
            with render_pdf.load_fitz().open(output) as doc:
                self.assertEqual([(page.rect.width, page.rect.height) for page in doc],
                                 [(200, 220), (260, 300)])
            args = SimpleNamespace(render_mode="raster", strict_qa=True, qa_mode="sample",
                                   qa_sample_size=0, qa_batch_chars=7000, model="test",
                                   reasoning_effort="low", retries=1)
            with (patch.object(pipeline, "build_page_render_plan",
                               side_effect=AssertionError("raster QA rebuilt vector layout")),
                  patch.object(pipeline.qa, "choose_items_for_qa", return_value=[])):
                report = pipeline.run_qa_for_job(selected, translations, job, (200, 220), args,
                                                 output_pdf_path=output, render_result=result)
            self.assertEqual(report["deterministic_issue_count"], 0)
            self.assertEqual(len(report["plan_artifact_paths"]), 2)

    def test_raster_rejects_missing_draw_box_before_replacing_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.pdf"
            output = root / "output.pdf"
            with render_pdf.load_fitz().open() as doc:
                doc.new_page(width=200, height=220)
                doc.save(source)
            output.write_bytes(b"existing output")
            job = {"job_dir": root, "pages_dir": root / "pages",
                   "translated_pages_dir": root / "translated_pages", "plans_dir": root / "plans"}
            job["pages_dir"].mkdir()
            job["translated_pages_dir"].mkdir()
            Image.new("RGB", (200, 220), "white").save(job["pages_dir"] / "page-001.png")
            block = {"id": "p001b0001", "page": 1, "block_index": 1,
                     "text": "A complete source paragraph.",
                     "xMin": 20, "yMin": 30, "xMax": 180, "yMax": 70}
            with (patch.object(pipeline, "build_render_boxes", return_value={}),
                  patch.object(pipeline, "avoid_protected_boxes", return_value=None)):
                with self.assertRaisesRegex(RuntimeError, "no raster text box"):
                    pipeline.render_translated_pdf(source, output, [(1, [block])],
                                                   {block["id"]: "完整译文。"}, (200, 220), 72,
                                                   job, render_mode="raster")
            self.assertEqual(output.read_bytes(), b"existing output")
            artifact = json.loads((job["plans_dir"] / "page-001.render-plan.json").read_text())
            self.assertIn("no raster text box", "\n".join(artifact["validation_results"]["errors"]))

    def test_raster_plan_rejects_lines_that_omit_text(self):
        block = {"id": "p001b0001", "text": "The complete method description."}
        item = render_plan.RenderItem("translated_text", [block["id"]], (10, 10, 180, 80),
                                      text="完整的方法说明。", font_size=14,
                                      raster_lines=["完整的"])
        plan = render_plan.PageRenderPlan(page_num=1, items=[item], raster_size=(200, 220),
                                          coordinate_space="pixels",
                                          coverage=[render_plan.CoverageSource(block["id"], "body")])
        _, errors = pipeline.validate_raster_page_plan(plan, [block])
        self.assertTrue(any("raster lines" in error for error in errors), errors)
        item.text = "完整的"
        _, errors = pipeline.validate_raster_page_plan(
            plan, [block], translations={block["id"]: "完整的方法说明。"},
        )
        self.assertTrue(any("translated content" in error for error in errors), errors)

    def raster_mixed_page(self):
        blocks = [
            {"id": "p001b0001", "page": 1, "block_index": 1,
             "text": "first := input\nPROOF. A complete explanation of the result.",
             "xMin": 20, "yMin": 20, "xMax": 180, "yMax": 150},
            {"id": "p001b0002", "page": 1, "block_index": 2,
             "text": "The following paragraph must remain translated.",
             "xMin": 20, "yMin": 190, "xMax": 180, "yMax": 240},
        ]
        regions = [{"source_ids": ["p001b0001"], "bbox": (20, 20, 180, 70),
                    "source_bbox": (20, 20, 180, 70),
                    "mixed_body_source_ids": ["p001b0001"], "mixed_body_bbox": (20, 100, 180, 150)}]
        classes = {"p001b0001": "code_region", "p001b0002": "body"}
        components = ownership.build_page_components(1, blocks, classes, visual_regions=regions)
        analysis = pipeline.TranslationPageOwnership(
            classes, components, ownership.validate_ownership(1, blocks, components),
            regions, [b["id"] for b in blocks], False,
        )
        translations = {"p001b0001": "first := input\n证明：这是完整正文。",
                        "p001b0002": "后面的正文仍然正常翻译。"}
        return blocks, translations, analysis

    def test_raster_renders_mixed_components_and_neighbor_once(self):
        blocks, translations, analysis = self.raster_mixed_page()
        for dpi in (72, 144):
            with self.subTest(dpi=dpi):
                plan, initial = pipeline.build_raster_page_plan(
                    1, blocks, translations, dpi, (200 * dpi // 72, 280 * dpi // 72),
                    (200, 280), source_analysis=analysis,
                )
                _, errors = pipeline.validate_raster_page_plan(plan, blocks, initial, translations)
                self.assertEqual(errors, [])
                self.assertEqual([i.text for i in plan.items if i.kind == "translated_text"],
                                 ["证明：这是完整正文。", "后面的正文仍然正常翻译。"])
                self.assertEqual({i.component_id for i in plan.items},
                                 {c.component_id for c in analysis.components})
                body = next(i for i in plan.items if i.text == "证明：这是完整正文。")
                self.assertGreaterEqual(body.raster_source_bbox[1], 100 * dpi // 72)

    def test_merged_body_does_not_cover_missing_visual_component_of_same_source(self):
        blocks, translations, analysis = self.raster_mixed_page()
        blocks[1].update(yMin=154, yMax=194)
        analysis.components[-1] = replace(analysis.components[-1], source_bbox=(20, 154, 180, 194))
        plan = render_plan.PageRenderPlan(1, page_size=(200, 280), components=analysis.components)
        for component in plan.components:
            visual = component.component_kind == "visual"
            plan.coverage.extend(render_plan.CoverageSource(
                source_id, "code_region" if visual else "body", component_id=component.component_id,
                component_kind=component.component_kind,
            ) for source_id in component.source_ids)
            plan.items.append(render_plan.RenderItem(
                "original_image_clip" if visual else "translated_text", component.source_ids,
                component.clip_bbox if visual else component.source_bbox,
                text="" if visual else ("证明：这是完整正文。" if component.parent_component_id
                                         else translations[component.source_ids[0]]),
                font_size=None if visual else 9.2, style_name="" if visual else "body",
                layout_role="mixed_visual_body" if component.parent_component_id else "normal",
                component_id=component.component_id, component_kind=component.component_kind,
            ))
        pipeline.merge_adjacent_body_text_flows(plan)
        pipeline.annotate_plan_with_component_metadata(plan, ownership.components_by_source_id(plan.components))
        self.assertTrue(all(entry.rendered for entry in plan.ledger))
        plan.items = [item for item in plan.items if item.kind != "original_image_clip"]
        self.assertEqual(len(plan.items), 1)
        self.assertEqual(plan.items[0].source_ids, [block["id"] for block in blocks])
        for candidate in (plan, render_plan.render_plan_from_json(render_plan.render_plan_to_json(plan))):
            checks, errors = pipeline.validate_final_page_plan(candidate, blocks, pipeline.load_fitz(), translations)
            self.assertTrue(checks["coverage_errors"], errors)
            self.assertTrue(all(entry.rendered for entry in candidate.ledger if entry.component_kind == "translated_text"))
            self.assertFalse(next(entry.rendered for entry in candidate.ledger if entry.component_kind == "visual"))

    def test_raster_rejects_missing_mixed_component_even_if_source_id_is_covered(self):
        blocks, translations, analysis = self.raster_mixed_page()
        plan, _ = pipeline.build_raster_page_plan(
            1, blocks, translations, 72, (200, 280), (200, 280), source_analysis=analysis,
        )
        body_id = analysis.components[1].component_id
        plan.items = [i for i in plan.items if i.component_id != body_id]
        plan.coverage = [e for e in plan.coverage if e.component_id != body_id]
        _, errors = pipeline.validate_raster_page_plan(plan, blocks, translations=translations)
        self.assertTrue(any(body_id in error for error in errors), errors)

    def test_raster_preserves_unresolved_mixed_body_with_explicit_image_fallback(self):
        blocks, translations, analysis = self.raster_mixed_page()
        del translations["p001b0001"]
        plan, initial = pipeline.build_raster_page_plan(
            1, blocks, translations, 72, (200, 280), (200, 280), source_analysis=analysis,
        )
        _, errors = pipeline.validate_raster_page_plan(plan, blocks, initial, translations)
        self.assertEqual(errors, [])
        body = [i for i in plan.items if i.component_id == analysis.components[1].component_id]
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0].kind, "original_image_clip")
        self.assertEqual(body[0].bbox, (20, 100, 180, 150))
        self.assertTrue(body[0].fallback_reason)

    def test_raster_rejects_erasing_the_visual_part_of_a_mixed_block(self):
        blocks, translations, analysis = self.raster_mixed_page()
        plan, _ = pipeline.build_raster_page_plan(
            1, blocks, translations, 72, (200, 280), (200, 280), source_analysis=analysis,
        )
        body = next(i for i in plan.items if i.text == "证明：这是完整正文。")
        body.raster_source_bbox = (20, 20, 180, 150)
        _, errors = pipeline.validate_raster_page_plan(plan, blocks, translations=translations)
        self.assertTrue(any("erase" in error for error in errors), errors)

    def test_raster_mixed_drawing_does_not_erase_the_entire_source_block(self):
        blocks, translations, analysis = self.raster_mixed_page()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = {"job_dir": root, "pages_dir": root / "pages", "plans_dir": root / "plans",
                   "translated_pages_dir": root / "translated_pages"}
            job["pages_dir"].mkdir()
            job["translated_pages_dir"].mkdir()
            source = Image.new("RGB", (200, 280), "white")
            source.paste((0, 0, 0), (30, 30, 100, 60))
            source.paste((255, 0, 0), (30, 80, 40, 90))
            source.save(job["pages_dir"] / "page-001.png")
            plans = pipeline.render_pages([(1, blocks)], translations, 72, job,
                                          source_analysis_by_page={1: analysis})
            with Image.open(pipeline.translated_page_path(1, job)) as output:
                self.assertEqual(output.convert("RGB").crop((20, 20, 180, 70)).tobytes(),
                                 source.crop((20, 20, 180, 70)).tobytes())
                self.assertEqual(output.convert("RGB").getpixel((35, 85)), (255, 0, 0))
                body_pixels = output.convert("RGB").crop((20, 100, 180, 150))
                self.assertLess(body_pixels.convert("L").getextrema()[0], 200)
            artifact = json.loads((job["plans_dir"] / "page-001.render-plan.json").read_text())
            self.assertEqual(artifact["validation_results"]["errors"], [])
            self.assertEqual(len(plans), 1)

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
