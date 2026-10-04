import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import layout
import pipeline
import render_plan


def body(source_id="p002b0001", text="This paragraph explains the algorithm."):
    return dict(id=source_id, page=2, block_index=1, text=text,
                xMin=100, yMin=100, xMax=400, yMax=150)


class SourceContractTests(unittest.TestCase):
    def test_numbered_heading_preserves_source_punctuation_without_translation_task(self):
        blocks = [dict(body("number", "2.1."), xMin=100, xMax=122, yMin=120, yMax=136),
                  dict(body("title", "System Design"), xMin=132, xMax=280, yMin=120, yMax=136)]
        analysis = pipeline.build_translation_page_components(2, blocks, page_size=(623, 801))
        self.assertEqual(analysis.translatable_ids, ["title"])
        plan = pipeline.build_final_page_plan(2, blocks, {"title": "系统设计"}, (623, 801), source_analysis=analysis)
        heading = next(item for item in plan.items if item.fallback_reason == "standalone_heading_pair")
        self.assertEqual(heading.text, "2.1. 系统设计")
        self.assertEqual(pipeline.validate_plan_text_content(2, blocks, {"title": "系统设计"}, plan), [])

    def test_heading_pair_roles_are_decided_by_source_analysis(self):
        blocks = [dict(body("number", "2"), xMin=100, xMax=110, yMin=120, yMax=132),
                  dict(body("title", "System Design"), xMin=120, xMax=280, yMin=120, yMax=132)]
        analysis = pipeline.build_translation_page_components(2, blocks, page_size=(623, 801))
        self.assertEqual(analysis.classes, {"number": "heading", "title": "heading"})
        plan = pipeline.build_page_render_plan(2, blocks, {"title": "系统设计"}, (623, 801), source_analysis=analysis)
        self.assertEqual({entry.block_id: entry.classification for entry in plan.ledger}, analysis.classes)

    def test_ocr_coordinates_use_each_source_pages_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for page, size in ((1, (400, 800)), (2, (800, 400))):
                Image.new("RGB", size, "white").save(root / f"page-{page:03d}.png")

            def ocr(path):
                with Image.open(path) as image:
                    w, h = image.size
                return [([[w/4, h/4], [w*3/4, h/4], [w*3/4, h*.3], [w/4, h*.3]],
                         "This is an ordinary paragraph about the system.", .99)], None

            with patch.object(pipeline, "load_rapidocr", return_value=ocr):
                pages = pipeline.generate_ocr_pages(
                    {"pages_dir": root}, {1: (200, 400), 2: (400, 200)})
            self.assertEqual([(p[0]["xMin"], p[0]["yMin"]) for p in pages], [(50, 100), (100, 50)])
            (root / "page-001.png").unlink()
            with patch.object(pipeline, "load_rapidocr", return_value=ocr):
                subset = pipeline.generate_ocr_pages({"pages_dir": root}, {2: (400, 200)})
            self.assertEqual(subset[1], pages[1])

    def test_journal_footer_preserves_actual_volume_and_date(self):
        for issue in ("Vol. 13, No. 2, April 1991.", "Vol. 46, No. 1, March 2024."):
            with self.subTest(issue=issue):
                text = "ACM Transactions on Programming Languages and Systems, " + issue
                items = pipeline.journal_footer_render_items(
                    [{"text": text, "bbox": (100, 740, 550, 750)}], (623, 801))
                self.assertEqual([item.text for item in items], [text])

    def test_footer_consistency_compares_style_not_content_or_page_size(self):
        plans = []
        for page, size, text in ((1, (623, 801), "First issue."), (2, (400, 600), "Second issue.")):
            item = render_plan.RenderItem("original_selectable_text", [], pipeline.journal_footer_bbox(size),
                                          text=text, font_size=layout.JOURNAL_FOOTER_FONT_SIZE,
                                          style_name="footer", layout_role="journal_footer")
            plans.append(render_plan.PageRenderPlan(page, items=[item], page_size=size))
        self.assertEqual(pipeline.validate_footer_consistency(plans), [])
        plans[1].items[0].font_size += 2
        self.assertTrue(pipeline.validate_footer_consistency(plans))


