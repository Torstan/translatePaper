import copy
import unittest
from dataclasses import replace
from pathlib import Path

import layout
import pipeline
import qa_visual
import render_plan
from tests.pdf_render_fixture_runner import iter_render_plan_fixtures


class LayoutPolicyTests(unittest.TestCase):
    def item(self, *, role="normal", policy="document", style="body", size=None, reason=""):
        return render_plan.RenderItem("translated_text", ["body"], (20, 30, 280, 90),
                                      text="完整的中文正文。", style_name=style,
                                      font_size=layout.text_style(style).font_size if size is None else size,
                                      fallback_reason=reason, layout_role=role, font_policy=policy)

    def plan(self, item, classification="body"):
        return render_plan.PageRenderPlan(1, items=[item], page_size=(300, 300),
                                         ledger=[render_plan.CoverageEntry("body", classification, item.kind, True)])

    def test_whitelisted_diagnostic_reasons_cannot_exempt_font_or_role_checks(self):
        for reason in ("embedded_heading", "embedded_heading_body", "first_page_abstract",
                       "first_page_metadata", "first_page_title", "body_flow_compact",
                       "dense_visual_body_row", "mixed_visual_body", "source_adapted_font",
                       "body_flow_source_adapted_font", "callout_heading", "callout_body"):
            with self.subTest(reason=reason):
                issues = layout.text_item_style_issues(self.item(size=1, reason=reason), {"heading"})
                self.assertTrue(any("expected heading/subheading" in message for _, message in issues))
                self.assertTrue(any("font size" in message for _, message in issues))

    def test_reason_does_not_select_style_flow_or_spacing(self):
        for reason in ("", "callout_body", "source_paragraph_split", "journal_footer", "reference_original"):
            with self.subTest(reason=reason):
                item = self.item(reason=reason)
                self.assertTrue(layout.item_is_body_layout_text(item))
                self.assertTrue(pipeline.item_is_body_flow_text(item, {"body": "body"}))
                self.assertEqual(layout.body_group_max_gap([item]), layout.BODY_FLOW_MAX_GAP_PT)
                item.style_name = ""
                self.assertEqual(layout.render_text_style_name(item), "body")

    def test_explicit_layout_roles_control_flow_with_any_reason(self):
        for reason in ("", "diagnostic text changed"):
            with self.subTest(reason=reason):
                callout = self.item(role="callout", reason=reason)
                paragraph = self.item(role="source_paragraph", reason=reason)
                self.assertFalse(layout.item_is_body_layout_text(callout))
                self.assertFalse(pipeline.item_is_body_flow_text(callout, {"body": "body"}))
                self.assertFalse(pipeline.item_is_body_flow_text(paragraph, {"body": "body"}))
                self.assertEqual(layout.body_group_max_gap([paragraph]), layout.SOURCE_PARAGRAPH_FLOW_MAX_GAP_PT)

    def test_role_split_allows_only_its_styles_and_still_checks_font(self):
        valid = self.item(role="embedded_heading", style="subheading", reason="changed explanation")
        self.assertEqual(layout.text_item_style_issues(valid, {"body"}), [])
        self.assertTrue(layout.text_item_style_issues(replace(valid, font_size=1), {"body"}))
        self.assertTrue(layout.text_item_style_issues(self.item(role="embedded_heading", style="footer"), {"body"}))
        self.assertTrue(layout.text_item_style_issues(valid, {"reference"}))

    def test_font_policies_have_finite_ranges_and_specific_contexts(self):
        cases = [
            ("normal", "source_adapted", "body", 10.0, True),
            ("normal", "source_adapted", "body", 100.0, False),
            ("normal", "source_adapted", "reference", 6.5, False),
            ("body_flow", "compact_body_flow", "body", 8.0, True),
            ("body_flow", "compact_body_flow", "body", 1.0, False),
            ("body_flow", "compact_body_flow", "body", 12.0, False),
            ("normal", "compact_body_flow", "body", 8.0, False),
            ("body_flow", "compact_body_flow", "heading", 8.0, False),
            ("normal", "document", "body", float("nan"), False),
            ("unknown-role", "document", "body", 9.2, False),
            ("normal", "unknown-policy", "body", 9.2, False),
        ]
        for role, policy, style, size, allowed in cases:
            with self.subTest(role=role, policy=policy, style=style, size=size):
                item = self.item(role=role, policy=policy, style=style, size=size)
                self.assertEqual(not layout.text_item_style_issues(item, {style}), allowed)
        original = self.item(policy="source_adapted", size=10)
        original.kind = "original_selectable_text"
        self.assertTrue(layout.text_item_style_issues(original, {"body"}))

    def test_compact_flow_selection_does_not_read_reason(self):
        item = self.item(role="body_flow", reason="explanation edited")
        item.bbox = (20, 30, 280, 48)
        item.text = "完整的中文正文需要在有限空间中完整保留。" * 3
        plan = self.plan(item)
        self.assertTrue(layout.validate_plan_text_fit(plan))
        pipeline.fit_body_flow_text_items(plan)
        self.assertEqual(layout.validate_plan_text_fit(plan), [])
        self.assertLess(item.font_size, layout.BODY_FONT_SIZE)
        self.assertEqual(item.font_policy, "compact_body_flow")
        self.assertEqual(layout.validate_plan_style_policy(plan), [])

    def test_callout_and_source_paragraph_are_not_merged_when_reason_changes(self):
        for role in ("callout", "source_paragraph"):
            with self.subTest(role=role):
                first = self.item(role=role, reason="edited")
                second = copy.deepcopy(first)
                second.source_ids = ["second"]
                second.bbox = (20, 92, 280, 152)
                plan = self.plan(first)
                plan.items.append(second)
                plan.ledger.append(render_plan.CoverageEntry("second", "body", "translated_text", True))
                pipeline.merge_adjacent_body_text_flows(plan)
                self.assertEqual(len(plan.items), 2)

    def test_splitting_preserves_layout_font_and_component_identity(self):
        item = self.item(role="callout", policy="source_adapted", size=10)
        item.text *= 15
        item.bbox = (20, 10, 280, 250)
        item.component_id = "component"
        item.component_kind = "translated_text"
        plan = self.plan(item)
        plan.items.append(render_plan.RenderItem("original_image_clip", ["figure"], (20, 100, 280, 150)))
        layout.split_translated_text_around_protected(plan)
        text_items = [value for value in plan.items if value.kind == "translated_text"]
        self.assertGreater(len(text_items), 1)
        for value in text_items:
            self.assertEqual((getattr(value, "layout_role", None), getattr(value, "font_policy", None),
                              value.component_id, value.component_kind),
                             ("callout", "source_adapted", "component", "translated_text"))

    def test_objects_and_json_enforce_the_same_explicit_font_policy(self):
        for size, valid in ((8.0, True), (1.0, False)):
            for reason in ("", "anything", "body_flow_compact"):
                plan = self.plan(self.item(role="body_flow", policy="compact_body_flow", size=size, reason=reason))
                payload = render_plan.render_plan_to_json(plan)
                self.assertEqual(payload["render_items"][0].get("layout_role"), "body_flow")
                self.assertEqual(payload["render_items"][0].get("font_policy"), "compact_body_flow")
                for representation in (plan, payload):
                    with self.subTest(size=size, reason=reason, representation=type(representation).__name__):
                        self.assertEqual(not qa_visual.detect_style_issues(representation), valid)

    def test_reference_and_footer_policy_do_not_read_reason(self):
        for reason in ("reference_original", "explanation edited"):
            item = self.item(style="reference", reason=reason)
            item.kind = "original_selectable_text"
            self.assertTrue(pipeline.validate_plan_reference_policy(self.plan(item, "reference")))
        for reason in ("journal_footer", "explanation edited"):
            item = self.item(role="journal_footer", style="footer", reason=reason)
            plan = self.plan(item, "footer")
            plan.items.append(copy.deepcopy(item))
            self.assertTrue(pipeline.validate_plan_footer_policy(plan))

    def test_dense_row_font_policy_requires_body_row_geometry(self):
        item = self.item(policy="dense_visual_row", size=7)
        item.bbox = (20, 30, 280, 38)
        self.assertEqual(layout.text_item_style_issues(item, {"body"}), [])
        self.assertTrue(layout.text_item_style_issues(replace(item, bbox=(20, 30, 280, 90)), {"body"}))
        self.assertTrue(layout.text_item_style_issues(item, {"unknown"}))
        self.assertTrue(layout.text_item_style_issues(replace(item, font_size=1), {"body"}))

    def test_merging_does_not_authorize_an_unapproved_font_size(self):
        first = self.item(size=10.8)
        second = self.item(policy="source_adapted", size=10)
        second.source_ids = ["second"]
        second.bbox = (20, 92, 280, 152)
        plan = self.plan(first)
        plan.items.append(second)
        plan.ledger.append(render_plan.CoverageEntry("second", "body", "translated_text", True))
        pipeline.merge_adjacent_body_text_flows(plan)
        self.assertEqual(len(plan.items), 2)
        self.assertTrue(layout.validate_plan_style_policy(plan))

    def test_merging_does_not_erase_invalid_layout_or_source_roles(self):
        for role, classification in (("mixed_visual_body", "reference"), ("bogus", "body")):
            with self.subTest(role=role, classification=classification):
                first = self.item(role=role)
                second = self.item()
                second.source_ids = ["second"]
                second.bbox = (20, 92, 280, 152)
                plan = self.plan(first, classification)
                plan.items.append(second)
                plan.ledger.append(render_plan.CoverageEntry("second", "body", "translated_text", True))
                self.assertTrue(layout.validate_plan_style_policy(plan))
                pipeline.merge_adjacent_body_text_flows(plan)
                self.assertEqual(len(plan.items), 2)
                self.assertTrue(layout.validate_plan_style_policy(plan))

    def test_body_flow_cannot_reclassify_reference_content(self):
        item = self.item(role="body_flow")
        item.source_ids.append("reference")
        plan = self.plan(item)
        plan.ledger.append(render_plan.CoverageEntry("reference", "reference", "translated_text", True))
        self.assertTrue(layout.validate_plan_style_policy(plan))

    def test_contained_merge_preserves_callouts_and_source_paragraphs(self):
        for role in ("callout", "source_paragraph"):
            for protected_side in ("large", "small"):
                with self.subTest(role=role, protected_side=protected_side):
                    large = self.item()
                    large.bbox = (20, 20, 280, 260)
                    small = self.item()
                    small.source_ids = ["small"]
                    small.bbox = (40, 100, 220, 119)
                    protected = large if protected_side == "large" else small
                    protected.layout_role = role
                    if role == "callout":
                        protected.style_name = "heading"
                        protected.font_size = layout.HEADING_FONT_SIZE
                    plan = self.plan(large)
                    plan.items.append(small)
                    plan.ledger.append(render_plan.CoverageEntry("small", "body", "translated_text", True))
                    self.assertEqual(layout.validate_plan_style_policy(plan), [])
                    pipeline.merge_contained_text_fragments(plan)
                    self.assertEqual(len(plan.items), 2)
                    self.assertIn(protected, plan.items)

    def test_numbered_repair_does_not_move_anchored_layout_roles(self):
        for role in ("callout", "source_paragraph"):
            with self.subTest(role=role):
                first = self.item()
                first.text = "(2) 普通正文。"
                first.bbox = (20, 30, 280, 70)
                anchored = self.item(role=role)
                anchored.source_ids = ["anchored"]
                anchored.text = "(1) 警示正文。"
                anchored.bbox = (20, 90, 280, 130)
                plan = self.plan(first)
                plan.items.append(anchored)
                plan.ledger.append(render_plan.CoverageEntry("anchored", "body", "translated_text", True))
                before = [i.bbox for i in plan.items]
                pipeline.repair_numbered_enumeration_flow(plan)
                self.assertEqual([i.bbox for i in plan.items], before)

    def test_numbered_continuation_does_not_cross_anchored_layout_roles(self):
        for role in ("callout", "source_paragraph"):
            for protected_side in ("source", "target"):
                with self.subTest(role=role, protected_side=protected_side):
                    first = self.item(role=role if protected_side == "target" else "normal")
                    first.text = "(1) 普通正文。"
                    first.bbox = (20, 30, 280, 70)
                    second = self.item(role=role if protected_side == "source" else "normal")
                    second.source_ids = ["second"]
                    second.text = "警示说明。(2) 警示列表。"
                    second.bbox = (20, 90, 280, 130)
                    plan = self.plan(first)
                    plan.items.append(second)
                    plan.ledger.append(render_plan.CoverageEntry("second", "body", "translated_text", True))
                    before = [i.text for i in plan.items]
                    pipeline.repair_numbered_enumeration_flow(plan)
                    self.assertEqual([i.text for i in plan.items], before)

    def test_short_fragment_absorption_preserves_roles_and_invalid_permissions(self):
        for role, classification in (("callout", "body"), ("source_paragraph", "body"),
                                     ("bogus", "body"), ("normal", "reference")):
            with self.subTest(role=role, classification=classification):
                first = self.item()
                first.text = "普通正文，包括链表。"
                first.bbox = (20, 20, 280, 70)
                fragment = self.item(role=role)
                fragment.source_ids = ["fragment"]
                fragment.text = "链表"
                fragment.bbox = (20, 80, 280, 90)
                plan = self.plan(first)
                plan.items.append(fragment)
                plan.ledger.append(render_plan.CoverageEntry("fragment", classification, "translated_text", True))
                blocks = [{"id": "body", "text": "A complete source paragraph."},
                          {"id": "fragment", "text": "list"}]
                before = layout.validate_plan_style_policy(plan)
                pipeline.drop_redundant_short_body_fragments(plan, blocks)
                self.assertEqual(len(plan.items), 2)
                self.assertEqual(layout.validate_plan_style_policy(plan), before)

    def test_numbered_repair_keeps_original_selectable_body_text_eligible(self):
        items = [self.item(), self.item()]
        items[0].text, items[1].text = "(2) Second item.", "(1) First item."
        for index, item in enumerate(items):
            item.kind = "original_selectable_text"
            item.source_ids = [str(index)]
            item.bbox = (20, 30 + index*60, 280, 70 + index*60)
        plan = render_plan.PageRenderPlan(1, items=items,
                                         ledger=[render_plan.CoverageEntry(str(i), "body", "original_selectable_text", True)
                                                 for i in range(2)])
        pipeline.repair_numbered_enumeration_flow(plan)
        self.assertLess(items[1].bbox[1], items[0].bbox[1])

    def test_editing_all_fixture_reasons_does_not_change_final_layout_or_style_checks(self):
        root = Path(__file__).parent / "fixtures/pdf_render"
        for document in ("bert", "wait-free"):
            for fixture in iter_render_plan_fixtures(root, document):
                with self.subTest(document=document, page=fixture.page_num):
                    original = fixture.plan
                    edited = copy.deepcopy(original)
                    for item in edited.items:
                        item.fallback_reason = "edited explanation"
                    for entry in edited.ledger:
                        entry.fallback_reason = "edited explanation"
                    for plan in (original, edited):
                        pipeline.normalize_vector_text_layout(plan, plan.page_size)
                    self.assertEqual([(i.bbox, i.text, i.font_size, i.style_name, i.layout_role, i.font_policy)
                                      for i in edited.items],
                                     [(i.bbox, i.text, i.font_size, i.style_name, i.layout_role, i.font_policy)
                                      for i in original.items])
                    self.assertEqual(layout.validate_plan_style_policy(edited), layout.validate_plan_style_policy(original))


if __name__ == "__main__":
    unittest.main()
