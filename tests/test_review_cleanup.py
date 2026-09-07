import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import classify
import layout
import qa_visual
import render_plan
import translate_pdf_via_codex as pipeline


class ReviewCleanupTests(unittest.TestCase):
    def block(self, text, y=180):
        return {"id": "b", "page": 2, "text": text,
                "xMin": 20, "xMax": 300, "yMin": y, "yMax": y + 44}

    def test_body_mentions_of_paper_title_and_author_are_preserved(self):
        for text in ("Wait-free synchronization is useful.",
                     "Maurice Herlihy proposed this algorithm.",
                     "Wait-Free Synchronization\nis discussed here."):
            with self.subTest(text=text):
                block = self.block(text)
                self.assertEqual(pipeline.source_boundary_text_for_block(block), text)
                self.assertEqual(pipeline.clean_render_text(block, text), text)
        block = self.block("Wait-free synchronization ensures progress.")
        self.assertEqual(pipeline.clean_render_text(block, "无等待同步能够保证进展。"),
                         "无等待同步能够保证进展。")

    def test_margin_header_classification_is_independent_of_paper_title(self):
        for title in ("Wait-Free Synchronization", "Concurrent Data Structures"):
            with self.subTest(title=title):
                block = self.block(title + "\nof operations. The next sentence.", y=46)
                other = {**block, "id": "other", "page": 3}
                block = classify.mark_running_headers([(2, [block]), (3, [other])])[0][1][0]
                self.assertEqual(pipeline.source_boundary_text_for_block(block),
                                 "of operations. The next sentence.")
                self.assertEqual(pipeline.clean_render_text(block, title + "\n操作。下一句。"),
                                 "操作。下一句。")
                translations = {"b": title + "\n操作。下一句。"}
                prepared = pipeline.translation_for_block(block, translations)
                self.assertEqual(pipeline.clean_render_text(block, prepared, translations["b"]),
                                 "操作。下一句。")

    def test_margin_body_subject_is_not_a_header_without_separation_or_repetition(self):
        for subject in ("Leslie Lamport", "Concurrent Data Structures"):
            with self.subTest(subject=subject):
                source = subject + "\nintroduced this algorithm."
                block = self.block(source, y=46)
                translation = subject + "\n提出了这个算法。"
                for rows in ([], [
                    {"bbox": (20, 46, 200, 56), "text": subject},
                    {"bbox": (20, 58, 280, 68), "text": "introduced this algorithm."},
                ]):
                    marked = classify.mark_running_headers([(2, [block])], {2: rows})[0][1][0]
                    self.assertEqual(pipeline.source_boundary_text_for_block(marked), source)
                    self.assertIn(subject, pipeline.translation_for_block(marked, {"b": translation}))

    def test_header_cleanup_preserves_unmatched_translation_lines(self):
        block = {**self.block("Concurrent Data Structures\nThe algorithm protects every operation.", y=46),
                 "running_header": "Concurrent Data Structures"}
        text = "算法保护每一个操作，\n并保证所有线程都能够继续运行。"
        self.assertEqual(pipeline.translation_for_block(block, {"b": text}),
                         pipeline.prepare_render_translation(text))

    def test_coverage_uses_the_same_trivial_content_policy(self):
        for text, needs_coverage in (("• 12 •", False), ("1234", True),
                                     ("https://example.com", False), ("Body text.", True)):
            with self.subTest(text=text):
                blocks = [self.block(text)]
                plan = render_plan.PageRenderPlan(2)
                self.assertEqual(bool(render_plan.validate_plan_coverage(2, blocks, plan)), needs_coverage)

    def test_visual_qa_uses_absolute_font_policy_for_objects_and_json(self):
        for reason in ("", "fit_shrink", "source_adapted_font"):
            plan = render_plan.PageRenderPlan(
                2,
                items=[render_plan.RenderItem("translated_text", ["b"], (20, 50, 300, 90),
                                              text="正文", font_size=1, style_name="body",
                                              fallback_reason=reason)],
                ledger=[render_plan.CoverageEntry("b", "body", "translated_text", True)],
            )
            for value in (plan, render_plan.render_plan_to_json(plan)):
                with self.subTest(reason=reason, representation=type(value).__name__):
                    self.assertEqual(bool(qa_visual.detect_style_issues(value)),
                                     bool(layout.validate_plan_style_policy(plan)))

    def test_boundary_repairs_reject_stale_and_invalid_outputs(self):
        good = {"items": [{"key": "a->b", "translation": "完整译文。",
                           "next_prefix_translation": ""}]}
        invalid = [None, {"items": []}, {"items": good["items"] * 2},
                   {"items": [{"key": "a->b", "translation": "", "next_prefix_translation": ""}]},
                   {"items": [{"key": "wrong", "translation": "旧译文。", "next_prefix_translation": ""}]},
                   {"items": [{"key": "a->b", "translation": "译文。", "next_prefix_translation": None}]}]
        for response in invalid:
            with self.subTest(response=response), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                output = root / "boundary-sentence-repair.out.json"
                output.write_text(json.dumps(good))
                attempts = []

                def run(command, **kwargs):
                    value = response if not attempts else good
                    attempts.append(command)
                    if value is not None:
                        output.write_text(json.dumps(value))
                    return subprocess.CompletedProcess(command, 0, "", "")

                candidate = {"key": "a->b", "source_sentence": "A sentence.",
                             "previous_source": "A", "next_source": "sentence.",
                             "previous_id": "a", "next_id": "b"}
                with patch("subprocess.run", side_effect=run), patch("time.sleep"):
                    result = pipeline.translate_boundary_sentence_repairs(
                        [candidate], {}, {"job_dir": root, "boundary_schema_path": root / "schema.json"},
                        model="test",
                    )
                self.assertEqual(len(attempts), 2)
                self.assertEqual(result, {"a->b": {"translation": "完整译文。", "next_prefix_translation": ""}})
