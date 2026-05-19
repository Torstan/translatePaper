import json
import unittest

import ownership


def block(block_id, text="Body text.", x0=10, y0=20, x1=110, y1=40):
    return {
        "id": block_id,
        "page": int(block_id[1:4]),
        "text": text,
        "xMin": float(x0),
        "yMin": float(y0),
        "xMax": float(x1),
        "yMax": float(y1),
    }


class OwnershipSerializationTests(unittest.TestCase):
    def test_page_component_serializes_deterministically(self):
        component = ownership.PageComponent(
            component_id="p001c0002",
            component_kind=ownership.COMPONENT_KIND_TRANSLATED_TEXT,
            source_ids=["p001b0002", "p001b0001"],
            source_bbox=(10.12345, 20, 30.5, 40.0),
            clip_bbox=None,
            confidence=ownership.CONFIDENCE_DETERMINISTIC,
            reason_codes=["layout_flow", "body_text"],
            render_strategy="translated_text",
        )

        self.assertEqual(
            ownership.page_component_to_json(component),
            {
                "clip_bbox": None,
                "component_id": "p001c0002",
                "component_kind": "translated_text",
                "confidence": "deterministic",
                "parent_component_id": "",
                "reason_codes": ["body_text", "layout_flow"],
                "render_strategy": "translated_text",
                "source_bbox": [10.123, 20.0, 30.5, 40.0],
                "source_ids": ["p001b0001", "p001b0002"],
            },
        )
        dumped = ownership.stable_json_dumps({"component": ownership.page_component_to_json(component)})
        self.assertEqual(
            dumped,
            json.dumps(json.loads(dumped), ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )

    def test_validation_result_serializes_deterministically(self):
        issue = ownership.OwnershipIssue(
            issue_code="duplicate_owner",
            severity="error",
            page_num=8,
            message="source block p008b0001 has multiple non-duplicate owners",
            source_ids=["p008b0001"],
            component_ids=["p008c0002", "p008c0001"],
            bboxes=[(10, 20, 30, 40)],
        )

        self.assertEqual(
            ownership.ownership_issue_to_json(issue),
            {
                "bboxes": [[10.0, 20.0, 30.0, 40.0]],
                "component_ids": ["p008c0001", "p008c0002"],
                "issue_code": "duplicate_owner",
                "message": "source block p008b0001 has multiple non-duplicate owners",
                "page_num": 8,
                "severity": "error",
                "source_ids": ["p008b0001"],
            },
        )


class OwnershipValidationTests(unittest.TestCase):
    def test_missing_owner_is_reported(self):
        result = ownership.validate_ownership(
            7,
            [block("p007b0001", "Owned text."), block("p007b0002", "Missing owner.")],
            [
                ownership.PageComponent(
                    component_id="p007c0001",
                    component_kind=ownership.COMPONENT_KIND_TRANSLATED_TEXT,
                    source_ids=["p007b0001"],
                    source_bbox=(10, 20, 110, 40),
                    clip_bbox=None,
                    confidence=ownership.CONFIDENCE_DETERMINISTIC,
                    reason_codes=["body_text"],
                    render_strategy="translated_text",
                )
            ],
        )

        self.assertFalse(result.ok)
        self.assertEqual([issue.issue_code for issue in result.issues], ["missing_owner"])
        self.assertEqual(result.issues[0].source_ids, ["p007b0002"])

    def test_duplicate_non_duplicate_owner_is_reported(self):
        result = ownership.validate_ownership(
            8,
            [block("p008b0001", "Duplicated owner.")],
            [
                ownership.PageComponent(
                    "p008c0001",
                    ownership.COMPONENT_KIND_TRANSLATED_TEXT,
                    ["p008b0001"],
                    (10, 20, 110, 40),
                    None,
                    ownership.CONFIDENCE_DETERMINISTIC,
                    ["body_text"],
                    "translated_text",
                ),
                ownership.PageComponent(
                    "p008c0002",
                    ownership.COMPONENT_KIND_REFERENCE,
                    ["p008b0001"],
                    (10, 20, 110, 40),
                    None,
                    ownership.CONFIDENCE_INFERRED,
                    ["reference_like"],
                    "original_selectable_text",
                ),
            ],
        )

        self.assertFalse(result.ok)
        self.assertEqual([issue.issue_code for issue in result.issues], ["duplicate_owner"])
        self.assertEqual(result.issues[0].component_ids, ["p008c0001", "p008c0002"])

    def test_duplicate_and_skip_are_valid_explicit_owners(self):
        result = ownership.validate_ownership(
            10,
            [block("p010b0001", "Duplicate extraction."), block("p010b0002", "Skip artifact.")],
            [
                ownership.PageComponent(
                    "p010c0001",
                    ownership.COMPONENT_KIND_DUPLICATE,
                    ["p010b0001"],
                    (10, 20, 110, 40),
                    None,
                    ownership.CONFIDENCE_DETERMINISTIC,
                    ["duplicated_extraction"],
                    "skip_explicitly",
                ),
                ownership.PageComponent(
                    "p010c0002",
                    ownership.COMPONENT_KIND_SKIP,
                    ["p010b0002"],
                    (10, 20, 110, 40),
                    None,
                    ownership.CONFIDENCE_DETERMINISTIC,
                    ["trivial_or_artifact"],
                    "skip_explicitly",
                ),
            ],
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.issues, [])

    def test_explicit_mixed_visual_body_split_allows_same_source_id_with_disjoint_bboxes(self):
        result = ownership.validate_ownership(
            17,
            [block("p017b0002", "code line\nPROOF. Body prose.", 135, 71, 495, 239)],
            [
                ownership.PageComponent(
                    "p017c0001",
                    ownership.COMPONENT_KIND_VISUAL,
                    ["p017b0002"],
                    (135, 71, 495, 164),
                    (135, 71, 495, 164),
                    ownership.CONFIDENCE_CONSERVATIVE,
                    ["mixed_visual_body_split", "visual_region"],
                    "original_image_clip",
                ),
                ownership.PageComponent(
                    "p017c0002",
                    ownership.COMPONENT_KIND_TRANSLATED_TEXT,
                    ["p017b0002"],
                    (135, 168, 495, 239),
                    None,
                    ownership.CONFIDENCE_INFERRED,
                    ["body", "mixed_visual_body_split"],
                    "translated_text",
                    parent_component_id="p017c0001",
                ),
            ],
        )

        self.assertTrue(result.ok, [issue.issue_code for issue in result.issues])

    def test_explicit_mixed_visual_body_split_requires_disjoint_bboxes(self):
        result = ownership.validate_ownership(
            17,
            [block("p017b0002", "code line\nPROOF. Body prose.", 135, 71, 495, 239)],
            [
                ownership.PageComponent(
                    "p017c0001",
                    ownership.COMPONENT_KIND_VISUAL,
                    ["p017b0002"],
                    (135, 71, 495, 190),
                    (135, 71, 495, 190),
                    ownership.CONFIDENCE_CONSERVATIVE,
                    ["mixed_visual_body_split", "visual_region"],
                    "original_image_clip",
                ),
                ownership.PageComponent(
                    "p017c0002",
                    ownership.COMPONENT_KIND_TRANSLATED_TEXT,
                    ["p017b0002"],
                    (135, 168, 495, 239),
                    None,
                    ownership.CONFIDENCE_INFERRED,
                    ["body", "mixed_visual_body_split"],
                    "translated_text",
                    parent_component_id="p017c0001",
                ),
            ],
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.issues[0].issue_code, "duplicate_owner")

    def test_explicit_mixed_visual_body_split_requires_exact_visual_text_pair(self):
        result = ownership.validate_ownership(
            17,
            [block("p017b0002", "code line\nPROOF. Body prose.", 135, 71, 495, 239)],
            [
                ownership.PageComponent(
                    "p017c0001",
                    ownership.COMPONENT_KIND_VISUAL,
                    ["p017b0002"],
                    (135, 71, 495, 120),
                    (135, 71, 495, 120),
                    ownership.CONFIDENCE_CONSERVATIVE,
                    ["mixed_visual_body_split", "visual_region"],
                    "original_image_clip",
                ),
                ownership.PageComponent(
                    "p017c0002",
                    ownership.COMPONENT_KIND_VISUAL,
                    ["p017b0002"],
                    (135, 122, 495, 164),
                    (135, 122, 495, 164),
                    ownership.CONFIDENCE_CONSERVATIVE,
                    ["mixed_visual_body_split", "visual_region"],
                    "original_image_clip",
                ),
                ownership.PageComponent(
                    "p017c0003",
                    ownership.COMPONENT_KIND_TRANSLATED_TEXT,
                    ["p017b0002"],
                    (135, 168, 495, 239),
                    None,
                    ownership.CONFIDENCE_INFERRED,
                    ["body", "mixed_visual_body_split"],
                    "translated_text",
                    parent_component_id="p017c0001",
                ),
            ],
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.issues[0].issue_code, "duplicate_owner")

    def test_components_by_source_id_preserves_split_component_pair(self):
        components = [
            ownership.PageComponent(
                "p017c0001",
                ownership.COMPONENT_KIND_VISUAL,
                ["p017b0002"],
                (135, 71, 495, 164),
                (135, 71, 495, 164),
                ownership.CONFIDENCE_CONSERVATIVE,
                ["mixed_visual_body_split", "visual_region"],
                "original_image_clip",
            ),
            ownership.PageComponent(
                "p017c0002",
                ownership.COMPONENT_KIND_TRANSLATED_TEXT,
                ["p017b0002"],
                (135, 168, 495, 239),
                None,
                ownership.CONFIDENCE_INFERRED,
                ["body", "mixed_visual_body_split"],
                "translated_text",
                parent_component_id="p017c0001",
            ),
        ]

        self.assertEqual(
            [component.component_id for component in ownership.components_by_source_id(components)["p017b0002"]],
            ["p017c0001", "p017c0002"],
        )

    def test_visual_clip_must_cover_visual_source_bbox(self):
        result = ownership.validate_ownership(
            21,
            [block("p021b0001", "Figure content.", 100, 100, 200, 200)],
            [
                ownership.PageComponent(
                    "p021c0001",
                    ownership.COMPONENT_KIND_VISUAL,
                    ["p021b0001"],
                    (100, 100, 200, 200),
                    (100, 100, 200, 180),
                    ownership.CONFIDENCE_CONSERVATIVE,
                    ["figure_region"],
                    "original_image_clip",
                )
            ],
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.issues[0].issue_code, "visual_clip_undercaptures_source")
        self.assertEqual(result.issues[0].component_ids, ["p021c0001"])

    def test_visual_clip_must_cover_owned_block_boxes(self):
        result = ownership.validate_ownership(
            22,
            [block("p022b0001", "Figure content.", 100, 100, 240, 200)],
            [
                ownership.PageComponent(
                    "p022c0001",
                    ownership.COMPONENT_KIND_VISUAL,
                    ["p022b0001"],
                    (100, 100, 200, 200),
                    (100, 100, 200, 200),
                    ownership.CONFIDENCE_CONSERVATIVE,
                    ["figure_region"],
                    "original_image_clip",
                )
            ],
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.issues[0].issue_code, "visual_clip_undercaptures_owned_block")
        self.assertEqual(result.issues[0].source_ids, ["p022b0001"])


class OwnershipBuilderTests(unittest.TestCase):
    def test_builder_assigns_core_component_kinds_once(self):
        blocks = [
            block("p011b0001", "1. Introduction", 72, 80, 190, 96),
            block("p011b0002", "Body paragraph for translation.", 72, 110, 300, 145),
            block("p011b0003", "Smith, J. 2019. Reference title. In ACL.", 72, 620, 300, 645),
            block("p011b0004", "11", 300, 760, 320, 772),
            block("p011b0005", "Conference footer", 72, 780, 250, 790),
        ]
        classes = {
            "p011b0001": "heading",
            "p011b0002": "body",
            "p011b0003": "reference",
            "p011b0004": "page_number",
            "p011b0005": "header_footer",
        }

        components = ownership.build_page_components(
            page_num=11,
            blocks=blocks,
            classes=classes,
            visual_regions=[],
            visual_covered_text_ids=set(),
            duplicate_ids=set(),
        )
        owner_by_id = ownership.component_by_source_id(components)

        self.assertEqual(owner_by_id["p011b0001"].component_kind, ownership.COMPONENT_KIND_TRANSLATED_TEXT)
        self.assertEqual(owner_by_id["p011b0002"].component_kind, ownership.COMPONENT_KIND_TRANSLATED_TEXT)
        self.assertEqual(owner_by_id["p011b0003"].component_kind, ownership.COMPONENT_KIND_REFERENCE)
        self.assertEqual(owner_by_id["p011b0004"].component_kind, ownership.COMPONENT_KIND_PAGE_NUMBER)
        self.assertEqual(owner_by_id["p011b0005"].component_kind, ownership.COMPONENT_KIND_HEADER_FOOTER)
        self.assertTrue(ownership.validate_ownership(11, blocks, components).ok)

    def test_visual_component_owns_region_and_covered_internal_labels(self):
        blocks = [
            block("p015b0009", "T 1 '", 221, 98, 229, 106),
            block("p015b0010", "T M '", 257, 98, 265, 106),
            block("p015b0097", "Body after figure.", 72, 488, 290, 552),
        ]
        components = ownership.build_page_components(
            page_num=15,
            blocks=blocks,
            classes={"p015b0009": "body", "p015b0010": "body", "p015b0097": "body"},
            visual_regions=[
                {"source_ids": ["p015b0009"], "bbox": (111.0, 65.0, 484.0, 402.0), "kind": "figure_region"}
            ],
            visual_covered_text_ids={"p015b0010"},
            duplicate_ids=set(),
        )

        visual_components = [component for component in components if component.component_kind == ownership.COMPONENT_KIND_VISUAL]
        self.assertEqual(len(visual_components), 1)
        self.assertEqual(set(visual_components[0].source_ids), {"p015b0009", "p015b0010"})
        self.assertEqual(
            ownership.component_by_source_id(components)["p015b0097"].component_kind,
            ownership.COMPONENT_KIND_TRANSLATED_TEXT,
        )

    def test_reference_components_do_not_capture_adjacent_body_column(self):
        blocks = [
            block("p012b0002", "for natural language understanding. In Proceedings", 82, 67, 292, 109),
            block("p012b0001", "Additional details are presented in Appendix B.", 320, 66, 526, 90),
        ]
        components = ownership.build_page_components(
            page_num=12,
            blocks=blocks,
            classes={"p012b0002": "reference", "p012b0001": "body"},
            visual_regions=[],
            visual_covered_text_ids=set(),
            duplicate_ids=set(),
        )
        owner_by_id = ownership.component_by_source_id(components)

        self.assertEqual(owner_by_id["p012b0002"].component_kind, ownership.COMPONENT_KIND_REFERENCE)
        self.assertEqual(owner_by_id["p012b0001"].component_kind, ownership.COMPONENT_KIND_TRANSLATED_TEXT)
        self.assertLess(owner_by_id["p012b0002"].source_bbox[2], owner_by_id["p012b0001"].source_bbox[0])

    def test_unknown_nontrivial_content_is_preserved_not_dropped(self):
        blocks = [block("p004b0008", "x := y + z", 120, 220, 240, 245)]
        components = ownership.build_page_components(
            page_num=4,
            blocks=blocks,
            classes={"p004b0008": "unknown"},
            visual_regions=[],
            visual_covered_text_ids=set(),
            duplicate_ids=set(),
        )

        owner = ownership.component_by_source_id(components)["p004b0008"]
        self.assertEqual(owner.component_kind, ownership.COMPONENT_KIND_UNKNOWN)
        self.assertEqual(owner.render_strategy, "original_image_clip")


class OwnershipTranslationFilterTests(unittest.TestCase):
    def test_only_translated_text_components_enter_translation_batches(self):
        blocks = [
            block("p020b0001", "Heading", 72, 80, 160, 96),
            block("p020b0002", "Body paragraph.", 72, 110, 300, 145),
            block("p020b0003", "Reference. 2020. Title.", 72, 620, 300, 645),
            block("p020b0004", "Figure label", 160, 200, 240, 220),
            block("p020b0005", "x := y + z", 160, 240, 260, 260),
        ]
        components = ownership.build_page_components(
            page_num=20,
            blocks=blocks,
            classes={
                "p020b0001": "heading",
                "p020b0002": "body",
                "p020b0003": "reference",
                "p020b0004": "figure_region",
                "p020b0005": "unknown",
            },
            visual_regions=[{"source_ids": ["p020b0004"], "bbox": (150, 190, 250, 230)}],
            visual_covered_text_ids=set(),
            duplicate_ids=set(),
        )

        items = ownership.translation_items_from_components(
            blocks,
            components,
            classes={"p020b0001": "heading", "p020b0002": "body"},
        )

        self.assertEqual(
            items,
            [{"id": "p020b0001", "text": "Heading"}, {"id": "p020b0002", "text": "Body paragraph."}],
        )


class RenderLayerOwnershipValidationTests(unittest.TestCase):
    def test_source_id_cannot_render_as_image_and_text(self):
        plan = type("Plan", (), {})()
        plan.page_num = 9
        plan.items = [
            type("Item", (), {"kind": "original_image_clip", "source_ids": ["p009b0005"], "bbox": (80, 600, 306, 656), "component_id": "p009c0001", "component_kind": "visual"})(),
            type("Item", (), {"kind": "translated_text", "source_ids": ["p009b0005"], "bbox": (85, 610, 120, 630), "component_id": "p009c0002", "component_kind": "translated_text"})(),
        ]
        components = [
            ownership.PageComponent("p009c0001", "visual", ["p009b0005"], (80, 600, 306, 656), (80, 600, 306, 656), "conservative", ["table_region"], "original_image_clip"),
            ownership.PageComponent("p009c0002", "translated_text", ["p009b0005"], (85, 610, 120, 630), None, "inferred", ["body"], "translated_text"),
        ]

        result = ownership.validate_render_layer_exclusivity(plan, components)

        self.assertFalse(result.ok)
        self.assertEqual(result.issues[0].issue_code, "source_rendered_as_image_and_text")

    def test_explicit_mixed_visual_body_split_can_share_one_source_id(self):
        plan = type("Plan", (), {})()
        plan.page_num = 17
        plan.items = [
            type("Item", (), {"kind": "original_image_clip", "source_ids": ["p017b0002"], "bbox": (135, 72, 499, 164), "component_id": "p017c0001", "component_kind": "visual", "fallback_reason": "visual_region"})(),
            type("Item", (), {"kind": "translated_text", "source_ids": ["p017b0002"], "bbox": (135, 168, 499, 238), "component_id": "p017c0002", "component_kind": "translated_text", "fallback_reason": "mixed_visual_body"})(),
        ]
        components = [
            ownership.PageComponent("p017c0001", "visual", ["p017b0002"], (135, 72, 499, 164), (135, 72, 499, 164), "conservative", ["code_region", ownership.REASON_MIXED_VISUAL_BODY_SPLIT], "original_image_clip"),
            ownership.PageComponent("p017c0002", "translated_text", ["p017b0002"], (135, 168, 499, 238), None, "inferred", ["body", ownership.REASON_MIXED_VISUAL_BODY_SPLIT], "translated_text", parent_component_id="p017c0001"),
        ]

        result = ownership.validate_render_layer_exclusivity(plan, components)

        self.assertTrue(result.ok, [issue.issue_code for issue in result.issues])

    def test_text_cannot_overlap_unrelated_visual_component(self):
        plan = type("Plan", (), {})()
        plan.page_num = 15
        plan.items = [
            type("Item", (), {"kind": "original_image_clip", "source_ids": ["p015b0009"], "bbox": (111, 65, 484, 402), "component_id": "p015c0001", "component_kind": "visual"})(),
            type("Item", (), {"kind": "translated_text", "source_ids": ["p015b0097"], "bbox": (120, 100, 250, 140), "component_id": "p015c0002", "component_kind": "translated_text"})(),
        ]
        components = [
            ownership.PageComponent("p015c0001", "visual", ["p015b0009"], (111, 65, 484, 402), (111, 65, 484, 402), "conservative", ["figure_region"], "original_image_clip"),
            ownership.PageComponent("p015c0002", "translated_text", ["p015b0097"], (120, 100, 250, 140), None, "inferred", ["body"], "translated_text"),
        ]

        result = ownership.validate_render_layer_exclusivity(plan, components)

        self.assertFalse(result.ok)
        self.assertEqual(result.issues[0].issue_code, "text_over_visual_component")

    def test_text_overlap_uses_visual_clip_bbox_when_available(self):
        plan = type("Plan", (), {})()
        plan.page_num = 9
        plan.items = [
            type("Item", (), {"kind": "original_image_clip", "source_ids": ["p009b0009"], "bbox": (291, 64, 533, 284), "component_id": "p009c0001", "component_kind": "visual"})(),
            type("Item", (), {"kind": "translated_text", "source_ids": ["p009b0010"], "bbox": (90, 100, 240, 140), "component_id": "p009c0002", "component_kind": "translated_text"})(),
        ]
        components = [
            ownership.PageComponent("p009c0001", "visual", ["p009b0009"], (72, 67, 525, 281), (291, 64, 533, 284), "conservative", ["table_region"], "original_image_clip"),
            ownership.PageComponent("p009c0002", "translated_text", ["p009b0010"], (90, 100, 240, 140), None, "inferred", ["body"], "translated_text"),
        ]

        result = ownership.validate_render_layer_exclusivity(plan, components)

        self.assertTrue(result.ok, [issue.issue_code for issue in result.issues])

    def test_visual_clip_cannot_capture_translated_component(self):
        plan = type("Plan", (), {})()
        plan.page_num = 12
        plan.items = [
            type("Item", (), {"kind": "original_image_clip", "source_ids": ["p012b0002"], "bbox": (70, 60, 526, 130), "component_id": "p012c0001", "component_kind": "visual"})(),
            type("Item", (), {"kind": "translated_text", "source_ids": ["p012b0001"], "bbox": (320, 66, 526, 90), "component_id": "p012c0002", "component_kind": "translated_text"})(),
        ]
        components = [
            ownership.PageComponent("p012c0001", "visual", ["p012b0002"], (82, 67, 292, 109), (70, 60, 526, 130), "conservative", ["reference_like"], "original_image_clip"),
            ownership.PageComponent("p012c0002", "translated_text", ["p012b0001"], (320, 66, 526, 90), None, "inferred", ["body"], "translated_text"),
        ]

        result = ownership.validate_render_layer_exclusivity(plan, components)

        self.assertFalse(result.ok)
        self.assertEqual(result.issues[0].issue_code, "visual_clip_overcaptures_translated_component")

    def test_visual_clip_overcapture_reports_unsafe_overlapping_source_bbox(self):
        plan = type("Plan", (), {})()
        plan.page_num = 23
        plan.items = [
            type("Item", (), {"kind": "original_image_clip", "source_ids": ["p023b0001"], "bbox": (90, 90, 300, 200), "component_id": "p023c0001", "component_kind": "visual"})(),
            type("Item", (), {"kind": "translated_text", "source_ids": ["p023b0002"], "bbox": (310, 120, 380, 150), "component_id": "p023c0002", "component_kind": "translated_text"})(),
        ]
        components = [
            ownership.PageComponent("p023c0001", "visual", ["p023b0001"], (100, 100, 220, 180), (90, 90, 300, 200), "conservative", ["figure_region"], "original_image_clip"),
            ownership.PageComponent("p023c0002", "translated_text", ["p023b0002"], (210, 120, 280, 150), None, "inferred", ["heading"], "translated_text"),
        ]

        result = ownership.validate_render_layer_exclusivity(plan, components)

        self.assertFalse(result.ok)
        self.assertEqual(result.issues[0].issue_code, "visual_clip_overcaptures_translated_component")

    def test_render_layer_exclusivity_allows_explicit_mixed_visual_body_split(self):
        plan = type("Plan", (), {})()
        plan.page_num = 17
        plan.items = [
            type("Item", (), {"kind": "original_image_clip", "source_ids": ["p017b0002"], "bbox": (135, 71, 495, 164), "component_id": "p017c0001", "component_kind": "visual"})(),
            type("Item", (), {"kind": "translated_text", "source_ids": ["p017b0002"], "bbox": (135, 168, 495, 239), "component_id": "p017c0002", "component_kind": "translated_text"})(),
        ]
        components = [
            ownership.PageComponent("p017c0001", "visual", ["p017b0002"], (135, 71, 495, 164), (135, 71, 495, 164), "conservative", ["mixed_visual_body_split", "visual_region"], "original_image_clip"),
            ownership.PageComponent("p017c0002", "translated_text", ["p017b0002"], (135, 168, 495, 239), None, "inferred", ["body", "mixed_visual_body_split"], "translated_text", parent_component_id="p017c0001"),
        ]

        result = ownership.validate_render_layer_exclusivity(plan, components)

        self.assertTrue(result.ok, [issue.issue_code for issue in result.issues])

    def test_render_layer_exclusivity_requires_split_items_to_match_components(self):
        plan = type("Plan", (), {})()
        plan.page_num = 17
        plan.items = [
            type("Item", (), {"kind": "original_image_clip", "source_ids": ["p017b0002"], "bbox": (135, 71, 495, 164), "component_id": "", "component_kind": ""})(),
            type("Item", (), {"kind": "translated_text", "source_ids": ["p017b0002"], "bbox": (135, 168, 495, 239), "component_id": "p017c0002", "component_kind": "translated_text"})(),
        ]
        components = [
            ownership.PageComponent("p017c0001", "visual", ["p017b0002"], (135, 71, 495, 164), (135, 71, 495, 164), "conservative", ["mixed_visual_body_split", "visual_region"], "original_image_clip"),
            ownership.PageComponent("p017c0002", "translated_text", ["p017b0002"], (135, 168, 495, 239), None, "inferred", ["body", "mixed_visual_body_split"], "translated_text", parent_component_id="p017c0001"),
        ]

        result = ownership.validate_render_layer_exclusivity(plan, components)

        self.assertFalse(result.ok)
        self.assertEqual(result.issues[0].issue_code, "source_rendered_as_image_and_text")


if __name__ == "__main__":
    unittest.main()
