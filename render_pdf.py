import io
import math
import sys
from pathlib import Path

from PIL import Image

from layout import (
    DOCUMENT_STYLES,
    VECTOR_BODY_COLOR,
    TextStyle,
    drawable_pdf_line_tokens,
    pdf_token_font,
    pdf_token_fontfile,
    pdf_token_width,
    render_text_style_name,
    text_box_fit_plan,
    text_style,
)
from render_plan import RenderItem
from regions import grouped_image_row_clips, image_info_preserve_bbox, valid_image_insert_bbox


TOOL_ROOT = Path(__file__).resolve().parent
VENDOR_ROOT = TOOL_ROOT / "vendor"


def load_fitz():
    if str(VENDOR_ROOT) not in sys.path:
        sys.path.insert(0, str(VENDOR_ROOT))
    try:
        import fitz
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "PDF rendering requires PyMuPDF. "
            "Install it with: python3 -m pip install --target vendor pymupdf"
        ) from exc
    return fitz


def insert_vector_textbox(
    page,
    fitz,
    rect,
    text: str,
    font_size: float,
    color,
    *,
    line_height_factor: float = 1.22,
    paragraph_spacing: float = 0.0,
    letter_spacing: float = 0.0,
    allow_shrink: bool = False,
    min_line_height_factor: float | None = None,
    min_paragraph_spacing: float | None = None,
):
    if not text.strip() or rect.is_empty:
        return True

    def draw_if_fits(size: float, line_factor: float, para_spacing: float) -> bool:
        style = TextStyle(
            font_size=size,
            line_height_factor=line_factor,
            paragraph_spacing=para_spacing,
            letter_spacing=letter_spacing,
            color=color,
            min_line_height_factor=min_line_height_factor,
            min_paragraph_spacing=min_paragraph_spacing,
        )
        plan = text_box_fit_plan(fitz, text, rect.width, rect.height, style, font_size=size)
        if plan is None:
            return False
        draw_mixed_pdf_lines(
            page,
            fitz,
            rect,
            plan.lines,
            plan.font_size,
            plan.font_size * plan.line_height_factor,
            color,
            paragraph_spacing=plan.paragraph_spacing,
            letter_spacing=letter_spacing,
        )
        return True

    if draw_if_fits(font_size, line_height_factor, paragraph_spacing):
        return True
    if not allow_shrink:
        return False
    size = font_size - 0.5
    while size >= 5.0:
        if draw_if_fits(size, line_height_factor, paragraph_spacing):
            return True
        size -= 0.5
    return False


def draw_mixed_pdf_lines(
    page,
    fitz,
    rect,
    lines,
    font_size: float,
    line_height: float,
    color,
    *,
    paragraph_spacing: float = 0.0,
    letter_spacing: float = 0.0,
):
    y = rect.y0 + font_size
    for line in lines:
        if y > rect.y1:
            return
        if line == []:
            y += line_height + paragraph_spacing
            continue
        x = rect.x0
        for token in drawable_pdf_line_tokens(line):
            if not token:
                continue
            fontname = pdf_token_font(token)
            fontfile = pdf_token_fontfile(token)
            if fontfile:
                page.insert_text(
                    (x, y),
                    token,
                    fontsize=font_size,
                    fontname=fontname,
                    fontfile=fontfile,
                    color=color,
                )
            else:
                page.insert_text(
                    (x, y),
                    token,
                    fontsize=font_size,
                    fontname=fontname,
                    color=color,
                )
            x += pdf_token_width(fitz, token, font_size) + letter_spacing
        y += line_height


def preserve_images_on_page(src_page, out_page, fitz, dpi: int):
    matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    page_area = max(1.0, src_page.rect.get_area())
    image_entries = []
    for index, info in enumerate(src_page.get_image_info(xrefs=True)):
        bbox = image_info_preserve_bbox(info, page_area)
        if bbox is None:
            continue
        image_entries.append({"index": index, "info": info, "bbox": bbox})

    grouped_indices = set()
    for clip in grouped_image_row_clips(image_entries, (src_page.rect.width, src_page.rect.height)):
        if not valid_image_insert_bbox(clip["bbox"]):
            continue
        pix = src_page.get_pixmap(matrix=matrix, clip=fitz.Rect(clip["bbox"]), alpha=False)
        out_page.insert_image(clip["bbox"], pixmap=pix, keep_proportion=False)
        grouped_indices.update(clip["indices"])

    for entry in image_entries:
        if entry["index"] in grouped_indices:
            continue
        info = entry["info"]
        if not valid_image_insert_bbox(entry["bbox"]):
            continue
        bbox = fitz.Rect(entry["bbox"])
        if info.get("has-mask"):
            pix = src_page.get_pixmap(matrix=matrix, clip=bbox, alpha=False)
            out_page.insert_image(bbox, pixmap=pix, keep_proportion=False)
            continue
        image_stream = None
        xref = info.get("xref") or 0
        if xref:
            try:
                image_stream = src_page.parent.extract_image(xref).get("image")
            except Exception:  # noqa: BLE001
                image_stream = None
        if image_stream:
            out_page.insert_image(bbox, stream=image_stream, keep_proportion=False)
        else:
            pix = src_page.get_pixmap(matrix=matrix, clip=bbox, alpha=False)
            out_page.insert_image(bbox, pixmap=pix, keep_proportion=False)


