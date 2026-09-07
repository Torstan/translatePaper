import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

import translate_pdf_parallel as parallel


class ParallelBatchPlanningTests(unittest.TestCase):
    def test_build_page_batches_includes_subheading_class(self):
        blocks = [
            {
                "id": "p001b0001",
                "text": "3.3 Queues, stacks, and lists",
                "xMin": 72.0,
                "yMin": 100.0,
                "xMax": 260.0,
                "yMax": 116.0,
            }
        ]
        with (
            patch.object(parallel.pipeline, "build_visual_regions", return_value=[]),
            patch.object(parallel.pipeline, "classify_blocks", return_value={"p001b0001": "subheading"}),
        ):
            batches = parallel.build_page_batches([(1, blocks)], max_chars=7000)

        self.assertEqual([[item["id"] for item in batch.items] for batch in batches], [["p001b0001"]])

    def test_build_page_batches_excludes_text_covered_by_visual_region(self):
        blocks = [
            {
                "id": "p001b0001",
                "text": "Figure 1: architecture",
                "xMin": 80.0,
                "yMin": 100.0,
                "xMax": 260.0,
                "yMax": 180.0,
            },
            {
                "id": "p001b0002",
                "text": "short caption fragment",
                "xMin": 100.0,
                "yMin": 130.0,
                "xMax": 180.0,
                "yMax": 145.0,
            },
            {
                "id": "p001b0003",
                "text": "This body paragraph should still be translated.",
                "xMin": 80.0,
                "yMin": 220.0,
                "xMax": 360.0,
                "yMax": 245.0,
            },
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
            patch.object(parallel.pipeline, "build_visual_regions", return_value=visual_regions),
            patch.object(parallel.pipeline, "classify_blocks", return_value=classes),
        ):
            batches = parallel.build_page_batches([(1, blocks)], max_chars=7000)

        self.assertEqual([[item["id"] for item in batch.items] for batch in batches], [["p001b0003"]])


    def test_build_page_batches_keeps_toolformer_body_rows_inside_visual_bbox_before_translation(self):
        blocks = [
            {
                "id": "p003b0003",
                "text": "some examples of API calls:",
                "xMin": 114.7,
                "yMin": 100.3,
                "xMax": 199.4,
                "yMax": 107.4,
            },
            {
                "id": "p003b0005",
                "text": 'Output: Joe Biden was born in [QA("Where was Joe Biden born?")] Scranton, [QA("In which state is Scranton?")]',
                "xMin": 114.7,
                "yMin": 127.7,
                "xMax": 459.0,
                "yMax": 134.8,
            },
            {
                "id": "p003b0007",
                "text": "Output: Joe Biden was born in Scranton, Pennsylvania, which is located in Lackawanna County.",
                "xMin": 114.7,
                "yMin": 149.0,
                "xMax": 459.0,
                "yMax": 156.0,
            },
            {
                "id": "p003b0008",
                "text": 'Output: Coca-Cola, or [QA("What other name is Coca-Cola known by?")] Coke, is a soft drink.',
                "xMin": 114.7,
                "yMin": 166.0,
                "xMax": 488.5,
                "yMax": 173.0,
            },
            {
                "id": "p003b0010",
                "text": "Input: x",
                "xMin": 114.7,
                "yMin": 192.1,
                "xMax": 139.3,
                "yMax": 200.4,
            },
            {
                "id": "p003b0012",
                "text": "Figure 3: An exemplary prompt P(x) used to generate API calls.",
                "xMin": 112.7,
                "yMin": 224.4,
                "xMax": 499.2,
                "yMax": 233.5,
            },
            {
                "id": "p003b0013",
                "text": "The model is trained on the generated examples in the following section.",
                "xMin": 107.6,
                "yMin": 260.0,
                "xMax": 504.1,
                "yMax": 285.0,
            },
        ]
        visual_regions = [
            {
                "source_ids": ["p003b0003", "p003b0008", "p003b0010", "p003b0012"],
                "bbox": (112.7, 100.3, 499.2, 233.5),
                "has_caption_seed": True,
            }
        ]
        classes = {
            "p003b0003": "figure_region",
            "p003b0005": "body",
            "p003b0007": "body",
            "p003b0008": "figure_region",
            "p003b0010": "figure_region",
            "p003b0012": "figure_region",
            "p003b0013": "body",
        }

        with (
            patch.object(parallel.pipeline, "build_visual_regions", return_value=visual_regions),
            patch.object(parallel.pipeline, "classify_blocks", return_value=classes),
        ):
            ownership_result = parallel.pipeline.build_translation_page_components(3, blocks, page_size=(612.0, 792.0))
            batches = parallel.build_page_batches([(3, blocks)], max_chars=7000, page_size=(612.0, 792.0))

        batch_ids = {item["id"] for batch in batches for item in batch.items}
        self.assertTrue({"p003b0005", "p003b0007", "p003b0013"} <= batch_ids)
        self.assertTrue({"p003b0005", "p003b0007", "p003b0013"} <= set(ownership_result.translatable_ids))

    def test_build_page_batches_keeps_toolformer_table_body_cells_inside_visual_bbox_before_translation(self):
        blocks = [
            {"id": "p004b0001", "text": "Table 1: Examples of inputs and outputs for all APIs used.", "xMin": 189.3, "yMin": 72.8, "xMax": 422.3, "yMax": 81.7},
            {"id": "p004b0002", "text": "API Name", "xMin": 113.9, "yMin": 94.3, "xMax": 154.0, "yMax": 102.4},
            {"id": "p004b0005", "text": "Question Answering", "xMin": 113.9, "yMin": 109.6, "xMax": 187.9, "yMax": 117.6},
            {
                "id": "p004b0008",
                "text": "Where was the Knights\nof Columbus founded?\nFishing Reel Types",
                "xMin": 200.0,
                "yMin": 109.6,
                "xMax": 285.1,
                "yMax": 137.5,
            },
            {"id": "p004b0009", "text": "Calculator\nCalendar\nMachine Translation", "xMin": 113.9, "yMin": 169.4, "xMax": 188.1, "yMax": 197.3},
            {"id": "p004b0010", "text": "27 + 4 * 2\nepsilon\nsurete nucleaire", "xMin": 200.0, "yMin": 169.4, "xMax": 256.5, "yMax": 197.3},
            {
                "id": "p004b0011",
                "text": "The calendar API converts date expressions into absolute dates for downstream tools.",
                "xMin": 289.0,
                "yMin": 169.4,
                "xMax": 420.0,
                "yMax": 197.3,
            },
            {
                "id": "p004b0012",
                "text": "Model Finetuning After sampling and filtering calls for all APIs, we merge the data.",
                "xMin": 107.6,
                "yMin": 221.6,
                "xMax": 504.1,
                "yMax": 328.8,
            },
        ]
        visual_regions = [
            {
                "source_ids": ["p004b0001", "p004b0002", "p004b0005", "p004b0009", "p004b0010"],
                "bbox": (113.9, 72.8, 422.3, 197.3),
                "has_caption_seed": True,
            }
        ]
        classes = {
            "p004b0001": "figure_region",
            "p004b0002": "figure_region",
            "p004b0005": "figure_region",
            "p004b0008": "body",
            "p004b0009": "figure_region",
            "p004b0010": "figure_region",
            "p004b0011": "body",
            "p004b0012": "body",
        }

        with (
            patch.object(parallel.pipeline, "build_visual_regions", return_value=visual_regions),
            patch.object(parallel.pipeline, "classify_blocks", return_value=classes),
        ):
            ownership_result = parallel.pipeline.build_translation_page_components(4, blocks, page_size=(612.0, 792.0))
            batches = parallel.build_page_batches([(4, blocks)], max_chars=7000, page_size=(612.0, 792.0))

        batch_ids = {item["id"] for batch in batches for item in batch.items}
        self.assertTrue({"p004b0008", "p004b0011", "p004b0012"} <= batch_ids)
        self.assertTrue({"p004b0008", "p004b0011", "p004b0012"} <= set(ownership_result.translatable_ids))

    def test_build_page_batches_consumes_pipeline_page_component_ownership(self):
        blocks = [
            {
                "id": "p001b0001",
                "text": "Figure 1: architecture",
                "xMin": 80.0,
                "yMin": 100.0,
                "xMax": 260.0,
                "yMax": 180.0,
            },
            {
                "id": "p001b0002",
                "text": "short diagram label",
                "xMin": 100.0,
                "yMin": 130.0,
                "xMax": 180.0,
                "yMax": 145.0,
            },
            {
                "id": "p001b0003",
                "text": "This body paragraph should still be translated.",
                "xMin": 80.0,
                "yMin": 220.0,
                "xMax": 360.0,
                "yMax": 245.0,
            },
        ]
        visual_regions = [{"source_ids": ["p001b0001"], "bbox": (78.0, 98.0, 262.0, 182.0)}]
        classes = {"p001b0001": "figure_region", "p001b0002": "body", "p001b0003": "body"}

        with (
            patch.object(parallel.pipeline, "build_visual_regions", return_value=visual_regions),
            patch.object(parallel.pipeline, "classify_blocks", return_value=classes),
        ):
            result = parallel.pipeline.build_translation_page_components(1, blocks)
            batches = parallel.build_page_batches([(1, blocks)], max_chars=7000)

        self.assertEqual(result.translatable_ids, ["p001b0003"])
        self.assertEqual([[item["id"] for item in batch.items] for batch in batches], [["p001b0003"]])

    def test_build_page_batches_routes_through_prepare_page_ownership(self):
        blocks = [
            {
                "id": "p001b0001",
                "text": "This body paragraph should be translated.",
                "xMin": 80.0,
                "yMin": 100.0,
                "xMax": 360.0,
                "yMax": 130.0,
            }
        ]
        ownership_result = SimpleNamespace(translatable_ids=["p001b0001"])

        with (
            patch.object(
                parallel.pipeline,
                "prepare_page_ownership",
                return_value=(ownership_result, True),
                create=True,
            ) as prepare_mock,
            patch.object(
                parallel.pipeline,
                "build_translation_page_components",
                side_effect=AssertionError("build_page_batches must use prepare_page_ownership"),
            ),
        ):
            batches = parallel.build_page_batches([(1, blocks)], max_chars=7000, page_size=(400, 400))

        prepare_mock.assert_called_once()
        _, kwargs = prepare_mock.call_args
        self.assertEqual(kwargs["page_size"], (400, 400))
        self.assertFalse(kwargs["in_reference_section"])
        self.assertEqual([[item["id"] for item in batch.items] for batch in batches], [["p001b0001"]])

    def test_build_page_batches_excludes_text_covered_by_final_visual_clip_padding(self):
        blocks = [
            {
                "id": "p001b0001",
                "text": "Figure 1",
                "xMin": 100.0,
                "yMin": 100.0,
                "xMax": 140.0,
                "yMax": 140.0,
            },
            {
                "id": "p001b0002",
                "text": "short protected-side label",
                "xMin": 145.0,
                "yMin": 110.0,
                "xMax": 150.0,
                "yMax": 120.0,
            },
            {
                "id": "p001b0003",
                "text": "This body paragraph should still be translated.",
                "xMin": 180.0,
                "yMin": 150.0,
                "xMax": 360.0,
                "yMax": 170.0,
            },
        ]
        visual_regions = [{"source_ids": ["p001b0001"], "bbox": (100.0, 100.0, 140.0, 140.0)}]
        classes = {
            "p001b0001": "figure_region",
            "p001b0002": "body",
            "p001b0003": "body",
        }
        with (
            patch.object(parallel.pipeline, "build_visual_regions", return_value=visual_regions),
            patch.object(parallel.pipeline, "classify_blocks", return_value=classes),
        ):
            batches = parallel.build_page_batches([(1, blocks)], max_chars=7000, page_size=(400, 400))

        self.assertEqual([[item["id"] for item in batch.items] for batch in batches], [["p001b0003"]])

    def test_build_page_batches_uses_bbox_lines_for_formula_final_clip_exclusion(self):
        blocks = [
            {
                "id": "p001b0001",
                "text": "x + y = z",
                "xMin": 100.0,
                "yMin": 100.0,
                "xMax": 160.0,
                "yMax": 140.0,
            },
            {
                "id": "p001b0002",
                "text": "short text covered only by line-refined formula clip",
                "xMin": 105.0,
                "yMin": 140.25,
                "xMax": 150.0,
                "yMax": 140.45,
            },
            {
                "id": "p001b0003",
                "text": "This body paragraph should still be translated.",
                "xMin": 105.0,
                "yMin": 180.0,
                "xMax": 320.0,
                "yMax": 198.0,
            },
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
                patch.object(parallel.pipeline, "build_visual_regions", return_value=visual_regions),
                patch.object(parallel.pipeline, "classify_blocks", return_value=classes),
            ):
                batches = parallel.build_page_batches(
                    [(1, blocks)],
                    max_chars=7000,
                    page_size=(400, 400),
                    job_paths={"bbox_path": bbox_path},
                )

        self.assertEqual([[item["id"] for item in batch.items] for batch in batches], [["p001b0003"]])

    def test_build_page_batches_excludes_translation_ineligible_classes(self):
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
        }
        blocks = [
            {
                "id": block_id,
                "text": f"Source text for {classification}",
                "xMin": 80.0,
                "yMin": 80.0 + idx * 20.0,
                "xMax": 360.0,
                "yMax": 94.0 + idx * 20.0,
            }
            for idx, (block_id, classification) in enumerate(class_by_id.items())
        ]
        with (
            patch.object(parallel.pipeline, "build_visual_regions", return_value=[]),
            patch.object(parallel.pipeline, "classify_blocks", return_value=class_by_id),
        ):
            batches = parallel.build_page_batches([(1, blocks)], max_chars=7000)

        sent_ids = [item["id"] for batch in batches for item in batch.items]
        self.assertEqual(sent_ids, ["p001b0001", "p001b0002", "p001b0003", "p001b0004"])

    def test_build_page_batches_keeps_references_untranslated_across_pages(self):
        page_10 = [
            {
                "id": "p010b0012",
                "text": "References",
                "xMin": 108.0,
                "yMin": 597.0,
                "xMax": 164.0,
                "yMax": 608.0,
            },
            {
                "id": "p010b0013",
                "text": "[1] Jimmy Lei Ba, Jamie Ryan Kiros, and Geoffrey E Hinton. Layer normalization.",
                "xMin": 113.0,
                "yMin": 616.0,
                "xMax": 504.0,
                "yMax": 636.0,
            },
        ]
        page_11 = [
            {
                "id": "p011b0001",
                "text": "[5] Kyunghyun Cho, Bart van Merrienboer, Caglar Gulcehre, and Yoshua Bengio. Learning phrase representations.",
                "xMin": 113.0,
                "yMin": 75.0,
                "xMax": 505.0,
                "yMax": 106.0,
            },
            {
                "id": "p011b0002",
                "text": "[6] Francois Chollet. Xception: Deep learning with depthwise separable convolutions.",
                "xMin": 113.0,
                "yMin": 117.0,
                "xMax": 504.0,
                "yMax": 137.0,
            },
            {
                "id": "p011b0022",
                "text": "11",
                "xMin": 301.0,
                "yMin": 743.0,
                "xMax": 311.0,
                "yMax": 752.0,
            },
        ]

        batches = parallel.build_page_batches([(10, page_10), (11, page_11)], max_chars=7000)

        sent_ids = [item["id"] for batch in batches for item in batch.items]
        self.assertEqual(sent_ids, [])

    def test_build_page_batches_keeps_wrapped_reference_continuation_untranslated(self):
        page_10 = [
            {
                "id": "p010b0012",
                "text": "References",
                "xMin": 108.0,
                "yMin": 597.0,
                "xMax": 164.0,
                "yMax": 608.0,
            },
            {
                "id": "p010b0013",
                "text": "[1] Jimmy Lei Ba, Jamie Ryan Kiros, and Geoffrey E Hinton. Layer normalization.",
                "xMin": 113.0,
                "yMin": 616.0,
                "xMax": 504.0,
                "yMax": 636.0,
            },
        ]
        page_11 = [
            {
                "id": "p011b0001",
                "text": "machine translation. CoRR, abs/1406.1078, 2014.",
                "xMin": 130.0,
                "yMin": 75.0,
                "xMax": 331.0,
                "yMax": 95.0,
            },
            {
                "id": "p011b0002",
                "text": "[6] Francois Chollet. Xception: Deep learning with depthwise separable convolutions.",
                "xMin": 113.0,
                "yMin": 118.0,
                "xMax": 504.0,
                "yMax": 138.0,
            },
            {
                "id": "p011b0003",
                "text": "11",
                "xMin": 301.0,
                "yMin": 743.0,
                "xMax": 311.0,
                "yMax": 752.0,
            },
        ]

        batches = parallel.build_page_batches([(10, page_10), (11, page_11)], max_chars=7000)

        sent_ids = [item["id"] for batch in batches for item in batch.items]
        self.assertEqual(sent_ids, [])

    def test_build_page_batches_keeps_author_year_references_untranslated_across_pages(self):
        page_10 = [
            {
                "id": "p010b0001",
                "text": "References",
                "xMin": 72.0,
                "yMin": 65.0,
                "xMax": 128.0,
                "yMax": 76.0,
            },
            {
                "id": "p010b0002",
                "text": "Alan Akbik, Duncan Blythe, and Roland Vollgraf. 2018. Contextual string embeddings for sequence labeling.",
                "xMin": 72.0,
                "yMin": 87.0,
                "xMax": 290.0,
                "yMax": 140.0,
            },
        ]
        page_11 = [
            {
                "id": "p011b0001",
                "text": "Mandar Joshi, Eunsol Choi, Daniel S Weld, and Luke Zettlemoyer. 2017. Triviaqa: A large scale distantly supervised challenge dataset for reading comprehension. In ACL.",
                "xMin": 72.0,
                "yMin": 67.0,
                "xMax": 290.0,
                "yMax": 109.0,
            },
            {
                "id": "p011b0002",
                "text": "Matthew Peters, Mark Neumann, Luke Zettlemoyer, and Wen-tau Yih. 2018b. Dissecting contextual word embeddings: Architecture and representation.",
                "xMin": 307.0,
                "yMin": 67.0,
                "xMax": 526.0,
                "yMax": 140.0,
            },
        ]

        batches = parallel.build_page_batches([(10, page_10), (11, page_11)], max_chars=7000)

        sent_ids = [item["id"] for batch in batches for item in batch.items]
        self.assertEqual(sent_ids, [])

    def test_build_page_batches_keeps_mixed_reference_appendix_page_selective(self):
        page_10 = [
            {
                "id": "p010b0001",
                "text": "References",
                "xMin": 72.0,
                "yMin": 65.0,
                "xMax": 128.0,
                "yMax": 76.0,
            },
            {
                "id": "p010b0002",
                "text": "Yacine Jernite, Samuel R. Bowman, and David Sontag. 2017. Discourse-based objectives for fast unsupervised sentence representation learning.",
                "xMin": 307.0,
                "yMin": 723.0,
                "xMax": 526.0,
                "yMax": 765.0,
            },
        ]
        page_11 = [
            {
                "id": "p011b0001",
                "text": "Mandar Joshi, Eunsol Choi, Daniel S Weld, and Luke Zettlemoyer. 2017. Triviaqa: A large scale distantly supervised challenge dataset for reading comprehension.",
                "xMin": 72.0,
                "yMin": 67.0,
                "xMax": 290.0,
                "yMax": 109.0,
            },
        ]
        mixed_page_12 = [
            {
                "id": "p012b0001",
                "text": "for natural language understanding. In Proceedings of the 2018 EMNLP Workshop BlackboxNLP: Analyzing and Interpreting Neural Networks for NLP, pages 353-355.",
                "xMin": 83.0,
                "yMin": 67.0,
                "xMax": 290.0,
                "yMax": 109.0,
            },
            {
                "id": "p012b0002",
                "text": "Wei Wang, Ming Yan, and Chen Wu. 2018b. Multi-granularity hierarchical attention fusion networks for reading comprehension and question answering. In Proceedings of ACL.",
                "xMin": 72.0,
                "yMin": 122.0,
                "xMax": 290.0,
                "yMax": 196.0,
            },
            {
                "id": "p012b0003",
                "text": "Appendix for BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding",
                "xMin": 86.0,
                "yMin": 670.0,
                "xMax": 276.0,
                "yMax": 708.0,
            },
            {
                "id": "p012b0004",
                "text": "Additional Details for BERT",
                "xMin": 328.0,
                "yMin": 222.0,
                "xMax": 474.0,
                "yMax": 233.0,
            },
            {
                "id": "p012b0005",
                "text": "We provide examples of the pre-training tasks in the following.",
                "xMin": 307.0,
                "yMin": 262.0,
                "xMax": 526.0,
                "yMax": 285.0,
            },
        ]

        batches = parallel.build_page_batches(
            [(10, page_10), (11, page_11), (12, mixed_page_12)],
            max_chars=7000,
        )

        sent_ids = [item["id"] for batch in batches for item in batch.items]
        self.assertEqual(sent_ids, ["p012b0003", "p012b0004", "p012b0005"])

    def test_build_page_batches_stops_reference_carryover_on_non_reference_page(self):
        page_10 = [
            {
                "id": "p010b0012",
                "text": "References",
                "xMin": 108.0,
                "yMin": 597.0,
                "xMax": 164.0,
                "yMax": 608.0,
            },
            {
                "id": "p010b0013",
                "text": "[1] Jimmy Lei Ba, Jamie Ryan Kiros, and Geoffrey E Hinton. Layer normalization.",
                "xMin": 113.0,
                "yMin": 616.0,
                "xMax": 504.0,
                "yMax": 636.0,
            },
        ]
        figure_page = [
            {
                "id": "p013b0040",
                "text": "Figure 3: An example of the attention mechanism following long-distance dependencies.",
                "xMin": 108.0,
                "yMin": 313.0,
                "xMax": 504.0,
                "yMax": 355.0,
            },
            {
                "id": "p013b0041",
                "text": "13",
                "xMin": 301.0,
                "yMin": 743.0,
                "xMax": 311.0,
                "yMax": 752.0,
            },
            {
                "id": "p013b0042",
                "text": "This normal paragraph should be translated after the references section has ended.",
                "xMin": 108.0,
                "yMin": 380.0,
                "xMax": 504.0,
                "yMax": 410.0,
            },
        ]

        batches = parallel.build_page_batches([(10, page_10), (13, figure_page)], max_chars=7000)

        sent_ids = [item["id"] for batch in batches for item in batch.items]
        self.assertEqual(sent_ids, ["p013b0042"])


