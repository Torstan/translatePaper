import tempfile
import unittest
from pathlib import Path

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
            allow_shrink=False,
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
                allow_shrink=False,
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


if __name__ == "__main__":
    unittest.main()
