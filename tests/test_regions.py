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


if __name__ == "__main__":
    unittest.main()