def insert_source_clip(src_page, out_page, fitz, bbox, dpi: int):
    rect = fitz.Rect(bbox)
    if rect.is_empty:
        return
    matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    pix = src_page.get_pixmap(matrix=matrix, clip=rect, alpha=False)
    out_page.insert_image(rect, pixmap=pix, keep_proportion=False)


def source_page_image_path(job_paths, page_num: int) -> Path | None:
    if not job_paths:
        return None
    pages_dir = job_paths.get("pages_dir")
    if not pages_dir:
        return None
    path = Path(pages_dir) / f"page-{page_num:03d}.png"
    return path if path.exists() else None


def source_image_clip_stream(source_image_path: Path, page_rect, bbox) -> bytes | None:
    rect = tuple(float(value) for value in bbox)
    if rect[2] <= rect[0] or rect[3] <= rect[1]:
        return None
    with Image.open(source_image_path) as image:
        source = image.convert("RGB")
        scale_x = source.width / max(1.0, float(page_rect.width))
        scale_y = source.height / max(1.0, float(page_rect.height))
        crop_box = (
            max(0, int(math.floor(rect[0] * scale_x))),
            max(0, int(math.floor(rect[1] * scale_y))),
            min(source.width, int(math.ceil(rect[2] * scale_x))),
            min(source.height, int(math.ceil(rect[3] * scale_y))),
        )
        if crop_box[2] <= crop_box[0] or crop_box[3] <= crop_box[1]:
            return None
        clipped = source.crop(crop_box)
        out = io.BytesIO()
        clipped.save(out, format="PNG", optimize=True)
        return out.getvalue()


def insert_source_image_clip(out_page, fitz, source_image_path: Path, page_rect, bbox) -> bool:
    rect = fitz.Rect(bbox)
    if rect.is_empty:
        return False
    stream = source_image_clip_stream(source_image_path, page_rect, bbox)
    if not stream:
        return False
    out_page.insert_image(rect, stream=stream, keep_proportion=False)
    return True


def render_plan_item(out_page, src_page, fitz, item: RenderItem, dpi: int, source_image_path: Path | None = None):
    rect = fitz.Rect(item.bbox)
    if item.kind == "translated_text":
        style = text_style(render_text_style_name(item))
        if insert_vector_textbox(
            out_page,
            fitz,
            rect,
            item.text,
            item.font_size or style.font_size,
            item.color,
            line_height_factor=style.line_height_factor,
            paragraph_spacing=style.paragraph_spacing,
            letter_spacing=style.letter_spacing,
            min_line_height_factor=style.min_line_height_factor,
            min_paragraph_spacing=style.min_paragraph_spacing,
            allow_shrink=False,
        ):
            return
        raise RuntimeError(f"text item {item.source_ids} did not fit during render")
    if item.kind == "original_selectable_text":
        style = text_style(render_text_style_name(item))
        if insert_vector_textbox(
            out_page,
            fitz,
            rect,
            item.text,
            item.font_size or style.font_size,
            VECTOR_BODY_COLOR,
            line_height_factor=style.line_height_factor,
            paragraph_spacing=style.paragraph_spacing,
            letter_spacing=style.letter_spacing,
            min_line_height_factor=style.min_line_height_factor,
            min_paragraph_spacing=style.min_paragraph_spacing,
            allow_shrink=False,
        ):
            return
        raise RuntimeError(f"selectable text item {item.source_ids or item.fallback_reason} did not fit during render")
    if item.kind == "original_image_clip":
        if source_image_path and insert_source_image_clip(out_page, fitz, source_image_path, src_page.rect, item.bbox):
            return
        insert_source_clip(src_page, out_page, fitz, item.bbox, dpi)
        return


def write_raster_pdf(pdf_output: Path, image_paths: list[Path], page_size) -> None:
    """Assemble one PDF page per raster image; publish only after all pages succeed."""
    fitz = load_fitz()
    width, height = page_size
    with fitz.open() as doc:
        for image_path in image_paths:
            if not image_path.exists():
                raise FileNotFoundError(f"missing translated raster page: {image_path}")
            page = doc.new_page(width=width, height=height)
            page.insert_image(page.rect, filename=str(image_path))
        pdf_output.parent.mkdir(parents=True, exist_ok=True)
        tmp_output = pdf_output.with_name(f"{pdf_output.name}.tmp")
        doc.save(tmp_output, garbage=4, deflate=True)
        tmp_output.replace(pdf_output)
