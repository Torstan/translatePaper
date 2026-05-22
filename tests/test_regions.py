import json
from pathlib import Path
import unittest

import regions
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


class RegionExtractionModuleTests(unittest.TestCase):
    def test_build_visual_regions_direct_api_matches_pipeline_compatibility(self):
        blocks = [
            block("p301b0001", 301, "1: procedure Enq(x)", x0=100, y0=100, x1=245, y1=114),
            block("p301b0002", 301, "2: if tail = null then", x0=100, y0=116, x1=270, y1=130),
            block("p301b0003", 301, "3: return false", x0=100, y0=132, x1=230, y1=146),
            block(
                "p301b0004",
                301,
                "The paragraph after the pseudocode should not be part of the visual region.",
                x0=100,
                y0=178,
                x1=480,
                y1=214,
            ),
        ]

        expected = [
            {
                "source_ids": ["p301b0001", "p301b0002", "p301b0003"],
                "bbox": (100, 100, 270, 146),
                "has_code_seed": True,
                "has_caption_seed": False,
                "has_row_cell_seed": False,
            }
        ]
        self.assertEqual(regions.build_visual_regions(blocks), expected)
        self.assertIs(pdf.build_visual_regions, regions.build_visual_regions)
        self.assertIs(pdf.heuristic_heading_from_block, regions.heuristic_heading_from_block)

    def test_protected_region_capping_direct_api_matches_pipeline_compatibility(self):
        blocks = [
            block("p401b0001", 401, "The paragraph before the display.", x0=90, y0=100, x1=500, y1=118),
            block("p401b0002", 401, "x := y + z", x0=120, y0=130, x1=220, y1=144),
            block("p401b0003", 401, "The following paragraph remains translated.", x0=90, y0=152, x1=500, y1=174),
        ]
        classes = {"p401b0001": "body", "p401b0002": "formula_region", "p401b0003": "body"}
        visual_ids = {"p401b0002"}
        region_bbox = (112.0, 126.0, 230.0, 148.0)
        oversized_bbox = (104.0, 92.0, 238.0, 166.0)

        capped = regions.cap_visual_bbox_after_preceding_text(region_bbox, oversized_bbox, blocks, classes, visual_ids)

        self.assertEqual(capped, (104.0, 118, 238.0, 166.0))
        self.assertIs(pdf.cap_visual_bbox_after_preceding_text, regions.cap_visual_bbox_after_preceding_text)

    def test_visual_clip_side_intrusion_is_capped_before_adjacent_body_column(self):
        blocks = [
            block("p016b0012", 16, "MNLI Dev Accuracy", x0=75.6, y0=596.9, x1=81.8, y1=656.3),
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
        region_bbox = (70.480, 564.319, 290.271, 763.124)
        oversized_bbox = (70.480, 564.319, 312.094, 763.124)

        capped = regions.cap_visual_bbox_against_adjacent_translated_text(
            region_bbox,
            oversized_bbox,
            blocks,
            classes,
            {"p016b0012"},
            (623.0, 801.0),
        )

        self.assertLess(capped[2], blocks[1]["xMin"])
        self.assertIs(
            pdf.cap_visual_bbox_against_adjacent_translated_text,
            regions.cap_visual_bbox_against_adjacent_translated_text,
        )

    def test_diagram_region_above_caption_ignores_distant_title_and_authors(self):
        blocks = [
            block(
                "p001b0001",
                1,
                "Chain-of-Thought Prompting Elicits Reasoning\nin Large Language Models",
                x0=132.0,
                y0=101.0,
                x1=480.0,
                y1=137.0,
            ),
            block("p001b0002", 1, "Jason Wei", x0=152.0, y0=182.0, x1=195.0, y1=191.0),
            block("p001b0003", 1, "Xuezhi Wang", x0=217.0, y0=182.0, x1=274.0, y1=191.0),
            block("p001b0004", 1, "Brian Ichter", x0=155.0, y0=200.0, x1=208.0, y1=209.0),
            block("p001b0005", 1, "Standard Prompting", x0=172.0, y0=455.0, x1=257.0, y1=466.0),
            block("p001b0006", 1, "Chain-of-Thought Prompting", x0=346.0, y0=455.0, x1=467.0, y1=466.0),
            block("p001b0007", 1, "Model Input", x0=137.0, y0=469.0, x1=172.0, y1=477.0),
            block("p001b0008", 1, "Model Output", x0=137.0, y0=560.0, x1=177.0, y1=568.0),
            block("p001b0009", 1, "A: The answer is 27.", x0=127.0, y0=574.0, x1=193.0, y1=582.0),
            block(
                "p001b0010",
                1,
                "Figure 1: Chain-of-thought prompting enables reasoning.",
                x0=108.0,
                y0=651.0,
                x1=506.0,
                y1=671.0,
            ),
        ]

        visual_ids = {
            source_id
            for region in regions.build_visual_regions(blocks)
            for source_id in region["source_ids"]
        }

        self.assertFalse({"p001b0001", "p001b0002", "p001b0003", "p001b0004"} & visual_ids)
        self.assertTrue({"p001b0005", "p001b0006", "p001b0007", "p001b0008", "p001b0009"} <= visual_ids)

    def test_citation_prose_below_figure_caption_is_translatable_body(self):
        blocks = [
            block("p001b0008", 1, "Agent: AppWorld", x0=156.4, y0=524.5, x1=223.8, y1=538.7),
            block(
                "p001b0009",
                1,
                "Domain Knowledge: FiNER Numerical Reasoning: Formula",
                x0=257.7,
                y0=524.5,
                x1=490.5,
                y1=538.7,
            ),
            block("p001b0011", 1, "Accuracy (%)", x0=116.6, y0=558.6, x1=128.9, y1=601.7),
            block(
                "p001b0014",
                1,
                "Figure 1: Overall Performance Results. Our proposed framework, ACE, consistently "
                "outperforms strong baselines across agent and domain-specific tasks.",
                x0=108.0,
                y0=652.7,
                x1=504.0,
                y1=672.7,
            ),
            block(
                "p001b0015",
                1,
                "Modern AI applications based on large language models (LLMs), such as LLM agents "
                "(Yao et al.,\n"
                "2023; Yang et al., 2024) and compound AI systems (Zaharia et al., 2024), "
                "increasingly depend on\n"
                "context adaptation. Instead of modifying model weights, context adaptation improves "
                "performance\n"
                "after model training by incorporating clarified instructions, structured reasoning "
                "steps, or domain-",
                x0=108.0,
                y0=690.2,
                x1=504.0,
                y1=732.0,
            ),
        ]

        visual_regions = regions.build_visual_regions(blocks)
        visual_ids = {source_id for region in visual_regions for source_id in region["source_ids"]}
        classes = pdf.classify_blocks(blocks, visual_regions)
        ownership_result = pdf.build_translation_page_components(1, blocks, page_size=(612.0, 792.0))

        self.assertIn("p001b0014", visual_ids)
        self.assertNotIn("p001b0015", visual_ids)
        self.assertEqual(classes["p001b0015"], "body")
        self.assertIn("p001b0015", ownership_result.translatable_ids)

    def test_first_page_title_authors_and_affiliation_are_not_visual_labels(self):
        blocks = [
            block("p001b0001", 1, "Front. Comput. Sci., 2025, 0(0): 1-42", x0=48.2, y0=32.0, x1=215.1, y1=41.8),
            block("p001b0002", 1, "https://doi.org/10.1007/s11704-024-40231-1", x0=48.2, y0=48.4, x1=242.4, y1=58.2),
            block("p001b0003", 1, "REVIEW ARTICLE", x0=58.9, y0=72.3, x1=165.2, y1=82.9),
            block(
                "p001b0004",
                1,
                "A Survey on Large Language Model based Autonomous\nAgents",
                x0=51.0,
                y0=124.3,
                x1=543.6,
                y1=172.5,
            ),
            block(
                "p001b0005",
                1,
                "Lei Wang, Chen Ma * , Xueyang Feng * , Zeyu Zhang, Hao Yang, Jingsen Zhang,\n"
                "Zhi-Yuan Chen, Jiakai Tang, Xu Chen( B ), Yankai Lin( B ), Wayne Xin Zhao,\n"
                "Zhewei Wei, Ji-Rong Wen",
                x0=59.0,
                y0=211.2,
                x1=538.2,
                y1=266.9,
            ),
            block(
                "p001b0006",
                1,
                "Gaoling School of Artificial Intelligence, Renmin University of China, Beijing, 100872, China",
                x0=71.6,
                y0=284.6,
                x1=523.8,
                y1=295.3,
            ),
        ]

        visual_ids = {
            source_id
            for region in regions.build_visual_regions(blocks)
            for source_id in region["source_ids"]
        }

        self.assertFalse({"p001b0004", "p001b0005", "p001b0006"} & visual_ids)

    def test_first_page_doi_metadata_does_not_seed_visual_region_over_title(self):
        blocks = [
            block("p001b0001", 1, "Article", x0=39.7, y0=24.8, x1=85.7, y1=42.5),
            block(
                "p001b0002",
                1,
                "Detecting hallucinations in large language\nmodels using semantic entropy",
                x0=39.7,
                y0=44.3,
                x1=533.7,
                y1=104.7,
            ),
            block("p001b0003", 1, "https://doi.org/10.1038/s41586-024-07421-0", x0=39.7, y0=143.3, x1=202.4, y1=154.3),
            block("p001b0004", 1, "Sebastian Farquhar, Jannik Kossen, Lorenz Kuhn & Yarin Gal", x0=217.3, y0=140.6, x1=464.7, y1=154.3),
            block(
                "p001b0005",
                1,
                "Large language model systems can show impressive reasoning and question-answering capabilities.",
                x0=217.3,
                y0=175.5,
                x1=561.3,
                y1=220.9,
            ),
        ]

        visual_ids = {
            source_id
            for region in regions.build_visual_regions(blocks)
            for source_id in region["source_ids"]
        }

        self.assertFalse({"p001b0001", "p001b0002", "p001b0003"} & visual_ids)

    def test_table_visual_region_includes_decimal_numeric_column_above_caption(self):
        blocks = [
            block(
                "p009b0021",
                9,
                "3 768 12\n6 768 3\n6 768 12\n12 768 12\n12 1024 16\n24 1024 16",
                x0=86.5,
                y0=638.5,
                x1=137.6,
                y1=696.4,
            ),
            block(
                "p009b0027",
                9,
                "5.84\n5.24\n4.68\n3.99\n3.54\n3.23",
                x0=152.7,
                y0=638.5,
                x1=168.5,
                y1=696.4,
            ),
            block(
                "p009b0031",
                9,
                "Table 6: Ablation over BERT model size. #L = the number of layers.",
                x0=72.0,
                y0=716.7,
                x1=290.3,
                y1=761.6,
            ),
        ]

        visual_regions = regions.build_visual_regions(blocks)
        table_region = next(region for region in visual_regions if "p009b0031" in region["source_ids"])

        self.assertIn("p009b0027", table_region["source_ids"])

    def test_table_visual_region_includes_narrow_dash_placeholder_column(self):
        blocks = [
            block("p778b0001", 778, "Table 7: Evaluation results.", x0=307.3, y0=237.0, x1=525.5, y1=281.7),
            block(
                "p778b0002",
                778,
                "Feature-based approach\nEmbeddings\nLast Hidden",
                x0=317.0,
                y0=153.8,
                x1=449.8,
                y1=221.6,
            ),
            block("p778b0003", 778, "91.0\n95.6\n94.9", x0=461.7, y0=163.8, x1=477.4, y1=221.6),
            block("p778b0004", 778, "-\n-\n-", x0=501.1, y0=163.8, x1=504.0, y1=221.6),
        ]

        table_region = next(
            region
            for region in regions.build_visual_regions(blocks)
            if "p778b0001" in region["source_ids"]
        )

        self.assertIn("p778b0004", table_region["source_ids"])

    def test_grouped_image_row_clips_groups_rows_without_renderer_dependencies(self):
        image_entries = [
            {"bbox": (40.0, 100.0, 70.0, 125.0), "index": 0, "xref": 1},
            {"bbox": (130.0, 101.0, 160.0, 126.0), "index": 1, "xref": 2},
            {"bbox": (230.0, 99.0, 260.0, 124.0), "index": 2, "xref": 3},
        ]

        self.assertEqual(
            regions.grouped_image_row_clips(image_entries, (300.0, 400.0)),
            [{"bbox": (38.0, 77.0, 262.0, 132.0), "indices": {0, 1, 2}}],
        )
        self.assertIs(pdf.grouped_image_row_clips, regions.grouped_image_row_clips)

    def test_formula_region_bbox_from_lines_uses_direct_module_api(self):
        region = {"bbox": (100.0, 100.0, 180.0, 118.0)}
        bbox_lines = [
            {"text": "x := y + z + w", "bbox": (104.0, 101.0, 174.0, 113.0)},
            {"text": "The next body row is not formula content.", "bbox": (90.0, 142.0, 400.0, 158.0)},
        ]

        self.assertEqual(
            regions.formula_region_bbox_from_lines(region, bbox_lines, (500.0, 700.0)),
            (104.0, 101.0, 174.0, 113.0),
        )
        self.assertIs(pdf.formula_region_bbox_from_lines, regions.formula_region_bbox_from_lines)

    def test_nontranslated_visual_region_coverage_excludes_large_prose(self):
        small_label = block("p501b0001", 501, "Δ", x0=120, y0=120, x1=130, y1=132)
        large_prose = block(
            "p501b0002",
            501,
            "This paper presents a wait-free implementation for concurrent objects. " * 6,
            x0=90,
            y0=150,
            x1=500,
            y1=230,
        )
        blocks = [small_label, large_prose]
        classes = {"p501b0001": "unknown", "p501b0002": "body"}

        self.assertEqual(
            regions.nontranslated_blocks_covered_by_visual_region(
                blocks,
                classes,
                (100.0, 100.0, 510.0, 240.0),
                visual_ids=set(),
            ),
            {"p501b0001"},
        )
        self.assertIs(pdf.nontranslated_blocks_covered_by_visual_region, regions.nontranslated_blocks_covered_by_visual_region)

    def test_diagram_caption_region_preserves_distant_internal_labels(self):
        blocks = [
            block("p901b0001", 901, "Input-Input Layer5", x0=110, y0=110, x1=280, y1=137),
            block("p901b0002", 901, "The\nLaw", x0=122, y0=213, x1=148, y1=230),
            block("p901b0003", 901, "will\nnever", x0=150, y0=206, x1=176, y1=230),
            block("p901b0004", 901, "application\nshould", x0=247, y0=185, x1=274, y1=230),
            block("p901b0005", 901, "this\nis\nwhat", x0=317, y0=215, x1=358, y1=230),
            block("p901b0006", 901, "we\nare", x0=360, y0=216, x1=386, y1=230),
            block("p901b0007", 901, "missing\n,", x0=388, y0=198, x1=414, y1=230),
            block("p901b0008", 901, "my\nopinion\n.", x0=429, y0=200, x1=470, y1=230),
            block("p901b0009", 901, "<EOS>\n<pad>", x0=471, y0=199, x1=498, y1=230),
            block("p901b0010", 901, "The\nLaw", x0=122, y0=327, x1=148, y1=344),
            block("p901b0011", 901, "perfect\n,\nbut", x0=191, y0=327, x1=232, y1=356),
            block("p901b0012", 901, "application\nshould", x0=247, y0=327, x1=274, y1=372),
            block("p901b0013", 901, "this\nis\nwhat", x0=317, y0=327, x1=358, y1=347),
            block("p901b0014", 901, "we\nare", x0=359, y0=327, x1=386, y1=341),
            block("p901b0015", 901, "missing", x0=387, y0=327, x1=400, y1=359),
            block("p901b0016", 901, "my\nopinion\n.", x0=429, y0=327, x1=470, y1=358),
            block("p901b0017", 901, "<EOS>", x0=471, y0=327, x1=484, y1=358),
            block("p901b0018", 901, "<pad>", x0=485, y0=327, x1=498, y1=354),
            block("p901b0019", 901, "The\nLaw", x0=121, y0=431, x1=147, y1=448),
            block("p901b0020", 901, "will\nnever", x0=149, y0=425, x1=176, y1=448),
            block("p901b0021", 901, "application\nshould", x0=247, y0=403, x1=274, y1=448),
            block("p901b0022", 901, "this", x0=317, y0=434, x1=330, y1=448),
            block("p901b0023", 901, "we\nare", x0=359, y0=435, x1=386, y1=448),
            block("p901b0024", 901, "missing\n,", x0=387, y0=416, x1=414, y1=448),
            block("p901b0025", 901, "my\nopinion\n.", x0=429, y0=418, x1=470, y1=448),
            block("p901b0026", 901, "<EOS>", x0=471, y0=417, x1=484, y1=448),
            block("p901b0027", 901, "<pad>", x0=485, y0=422, x1=498, y1=448),
            block("p901b0029", 901, ",\nin", x0=401, y0=327, x1=428, y1=335),
            block(
                "p901b0028",
                901,
                "Figure 5: Many attention heads show structure in a sentence.",
                x0=108,
                y0=603,
                x1=504,
                y1=634,
            ),
        ]

        visual_ids = {
            source_id
            for region in regions.build_visual_regions(blocks)
            for source_id in region["source_ids"]
        }

        self.assertTrue({block["id"] for block in blocks} - {"p901b0028"} <= visual_ids)

    def test_diagram_caption_region_preserves_small_label_group(self):
        blocks = [
            block("p902b0001", 902, "Encoder", x0=130, y0=210, x1=178, y1=226),
            block("p902b0002", 902, "Attention", x0=238, y0=184, x1=298, y1=200),
            block("p902b0003", 902, "Decoder", x0=360, y0=210, x1=409, y1=226),
            block(
                "p902b0004",
                902,
                "Figure 2: A compact model diagram with three internal labels.",
                x0=108,
                y0=420,
                x1=504,
                y1=444,
            ),
        ]

        visual_ids = {
            source_id
            for region in regions.build_visual_regions(blocks)
            for source_id in region["source_ids"]
        }

        self.assertTrue({"p902b0001", "p902b0002", "p902b0003"} <= visual_ids)

    def test_caption_region_expands_to_embedding_diagram_labels_above(self):
        blocks = [
            block("p005b0001", 5, "Input", x0=117.3, y0=68.5, x1=132.3, y1=74.8),
            block("p005b0002", 5, "[CLS]", x0=178.0, y0=71.1, x1=190.1, y1=75.6),
            block("p005b0003", 5, "my", x0=205.2, y0=70.2, x1=215.7, y1=76.8),
            block("p005b0004", 5, "dog", x0=230.2, y0=70.2, x1=242.7, y1=76.8),
            block("p005b0005", 5, "Token\nEmbeddings", x0=117.3, y0=90.9, x1=154.8, y1=105.2),
            block("p005b0006", 5, "E [CLS]", x0=176.5, y0=95.1, x1=192.2, y1=103.5),
            block("p005b0007", 5, "E my", x0=204.8, y0=94.9, x1=216.1, y1=103.5),
            block("p005b0008", 5, "Segment\nEmbeddings", x0=117.3, y0=119.8, x1=154.8, y1=134.0),
            block("p005b0009", 5, "E A", x0=179.2, y0=123.2, x1=188.9, y1=134.0),
            block("p005b0010", 5, "Position\nEmbeddings", x0=117.3, y0=151.5, x1=154.8, y1=165.8),
            block("p005b0011", 5, "E 0", x0=179.6, y0=154.9, x1=188.6, y1=165.7),
            block(
                "p005b0012",
                5,
                "Figure 2: BERT input representation. The input embeddings are the sum of token, segment, and position embeddings.",
                x0=72.0,
                y0=185.3,
                x1=525.5,
                y1=206.1,
            ),
            block(
                "p005b0013",
                5,
                "The NSP task is closely related to representation-learning objectives.",
                x0=72.0,
                y0=231.0,
                x1=290.3,
                y1=308.5,
            ),
        ]

        visual_ids = {
            source_id
            for region in regions.build_visual_regions(blocks)
            for source_id in region["source_ids"]
        }

        self.assertTrue({f"p005b{idx:04d}" for idx in range(1, 13)} <= visual_ids)
        self.assertNotIn("p005b0013", visual_ids)

    def test_table_caption_region_expands_to_header_cells_above(self):
        blocks = [
            block(
                "p008b0001",
                8,
                "Dev Set\nMNLI-m QNLI MRPC SST-2 SQuAD\n(Acc) (Acc) (Acc) (Acc)\n(F1)",
                x0=135.8,
                y0=67.6,
                x1=292.1,
                y1=95.6,
            ),
            block("p008b0002", 8, "Tasks", x0=72.0, y0=77.6, x1=92.2, y1=85.6),
            block("p008b0003", 8, "BERT BASE\nNo NSP\nLTR & No NSP\n+ BiLSTM", x0=72.0, y0=103.0, x1=129.2, y1=140.9),
            block("p008b0004", 8, "84.4\n83.9\n82.1\n82.1", x0=144.3, y0=103.0, x1=160.0, y1=140.9),
            block("p008b0005", 8, "88.4\n84.9\n84.3\n84.1", x0=178.0, y0=103.0, x1=193.7, y1=140.9),
            block(
                "p008b0006",
                8,
                "Table 5: Ablation over the pre-training tasks using the BERT BASE architecture.",
                x0=72.0,
                y0=156.3,
                x1=290.3,
                y1=236.9,
            ),
            block("p008b0007", 8, "5.1", x0=72.0, y0=290.9, x1=85.6, y1=300.7),
            block("p008b0008", 8, "Effect of Pre-training Tasks", x0=96.5, y0=290.9, x1=225.3, y1=300.7),
        ]

        visual_ids = {
            source_id
            for region in regions.build_visual_regions(blocks)
            for source_id in region["source_ids"]
        }

        self.assertTrue({f"p008b{idx:04d}" for idx in range(1, 7)} <= visual_ids)
        self.assertFalse({"p008b0007", "p008b0008"} & visual_ids)

    def test_table_caption_region_does_not_capture_adjacent_column_heading(self):
        fixture_path = Path(__file__).resolve().parent / "fixtures" / "pdf_render" / "source_pages" / "bert" / "page-009.json"
        blocks = json.loads(fixture_path.read_text(encoding="utf-8"))["blocks"]

        visual_ids = {
            source_id
            for region in regions.build_visual_regions(blocks)
            for source_id in region["source_ids"]
        }

        self.assertTrue({"p009b0006", "p009b0007", "p009b0008", "p009b0022", "p009b0023", "p009b0026"} <= visual_ids)
        self.assertFalse({"p009b0002", "p009b0003"} & visual_ids)

    def test_bert_page9_ownership_visual_regions_do_not_merge_tables_or_body_text(self):
        fixture_path = Path(__file__).resolve().parent / "fixtures" / "pdf_render" / "source_pages" / "bert" / "page-009.json"
        blocks = json.loads(fixture_path.read_text(encoding="utf-8"))["blocks"]
        visual_regions = regions.build_visual_regions(blocks)
        classes = pdf.classify_blocks(blocks, visual_regions)

        ownership_regions = pdf.final_visual_ownership_regions(
            blocks,
            classes,
            visual_regions,
            page_size=(623, 801),
            page_num=9,
            bbox_lines=None,
            source_image_path=None,
            translations={},
        )
        visual_ids = {source_id for region in ownership_regions for source_id in region["source_ids"]}

        self.assertNotIn("p009b0003", visual_ids)
        self.assertNotIn("p009b0032", visual_ids)
        self.assertFalse(
            any({"p009b0023", "p009b0031"} <= set(region["source_ids"]) for region in ownership_regions),
            "Table 7 and Table 6 should not collapse into one ownership visual region.",
        )

    def test_figure_caption_region_expands_to_axis_labels_above(self):
        blocks = [
            block(
                "p016b0001",
                16,
                "In Section 3.1, we mention that BERT uses a mixed strategy for masking target tokens.",
                x0=72.0,
                y0=473.7,
                x1=290.3,
                y1=551.2,
            ),
            block("p016b0002", 16, "MNLI Dev Accuracy", x0=75.6, y0=596.9, x1=81.8, y1=656.3),
            block("p016b0003", 16, "84", x0=86.8, y0=580.8, x1=94.8, y1=587.0),
            block("p016b0004", 16, "82", x0=86.8, y0=602.4, x1=94.8, y1=608.6),
            block("p016b0005", 16, "80", x0=86.8, y0=624.0, x1=94.8, y1=630.2),
            block("p016b0006", 16, "78", x0=86.8, y0=645.6, x1=94.8, y1=651.8),
            block("p016b0007", 16, "BERT BASE (Masked LM)\nBERT BASE (Left-to-Right)", x0=180.9, y0=659.7, x1=255.9, y1=676.8),
            block("p016b0008", 16, "76", x0=86.8, y0=667.2, x1=94.8, y1=673.4),
            block("p016b0009", 16, "200", x0=125.1, y0=684.1, x1=137.1, y1=690.3),
            block("p016b0010", 16, "400", x0=157.7, y0=684.1, x1=169.6, y1=690.3),
            block("p016b0011", 16, "Pre-training Steps (Thousands)", x0=136.5, y0=695.3, x1=223.2, y1=701.5),
            block(
                "p016b0012",
                16,
                "Figure 5: Ablation over number of training steps. This shows the MNLI accuracy after fine-tuning.",
                x0=72.0,
                y0=716.8,
                x1=290.3,
                y1=761.5,
            ),
        ]

        visual_ids = {
            source_id
            for region in regions.build_visual_regions(blocks)
            for source_id in region["source_ids"]
        }

        self.assertTrue({f"p016b{idx:04d}" for idx in range(2, 13)} <= visual_ids)
        self.assertNotIn("p016b0001", visual_ids)


if __name__ == "__main__":
    unittest.main()
