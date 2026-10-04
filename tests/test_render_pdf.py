import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import render_pdf
import layout
import render_plan
import qa_visual
import pipeline as pdf


class RenderPdfExtractionModuleTests(unittest.TestCase):

    def test_native_images_are_planned_per_occurrence_and_keep_mask_appearance(self):
        fitz = render_pdf.load_fitz()
        for mode, color in (("RGB", (180, 20, 40)), ("RGBA", (180, 20, 40, 128))):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source, output = root / "source.pdf", root / "output.pdf"
                stream = io.BytesIO()
                Image.new(mode, (20, 20), color).save(stream, format="PNG")
                with fitz.open() as doc:
                    page = doc.new_page(width=240, height=300)
                    xref = page.insert_image((20, 40, 80, 100), stream=stream.getvalue())
                    page.insert_image((140, 180, 200, 240), xref=xref)
                    doc.save(source)
                with fitz.open(source) as doc:
                    expected = doc[0].get_pixmap().samples
                result = pdf.write_vector_pdf(source, output, [(1, [])], {}, 72,
                                              {"plans_dir": root / "plans"})
                plan = result.plans[0]
                self.assertEqual(len(plan.items), 2)
                self.assertEqual([item.source_ids for item in plan.items], [["p001i0000"], ["p001i0001"]])
                self.assertEqual({entry.block_id for entry in plan.ledger}, {"p001i0000", "p001i0001"})
                self.assertTrue(all(item.source_image_xref == (xref if mode == "RGB" else 0)
                                    for item in plan.items))
                artifact = json.loads((root / "plans/page-001.render-plan.json").read_text())
                self.assertEqual(len(artifact["render_items"]), 2)
                self.assertEqual(artifact["validation_results"]["coverage_errors"], [])
                with fitz.open(output) as doc:
                    self.assertEqual(doc[0].get_pixmap().samples, expected)

    def test_overlapping_native_images_preserve_source_compositing(self):
        fitz = render_pdf.load_fitz()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, output = root / "source.pdf", root / "output.pdf"
            with fitz.open() as doc:
                page = doc.new_page(width=200, height=240)
                for bbox, color in (((20, 20, 100, 100), "red"), ((50, 50, 120, 120), "blue")):
                    stream = io.BytesIO()
                    Image.new("RGB", (20, 20), color).save(stream, format="PNG")
                    page.insert_image(bbox, stream=stream.getvalue())
                doc.save(source)
            with fitz.open(source) as doc:
                expected = doc[0].get_pixmap().samples
                doc[0].get_pixmap().save(root / "source.png")
            result = pdf.write_vector_pdf(source, output, [(1, [])], {}, 72)
            with fitz.open(output) as doc:
                self.assertEqual(doc[0].get_pixmap().samples, expected)
            plan = result.plans[0]
            self.assertEqual(qa_visual.detect_image_clip_boundary_issues(plan, root / "source.png", (200, 240)), [])
            plan.items.pop()
            issues = qa_visual.detect_image_clip_boundary_issues(plan, root / "source.png", (200, 240))
            self.assertIn("clipped_content", [issue.category for issue in issues])

    def test_native_image_under_selectable_text_preserves_visible_text(self):
        fitz = render_pdf.load_fitz()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, output = root / "source.pdf", root / "output.pdf"
            stream = io.BytesIO()
            Image.new("RGB", (20, 20), (220, 220, 220)).save(stream, format="PNG")
            with fitz.open() as doc:
                page = doc.new_page(width=200, height=200)
                page.insert_image((20, 20, 180, 120), stream=stream.getvalue())
                page.insert_text((40, 70), "References")
                doc.save(source)
            block = {"id": "p001b0001", "page": 1, "text": "References",
                     "xMin": 40, "yMin": 50, "xMax": 180, "yMax": 90}

            result = pdf.write_vector_pdf(source, output, [(1, [block])], {}, 72)
            self.assertEqual([item.kind for item in result.plans[0].items],
                             ["original_image_clip", "original_selectable_text"])
            with fitz.open(source) as doc:
                source_pixels = doc[0].get_pixmap(clip=fitz.Rect(40, 50, 180, 90)).samples
            with fitz.open(output) as doc:
                output_pixels = doc[0].get_pixmap(clip=fitz.Rect(40, 50, 180, 90)).samples
                self.assertIn("References", doc[0].get_text())
            self.assertGreater(sum(value < 100 for value in source_pixels), 0)
            self.assertGreater(sum(value < 100 for value in output_pixels), 0)

    def test_adjacent_independent_native_images_have_no_clip_warning(self):
        fitz = render_pdf.load_fitz()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, output = root / "source.pdf", root / "output.pdf"
            with fitz.open() as doc:
                page = doc.new_page(width=200, height=200)
                for bbox, color in (((20, 20, 60, 60), "red"), ((61, 20, 101, 60), "blue")):
                    stream = io.BytesIO()
                    Image.new("RGB", (20, 20), color).save(stream, format="PNG")
                    page.insert_image(bbox, stream=stream.getvalue())
                doc.save(source)
            with fitz.open(source) as doc:
                source_pixels = doc[0].get_pixmap().samples
                doc[0].get_pixmap().save(root / "source.png")

            pdf.write_vector_pdf(source, output, [(1, [])], {}, 72,
                                 {"plans_dir": root / "plans"})
            with fitz.open(output) as doc:
                self.assertEqual(doc[0].get_pixmap().samples, source_pixels)
            report = qa_visual.generate_visual_qa_report(
                [root / "plans/page-001.render-plan.json"], output_dir=root / "qa",
                source_png_paths={1: root / "source.png"},
            )
            self.assertEqual(report.error_count, 0)
            report_data = json.loads(report.json_path.read_text())
            self.assertEqual(report_data["issue_count"], 0)
            self.assertEqual(report_data["plan_artifact_paths"],
                             [str(root / "plans/page-001.render-plan.json")])

    def test_missing_native_image_coverage_blocks_publication(self):
        fitz = render_pdf.load_fitz()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, output = root / "source.pdf", root / "output.pdf"
            stream = io.BytesIO()
            Image.new("RGB", (20, 20), "red").save(stream, format="PNG")
            with fitz.open() as doc:
                page = doc.new_page(width=200, height=240)
                page.insert_image((20, 20, 100, 100), stream=stream.getvalue())
                doc.save(source)
            output.write_bytes(b"previous output")
            def lose_image(plan, *args, **kwargs):
                plan.items.clear()
            with (patch.object(pdf, "normalize_vector_text_layout", side_effect=lose_image),
                  patch.object(pdf, "render_plan_item") as draw):
                with self.assertRaisesRegex(RuntimeError, "source image.*not fully covered"):
                    pdf.write_vector_pdf(source, output, [(1, [])], {}, 72)
                draw.assert_not_called()
            self.assertEqual(output.read_bytes(), b"previous output")

    def test_grouped_image_row_plans_a_page_crop_with_each_source_id(self):
        fitz = render_pdf.load_fitz()
        with fitz.open() as doc:
            page = doc.new_page(width=300, height=240)
            stream = io.BytesIO()
            Image.new("RGB", (20, 20), "red").save(stream, format="PNG")
            for x in (20, 120, 220):
                page.insert_image((x, 50, x + 20, 70), stream=stream.getvalue())
            items = render_pdf.source_image_items(page, 4)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].source_ids, ["p004i0000", "p004i0001", "p004i0002"])
            self.assertEqual(items[0].source_image_xref, 0)
            with fitz.open() as out:
                output = out.new_page(width=300, height=240)
                render_pdf.render_plan_item(output, page, fitz, items[0], 72)
                self.assertEqual(output.get_pixmap().samples, page.get_pixmap().samples)

    def test_images_outside_page_preserve_only_visible_pixels_without_stretching(self):
        fitz = render_pdf.load_fitz()
        for bbox in ((-20, 50, 80, 150), (140, 50, 220, 130),
                     (50, -20, 150, 80), (50, 160, 150, 220), (220, 10, 270, 60)):
            with self.subTest(bbox=bbox), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source, output = root / "source.pdf", root / "output.pdf"
                stream = io.BytesIO()
                image = Image.new("RGB", (20, 20), "red")
                image.paste("blue", (10, 0, 20, 10))
                image.save(stream, format="PNG")
                with fitz.open() as doc:
                    page = doc.new_page(width=200, height=200)
                    page.insert_image(bbox, stream=stream.getvalue(), keep_proportion=False)
                    doc.save(source)
                with fitz.open(source) as doc:
                    expected = doc[0].get_pixmap().samples
                result = pdf.write_vector_pdf(source, output, [(1, [])], {}, 72)
                for item in result.plans[0].items:
                    self.assertEqual(item.source_image_xref, 0)
                    self.assertGreaterEqual(min(item.bbox), 0)
                    self.assertLessEqual(max(item.bbox), 200)
                with fitz.open(output) as doc:
                    self.assertEqual(doc[0].get_pixmap().samples, expected)

    def test_native_image_covered_by_visual_clips_is_not_drawn_twice(self):
        # A partial intersection requires clipping only the uncovered remainder.
        for visual_box in ((10, 10, 100, 110), (10, 10, 55, 110)):
            with self.subTest(visual_box=visual_box):
                plan = render_plan.PageRenderPlan(1, items=[
                    render_plan.RenderItem("original_image_clip", ["figure"], visual_box)])
                source = render_plan.RenderItem("original_image_clip", ["p001i0000"], (20, 20, 80, 100))
                pdf.add_source_images_to_plan(plan, [source])
                self.assertEqual(render_plan.validate_source_image_coverage(plan, [source]), [])
                for index, left in enumerate(plan.items):
                    for right in plan.items[index + 1:]:
                        self.assertEqual(render_plan.bbox_overlap_area(left.bbox, right.bbox), 0)
                self.assertEqual(len(plan.items), 1 if visual_box[2] == 100 else 2)
                # The original source obligation cannot be changed by deduplication.
                self.assertEqual(source.source_ids, ["p001i0000"])
                plan.items.clear()
                self.assertTrue(render_plan.validate_source_image_coverage(plan, [source]))

    def test_native_image_is_an_obstacle_before_text_layout(self):
        fitz = render_pdf.load_fitz()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, output = root / "source.pdf", root / "output.pdf"
            stream = io.BytesIO()
            Image.new("RGB", (20, 20), "red").save(stream, format="PNG")
            with fitz.open() as doc:
                page = doc.new_page(width=240, height=300)
                page.insert_image((20, 120, 220, 160), stream=stream.getvalue(), keep_proportion=False)
                doc.save(source)
            block = {"id": "p001b0001", "page": 1, "text": "This is an explanation of the complete method.",
                     "xMin": 20, "yMin": 40, "xMax": 220, "yMax": 220}
            result = pdf.write_vector_pdf(source, output, [(1, [block])], {block["id"]: "完整的方法说明。"}, 72)
            plan = result.plans[0]
            self.assertTrue(any(item.source_ids == ["p001i0000"] for item in plan.items))
            self.assertEqual(render_plan.validate_plan_layout(plan, (240, 300)), [])
            self.assertEqual("".join(item.text for item in plan.items if item.kind == "translated_text"),
                             "完整的方法说明。")

    def test_insert_vector_textbox_direct_api_rejects_fixed_style_overflow(self):
        fitz = render_pdf.load_fitz()
        doc = fitz.open()
        page = doc.new_page(width=100, height=100)

        ok = render_pdf.insert_vector_textbox(
            page,
            fitz,
            fitz.Rect(10, 10, 40, 18),
            "这是一个很长很长的译文，不能通过缩小字号塞进框里。",
            layout.DOCUMENT_STYLES["body"].font_size,
            layout.VECTOR_BODY_COLOR,
            line_height_factor=layout.DOCUMENT_STYLES["body"].line_height_factor,
        )
        doc.close()

        self.assertFalse(ok)

    def test_insert_vector_textbox_uses_shared_text_box_fit_plan(self):
        fitz = render_pdf.load_fitz()
        doc = fitz.open()
        page = doc.new_page(width=120, height=120)
        calls = []
        original = render_pdf.text_box_fit_plan

        def recording_fit_plan(*args, **kwargs):
            plan = original(*args, **kwargs)
            calls.append(plan)
            return plan

        try:
            render_pdf.text_box_fit_plan = recording_fit_plan
            ok = render_pdf.insert_vector_textbox(
                page,
                fitz,
                fitz.Rect(10, 10, 110, 70),
                "第一段。\n第二段。",
                layout.DOCUMENT_STYLES["body"].font_size,
                layout.VECTOR_BODY_COLOR,
                line_height_factor=layout.DOCUMENT_STYLES["body"].line_height_factor,
                paragraph_spacing=layout.DOCUMENT_STYLES["body"].paragraph_spacing,
                min_line_height_factor=layout.DOCUMENT_STYLES["body"].min_line_height_factor,
                min_paragraph_spacing=layout.DOCUMENT_STYLES["body"].min_paragraph_spacing,
            )
        finally:
            render_pdf.text_box_fit_plan = original
            doc.close()

        self.assertTrue(ok)
        self.assertTrue(calls)
        self.assertIsNotNone(calls[0])
        self.assertGreater(len(calls[0].lines), 0)

    def test_render_plan_item_direct_api_raises_on_text_overflow(self):
        fitz = render_pdf.load_fitz()
        src_doc = fitz.open()
        src_page = src_doc.new_page(width=100, height=100)
        out_doc = fitz.open()
        out_page = out_doc.new_page(width=100, height=100)
        item = render_plan.RenderItem(
            "translated_text",
            ["body"],
            (10, 10, 40, 18),
            text="这是一个很长很长的译文，渲染阶段不能偷偷贴回英文截图。" * 4,
            font_size=layout.DOCUMENT_STYLES["body"].font_size,
            style_name="body",
        )

        with self.assertRaisesRegex(RuntimeError, "did not fit during render"):
            render_pdf.render_plan_item(out_page, src_page, fitz, item, 72)

        out_doc.close()
        src_doc.close()

    def test_render_plan_item_direct_api_prefers_cached_source_page_png(self):
        fitz = render_pdf.load_fitz()
        with tempfile.TemporaryDirectory() as tmp:
            source_image = Path(tmp) / "page-001.png"
            image = Image.new("RGB", (100, 100), "white")
            for x in range(10, 40):
                for y in range(10, 40):
                    image.putpixel((x, y), (0, 0, 0))
            image.save(source_image)

            src_doc = fitz.open()
            src_page = src_doc.new_page(width=100, height=100)
            out_doc = fitz.open()
            out_page = out_doc.new_page(width=100, height=100)
            item = render_plan.RenderItem("original_image_clip", ["fig"], (10, 10, 40, 40))

            render_pdf.render_plan_item(out_page, src_page, fitz, item, 72, source_image_path=source_image)

            pix = out_page.get_pixmap(alpha=False)
            rendered = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            self.assertLess(sum(rendered.getpixel((20, 20))), 40)

            out_doc.close()
            src_doc.close()

    def test_draw_block_uses_expanded_raster_line_height(self):
        class FakeRasterFont:
            def getmetrics(self):
                return 10, 10

        class FakeTextDraw:
            def __init__(self):
                self.calls = []

            def text(self, xy, text, font=None, fill=None):
                self.calls.append((xy, text))

        text_draw = FakeTextDraw()
        image = Image.new("RGBA", (220, 140), "white")
        block = {
            "id": "p001b0001",
            "page": 1,
            "text": "source line",
            "xMin": 10.0,
            "yMin": 10.0,
            "xMax": 190.0,
            "yMax": 110.0,
        }

        with (
            patch.object(pdf, "fit_font_and_lines", return_value=(12, ["第一行", "第二行", "第三行"])),
            patch.object(pdf, "raster_image_font", return_value=FakeRasterFont()),
            patch.object(pdf.ImageDraw, "Draw", return_value=text_draw),
        ):
            pdf.draw_block(image, block, "ignored", dpi=72, render_box=(10, 10, 190, 110))

        self.assertEqual([xy[1] for xy, _text in text_draw.calls], [2, 32, 62])

    def test_raster_assembly_preserves_page_order_size_without_external_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = [root / "source-005.png", root / "source-009.png"]
            for path, color in zip(paths, [(240, 0, 0), (0, 0, 240)]):
                Image.new("RGB", (120, 160), color).save(path)
            output = root / "out.pdf"
            with patch("subprocess.run", side_effect=AssertionError("external command invoked")):
                render_pdf.write_raster_pdf(output, paths, (120.0, 160.0))
            with render_pdf.load_fitz().open(output) as doc:
                self.assertEqual(doc.page_count, 2)
                for page, expected in zip(doc, [(240, 0, 0), (0, 0, 240)]):
                    self.assertEqual((page.rect.width, page.rect.height), (120, 160))
                    self.assertEqual(page.get_pixmap().pixel(60, 80)[:3], expected)

    def test_raster_assembly_missing_image_does_not_replace_existing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "page.png"
            Image.new("RGB", (10, 10), "red").save(image)
            output = root / "out.pdf"
            output.write_bytes(b"previous accepted PDF")
            with self.assertRaises(FileNotFoundError):
                render_pdf.write_raster_pdf(output, [image, root / "missing.png"], (10, 10))
            self.assertEqual(output.read_bytes(), b"previous accepted PDF")


if __name__ == "__main__":
    unittest.main()
