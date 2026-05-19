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

    def test_page1_title_metadata_and_abstract_are_separate_styles(self):
        bbox_lines = [
            line
            for line in pdf.parse_bbox_lines(JOB / "source_bbox.html")
            if line["page"] == 1
        ]
        plan = pdf.build_page_render_plan(
            1,
            self.pages[0],
            self.translations,
            page_size=(623, 801),
            bbox_lines=bbox_lines,
            source_image_path=JOB / "pages" / "page-001.png",
        )
        title_items = [
            item
            for item in plan.items
            if item.kind == "translated_text" and item.style_name == "title" and "p001b0001" in item.source_ids
        ]
        abstract_items = [
            item
            for item in plan.items
            if item.kind == "translated_text" and item.style_name == "body" and "p001b0001" in item.source_ids
        ]
        combined_text = "\n".join(item.text for item in plan.items if "p001b0001" in item.source_ids)

        self.assertEqual(len(title_items), 1)
        self.assertEqual(title_items[0].text.strip(), "无等待同步")
        self.assertTrue(any(item.text.startswith("并发数据对象的无等待实现") for item in abstract_items))
        self.assertNotIn("无等待同步 MAURICE HERLIHY", combined_text)
        self.assertEqual(pdf.validate_plan_text_noise_policy(plan), [])

    def test_page2_preserves_paragraph_breaks_and_no_duplicate_line_fragments_at_top(self):
        bbox_lines = [
            line
            for line in pdf.parse_bbox_lines(JOB / "source_bbox.html")
            if line["page"] == 2
        ]
        plan = pdf.build_page_render_plan(
            2,
            self.pages[1],
            self.translations,
            page_size=(623, 801),
            bbox_lines=bbox_lines,
            source_image_path=JOB / "pages" / "page-002.png",
        )
        body_items = [
            item
            for item in plan.items
            if item.kind == "translated_text" and "p002b0002" in item.source_ids
        ]
        body_text = "\n".join(item.text for item in body_items)

        self.assertTrue(body_text.startswith("页面错误或缓存未命中"))
        self.assertNotIn("无等待同步页面错误", body_text[:80])
        self.assertNotIn("\n访问。\n", body_text[:120])
        self.assertIn("\n并发数据对象的无等待实现", body_text)
        self.assertIn("\n显然，证明某个无等待实现存在的方法", body_text)

    def test_page2_large_body_block_is_split_into_source_paragraphs(self):
        bbox_lines = [
            line
            for line in pdf.parse_bbox_lines(JOB / "source_bbox.html")
            if line["page"] == 2
        ]
        plan = pdf.build_page_render_plan(
            2,
            self.pages[1],
            self.translations,
            page_size=(623, 801),
            bbox_lines=bbox_lines,
            source_image_path=JOB / "pages" / "page-002.png",
        )
        paragraph_items = [
            item
            for item in plan.items
            if item.kind == "translated_text"
            and item.fallback_reason == "source_paragraph_split"
            and "p002b0002" in item.source_ids
        ]
        body_text = "\n".join(item.text for item in paragraph_items)

        self.assertGreaterEqual(len(paragraph_items), 6)
        self.assertLess(paragraph_items[0].bbox[1], 90.0)
        self.assertGreater(paragraph_items[-1].bbox[1], 500.0)
        self.assertIn("可以表述为：\n给定两个并发对象", body_text)
        self.assertNotIn("实现？如下", body_text)

    def test_page4_input_events_block_has_fallback_or_translation(self):
        plan = self.plan_for_page(4)
        entries = {entry.block_id: entry for entry in plan.ledger}

        self.assertIn("p004b0005", entries)
        self.assertIn(entries["p004b0005"].render_kind, {"translated_text", "original_selectable_text", "original_image_clip"})

    def test_page4_preserved_missing_input_events_uses_selectable_text_at_body_margin(self):
        bbox_lines = [
            line
            for line in pdf.parse_bbox_lines(JOB / "source_bbox.html")
            if line["page"] == 4
        ]
        plan = pdf.build_page_render_plan(
            4,
            self.pages[3],
            self.translations,
            page_size=(623, 801),
            bbox_lines=bbox_lines,
        )
        items = [item for item in plan.items if "p004b0005" in item.source_ids]

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].kind, "translated_text")
        self.assertIn("输入事件集合", items[0].text)
        self.assertGreater(items[0].bbox[0], 100)

    def test_page4_io_automaton_component_enumeration_keeps_source_order(self):
        bbox_lines = [
            line
            for line in pdf.parse_bbox_lines(JOB / "source_bbox.html")
            if line["page"] == 4
        ]
        plan = pdf.build_page_render_plan(
            4,
            self.pages[3],
            self.translations,
            page_size=(623, 801),
            bbox_lines=bbox_lines,
            source_image_path=JOB / "pages" / "page-004.png",
        )
        body_text = "\n".join(
            item.text
            for item in sorted(plan.items, key=lambda candidate: (candidate.bbox[1], candidate.bbox[0]))
            if item.kind == "translated_text"
        )

        self.assertLess(body_text.index("(1) States(A)"), body_text.index("(2) In(A)"))
        self.assertLess(body_text.index("(2) In(A)"), body_text.index("(3) Out(A)"))
        self.assertNotIn("初始状态集合。(3) Out(A)", body_text)

    def test_page15_assertion_formulas_are_image_clips(self):
        plan = self.plan_for_page(15)
        image_ids = {source_id for item in plan.items if item.kind == "original_image_clip" for source_id in item.source_ids}

        self.assertIn("p015b0004", image_ids)
        self.assertIn("p015b0007", image_ids)
        self.assertIn("p015b0011", image_ids)

    def test_page15_formula_clip_does_not_swallow_assertion_intro_text(self):
        plan = self.plan_for_page(15)
        image_ids = {source_id for item in plan.items if item.kind == "original_image_clip" for source_id in item.source_ids}
        translated_ids = {source_id for item in plan.items if item.kind == "translated_text" for source_id in item.source_ids}

        self.assertNotIn("p015b0006", image_ids)
        self.assertIn("p015b0006", translated_ids)

    def test_page16_text_split_around_formula_keeps_fixed_body_style_and_fits(self):
        bbox_lines = [
            line
            for line in pdf.parse_bbox_lines(JOB / "source_bbox.html")
            if line["page"] == 16
        ]
        plan = pdf.build_page_render_plan(
            16,
            self.pages[15],
            self.translations,
            page_size=(623, 801),
            bbox_lines=bbox_lines,
            source_image_path=JOB / "pages" / "page-016.png",
        )
        body_items = [
            item
            for item in plan.items
            if item.kind == "translated_text" and "p016b0013" in item.source_ids
        ]

        self.assertTrue(body_items)
        self.assertEqual({item.font_size for item in body_items}, {pdf.BODY_FONT_SIZE})
        self.assertEqual(pdf.validate_plan_text_fit(plan), [])

    def test_page16_top_decide_signature_is_code_image_not_body_fallback(self):
        bbox_lines = [
            line
            for line in pdf.parse_bbox_lines(JOB / "source_bbox.html")
            if line["page"] == 16
        ]
        plan = pdf.build_page_render_plan(
            16,
            self.pages[15],
            self.translations,
            page_size=(623, 801),
            bbox_lines=bbox_lines,
            source_image_path=JOB / "pages" / "page-016.png",
        )
        image_ids = {source_id for item in plan.items if item.kind == "original_image_clip" for source_id in item.source_ids}
        body_fallback_ids = {
            source_id
            for item in plan.items
            if item.kind == "original_selectable_text" and item.fallback_reason == "untranslated_fallback_original"
            for source_id in item.source_ids
        }
        translated_text = "\n".join(item.text for item in plan.items if item.kind == "translated_text")
        translated_ids = {source_id for item in plan.items if item.kind == "translated_text" for source_id in item.source_ids}

        self.assertIn("p016b0003", image_ids)
        self.assertNotIn("p016b0003", body_fallback_ids)
        self.assertNotIn("p016b0002", translated_ids)
        self.assertNotIn("電·139", translated_text)

    def test_page16_split_text_does_not_reexpand_over_formula_clip(self):
        bbox_lines = [
            line
            for line in pdf.parse_bbox_lines(JOB / "source_bbox.html")
            if line["page"] == 16
        ]
        plan = pdf.build_page_render_plan(
            16,
            self.pages[15],
            self.translations,
            page_size=(623, 801),
            bbox_lines=bbox_lines,
            source_image_path=JOB / "pages" / "page-016.png",
        )

        self.assertEqual(pdf.validate_plan_layout(plan, (623, 801)), [])

    def test_page16_assertion_text_drops_ocr_garbage_and_keeps_prose_completion(self):
        bbox_lines = [
            line
            for line in pdf.parse_bbox_lines(JOB / "source_bbox.html")
            if line["page"] == 16
        ]
        plan = pdf.build_page_render_plan(
            16,
            self.pages[15],
            self.translations,
            page_size=(623, 801),
            bbox_lines=bbox_lines,
            source_image_path=JOB / "pages" / "page-016.png",
        )
        rendered_text = "\n".join(item.text for item in plan.items if item.kind == "translated_text")

        self.assertNotIn("sisixa", rendered_text)
        self.assertNotIn("arra s", rendered_text)
        self.assertNotIn("有效性成立，因为每个进程在。", rendered_text)
        self.assertIn("执行 swap 前初始化其在 prefer 中的位置", rendered_text)
        self.assertEqual(pdf.validate_plan_quality(16, self.pages[15], self.translations, plan), [])

    def test_page21_long_body_keeps_definition_prefix_before_figure_description(self):
        bbox_lines = [
            line
            for line in pdf.parse_bbox_lines(JOB / "source_bbox.html")
            if line["page"] == 21
        ]
        plan = pdf.build_page_render_plan(
            21,
            self.pages[20],
            self.translations,
            page_size=(623, 801),
            bbox_lines=bbox_lines,
            source_image_path=JOB / "pages" / "page-021.png",
        )
        rendered = "\n".join(
            item.text
            for item in plan.items
            if item.kind == "translated_text" and "p021b0002" in item.source_ids
        )

        self.assertIn("令 max(head)", rendered)
        self.assertIn("(1) concur(P)", rendered)
        self.assertIn("(2) start(P)", rendered)
        self.assertIn("|concur(P)| + start(P) = max(head)", rendered)
        self.assertIn("图 14", rendered)

    def test_page12_embedded_subsection_uses_subheading_style_not_body_flow(self):
        bbox_lines = [
            line
            for line in pdf.parse_bbox_lines(JOB / "source_bbox.html")
            if line["page"] == 12
        ]
        plan = pdf.build_page_render_plan(
            12,
            self.pages[11],
            self.translations,
            page_size=(623, 801),
            bbox_lines=bbox_lines,
            source_image_path=JOB / "pages" / "page-012.png",
        )
        subheadings = [
            item
            for item in plan.items
            if item.kind == "translated_text"
            and item.style_name == "subheading"
            and "3.3 队列、栈、列表等" in item.text
        ]
        body_text = "\n".join(
            item.text
            for item in plan.items
            if item.kind == "translated_text" and item.style_name == "body"
        )
        first_following_body = next(
            item
            for item in sorted(plan.items, key=lambda candidate: (candidate.bbox[1], candidate.bbox[0]))
            if item.kind == "translated_text" and "考虑一个具有两个操作的 FIFO 队列" in item.text
        )

        self.assertEqual(len(subheadings), 1)
        self.assertEqual(subheadings[0].font_size, pdf.DOCUMENT_STYLES["subheading"].font_size)
        self.assertNotIn("3.3 队列、栈、列表等", body_text)
        self.assertLess(subheadings[0].bbox[1], first_following_body.bbox[1])
        self.assertEqual(pdf.validate_plan_style_policy(plan), [])

    def test_page20_drops_redundant_short_list_fragment(self):
        bbox_lines = [
            line
            for line in pdf.parse_bbox_lines(JOB / "source_bbox.html")
            if line["page"] == 20
        ]
        plan = pdf.build_page_render_plan(
            20,
            self.pages[19],
            self.translations,
            page_size=(623, 801),
            bbox_lines=bbox_lines,
            source_image_path=JOB / "pages" / "page-020.png",
        )
        standalone = [
            item
            for item in plan.items
            if item.kind == "translated_text" and item.text.strip() == "链表。"
        ]
        rendered_text = "\n".join(item.text for item in plan.items if item.kind == "translated_text")
        rendered_by_id = pdf.rendered_text_by_source_id(plan)
        merged_after_items = [
            item
            for item in plan.items
            if item.kind == "translated_text"
            and "p020b0005" in item.source_ids
            and "(5)" in item.text
        ]

        self.assertEqual(standalone, [])
        self.assertNotIn("·143", rendered_text)
        self.assertNotIn("143 进程", rendered_text)
        self.assertEqual(len(merged_after_items), 1)
        self.assertIn("链表", merged_after_items[0].text)
        self.assertIn("p020b0005", rendered_by_id)
        self.assertIn("链表", rendered_by_id["p020b0005"])

    def test_pages23_and_25_strip_embedded_running_author_header(self):
        for page_num in (23, 25):
            bbox_lines = [
                line
                for line in pdf.parse_bbox_lines(JOB / "source_bbox.html")
                if line["page"] == page_num
            ]
            plan = pdf.build_page_render_plan(
                page_num,
                self.pages[page_num - 1],
                self.translations,
                page_size=(623, 801),
                bbox_lines=bbox_lines,
                source_image_path=JOB / "pages" / f"page-{page_num:03d}.png",
            )
            rendered_text = "\n".join(item.text for item in plan.items if item.kind == "translated_text")

            self.assertNotIn("Maurice Herlihy", rendered_text)
            self.assertNotIn("MauriceHerlihy", rendered_text)
            self.assertEqual(pdf.validate_plan_quality(page_num, self.pages[page_num - 1], self.translations, plan), [])

    def test_page3_and_page9_figures_are_image_clips(self):
        page3 = self.plan_for_page(3)
        page9 = self.plan_for_page(9)
        page3_images = {source_id for item in page3.items if item.kind == "original_image_clip" for source_id in item.source_ids}
        page9_images = {source_id for item in page9.items if item.kind == "original_image_clip" for source_id in item.source_ids}

        self.assertIn("p003b0004", page3_images)
        self.assertIn("p009b0001", page9_images)
        self.assertIn("p009b0004", page9_images)
        self.assertIn("p009b0005", page9_images)
        self.assertIn("p009b0006", page9_images)
        self.assertIn("p009b0007", page9_images)

    def test_page3_figure_clip_includes_right_table_border_without_running_header(self):
        page3 = self.plan_for_page(3)
        figure_items = [
            item
            for item in page3.items
            if item.kind == "original_image_clip" and "p003b0004" in item.source_ids
        ]

        self.assertEqual(len(figure_items), 1)
        self.assertNotIn("p003b0001", figure_items[0].source_ids)
        self.assertNotIn("p003b0002", figure_items[0].source_ids)
        self.assertIn("p003b0005", figure_items[0].source_ids)
        self.assertGreaterEqual(figure_items[0].bbox[2], 434.0)

    def test_page3_section_headings_are_not_merged_into_body_text(self):
        bbox_lines = [
            line
            for line in pdf.parse_bbox_lines(JOB / "source_bbox.html")
            if line["page"] == 3
        ]
        plan = pdf.build_page_render_plan(
            3,
            self.pages[2],
            self.translations,
            page_size=(623, 801),
            bbox_lines=bbox_lines,
            source_image_path=JOB / "pages" / "page-003.png",
        )
        items_by_id = {
            source_id: item
            for item in plan.items
            if item.kind == "translated_text"
            for source_id in item.source_ids
        }
        body_text = "\n".join(
            item.text
            for item in plan.items
            if item.kind == "translated_text" and item.style_name == "body"
        )

        self.assertEqual(items_by_id["p003b0009"].style_name, "heading")
        self.assertEqual(items_by_id["p003b0010"].style_name, "subheading")
        self.assertNotIn("2. 模型", body_text)
        self.assertNotIn("2.1 I/O 自动机", body_text)
        self.assertLess(items_by_id["p003b0009"].bbox[3], items_by_id["p003b0010"].bbox[1])

    def test_page7_figure_clip_includes_right_object_boundary(self):
        bbox_lines = [
            line
            for line in pdf.parse_bbox_lines(JOB / "source_bbox.html")
            if line["page"] == 7
        ]
        plan = pdf.build_page_render_plan(
            7,
            self.pages[6],
            self.translations,
            page_size=(623, 801),
            bbox_lines=bbox_lines,
            source_image_path=JOB / "pages" / "page-007.png",
        )
        figure_items = [
            item
            for item in plan.items
            if item.kind == "original_image_clip" and "p007b0003" in item.source_ids
        ]

        self.assertEqual(len(figure_items), 1)
        self.assertGreaterEqual(figure_items[0].bbox[2], 440.0)

    def test_page10_split_figure_clips_do_not_leave_broken_line_gap(self):
        bbox_lines = [
            line
            for line in pdf.parse_bbox_lines(JOB / "source_bbox.html")
            if line["page"] == 10
        ]
        plan = pdf.build_page_render_plan(
            10,
            self.pages[9],
            self.translations,
            page_size=(623, 801),
            bbox_lines=bbox_lines,
            source_image_path=JOB / "pages" / "page-010.png",
        )
        upper = next(item for item in plan.items if "p010b0002" in item.source_ids)
        lower = next(item for item in plan.items if "p010b0004" in item.source_ids)

        self.assertLessEqual(lower.bbox[1] - upper.bbox[3], 4.0)

    def test_page7_consensus_body_is_not_misclassified_as_code_image(self):
        plan = self.plan_for_page(7)
        image_ids = {source_id for item in plan.items if item.kind == "original_image_clip" for source_id in item.source_ids}
        translated_ids = {source_id for item in plan.items if item.kind == "translated_text" for source_id in item.source_ids}

        self.assertNotIn("p007b0006", image_ids)
        self.assertIn("p007b0006", translated_ids)

    def test_ocr_merged_text_over_heading_does_not_force_large_english_clip(self):
        page18 = self.plan_for_page(18)
        image_ids = {source_id for item in page18.items if item.kind == "original_image_clip" for source_id in item.source_ids}
        translated_ids = {source_id for item in page18.items if item.kind == "translated_text" for source_id in item.source_ids}

        self.assertNotIn("p018b0002", image_ids)
        self.assertIn("p018b0002", translated_ids)

    def test_ocr_fragments_inside_large_body_are_merged_to_avoid_overlap(self):
        page18 = self.plan_for_page(18)
        errors = pdf.validate_plan_text_overlaps(page18)
        translated_ids = {source_id for item in page18.items if item.kind == "translated_text" for source_id in item.source_ids}

        self.assertFalse(any("p018b0002" in error for error in errors))
        self.assertIn("p018b0006", translated_ids)

    def test_page21_large_body_is_not_forced_to_english_image_by_inline_number(self):
        plan = self.plan_for_page(21)
        image_ids = {source_id for item in plan.items if item.kind == "original_image_clip" for source_id in item.source_ids}
        translated_ids = {source_id for item in plan.items if item.kind == "translated_text" for source_id in item.source_ids}

        self.assertNotIn("p021b0002", image_ids)
        self.assertIn("p021b0002", translated_ids)

    def test_page21_proof_text_is_not_dropped_with_footer_block(self):
        plan = self.plan_for_page(21)
        translated_items = [item for item in plan.items if "p021b0006" in item.source_ids]
        translated_text = "\n".join(item.text for item in translated_items)

        self.assertTrue(translated_items)
        self.assertIn("若 |concur(P)| > n", translated_text)
        self.assertNotIn("ACM Transactions on Programming Languages and Systems", translated_text)

    def test_page6_implementation_body_is_not_forced_to_english_image_by_formula(self):
        plan = self.plan_for_page(6)
        image_ids = {source_id for item in plan.items if item.kind == "original_image_clip" for source_id in item.source_ids}
        translated_ids = {source_id for item in plan.items if item.kind == "translated_text" for source_id in item.source_ids}
        layout_errors = pdf.validate_plan_layout(plan, (623, 801))
        quality_errors = pdf.validate_plan_quality(6, self.pages[5], self.translations, plan)

        self.assertNotIn("p006b0004", image_ids)
        self.assertIn("p006b0004", translated_ids)
        self.assertIn("p006b0007", image_ids)
        self.assertEqual(layout_errors, [])
        self.assertEqual(quality_errors, [])

    def test_page6_decorated_ocr_page_number_does_not_leak_into_body_text(self):
        plan = self.plan_for_page(6)
        rendered_text = "\n".join(item.text for item in plan.items if item.kind == "translated_text")

        self.assertNotIn("¥", rendered_text)
        self.assertNotIn("129\n无等待", rendered_text)

    def test_page11_body_is_not_forced_to_english_image_by_formula(self):
        plan = self.plan_for_page(11)
        image_ids = {source_id for item in plan.items if item.kind == "original_image_clip" for source_id in item.source_ids}
        translated_ids = {source_id for item in plan.items if item.kind == "translated_text" for source_id in item.source_ids}
        layout_errors = pdf.validate_plan_layout(plan, (623, 801))
        quality_errors = pdf.validate_plan_quality(11, self.pages[10], self.translations, plan)

        self.assertNotIn("p011b0006", image_ids)
        self.assertIn("p011b0006", translated_ids)
        self.assertIn("p011b0007", image_ids)
        self.assertEqual(layout_errors, [])
        self.assertEqual(quality_errors, [])

    def test_page12_mixed_code_and_body_block_keeps_code_image_and_renders_body_text(self):
        bbox_lines = [
            line
            for line in pdf.parse_bbox_lines(JOB / "source_bbox.html")
            if line["page"] == 12
        ]
        plan = pdf.build_page_render_plan(
            12,
            self.pages[11],
            self.translations,
            page_size=(623, 801),
            bbox_lines=bbox_lines,
            source_image_path=JOB / "pages" / "page-012.png",
        )
        body_items = [
            item
            for item in plan.items
            if item.kind == "translated_text" and "p012b0004" in item.source_ids
        ]

        self.assertTrue(body_items)
        self.assertIn("另一个经典原语是 compare&swap", "\n".join(item.text for item in body_items))
        self.assertEqual(pdf.validate_plan_layout(plan, (623, 801)), [])
        self.assertEqual(pdf.validate_plan_text_fit(plan), [])

    def test_page13_algorithm_prefix_is_image_only_not_body_text(self):
        bbox_lines = [
            line
            for line in pdf.parse_bbox_lines(JOB / "source_bbox.html")
            if line["page"] == 13
        ]
        plan = pdf.build_page_render_plan(
            13,
            self.pages[12],
            self.translations,
            page_size=(623, 801),
            bbox_lines=bbox_lines,
            source_image_path=JOB / "pages" / "page-013.png",
        )
        body_text = "\n".join(
            item.text
            for item in plan.items
            if item.kind == "translated_text" and "p013b0002" in item.source_ids
        )
        image_ids = {source_id for item in plan.items if item.kind == "original_image_clip" for source_id in item.source_ids}

        self.assertIn("p013b0003", image_ids)
        self.assertIn("该协议是 wait-free", body_text)
        self.assertNotIn("decide(input", body_text)
        self.assertNotIn("then return prefer", body_text)
        self.assertNotIn("enddecide", body_text)

    def test_page22_duplicate_body_fragment_is_merged_not_overlapped(self):
        plan = self.plan_for_page(22)
        translated_ids = {source_id for item in plan.items if item.kind == "translated_text" for source_id in item.source_ids}
        layout_errors = pdf.validate_plan_layout(plan, (623, 801))
        quality_errors = pdf.validate_plan_quality(22, self.pages[21], self.translations, plan)

        self.assertIn("p022b0015", translated_ids)
        self.assertIn("p022b0016", translated_ids)
        self.assertFalse(any("p022b0015" in error and "p022b0016" in error for error in quality_errors))
        self.assertEqual(layout_errors, [])

    def test_page22_proof_body_is_not_forced_to_english_image_by_formula(self):
        plan = self.plan_for_page(22)
        image_ids = {source_id for item in plan.items if item.kind == "original_image_clip" for source_id in item.source_ids}
        translated_ids = {source_id for item in plan.items if item.kind == "translated_text" for source_id in item.source_ids}
        layout_errors = pdf.validate_plan_layout(plan, (623, 801))
        quality_errors = pdf.validate_plan_quality(22, self.pages[21], self.translations, plan)

        self.assertNotIn("p022b0018", image_ids)
        self.assertIn("p022b0018", translated_ids)
        self.assertIn("p022b0019", image_ids)
        self.assertEqual(layout_errors, [])
        self.assertEqual(quality_errors, [])

    def test_page22_short_formula_clip_does_not_absorb_proof_text_below(self):
        bbox_lines = [
            line
            for line in pdf.parse_bbox_lines(JOB / "source_bbox.html")
            if line["page"] == 22
        ]
        plan = pdf.build_page_render_plan(
            22,
            self.pages[21],
            self.translations,
            page_size=(623, 801),
            bbox_lines=bbox_lines,
            source_image_path=JOB / "pages" / "page-022.png",
        )
        formula_items = [
            item
            for item in plan.items
            if item.kind == "original_image_clip" and "p022b0019" in item.source_ids
        ]

        self.assertEqual(len(formula_items), 1)
        self.assertLess(formula_items[0].bbox[3] - formula_items[0].bbox[1], 25.0)

    def test_page22_source_paragraph_split_does_not_overlap_formula_clip(self):
        bbox_lines = [
            line
            for line in pdf.parse_bbox_lines(JOB / "source_bbox.html")
            if line["page"] == 22
        ]
        plan = pdf.build_page_render_plan(
            22,
            self.pages[21],
            self.translations,
            page_size=(623, 801),
            bbox_lines=bbox_lines,
            source_image_path=JOB / "pages" / "page-022.png",
        )

        self.assertEqual(pdf.validate_plan_layout(plan, (623, 801)), [])
        self.assertEqual(pdf.validate_plan_quality(22, self.pages[21], self.translations, plan), [])

    def test_pages21_22_formula_clips_are_tight_and_do_not_capture_previous_prose(self):
        for page_num, source_id, min_y0 in [(21, "p021b0005", 556.0), (22, "p022b0019", 608.0)]:
            bbox_lines = [
                line
                for line in pdf.parse_bbox_lines(JOB / "source_bbox.html")
                if line["page"] == page_num
            ]
            plan = pdf.build_page_render_plan(
                page_num,
                self.pages[page_num - 1],
                self.translations,
                page_size=(623, 801),
                bbox_lines=bbox_lines,
                source_image_path=JOB / "pages" / f"page-{page_num:03d}.png",
            )
            formula_item = next(
                item
                for item in plan.items
                if item.kind == "original_image_clip" and source_id in item.source_ids
            )

            self.assertGreaterEqual(formula_item.bbox[1], min_y0)
            self.assertLessEqual(formula_item.bbox[3] - formula_item.bbox[1], 16.0)

    def test_page21_lemma_intro_stays_near_its_formula_clip(self):
        bbox_lines = [
            line
            for line in pdf.parse_bbox_lines(JOB / "source_bbox.html")
            if line["page"] == 21
        ]
        plan = pdf.build_page_render_plan(
            21,
            self.pages[20],
            self.translations,
            page_size=(623, 801),
            bbox_lines=bbox_lines,
            source_image_path=JOB / "pages" / "page-021.png",
        )
        lemma_item = next(
            item
            for item in plan.items
            if item.kind == "translated_text" and item.text.startswith("引理 1")
        )
        formula_item = next(
            item
            for item in plan.items
            if item.kind == "original_image_clip" and "p021b0005" in item.source_ids
        )

        self.assertLessEqual(formula_item.bbox[1] - lemma_item.bbox[3], 28.0)

    def test_page17_body_flow_has_no_large_dead_space_between_text_blocks(self):
        bbox_lines = [
            line
            for line in pdf.parse_bbox_lines(JOB / "source_bbox.html")
            if line["page"] == 17
        ]
        plan = pdf.build_page_render_plan(
            17,
            self.pages[16],
            self.translations,
            page_size=(623, 801),
            bbox_lines=bbox_lines,
            source_image_path=JOB / "pages" / "page-017.png",
        )
        body_items = [
            item
            for item in sorted(plan.items, key=lambda candidate: candidate.bbox[1])
            if item.kind == "translated_text" and item.style_name == "body"
        ]
        fitz = pdf.load_fitz()

        self.assertGreaterEqual(len(body_items), 2)
        first = body_items[0]
        first_style = pdf.text_style(first.style_name)
        first_lines = pdf.wrap_mixed_pdf_text(fitz, first.text, first.bbox[2] - first.bbox[0], first.font_size)
        first_preferred = pdf.text_height_for_lines(first_lines, first.font_size, first_style.line_height_factor, first_style.paragraph_spacing)
        visible_gap = body_items[1].bbox[1] - (body_items[0].bbox[1] + first_preferred)

        self.assertLessEqual(visible_gap, 22.0)

    def test_page17_mixed_code_body_tail_does_not_render_code_as_body_text(self):
        bbox_lines = [
            line
            for line in pdf.parse_bbox_lines(JOB / "source_bbox.html")
            if line["page"] == 17
        ]
        plan = pdf.build_page_render_plan(
            17,
            self.pages[16],
            self.translations,
            page_size=(623, 801),
            bbox_lines=bbox_lines,
            source_image_path=JOB / "pages" / "page-017.png",
        )
        body_text = "\n".join(
            item.text
            for item in plan.items
            if item.kind == "translated_text" and "p017b0002" in item.source_ids
        )

        self.assertIn("证明。该协议使用", body_text)
        self.assertNotIn("for Q in 1 .. n", body_text)
        self.assertNotIn("enddecide", body_text)

    def test_page17_mixed_code_body_block_still_enters_translation_batches(self):
        bbox_lines = [
            line
            for line in pdf.parse_bbox_lines(JOB / "source_bbox.html")
            if line["page"] == 17
        ]
        ownership_result = pdf.build_translation_page_components(
            17,
            self.pages[16],
            page_size=(623, 801),
            bbox_lines=bbox_lines,
            source_image_path=JOB / "pages" / "page-017.png",
        )

        self.assertIn("p017b0002", ownership_result.translatable_ids)

    def test_page17_mixed_code_body_tail_has_original_fallback_when_translation_tail_missing(self):
        bbox_lines = [
            line
            for line in pdf.parse_bbox_lines(JOB / "source_bbox.html")
            if line["page"] == 17
        ]
        translations = dict(self.translations)
        translations["p017b0002"] = "decide(input:value)returns(value)\nenddecide"
        plan = pdf.build_page_render_plan(
            17,
            self.pages[16],
            translations,
            page_size=(623, 801),
            bbox_lines=bbox_lines,
            source_image_path=JOB / "pages" / "page-017.png",
        )
        fallback_items = [
            item
            for item in plan.items
            if item.kind == "original_selectable_text"
            and item.fallback_reason == "mixed_visual_body_original"
            and "p017b0002" in item.source_ids
        ]
        ledger_entries = [
            entry
            for entry in plan.ledger
            if entry.block_id == "p017b0002"
            and entry.render_kind == "original_selectable_text"
            and entry.fallback_reason == "mixed_visual_body_original"
        ]

        self.assertTrue(fallback_items)
        self.assertIn("PROOF. The protocol uses", "\n".join(item.text for item in fallback_items))
        self.assertTrue(ledger_entries)
        self.assertEqual(pdf.validate_plan_coverage(17, self.pages[16], plan), [])

    def test_page17_fig13_code_clip_does_not_capture_proof_text(self):
        bbox_lines = [
            line
            for line in pdf.parse_bbox_lines(JOB / "source_bbox.html")
            if line["page"] == 17
        ]
        plan = pdf.build_page_render_plan(
            17,
            self.pages[16],
            self.translations,
            page_size=(623, 801),
            bbox_lines=bbox_lines,
            source_image_path=JOB / "pages" / "page-017.png",
        )
        code_clip = next(
            item
            for item in plan.items
            if item.kind == "original_image_clip" and "p017b0002" in item.source_ids
        )
        proof_item = next(
            item
            for item in plan.items
            if item.kind == "translated_text" and "p017b0002" in item.source_ids
        )

        self.assertLessEqual(code_clip.bbox[3], 170.0)
        self.assertGreaterEqual(proof_item.bbox[1] - code_clip.bbox[3], 4.0)

    def test_page24_conclusions_heading_uses_heading_style(self):
        bbox_lines = [
            line
            for line in pdf.parse_bbox_lines(JOB / "source_bbox.html")
            if line["page"] == 24
        ]
        plan = pdf.build_page_render_plan(
            24,
            self.pages[23],
            self.translations,
            page_size=(623, 801),
            bbox_lines=bbox_lines,
            source_image_path=JOB / "pages" / "page-024.png",
        )
        conclusion_item = next(
            item
            for item in plan.items
            if item.kind == "translated_text" and item.text.strip().startswith("5. 结论")
        )

        self.assertEqual(conclusion_item.style_name, "heading")
        self.assertEqual(conclusion_item.font_size, pdf.DOCUMENT_STYLES["heading"].font_size)

    def test_page22_top_universal_code_is_not_split_as_body_text(self):
        bbox_lines = [
            line
            for line in pdf.parse_bbox_lines(JOB / "source_bbox.html")
            if line["page"] == 22
        ]
        plan = pdf.build_page_render_plan(
            22,
            self.pages[21],
            self.translations,
            page_size=(623, 801),
            bbox_lines=bbox_lines,
            source_image_path=JOB / "pages" / "page-022.png",
        )
        image_ids = {
            source_id
            for item in plan.items
            if item.kind == "original_image_clip"
            for source_id in item.source_ids
        }
        mixed_body_ids = {
            source_id
            for item in plan.items
            if item.fallback_reason == "mixed_visual_body"
            for source_id in item.source_ids
        }

        self.assertIn("p022b0003", image_ids)
        self.assertNotIn("p022b0003", mixed_body_ids)

    def test_waitfree_body_font_size_is_uniform(self):
        font_sizes = []
        for page_num in (4, 6, 11, 21, 22):
            plan = self.plan_for_page(page_num)
            for item in plan.items:
                if item.kind != "translated_text" or not item.source_ids:
                    continue
                entries = [entry for entry in plan.ledger if entry.block_id in item.source_ids]
                if any(entry.classification == "body" for entry in entries):
                    font_sizes.append(item.font_size)

        self.assertTrue(font_sizes)
        self.assertEqual(set(font_sizes), {pdf.BODY_FONT_SIZE})

    def test_pages25_26_references_are_not_translated(self):
        for page_num in (25, 26):
            bbox_lines = [
                line
                for line in pdf.parse_bbox_lines(JOB / "source_bbox.html")
                if line["page"] == page_num
            ]
            plan = pdf.build_page_render_plan(
                page_num,
                self.pages[page_num - 1],
                self.translations,
                page_size=(623, 801),
                bbox_lines=bbox_lines,
            )
            reference_items = [
                item
                for item in plan.items
                if item.kind == "original_selectable_text" and item.fallback_reason == "reference_original"
            ]
            combined = "\n".join(item.text for item in reference_items)

            self.assertNotIn("载于", combined)
            self.assertRegex(combined, r"(REFERENCES|LAMPORT|ANDERSON|HERLIHY)")

    def test_references_are_excluded_from_translation_batches(self):
        batches = pdf.build_batches([self.pages[24], self.pages[25]], max_chars=100000)
        ids = {item["id"] for batch in batches for item in batch}

        self.assertNotIn("p025b0005", ids)
        self.assertNotIn("p025b0006", ids)
        self.assertNotIn("p026b0001", ids)

    def test_page25_references_use_line_level_bboxes_when_available(self):
        bbox_lines = [
            line
            for line in pdf.parse_bbox_lines(JOB / "source_bbox.html")
            if line["page"] == 25
        ]
        plan = pdf.build_page_render_plan(
            25,
            self.pages[24],
            self.translations,
            page_size=(623, 801),
            bbox_lines=bbox_lines,
        )
        reference_items = [
            item
            for item in plan.items
            if item.kind == "original_selectable_text" and item.fallback_reason == "reference_original"
        ]

        self.assertGreater(len(reference_items), 20)
        self.assertLess(len(reference_items), 80)
        self.assertTrue(all(item.bbox[0] > 100 for item in reference_items))

    def test_journal_footers_are_uniform_selectable_text(self):
        for page_num in (1, 3, 4, 25, 26):
            bbox_lines = [
                line
                for line in pdf.parse_bbox_lines(JOB / "source_bbox.html")
                if line["page"] == page_num
            ]
            plan = pdf.build_page_render_plan(
                page_num,
                self.pages[page_num - 1],
                self.translations,
                page_size=(623, 801),
                bbox_lines=bbox_lines,
            )
            footers = [item for item in plan.items if item.fallback_reason == "journal_footer"]
            translated_text = "\n".join(item.text for item in plan.items if item.kind == "translated_text")

            self.assertEqual(len(footers), 1, page_num)
            self.assertEqual(footers[0].kind, "original_selectable_text")
            self.assertEqual(footers[0].font_size, pdf.JOURNAL_FOOTER_FONT_SIZE)
            self.assertEqual(footers[0].bbox, pdf.journal_footer_bbox((623, 801)))
            self.assertIn("ACM Transactions on Programming Languages and Systems", footers[0].text)
            self.assertNotIn("ACM Transactions on Programming Languages and Systems", translated_text)


if __name__ == "__main__":
    unittest.main()