class TextContractTests(unittest.TestCase):
    def test_ordinary_text_keeps_visual_like_prefix_in_final_translation(self):
        source = body("a", "max(x,y)\nThis paragraph explains the algorithm in detail.")
        final = pipeline.finalize_translations([(2, [source])], {"a": "max(x,y)\n证明：成立。"})
        self.assertEqual(final["a"], "max(x,y)\n证明：成立。")
        plan = pipeline.build_final_page_plan(2, [source], final, (623, 801))
        self.assertEqual(pipeline.validate_plan_text_content(2, [source], final, plan), [])

    def test_numbered_continuation_stays_translated_when_previous_item_is_original(self):
        original = body("a", "(1) Read all input values before computing the result.")
        following = dict(body("b", "and returns the computed value. (2) The algorithm outputs the next result."),
                         yMin=170, yMax=230)
        translations = {"b": "然后完成计算。(2) 输出下一个结果。"}
        plan = pipeline.build_final_page_plan(2, [original, following], translations, (623, 801))
        translated = [item for item in plan.items if item.kind == "translated_text"]
        self.assertEqual([item.text for item in translated], [translations["b"]])
        self.assertEqual(pipeline.validate_plan_text_content(2, [original, following], translations, plan), [])

    def test_body_flow_rejects_reordering_even_when_spans_move_with_text(self):
        a, b = body("a"), dict(body("b"), yMin=160, yMax=210)
        translations = {"a": "第一段说明。", "b": "第二段结论。"}
        plan = pipeline.build_final_page_plan(2, [a, b], translations, (623, 801))
        flow = next(i for i in plan.items if i.layout_role == "body_flow")
        flow.text = translations["b"] + translations["a"]
        flow.text_spans = list(reversed(flow.text_spans))
        self.assertTrue(pipeline.validate_plan_text_content(2, [a, b], translations, plan))

    def test_split_spans_survive_json_and_reject_repeated_fragment(self):
        source = body("a")
        text = "第一句说明输入。第二句说明计算。第三句说明输出。"
        item = render_plan.RenderItem("translated_text", ["a"], (20, 20, 180, 160), text=text,
                                      font_size=layout.BODY_FONT_SIZE, style_name="body")
        plan = render_plan.PageRenderPlan(2, items=[item,
            render_plan.RenderItem("original_image_clip", [], (10, 60, 190, 90))],
            coverage=[render_plan.CoverageSource("a", "body")], page_size=(220, 220))
        pipeline.bind_plan_text_spans(plan, [source], {"a": text})
        layout.split_translated_text_around_protected(plan, plan.page_size)
        self.assertGreater(len([i for i in plan.items if i.kind == "translated_text"]), 1)
        decoded = render_plan.render_plan_from_json(render_plan.render_plan_to_json(plan))
        self.assertEqual(pipeline.validate_plan_text_content(2, [source], {"a": text}, decoded), [])
        first = next(i for i in decoded.items if i.kind == "translated_text")
        decoded.items.append(replace(first, bbox=(20, 170, 180, 210)))
        self.assertTrue(pipeline.validate_plan_text_content(2, [source], {"a": text}, decoded))

    def test_missing_translation_is_preserved_and_reported_not_invented(self):
        source = body(text="(2) In(A) is a set of input events,")
        plan = pipeline.build_page_render_plan(2, [source], {}, (623, 801))
        self.assertFalse(any(i.kind == "translated_text" for i in plan.items))
        self.assertTrue(pipeline.validate_plan_translation_quality(2, [source], {}, plan))

    def test_final_validation_rejects_nonoverlapping_duplicate_translation(self):
        source = body()
        translations = {source["id"]: "这段正文解释算法。"}
        plan = pipeline.build_final_page_plan(2, [source], translations, (623, 801))
        item = next(i for i in plan.items if i.kind == "translated_text")
        plan.items.append(replace(item, bbox=(100, 230, 400, 280)))
        _, errors = pipeline.validate_final_page_plan(plan, [source], pipeline.load_fitz(), translations)
        self.assertTrue(errors)

    def test_final_validation_rejects_changed_punctuation_and_letter_case(self):
        source = body()
        translations = {source["id"]: "计算结果为 A，不是 a。"}
        for changed in ("计算结果为 A？不是 a。", "计算结果为 a，不是 A。"):
            with self.subTest(changed=changed):
                plan = pipeline.build_final_page_plan(2, [source], translations, (623, 801))
                next(i for i in plan.items if i.kind == "translated_text").text = changed
                self.assertTrue(pipeline.validate_plan_text_content(2, [source], translations, plan))

    def test_short_fragment_merge_does_not_rewrite_words_or_punctuation(self):
        self.assertEqual(pipeline.merge_short_fragment_text("读取下一个单元。", "链表"),
                         "读取下一个单元。 链表")

    def test_equal_text_from_different_sources_is_not_a_duplicate(self):
        a, b = body("a"), dict(body("b"), yMin=160, yMax=210)
        translations = {"a": "相同的正文。", "b": "相同的正文。"}
        plan = pipeline.build_final_page_plan(2, [a, b], translations, (623, 801))
        self.assertEqual(pipeline.validate_plan_text_content(2, [a, b], translations, plan), [])
        self.assertEqual("".join(i.text for i in plan.items if i.kind == "translated_text").count("相同的正文。"), 2)


