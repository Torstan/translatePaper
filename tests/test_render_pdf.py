import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import render_pdf
import translate_pdf_via_codex as pdf


class RenderPdfExtractionModuleTests(unittest.TestCase):
    def test_render_pdf_api_direct_and_compatibility_identity(self):
        self.assertIs(pdf.load_fitz, render_pdf.load_fitz)
        self.assertIs(pdf.insert_vector_textbox, render_pdf.insert_vector_textbox)
        self.assertIs(pdf.draw_mixed_pdf_lines, render_pdf.draw_mixed_pdf_lines)
        self.assertIs(pdf.insert_source_clip, render_pdf.insert_source_clip)
        self.assertIs(pdf.source_image_clip_stream, render_pdf.source_image_clip_stream)
        self.assertIs(pdf.insert_source_image_clip, render_pdf.insert_source_image_clip)
        self.assertIs(pdf.render_plan_item, render_pdf.render_plan_item)

    def test_insert_vector_textbox_direct_api_rejects_fixed_style_overflow(self):
        fitz = render_pdf.load_fitz()
        doc = fitz.open()
        page = doc.new_page(width=100, height=100)

        ok = render_pdf.insert_vector_textbox(
            page,
            fitz,
            fitz.Rect(10, 10, 40, 18),
            "这是一个很长很长的译文，不能通过缩小字号塞进框里。",
            pdf.DOCUMENT_STYLES["body"].font_size,
            pdf.VECTOR_BODY_COLOR,
            line_height_factor=pdf.DOCUMENT_STYLES["body"].line_height_factor,
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
                pdf.DOCUMENT_STYLES["body"].font_size,
                pdf.VECTOR_BODY_COLOR,
                line_height_factor=pdf.DOCUMENT_STYLES["body"].line_height_factor,
                paragraph_spacing=pdf.DOCUMENT_STYLES["body"].paragraph_spacing,
                min_line_height_factor=pdf.DOCUMENT_STYLES["body"].min_line_height_factor,
                min_paragraph_spacing=pdf.DOCUMENT_STYLES["body"].min_paragraph_spacing,
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
        item = pdf.RenderItem(
            "translated_text",
            ["body"],
            (10, 10, 40, 18),
            text="这是一个很长很长的译文，渲染阶段不能偷偷贴回英文截图。" * 4,
            font_size=pdf.DOCUMENT_STYLES["body"].font_size,
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
            item = pdf.RenderItem("original_image_clip", ["fig"], (10, 10, 40, 40))

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
