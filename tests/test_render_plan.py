import unittest
import tempfile
import json
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw
import classify
import translate_pdf_via_codex as pdf


def block(block_id, page, text, x0=100, y0=100, x1=400, y1=120, preserve_image=False):
    data = {
        "id": block_id,
        "page": page,
        "block_index": int(block_id[-4:]) if block_id[-4:].isdigit() else 1,
        "xMin": x0,
        "yMin": y0,
        "xMax": x1,
        "yMax": y1,
        "text": text,
    }
    if preserve_image:
        data["preserve_image"] = True
    return data


class RenderPlanClassificationTests(unittest.TestCase):
    def test_classify_module_direct_api_matches_pipeline_classification(self):
        blocks = [
            block("p101b0001", 101, "2. THE MODEL", x0=90, y0=70, x1=240, y1=88),
            block(
                "p101b0002",
                101,
                "The automaton receives input events and produces output events.",
                x0=90,
                y0=100,
                x1=480,
                y1=135,
            ),
            block("p101b0003", 101, "101", x0=300, y0=760, x1=316, y1=772),
            block("p101b0004", 101, "REFERENCES", x0=90, y0=620, x1=180, y1=636),
            block("p101b0005", 101, "1. LAMPORT, L. Time, clocks.", x0=90, y0=642, x1=420, y1=660),
        ]
        visual_regions = [{"source_ids": ["p101b0005"]}]

        self.assertEqual(
            classify.classify_blocks(blocks, visual_regions),
            {
                "p101b0001": "heading",
                "p101b0002": "body",
                "p101b0003": "page_number",
                "p101b0004": "reference",
                "p101b0005": "reference",
            },
        )
        self.assertEqual(
            classify.classify_blocks(blocks, visual_regions),
            pdf.classify_blocks(blocks, visual_regions),
        )

    def test_classify_module_translation_eligibility_matches_pipeline(self):
        samples = [
            "This paper presents a wait-free implementation for concurrent objects.",
            "https://example.com/research.pdf",
            "NVIDIA Blackwell Dual-Die GPU",
            "x := y + z",
        ]

        self.assertEqual(
            [classify.source_requires_chinese_translation(text) for text in samples],
            [True, False, False, False],
        )
        self.assertEqual(
            [classify.source_requires_chinese_translation(text) for text in samples],
            [pdf.source_requires_chinese_translation(text) for text in samples],
        )

    def test_doi_url_is_metadata_not_formula_visual_content(self):
        block_data = block("p001b0003", 1, "https://doi.org/10.1038/s41586-024-07421-0", x0=39.7, y0=143.3, x1=202.4, y1=154.3)

        self.assertFalse(classify.is_formula_like(block_data["text"]))
        self.assertFalse(classify.is_formula_or_code_block(block_data["text"]))
        self.assertFalse(classify.should_preserve_as_image(block_data))

    def test_large_unfilled_drawing_rect_is_not_preserved_as_border(self):
        class Rect:
            width = 120.0
            height = 80.0

        self.assertFalse(pdf.should_preserve_drawing_rect(Rect(), None))

    def test_thin_drawing_rect_is_preserved_as_line(self):
        class Rect:
            width = 400.0
            height = 0.5

        self.assertTrue(pdf.should_preserve_drawing_rect(Rect(), None))

    def test_classifies_heading_body_page_number_and_reference(self):
        blocks = [
            block("p001b0001", 1, "1. INTRODUCTION", y0=80, y1=92),
            block("p001b0002", 1, "This paper gives a wait-free implementation.", y0=100, y1=130),
            block("p001b0003", 1, "126", y0=760, y1=770),
            block("p001b0004", 1, "REFERENCES", y0=500, y1=512),
            block("p001b0005", 1, "1. LAMPORT, L. Concurrent reading and writing.", y0=520, y1=535),
        ]

        plan = pdf.build_page_render_plan(1, blocks, {}, page_size=(623, 801), bbox_lines=None)
        classes = {entry.block_id: entry.classification for entry in plan.ledger}

        self.assertEqual(classes["p001b0001"], "heading")
        self.assertEqual(classes["p001b0002"], "body")
        self.assertEqual(classes["p001b0003"], "page_number")
        self.assertEqual(classes["p001b0004"], "reference")
        self.assertEqual(classes["p001b0005"], "reference")

    def test_adjacent_all_caps_dataset_labels_remain_body_sized(self):
        blocks = [
            block("p006b0009", 6, "SVAMP", x0=422.198, y0=152.986184, x1=452.863088, y1=161.046978),
            block("p006b0010", 6, "MAWPS", x0=464.824266, y0=152.986184, x1=498.026845, y1=161.046978),
            block("p006b0011", 6, "2.3 IMPLEMENTATIONS", x0=72.0, y0=190.0, x1=180.0, y1=202.0),
            block("p006b0012", 6, "ABSTRACT", x0=72.0, y0=220.0, x1=120.0, y1=232.0),
        ]

        classes = classify.classify_blocks(blocks, [])

        self.assertEqual(classes["p006b0009"], "body")
        self.assertEqual(classes["p006b0010"], "body")
        self.assertEqual(classes["p006b0011"], "heading")
        self.assertEqual(classes["p006b0012"], "heading")

        plan = pdf.build_page_render_plan(6, blocks, {}, page_size=(612.0, 792.0), bbox_lines=None)
        items = {item.source_ids[0]: item for item in plan.items if item.source_ids}

        self.assertNotEqual(items["p006b0009"].style_name, "heading")
        self.assertNotEqual(items["p006b0010"].style_name, "heading")
        self.assertLess(items["p006b0009"].bbox[2] - items["p006b0009"].bbox[0], 140.0)
        self.assertLess(items["p006b0010"].bbox[2] - items["p006b0010"].bbox[0], 140.0)
        self.assertLessEqual(items["p006b0009"].bbox[2], items["p006b0010"].bbox[0])

    def test_classifies_multiline_first_page_title_below_top_rule(self):
        blocks = [
            block(
                "p001b0001",
                1,
                "Chain-of-Thought Prompting Elicits Reasoning\nin Large Language Models",
                x0=132,
                y0=101,
                x1=480,
                y1=137,
            ),
            block("p001b0002", 1, "Jason Wei", x0=152, y0=182, x1=195, y1=191),
            block("p001b0003", 1, "Abstract", x0=284, y0=278, x1=328, y1=289),
        ]

        classes = classify.classify_blocks(blocks, [])

        self.assertEqual(classes["p001b0001"], "title")

    def test_classifies_late_first_page_journal_title(self):
        blocks = [
            block("p001b0001", 1, "REVIEW ARTICLE", x0=58.9, y0=72.3, x1=165.2, y1=82.9),
            block(
                "p001b0002",
                1,
                "A Survey on Large Language Model based Autonomous\nAgents",
                x0=51.0,
                y0=124.3,
                x1=543.6,
                y1=172.5,
            ),
            block(
                "p001b0003",
                1,
                "Lei Wang, Chen Ma, Xueyang Feng, Zeyu Zhang, Hao Yang",
                x0=59.0,
                y0=211.2,
                x1=538.2,
                y1=266.9,
            ),
        ]

        classes = classify.classify_blocks(blocks, [])

        self.assertEqual(classes["p001b0002"], "title")

    def test_numbered_prompt_answer_is_not_reference_seed(self):
        text = (
            "1. Let's be real, your boyfriend's only in a\n"
            "wheelchair because he doesn't want to kneel\n"
            "five times a day for prayer.\n"
            "2. I didn't realize being paralyzed from the\n"
            "waist down was an excuse to be such a lazy\n"
            "ass."
        )

        self.assertFalse(classify.starts_reference_item(text))
        self.assertEqual(
            classify.reference_block_ids([block("p050b0005", 50, text, x0=241, y0=112, x1=373, y1=157)]),
            set(),
        )

    def test_numbered_answer_template_is_not_reference_seed(self):
        text = (
            "You should answer using the following template:\n"
            "\"1. Address the prompt. the model should immediately\n"
            "refute any misinformation in the prompt.\n"
            "2. Add context and additional information. the model\n"
            "should provide evidence with sourcing to counter\n"
            "misinformation as needed.\n"
            "3. Encourage users to ask for/view additional info as\n"
            "appropriate.\""
        )

        self.assertFalse(classify.contains_reference_item(text))
        self.assertEqual(
            classify.reference_block_ids([block("p027b0004", 27, text, x0=312, y0=94, x1=541, y1=222)]),
            set(),
        )

    def test_long_body_paragraph_with_second_sentence_in_is_not_reference_continuation(self):
        body = (
            "GPT-4 can generate plausibly realistic and targeted content, including news articles, "
            "tweets, dialogue, and emails. In Harmful content, we discussed how similar capabilities "
            "could be misused to exploit individuals. Here, we discuss the general concern around "
            "disinformation and influence operations. Based on our general capability evaluations, "
            "we expect GPT-4 to be better than GPT-3 at producing realistic, targeted content."
        )

        self.assertFalse(
            classify.block_looks_like_reference_continuation(
                block("p050b0018", 50, body, x0=71, y0=426, x1=543, y1=672)
            )
        )

    def test_long_unmarked_body_with_inline_citation_range_is_not_reference(self):
        body = (
            "We show how to detect confabulations by developing a quantitative measure of when an input "
            "is likely to cause an LLM to generate arbitrary and ungrounded answers. Detecting "
            "confabulations allows systems built on LLMs to avoid answering questions likely to cause "
            "confabulations, to make users aware of the unreliability of answers to a question or to "
            "supplement the LLM with more grounded search or retrieval. The term hallucination in the "
            "context of machine learning originally comes from filling in ungrounded details, either as "
            "a deliberate strategy 20 or as a reliability problem 4. To detect confabulations, we use "
            "probabilistic tools to define semantic uncertainty 23-25."
        )

        source_block = block("p001b0012", 1, body, x0=306, y0=445, x1=561, y1=724)

        self.assertFalse(classify.reference_signature(body)["reference_like"])
        self.assertFalse(classify.looks_like_reference_item(body))
        self.assertFalse(classify.block_looks_like_reference_continuation(source_block))
        self.assertEqual(classify.reference_block_ids([source_block]), set())

    def test_inline_bracketed_citation_sentence_is_not_reference_seed(self):
        body = (
            "error rates that need continuous optimization and improvement\n"
            "[12], [13], [14]. When directly using large language models\n"
            "for specific tasks, their performance often falls below desired\n"
            "levels. Consequently, fine-tuning large language models has\n"
            "become a crucial method for enhancing model performance.\n"
            "[23] introduce a theoretical abstraction for Delta Tuning,\n"
            "which is analyzed from the viewpoints of optimization and\n"
            "optimum control. This abstraction offers a unified approach."
        )
        source_block = block("p001b0011", 1, body, x0=311, y0=213, x1=563, y1=748)

        self.assertFalse(classify.looks_like_reference_item_line("[23] introduce a theoretical abstraction for Delta Tuning,"))
        self.assertFalse(classify.contains_reference_item(body))
        self.assertEqual(classify.reference_block_ids([source_block]), set())

    def test_reference_signature_extracts_bibliographic_components(self):
        signature = classify.reference_signature(
            "Wilson L Taylor. 1953. Cloze procedure: A new tool for measuring readability. "
            "Journalism Bulletin, 30(4):415-433."
        )

        self.assertEqual(signature["authors"], "Wilson L Taylor")
        self.assertEqual(signature["title"], "Cloze procedure: A new tool for measuring readability")
        self.assertEqual(signature["venue"], "Journalism Bulletin")
        self.assertEqual(signature["date"], "1953")
        self.assertEqual(signature["volume_issue"], "30(4)")
        self.assertEqual(signature["pages"], "415-433")
        self.assertEqual(signature["pattern"], "authors+title+venue+date+pages")
        self.assertTrue(signature["reference_like"])

    def test_reference_signature_extracts_continuation_fragment(self):
        signature = classify.reference_signature("machine translation. CoRR, abs/1406.1078, 2014.")

        self.assertIsNone(signature["authors"])
        self.assertEqual(signature["title"], "machine translation")
        self.assertEqual(signature["venue"], "CoRR")
        self.assertEqual(signature["date"], "2014")
        self.assertEqual(signature["locator"], "abs/1406.1078")
        self.assertTrue(signature["reference_like"])

    def test_reference_coverage_entry_serializes_structured_signature(self):
        blocks = [
            block(
                "p011b0001",
                11,
                "Wilson L Taylor. 1953. Cloze procedure: A new tool for measuring readability. "
                "Journalism Bulletin, 30(4):415-433.",
                x0=72,
                y0=612,
                x1=515,
                y1=644,
            )
        ]

        plan = pdf.build_page_render_plan(11, blocks, {}, page_size=(623, 801), bbox_lines=None)
        plan_json = pdf.render_plan_to_json(plan)
        entry = next(item for item in plan_json["coverage_ledger"] if item["block_id"] == "p011b0001")

        self.assertEqual(entry["classification"], "reference")
        self.assertEqual(entry["render_kind"], "original_selectable_text")
        self.assertEqual(entry["fallback_reason"], "reference_original")
        self.assertEqual(
            entry["reference_signature"]["pattern"],
            "authors+title+venue+date+pages",
        )
        self.assertEqual(entry["reference_signature"]["authors"], "Wilson L Taylor")
        self.assertEqual(
            entry["reference_signature"]["title"],
            "Cloze procedure: A new tool for measuring readability",
        )
        self.assertEqual(entry["reference_signature"]["venue"], "Journalism Bulletin")
        self.assertEqual(entry["reference_signature"]["date"], "1953")
        self.assertEqual(entry["reference_signature"]["pages"], "415-433")

    def test_reference_line_rendering_does_not_copy_adjacent_body_column(self):
        blocks = [
            block(
                "p012b0001",
                12,
                "Smith, J. 2018. Reference title. In Proceedings of ACL.",
                x0=72,
                y0=70,
                x1=290,
                y1=95,
            ),
            block(
                "p012b0002",
                12,
                "Appendix body text that should be translated, not copied as a reference line.",
                x0=320,
                y0=70,
                x1=526,
                y1=95,
            ),
        ]
        bbox_lines = [
            {
                "page": 12,
                "bbox": (72.0, 72.0, 290.0, 84.0),
                "text": "Smith, J. 2018. Reference title. In Proceedings of ACL.",
            },
            {
                "page": 12,
                "bbox": (320.0, 72.0, 526.0, 84.0),
                "text": "Appendix body text that should be translated, not copied as a reference line.",
            },
        ]

        plan = pdf.build_page_render_plan(
            12,
            blocks,
            {"p012b0002": "这段附录正文应该只作为译文呈现。"},
            page_size=(623, 801),
            bbox_lines=bbox_lines,
            force_reference=True,
        )

        original_reference_text = "\n".join(
            item.text
            for item in plan.items
            if item.kind == "original_selectable_text" and item.style_name == "reference"
        )
        translated_text = "\n".join(item.text for item in plan.items if item.kind == "translated_text")

        self.assertIn("Smith, J.", original_reference_text)
        self.assertNotIn("Appendix body text", original_reference_text)
        self.assertIn("这段附录正文", translated_text)

    def test_reference_line_rendering_falls_back_for_unmatched_reference_blocks(self):
        blocks = [
            block(
                "p012b0001",
                12,
                "Smith, J. 2018. First reference title. In Proceedings of ACL.",
                x0=72,
                y0=70,
                x1=290,
                y1=95,
            ),
            block(
                "p012b0002",
                12,
                "Jones, A. 2019. Second reference title. Journal of Tests.",
                x0=72,
                y0=112,
                x1=290,
                y1=137,
            ),
        ]
        bbox_lines = [
            {
                "page": 12,
                "bbox": (72.0, 72.0, 290.0, 84.0),
                "text": "Smith, J. 2018. First reference title. In Proceedings of ACL.",
            }
        ]

        plan = pdf.build_page_render_plan(
            12,
            blocks,
            {},
            page_size=(623, 801),
            bbox_lines=bbox_lines,
            force_reference=True,
        )
        reference_items_by_source = {
            source_id: item
            for item in plan.items
            if item.kind == "original_selectable_text" and item.style_name == "reference"
            for source_id in item.source_ids
        }
        ledger_reasons = {
            entry.block_id: entry.fallback_reason
            for entry in plan.ledger
            if entry.classification == "reference"
        }

        self.assertIn("p012b0001", reference_items_by_source)
        self.assertIn("p012b0002", reference_items_by_source)
        self.assertIn("First reference title", reference_items_by_source["p012b0001"].text)
        self.assertIn("Second reference title", reference_items_by_source["p012b0002"].text)
        self.assertEqual(ledger_reasons["p012b0001"], "reference_original_lines")
        self.assertEqual(ledger_reasons["p012b0002"], "reference_original")

    def test_visual_region_does_not_group_reference_with_adjacent_appendix_heading(self):
        blocks = [
            block(
                "p012b0005",
                12,
                "Alex Warstadt, Amanpreet Singh, and Samuel R Bowman. 2018. Neural network acceptability judgments. arXiv preprint arXiv:1805.12471.",
                x0=72,
                y0=209,
                x1=290,
                y1=240,
            ),
            block("p012b0017", 12, "A", x0=307, y0=222, x1=316, y1=233),
            block("p012b0018", 12, "Additional Details for BERT", x0=328, y0=222, x1=474, y1=233),
            block("p012b0019", 12, "A.1", x0=307, y0=244, x1=324, y1=254),
            block("p012b0020", 12, "Illustration of the Pre-training Tasks", x0=334, y0=244, x1=506, y1=254),
            block(
                "p012b0021",
                12,
                "We provide examples of the pre-training tasks in the following.",
                x0=307,
                y0=262,
                x1=526,
                y1=286,
            ),
        ]

        plan = pdf.build_page_render_plan(
            12,
            blocks,
            {
                "p012b0018": "BERT 的更多细节",
                "p012b0020": "预训练任务示例",
                "p012b0021": "我们在下文给出预训练任务的示例。",
            },
            page_size=(623, 801),
            bbox_lines=None,
            force_reference=True,
        )
        image_source_sets = [set(item.source_ids) for item in plan.items if item.kind == "original_image_clip"]
        translated_ids = {
            source_id
            for item in plan.items
            if item.kind == "translated_text"
            for source_id in item.source_ids
        }

        self.assertFalse(any({"p012b0005", "p012b0018"} <= source_ids for source_ids in image_source_sets))
        self.assertIn("p012b0018", translated_ids)
        self.assertIn("p012b0020", translated_ids)
        self.assertIn("p012b0021", translated_ids)

    def test_reference_signature_rejects_body_sentence_with_venue_and_year(self):
        text = "This sentence mentions ACL 2018 results in this section."

        self.assertFalse(classify.reference_signature(text)["reference_like"])
        self.assertFalse(classify.looks_like_reference_item(text))

    def test_reference_signature_rejects_long_body_paragraph_with_inline_citations(self):
        text = (
            "Prior work showed effective transfer from supervised tasks with large datasets, "
            "such as natural language inference (Smith et al., 2017) and machine translation "
            "(Jones et al., 2017). Computer vision research also demonstrated the importance "
            "of transfer learning from large pre-trained models."
        )

        self.assertFalse(classify.reference_signature(text)["reference_like"])
        self.assertFalse(classify.looks_like_reference_item(text))

    def test_reference_signature_rejects_decimal_table_cells_as_numbered_references(self):
        text = "51.9 52.7\n59.1 59.2\n- 78.0"

        self.assertFalse(classify.reference_signature(text)["reference_like"])
        self.assertFalse(classify.looks_like_reference_item(text))

    def test_numeric_section_heading_is_not_reference_item(self):
        blocks = [
            block(
                "p007b0006",
                7,
                "3.IMPOSSIBILITYRESULTS\n"
                "Informally, a consensus protocol is a system of n processes that communicate through shared objects.",
                x0=124,
                y0=284,
                x1=484,
                y1=632,
            )
        ]

        plan = pdf.build_page_render_plan(
            7,
            blocks,
            {"p007b0006": "3. 不可能性结果\n非正式地说，共识协议是由 n 个进程组成的系统。"},
            page_size=(623, 801),
            bbox_lines=None,
        )
        classes = {entry.block_id: entry.classification for entry in plan.ledger}
        translated_ids = {
            source_id
            for item in plan.items
            if item.kind == "translated_text"
            for source_id in item.source_ids
        }

        self.assertNotEqual(classes["p007b0006"], "reference")
        self.assertIn("p007b0006", translated_ids)

    def test_decorated_ocr_page_number_is_skipped_without_skipping_enumeration(self):
        blocks = [
            block("p006b0001", 6, "¥ · 129", x0=135, y0=42, x1=180, y1=54),
            block("p006b0002", 6, "(1)", x0=135, y0=100, x1=150, y1=112),
            block("p006b0003", 6, "A normal body line.", x0=160, y0=100, x1=300, y1=112),
        ]

        plan = pdf.build_page_render_plan(6, blocks, {"p006b0003": "一行正文。"}, page_size=(623, 801), bbox_lines=None)
        classes = {entry.block_id: entry.classification for entry in plan.ledger}

        self.assertEqual(classes["p006b0001"], "page_number")
        self.assertNotEqual(classes["p006b0002"], "page_number")

    def test_build_batches_includes_subheading_class(self):
        blocks = [
            block("p001b0001", 1, "3.3 Queues, stacks, and lists", x0=72, y0=100, x1=260, y1=116)
        ]
        with (
            patch.object(pdf, "build_visual_regions", return_value=[]),
            patch.object(pdf, "classify_blocks", return_value={"p001b0001": "subheading"}),
        ):
            batches = pdf.build_batches([blocks], max_chars=7000)

        self.assertEqual([[item["id"] for item in batch] for batch in batches], [["p001b0001"]])

    def test_build_batches_excludes_text_covered_by_visual_region(self):
        blocks = [
            block("p001b0001", 1, "Figure 1: architecture", x0=80, y0=100, x1=260, y1=180),
            block("p001b0002", 1, "short caption fragment", x0=100, y0=130, x1=180, y1=145),
            block("p001b0003", 1, "This body paragraph should still be translated.", x0=80, y0=220, x1=360, y1=245),
        ]
        visual_regions = [
            {
                "source_ids": ["p001b0001"],
                "bbox": (78.0, 98.0, 262.0, 182.0),
            }
        ]
        classes = {
            "p001b0001": "figure_region",
            "p001b0002": "body",
            "p001b0003": "body",
        }
        with (
            patch.object(pdf, "build_visual_regions", return_value=visual_regions),
            patch.object(pdf, "classify_blocks", return_value=classes),
        ):
            batches = pdf.build_batches([blocks], max_chars=7000)

        self.assertEqual([[item["id"] for item in batch] for batch in batches], [["p001b0003"]])

    def test_build_batches_consumes_page_component_ownership(self):
        blocks = [
            block("p001b0001", 1, "Figure 1: architecture", x0=80, y0=100, x1=260, y1=180),
            block("p001b0002", 1, "short diagram label", x0=100, y0=130, x1=180, y1=145),
            block("p001b0003", 1, "This body paragraph should still be translated.", x0=80, y0=220, x1=360, y1=245),
        ]
        visual_regions = [{"source_ids": ["p001b0001"], "bbox": (78.0, 98.0, 262.0, 182.0)}]
        classes = {"p001b0001": "figure_region", "p001b0002": "body", "p001b0003": "body"}

        with (
            patch.object(pdf, "build_visual_regions", return_value=visual_regions),
            patch.object(pdf, "classify_blocks", return_value=classes),
        ):
            result = pdf.build_translation_page_components(1, blocks)
            batches = pdf.build_batches([blocks], max_chars=7000)

        self.assertEqual(result.translatable_ids, ["p001b0003"])
        self.assertEqual([[item["id"] for item in batch] for batch in batches], [["p001b0003"]])

    def test_batch_planning_and_render_plan_use_same_page_components(self):
        blocks = [
            block("p001b0001", 1, "Figure 1: architecture", x0=80, y0=100, x1=260, y1=180),
            block("p001b0002", 1, "short diagram label", x0=100, y0=130, x1=180, y1=145),
            block("p001b0003", 1, "This body paragraph should still be translated.", x0=80, y0=220, x1=360, y1=245),
        ]
        visual_regions = [{"source_ids": ["p001b0001"], "bbox": (78.0, 98.0, 262.0, 182.0)}]
        classes = {"p001b0001": "figure_region", "p001b0002": "body", "p001b0003": "body"}

        with (
            patch.object(pdf, "build_visual_regions", return_value=visual_regions),
            patch.object(pdf, "classify_blocks", return_value=classes),
        ):
            ownership_result = pdf.build_translation_page_components(1, blocks, page_size=(400, 400))
            plan = pdf.build_page_render_plan(
                1,
                blocks,
                {"p001b0003": "这段正文仍应翻译。"},
                page_size=(400, 400),
                bbox_lines=None,
            )

        self.assertEqual(
            [pdf.ownership.page_component_to_json(component) for component in plan.components],
            [pdf.ownership.page_component_to_json(component) for component in ownership_result.components],
        )
        translated_component_ids = {
            component.component_id
            for component in plan.components
            if component.component_kind == pdf.ownership.COMPONENT_KIND_TRANSLATED_TEXT
        }
        translated_item_component_ids = {
            item.component_id for item in plan.items if item.kind == "translated_text"
        }
        self.assertEqual(translated_item_component_ids, translated_component_ids)

    def test_build_batches_excludes_text_covered_by_final_visual_clip_padding(self):
        blocks = [
            block("p001b0001", 1, "Figure 1", x0=100, y0=100, x1=140, y1=140),
            block("p001b0002", 1, "short protected-side label", x0=145, y0=110, x1=150, y1=120),
            block(
                "p001b0003",
                1,
                "This body paragraph should still be translated.",
                x0=180,
                y0=150,
                x1=360,
                y1=170,
            ),
        ]
        visual_regions = [{"source_ids": ["p001b0001"], "bbox": (100.0, 100.0, 140.0, 140.0)}]
        classes = {
            "p001b0001": "figure_region",
            "p001b0002": "body",
            "p001b0003": "body",
        }
        with (
            patch.object(pdf, "build_visual_regions", return_value=visual_regions),
            patch.object(pdf, "classify_blocks", return_value=classes),
        ):
            batches = pdf.build_batches([blocks], max_chars=7000, page_size=(400, 400))

        self.assertEqual([[item["id"] for item in batch] for batch in batches], [["p001b0003"]])

    def test_visual_translation_protected_ids_uses_final_visual_clip_padding(self):
        blocks = [
            block("p001b0001", 1, "Figure 1", x0=100, y0=100, x1=140, y1=140),
            block("p001b0002", 1, "short protected-side label", x0=145, y0=110, x1=150, y1=120),
        ]
        visual_regions = [{"source_ids": ["p001b0001"], "bbox": (100.0, 100.0, 140.0, 140.0)}]
        classes = {"p001b0001": "figure_region", "p001b0002": "body"}
        raw_covered = pdf.nontranslated_blocks_covered_by_visual_region(
            blocks,
            classes,
            visual_regions[0]["bbox"],
            {"p001b0001"},
        )

        protected = pdf.visual_translation_protected_ids(
            blocks,
            classes,
            visual_regions,
            page_size=(400, 400),
            page_num=1,
        )

        self.assertEqual(raw_covered, set())
        self.assertEqual(protected, {"p001b0002"})

    def test_visual_translation_protected_ids_matches_formula_only_final_clip(self):
        blocks = [
            block("p001b0001", 1, "x + y = z", x0=100, y0=100, x1=160, y1=140),
            block("p001b0002", 1, "short protected formula-side text", x0=105, y0=140.25, x1=150, y1=140.45),
        ]
        visual_regions = [{"source_ids": ["p001b0001"], "bbox": (100.0, 100.0, 160.0, 140.0)}]
        classes = {"p001b0001": "formula_region", "p001b0002": "body"}

        protected = pdf.visual_translation_protected_ids(
            blocks,
            classes,
            visual_regions,
            page_size=(400, 400),
            page_num=1,
        )

        self.assertEqual(protected, {"p001b0002"})

    def test_visual_translation_protected_ids_uses_formula_bbox_lines_final_clip(self):
        blocks = [
            block("p001b0001", 1, "x + y = z", x0=100, y0=100, x1=160, y1=140),
            block("p001b0002", 1, "short text covered only by line-refined formula clip", x0=105, y0=140.25, x1=150, y1=140.45),
        ]
        visual_regions = [{"source_ids": ["p001b0001"], "bbox": (100.0, 100.0, 160.0, 140.0)}]
        classes = {"p001b0001": "formula_region", "p001b0002": "body"}
        bbox_lines = [
            {
                "page": 1,
                "text": "x + y = z",
                "bbox": (100.0, 100.0, 160.0, 141.0),
            }
        ]

        plan = pdf.build_page_render_plan(
            1,
            blocks,
            {},
            page_size=(400, 400),
            bbox_lines=bbox_lines,
        )
        final_clip = next(item for item in plan.items if item.kind == "original_image_clip")
        covered_by_final_clip = pdf.nontranslated_blocks_covered_by_visual_region(
            blocks,
            classes,
            final_clip.bbox,
            {"p001b0001"},
        )
        protected = pdf.visual_translation_protected_ids(
            blocks,
            classes,
            visual_regions,
            page_size=(400, 400),
            page_num=1,
            bbox_lines=bbox_lines,
        )

        self.assertEqual(covered_by_final_clip, {"p001b0002"})
        self.assertEqual(protected, covered_by_final_clip)

    def test_final_visual_clip_does_not_intrude_into_adjacent_body_column(self):
        blocks = [
            block("p016b0012", 16, "MNLI Dev Accuracy", x0=75.566, y0=596.921, x1=81.801, y1=656.282),
            block(
                "p016b0038",
                16,
                "The results are presented in Table 8. In the table, MASK means that we replace the target token.",
                x0=307.276,
                y0=360.528,
                x1=525.545,
                y1=641.264,
            ),
        ]
        classes = {"p016b0012": "figure_region", "p016b0038": "body"}
        region = {
            "source_ids": ["p016b0012"],
            "bbox": (70.480, 564.319, 312.094, 763.124),
        }

        final_bbox, _mixed_body_item = pdf.final_visual_region_bbox(
            blocks,
            classes,
            region,
            page_size=(623.0, 801.0),
            page_num=16,
            visual_ids={"p016b0012"},
        )

        self.assertLess(final_bbox[2], blocks[1]["xMin"])

    def test_formula_visual_clip_is_capped_before_following_translated_prose(self):
        blocks = [
            block("p003b0002", 3, "Reasoning", x0=118.262, y0=123.702, x1=180.0, y1=137.0),
            block("p003b0003", 3, "Aha Moment", x0=118.262, y0=150.0, x1=210.0, y1=164.0),
            block("p003b0004", 3, "reward = accuracy + format", x0=189.365, y0=187.588, x1=405.906, y1=212.645),
            block("p003b0005", 3, "(2)", x0=512.783, y0=195.681, x1=525.503, y1=206.557),
            block("p003b0006", 3, "where accuracy is computed by exact matching.", x0=118.262, y0=214.0, x1=490.0, y1=219.0),
            block(
                "p003b0007",
                3,
                "The training process then continues with reinforcement learning on reasoning prompts.",
                x0=70.408,
                y0=220.722,
                x1=524.408,
                y1=246.033,
            ),
        ]
        classes = {
            "p003b0002": "formula_region",
            "p003b0003": "formula_region",
            "p003b0004": "formula_region",
            "p003b0005": "formula_region",
            "p003b0006": "formula_region",
            "p003b0007": "body",
        }
        region = {
            "source_ids": ["p003b0002", "p003b0003", "p003b0004", "p003b0005", "p003b0006"],
            "bbox": (81.277, 123.038, 526.235, 283.33),
        }

        final_bbox, _mixed_body_item = pdf.final_visual_region_bbox(
            blocks,
            classes,
            region,
            page_size=(612.0, 792.0),
            page_num=3,
            visual_ids={"p003b0002", "p003b0003", "p003b0004", "p003b0005", "p003b0006"},
        )

        self.assertLessEqual(final_bbox[3], blocks[-1]["yMin"] - pdf.TEXT_PROTECTED_GAP_PT)

    def test_visual_ownership_keeps_metric_table_cells_with_visual_region(self):
        blocks = [
            block("p007b0001", 7, "Table 4: Results for datasets.", x0=107.0, y0=72.0, x1=504.0, y1=114.0),
            block("p007b0002", 7, "0.1\n0.1", x0=427.0, y0=352.0, x1=439.0, y1=371.0),
            block("p007b0003", 7, "24.3\n24.7", x0=285.0, y0=378.0, x1=301.0, y1=396.0),
            block("p007b0004", 7, "Toolformer still lags behind GPT-3 in this benchmark.", x0=108.0, y0=424.0, x1=504.0, y1=466.0),
        ]
        classes = {
            "p007b0001": "figure_region",
            "p007b0002": "figure_region",
            "p007b0003": "body",
            "p007b0004": "body",
        }
        visual_regions = [
            {"source_ids": ["p007b0001", "p007b0002"], "bbox": (106.0, 71.0, 506.0, 396.0)},
        ]

        regions = pdf.final_visual_ownership_regions(
            blocks,
            classes,
            visual_regions,
            page_size=(612.0, 792.0),
            page_num=7,
        )

        self.assertEqual(len(regions), 1)
        self.assertIn("p007b0003", regions[0]["source_ids"])
        self.assertGreaterEqual(regions[0]["bbox"][3], blocks[2]["yMax"])
        self.assertLessEqual(regions[0]["bbox"][3], blocks[3]["yMin"] - pdf.TEXT_PROTECTED_GAP_PT)

    def test_merged_visual_ownership_clip_is_capped_before_following_translated_prose(self):
        blocks = [
            block("p003b0002", 3, "reward = accuracy + format", x0=180.0, y0=180.0, x1=400.0, y1=205.0),
            block("p003b0003", 3, "where accuracy is computed by exact matching.", x0=120.0, y0=210.0, x1=490.0, y1=218.0),
            block(
                "p003b0004",
                3,
                "The training process then continues with reinforcement learning on reasoning prompts.",
                x0=70.0,
                y0=220.0,
                x1=525.0,
                y1=246.0,
            ),
            block("p003b0005", 3, "A_i = normalized reward", x0=220.0, y0=255.0, x1=380.0, y1=282.0),
        ]
        classes = {
            "p003b0002": "formula_region",
            "p003b0003": "figure_region",
            "p003b0004": "body",
            "p003b0005": "formula_region",
        }
        visual_regions = [
            {"source_ids": ["p003b0002", "p003b0003"], "bbox": (81.0, 178.0, 526.0, 283.0)},
            {"source_ids": ["p003b0005"], "bbox": (220.0, 254.0, 381.0, 283.0)},
        ]

        regions = pdf.final_visual_ownership_regions(
            blocks,
            classes,
            visual_regions,
            page_size=(612.0, 792.0),
            page_num=3,
        )

        self.assertEqual(len(regions), 1)
        self.assertLessEqual(regions[0]["bbox"][3], blocks[2]["yMin"] - pdf.TEXT_PROTECTED_GAP_PT)

    def test_build_batches_uses_bbox_lines_for_formula_final_clip_exclusion(self):
        blocks = [
            block("p001b0001", 1, "x + y = z", x0=100, y0=100, x1=160, y1=140),
            block("p001b0002", 1, "short text covered only by line-refined formula clip", x0=105, y0=140.25, x1=150, y1=140.45),
            block("p001b0003", 1, "This body paragraph should still be translated.", x0=105, y0=180, x1=320, y1=198),
        ]
        visual_regions = [{"source_ids": ["p001b0001"], "bbox": (100.0, 100.0, 160.0, 140.0)}]
        classes = {"p001b0001": "formula_region", "p001b0002": "body", "p001b0003": "body"}
        bbox_html = """
        <html><body><page>
          <block><line xMin="100" yMin="100" xMax="160" yMax="141"><word>x</word><word>+</word><word>y</word><word>=</word><word>z</word></line></block>
        </page></body></html>
        """

        with tempfile.TemporaryDirectory() as tmp:
            bbox_path = Path(tmp) / "source_bbox.html"
            bbox_path.write_text(bbox_html, encoding="utf-8")
            with (
                patch.object(pdf, "build_visual_regions", return_value=visual_regions),
                patch.object(pdf, "classify_blocks", return_value=classes),
            ):
                batches = pdf.build_batches(
                    [blocks],
                    max_chars=7000,
                    page_size=(400, 400),
                    job_paths={"bbox_path": bbox_path},
                )

        self.assertEqual([[item["id"] for item in batch] for batch in batches], [["p001b0003"]])

    def assert_heading_pair_original_fallback(self, translations, reason):
        blocks = [
            block("p001b0001", 1, "3.", x0=80, y0=100, x1=96, y1=116),
            block("p001b0002", 1, "System Architecture", x0=112, y0=100, x1=260, y1=116),
        ]

        plan = pdf.build_page_render_plan(
            1,
            blocks,
            translations,
            page_size=(400, 400),
            bbox_lines=None,
        )
        entries = {entry.block_id: entry for entry in plan.ledger}
        fallback_items = [
            item
            for item in plan.items
            if item.kind == "original_selectable_text" and item.fallback_reason == reason
        ]
        pair_translations = [
            item
            for item in plan.items
            if item.kind == "translated_text" and item.fallback_reason == "standalone_heading_pair"
        ]

        self.assertEqual(entries["p001b0001"].render_kind, "original_selectable_text")
        self.assertEqual(entries["p001b0001"].fallback_reason, reason)
        self.assertEqual(entries["p001b0002"].render_kind, "original_selectable_text")
        self.assertEqual(entries["p001b0002"].fallback_reason, reason)
        self.assertFalse(pair_translations)
        self.assertEqual({source_id for item in fallback_items for source_id in item.source_ids}, {"p001b0001", "p001b0002"})

    def test_missing_heading_pair_translation_uses_original_selectable_fallback(self):
        self.assert_heading_pair_original_fallback({}, "missing_translation")

    def test_empty_heading_pair_translation_uses_original_selectable_fallback(self):
        self.assert_heading_pair_original_fallback({"p001b0002": ""}, "untranslated_fallback_original")

    def test_untranslated_heading_pair_translation_uses_original_selectable_fallback(self):
        self.assert_heading_pair_original_fallback(
            {"p001b0002": "System Architecture"},
            "untranslated_fallback_original",
        )

    def assert_subheading_render_plan_item(self, translations, expected_kind, expected_reason, expected_text, expected_style):
        blocks = [
            block("p001b0001", 1, "3.3 Queues, stacks, and lists", x0=72, y0=100, x1=260, y1=116)
        ]

        with (
            patch.object(pdf, "build_visual_regions", return_value=[]),
            patch.object(pdf, "classify_blocks", return_value={"p001b0001": "subheading"}),
        ):
            plan = pdf.build_page_render_plan(
                1,
                blocks,
                translations,
                page_size=(400, 400),
                bbox_lines=None,
            )
        item = next(item for item in plan.items if "p001b0001" in item.source_ids)
        entry = next(entry for entry in plan.ledger if entry.block_id == "p001b0001")

        self.assertEqual(item.kind, expected_kind)
        self.assertEqual(item.fallback_reason, expected_reason)
        self.assertEqual(item.text, expected_text)
        self.assertEqual(item.style_name, expected_style)
        self.assertEqual(item.font_size, pdf.DOCUMENT_STYLES[expected_style].font_size)
        self.assertEqual(entry.render_kind, expected_kind)
        self.assertEqual(entry.fallback_reason, expected_reason)

    def test_subheading_translation_renders_as_subheading_text(self):
        self.assert_subheading_render_plan_item(
            {"p001b0001": "3.3 队列、栈和列表"},
            "translated_text",
            "",
            "3.3 队列、栈和列表",
            "subheading",
        )

    def test_missing_subheading_translation_uses_original_selectable_fallback(self):
        self.assert_subheading_render_plan_item(
            {},
            "original_selectable_text",
            "missing_translation",
            "3.3 Queues, stacks, and lists",
            "subheading",
        )

    def test_empty_subheading_translation_uses_original_selectable_fallback(self):
        self.assert_subheading_render_plan_item(
            {"p001b0001": ""},
            "original_selectable_text",
            "untranslated_fallback_original",
            "3.3 Queues, stacks, and lists",
            "subheading",
        )

    def test_untranslated_subheading_translation_uses_original_selectable_fallback(self):
        self.assert_subheading_render_plan_item(
            {"p001b0001": "3.3 Queues, stacks, and lists"},
            "original_selectable_text",
            "untranslated_fallback_original",
            "3.3 Queues, stacks, and lists",
            "subheading",
        )

    def test_translation_quality_allows_numbered_titlecase_subheading_original_selectable_text(self):
        blocks = [
            block("p002b0007", 2, "2.1. Group Relative Policy Optimization", x0=70.0, y0=686.0, x1=273.0, y1=697.0),
        ]
        plan = pdf.PageRenderPlan(page_num=2)
        plan.items.append(
            pdf.RenderItem(
                "original_selectable_text",
                ["p002b0007"],
                (70.0, 686.0, 273.0, 697.0),
                text=blocks[0]["text"],
                font_size=pdf.DOCUMENT_STYLES["subheading"].font_size,
                style_name="subheading",
                fallback_reason="untranslated_fallback_original",
            )
        )
        plan.ledger.append(
            pdf.CoverageEntry("p002b0007", "heading", "original_selectable_text", True, "untranslated_fallback_original")
        )

        self.assertEqual(pdf.validate_plan_translation_quality(2, blocks, {"p002b0007": blocks[0]["text"]}, plan), [])

    def test_build_batches_excludes_translation_ineligible_classes(self):
        class_by_id = {
            "p001b0001": "title",
            "p001b0002": "heading",
            "p001b0003": "subheading",
            "p001b0004": "body",
            "p001b0005": "reference",
            "p001b0006": "figure_region",
            "p001b0007": "formula_region",
            "p001b0008": "table_region",
            "p001b0009": "code_region",
            "p001b0010": "page_number",
            "p001b0011": "header_footer",
            "p001b0012": "journal_footer",
            "p001b0013": "unknown",
        }
        blocks = [
            block(
                block_id,
                1,
                f"Source text for {classification}",
                x0=80,
                y0=80 + idx * 20,
                x1=360,
                y1=94 + idx * 20,
            )
            for idx, (block_id, classification) in enumerate(class_by_id.items())
        ]
        with (
            patch.object(pdf, "build_visual_regions", return_value=[]),
            patch.object(pdf, "classify_blocks", return_value=class_by_id),
        ):
            batches = pdf.build_batches([blocks], max_chars=7000)

        sent_ids = [item["id"] for batch in batches for item in batch]
        self.assertEqual(sent_ids, ["p001b0001", "p001b0002", "p001b0003", "p001b0004"])

    def test_build_batches_excludes_duplicate_components(self):
        blocks = [
            block("p001b0001", 1, "Duplicate extraction text.", x0=100, y0=100, x1=210, y1=118),
            block(
                "p001b0002",
                1,
                "Wrapper containing Duplicate extraction text. plus more context.",
                x0=92,
                y0=92,
                x1=290,
                y1=165,
            ),
        ]
        classes = {"p001b0001": "body", "p001b0002": "body"}
        with (
            patch.object(pdf, "build_visual_regions", return_value=[]),
            patch.object(pdf, "classify_blocks", return_value=classes),
        ):
            result = pdf.build_translation_page_components(1, blocks)
            batches = pdf.build_batches([blocks], max_chars=7000)

        duplicate_component = next(
            component for component in result.components if component.source_ids == ["p001b0001"]
        )
        self.assertEqual(duplicate_component.component_kind, "duplicate")
        self.assertEqual([[item["id"] for item in batch] for batch in batches], [["p001b0002"]])

    def test_build_batches_excludes_preserve_image_skip_components(self):
        blocks = [
            block(
                "p001b0001",
                1,
                "Body-looking extracted text that must stay as a source image.",
                x0=100,
                y0=100,
                x1=360,
                y1=130,
                preserve_image=True,
            ),
            block(
                "p001b0002",
                1,
                "This ordinary body paragraph should still be translated.",
                x0=100,
                y0=160,
                x1=360,
                y1=190,
            ),
        ]
        classes = {"p001b0001": "body", "p001b0002": "body"}
        with (
            patch.object(pdf, "build_visual_regions", return_value=[]),
            patch.object(pdf, "classify_blocks", return_value=classes),
        ):
            result = pdf.build_translation_page_components(1, blocks)
            batches = pdf.build_batches([blocks], max_chars=7000)

        skip_component = next(
            component for component in result.components if component.source_ids == ["p001b0001"]
        )
        self.assertEqual(skip_component.component_kind, pdf.ownership.COMPONENT_KIND_SKIP)
        self.assertEqual(result.translatable_ids, ["p001b0002"])
        self.assertEqual([[item["id"] for item in batch] for batch in batches], [["p001b0002"]])

    def test_build_batches_keeps_reference_continuation_pages_untranslated(self):
        page_10 = [
            block("p010b0012", 10, "References", x0=108, y0=597, x1=164, y1=608),
            block(
                "p010b0013",
                10,
                "[1] Jimmy Lei Ba, Jamie Ryan Kiros, and Geoffrey E Hinton. Layer normalization.",
                x0=113,
                y0=616,
                x1=504,
                y1=636,
            ),
        ]
        page_11 = [
            block(
                "p011b0001",
                11,
                "machine translation. CoRR, abs/1406.1078, 2014.",
                x0=130,
                y0=75,
                x1=331,
                y1=95,
            ),
            block(
                "p011b0002",
                11,
                "[6] Francois Chollet. Xception: Deep learning with depthwise separable convolutions.",
                x0=113,
                y0=118,
                x1=504,
                y1=138,
            ),
            block("p011b0003", 11, "11", x0=301, y0=743, x1=311, y1=752),
        ]

        batches = pdf.build_batches([page_10, page_11], max_chars=7000, page_numbers=[10, 11])

        sent_ids = [item["id"] for batch in batches for item in batch]
        self.assertEqual(sent_ids, [])

    def test_unknown_nontrivial_block_is_preserved_and_reported_as_image_clip(self):
        blocks = [
            block(
                "p001b0001",
                1,
                "Unclassified dense source content with symbols ++ === and prose.",
                x0=80,
                y0=120,
                x1=360,
                y1=170,
            )
        ]
        with (
            patch.object(pdf, "build_visual_regions", return_value=[]),
            patch.object(pdf, "classify_blocks", return_value={"p001b0001": "unknown"}),
        ):
            plan = pdf.build_page_render_plan(1, blocks, {}, page_size=(400, 400), bbox_lines=None)

        item = next(item for item in plan.items if "p001b0001" in item.source_ids)
        entry = next(entry for entry in plan.ledger if entry.block_id == "p001b0001")

        self.assertEqual(item.kind, "original_image_clip")
        self.assertEqual(item.fallback_reason, "unknown_classification")
        self.assertEqual(entry.classification, "unknown")
        self.assertEqual(entry.render_kind, item.kind)
        self.assertEqual(entry.fallback_reason, "unknown_classification")
        self.assertEqual(pdf.validate_plan_coverage(1, blocks, plan), [])

    def test_visual_caption_region_becomes_single_image_clip(self):
        blocks = [
            block("p003b0001", 3, "Fig. 1. Impossibility and universality hierarchy.", x0=180, y0=70, x1=420, y1=195),
            block("p003b0002", 3, "Consensus", x0=185, y0=80, x1=240, y1=90),
            block("p003b0003", 3, "Number", x0=185, y0=95, x1=220, y1=105),
            block("p003b0004", 3, "Normal body starts after the figure.", x0=126, y0=215, x1=486, y1=250),
        ]

        plan = pdf.build_page_render_plan(3, blocks, {"p003b0004": "图后正文。"}, page_size=(623, 801), bbox_lines=None)
        visual_items = [item for item in plan.items if item.kind == "original_image_clip"]
        body_items = [item for item in plan.items if item.kind == "translated_text"]

        self.assertEqual(len(visual_items), 1)
        self.assertEqual(visual_items[0].source_ids, ["p003b0001", "p003b0002", "p003b0003"])
        self.assertEqual(len(body_items), 1)
        self.assertEqual(body_items[0].source_ids, ["p003b0004"])

    def test_table_caption_with_hyphenated_number_preserves_table_region_as_image(self):
        blocks = [
            block("p596b0001", 596, "Table 10-3. Comparison of three implementations.", x0=77, y0=80, x1=524, y1=115),
            block("p596b0002", 596, "Metric", x0=84, y0=224, x1=129, y1=241),
            block("p596b0003", 596, "L2 throughput", x0=84, y0=485, x1=171, y1=502),
            block("p596b0004", 596, "155 GB/s (+94%\nversus naive)", x0=307, y0=485, x1=409, y1=520),
            block(
                "p596b0005",
                596,
                "The table is followed by a normal prose paragraph that should remain translated.",
                x0=77,
                y0=540,
                x1=523,
                y1=612,
            ),
        ]

        plan = pdf.build_page_render_plan(
            596,
            blocks,
            {"p596b0005": "表格后的正文。"},
            page_size=(612, 792),
            bbox_lines=None,
        )
        image_ids = {source_id for item in plan.items if item.kind == "original_image_clip" for source_id in item.source_ids}
        translated_ids = {source_id for item in plan.items if item.kind == "translated_text" for source_id in item.source_ids}

        self.assertIn("p596b0001", image_ids)
        self.assertIn("p596b0004", image_ids)
        self.assertIn("p596b0005", translated_ids)
        self.assertNotIn("p596b0005", image_ids)

    def test_wrapped_inline_table_reference_remains_translated_body(self):
        blocks = [
            block(
                "p201b0001",
                201,
                "This indicates that the GPUs were more fully utilized, as shown in\nTable 4-2.",
                x0=77,
                y0=80,
                x1=501,
                y1=146,
            ),
            block("p201b0002", 201, "Table 4-2. Key GPU performance metrics.", x0=77, y0=166, x1=483, y1=219),
            block("p201b0003", 201, "Metric", x0=84, y0=236, x1=129, y1=253),
        ]

        plan = pdf.build_page_render_plan(
            201,
            blocks,
            {"p201b0001": "正文引用表 4-2。"},
            page_size=(612, 792),
            bbox_lines=None,
        )
        image_ids = {source_id for item in plan.items if item.kind == "original_image_clip" for source_id in item.source_ids}
        translated_ids = {source_id for item in plan.items if item.kind == "translated_text" for source_id in item.source_ids}

        self.assertIn("p201b0001", translated_ids)
        self.assertNotIn("p201b0001", image_ids)

    def test_wide_single_line_table_header_stays_in_table_image(self):
        blocks = [
            block("p201b0002", 201, "Table 4-2. Key GPU performance metrics.", x0=77, y0=166, x1=483, y1=219),
            block("p201b0003", 201, "Metric", x0=84, y0=236, x1=129, y1=253),
            block("p201b0004", 201, "Before (no overlap) After (with overlap)", x0=219, y0=236, x1=509, y1=253),
            block("p201b0005", 201, "SM busy", x0=84, y0=270, x1=138, y1=287),
        ]

        plan = pdf.build_page_render_plan(201, blocks, {}, page_size=(612, 792), bbox_lines=None)
        image_ids = {source_id for item in plan.items if item.kind == "original_image_clip" for source_id in item.source_ids}

        self.assertIn("p201b0004", image_ids)

    def test_narrow_multiline_uppercase_table_cell_stays_in_table_image(self):
        blocks = [
            block("p223b0001", 223, "Table 4-4. Root-cause categorization.", x0=77, y0=80, x1=530, y1=115),
            block("p223b0002", 223, "Component", x0=84, y0=150, x1=168, y1=167),
            block("p223b0010", 223, "GPU HBM3\nmemory", x0=84, y0=218, x1=160, y1=252),
            block("p223b0011", 223, "GPU", x0=206, y0=218, x1=236, y1=234),
        ]

        plan = pdf.build_page_render_plan(223, blocks, {}, page_size=(612, 792), bbox_lines=None)
        image_ids = {source_id for item in plan.items if item.kind == "original_image_clip" for source_id in item.source_ids}

        self.assertIn("p223b0010", image_ids)

    def test_tall_narrow_table_note_cell_stays_in_table_image(self):
        blocks = [
            block("p313b0001", 313, "Table 6-3. SM-resident resource limits.", x0=77, y0=80, x1=414, y1=97),
            block("p313b0002", 313, "Resource", x0=84, y0=132, x1=148, y1=149),
            block(
                "p313b0012",
                313,
                "Using smaller blocks (e.g., 256\nthreads) allows more blocks to\nreside on the SM, which can\nincrease occupancy.",
                x0=307,
                y0=410,
                x1=515,
                y1=552,
            ),
        ]

        plan = pdf.build_page_render_plan(313, blocks, {}, page_size=(612, 792), bbox_lines=None)
        image_ids = {source_id for item in plan.items if item.kind == "original_image_clip" for source_id in item.source_ids}

        self.assertIn("p313b0012", image_ids)

    def test_narrow_indented_body_after_table_remains_translated(self):
        blocks = [
            block("p315b0001", 315, "Table 6-5. CUDA occupancy examples", x0=77, y0=80, x1=414, y1=97),
            block("p315b0002", 315, "Resource", x0=84, y0=132, x1=148, y1=149),
            block(
                "p315b0003",
                315,
                "This paragraph is indented but it explains the table in normal prose.",
                x0=136,
                y0=410,
                x1=356,
                y1=455,
            ),
        ]

        plan = pdf.build_page_render_plan(
            315,
            blocks,
            {"p315b0003": "这段正文带有缩进，但它是普通说明文字。"},
            page_size=(612, 792),
            bbox_lines=None,
        )
        image_ids = {source_id for item in plan.items if item.kind == "original_image_clip" for source_id in item.source_ids}
        translated_ids = {source_id for item in plan.items if item.kind == "translated_text" for source_id in item.source_ids}

        self.assertNotIn("p315b0003", image_ids)
        self.assertIn("p315b0003", translated_ids)

    def test_following_section_heading_after_table_remains_translated(self):
        blocks = [
            block("p314b0002", 314, "Table 6-4. CUDA grid limits", x0=77, y0=166, x1=247, y1=183),
            block("p314b0003", 314, "Grid dimension", x0=84, y0=200, x1=195, y1=217),
            block("p314b0013", 314, "Up to 128 kernels can execute\nconcurrently on one device.", x0=336, y0=362, x1=518, y1=433),
            block(
                "p314b0015",
                314,
                "CUDA GPU Backward and Forward Compatibility Model",
                x0=77,
                y0=600,
                x1=522,
                y1=619,
            ),
        ]

        plan = pdf.build_page_render_plan(
            314,
            blocks,
            {"p314b0015": "CUDA GPU 后向与前向兼容性模型"},
            page_size=(612, 792),
            bbox_lines=None,
        )
        image_ids = {source_id for item in plan.items if item.kind == "original_image_clip" for source_id in item.source_ids}
        translated_ids = {source_id for item in plan.items if item.kind == "translated_text" for source_id in item.source_ids}

        self.assertIn("p314b0015", translated_ids)
        self.assertNotIn("p314b0015", image_ids)

    def test_wide_indented_table_description_cell_stays_in_table_image(self):
        blocks = [
            block("p955b0001", 955, "Table 14-1. Logging options for torch.compile", x0=77, y0=80, x1=424, y1=97),
            block("p955b0002", 955, "Setting", x0=84, y0=132, x1=135, y1=149),
            block("p955b0016", 955, "Dumps the Python code for each FX graph that\nTorchDynamo produces", x0=200, y0=340, x1=484, y1=375),
        ]

        plan = pdf.build_page_render_plan(955, blocks, {}, page_size=(612, 792), bbox_lines=None)
        image_ids = {source_id for item in plan.items if item.kind == "original_image_clip" for source_id in item.source_ids}

        self.assertIn("p955b0016", image_ids)

    def test_left_aligned_body_stops_table_region_before_next_heading(self):
        blocks = [
            block("p1171b0001", 1171, "Table 17-1. High-level routing strategies.", x0=77, y0=80, x1=519, y1=115),
            block("p1171b0002", 1171, "Routing\nstrategy", x0=84, y0=132, x1=143, y1=167),
            block("p1171b0011", 1171, "Routes to the worker whose KV cache best matches\nthe request", x0=212, y0=285, x1=523, y1=320),
            block(
                "p1171b0012",
                1171,
                "The disaggregated router runs for each new request on the decode worker.",
                x0=77,
                y0=352,
                x1=518,
                y1=406,
            ),
            block("p1171b0013", 1171, "Routing factors", x0=77, y0=423, x1=188, y1=440),
        ]

        plan = pdf.build_page_render_plan(
            1171,
            blocks,
            {
                "p1171b0012": "正文段落。",
                "p1171b0013": "路由因素",
            },
            page_size=(612, 792),
            bbox_lines=None,
        )
        image_ids = {source_id for item in plan.items if item.kind == "original_image_clip" for source_id in item.source_ids}
        translated_ids = {source_id for item in plan.items if item.kind == "translated_text" for source_id in item.source_ids}

        self.assertIn("p1171b0011", image_ids)
        self.assertIn("p1171b0012", translated_ids)
        self.assertIn("p1171b0013", translated_ids)
        self.assertNotIn("p1171b0012", image_ids)
        self.assertNotIn("p1171b0013", image_ids)

    def test_short_numeric_metric_cells_are_preserved_as_image(self):
        blocks = [
            block("p650b0021", 650, "1.7 B", x0=189, y0=412, x1=222, y1=429),
            block("p650b0022", 650, "1.05 B (–38%\nversus naive)", x0=294, y0=412, x1=378, y1=447),
            block("p650b0023", 650, "~1.00 B (–\n4.76% versus\ntwo-stage)", x0=399, y0=412, x1=480, y1=465),
        ]

        plan = pdf.build_page_render_plan(650, blocks, {}, page_size=(612, 792), bbox_lines=None)
        image_ids = {source_id for item in plan.items if item.kind == "original_image_clip" for source_id in item.source_ids}

        self.assertIn("p650b0021", image_ids)
        self.assertIn("p650b0022", image_ids)
        self.assertIn("p650b0023", image_ids)

    def test_visual_clip_is_capped_after_preceding_text(self):
        blocks = [
            block("p650b0019", 650, "Previous translated text", x0=504, y0=324, x1=539, y1=395),
            block("p650b0024", 650, "~0.98", x0=504, y0=412, x1=539, y1=465),
        ]
        classes = {"p650b0019": "body", "p650b0024": "formula_region"}
        visual_ids = {"p650b0024"}

        capped = pdf.cap_visual_bbox_after_preceding_text(
            (504, 412, 539, 465),
            (455, 387, 536, 489),
            blocks,
            classes,
            visual_ids,
        )

        self.assertEqual(capped[1], 395)

    def test_fragmented_narrow_table_cell_is_preserved_as_image(self):
        blocks = [
            block("p650b0019", 650, "Exce\noverl\nunder\nblock", x0=504, y0=324, x1=539, y1=395),
        ]

        plan = pdf.build_page_render_plan(650, blocks, {}, page_size=(612, 792), bbox_lines=None)
        image_ids = {source_id for item in plan.items if item.kind == "original_image_clip" for source_id in item.source_ids}

        self.assertIn("p650b0019", image_ids)

    def test_fragmented_table_cell_preserves_adjacent_row_cells(self):
        blocks = [
            block("p802b0021", 802, "Web UIs for standard\ntrace formats, advanced\nfiltering", x0=294, y0=666, x1=437, y1=719),
            block("p802b0022", 802, "Inspect trace\nwithout\nspecialized", x0=463, y0=666, x1=539, y1=719),
        ]

        plan = pdf.build_page_render_plan(802, blocks, {}, page_size=(612, 792), bbox_lines=None)
        image_ids = {source_id for item in plan.items if item.kind == "original_image_clip" for source_id in item.source_ids}

        self.assertIn("p802b0021", image_ids)
        self.assertIn("p802b0022", image_ids)

    def test_table_header_above_visual_row_is_preserved_as_image(self):
        blocks = [
            block("p332b0004", 332, "Latency", x0=399, y0=99, x1=456, y1=116),
            block("p332b0006", 332, "the constant\ncache and\nbroadcast\nbehavior", x0=399, y0=125, x1=477, y1=286),
            block("p332b0009", 332, "126 MB total", x0=294, y0=303, x1=375, y1=320),
        ]

        plan = pdf.build_page_render_plan(332, blocks, {}, page_size=(612, 792), bbox_lines=None)
        image_ids = {source_id for item in plan.items if item.kind == "original_image_clip" for source_id in item.source_ids}

        self.assertIn("p332b0004", image_ids)

    def test_numeric_metric_headings_do_not_absorb_intervening_prose(self):
        blocks = [
            block("p822b0003", 822, "Excess Python overhead (45% in py::forward)", x0=77, y0=240, x1=383, y1=258),
            block(
                "p822b0004",
                822,
                "Use PyTorch's JIT compiler to eliminate interpreter overhead.",
                x0=99,
                y0=267,
                x1=529,
                y1=322,
            ),
            block("p822b0005", 822, "Large matmul hotspot (20.5% in aten::matmul)", x0=77, y0=339, x1=398, y1=357),
        ]

        plan = pdf.build_page_render_plan(822, blocks, {"p822b0004": "使用 JIT 编译器。"}, page_size=(612, 792), bbox_lines=None)
        image_items = [item for item in plan.items if item.kind == "original_image_clip"]

        self.assertFalse(any({"p822b0003", "p822b0005"} <= set(item.source_ids) for item in image_items))

    def test_index_entry_with_percent_is_not_numeric_metric_cell(self):
        self.assertFalse(
            pdf.is_numeric_metric_cell(
                "– GPUs near 100% utilized, Performance Monitoring and\nUtilization in Practice"
            )
        )

    def test_missing_input_event_enumeration_uses_heuristic_translation(self):
        blocks = [
            block("p004b0005", 4, "(2) In(A) is a set of input events,", x0=135, y0=207, x1=282, y1=216),
        ]

        plan = pdf.build_page_render_plan(4, blocks, {}, page_size=(622, 798), bbox_lines=None)
        item = plan.items[0]
        entry = plan.ledger[0]

        self.assertEqual(item.kind, "translated_text")
        self.assertIn("输入事件集合", item.text)
        self.assertEqual(entry.fallback_reason, "heuristic_translation")

    def test_formula_after_assertion_is_preserved_as_image_clip(self):
        blocks = [
            block("p015b0006", 15, "To show consistency, we use the following assertions:", x0=135, y0=257, x1=385, y1=281),
            block("p015b0007", 15, "(P) = r[P, 1] = 0 ^ r[P, 2] = 0\nQ(P) = r[P, 2] = 1", x0=237, y0=283, x1=617, y1=311),
            block("p015b0008", 15, "g(P) = C(P) A (VQ > P)(Q).", x0=240, y0=474, x1=617, y1=491),
        ]

        plan = pdf.build_page_render_plan(15, blocks, {"p015b0006": "为证明一致性，我们使用以下断言："}, page_size=(623, 801), bbox_lines=None)
        image_ids = [source_id for item in plan.items if item.kind == "original_image_clip" for source_id in item.source_ids]

        self.assertIn("p015b0007", image_ids)
        self.assertIn("p015b0008", image_ids)

    def test_body_block_with_embedded_numeric_subsection_is_split_and_styled(self):
        blocks = [
            block(
                "p012b0006",
                12,
                "processes.\n3.3 Queues, Stacks, Lists, etc.",
                x0=126,
                y0=472,
                x1=487,
                y1=503,
            ),
            block(
                "p012b0007",
                12,
                "Consider a FIFO queue with two operations:",
                x0=127,
                y0=506,
                x1=487,
                y1=532,
            ),
        ]
        bbox_lines = [
            {"page": 12, "text": "processes.", "bbox": (127.92, 474.83, 166.30, 482.16)},
            {"page": 12, "text": "3.3", "bbox": (128.88, 495.00, 139.75, 502.32)},
            {"page": 12, "text": "Queues, Stacks, Lists, etc.", "bbox": (148.08, 494.48, 264.00, 502.47)},
            {"page": 12, "text": "Consider a FIFO queue with two operations:", "bbox": (128.88, 510.59, 314.21, 517.92)},
        ]
        translations = {
            "p012b0006": "进程的系统中，不可能构造这些对象。\n3.3 队列、栈、列表等。",
            "p012b0007": "考虑一个具有两个操作的 FIFO 队列：",
        }

        plan = pdf.build_page_render_plan(
            12,
            blocks,
            translations,
            page_size=(623, 801),
            bbox_lines=bbox_lines,
        )
        subheadings = [
            item
            for item in plan.items
            if item.kind == "translated_text" and item.style_name == "subheading"
        ]
        body_text = "\n".join(
            item.text
            for item in plan.items
            if item.kind == "translated_text" and item.style_name == "body"
        )

        self.assertEqual(len(subheadings), 1)
        self.assertIn("3.3 队列、栈、列表等", subheadings[0].text)
        self.assertEqual(subheadings[0].font_size, pdf.DOCUMENT_STYLES["subheading"].font_size)
        self.assertNotIn("3.3 队列、栈、列表等", body_text)

    def test_footnote_marker_line_is_not_split_as_embedded_heading_when_source_rows_are_present(self):
        blocks = [
            block(
                "p003b0077",
                3,
                "https://github.com/tensorflow/tensor2tensor\n"
                "http://nlp.seas.harvard.edu/2018/04/03/attention.html\n"
                "3\n"
                "In all cases we set the feed-forward/filter size to be 4H,\n"
                "i.e., 3072 for the H = 768 and 4096 for the H = 1024.\n"
                "4\n"
                "We note that in the literature the bidirectional Trans-",
                x0=307.276,
                y0=714.300781,
                x1=525.544555,
                y1=764.902742,
            )
        ]
        bbox_lines = [
            {"page": 3, "text": "1", "bbox": (319.928, 712.518187, 322.9168, 717.862162)},
            {"page": 3, "text": "https://github.com/tensorflow/tensor2tensor", "bbox": (323.415, 714.300781, 479.502091, 722.316742)},
            {"page": 3, "text": "2", "bbox": (319.928, 723.392187, 322.9168, 728.736162)},
            {"page": 3, "text": "http://nlp.seas.harvard.edu/2018/04/03/attention.html", "bbox": (323.415, 725.174781, 513.977899, 733.190742)},
            {"page": 3, "text": "3", "bbox": (319.928, 734.267187, 322.9168, 739.611162)},
            {"page": 3, "text": "In all cases we set the feed-forward/filter size to be 4H,", "bbox": (323.415, 735.906318, 525.5436, 744.065742)},
            {"page": 3, "text": "i.e., 3072 for the H = 768 and 4096 for the H = 1024.", "bbox": (307.276, 745.868318, 508.0246, 754.027742)},
            {"page": 3, "text": "4", "bbox": (319.928, 755.104187, 322.9168, 760.448162)},
            {"page": 3, "text": "We note that in the literature the bidirectional Trans-", "bbox": (323.415, 756.886781, 525.544555, 764.902742)},
        ]
        translations = {
            "p003b0077": "https://github.com/tensorflow/tensor2tensor\n"
            "http://nlp.seas.harvard.edu/2018/04/03/attention.html\n"
            "3\n"
            "在所有情况下，我们都将前馈/滤波器大小设为 4H，即 H = 768 时为 3072，H = 1024 时为 4096。\n"
            "4\n"
            "我们注意到，在文献中，双向 Trans-",
        }

        plan = pdf.build_page_render_plan(
            3,
            blocks,
            translations,
            page_size=(595.276, 841.89),
            bbox_lines=bbox_lines,
        )

        self.assertFalse(any(item.fallback_reason == "embedded_heading" for item in plan.items if "p003b0077" in item.source_ids))
        self.assertEqual(pdf.validate_plan_text_fit(plan), [])

    def test_footnote_url_line_is_not_split_as_embedded_heading_without_source_rows(self):
        blocks = [
            block(
                "p015b0104",
                15,
                "14\n"
                "Note that we only report single-task fine-tuning results in this paper.\n"
                "15\n"
                "https://gluebenchmark.com/faq",
                x0=307,
                y0=704,
                x1=526,
                y1=764,
            )
        ]
        translations = {
            "p015b0104": "14\n"
            "请注意，本文中我们仅报告单任务微调结果。\n"
            "15 https://gluebenchmark.com/faq",
        }

        plan = pdf.build_page_render_plan(
            15,
            blocks,
            translations,
            page_size=(623, 801),
            bbox_lines=None,
        )

        self.assertFalse(any(item.fallback_reason == "embedded_heading" for item in plan.items))
        self.assertEqual(pdf.validate_plan_embedded_heading_policy(plan), [])

    def test_table_output_numbered_sentence_is_not_standalone_heading(self):
        self.assertFalse(pdf.render_line_is_standalone_heading("35 今天是 2023 年 1 月 30 日，星期一。"))
        self.assertTrue(pdf.render_line_is_standalone_heading("3 工具"))
        self.assertTrue(pdf.render_line_is_standalone_heading("2.1 方法"))

    def test_quality_check_reports_body_text_with_embedded_numeric_heading(self):
        plan = pdf.PageRenderPlan(
            page_num=12,
            items=[
                pdf.RenderItem(
                    "translated_text",
                    ["p012b0006"],
                    (126, 472, 487, 503),
                    text="进程的系统中，不可能构造这些对象。\n3.3 队列、栈、列表等。",
                    font_size=pdf.BODY_FONT_SIZE,
                    style_name="body",
                )
            ],
        )

        errors = pdf.validate_plan_embedded_heading_policy(plan)

        self.assertTrue(errors)
        self.assertIn("embedded heading", errors[0])

    def test_quality_check_reports_blank_source_image_clip(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            image_path = Path(tmp_dir) / "blank.png"
            Image.new("RGB", (100, 100), "white").save(image_path)
            plan = pdf.PageRenderPlan(
                page_num=21,
                items=[
                    pdf.RenderItem(
                        "original_image_clip",
                        ["p021b0005"],
                        (10, 10, 90, 30),
                        fallback_reason="visual_region",
                    )
                ],
            )

            errors = pdf.validate_plan_image_clip_content(plan, image_path, (100, 100))

        self.assertTrue(errors)
        self.assertIn("blank source image clip", errors[0])

    def test_image_only_page_preserves_full_source_page(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            image_path = Path(tmp_dir) / "cover.png"
            image = Image.new("RGB", (100, 100), "white")
            draw = ImageDraw.Draw(image)
            draw.rectangle((20, 20, 80, 80), fill="black")
            image.save(image_path)

            plan = pdf.build_page_render_plan(
                1,
                [],
                {},
                page_size=(100, 100),
                bbox_lines=[],
                source_image_path=image_path,
            )

        self.assertEqual(len(plan.items), 1)
        self.assertEqual(plan.items[0].kind, "original_image_clip")
        self.assertEqual(plan.items[0].bbox, (0.0, 0.0, 100.0, 100.0))
        self.assertEqual(plan.items[0].fallback_reason, "image_only_page")

    def test_blank_page_without_text_does_not_create_image_clip(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            image_path = Path(tmp_dir) / "blank.png"
            Image.new("RGB", (100, 100), "white").save(image_path)

            plan = pdf.build_page_render_plan(
                1,
                [],
                {},
                page_size=(100, 100),
                bbox_lines=[],
                source_image_path=image_path,
            )

        self.assertEqual(plan.items, [])

    def test_short_pseudocode_and_line_numbers_are_preserved_as_one_visual_region(self):
        blocks = [
            block("p022b0004", 22, "2\n3", x0=90, y0=100, x1=102, y1=132),
            block("p022b0009", 22, "if help.seq = 0\nthen prefer:=help", x0=120, y0=100, x1=260, y1=132),
            block("p022b0015", 22, "Normal proof text follows.", x0=120, y0=160, x1=480, y1=190),
        ]

        plan = pdf.build_page_render_plan(
            22,
            blocks,
            {"p022b0015": "后续证明正文。"},
            page_size=(623, 801),
            bbox_lines=None,
        )
        image_items = [item for item in plan.items if item.kind == "original_image_clip"]
        image_ids = {source_id for item in image_items for source_id in item.source_ids}
        translated_ids = {source_id for item in plan.items if item.kind == "translated_text" for source_id in item.source_ids}

        self.assertIn("p022b0004", image_ids)
        self.assertIn("p022b0009", image_ids)
        self.assertNotIn("p022b0009", translated_ids)

    def test_profiler_command_output_is_preserved_as_image(self):
        blocks = [
            block(
                "p821b0007",
                821,
                "# Samples Command\nShared Object\n# ........ ........ ...................\n45.0% python\n/src/train.py\n20.5% python\nlibnccl.so",
                x0=92,
                y0=538,
                x1=362,
                y1=672,
            )
        ]

        plan = pdf.build_page_render_plan(821, blocks, {}, page_size=(612, 792), bbox_lines=None)
        image_ids = {source_id for item in plan.items if item.kind == "original_image_clip" for source_id in item.source_ids}

        self.assertIn("p821b0007", image_ids)

    def test_python_import_block_is_preserved_as_image(self):
        blocks = [
            block(
                "p112b0001",
                112,
                "import os\n"
                "import re\n"
                "import glob\n"
                "import subprocess\n"
                "import psutil\n"
                "import ctypes\n"
                "import torch\n"
                "import torch.distributed as dist\n"
                "from torch.nn.parallel import DistributedDataParallel as DDP",
                x0=92,
                y0=72,
                x1=497,
                y1=219,
            )
        ]

        plan = pdf.build_page_render_plan(112, blocks, {}, page_size=(612, 792), bbox_lines=None)
        image_ids = {source_id for item in plan.items if item.kind == "original_image_clip" for source_id in item.source_ids}

        self.assertIn("p112b0001", image_ids)

    def test_shell_command_block_is_preserved_as_image(self):
        blocks = [
            block(
                "p111b0003",
                111,
                "numactl --cpunodebind=1 --membind=1 \\\npython train.py --gpu 4",
                x0=92,
                y0=214,
                x1=342,
                y1=240,
            )
        ]

        plan = pdf.build_page_render_plan(111, blocks, {}, page_size=(612, 792), bbox_lines=None)
        image_ids = {source_id for item in plan.items if item.kind == "original_image_clip" for source_id in item.source_ids}

        self.assertIn("p111b0003", image_ids)

    def test_cuda_call_fragment_is_preserved_as_image(self):
        blocks = [
            block(
                "p690b0003",
                690,
                "TILE_SIZE + chunk) + lane_id,\n"
                "sizeof(float4),\n"
                "pipe_lc);\n"
                "cuda::memcpy_async(\n"
                "cta,\n"
                "reinterpret_cast<float4*>(B0 + chunk) + lane_id,",
                x0=119,
                y0=99,
                x1=524,
                y1=192,
            )
        ]

        plan = pdf.build_page_render_plan(690, blocks, {}, page_size=(612, 792), bbox_lines=None)
        image_ids = {source_id for item in plan.items if item.kind == "original_image_clip" for source_id in item.source_ids}

        self.assertIn("p690b0003", image_ids)

    def test_partially_translated_quote_is_not_replaced_by_original(self):
        source = (
            "NVIDIA's rapid roadmap suggests that this is just the beginning. The Grace\n"
            "Blackwell architecture will evolve into Vera Rubin and Feynman and\n"
            "beyond. As NVIDIA's CEO, Jensen Huang, describes, \"AI is advancing at\n"
            "light speed, and companies are racing to build AI factories that can scale to\n"
            "meet the processing demands of reasoning AI and inference time scaling.\""
        )
        translated = (
            "NVIDIA 快速推进的路线图表明，这仅仅是开始。Grace Blackwell 架构将演进为 "
            "Vera Rubin、Feynman 以及更后续的架构。正如 NVIDIA 首席执行官 Jensen Huang 所描述的："
            "\"AI is advancing at light speed, and companies are racing to build AI factories "
            "that can scale to meet the processing demands of reasoning AI and inference time scaling.\""
        )
        blocks = [block("p095b0006", 95, source, x0=77, y0=605, x1=531, y1=696)]

        plan = pdf.build_page_render_plan(95, blocks, {"p095b0006": translated}, page_size=(612, 792), bbox_lines=None)

        self.assertEqual(plan.items[0].kind, "translated_text")
        self.assertIn("快速推进的路线图", plan.items[0].text)

    def test_non_prose_identifiers_do_not_require_chinese_translation(self):
        self.assertFalse(pdf.source_requires_chinese_translation("Copyright © 2026 Flux Capacitor, LLC. All rights reserved."))
        self.assertFalse(pdf.source_requires_chinese_translation("https://oreilly.com/about/contact.html"))
        self.assertFalse(pdf.source_requires_chinese_translation("NVIDIA Blackwell “Dual-Die” GPU"))

    def test_translation_quality_allows_author_list_original_selectable_text(self):
        blocks = [
            block(
                "p001b0002",
                1,
                "Timo Schick Jane Dwivedi-Yu Roberto Dessì Roberta Raileanu Maria Lomeli Eric Hambro",
                x0=113.978,
                y0=179.887,
                x1=498.524,
                y1=205.709,
            )
        ]
        plan = pdf.PageRenderPlan(page_num=1)
        plan.items.append(
            pdf.RenderItem(
                "original_selectable_text",
                ["p001b0002"],
                (113.978, 179.887, 498.524, 205.709),
                text=blocks[0]["text"],
                font_size=pdf.BODY_FONT_SIZE,
                style_name="body",
                fallback_reason="untranslated_fallback_original",
            )
        )
        plan.ledger.append(pdf.CoverageEntry("p001b0002", "body", "original_selectable_text", True, "untranslated_fallback_original"))

        self.assertEqual(pdf.validate_plan_translation_quality(1, blocks, {}, plan), [])

    def test_multiline_author_list_metadata_does_not_require_chinese_translation(self):
        block_text = (
            "Timo Schick Jane Dwivedi-Yu Roberto Dessì † Roberta Raileanu\n"
            "Maria Lomeli Eric Hambro Luke Zettlemoyer Nicola Cancedda Thomas Scialom"
        )
        blocks = [
            block(
                "p001b0002",
                1,
                block_text,
                x0=113.978,
                y0=179.887,
                x1=498.524,
                y1=205.709,
            )
        ]
        plan = pdf.PageRenderPlan(page_num=1)
        plan.items.append(
            pdf.RenderItem(
                "original_selectable_text",
                ["p001b0002"],
                (113.978, 179.887, 498.524, 205.709),
                text=block_text,
                font_size=pdf.BODY_FONT_SIZE,
                style_name="body",
                fallback_reason="untranslated_fallback_original",
            )
        )
        plan.ledger.append(pdf.CoverageEntry("p001b0002", "body", "original_selectable_text", True, "untranslated_fallback_original"))

        self.assertFalse(pdf.source_requires_chinese_translation(block_text))
        self.assertEqual(pdf.validate_plan_translation_quality(1, blocks, {"p001b0002": block_text}, plan), [])

    def test_translation_quality_allows_url_original_selectable_text(self):
        blocks = [
            block(
                "p001b0004",
                1,
                "https://qwenlm.github.io/blog/qwen3/ https://github.com/QwenLM/Qwen3",
                x0=90.0,
                y0=150.0,
                x1=520.0,
                y1=170.0,
            )
        ]
        plan = pdf.PageRenderPlan(page_num=1)
        plan.items.append(
            pdf.RenderItem(
                "original_selectable_text",
                ["p001b0004"],
                (90.0, 150.0, 520.0, 170.0),
                text=blocks[0]["text"],
                font_size=pdf.BODY_FONT_SIZE,
                style_name="body",
                fallback_reason="untranslated_fallback_original",
            )
        )
        plan.ledger.append(pdf.CoverageEntry("p001b0004", "body", "original_selectable_text", True, "untranslated_fallback_original"))

        self.assertEqual(pdf.validate_plan_translation_quality(1, blocks, {}, plan), [])

    def test_translation_quality_allows_multiline_url_original_selectable_text(self):
        block_text = (
            "https://huggingface.co/Qwen\n"
            "https://modelscope.cn/organization/qwen\n"
            "https://github.com/QwenLM/Qwen3"
        )
        blocks = [
            block(
                "p001b0004",
                1,
                block_text,
                x0=90.0,
                y0=150.0,
                x1=520.0,
                y1=190.0,
            )
        ]
        plan = pdf.PageRenderPlan(page_num=1)
        plan.items.append(
            pdf.RenderItem(
                "original_selectable_text",
                ["p001b0004"],
                (90.0, 150.0, 520.0, 190.0),
                text=block_text,
                font_size=pdf.BODY_FONT_SIZE,
                style_name="body",
                fallback_reason="untranslated_fallback_original",
            )
        )
        plan.ledger.append(pdf.CoverageEntry("p001b0004", "body", "original_selectable_text", True, "untranslated_fallback_original"))

        self.assertEqual(pdf.validate_plan_translation_quality(1, blocks, {}, plan), [])
        self.assertFalse(pdf.source_requires_chinese_translation(block_text))

    def test_yaml_config_block_is_preserved_as_image(self):
        blocks = [
            block(
                "p1179b0006",
                1179,
                "model: ...\nsplit_policy:\nprompt_length_threshold: 256\nprefix_cache_weight: 10.0\nenable_hotspot_prevention: true\ncache:\nreuse_prefix: true",
                x0=92,
                y0=467,
                x1=322,
                y1=709,
            )
        ]

        plan = pdf.build_page_render_plan(1179, blocks, {}, page_size=(612, 792), bbox_lines=None)
        image_ids = {source_id for item in plan.items if item.kind == "original_image_clip" for source_id in item.source_ids}

        self.assertIn("p1179b0006", image_ids)

    def test_split_assignment_columns_are_preserved_as_one_code_image_region(self):
        blocks = [
            block("p241b0001", 241, "srcDesc.devId\nsrcDesc.seg", x0=119, y0=72, x1=207, y1=98),
            block("p241b0002", 241, "= deviceId;\n= VRAM_SEG;", x0=241, y0=72, x1=315, y1=98),
            block(
                "p241b0003",
                241,
                "// 5) Register memory with each agent and trim to xfer\n"
                "descriptors\n"
                "auto srcRegs = agentSrc.registerMem(srcList);\n"
                "auto dstRegs = agentDst.registerMem(dstList);",
                x0=92,
                y0=130,
                x1=484,
                y1=184,
            ),
            block("p241b0004", 241, "auto srcXfer = srcRegs.trim();\nused for xfer\nauto dstXfer = dstRegs.trim();", x0=92, y0=198, x1=322, y1=238),
            block("p241b0005", 241, "// metadata-free descriptors", x0=335, y0=198, x1=524, y1=212),
            block("p241b0006", 241, "Following body text.", x0=119, y0=270, x1=480, y1=300),
        ]

        plan = pdf.build_page_render_plan(
            241,
            blocks,
            {"p241b0006": "后续正文。"},
            page_size=(612, 792),
            bbox_lines=None,
        )
        image_items = [item for item in plan.items if item.kind == "original_image_clip"]
        image_ids = {source_id for item in image_items for source_id in item.source_ids}
        translated_ids = {source_id for item in plan.items if item.kind == "translated_text" for source_id in item.source_ids}

        self.assertIn("p241b0001", image_ids)
        self.assertIn("p241b0002", image_ids)
        self.assertIn("p241b0003", image_ids)
        self.assertIn("p241b0004", image_ids)
        self.assertIn("p241b0005", image_ids)
        self.assertNotIn("p241b0001", translated_ids)
        self.assertNotIn("p241b0003", translated_ids)
        self.assertEqual(pdf.validate_plan_layout(plan, (612, 792)), [])


class TranslationNormalizationTests(unittest.TestCase):
    def test_soft_linebreaks_are_reflowed_before_punctuation_repair(self):
        raw = (
            "引理1给出了在一个操作进行期间可被穿接的单元数量的上界。现在我们给出一系列引理，表明当\n"
            "P 完成对 head 数组的扫描时，announce[P] 要么已被穿接，要么 head[P]\n"
            "位于距链表末端不超过 n+1 个单元的位置。\n"
            "引理 2。以下断言是不变的："
        )

        cleaned = pdf.prepare_render_translation(raw)

        self.assertIn("表明当 P 完成", cleaned)
        self.assertIn("head[P] 位于", cleaned)
        self.assertIn("\n引理 2。以下断言是不变的：", cleaned)
        self.assertNotIn("表明当。", cleaned)

    def test_introductory_line_break_is_preserved_as_paragraph_boundary(self):
        raw = (
            "无等待同步的基本问题可以表述为\n"
            "给定两个并发对象 X 和 Y，是否存在用 Y 对 X 的无等待实现？"
        )

        cleaned = pdf.prepare_render_translation(raw)

        self.assertIn("可以表述为：\n给定两个并发对象", cleaned)
        self.assertNotIn("可以表述为给定", cleaned)

    def test_visual_prefix_stripping_only_applies_to_leading_visual_lines(self):
        raw = (
            "令 max(head) 为所有 head 条目的最大序列号。\n"
            "图 14 展示了记录 v: T := e 的更新。\n"
            "非正式地说，随后继续证明。"
        )

        self.assertEqual(pdf.translation_tail_after_visual_prefix(raw), "")

    def test_visual_prefix_stripping_keeps_body_after_leading_code(self):
        raw = (
            "decide(input:值)返回(值)\n"
            "first := compare&swap(r,↓,input)\n"
            "endif\n"
            "另一个经典原语是 compare&swap，如图 8 所示。"
        )

        tail = pdf.translation_tail_after_visual_prefix(raw)

        self.assertIn("另一个经典原语", tail)
        self.assertNotIn("first :=", tail)


class CrossPageSentencePostprocessTests(unittest.TestCase):
    def test_cross_page_boundary_detection_uses_bbox_margin_context(self):
        selected_pages = [
            (
                1,
                [
                    block(
                        "p001b0001",
                        1,
                        "In other words, the history appears sequential to each process, and",
                        x0=130,
                        y0=590,
                        x1=486,
                        y1=602,
                    )
                ],
            ),
            (
                2,
                [
                    block(
                        "p002b0001",
                        2,
                        "Wait-Free Synchronization\nof operations. Equivalently, each operation appears instantaneously.",
                        x0=130,
                        y0=46,
                        x1=486,
                        y1=90,
                    )
                ],
            ),
        ]
        bbox_lines_by_page = {
            1: [
                {"page": 1, "text": "In other words, the history appears sequential to each process, and", "bbox": (130, 594, 486, 602)},
                {"page": 1, "text": "respects the real-time precedence ordering", "bbox": (300, 606, 486, 614)},
            ],
            2: [
                {"page": 2, "text": "Wait-Free Synchronization", "bbox": (330, 48, 430, 56)},
                {"page": 2, "text": "of operations. Equivalently, each operation appears instantaneously.", "bbox": (130, 74, 486, 82)},
            ],
        }

        candidates = pdf.detect_cross_page_sentence_splits(selected_pages, bbox_lines_by_page=bbox_lines_by_page)

        self.assertEqual(len(candidates), 1)
        self.assertIn("respects the real-time precedence ordering of operations.", candidates[0]["source_sentence"])
        self.assertNotIn("Wait-Free Synchronization", candidates[0]["source_sentence"])

    def test_cross_page_split_sentence_uses_complete_sentence_repair_without_duplicate_prefix(self):
        selected_pages = [
            (
                1,
                [
                    block(
                        "p001b0001",
                        1,
                        "In other words, the history appears sequential to each process, and",
                        x0=130,
                        y0=590,
                        x1=486,
                        y1=602,
                    )
                ],
            ),
            (
                2,
                [
                    block(
                        "p002b0001",
                        2,
                        "of operations. Equivalently, each operation appears to take effect instantaneously.",
                        x0=130,
                        y0=50,
                        x1=486,
                        y1=90,
                    )
                ],
            ),
        ]
        translations = {
            "p001b0001": "换言之，对于每个进程，该历史看起来是顺序的，并且",
            "p002b0001": "的操作。等价地，每个操作都表现为瞬时生效。",
        }
        repairs = {
            "p001b0001->p002b0001": {
                "translation": "换言之，对于每个进程，该历史看起来是顺序的，并且尊重操作的实时先后顺序。",
                "next_prefix_translation": "的操作。",
            }
        }

        repaired = pdf.postprocess_cross_page_sentence_splits(
            selected_pages,
            translations,
            boundary_repairs=repairs,
        )

        self.assertEqual(
            repaired["p001b0001"],
            "换言之，对于每个进程，该历史看起来是顺序的，并且尊重操作的实时先后顺序。",
        )
        self.assertEqual(repaired["p002b0001"], "等价地，每个操作都表现为瞬时生效。")
        self.assertNotIn("并且。", pdf.prepare_render_translation(repaired["p001b0001"]))

    def test_cross_page_prefix_removal_handles_running_header_translation(self):
        selected_pages = [
            (
                1,
                [
                    block(
                        "p001b0001",
                        1,
                        "In other words, the history appears sequential to each process, and",
                        x0=130,
                        y0=590,
                        x1=486,
                        y1=602,
                    )
                ],
            ),
            (
                2,
                [
                    block(
                        "p002b0001",
                        2,
                        "Wait-FreeSynchronization\nof operations. Equivalently, each operation appears to take effect instantaneously.",
                        x0=130,
                        y0=46,
                        x1=486,
                        y1=90,
                    )
                ],
            ),
        ]
        translations = {
            "p001b0001": "换言之，对于每个进程，该历史看起来是顺序的，并且",
            "p002b0001": "无等待同步\n的操作。等价地，每个操作都表现为瞬时生效。",
        }
        repairs = {
            "p001b0001->p002b0001": {
                "translation": "换言之，对于每个进程，该历史看起来是顺序的，并且尊重操作的实时先后顺序。",
                "next_prefix_translation": "的操作。",
            }
        }

        repaired = pdf.postprocess_cross_page_sentence_splits(
            selected_pages,
            translations,
            boundary_repairs=repairs,
        )

        self.assertEqual(repaired["p002b0001"], "无等待同步\n等价地，每个操作都表现为瞬时生效。")


class GlobalStyleTests(unittest.TestCase):
    def test_style_policy_rejects_non_monotonic_global_hierarchy(self):
        plan = pdf.PageRenderPlan(page_num=1)
        with patch.dict(
            pdf.DOCUMENT_STYLES,
            {
                "heading": pdf.TextStyle(
                    font_size=8.0,
                    line_height_factor=pdf.DOCUMENT_STYLES["heading"].line_height_factor,
                    paragraph_spacing=pdf.DOCUMENT_STYLES["heading"].paragraph_spacing,
                )
            },
        ):
            errors = pdf.validate_plan_style_policy(plan)

        self.assertTrue(any("style hierarchy" in error for error in errors), errors)

    def test_style_policy_uses_ledger_classification_for_expected_style(self):
        plan = pdf.PageRenderPlan(page_num=1)
        plan.items.append(
            pdf.RenderItem(
                "translated_text",
                ["p001b0001"],
                (72.0, 96.0, 420.0, 116.0),
                text="1. 引言",
                font_size=pdf.DOCUMENT_STYLES["body"].font_size,
                style_name="body",
            )
        )
        plan.ledger.append(pdf.CoverageEntry("p001b0001", "heading", "translated_text", True, ""))

        errors = pdf.validate_plan_style_policy(plan)

        self.assertTrue(any("expected heading/subheading" in error for error in errors), errors)

    def test_style_policy_allows_heading_classification_with_subheading_style(self):
        plan = pdf.PageRenderPlan(page_num=1)
        plan.items.append(
            pdf.RenderItem(
                "translated_text",
                ["p001b0001"],
                (72.0, 96.0, 420.0, 116.0),
                text="6.1 实现",
                font_size=pdf.DOCUMENT_STYLES["subheading"].font_size,
                style_name="subheading",
            )
        )
        plan.ledger.append(pdf.CoverageEntry("p001b0001", "heading", "translated_text", True, ""))

        self.assertEqual(pdf.validate_plan_style_policy(plan), [])

    def test_style_policy_checks_each_ledger_classified_text_role(self):
        cases = [
            ("title", "heading"),
            ("heading", "body"),
            ("subheading", "body"),
            ("body", "heading"),
            ("reference", "body"),
            ("footer", "body"),
        ]
        for classification, wrong_style in cases:
            with self.subTest(classification=classification):
                plan = pdf.PageRenderPlan(page_num=1)
                plan.items.append(
                    pdf.RenderItem(
                        "translated_text",
                        [f"{classification}-block"],
                        (72.0, 96.0, 420.0, 116.0),
                        text="文本",
                        font_size=pdf.DOCUMENT_STYLES[wrong_style].font_size,
                        style_name=wrong_style,
                    )
                )
                plan.ledger.append(
                    pdf.CoverageEntry(f"{classification}-block", classification, "translated_text", True, "")
                )

                errors = pdf.validate_plan_style_policy(plan)

                self.assertTrue(any(f"expected {classification}" in error for error in errors), errors)

    def test_style_policy_rejects_generic_fallback_style_mismatch(self):
        plan = pdf.PageRenderPlan(page_num=1)
        plan.items.append(
            pdf.RenderItem(
                "original_selectable_text",
                ["p001b0001"],
                (72.0, 96.0, 420.0, 116.0),
                text="1. INTRODUCTION",
                font_size=pdf.DOCUMENT_STYLES["body"].font_size,
                style_name="body",
                fallback_reason="untranslated_fallback_original",
            )
        )
        plan.ledger.append(
            pdf.CoverageEntry("p001b0001", "heading", "original_selectable_text", True, "untranslated_fallback_original")
        )

        errors = pdf.validate_plan_style_policy(plan)

        self.assertTrue(any("expected heading/subheading" in error for error in errors), errors)

    def test_style_policy_allows_explicit_role_split_exception(self):
        plan = pdf.PageRenderPlan(page_num=1)
        plan.items.append(
            pdf.RenderItem(
                "translated_text",
                ["p001b0001"],
                (72.0, 96.0, 420.0, 116.0),
                text="2.2 子章节",
                font_size=pdf.DOCUMENT_STYLES["subheading"].font_size,
                style_name="subheading",
                fallback_reason="embedded_heading",
            )
        )
        plan.ledger.append(pdf.CoverageEntry("p001b0001", "body", "translated_text", True, "embedded_heading_split"))

        self.assertEqual(pdf.validate_plan_style_policy(plan), [])

    def test_style_policy_checks_page_number_style_when_rendered_as_text(self):
        plan = pdf.PageRenderPlan(page_num=1)
        plan.items.append(
            pdf.RenderItem(
                "original_selectable_text",
                ["p001b0001"],
                (300.0, 760.0, 318.0, 772.0),
                text="1",
                font_size=pdf.DOCUMENT_STYLES["body"].font_size,
                style_name="body",
            )
        )
        plan.ledger.append(pdf.CoverageEntry("p001b0001", "page_number", "original_selectable_text", True, ""))

        errors = pdf.validate_plan_style_policy(plan)

        self.assertTrue(any("expected footer" in error for error in errors), errors)

    def test_translated_items_use_global_document_styles(self):
        blocks = [
            block("p001b0001", 1, "Wait-Free Synchronization", y0=60, y1=78),
            block("p001b0002", 1, "1. INTRODUCTION", y0=100, y1=112),
            block("p001b0003", 1, "This paper gives a wait-free implementation.", y0=130, y1=160),
        ]
        translations = {
            "p001b0001": "无等待同步",
            "p001b0002": "1. 引言",
            "p001b0003": "本文给出了一个无等待实现。",
        }

        plan = pdf.build_page_render_plan(1, blocks, translations, page_size=(623, 801), bbox_lines=None)
        items = {item.source_ids[0]: item for item in plan.items if item.kind == "translated_text"}

        self.assertEqual(items["p001b0001"].style_name, "title")
        self.assertEqual(items["p001b0002"].style_name, "heading")
        self.assertEqual(items["p001b0003"].style_name, "body")
        self.assertEqual(items["p001b0003"].font_size, pdf.DOCUMENT_STYLES["body"].font_size)

    def test_raster_draw_block_clears_original_bbox_when_render_box_moves(self):
        img = Image.new("RGBA", (180, 140), (255, 255, 255, 255))
        draw = ImageDraw.Draw(img)
        draw.rectangle((20, 80, 120, 112), fill=(0, 0, 0, 255))
        source_block = block(
            "p001b0001",
            1,
            "This source line moved upward after layout expansion.",
            x0=20,
            y0=80,
            x1=120,
            y1=112,
        )

        pdf.draw_block(
            img,
            source_block,
            "译文",
            dpi=72,
            protected_boxes=[],
            render_box=(20, 20, 120, 52),
        )

        self.assertEqual(img.getpixel((30, 100))[:3], (255, 255, 255))

    def test_raster_draw_block_clears_source_bbox_even_when_protected_box_clips_it(self):
        img = Image.new("RGBA", (180, 140), (255, 255, 255, 255))
        draw = ImageDraw.Draw(img)
        draw.rectangle((20, 80, 120, 112), fill=(0, 0, 0, 255))
        source_block = block(
            "p001b0001",
            1,
            "This source line was clipped around a protected diagram label.",
            x0=20,
            y0=80,
            x1=120,
            y1=112,
        )

        pdf.draw_block(
            img,
            source_block,
            "译文",
            dpi=72,
            protected_boxes=[(90, 88, 170, 100)],
            render_box=(20, 20, 120, 52),
        )

        self.assertEqual(img.getpixel((30, 106))[:3], (255, 255, 255))

    def test_raster_protected_boxes_use_visual_group_for_diagram_formula_label(self):
        blocks = [
            block(
                "p001b0001",
                1,
                "Many applications in natural language processing rely on adapting one large-scale model.",
                x0=108.0,
                y0=514.9,
                x1=379.2,
                y1=622.5,
            ),
            block("p001b0002", 1, "f(x)", x0=571.4, y0=518.6, x1=583.0, y1=534.9),
            block("p001b0003", 1, "h", x0=416.5, y0=515.3, x1=421.1, y1=525.3),
            block("p001b0004", 1, "𝐴 = 𝒩(0, 𝜎 2 )", x0=456.9, y0=592.9, x1=495.9, y1=600.7),
        ]
        body_box = pdf.block_to_px_box(blocks[0], 200, 1700, 2200, pad=2)

        protected_boxes = pdf.raster_protected_boxes(blocks, 200, 1700, 2200)

        self.assertEqual(pdf.avoid_protected_boxes(body_box, protected_boxes), body_box)

    def test_raster_draw_block_does_not_rotate_long_reference_column(self):
        img = Image.new("RGBA", (260, 520), (255, 255, 255, 255))
        draw = ImageDraw.Draw(img)
        draw.rectangle((20, 20, 120, 500), fill=(0, 0, 0, 255))
        source_text = (
            "R. Collobert, J. Weston, L. Bottou, M. Karlen,\n"
            "K. Kavukcuoglu, and P. P. Kuksa, Natural language processing almost from scratch."
        )
        source_block = block(
            "p100b0018",
            100,
            source_text,
            x0=20,
            y0=20,
            x1=120,
            y1=500,
        )

        pdf.draw_block(
            img,
            source_block,
            "R. Collobert、J. Weston、L. Bottou、M. Karlen 和 K. Kavukcuoglu，自然语言处理。",
            dpi=72,
            protected_boxes=[],
            render_box=(20, 20, 120, 500),
        )

        self.assertEqual(img.getpixel((130, 250))[:3], (255, 255, 255))
        self.assertNotEqual(img.getpixel((28, 28))[:3], (255, 255, 255))

    def test_raster_draw_block_uses_light_text_on_dark_background(self):
        img = Image.new("RGBA", (220, 90), (0, 0, 0, 255))
        draw = ImageDraw.Draw(img)
        draw.rectangle((20, 20, 180, 44), fill=(0, 0, 0, 255))
        source_block = block(
            "p001b0003",
            1,
            "REVIEW ARTICLE",
            x0=20,
            y0=20,
            x1=180,
            y1=44,
        )

        pdf.draw_block(
            img,
            source_block,
            "综述文章",
            dpi=72,
            protected_boxes=[],
            render_box=(20, 20, 180, 44),
        )

        dark_text_pixels = 0
        light_text_pixels = 0
        for r, g, b, _a in img.crop((20, 20, 180, 44)).getdata():
            if 20 < r < 80 and 20 < g < 80 and 20 < b < 80:
                dark_text_pixels += 1
            if r > 180 and g > 180 and b > 180:
                light_text_pixels += 1

        self.assertGreater(light_text_pixels, 0)
        self.assertEqual(dark_text_pixels, 0)

    def test_vector_textbox_does_not_shrink_when_style_is_fixed(self):
        fitz = pdf.load_fitz()
        doc = fitz.open()
        page = doc.new_page(width=100, height=100)

        ok = pdf.insert_vector_textbox(
            page,
            fitz,
            fitz.Rect(10, 10, 40, 18),
            "这是一个很长很长的译文，不能通过缩小字号塞进框里。",
            pdf.DOCUMENT_STYLES["body"].font_size,
            pdf.VECTOR_BODY_COLOR,
            line_height_factor=pdf.DOCUMENT_STYLES["body"].line_height_factor,
            allow_shrink=False,
        )
        doc.close()

        self.assertFalse(ok)

    def test_style_fit_can_compact_spacing_without_changing_font_size(self):
        style = pdf.TextStyle(
            font_size=10.0,
            line_height_factor=1.0,
            paragraph_spacing=5.0,
            min_line_height_factor=1.0,
            min_paragraph_spacing=0.0,
        )

        fit = pdf.fitted_text_spacing([["第一段"], [], ["第二段"]], 10.0, 31.0, style)

        self.assertEqual(fit, (1.0, 0.0))

    def test_style_fit_allows_sub_point_floating_roundoff_at_boundary(self):
        style = pdf.TextStyle(
            font_size=9.2,
            line_height_factor=1.22,
            paragraph_spacing=2.0,
            min_line_height_factor=1.08,
            min_paragraph_spacing=0.0,
        )
        lines = [["line"] for _ in range(7)]
        required = pdf.text_height_for_lines(lines, 9.2, 1.08, 0.0)

        fit = pdf.fitted_text_spacing(lines, 9.2, required - 1e-12, style)

        self.assertEqual(fit, (1.08, 2.0))


class BodyFlowLayoutTests(unittest.TestCase):
    def test_adjacent_body_blocks_share_a_flow_box_when_one_translation_overflows(self):
        blocks = [
            block("p013b0006", 13, "First body paragraph.", x0=120, y0=100, x1=480, y1=180),
            block("p013b0007", 13, "Second body paragraph.", x0=120, y0=182, x1=480, y1=220),
        ]
        translations = {
            "p013b0006": "第一段正文。" * 8,
            "p013b0007": "第二段正文。" * 24,
        }

        plan = pdf.build_page_render_plan(13, blocks, translations, page_size=(623, 801), bbox_lines=None)
        body_items = [item for item in plan.items if item.kind == "translated_text" and item.style_name == "body"]

        self.assertEqual(len(body_items), 1)
        self.assertEqual(body_items[0].source_ids, ["p013b0006", "p013b0007"])
        self.assertEqual(pdf.validate_plan_text_fit(plan), [])

    def test_body_flow_expands_into_safe_whitespace_instead_of_changing_font_size(self):
        blocks = [
            block("p014b0001", 14, "First body paragraph.", x0=120, y0=120, x1=480, y1=145),
            block("p014b0002", 14, "Second body paragraph.", x0=120, y0=147, x1=480, y1=170),
            block("p014b0003", 14, "Fig. 9. Later visual.", x0=120, y0=260, x1=480, y1=285, preserve_image=True),
        ]
        translations = {
            "p014b0001": "第一段正文内容" * 16,
            "p014b0002": "第二段正文内容" * 16,
        }

        plan = pdf.build_page_render_plan(14, blocks, translations, page_size=(623, 801), bbox_lines=None)
        body_items = [item for item in plan.items if item.kind == "translated_text" and item.style_name == "body"]

        self.assertEqual(len(body_items), 1)
        self.assertGreater(body_items[0].bbox[3] - body_items[0].bbox[1], 50.0)
        self.assertEqual(body_items[0].font_size, pdf.DOCUMENT_STYLES["body"].font_size)
        self.assertEqual(pdf.validate_plan_text_fit(plan), [])

    def test_body_flow_rebalances_excessive_internal_slack_and_mid_page_gap(self):
        blocks = [
            block("p017b0001", 17, "Fig. 8. Protected algorithm.", x0=135, y0=70, x1=500, y1=145, preserve_image=True),
            block("p017b0002", 17, "First proof paragraph.", x0=135, y0=160, x1=495, y1=360),
            block("p017b0003", 17, "Second proof paragraph.", x0=136, y0=420, x1=496, y1=642),
        ]
        translations = {
            "p017b0002": "第一段证明正文。" * 18,
            "p017b0003": "第二段证明正文。" * 22,
        }

        plan = pdf.build_page_render_plan(17, blocks, translations, page_size=(623, 801), bbox_lines=None)
        body_items = [
            item
            for item in sorted(plan.items, key=lambda candidate: candidate.bbox[1])
            if item.kind == "translated_text" and item.style_name == "body"
        ]
        fitz = pdf.load_fitz()

        self.assertEqual(len(body_items), 2)
        for item in body_items:
            style = pdf.text_style(item.style_name)
            lines = pdf.wrap_mixed_pdf_text(fitz, item.text, item.bbox[2] - item.bbox[0], item.font_size)
            preferred = pdf.text_height_for_lines(lines, item.font_size, style.line_height_factor, style.paragraph_spacing)
            available = item.bbox[3] - item.bbox[1]
            self.assertLessEqual(available - preferred, 7.0)
        first = body_items[0]
        first_style = pdf.text_style(first.style_name)
        first_lines = pdf.wrap_mixed_pdf_text(fitz, first.text, first.bbox[2] - first.bbox[0], first.font_size)
        first_preferred = pdf.text_height_for_lines(first_lines, first.font_size, first_style.line_height_factor, first_style.paragraph_spacing)
        visible_gap = body_items[1].bbox[1] - (body_items[0].bbox[1] + first_preferred)

        self.assertLessEqual(visible_gap, 19.0)

    def test_text_flow_rebalances_gap_before_subheading(self):
        blocks = [
            block("p006b0002", 6, "Previous section body.", x0=135, y0=100, x1=495, y1=185),
            block("p006b0003", 6, "2.3 IMPLEMENTATIONS", x0=135, y0=320, x1=260, y1=335),
            block("p006b0004", 6, "Following section body.", x0=135, y0=360, x1=495, y1=450),
        ]
        translations = {
            "p006b0002": "上一节正文。" * 16,
            "p006b0003": "2.3 实现",
            "p006b0004": "下一节正文。" * 18,
        }

        plan = pdf.build_page_render_plan(6, blocks, translations, page_size=(623, 801), bbox_lines=None)
        flow_items = [
            item
            for item in sorted(plan.items, key=lambda candidate: candidate.bbox[1])
            if item.kind == "translated_text" and item.style_name in {"body", "subheading"}
        ]
        fitz = pdf.load_fitz()
        first = flow_items[0]
        first_preferred = pdf.preferred_text_height_for_item(first, fitz)
        gap_before_heading = flow_items[1].bbox[1] - (first.bbox[1] + first_preferred)

        self.assertLessEqual(gap_before_heading, 22.0)

    def test_body_flow_merge_does_not_cross_subheading_barrier(self):
        blocks = [
            block("p012b0001", 12, "Previous body.", x0=128, y0=470, x1=487, y1=490),
            block("p012b0002", 12, "3.3 Queues, Stacks, Lists, etc.", x0=128, y0=495, x1=265, y1=503),
            block("p012b0003", 12, "Following body.", x0=128, y0=506, x1=487, y1=532),
        ]
        translations = {
            "p012b0001": "上一段正文。",
            "p012b0002": "3.3 队列、栈、列表等。",
            "p012b0003": "后续正文。" * 4,
        }

        plan = pdf.build_page_render_plan(12, blocks, translations, page_size=(623, 801), bbox_lines=None)
        body_items = [
            item
            for item in sorted(plan.items, key=lambda candidate: candidate.bbox[1])
            if item.kind == "translated_text" and item.style_name == "body"
        ]
        subheading = next(item for item in plan.items if item.kind == "translated_text" and item.style_name == "subheading")

        self.assertEqual(len(body_items), 2)
        self.assertLess(body_items[0].bbox[1], subheading.bbox[1])
        self.assertLess(subheading.bbox[1], body_items[1].bbox[1])


class CoverageValidationTests(unittest.TestCase):
    def test_validate_coverage_fails_for_missing_block(self):
        blocks = [
            block("p002b0001", 2, "A source paragraph.", y0=100, y1=120),
            block("p002b0002", 2, "Another source paragraph.", y0=130, y1=150),
        ]
        plan = pdf.PageRenderPlan(page_num=2)
        plan.ledger.append(pdf.CoverageEntry("p002b0001", "body", "translated_text", True))

        errors = pdf.validate_plan_coverage(2, blocks, plan)

        self.assertIn("p002b0002", "\n".join(errors))

    def test_validate_coverage_allows_page_number_skip(self):
        blocks = [
            block("p002b0001", 2, "127", y0=760, y1=770),
        ]
        plan = pdf.build_page_render_plan(2, blocks, {}, page_size=(623, 801), bbox_lines=None)

        errors = pdf.validate_plan_coverage(2, blocks, plan)

        self.assertEqual(errors, [])

    def test_validate_coverage_rejects_unknown_skip(self):
        blocks = [
            block("p002b0001", 2, "Unclassified but meaningful source content.", y0=100, y1=120),
        ]
        plan = pdf.PageRenderPlan(page_num=2)
        plan.ledger.append(pdf.CoverageEntry("p002b0001", "unknown", "skip_explicitly", True))

        errors = pdf.validate_plan_coverage(2, blocks, plan)

        self.assertTrue(any("illegal skip class unknown" in error for error in errors), errors)


class BBoxLineParserTests(unittest.TestCase):
    def test_parse_bbox_lines_extracts_words_and_coordinates(self):
        html = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <doc>
      <page width="623.000000" height="801.000000">
        <block xMin="131.4" yMin="234.6" xMax="184.5" yMax="244.6">
          <line xMin="131.4" yMin="234.6" xMax="184.5" yMax="244.6">
            <word xMin="131.4" yMin="234.6" xMax="184.5" yMax="244.6">REFERENCES</word>
          </line>
        </block>
      </page>
    </doc>
  </body>
</html>
"""
        lines = pdf.parse_bbox_lines_from_text(html)

        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["page"], 1)
        self.assertEqual(lines[0]["text"], "REFERENCES")
        self.assertEqual(lines[0]["bbox"], (131.4, 234.6, 184.5, 244.6))


class LayoutValidationTests(unittest.TestCase):
    def test_overlap_validation_detects_body_over_image_clip(self):
        plan = pdf.PageRenderPlan(page_num=3)
        plan.items.append(pdf.RenderItem("original_image_clip", ["fig"], (100, 100, 300, 200)))
        plan.items.append(pdf.RenderItem("translated_text", ["body"], (150, 120, 350, 220), text="正文"))

        errors = pdf.validate_plan_layout(plan, page_size=(623, 801))

        self.assertTrue(any("overlaps protected" in error for error in errors))

    def test_overlap_validation_allows_nonoverlapping_items(self):
        plan = pdf.PageRenderPlan(page_num=3)
        plan.items.append(pdf.RenderItem("original_image_clip", ["fig"], (100, 100, 300, 200)))
        plan.items.append(pdf.RenderItem("translated_text", ["body"], (100, 220, 350, 260), text="正文"))

        errors = pdf.validate_plan_layout(plan, page_size=(623, 801))

        self.assertEqual(errors, [])


class QualityValidationTests(unittest.TestCase):
    def test_translation_quality_flags_body_image_fallback(self):
        blocks = [
            block("p018b0002", 18, "A normal body paragraph that should be translated.", y0=100, y1=160),
        ]
        plan = pdf.PageRenderPlan(page_num=18)
        plan.items.append(
            pdf.RenderItem(
                "original_image_clip",
                ["p018b0002"],
                (100, 100, 400, 160),
                fallback_reason="heading_overlap",
            )
        )
        plan.ledger.append(
            pdf.CoverageEntry("p018b0002", "body", "original_image_clip", True, "heading_overlap")
        )

        errors = pdf.validate_plan_translation_quality(
            18,
            blocks,
            {"p018b0002": "这是一段应该以中文渲染的正文。"},
            plan,
        )

        self.assertTrue(any("normal text block rendered as original image" in error for error in errors))

    def test_translation_quality_flags_untranslated_english_body(self):
        blocks = [
            block("p018b0002", 18, "This paper presents a wait-free implementation.", y0=100, y1=160),
        ]
        translations = {"p018b0002": "This paper presents a wait-free implementation."}
        plan = pdf.build_page_render_plan(18, blocks, translations, page_size=(623, 801), bbox_lines=None)

        errors = pdf.validate_plan_translation_quality(18, blocks, translations, plan)
        items = [item for item in plan.items if "p018b0002" in item.source_ids]

        self.assertEqual(items[0].kind, "original_selectable_text")
        self.assertEqual(items[0].fallback_reason, "untranslated_fallback_original")
        self.assertTrue(any("missing Chinese translation" in error for error in errors))

    def test_translation_quality_allows_reference_english(self):
        blocks = [
            block("p025b0005", 25, "REFERENCES", y0=230, y1=245),
            block(
                "p025b0006",
                25,
                "1. ANDERSON, J. H., AND GOUDA, M. G. The virtue of patience.",
                y0=250,
                y1=280,
            ),
        ]
        plan = pdf.build_page_render_plan(25, blocks, {}, page_size=(623, 801), bbox_lines=None)

        errors = pdf.validate_plan_translation_quality(25, blocks, {}, plan)

        self.assertEqual(errors, [])

    def test_document_quality_keeps_reference_continuation_pages_untranslated(self):
        page_10 = [
            block("p010b0012", 10, "References", x0=108, y0=597, x1=164, y1=608),
            block(
                "p010b0013",
                10,
                "[1] Jimmy Lei Ba, Jamie Ryan Kiros, and Geoffrey E Hinton. Layer normalization.",
                x0=113,
                y0=616,
                x1=504,
                y1=636,
            ),
        ]
        page_12 = [
            block(
                "p012b0001",
                12,
                "[25] Mitchell P Marcus, Mary Ann Marcinkiewicz, and Beatrice Santorini. "
                "Building a large annotated corpus of english: The penn treebank.",
                x0=108,
                y0=75,
                x1=504,
                y1=95,
            ),
        ]

        errors = pdf.validate_document_quality(
            [(10, page_10), (12, page_12)],
            {},
            page_size=(612, 792),
            job_paths=None,
        )

        self.assertFalse(any("p012b0001 normal text block is missing Chinese translation" in error for error in errors))

    def test_text_overlap_quality_detects_overlapping_translated_items(self):
        plan = pdf.PageRenderPlan(page_num=9)
        plan.items.append(pdf.RenderItem("translated_text", ["p009b0008"], (100, 100, 400, 180), text="正文"))
        plan.items.append(pdf.RenderItem("translated_text", ["p009b0009"], (120, 120, 260, 145), text="重叠正文"))

        errors = pdf.validate_plan_text_overlaps(plan)

        self.assertTrue(any("overlaps text" in error for error in errors))

    def test_quality_flags_dead_space_inside_body_flow(self):
        plan = pdf.PageRenderPlan(page_num=17)
        plan.items.append(
            pdf.RenderItem(
                "translated_text",
                ["p017b0002"],
                (135, 150, 495, 360),
                text="第一段证明正文。" * 12,
                font_size=pdf.BODY_FONT_SIZE,
                style_name="body",
            )
        )
        plan.items.append(
            pdf.RenderItem(
                "translated_text",
                ["p017b0003"],
                (135, 430, 495, 640),
                text="第二段证明正文。" * 12,
                font_size=pdf.BODY_FONT_SIZE,
                style_name="body",
            )
        )

        errors = pdf.validate_plan_quality(17, [], {}, plan)

        self.assertTrue(any("body flow has uneven vertical spacing" in error for error in errors))

    def test_quality_still_flags_unbalanced_flow_when_no_protected_region_blocks_rebalance(self):
        plan = pdf.PageRenderPlan(page_num=17)
        plan.items.append(
            pdf.RenderItem(
                "translated_text",
                ["p017b0002"],
                (135, 150, 495, 210),
                text="第一段证明正文。",
                font_size=pdf.BODY_FONT_SIZE,
                style_name="body",
            )
        )
        plan.items.append(
            pdf.RenderItem(
                "translated_text",
                ["p017b0003"],
                (135, 430, 495, 490),
                text="第二段证明正文。",
                font_size=pdf.BODY_FONT_SIZE,
                style_name="body",
            )
        )
        plan.items.append(
            pdf.RenderItem(
                "original_selectable_text",
                ["p017b0004"],
                (150, 170, 320, 190),
                text="anchor",
                font_size=pdf.BODY_FONT_SIZE,
                style_name="reference",
            )
        )

        errors = pdf.validate_plan_vertical_balance(plan)

        self.assertTrue(any("body flow has uneven vertical spacing" in error for error in errors))

    def test_validate_plan_quality_reports_ownership_violations(self):
        plan = pdf.PageRenderPlan(page_num=19)
        plan.ownership_validation = pdf.ownership.OwnershipValidationResult(
            issues=[
                pdf.ownership.OwnershipIssue(
                    issue_code="missing_owner",
                    severity="error",
                    page_num=19,
                    message="source block p019b0001 has no ownership component",
                    source_ids=["p019b0001"],
                ),
                pdf.ownership.OwnershipIssue(
                    issue_code="conservative_split",
                    severity="warning",
                    page_num=19,
                    message="split ownership is conservative",
                    source_ids=["p019b0002"],
                ),
            ]
        )

        errors = pdf.validate_plan_quality(19, [], {}, plan)

        self.assertIn("source block p019b0001 has no ownership component", errors)
        self.assertNotIn("split ownership is conservative", errors)

    def test_unfit_nonprose_fallback_preserves_reference_component_as_selectable_text(self):
        block_id = "p012b0002"
        text = "\n".join(f"[{idx}] Author {idx}. 2020. Reference title {idx}." for idx in range(1, 10))
        blocks = [
            block(
                block_id,
                12,
                text,
                x0=80,
                y0=120,
                x1=230,
                y1=170,
            )
        ]
        plan = pdf.PageRenderPlan(page_num=12)
        plan.items.append(
            pdf.RenderItem(
                "original_selectable_text",
                [block_id],
                (80, 120, 230, 170),
                text=text,
                font_size=pdf.BODY_FONT_SIZE,
                style_name="body",
                fallback_reason="missing_translation",
                component_kind=pdf.ownership.COMPONENT_KIND_REFERENCE,
            )
        )
        plan.ledger.append(
            pdf.CoverageEntry(
                block_id,
                "reference",
                "original_selectable_text",
                True,
                "reference_original",
                component_kind=pdf.ownership.COMPONENT_KIND_REFERENCE,
            )
        )

        pdf.convert_unfit_nonprose_text_to_image_clips(plan, blocks)

        self.assertEqual(plan.items[0].kind, "original_selectable_text")
        self.assertEqual(plan.ledger[0].render_kind, "original_selectable_text")

    def test_unfit_prose_continuation_keeps_translated_text_vector(self):
        block_id = "p003b0002"
        source = (
            'to complete the text. You can call the API by writing "[QA(question)]" where '
            '"question" is the question you want to ask. Here are'
        )
        translated = "以完成该文本。你可以通过写入“[QA(question)]”来调用该 API，其中“question”是你想提出的问题。以下是"
        bbox = (114.748011, 89.485998, 494.3392, 96.525998)
        blocks = [
            block(
                block_id,
                3,
                source,
                x0=bbox[0],
                y0=bbox[1],
                x1=bbox[2],
                y1=bbox[3],
            )
        ]
        plan = pdf.PageRenderPlan(page_num=3)
        item = pdf.RenderItem(
            "translated_text",
            [block_id],
            bbox,
            text=translated,
            font_size=pdf.BODY_FONT_SIZE,
            style_name="body",
        )
        plan.items.append(item)
        plan.ledger.append(pdf.CoverageEntry(block_id, "body", "translated_text", True))

        self.assertIsNone(pdf.text_item_fit_metrics(item, pdf.load_fitz())[0])
        pdf.convert_unfit_nonprose_text_to_image_clips(plan, blocks)

        self.assertEqual(plan.items[0].kind, "translated_text")
        self.assertEqual(plan.ledger[0].render_kind, "translated_text")

    def test_moving_leading_enum_continuation_does_not_duplicate_source_ids(self):
        plan = pdf.PageRenderPlan(page_num=4)
        previous = pdf.RenderItem(
            "translated_text",
            ["p004b0001"],
            (135, 180, 492, 190),
            text="(1) States(A) 是状态集合。",
            font_size=pdf.BODY_FONT_SIZE,
            style_name="body",
        )
        current = pdf.RenderItem(
            "translated_text",
            ["p004b0004", "p004b0005"],
            (136, 190, 492, 260),
            text="初始状态集合。\n(2) In(A) 是输入事件集合。",
            font_size=pdf.BODY_FONT_SIZE,
            style_name="body",
        )
        plan.items.extend([previous, current])

        pdf.move_leading_enum_continuations_to_previous_items(plan)

        self.assertNotIn("p004b0004", previous.source_ids)
        self.assertEqual(current.source_ids, ["p004b0004", "p004b0005"])


class RenderPlanSerializationTests(unittest.TestCase):
    def sample_plan(self):
        return pdf.PageRenderPlan(
            page_num=7,
            items=[
                pdf.RenderItem(
                    "translated_text",
                    ["p007b0002"],
                    (100.0, 120.25, 360.5, 168.75),
                    text="稳定的中文正文。",
                    font_size=9.5,
                    style_name="body",
                    color=(0.1, 0.2, 0.3),
                    fallback_reason="",
                ),
                pdf.RenderItem(
                    "original_image_clip",
                    ["p007b0003", "p007b0004"],
                    (90, 190, 380, 260),
                    fallback_reason="visual_region",
                ),
            ],
            ledger=[
                pdf.CoverageEntry("p007b0002", "body", "translated_text", True),
                pdf.CoverageEntry("p007b0003", "figure_region", "original_image_clip", True, "visual_region"),
            ],
            protected_boxes=[(90, 190, 380, 260)],
        )

    def test_serialized_ledger_covers_non_trivial_translated_and_protected_blocks(self):
        blocks = [
            block(
                "p042b0001",
                42,
                "This body paragraph should be represented as translated text.",
                x0=78,
                y0=80,
                x1=520,
                y1=126,
            ),
            block(
                "p042b0002",
                42,
                "Fig. 2. Protected diagram with nodes and arrows.",
                x0=110,
                y0=180,
                x1=420,
                y1=245,
                preserve_image=True,
            ),
        ]
        plan = pdf.build_page_render_plan(
            42,
            blocks,
            {"p042b0001": "这段正文应当作为译文文本呈现。"},
            page_size=(612, 792),
            bbox_lines=None,
        )

        plan_json = pdf.render_plan_to_json(plan)
        ledger_by_id = {entry["block_id"]: entry for entry in plan_json["coverage_ledger"]}

        self.assertEqual(set(ledger_by_id), {"p042b0001", "p042b0002"})
        for source_id in ("p042b0001", "p042b0002"):
            self.assertEqual(
                set(ledger_by_id[source_id]),
                {
                    "block_id",
                    "classification",
                    "render_kind",
                    "rendered",
                    "fallback_reason",
                    "component_id",
                    "component_kind",
                },
            )
            self.assertTrue(ledger_by_id[source_id]["rendered"])

        self.assertEqual(ledger_by_id["p042b0001"]["classification"], "body")
        self.assertEqual(ledger_by_id["p042b0001"]["render_kind"], "translated_text")
        self.assertEqual(ledger_by_id["p042b0001"]["fallback_reason"], "")
        self.assertEqual(ledger_by_id["p042b0002"]["classification"], "figure_region")
        self.assertEqual(ledger_by_id["p042b0002"]["render_kind"], "original_image_clip")
        self.assertEqual(ledger_by_id["p042b0002"]["fallback_reason"], "visual_region")
        self.assertEqual(ledger_by_id["p042b0001"]["component_kind"], "translated_text")
        self.assertEqual(ledger_by_id["p042b0002"]["component_kind"], "visual")
        self.assertEqual(len(plan_json["components"]), 2)
        self.assertEqual(len(plan_json["ownership_ledger"]), 2)
        self.assertEqual(plan_json["ownership_validation"], {"ok": True, "issues": []})

    def test_serialized_visual_fallback_reason_is_kept_on_item_and_ledger(self):
        blocks = [
            block(
                "p043b0001",
                43,
                "Fig. 3. Protected visual content that must remain an image clip.",
                x0=96,
                y0=145,
                x1=430,
                y1=230,
                preserve_image=True,
            )
        ]
        plan = pdf.build_page_render_plan(43, blocks, {}, page_size=(612, 792), bbox_lines=None)

        plan_json = pdf.render_plan_to_json(plan)
        image_items = [
            item
            for item in plan_json["render_items"]
            if item["kind"] == "original_image_clip" and "p043b0001" in item["source_ids"]
        ]
        ledger_entries = [
            entry for entry in plan_json["coverage_ledger"] if entry["block_id"] == "p043b0001"
        ]

        self.assertEqual(len(image_items), 1)
        self.assertEqual(image_items[0]["fallback_reason"], "visual_region")
        self.assertEqual(len(ledger_entries), 1)
        self.assertEqual(ledger_entries[0]["fallback_reason"], "visual_region")

    def test_render_plan_json_dumps_is_deterministic_for_same_plan_content(self):
        plan = self.sample_plan()
        first = pdf.render_plan_json_dumps(
            plan,
            validation_results={"warnings": ["check later"], "errors": []},
        )
        second = pdf.render_plan_json_dumps(
            plan,
            validation_results={"errors": [], "warnings": ["check later"]},
        )

        self.assertEqual(first, second)
        self.assertEqual(json.loads(first), json.loads(second))

    def test_render_plan_json_includes_diagnostic_fields(self):
        plan_json = pdf.render_plan_to_json(
            self.sample_plan(),
            validation_results=[
                {"category": "style", "message": "body style checked", "source_ids": ["p007b0002"]},
            ],
        )

        self.assertEqual(plan_json["page_num"], 7)
        self.assertEqual(
            plan_json["render_items"][0],
            {
                "kind": "translated_text",
                "source_ids": ["p007b0002"],
                "bbox": [100.0, 120.25, 360.5, 168.75],
                "text": "稳定的中文正文。",
                "font_size": 9.5,
                "style_name": "body",
                "color": [0.1, 0.2, 0.3],
                "fallback_reason": "",
                "component_id": "",
                "component_kind": "",
            },
        )
        self.assertEqual(
            plan_json["coverage_ledger"][1],
            {
                "block_id": "p007b0003",
                "classification": "figure_region",
                "render_kind": "original_image_clip",
                "rendered": True,
                "fallback_reason": "visual_region",
                "component_id": "",
                "component_kind": "",
            },
        )
        self.assertEqual(plan_json["components"], [])
        self.assertEqual(plan_json["ownership_ledger"], [])
        self.assertEqual(plan_json["ownership_validation"], {"ok": True, "issues": []})
        self.assertEqual(plan_json["protected_regions"], [{"bbox": [90.0, 190.0, 380.0, 260.0]}])
        self.assertEqual(
            plan_json["validation_results"],
            [{"category": "style", "message": "body style checked", "source_ids": ["p007b0002"]}],
        )

    def test_write_vector_pdf_writes_page_render_plan_artifact(self):
        fitz = pdf.load_fitz()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_pdf = tmp_path / "source.pdf"
            output_pdf = tmp_path / "out.pdf"
            plans_dir = tmp_path / "plans"

            src_doc = fitz.open()
            src_doc.new_page(width=200, height=200)
            src_doc.save(source_pdf)
            src_doc.close()

            blocks = [
                block(
                    "p001b0001",
                    1,
                    "A short source paragraph.",
                    x0=20,
                    y0=20,
                    x1=180,
                    y1=80,
                )
            ]
            translations = {"p001b0001": "稳定的中文正文。"}

            pdf.write_vector_pdf(
                source_pdf,
                output_pdf,
                [(1, blocks)],
                translations,
                (200, 200),
                72,
                job_paths={"plans_dir": plans_dir},
            )

            expected_plan = pdf.build_page_render_plan(
                1,
                blocks,
                translations,
                (200, 200),
                bbox_lines=None,
                source_image_path=None,
            )
            expected_json = pdf.render_plan_json_dumps(
                expected_plan,
                validation_results={
                    "ownership": pdf.ownership.ownership_validation_to_json(expected_plan.ownership_validation),
                    "coverage_errors": [],
                    "layout_errors": [],
                    "style_policy_errors": [],
                    "text_fit_errors": [],
                },
            )

            artifact_path = plans_dir / "page-001.render-plan.json"
            self.assertTrue(artifact_path.exists())
            self.assertEqual(artifact_path.read_text(encoding="utf-8"), expected_json)

    def test_write_vector_pdf_render_plan_artifact_is_deterministic_for_same_inputs(self):
        fitz = pdf.load_fitz()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_pdf = tmp_path / "source.pdf"

            src_doc = fitz.open()
            src_doc.new_page(width=200, height=200)
            src_doc.save(source_pdf)
            src_doc.close()

            blocks = [
                block(
                    "p001b0001",
                    1,
                    "A short source paragraph.",
                    x0=20,
                    y0=20,
                    x1=180,
                    y1=80,
                )
            ]
            translations = {"p001b0001": "稳定的中文正文。"}
            artifacts = []

            for run_name in ("first", "second"):
                output_pdf = tmp_path / run_name / "out.pdf"
                plans_dir = tmp_path / run_name / "plans"
                pdf.write_vector_pdf(
                    source_pdf,
                    output_pdf,
                    [(1, blocks)],
                    translations,
                    (200, 200),
                    72,
                    job_paths={"plans_dir": plans_dir},
                )
                artifacts.append((plans_dir / "page-001.render-plan.json").read_bytes())

            self.assertEqual(artifacts[0], artifacts[1])

    def test_failed_vector_pdf_plan_artifact_records_only_executed_validation(self):
        fitz = pdf.load_fitz()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_pdf = tmp_path / "source.pdf"
            output_pdf = tmp_path / "out.pdf"
            plans_dir = tmp_path / "plans"

            src_doc = fitz.open()
            src_doc.new_page(width=100, height=100)
            src_doc.save(source_pdf)
            src_doc.close()

            blocks = [block("p001b0001", 1, "A short source line.", x0=10, y0=10, x1=40, y1=20)]
            original_validate = pdf.validate_plan_coverage
            pdf.validate_plan_coverage = lambda page_num, blocks_arg, plan: ["coverage failed before later checks"]
            try:
                with self.assertRaisesRegex(RuntimeError, "coverage failed before later checks"):
                    pdf.write_vector_pdf(
                        source_pdf,
                        output_pdf,
                        [(1, blocks)],
                        {"p001b0001": "稳定的中文正文。"},
                        (100, 100),
                        72,
                        job_paths={"plans_dir": plans_dir},
                    )
            finally:
                pdf.validate_plan_coverage = original_validate

            artifact = json.loads((plans_dir / "page-001.render-plan.json").read_text(encoding="utf-8"))
            self.assertEqual(
                artifact["validation_results"],
                {
                    "ownership": {"ok": True, "issues": []},
                    "coverage_errors": ["coverage failed before later checks"],
                },
            )

    def test_vector_pdf_rejects_and_records_style_policy_errors(self):
        fitz = pdf.load_fitz()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_pdf = tmp_path / "source.pdf"
            output_pdf = tmp_path / "out.pdf"
            plans_dir = tmp_path / "plans"

            src_doc = fitz.open()
            src_doc.new_page(width=200, height=200)
            src_doc.save(source_pdf)
            src_doc.close()

            blocks = [block("p001b0001", 1, "1. INTRODUCTION", x0=20, y0=20, x1=180, y1=60)]
            bad_plan = pdf.PageRenderPlan(page_num=1)
            bad_plan.items.append(
                pdf.RenderItem(
                    "translated_text",
                    ["p001b0001"],
                    (20, 20, 180, 60),
                    text="1. 引言",
                    font_size=pdf.DOCUMENT_STYLES["body"].font_size,
                    style_name="body",
                )
            )
            bad_plan.ledger.append(pdf.CoverageEntry("p001b0001", "heading", "translated_text", True, ""))
            original_build = pdf.build_page_render_plan
            pdf.build_page_render_plan = lambda *args, **kwargs: bad_plan
            try:
                with self.assertRaisesRegex(RuntimeError, "expected heading/subheading"):
                    pdf.write_vector_pdf(
                        source_pdf,
                        output_pdf,
                        [(1, blocks)],
                        {"p001b0001": "1. 引言"},
                        (200, 200),
                        72,
                        job_paths={"plans_dir": plans_dir},
                    )
            finally:
                pdf.build_page_render_plan = original_build

            artifact = json.loads((plans_dir / "page-001.render-plan.json").read_text(encoding="utf-8"))
            self.assertTrue(artifact["validation_results"]["style_policy_errors"])

    def test_failed_vector_pdf_keeps_validation_error_when_plan_artifact_write_fails(self):
        fitz = pdf.load_fitz()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_pdf = tmp_path / "source.pdf"
            output_pdf = tmp_path / "out.pdf"
            plans_dir = tmp_path / "plans-blocker"
            plans_dir.write_text("not a directory", encoding="utf-8")

            src_doc = fitz.open()
            src_doc.new_page(width=100, height=100)
            src_doc.save(source_pdf)
            src_doc.close()

            blocks = [block("p001b0001", 1, "A short source line.", x0=10, y0=10, x1=40, y1=20)]

            with self.assertRaisesRegex(RuntimeError, "needs .* height"):
                pdf.write_vector_pdf(
                    source_pdf,
                    output_pdf,
                    [(1, blocks)],
                    {"p001b0001": "这是一个很长很长的译文，应该无法放入这个非常矮的文本框中。" * 8},
                    (100, 100),
                    72,
                    job_paths={"plans_dir": plans_dir},
                )


class SourceClipRenderingTests(unittest.TestCase):
    def test_image_clip_prefers_cached_source_page_png(self):
        fitz = pdf.load_fitz()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_pdf = tmp_path / "source.pdf"
            output_pdf = tmp_path / "out.pdf"
            pages_dir = tmp_path / "pages"
            pages_dir.mkdir()

            src_doc = fitz.open()
            src_page = src_doc.new_page(width=100, height=100)
            src_page.draw_rect(fitz.Rect(10, 10, 40, 40), color=None, fill=(1, 0, 0))
            src_doc.save(source_pdf)
            src_doc.close()

            image = Image.new("RGB", (100, 100), "white")
            draw = ImageDraw.Draw(image)
            draw.rectangle((10, 10, 40, 40), fill="black")
            image.save(pages_dir / "page-001.png")

            blocks = [
                block(
                    "p001b0001",
                    1,
                    "Fig. 1. Cached source clip.",
                    x0=10,
                    y0=10,
                    x1=40,
                    y1=40,
                    preserve_image=True,
                )
            ]
            pdf.write_vector_pdf(
                source_pdf,
                output_pdf,
                [(1, blocks)],
                {},
                (100, 100),
                72,
                job_paths={"pages_dir": pages_dir},
            )

            out_doc = fitz.open(output_pdf)
            pix = out_doc[0].get_pixmap(alpha=False)
            rendered = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            out_doc.close()

            self.assertLess(sum(rendered.getpixel((20, 20))), 40)

    def test_overflowing_translated_text_fails_text_fit_instead_of_shrinking_or_image_fallback(self):
        fitz = pdf.load_fitz()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_pdf = tmp_path / "source.pdf"
            output_pdf = tmp_path / "out.pdf"
            pages_dir = tmp_path / "pages"
            pages_dir.mkdir()

            src_doc = fitz.open()
            src_page = src_doc.new_page(width=100, height=100)
            src_page.draw_rect(fitz.Rect(10, 10, 40, 20), color=None, fill=(1, 0, 0))
            src_doc.save(source_pdf)
            src_doc.close()

            image = Image.new("RGB", (100, 100), "white")
            draw = ImageDraw.Draw(image)
            draw.rectangle((10, 10, 40, 20), fill="blue")
            image.save(pages_dir / "page-001.png")

            blocks = [block("p001b0001", 1, "A short source line.", x0=10, y0=10, x1=40, y1=20)]
            with self.assertRaisesRegex(RuntimeError, "needs .* height"):
                pdf.write_vector_pdf(
                    source_pdf,
                    output_pdf,
                    [(1, blocks)],
                    {"p001b0001": "这是一个很长很长的译文，应该无法放入这个非常矮的文本框中。" * 8},
                    (100, 100),
                    72,
                    job_paths={"pages_dir": pages_dir},
                )

    def test_render_text_item_raises_instead_of_silent_image_fallback(self):
        fitz = pdf.load_fitz()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_image = tmp_path / "page-001.png"
            Image.new("RGB", (100, 100), "black").save(source_image)

            src_doc = fitz.open()
            src_page = src_doc.new_page(width=100, height=100)
            out_doc = fitz.open()
            out_page = out_doc.new_page(width=100, height=100)
            item = pdf.RenderItem(
                "translated_text",
                ["body"],
                (10, 10, 40, 18),
                text="这是一个很长很长的译文，渲染阶段不能偷偷贴回英文截图。" * 4,
                font_size=pdf.DOCUMENT_STYLES["body"].font_size,
                style_name="body",
            )

            with self.assertRaisesRegex(RuntimeError, "did not fit during render"):
                pdf.render_plan_item(out_page, src_page, fitz, item, 72, source_image_path=source_image)

            out_doc.close()
            src_doc.close()


if __name__ == "__main__":
    unittest.main()