class QaContractTests(unittest.TestCase):
    def test_strict_quality_failure_is_applied_after_independent_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = body()
            plan = pipeline.build_final_page_plan(2, [source], {}, (623, 801), output_page_num=1)
            output = root / "output.pdf"
            with pipeline.load_fitz().open() as doc:
                doc.new_page(width=623, height=801)
                doc.save(output)
            job = {"job_dir": root, "plans_dir": root / "plans", "pages_dir": root / "pages"}
            with self.assertRaisesRegex(RuntimeError, "deterministic QA"):
                pipeline.run_qa_for_job([(2, [source])], {}, job, (623, 801),
                    pipeline.DocumentOptions(qa=True, strict_qa=True), output,
                    render_result=render_plan.DocumentRenderResult([plan], {}))
            summary = json.loads((root / "qa_summary.json").read_text())
            self.assertGreater(summary["deterministic_issue_count"], 0)
            self.assertEqual(summary["visual_status"], "completed")
            self.assertEqual(summary["semantic_status"], "completed")
            self.assertTrue((root / "visual_qa/visual_qa_report.json").exists())
            self.assertEqual(json.loads((root / "backtranslate_report.json").read_text()), [])

    def test_backtranslation_failure_still_writes_visual_and_deterministic_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = body()
            translations = {source["id"]: "这段正文解释算法。"}
            plan = pipeline.build_final_page_plan(2, [source], translations, (623, 801), output_page_num=1)
            output = root / "output.pdf"
            with pipeline.load_fitz().open() as doc:
                doc.new_page(width=623, height=801)
                doc.save(output)
            job = {"job_dir": root, "plans_dir": root / "plans", "pages_dir": root / "pages"}
            with patch.object(pipeline.qa, "run_backtranslation", side_effect=RuntimeError("model unavailable")):
                with self.assertRaisesRegex(RuntimeError, "model unavailable"):
                    pipeline.run_qa_for_job([(2, [source])], translations, job, (623, 801),
                        pipeline.DocumentOptions(qa=True), output,
                        render_result=render_plan.DocumentRenderResult([plan], translations))
            self.assertTrue((root / "deterministic_quality_report.json").exists())
            self.assertTrue((root / "visual_qa/visual_qa_report.json").exists())
            summary = json.loads((root / "qa_summary.json").read_text())
            self.assertEqual(summary["semantic_status"], "failed")
            self.assertIn("model unavailable", summary["semantic_error"])


if __name__ == "__main__":
    unittest.main()