class ParallelArtifactReportTests(unittest.TestCase):
    def test_run_qa_reports_sorted_plan_artifact_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "job"
            plans_dir = job_dir / "plans"
            job_dir.mkdir()
            selected_pages = [(2, []), (1, [])]
            job_paths = {"job_dir": job_dir, "plans_dir": plans_dir}
            args = SimpleNamespace(
                strict_qa=False,
                qa_mode="sample",
                qa_sample_size=10,
                qa_batch_chars=7000,
                model="test-model",
                reasoning_effort="low",
                retries=1,
            )

            with (
                patch.object(parallel.pipeline, "validate_document_quality", return_value=["layout issue"]),
                patch.object(parallel.qa, "choose_items_for_qa", return_value=[]),
                patch.object(parallel.qa, "run_backtranslation", return_value={}),
                patch.object(parallel.qa, "build_report", return_value=[]),
                patch.object(parallel.qa, "write_markdown"),
            ):
                result = parallel.run_qa_for_job(
                    selected_pages,
                    translations={},
                    job_paths=job_paths,
                    page_size=(612, 792),
                    args=args,
                    render_result=parallel.pipeline.DocumentRenderResult([], {}),
                )

            expected_paths = [
                str(plans_dir / "page-001.render-plan.json"),
                str(plans_dir / "page-002.render-plan.json"),
            ]
            self.assertEqual(result["plan_artifact_paths"], expected_paths)

            report_json = json.loads(
                (job_dir / "deterministic_quality_report.json").read_text(encoding="utf-8")
            )
            self.assertEqual(report_json["plan_artifact_paths"], expected_paths)

            report_md = (job_dir / "deterministic_quality_report.md").read_text(encoding="utf-8")
            self.assertIn("## Plan Artifacts", report_md)
            self.assertLess(report_md.index(expected_paths[0]), report_md.index(expected_paths[1]))

    def test_write_summary_lists_sorted_qa_plan_artifact_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            original_tmp_dir = parallel.TMP_DIR
            original_summary_json = parallel.SUMMARY_JSON
            original_summary_md = parallel.SUMMARY_MD
            original_pipeline_tmp_root = parallel.pipeline.TMP_ROOT

            def restore_work_dirs():
                parallel.TMP_DIR = original_tmp_dir
                parallel.SUMMARY_JSON = original_summary_json
                parallel.SUMMARY_MD = original_summary_md
                parallel.pipeline.TMP_ROOT = original_pipeline_tmp_root

            self.addCleanup(restore_work_dirs)
            parallel.set_work_dir(Path(tmp))
            result = {
                "pdf": "/docs/example.pdf",
                "output": "/out/example-Chinese.pdf",
                "status": "translated",
                "qa": {
                    "deterministic_issue_count": 0,
                    "deterministic_issues": [],
                    "checked_blocks": 0,
                    "worst_score": None,
                    "worst_items": [],
                    "plan_artifact_paths": [
                        "/work/job/plans/page-003.render-plan.json",
                        "/work/job/plans/page-001.render-plan.json",
                    ],
                    "visual_issue_count": 2,
                    "visual_error_count": 1,
                    "visual_warning_count": 1,
                    "visual_report_json": "/work/job/visual_qa/visual_qa_report.json",
                    "visual_report_md": "/work/job/visual_qa/visual_qa_report.md",
                    "visual_checked_pages": [3, 1],
                    "visual_highest_severity": "error",
                    "visual_highest_severity_issues": [
                        {
                            "category": "blank_clip",
                            "severity": "error",
                            "page_num": 3,
                            "message": "blank source image clip",
                            "source_ids": ["p003b0001"],
                        }
                    ],
                },
            }

            parallel.write_summary([result])

            summary_json = json.loads(parallel.SUMMARY_JSON.read_text(encoding="utf-8"))
            self.assertEqual(
                summary_json[0]["qa"]["plan_artifact_paths"],
                [
                    "/work/job/plans/page-001.render-plan.json",
                    "/work/job/plans/page-003.render-plan.json",
                ],
            )
            self.assertEqual(summary_json[0]["qa"]["visual_checked_pages"], [1, 3])

            summary_md = parallel.SUMMARY_MD.read_text(encoding="utf-8")
            self.assertIn("- plan_artifact: /work/job/plans/page-001.render-plan.json", summary_md)
            self.assertIn("- plan_artifact: /work/job/plans/page-003.render-plan.json", summary_md)
            self.assertIn("- visual_issue_count: 2", summary_md)
            self.assertIn("- visual_error_count: 1", summary_md)
            self.assertIn("- visual_warning_count: 1", summary_md)
            self.assertIn("- visual_report_json: /work/job/visual_qa/visual_qa_report.json", summary_md)
            self.assertIn("- visual_report_md: /work/job/visual_qa/visual_qa_report.md", summary_md)
            self.assertIn("- visual_checked_pages: 1, 3", summary_md)
            self.assertIn("- visual_highest_severity: error", summary_md)
            self.assertIn("- visual_issue: page 003 [error] blank_clip: blank source image clip", summary_md)
            self.assertLess(
                summary_md.index("/work/job/plans/page-001.render-plan.json"),
                summary_md.index("/work/job/plans/page-003.render-plan.json"),
            )

    def test_run_qa_writes_visual_qa_report_after_vector_rendering(self):
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "job"
            plans_dir = job_dir / "plans"
            pages_dir = job_dir / "pages"
            job_dir.mkdir()
            plans_dir.mkdir()
            pages_dir.mkdir()
            (plans_dir / "page-001.render-plan.json").write_text("{}", encoding="utf-8")
            (pages_dir / "page-001.png").write_bytes(b"source png")
            output_pdf = Path(tmp) / "translated.pdf"
            output_pdf.write_bytes(b"%PDF-1.4\n")
            selected_pages = [(1, [{"id": "p001b0001", "text": "Body"}])]
            job_paths = {
                "job_dir": job_dir,
                "plans_dir": plans_dir,
                "pages_dir": pages_dir,
            }
            args = SimpleNamespace(
                strict_qa=False,
                qa_mode="sample",
                qa_sample_size=10,
                qa_batch_chars=7000,
                model="test-model",
                reasoning_effort="low",
                retries=1,
                render_mode="vector",
                strict_body_flow=False,
            )
            visual_report = SimpleNamespace(
                json_path=job_dir / "visual_qa" / "visual_qa_report.json",
                markdown_path=job_dir / "visual_qa" / "visual_qa_report.md",
                issue_count=2,
                error_count=1,
                warning_count=1,
            )

            with (
                patch.object(parallel.pipeline, "validate_document_quality", return_value=[]),
                patch.object(parallel.qa, "choose_items_for_qa", return_value=[]),
                patch.object(parallel.qa, "run_backtranslation", return_value={}),
                patch.object(parallel.qa, "build_report", return_value=[]),
                patch.object(parallel.qa, "write_markdown"),
                patch.object(parallel.qa_visual, "generate_visual_qa_report", return_value=visual_report) as visual_mock,
            ):
                result = parallel.run_qa_for_job(
                    selected_pages,
                    translations={},
                    job_paths=job_paths,
                    page_size=(612, 792),
                    args=args,
                    output_pdf_path=output_pdf,
                    render_result=parallel.pipeline.DocumentRenderResult([], {}),
                )

            visual_mock.assert_called_once()
            _, kwargs = visual_mock.call_args
            self.assertEqual(kwargs["output_dir"], job_dir / "visual_qa")
            self.assertEqual(kwargs["translated_pdf_path"], output_pdf)
            self.assertEqual(
                kwargs["source_png_paths"],
                {1: pages_dir / "page-001.png"},
            )
            self.assertEqual(
                kwargs["source_blocks_by_page"],
                {1: [{"id": "p001b0001", "text": "Body"}]},
            )
            self.assertEqual(kwargs["page_size"], (612, 792))
            self.assertFalse(kwargs["strict_body_flow"])
            self.assertEqual(result["visual_issue_count"], 2)
            self.assertEqual(result["visual_error_count"], 1)
            self.assertEqual(result["visual_warning_count"], 1)
            self.assertEqual(result["visual_report_json"], str(visual_report.json_path))
            self.assertEqual(result["visual_report_md"], str(visual_report.markdown_path))

    def test_run_qa_includes_visual_checked_pages_and_highest_severity(self):
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "job"
            plans_dir = job_dir / "plans"
            pages_dir = job_dir / "pages"
            visual_dir = job_dir / "visual_qa"
            job_dir.mkdir()
            plans_dir.mkdir()
            pages_dir.mkdir()
            visual_dir.mkdir()
            (plans_dir / "page-001.render-plan.json").write_text("{}", encoding="utf-8")
            (pages_dir / "page-001.png").write_bytes(b"source png")
            output_pdf = Path(tmp) / "translated.pdf"
            output_pdf.write_bytes(b"%PDF-1.4\n")
            report_json_path = visual_dir / "visual_qa_report.json"
            report_json_path.write_text(
                json.dumps(
                    {
                        "checked_pages": [2, 1],
                        "highest_severity": "error",
                        "issues": [
                            {
                                "category": "blank_clip",
                                "severity": "error",
                                "page_num": 2,
                                "message": "blank source image clip",
                                "source_ids": ["p002b0003"],
                            },
                            {
                                "category": "body_flow_whitespace",
                                "severity": "warning",
                                "page_num": 1,
                                "message": "body flow gap is 60.0pt",
                                "source_ids": ["p001b0001", "p001b0002"],
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            selected_pages = [(1, [{"id": "p001b0001", "text": "Body"}])]
            job_paths = {
                "job_dir": job_dir,
                "plans_dir": plans_dir,
                "pages_dir": pages_dir,
            }
            args = SimpleNamespace(
                strict_qa=False,
                qa_mode="sample",
                qa_sample_size=10,
                qa_batch_chars=7000,
                model="test-model",
                reasoning_effort="low",
                retries=1,
                render_mode="vector",
                strict_body_flow=False,
            )
            visual_report = SimpleNamespace(
                json_path=report_json_path,
                markdown_path=visual_dir / "visual_qa_report.md",
                issue_count=2,
                error_count=1,
                warning_count=1,
            )

            with (
                patch.object(parallel.pipeline, "validate_document_quality", return_value=[]),
                patch.object(parallel.qa, "choose_items_for_qa", return_value=[]),
                patch.object(parallel.qa, "run_backtranslation", return_value={}),
                patch.object(parallel.qa, "build_report", return_value=[]),
                patch.object(parallel.qa, "write_markdown"),
                patch.object(parallel.qa_visual, "generate_visual_qa_report", return_value=visual_report),
            ):
                result = parallel.run_qa_for_job(
                    selected_pages,
                    translations={},
                    job_paths=job_paths,
                    page_size=(612, 792),
                    args=args,
                    output_pdf_path=output_pdf,
                    render_result=parallel.pipeline.DocumentRenderResult([], {}),
                )

            self.assertEqual(result["visual_checked_pages"], [1, 2])
            self.assertEqual(result["visual_highest_severity"], "error")
            self.assertEqual(
                result["visual_highest_severity_issues"],
                [
                    {
                        "category": "blank_clip",
                        "severity": "error",
                        "page_num": 2,
                        "message": "blank source image clip",
                        "source_ids": ["p002b0003"],
                    }
                ],
            )

    def test_run_qa_strict_mode_fails_on_visual_qa_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "job"
            plans_dir = job_dir / "plans"
            pages_dir = job_dir / "pages"
            job_dir.mkdir()
            plans_dir.mkdir()
            pages_dir.mkdir()
            (plans_dir / "page-001.render-plan.json").write_text("{}", encoding="utf-8")
            (pages_dir / "page-001.png").write_bytes(b"source png")
            output_pdf = Path(tmp) / "translated.pdf"
            output_pdf.write_bytes(b"%PDF-1.4\n")
            selected_pages = [(1, [{"id": "p001b0001", "text": "Body"}])]
            job_paths = {
                "job_dir": job_dir,
                "plans_dir": plans_dir,
                "pages_dir": pages_dir,
            }
            args = SimpleNamespace(
                strict_qa=True,
                qa_mode="sample",
                qa_sample_size=10,
                qa_batch_chars=7000,
                model="test-model",
                reasoning_effort="low",
                retries=1,
                render_mode="vector",
                strict_body_flow=True,
            )
            visual_report = SimpleNamespace(
                json_path=job_dir / "visual_qa" / "visual_qa_report.json",
                markdown_path=job_dir / "visual_qa" / "visual_qa_report.md",
                issue_count=1,
                error_count=1,
                warning_count=0,
            )

            with (
                patch.object(parallel.pipeline, "validate_document_quality", return_value=[]),
                patch.object(parallel.qa, "choose_items_for_qa", return_value=[]),
                patch.object(parallel.qa, "run_backtranslation", return_value={}),
                patch.object(parallel.qa, "build_report", return_value=[]),
                patch.object(parallel.qa, "write_markdown"),
                patch.object(parallel.qa_visual, "generate_visual_qa_report", return_value=visual_report),
            ):
                with self.assertRaisesRegex(RuntimeError, "visual QA found 1 error"):
                    parallel.run_qa_for_job(
                        selected_pages,
                        translations={},
                        job_paths=job_paths,
                        page_size=(612, 792),
                        args=args,
                        output_pdf_path=output_pdf,
                        render_result=parallel.pipeline.DocumentRenderResult([], {}),
                    )

    def test_run_qa_strict_mode_allows_visual_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "job"
            plans_dir = job_dir / "plans"
            pages_dir = job_dir / "pages"
            job_dir.mkdir()
            plans_dir.mkdir()
            pages_dir.mkdir()
            (plans_dir / "page-001.render-plan.json").write_text("{}", encoding="utf-8")
            (pages_dir / "page-001.png").write_bytes(b"source png")
            output_pdf = Path(tmp) / "translated.pdf"
            output_pdf.write_bytes(b"%PDF-1.4\n")
            selected_pages = [(1, [{"id": "p001b0001", "text": "Body"}])]
            job_paths = {
                "job_dir": job_dir,
                "plans_dir": plans_dir,
                "pages_dir": pages_dir,
            }
            args = SimpleNamespace(
                strict_qa=True,
                qa_mode="sample",
                qa_sample_size=10,
                qa_batch_chars=7000,
                model="test-model",
                reasoning_effort="low",
                retries=1,
                render_mode="vector",
                strict_body_flow=False,
            )
            visual_report = SimpleNamespace(
                json_path=job_dir / "visual_qa" / "visual_qa_report.json",
                markdown_path=job_dir / "visual_qa" / "visual_qa_report.md",
                issue_count=1,
                error_count=0,
                warning_count=1,
            )

            with (
                patch.object(parallel.pipeline, "validate_document_quality", return_value=[]),
                patch.object(parallel.qa, "choose_items_for_qa", return_value=[]),
                patch.object(parallel.qa, "run_backtranslation", return_value={}),
                patch.object(parallel.qa, "build_report", return_value=[]),
                patch.object(parallel.qa, "write_markdown"),
                patch.object(parallel.qa_visual, "generate_visual_qa_report", return_value=visual_report),
            ):
                result = parallel.run_qa_for_job(
                    selected_pages,
                    translations={},
                    job_paths=job_paths,
                    page_size=(612, 792),
                    args=args,
                    output_pdf_path=output_pdf,
                    render_result=parallel.pipeline.DocumentRenderResult([], {}),
                )

            self.assertEqual(result["visual_issue_count"], 1)
            self.assertEqual(result["visual_error_count"], 0)
            self.assertEqual(result["visual_warning_count"], 1)

    def test_run_qa_non_strict_reports_visual_errors_without_failing(self):
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "job"
            plans_dir = job_dir / "plans"
            pages_dir = job_dir / "pages"
            job_dir.mkdir()
            plans_dir.mkdir()
            pages_dir.mkdir()
            (plans_dir / "page-001.render-plan.json").write_text("{}", encoding="utf-8")
            (pages_dir / "page-001.png").write_bytes(b"source png")
            output_pdf = Path(tmp) / "translated.pdf"
            output_pdf.write_bytes(b"%PDF-1.4\n")
            selected_pages = [(1, [{"id": "p001b0001", "text": "Body"}])]
            job_paths = {
                "job_dir": job_dir,
                "plans_dir": plans_dir,
                "pages_dir": pages_dir,
            }
            args = SimpleNamespace(
                strict_qa=False,
                qa_mode="sample",
                qa_sample_size=10,
                qa_batch_chars=7000,
                model="test-model",
                reasoning_effort="low",
                retries=1,
                render_mode="vector",
                strict_body_flow=False,
            )
            visual_report = SimpleNamespace(
                json_path=job_dir / "visual_qa" / "visual_qa_report.json",
                markdown_path=job_dir / "visual_qa" / "visual_qa_report.md",
                issue_count=3,
                error_count=2,
                warning_count=1,
            )

            with (
                patch.object(parallel.pipeline, "validate_document_quality", return_value=[]),
                patch.object(parallel.qa, "choose_items_for_qa", return_value=[]),
                patch.object(parallel.qa, "run_backtranslation", return_value={}),
                patch.object(parallel.qa, "build_report", return_value=[]),
                patch.object(parallel.qa, "write_markdown"),
                patch.object(parallel.qa_visual, "generate_visual_qa_report", return_value=visual_report),
            ):
                result = parallel.run_qa_for_job(
                    selected_pages,
                    translations={},
                    job_paths=job_paths,
                    page_size=(612, 792),
                    args=args,
                    output_pdf_path=output_pdf,
                    render_result=parallel.pipeline.DocumentRenderResult([], {}),
                )

            self.assertEqual(result["visual_issue_count"], 3)
            self.assertEqual(result["visual_error_count"], 2)
            self.assertEqual(result["visual_warning_count"], 1)
            self.assertEqual(result["visual_report_json"], str(visual_report.json_path))
            self.assertEqual(result["visual_report_md"], str(visual_report.markdown_path))

    def test_run_qa_strict_mode_fails_on_ownership_visual_qa_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "job"
            plans_dir = job_dir / "plans"
            pages_dir = job_dir / "pages"
            visual_dir = job_dir / "visual_qa"
            job_dir.mkdir()
            plans_dir.mkdir()
            pages_dir.mkdir()
            visual_dir.mkdir()
            (plans_dir / "page-001.render-plan.json").write_text("{}", encoding="utf-8")
            (pages_dir / "page-001.png").write_bytes(b"source png")
            output_pdf = Path(tmp) / "translated.pdf"
            output_pdf.write_bytes(b"%PDF-1.4\n")
            report_json_path = visual_dir / "visual_qa_report.json"
            report_json_path.write_text(
                json.dumps(
                    {
                        "checked_pages": [1],
                        "highest_severity": "error",
                        "issues": [
                            {
                                "category": "ownership_validation",
                                "render_kind": "ownership",
                                "severity": "error",
                                "page_num": 1,
                                "message": "source block p001b0001 has no owner",
                                "source_ids": ["p001b0001"],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            selected_pages = [(1, [{"id": "p001b0001", "text": "Body"}])]
            job_paths = {
                "job_dir": job_dir,
                "plans_dir": plans_dir,
                "pages_dir": pages_dir,
            }
            args = SimpleNamespace(
                strict_qa=True,
                qa_mode="sample",
                qa_sample_size=10,
                qa_batch_chars=7000,
                model="test-model",
                reasoning_effort="low",
                retries=1,
                render_mode="vector",
                strict_body_flow=False,
            )
            visual_report = SimpleNamespace(
                json_path=report_json_path,
                markdown_path=visual_dir / "visual_qa_report.md",
                issue_count=1,
                error_count=1,
                warning_count=0,
            )

            with (
                patch.object(parallel.pipeline, "validate_document_quality", return_value=[]),
                patch.object(parallel.qa, "choose_items_for_qa", return_value=[]),
                patch.object(parallel.qa, "run_backtranslation", return_value={}),
                patch.object(parallel.qa, "build_report", return_value=[]),
                patch.object(parallel.qa, "write_markdown"),
                patch.object(parallel.qa_visual, "generate_visual_qa_report", return_value=visual_report),
            ):
                with self.assertRaisesRegex(RuntimeError, "strict QA failed with 1 ownership error"):
                    parallel.run_qa_for_job(
                        selected_pages,
                        translations={},
                        job_paths=job_paths,
                        page_size=(612, 792),
                        args=args,
                        output_pdf_path=output_pdf,
                        render_result=parallel.pipeline.DocumentRenderResult([], {}),
                    )

    def test_run_qa_non_strict_reports_ownership_visual_qa_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "job"
            plans_dir = job_dir / "plans"
            pages_dir = job_dir / "pages"
            visual_dir = job_dir / "visual_qa"
            job_dir.mkdir()
            plans_dir.mkdir()
            pages_dir.mkdir()
            visual_dir.mkdir()
            (plans_dir / "page-001.render-plan.json").write_text("{}", encoding="utf-8")
            (pages_dir / "page-001.png").write_bytes(b"source png")
            output_pdf = Path(tmp) / "translated.pdf"
            output_pdf.write_bytes(b"%PDF-1.4\n")
            report_json_path = visual_dir / "visual_qa_report.json"
            report_json_path.write_text(
                json.dumps(
                    {
                        "checked_pages": [1],
                        "highest_severity": "warning",
                        "issues": [
                            {
                                "category": "ownership_validation",
                                "render_kind": "ownership",
                                "severity": "error",
                                "page_num": 1,
                                "message": "source block p001b0001 has no owner",
                                "source_ids": ["p001b0001"],
                            },
                            {
                                "category": "ownership_validation",
                                "render_kind": "ownership",
                                "severity": "warning",
                                "page_num": 1,
                                "message": "ownership split is conservative",
                                "source_ids": ["p001b0002"],
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            selected_pages = [(1, [{"id": "p001b0001", "text": "Body"}])]
            job_paths = {
                "job_dir": job_dir,
                "plans_dir": plans_dir,
                "pages_dir": pages_dir,
            }
            args = SimpleNamespace(
                strict_qa=False,
                qa_mode="sample",
                qa_sample_size=10,
                qa_batch_chars=7000,
                model="test-model",
                reasoning_effort="low",
                retries=1,
                render_mode="vector",
                strict_body_flow=False,
            )
            visual_report = SimpleNamespace(
                json_path=report_json_path,
                markdown_path=visual_dir / "visual_qa_report.md",
                issue_count=2,
                error_count=1,
                warning_count=1,
            )

            with (
                patch.object(parallel.pipeline, "validate_document_quality", return_value=[]),
                patch.object(parallel.qa, "choose_items_for_qa", return_value=[]),
                patch.object(parallel.qa, "run_backtranslation", return_value={}),
                patch.object(parallel.qa, "build_report", return_value=[]),
                patch.object(parallel.qa, "write_markdown"),
                patch.object(parallel.qa_visual, "generate_visual_qa_report", return_value=visual_report),
            ):
                result = parallel.run_qa_for_job(
                    selected_pages,
                    translations={},
                    job_paths=job_paths,
                    page_size=(612, 792),
                    args=args,
                    output_pdf_path=output_pdf,
                    render_result=parallel.pipeline.DocumentRenderResult([], {}),
                )

            self.assertEqual(result["ownership_issue_count"], 2)
            self.assertEqual(result["ownership_error_count"], 1)


class ParallelCliIntegrationTests(unittest.TestCase):
    def test_main_non_strict_visual_errors_write_success_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_dir = tmp_path / "source"
            target_dir = tmp_path / "target"
            work_dir = tmp_path / "work"
            source_dir.mkdir()
            pdf_path = source_dir / "paper.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n")

            def fake_translate_one_pdf(pdf_path_arg, output_dir_arg, args):
                self.assertFalse(args.strict_qa)
                return {
                    "pdf": str(pdf_path_arg),
                    "output": str(output_dir_arg / "paper-Chinese.pdf"),
                    "status": "translated",
                    "qa": {
                        "deterministic_issue_count": 0,
                        "deterministic_issues": [],
                        "checked_blocks": 0,
                        "worst_score": None,
                        "worst_items": [],
                        "plan_artifact_paths": [],
                        "visual_issue_count": 2,
                        "visual_error_count": 1,
                        "visual_warning_count": 1,
                        "visual_report_json": str(work_dir / "jobs" / "paper" / "visual_qa" / "visual_qa_report.json"),
                        "visual_report_md": str(work_dir / "jobs" / "paper" / "visual_qa" / "visual_qa_report.md"),
                        "visual_checked_pages": [1],
                        "visual_highest_severity": "error",
                        "visual_highest_severity_issues": [
                            {
                                "category": "blank_clip",
                                "severity": "error",
                                "page_num": 1,
                                "message": "blank source image clip",
                                "source_ids": ["p001b0001"],
                            }
                        ],
                    },
                }

            argv = [
                "translate_pdf_parallel.py",
                "--source-dir",
                str(source_dir),
                "--target-dir",
                str(target_dir),
                "--work-dir",
                str(work_dir),
            ]
            with (
                patch.object(sys, "argv", argv),
                patch.object(parallel, "translate_one_pdf", side_effect=fake_translate_one_pdf),
            ):
                parallel.main()

            summary_json = json.loads((work_dir / "parallel_translation_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary_json[0]["status"], "translated")
            self.assertEqual(summary_json[0]["qa"]["visual_error_count"], 1)
            summary_md = (work_dir / "parallel_translation_summary.md").read_text(encoding="utf-8")
            self.assertIn("- status: translated", summary_md)
            self.assertIn("- visual_error_count: 1", summary_md)
            self.assertIn("- visual_highest_severity: error", summary_md)

    def test_main_strict_visual_error_with_continue_on_error_writes_failed_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_dir = tmp_path / "source"
            target_dir = tmp_path / "target"
            work_dir = tmp_path / "work"
            source_dir.mkdir()
            pdf_path = source_dir / "paper.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n")

            def fake_translate_one_pdf(_pdf_path_arg, _output_dir_arg, args):
                self.assertTrue(args.strict_qa)
                raise RuntimeError("visual QA found 1 error(s); see visual_qa_report.md")

            argv = [
                "translate_pdf_parallel.py",
                "--source-dir",
                str(source_dir),
                "--target-dir",
                str(target_dir),
                "--work-dir",
                str(work_dir),
                "--strict-qa",
                "--continue-on-error",
            ]
            with (
                patch.object(sys, "argv", argv),
                patch.object(parallel, "translate_one_pdf", side_effect=fake_translate_one_pdf),
            ):
                with self.assertRaisesRegex(RuntimeError, "1 PDF\\(s\\) failed"):
                    parallel.main()

            summary_json = json.loads((work_dir / "parallel_translation_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary_json[0]["status"], "failed")
            self.assertIn("visual QA found 1 error", summary_json[0]["error"])
            summary_md = (work_dir / "parallel_translation_summary.md").read_text(encoding="utf-8")
            self.assertIn("- status: failed", summary_md)
            self.assertIn("- error: visual QA found 1 error(s); see visual_qa_report.md", summary_md)


if __name__ == "__main__":
    unittest.main()
