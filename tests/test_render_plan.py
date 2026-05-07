import unittest

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

    def test_untranslated_body_uses_original_text_fallback(self):
        blocks = [
            block("p004b0005", 4, "(2) In(A) is a set of input events,", x0=135, y0=207, x1=282, y1=216),
        ]

        plan = pdf.build_page_render_plan(4, blocks, {}, page_size=(622, 798), bbox_lines=None)
        item = plan.items[0]
        entry = plan.ledger[0]

        self.assertEqual(item.kind, "original_selectable_text")
        self.assertEqual(item.text, "(2) In(A) is a set of input events,")
        self.assertEqual(entry.fallback_reason, "missing_translation")

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


if __name__ == "__main__":
    unittest.main()
