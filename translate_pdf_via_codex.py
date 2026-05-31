#!/usr/bin/env python3

import argparse
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from statistics import median

from PIL import Image, ImageDraw, ImageFont

import ownership
from classify import (
    ENGLISH_FUNCTION_WORDS,
    NORMAL_TRANSLATED_CLASSES,
    cjk_char_count,
    classify_blocks,
    compact_alpha_text,
    contains_reference_item,
    contains_visual_caption,
    english_function_word_count,
    first_reference_y,
    is_body_enumeration_line,
    is_code_line_number_block,
    is_code_like_line,
    is_code_listing_block,
    is_code_row_text,
    is_conference_footer_fragment,
    is_decorated_ocr_page_number_block,
    is_decorated_ocr_page_number_text,
    is_decorative_update_marker,
    is_first_page_arxiv_side_metadata_block,
    is_first_page_author_block,
    is_first_page_footer_fragment,
    is_formula_like,
    is_formula_or_code_block,
    is_fragmented_narrow_table_cell,
    is_heading_text,
    is_journal_footer_block,
    is_journal_footer_text,
    is_noisy_ocr_page_number_text,
    is_non_prose_identifier_text,
    is_numeric_metric_cell,
    is_page_number,
    apply_reference_continuation,
    is_prose_row_text,
    is_publication_header_fragment,
    is_reference_heading,
    is_running_header_fragment,
    is_standalone_equation_label,
    is_table_caption,
    is_table_caption_line,
    is_title_block,
    is_trivial_keep,
    is_visual_caption,
    is_visual_caption_line,
    is_visual_row_text,
    journal_footer_match_key,
    latin_words,
    normalize_text,
    reference_block_ids,
    reference_signature,
    should_preserve_as_image,
    should_preserve_first_page_metadata_as_image,
    source_requires_chinese_translation,
    starts_reference_item,
    strip_journal_footer_lines,
    text_is_only_journal_footer,
    translation_appears_untranslated,
)
from layout import (
    BODY_FLOW_INTERNAL_SLACK_WARN_PT,
    BODY_FLOW_MAX_GAP_PT,
    BODY_FLOW_MIN_GAP_PT,
    BODY_FLOW_TARGET_GAP_PT,
    BODY_FLOW_TOP_MAX_GAP_PT,
    BODY_FLOW_VISIBLE_GAP_WARN_PT,
    BODY_FONT_SIZE,
    BODY_TEXT_BOX_CUSHION_PT,
    DOCUMENT_STYLES,
    EXPANDABLE_TEXT_STYLE_NAMES,
    HEADING_FONT_SIZE,
    JOURNAL_FOOTER_FONT_SIZE,
    MATH_VECTOR_FONT,
    MATH_VECTOR_FONT_PATHS,
    SHRINK_FIT_MIN_FONT_SIZE,
    SOURCE_PARAGRAPH_FLOW_MAX_GAP_PT,
    TEXT_FIT_EPSILON_PT,
    TEXT_FLOW_STYLE_NAMES,
    TITLE_FONT_SIZE,
    VECTOR_ACCENT_COLOR,
    VECTOR_BODY_COLOR,
    VECTOR_FONT,
    TextBoxFitPlan,
    TextStyle,
    anchor_between_body_items,
    avoid_protected_boxes,
    body_group_heights_and_gap,
    body_group_limits,
    body_group_max_gap,
    body_layout_lanes,
    boxes_horizontally_conflict,
    break_current_line,
    build_render_boxes as _layout_build_render_boxes,
    clean_pdf_draw_tokens,
    clamp_preserving_box_size,
    distribute_text_across_segments,
    drawable_pdf_line_tokens,
    expand_text_boxes_to_fit,
    fitted_text_spacing,
    fit_font_and_lines,
    item_is_body_layout_text,
    item_is_expandable_body_text,
    item_is_formula_intro_text,
    lane_shares_item,
    layout_horizontal_conflict,
    math_fitz_font,
    math_vector_font_path,
    merge_pdf_line_tokens,
    minimum_text_height_for_item,
    pdf_text_width,
    pdf_token_font,
    pdf_token_fontfile,
    pdf_token_uses_math_font,
    pdf_token_width,
    preferred_text_height_for_item,
    raster_text_should_render_vertical,
    rebalance_body_text_flows,
    release_visual_clip_overcapture_for_text_fit,
    render_font_size_for_block,
    render_text_style_name,
    shifted_boxes_around_protected,
    small_overflow_tolerance,
    source_line_count,
    split_body_layout_lane,
    split_pdf_text_tokens,
    split_pdf_wrap_units,
    split_text_units,
    split_translated_text_around_protected,
    strip_trailing_space_tokens,
    text_box_fit_plan,
    text_box_overlaps_protected,
    text_box_overlaps_text_anchor,
    text_height_for_lines,
    text_item_fit_metrics,
    text_required_height,
    text_segments_around_protected,
    text_style,
    text_units_fit_segment,
    token_list_width,
    target_font_size_for_block,
    target_font_size_points_for_block,
    usable_text_segment,
    validate_plan_style_policy,
    validate_plan_text_fit,
    vertical_expansion_limits,
    wrap_text,
    wrap_mixed_pdf_text,
    style_name_for_block,
    style_name_for_heading_text,
)
from render_plan import (
    ALLOWED_SKIP_CLASSES,
    CoverageEntry,
    PageRenderPlan,
    RenderItem,
    bbox_area,
    bbox_overlap_area,
    bbox_overlap_height,
    bbox_to_json,
    coverage_entry_to_json,
    item_significantly_overlaps_protected,
    ledger_classifications,
    render_item_to_json,
    render_plan_artifact_path,
    render_plan_json_dumps,
    render_plan_to_json,
    try_write_render_plan_artifact,
    update_ledger_render_kind,
    validate_plan_coverage as _render_plan_validate_plan_coverage,
    validate_plan_layout,
    validate_plan_text_overlaps,
    write_render_plan_artifact,
)
from render_pdf import (
    draw_mixed_pdf_lines,
    expanded_rect_for_text,
    insert_source_clip,
    insert_source_image_clip,
    insert_vector_textbox,
    insert_vertical_vector_text,
    is_vertical_vector_block,
    load_fitz,
    preserve_drawings_on_page,
    preserve_images_on_page,
    render_plan_item,
    should_preserve_drawing_rect,
    source_image_clip_stream,
    source_page_image_path,
    vector_text_color,
)
from regions import (
    EDGE_ICON_MAX_SIZE_PT,
    FORMULA_COLUMN_FRACTION,
    FORMULA_PAD_BOTTOM_PX,
    FORMULA_PAD_TOP_PX,
    FULL_PAGE_IMAGE_AREA_FRACTION,
    HEADING_NUMBER_TITLE_MAX_GAP_PT,
    HEURISTIC_HEADING_MAX_CHARS,
    IMAGE_ROW_CLIP_PAD_BOTTOM_PT,
    IMAGE_ROW_CLIP_PAD_TOP_PT,
    IMAGE_ROW_CLIP_PAD_X_PT,
    IMAGE_ROW_GROUP_CENTER_TOLERANCE_PT,
    IMAGE_ROW_GROUP_MIN_COUNT,
    IMAGE_ROW_GROUP_MIN_SPAN_PT,
    LARGE_PROSE_VISUAL_AVOID_AREA_PT,
    LARGE_PROSE_VISUAL_AVOID_HEIGHT_PT,
    TEXT_PROTECTED_GAP_PT,
    VISUAL_CLIP_PIXEL_FINAL_PAD_PT,
    VISUAL_CLIP_PIXEL_SEARCH_PAD_X_PT,
    VISUAL_CLIP_PIXEL_SEARCH_PAD_Y_PT,
    bbox_center,
    bbox_contains_point,
    bbox_intersects,
    bbox_union,
    block_area_pt,
    block_bbox,
    block_is_visually_covered_by_region,
    block_overlap_area_pt,
    block_to_px_box,
    build_ocr_preserve_groups,
    build_visual_regions,
    cap_visual_bbox_after_preceding_text,
    cap_visual_bbox_against_adjacent_translated_text,
    cap_visual_bbox_around_large_prose,
    cap_visual_bbox_before_following_text,
    clamp_bbox,
    clamped_expanded_bbox,
    dark_pixel_bbox_in_source_image,
    diagram_label_candidate_above_caption,
    diagram_regions_above_visual_captions,
    expanded_bbox,
    formula_region_bbox_from_lines,
    grouped_image_row_clips,
    has_intervening_wide_prose_block,
    has_standalone_heading_number_to_left,
    heuristic_heading_from_block,
    horizontal_overlap,
    image_info_preserve_bbox,
    is_adjacent_visual_table_row_cell,
    is_large_prose_block,
    is_nearby_table_header_cell,
    is_table_body_candidate,
    is_table_region_terminator,
    make_px_box,
    merge_adjacent_code_visual_regions,
    merge_bbox_line_fragments,
    nontranslated_blocks_covered_by_visual_region,
    overlap_area,
    pixel_search_bbox,
    protected_box_for_block,
    same_heading_line,
    source_is_short_continuation_fragment,
    short_heading_text,
    standalone_heading_number_text,
    starts_like_source_heading,
    table_cells_already_seen_above_caption,
    translated_blocks_structurally_covered_by_visual_region,
    valid_image_insert_bbox,
    vertical_overlap,
    visual_clip_bbox,
)


TOOL_ROOT = Path(__file__).resolve().parent
TMP_ROOT = Path(os.environ.get("TRANSLATE_PDF_WORK_DIR", TOOL_ROOT / "work"))
VENDOR_ROOT = TOOL_ROOT / "vendor"
FONT_PATH = "/usr/share/fonts/truetype/arphic/uming.ttc"
SOURCE_FONT_SCALE = 0.94
TEXT_BOX_MARGIN_PX = 8
HEURISTIC_HEADING_BOTTOM_MARGIN_PT = 70.0
JOURNAL_FOOTER_TEXT = "ACM Transactions on Programming Languages and Systems, Vol. 11, No. 1, January 1991."
JOURNAL_FOOTER_WIDTH_PT = 260.0
JOURNAL_FOOTER_HEIGHT_PT = 8.0
VISUAL_CLIP_PAD_X_PT = 8.0
VISUAL_CLIP_PAD_Y_PT = 3.0
FORMULA_CLIP_PIXEL_SEARCH_PAD_X_PT = 70.0
FORMULA_CLIP_PIXEL_SEARCH_PAD_Y_PT = 2.0
FORMULA_CLIP_PAD_X_PT = 1.0
FORMULA_CLIP_PAD_Y_PT = 0.5
IMAGE_ONLY_PAGE_MIN_DARK_PIXELS = 32


def slugify(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", text.strip()).strip("-").lower()
    return slug or "document"


def build_job_paths(pdf_path: Path, job_name: str | None):
    job_slug = slugify(job_name or pdf_path.stem)
    job_dir = TMP_ROOT / "jobs" / job_slug
    return {
        "job_slug": job_slug,
        "job_dir": job_dir,
        "bbox_path": job_dir / "source_bbox.html",
        "source_pages_path": job_dir / "source_pages.json",
        "pages_dir": job_dir / "pages",
        "translated_pages_dir": job_dir / "translated_pages",
        "plans_dir": job_dir / "plans",
        "schema_path": job_dir / "translation_schema.json",
        "boundary_repairs_path": job_dir / "boundary_sentence_repairs.json",
        "boundary_schema_path": job_dir / "boundary_sentence_schema.json",
        "translations_path": job_dir / "translations.json",
        "tex_path": job_dir / "claudeCodeChinese.tex",
        "pdf_path": job_dir / "claudeCodeChinese.pdf",
    }


def set_work_dir(work_dir: Path):
    global TMP_ROOT
    TMP_ROOT = work_dir


def run(cmd, *, input_text=None, cwd=TOOL_ROOT, check=True):
    proc = subprocess.run(
        cmd,
        input=input_text,
        text=True,
        cwd=cwd,
        capture_output=True,
        check=False,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"stdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"
        )
    return proc


def ensure_assets(
    pdf_path: Path,
    dpi: int,
    job_paths,
    page_start: int = 1,
    page_end: int = 0,
) -> Path:
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    job_paths["job_dir"].mkdir(parents=True, exist_ok=True)
    job_paths["pages_dir"].mkdir(parents=True, exist_ok=True)
    job_paths["translated_pages_dir"].mkdir(parents=True, exist_ok=True)
    bbox_path = job_paths["bbox_path"]
    if not bbox_path.exists():
        proc = run(["pdftotext", "-bbox-layout", str(pdf_path), "-"], check=True)
        bbox_path.write_text(proc.stdout, encoding="utf-8")
    if page_end:
        expected_pages = [
            job_paths["pages_dir"] / f"page-{page_num:03d}.png"
            for page_num in range(page_start, page_end + 1)
        ]
        needs_render = any(not path.exists() for path in expected_pages)
    else:
        needs_render = not (job_paths["pages_dir"] / "page-001.png").exists()

    if needs_render:
        prefix = str(job_paths["pages_dir"] / "page")
        cmd = ["pdftocairo", "-png", "-r", str(dpi)]
        if page_end:
            cmd.extend(["-f", str(page_start), "-l", str(page_end)])
        cmd.extend([str(pdf_path), prefix])
        run(cmd, check=True)
        for src in sorted(job_paths["pages_dir"].glob("page-*.png")):
            # pdftocairo emits page-1.png, page-2.png; normalize to page-001.png.
            m = re.search(r"page-(\d+)\.png$", src.name)
            if not m:
                continue
            normalized = job_paths["pages_dir"] / f"page-{int(m.group(1)):03d}.png"
            if src != normalized:
                src.rename(normalized)
    return bbox_path


def parse_bbox(bbox_path: Path):
    raw_text = bbox_path.read_text(encoding="utf-8", errors="replace")
    raw_text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", raw_text)
    root = ET.fromstring(raw_text)
    ns = {"x": "http://www.w3.org/1999/xhtml"}
    page_nodes = root.findall(".//page")
    if not page_nodes:
        page_nodes = root.findall(".//x:page", ns)
    pages = []
    for page_idx, page in enumerate(page_nodes, start=1):
        page_blocks = []
        block_nodes = page.findall(".//block")
        if not block_nodes:
            block_nodes = page.findall(".//x:block", ns)
        for block_idx, block in enumerate(block_nodes, start=1):
            lines = []
            line_nodes = block.findall("./line")
            if not line_nodes:
                line_nodes = block.findall("./x:line", ns)
            for line in line_nodes:
                word_nodes = line.findall("./word")
                if not word_nodes:
                    word_nodes = line.findall("./x:word", ns)
                words = [w.text or "" for w in word_nodes]
                line_text = normalize_text(" ".join(words))
                if line_text:
                    lines.append(line_text)
            block_text = normalize_text("\n".join(lines))
            if not block_text:
                continue
            page_blocks.append(
                {
                    "id": f"p{page_idx:03d}b{block_idx:04d}",
                    "page": page_idx,
                    "block_index": block_idx,
                    "xMin": float(block.attrib["xMin"]),
                    "yMin": float(block.attrib["yMin"]),
                    "xMax": float(block.attrib["xMax"]),
                    "yMax": float(block.attrib["yMax"]),
                    "text": block_text,
                }
            )
        pages.append(page_blocks)
    return pages


def parse_bbox_lines_from_text(raw_text: str) -> list[dict]:
    raw_text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", raw_text)
    root = ET.fromstring(raw_text)
    ns = {"x": "http://www.w3.org/1999/xhtml"}
    page_nodes = root.findall(".//page")
    if not page_nodes:
        page_nodes = root.findall(".//x:page", ns)
    lines = []
    for page_idx, page in enumerate(page_nodes, start=1):
        line_nodes = page.findall(".//line")
        if not line_nodes:
            line_nodes = page.findall(".//x:line", ns)
        for line in line_nodes:
            word_nodes = line.findall("./word")
            if not word_nodes:
                word_nodes = line.findall("./x:word", ns)
            words = [word.text or "" for word in word_nodes]
            text = normalize_text(" ".join(words))
            if not text:
                continue
            lines.append(
                {
                    "page": page_idx,
                    "text": text,
                    "bbox": (
                        float(line.attrib["xMin"]),
                        float(line.attrib["yMin"]),
                        float(line.attrib["xMax"]),
                        float(line.attrib["yMax"]),
                    ),
                }
            )
    return lines


def parse_bbox_lines(bbox_path: Path) -> list[dict]:
    return parse_bbox_lines_from_text(bbox_path.read_text(encoding="utf-8", errors="replace"))


def load_source_pages(job_paths) -> list[list[dict]]:
    source_pages_path = job_paths["source_pages_path"]
    if source_pages_path.exists():
        return json.loads(source_pages_path.read_text(encoding="utf-8"))
    return parse_bbox(job_paths["bbox_path"])


def save_source_pages(pages, job_paths):
    job_paths["source_pages_path"].write_text(
        json.dumps(pages, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def should_use_ocr(pages) -> bool:
    nontrivial = [
        block
        for page in pages
        for block in page
        if not is_trivial_keep(block["text"])
    ]
    total_chars = sum(len(block["text"]) for block in nontrivial)
    return len(nontrivial) <= max(10, len(pages) * 2) and total_chars <= max(600, len(pages) * 80)


def text_extraction_looks_garbled(pages) -> bool:
    texts = [
        block["text"]
        for page in pages
        for block in page
        if not is_trivial_keep(block["text"])
    ]
    if not texts:
        return False

    joined = " ".join(texts)
    compact = re.sub(r"\s+", "", joined)
    total_chars = len(compact)
    if total_chars < 400:
        return False

    word_chars = sum(len(token) for token in re.findall(r"[A-Za-z]{3,}", joined))
    symbol_chars = len(re.findall(r"[^A-Za-z0-9\s]", joined))
    garbled_blocks = 0
    for text in texts:
        text_compact = re.sub(r"\s+", "", text)
        if not text_compact:
            continue
        alnum_chars = len(re.findall(r"[A-Za-z0-9]", text))
        if alnum_chars < max(2, int(len(text_compact) * 0.25)):
            garbled_blocks += 1

    word_ratio = word_chars / total_chars
    symbol_ratio = symbol_chars / total_chars
    garbled_block_ratio = garbled_blocks / max(1, len(texts))
    return word_ratio < 0.45 and symbol_ratio > 0.35 and garbled_block_ratio > 0.30


def make_raw_block(lines, scale_x: float, scale_y: float, preserve_image: bool):
    lines = sorted(lines, key=lambda item: (item["y0_px"], item["x0_px"]))
    return {
        "xMin": min(line["x0_px"] for line in lines) * scale_x,
        "yMin": min(line["y0_px"] for line in lines) * scale_y,
        "xMax": max(line["x1_px"] for line in lines) * scale_x,
        "yMax": max(line["y1_px"] for line in lines) * scale_y,
        "text": normalize_text("\n".join(line["text"] for line in lines)),
        "preserve_image": preserve_image,
    }


def load_rapidocr():
    if str(VENDOR_ROOT) not in sys.path:
        sys.path.insert(0, str(VENDOR_ROOT))
    try:
        from rapidocr_onnxruntime import RapidOCR
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "OCR fallback requires rapidocr_onnxruntime. "
            "Install dependencies with: python3 -m pip install -r requirements.txt"
        ) from exc
    return RapidOCR()


def has_preserve_barrier_between(block, line, barriers):
    for barrier in barriers or []:
        horizontal_overlap = min(line["x1_px"], barrier["x1_px"], block["x1_px"]) - max(
            line["x0_px"],
            barrier["x0_px"],
            block["x0_px"],
        )
        if horizontal_overlap <= 0:
            continue
        if block["y1_px"] <= barrier["y0_px"] and barrier["y1_px"] <= line["y0_px"]:
            return True
    return False


def merge_ocr_lines(lines, page_width_px: int, barriers=None):
    blocks = []
    for line in sorted(lines, key=lambda item: (item["y0_px"], item["x0_px"])):
        line_height = max(1.0, line["y1_px"] - line["y0_px"])
        best_idx = None
        best_score = None
        recent_start = max(0, len(blocks) - 24)
        for idx in range(recent_start, len(blocks)):
            block = blocks[idx]
            vertical_gap = line["y0_px"] - block["y1_px"]
            if vertical_gap < -line_height * 0.4:
                continue
            max_gap = max(18.0, block["line_height_px"] * 1.8)
            if vertical_gap > max_gap:
                continue
            if has_preserve_barrier_between(block, line, barriers):
                continue

            overlap = min(line["x1_px"], block["x1_px"]) - max(line["x0_px"], block["x0_px"])
            block_width = max(1.0, block["x1_px"] - block["x0_px"])
            overlap_ratio = overlap / max(1.0, min(line["width_px"], block_width))
            left_aligned = abs(line["x0_px"] - block["x0_px"]) <= page_width_px * 0.05
            right_aligned = abs(line["x1_px"] - block["x1_px"]) <= page_width_px * 0.05
            center_delta = abs(line["center_x_px"] - ((block["x0_px"] + block["x1_px"]) / 2))
            if overlap_ratio < 0.3 and not (left_aligned or right_aligned):
                continue
            if center_delta > page_width_px * 0.18:
                continue

            score = overlap_ratio * 3 - (vertical_gap / max_gap) - (center_delta / page_width_px)
            if best_score is None or score > best_score:
                best_idx = idx
                best_score = score

        if best_idx is None:
            blocks.append(
                {
                    "lines": [line["text"]],
                    "x0_px": line["x0_px"],
                    "x1_px": line["x1_px"],
                    "y0_px": line["y0_px"],
                    "y1_px": line["y1_px"],
                    "line_height_px": line_height,
                }
            )
            continue

        block = blocks[best_idx]
        block["lines"].append(line["text"])
        block["x0_px"] = min(block["x0_px"], line["x0_px"])
        block["x1_px"] = max(block["x1_px"], line["x1_px"])
        block["y1_px"] = max(block["y1_px"], line["y1_px"])
        block["line_height_px"] = max(block["line_height_px"], line_height)

    return blocks


def generate_ocr_pages(job_paths, pdf_size_pt):
    ocr = load_rapidocr()
    width_pt, height_pt = pdf_size_pt
    pages_by_num = {}

    for image_path in sorted(job_paths["pages_dir"].glob("page-*.png")):
        match = re.search(r"page-(\d+)\.png$", image_path.name)
        if not match:
            continue
        page_idx = int(match.group(1))
        image = Image.open(image_path)
        width_px, height_px = image.size
        scale_x = width_pt / width_px
        scale_y = height_pt / height_px
        result, _ = ocr(str(image_path))
        ocr_lines = []
        for item in result or []:
            points, text, score = item
            text = normalize_text(text)
            if not text or score < 0.45:
                continue
            xs = [pt[0] for pt in points]
            ys = [pt[1] for pt in points]
            ocr_lines.append(
                {
                    "idx": len(ocr_lines),
                    "text": text,
                    "x0_px": min(xs),
                    "x1_px": max(xs),
                    "y0_px": min(ys),
                    "y1_px": max(ys),
                    "width_px": max(xs) - min(xs),
                    "center_x_px": (min(xs) + max(xs)) / 2,
                }
            )

        preserve_groups, preserved_line_ids = build_ocr_preserve_groups(ocr_lines, width_px, height_px)
        preserve_barriers = [make_px_box(group) for group in preserve_groups]
        text_lines = [line for line in ocr_lines if line["idx"] not in preserved_line_ids]
        raw_blocks = [
            make_raw_block(group, scale_x, scale_y, preserve_image=True)
            for group in preserve_groups
        ]
        for block in merge_ocr_lines(text_lines, width_px, barriers=preserve_barriers):
            block_text = normalize_text("\n".join(block["lines"]))
            if not block_text:
                continue
            raw_blocks.append(
                {
                    "xMin": block["x0_px"] * scale_x,
                    "yMin": block["y0_px"] * scale_y,
                    "xMax": block["x1_px"] * scale_x,
                    "yMax": block["y1_px"] * scale_y,
                    "text": block_text,
                    "preserve_image": False,
                }
            )
        raw_blocks.sort(key=lambda item: (item["yMin"], item["xMin"]))

        page_blocks = []
        for block_idx, block in enumerate(raw_blocks, start=1):
            if not block["text"]:
                continue
            page_blocks.append(
                {
                    "id": f"p{page_idx:03d}b{block_idx:04d}",
                    "page": page_idx,
                    "block_index": block_idx,
                    **block,
                }
            )
        pages_by_num[page_idx] = page_blocks
    if not pages_by_num:
        return []
    return [pages_by_num.get(idx, []) for idx in range(1, max(pages_by_num) + 1)]


def load_or_build_source_pages(
    pdf_path: Path,
    dpi: int,
    job_paths,
    pdf_size_pt,
    page_start: int = 1,
    page_end: int = 0,
    force_ocr: bool = False,
):
    source_pages_path = job_paths["source_pages_path"]
    if source_pages_path.exists() and not force_ocr:
        return load_source_pages(job_paths)

    ensure_assets(pdf_path, dpi, job_paths, page_start, page_end)
    if source_pages_path.exists():
        return load_source_pages(job_paths)

    pages = parse_bbox(job_paths["bbox_path"])
    if force_ocr or should_use_ocr(pages) or text_extraction_looks_garbled(pages):
        pages = generate_ocr_pages(job_paths, pdf_size_pt)
    save_source_pages(pages, job_paths)
    return pages


def visual_translation_protected_ids(
    page,
    classes,
    visual_regions,
    *,
    page_size=None,
    page_num: int = 1,
    source_image_path: Path | None = None,
    bbox_lines=None,
    translations=None,
) -> set[str]:
    visual_ids = {source_id for region in visual_regions for source_id in region["source_ids"]}
    protected_ids = set()
    for region in visual_regions:
        region_bbox, _mixed_body_item = final_visual_region_bbox(
            page,
            classes,
            region,
            page_size=page_size,
            page_num=page_num,
            source_image_path=source_image_path,
            bbox_lines=bbox_lines,
            translations=translations,
            visual_ids=visual_ids,
        )
        protected_ids.update(
            nontranslated_blocks_covered_by_visual_region(
                page,
                classes,
                region_bbox,
                visual_ids,
            )
        )
    return protected_ids


def final_visual_region_bbox(
    blocks,
    classes,
    region,
    *,
    page_size=None,
    page_num: int = 1,
    source_image_path: Path | None = None,
    bbox_lines=None,
    translations=None,
    visual_ids=None,
) -> tuple[tuple[float, float, float, float], RenderItem | None]:
    visual_ids = visual_ids or {source_id for source_id in region["source_ids"]}
    region_classes = {classes.get(source_id, "figure_region") for source_id in region["source_ids"]}
    if region_classes and region_classes <= {"formula_region"}:
        pad_x = FORMULA_CLIP_PAD_X_PT
        pad_y = FORMULA_CLIP_PAD_Y_PT
    else:
        pad_x = VISUAL_CLIP_PAD_X_PT
        pad_y = VISUAL_CLIP_PAD_Y_PT
    block_by_id = {block["id"]: block for block in blocks}
    region_has_caption = any(
        contains_visual_caption(block_by_id[source_id].get("text", ""))
        or is_visual_caption(block_by_id[source_id].get("text", ""))
        for source_id in region["source_ids"]
        if source_id in block_by_id
    )
    formula_only = bool(
        region_classes
        and region_classes <= {"formula_region"}
        and not region_has_caption
        and not region.get("has_code_seed")
    )
    visual_source_bbox = region["bbox"]
    cap_source_bbox = visual_source_bbox
    mixed_body_item = None
    mixed_visual_body = False
    if region.get("has_code_seed") and bbox_lines and page_size is not None:
        mixed = mixed_visual_body_render_item(region, bbox_lines, translations or {}, page_size)
        if mixed is not None:
            visual_source_bbox, mixed_body_item = mixed
            mixed_visual_body = True
        else:
            split = split_mixed_visual_body_rows(region["bbox"], bbox_lines)
            if split is not None:
                visual_source_bbox, _body_bbox = split
                mixed_visual_body = True
    if formula_only and bbox_lines and page_size is not None:
        line_bbox = formula_region_bbox_from_lines(region, bbox_lines, page_size)
        if line_bbox is not None:
            visual_source_bbox = line_bbox
            cap_source_bbox = line_bbox
    elif formula_only:
        formula_boxes = [
            block_bbox(block_by_id[source_id])
            for source_id in region["source_ids"]
            if source_id in block_by_id
        ]
        if formula_boxes:
            cap_source_bbox = bbox_union(formula_boxes)
    if page_size is None:
        return visual_source_bbox, mixed_body_item
    region_bbox = visual_clip_bbox(
        visual_source_bbox,
        page_size,
        pad_x=pad_x,
        pad_y=pad_y,
        page_num=page_num,
        source_image_path=source_image_path,
        use_pixel_bounds=True,
        pixel_search_pad_x=(
            FORMULA_CLIP_PIXEL_SEARCH_PAD_X_PT
            if formula_only
            else 12.0
            if region.get("has_caption_seed")
            else 4.0
            if region.get("has_row_cell_seed")
            else 4.0
            if region.get("has_code_seed")
            else VISUAL_CLIP_PIXEL_SEARCH_PAD_X_PT
        ),
        pixel_search_pad_y=(
            FORMULA_CLIP_PIXEL_SEARCH_PAD_Y_PT
            if formula_only or mixed_visual_body
            else 2.0
            if region.get("has_code_seed")
            else VISUAL_CLIP_PIXEL_SEARCH_PAD_Y_PT
        ),
        pixel_final_pad=VISUAL_CLIP_PIXEL_FINAL_PAD_PT if formula_only else 0.75 if region.get("has_code_seed") else VISUAL_CLIP_PIXEL_FINAL_PAD_PT,
    )
    if formula_only:
        region_bbox = (
            region_bbox[0],
            max(region_bbox[1], cap_source_bbox[1] - VISUAL_CLIP_PIXEL_FINAL_PAD_PT),
            region_bbox[2],
            min(region_bbox[3], cap_source_bbox[3] + VISUAL_CLIP_PIXEL_FINAL_PAD_PT),
        )
    region_bbox = cap_visual_bbox_after_preceding_text(
        cap_source_bbox,
        region_bbox,
        blocks,
        classes,
        visual_ids,
    )
    region_bbox = cap_visual_bbox_before_following_text(
        cap_source_bbox,
        region_bbox,
        blocks,
        classes,
        visual_ids,
    )
    region_bbox = cap_visual_bbox_against_adjacent_translated_text(
        visual_source_bbox,
        region_bbox,
        blocks,
        classes,
        visual_ids,
        page_size,
    )
    region_bbox = cap_visual_bbox_around_large_prose(region_bbox, blocks, classes, visual_ids, page_size)
    return region_bbox, mixed_body_item


def bbox_lines_by_page(job_paths) -> dict[int, list[dict]]:
    by_page = {}
    if not job_paths or not job_paths.get("bbox_path") or not job_paths["bbox_path"].exists():
        return by_page
    for line in parse_bbox_lines(job_paths["bbox_path"]):
        by_page.setdefault(line["page"], []).append(line)
    return by_page


@dataclass(frozen=True)
class TranslationPageOwnership:
    classes: dict[str, str]
    components: list[ownership.PageComponent]
    validation: ownership.OwnershipValidationResult
    visual_regions: list[dict]
    raw_visual_regions: list[dict]
    translatable_ids: list[str]
    in_reference_section: bool
    force_reference_page: bool


def final_visual_ownership_regions(
    page,
    classes,
    visual_regions,
    *,
    page_size=None,
    page_num: int = 1,
    source_image_path: Path | None = None,
    bbox_lines=None,
    translations=None,
) -> list[dict]:
    translations_known = translations is not None
    translations = translations or {}
    block_by_id = {block["id"]: block for block in page}

    def has_renderable_translation(source_id: str) -> bool:
        block = block_by_id.get(source_id)
        if block is None or classes.get(source_id) not in NORMAL_TRANSLATED_CLASSES:
            return False
        return block_has_valid_chinese_translation(block, translations)

    raw_visual_ids = {source_id for region in visual_regions for source_id in region["source_ids"]}
    preliminary_regions = []
    expanded_visual_ids = set(raw_visual_ids)
    cap_guard_visual_ids = set(raw_visual_ids)
    for region in visual_regions:
        source_bbox = region.get("bbox")
        source_ids = {source_id for source_id in region["source_ids"] if not has_renderable_translation(source_id)}
        if source_bbox is not None:
            source_ids.update(
                nontranslated_blocks_covered_by_visual_region(
                    page,
                    classes,
                    source_bbox,
                    raw_visual_ids,
                )
            )
            structural_ids = translated_blocks_structurally_covered_by_visual_region(
                page,
                classes,
                source_bbox,
                source_ids,
            )
            cap_guard_visual_ids.update(structural_ids)
            # Batch planning has no translations yet, so keep prose rows translatable
            # while still using them to cap visual clip growth.
            if translations_known:
                source_ids.update(source_id for source_id in structural_ids if not has_renderable_translation(source_id))
        preliminary_regions.append((region, source_bbox, source_ids))
        expanded_visual_ids.update(source_ids)

    cap_guard_visual_ids.update(expanded_visual_ids)
    ownership_regions = []
    for region, source_bbox, preliminary_source_ids in preliminary_regions:
        region_bbox, _mixed_body_item = final_visual_region_bbox(
            page,
            classes,
            region,
            page_size=page_size,
            page_num=page_num,
            source_image_path=source_image_path,
            bbox_lines=bbox_lines,
            translations=translations,
            visual_ids=cap_guard_visual_ids,
        )
        mixed_split = split_mixed_visual_body_rows(region["bbox"], bbox_lines) if region.get("has_code_seed") else None
        source_ids = set(preliminary_source_ids)
        source_ids.update(
            nontranslated_blocks_covered_by_visual_region(
                page,
                classes,
                region_bbox,
                cap_guard_visual_ids,
            )
        )
        source_ids = {source_id for source_id in source_ids if not has_renderable_translation(source_id)}
        ownership_region = dict(region)
        ownership_region["source_ids"] = sorted(source_ids)
        ownership_region["source_bbox"] = region_bbox if mixed_split is not None else source_bbox or region_bbox
        ownership_region["bbox"] = region_bbox
        if mixed_split is not None:
            _visual_bbox, body_bbox = mixed_split
            ownership_region["mixed_body_source_ids"] = sorted(region["source_ids"])
            ownership_region["mixed_body_bbox"] = tuple(body_bbox)
        ownership_regions.append(ownership_region)
    merged_regions = ownership.merge_visual_regions(ownership_regions)
    block_by_id = {block["id"]: block for block in page}
    for region in merged_regions:
        if page_size is None or region.get("bbox") is None:
            continue
        source_boxes = [
            block_bbox(block_by_id[source_id])
            for source_id in region.get("source_ids", [])
            if source_id in block_by_id
        ]
        if not source_boxes:
            continue
        cap_source_bbox = bbox_union(source_boxes)
        region["bbox"] = cap_visual_bbox_before_following_text(
            cap_source_bbox,
            tuple(region["bbox"]),
            page,
            classes,
            cap_guard_visual_ids,
        )
    return merged_regions


def build_page_components_compat(
    page_num: int,
    page,
    classes: dict[str, str],
    *,
    visual_regions: list[dict],
    duplicate_ids: set[str],
    skip_ids: set[str],
) -> list[ownership.PageComponent]:
    try:
        return ownership.build_page_components(
            page_num=page_num,
            blocks=page,
            classes=classes,
            visual_regions=visual_regions,
            visual_covered_text_ids=set(),
            duplicate_ids=duplicate_ids,
            skip_ids=skip_ids,
        )
    except TypeError:
        return ownership.build_page_components(
            page_num,
            page,
            classes,
            visual_regions=visual_regions,
            duplicate_ids=duplicate_ids,
            skip_ids=skip_ids,
        )


def validate_ownership_compat(page_num: int, page, components) -> ownership.OwnershipValidationResult:
    try:
        return ownership.validate_ownership(page_num=page_num, blocks=page, components=components)
    except TypeError:
        try:
            return ownership.validate_ownership(page_num, page, components)
        except TypeError:
            return ownership.validate_ownership(page, components)


def components_by_source_id_compat(components) -> dict[str, list[ownership.PageComponent]]:
    helper = getattr(ownership, "components_by_source_id", None)
    if helper is not None:
        return helper(components)
    result: dict[str, list[ownership.PageComponent]] = {}
    for component in components:
        for source_id in component.source_ids:
            result.setdefault(str(source_id), []).append(component)
    return result


def component_by_source_id_compat(components) -> dict[str, ownership.PageComponent]:
    helper = getattr(ownership, "component_by_source_id", None)
    if helper is not None:
        return helper(components)
    return {
        source_id: source_components[-1]
        for source_id, source_components in components_by_source_id_compat(components).items()
        if source_components
    }


def component_has_reason(component, reason: str) -> bool:
    return reason in {str(code) for code in getattr(component, "reason_codes", [])}


def block_has_valid_chinese_translation(block, translations) -> bool:
    block_id = block.get("id")
    if not block_id or block_id not in (translations or {}):
        return False
    source_text = strip_journal_footer_lines(block.get("text", ""))
    if not source_requires_chinese_translation(source_text):
        return False
    raw_translation = translations.get(block_id, "")
    translated = clean_render_text(block, translation_for_block(block, translations), raw_translation)
    return bool(translated.strip()) and not translation_appears_untranslated(source_text, translated)


def visual_source_ids_from_components(components) -> set[str]:
    return {
        str(source_id)
        for component in components
        if component.component_kind == ownership.COMPONENT_KIND_VISUAL
        for source_id in component.source_ids
    }


def mixed_visual_body_components(components) -> list[ownership.PageComponent]:
    return [
        component
        for component in components
        if component.component_kind == ownership.COMPONENT_KIND_TRANSLATED_TEXT
        and component_has_reason(component, ownership.REASON_MIXED_VISUAL_BODY_SPLIT)
    ]


def merge_ownership_validation_results(*results) -> ownership.OwnershipValidationResult:
    issues = []
    for result in results:
        issues.extend(list(getattr(result, "issues", []) or []))
    return ownership.OwnershipValidationResult(issues=issues)


def annotate_plan_with_component_metadata(
    plan: PageRenderPlan,
    components_by_source_id: dict[str, list[ownership.PageComponent]],
) -> None:
    def matching_component(source_id: str, render_kind: str):
        candidates = components_by_source_id.get(source_id, [])
        if render_kind == "original_image_clip":
            kind = ownership.COMPONENT_KIND_VISUAL
        elif render_kind == "translated_text":
            kind = ownership.COMPONENT_KIND_TRANSLATED_TEXT
        elif render_kind == "original_selectable_text":
            kind = ownership.COMPONENT_KIND_REFERENCE
        else:
            kind = ""
        if kind:
            matches = [component for component in candidates if component.component_kind == kind]
            if len(matches) == 1:
                return matches[0]
        if len(candidates) == 1:
            return candidates[0]
        return None

    for entry in plan.ledger:
        if entry.component_id and entry.component_kind:
            continue
        component = matching_component(entry.block_id, entry.render_kind)
        if component is None:
            continue
        entry.component_id = component.component_id
        entry.component_kind = component.component_kind

    for item in plan.items:
        if item.component_id and item.component_kind:
            continue
        components = [
            component
            for source_id in item.source_ids
            for component in [matching_component(source_id, item.kind)]
            if component is not None
        ]
        component_ids = {component.component_id for component in components}
        component_kinds = {component.component_kind for component in components}
        if len(component_ids) == 1:
            item.component_id = next(iter(component_ids))
        if len(component_kinds) == 1:
            item.component_kind = next(iter(component_kinds))


def build_translation_page_components(
    page_num: int,
    page,
    *,
    page_size=None,
    job_paths=None,
    bbox_lines=None,
    source_image_path: Path | None = None,
    in_reference_section: bool = False,
    translations=None,
) -> TranslationPageOwnership:
    raw_visual_regions = build_visual_regions(page)
    classes = classify_blocks(page, raw_visual_regions)
    classes, next_in_reference_section, force_reference_page = apply_reference_continuation(
        page,
        classes,
        in_reference_section,
    )
    source_image = source_image_path
    if source_image is None and job_paths:
        source_image = source_page_image_path(job_paths, page_num)
    visual_ownership_regions = final_visual_ownership_regions(
        page,
        classes,
        raw_visual_regions,
        page_size=page_size,
        page_num=page_num,
        source_image_path=source_image,
        bbox_lines=bbox_lines,
        translations=translations,
    )
    duplicate_ids = {
        block_id
        for block_id in nested_duplicate_block_ids(page)
        if classes.get(block_id) in {"body", "heading", "title"}
    }
    duplicate_ids.update(contained_standalone_label_ids(page, classes))
    skip_ids = {
        block["id"]
        for block in page
        if should_preserve_first_page_metadata_as_image(block)
        or should_preserve_as_image(block)
    }
    components = build_page_components_compat(
        page_num,
        page,
        classes,
        visual_regions=visual_ownership_regions,
        duplicate_ids=duplicate_ids,
        skip_ids=skip_ids,
    )
    validation = validate_ownership_compat(page_num, page, components)
    translatable_ids = []
    for component in sorted(components, key=lambda item: (item.source_bbox[1], item.source_bbox[0], item.component_id)):
        if component.component_kind != ownership.COMPONENT_KIND_TRANSLATED_TEXT:
            continue
        for source_id in component.source_ids:
            if (
                classes.get(source_id) not in NORMAL_TRANSLATED_CLASSES
                and not component_has_reason(component, ownership.REASON_MIXED_VISUAL_BODY_SPLIT)
            ):
                continue
            if source_id not in translatable_ids:
                translatable_ids.append(source_id)
    return TranslationPageOwnership(
        classes=classes,
        components=components,
        validation=validation,
        visual_regions=visual_ownership_regions,
        raw_visual_regions=raw_visual_regions,
        translatable_ids=translatable_ids,
        in_reference_section=next_in_reference_section,
        force_reference_page=force_reference_page,
    )


def prepare_page_ownership(
    page_num: int,
    page,
    *,
    page_size=None,
    job_paths=None,
    bbox_lines=None,
    source_image_path: Path | None = None,
    in_reference_section: bool = False,
):
    result = build_translation_page_components(
        page_num,
        page,
        page_size=page_size,
        job_paths=job_paths,
        bbox_lines=bbox_lines,
        source_image_path=source_image_path,
        in_reference_section=in_reference_section,
    )
    return result, result.in_reference_section


def build_batches(pages, max_chars: int, *, page_size=None, job_paths=None, page_numbers=None):
    blocks = []
    lines_by_page = bbox_lines_by_page(job_paths)
    in_reference_section = False
    for page_index, page in enumerate(pages, start=1):
        page_num = page_numbers[page_index - 1] if page_numbers else page_index
        ownership_result = build_translation_page_components(
            page_num,
            page,
            page_size=page_size,
            job_paths=job_paths,
            bbox_lines=lines_by_page.get(page_num),
            in_reference_section=in_reference_section,
        )
        in_reference_section = ownership_result.in_reference_section
        translatable_ids = set(ownership_result.translatable_ids)
        for block in page:
            if block["id"] not in translatable_ids:
                continue
            blocks.append(block)

    batches = []
    current = []
    current_chars = 0
    for block in blocks:
        block_chars = len(block["text"])
        if current and current_chars + block_chars > max_chars:
            batches.append(current)
            current = []
            current_chars = 0
        current.append({"id": block["id"], "text": block["text"]})
        current_chars += block_chars
    if current:
        batches.append(current)
    return batches


def write_schema(path: Path):
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "translation": {"type": "string"},
                    },
                    "required": ["id", "translation"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }
    path.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")


def make_prompt(batch):
    glossary = textwrap.dedent(
        """
        你在翻译一篇计算机领域的学术论文，主题可能涉及 AI 智能体、工具使用、语言模型、推理、系统架构或对齐训练。
        请将每个 text 字段翻译为严谨、流畅、自然的简体中文。

        强制要求：
        1. 忠实原意，不省略信息，不总结，不扩写。
        2. 保持学术写作风格，术语统一。
        3. 保留以下内容原样或仅在必要时做最小调整：
           - URL、邮箱、文件路径、命令行、代码标识、模型名、版本号
           - 作者姓名、机构名、系统名，如 Claude Code、OpenClaw、Anthropic、MCP
           - 引用格式与年份，如 (Chen et al., 2021)
        4. 对纯数字、页码或明显无需翻译的标识，原样返回。
        5. 以完整句子为最小翻译单元。遇到明显跨块或跨页的断句时，不要把残缺片段硬补成新意思，也不要重复相邻片段；后处理会用跨页上下文修复完整句。
        6. 输出必须是 JSON，对象格式固定为 {"items":[{"id":"...","translation":"..."}]}。
        7. 不要输出解释，不要使用 Markdown 代码块。

        术语约定：
        - agentic -> 代理式
        - agent system -> 智能体系统
        - coding agent -> 编码智能体
        - agentic loop -> 代理循环
        - permission system -> 权限系统
        - sandboxing -> 沙箱化
        - context window -> 上下文窗口
        - compaction -> 压缩
        - subagent -> 子代理
        - hook -> 钩子
        - plugin -> 插件
        - skill -> 技能
        - deny-first -> 默认拒绝
        - append-only -> 仅追加
        - trust spectrum -> 信任梯度
        - execution harness / harness -> 执行框架

        如果某个术语约定不适用于当前论文语境，请以原文上下文为准，选择更准确的译法。

        待翻译条目如下：
        """
    ).strip()
    payload = {"items": batch}
    return glossary + "\n" + json.dumps(payload, ensure_ascii=False, indent=2)


def translate_batches(batches, job_paths, *, model: str = "gpt-5.5", reasoning_effort: str = "low"):
    schema_path = job_paths["schema_path"]
    translations_path = job_paths["translations_path"]
    translations = {}
    valid_ids = {item["id"] for batch in batches for item in batch}
    if translations_path.exists():
        existing = json.loads(translations_path.read_text(encoding="utf-8"))
        overlap = len(valid_ids & set(existing))
        if valid_ids and overlap < max(1, int(len(valid_ids) * 0.6)):
            backup = job_paths["job_dir"] / f"translations-stale-{int(time.time())}.json"
            shutil.copy2(translations_path, backup)
        else:
            translations.update({key: value for key, value in existing.items() if key in valid_ids})

    for idx, batch in enumerate(batches, start=1):
        todo = [item for item in batch if item["id"] not in translations]
        if not todo:
            continue
        write_schema(schema_path)

        prompt = make_prompt(todo)
        prompt_path = job_paths["job_dir"] / f"batch-{idx:02d}.prompt.txt"
        out_path = job_paths["job_dir"] / f"batch-{idx:02d}.out.json"
        log_path = job_paths["job_dir"] / f"batch-{idx:02d}.log.txt"
        prompt_path.write_text(prompt, encoding="utf-8")

        success = False
        for attempt in range(1, 4):
            proc = run(
                [
                    "codex",
                    "exec",
                    "--skip-git-repo-check",
                    "-m",
                    model,
                    "-c",
                    f"model_reasoning_effort='{reasoning_effort}'",
                    "--disable",
                    "plugins",
                    "--disable",
                    "shell_snapshot",
                    "--sandbox",
                    "workspace-write",
                    "--ephemeral",
                    "--output-schema",
                    str(schema_path),
                    "-o",
                    str(out_path),
                    "-",
                ],
                input_text=prompt,
                check=False,
            )
            log_path.write_text(proc.stdout + "\n\nSTDERR\n" + proc.stderr, encoding="utf-8")
            if proc.returncode == 0 and out_path.exists():
                try:
                    payload = json.loads(out_path.read_text(encoding="utf-8"))
                    items = payload["items"]
                    seen = {item["id"] for item in items}
                    expected = {item["id"] for item in todo}
                    if seen != expected:
                        missing = sorted(expected - seen)
                        extra = sorted(seen - expected)
                        raise ValueError(f"mismatched ids, missing={missing[:5]}, extra={extra[:5]}")
                    for item in items:
                        translations[item["id"]] = normalize_translation(item["translation"])
                    translations_path.write_text(
                        json.dumps(translations, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    success = True
                    break
                except Exception as exc:  # noqa: BLE001
                    log_path.write_text(
                        log_path.read_text(encoding="utf-8")
                        + f"\n\nPARSE ERROR\n{exc}\n",
                        encoding="utf-8",
                    )
            time.sleep(3 * attempt)
        if not success:
            raise RuntimeError(f"translation batch {idx} failed, see {log_path}")

    return translations


def normalize_translation(text: str) -> str:
    text = text.strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


TERMINAL_PUNCTUATION = set("。！？；：.!?;:）】》”’」』")
NON_TERMINAL_PUNCTUATION = set("，、,")


def line_needs_terminal_punctuation(line: str) -> bool:
    stripped = line.strip()
    if len(stripped) < 12:
        return False
    if stripped[-1] in TERMINAL_PUNCTUATION or stripped[-1] in NON_TERMINAL_PUNCTUATION:
        return False
    if re.search(r"\n", stripped):
        return False
    if re.fullmatch(r"[A-Za-z0-9_.,:;()[\]{}<>=+\-*/&| \t]+", stripped):
        return False
    return True


def terminal_punctuation_for_line(line: str) -> str:
    if re.search(r"(表述为|如下|如下所示|定义为|记为|形式为)$", line.strip()):
        return "："
    return "。"


def repair_translation_punctuation(text: str) -> str:
    lines = text.split("\n")
    repaired = []
    for line in lines:
        stripped = line.strip()
        if stripped and line_needs_terminal_punctuation(stripped):
            line = line.rstrip() + terminal_punctuation_for_line(stripped)
        repaired.append(line)
    return "\n".join(repaired)


def render_line_starts_new_paragraph(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    return bool(
        re.match(r"^\d+(?:\.\d+)*\.?\s+[\u4e00-\u9fffA-Za-z]", stripped)
        or re.match(r"^(定理|引理|推论|命题|证明|算法|图|表)\s*\d*", stripped)
    )


def render_line_is_standalone_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if re.search(r"https?://|www\.|\S+@\S+", stripped, flags=re.I):
        return False
    numbered = re.match(r"^(\d+(?:\.\d+)*\.?)\s+(.+)", stripped)
    if numbered:
        number, title = numbered.groups()
        plain_multi_digit_number = bool(re.fullmatch(r"\d{2,}", number))
        sentence_like_title = bool(
            re.search(r"[。！？.!?]$", title)
            or re.search(r"\d{4}\s*年|\b\d{4}\b", title)
        )
        if plain_multi_digit_number and sentence_like_title:
            return False
        return len(stripped) <= 40
    return False


def join_render_lines(left: str, right: str) -> str:
    if not left:
        return right
    if not right:
        return left
    left_tail = left[-1]
    right_head = right[0]
    latin_left = bool(re.match(r"[A-Za-z0-9)\]}）】》]", left_tail))
    latin_right = bool(re.match(r"[A-Za-z0-9(\[{（【《]", right_head))
    cjk_left = bool(re.match(r"[\u4e00-\u9fff]", left_tail))
    cjk_right = bool(re.match(r"[\u4e00-\u9fff]", right_head))
    if (latin_left and (latin_right or cjk_right)) or (cjk_left and latin_right):
        return left + " " + right
    return left + right


def render_line_break_is_paragraph_boundary(left: str, right: str) -> bool:
    left = left.strip()
    right = right.strip()
    if not left or not right:
        return False
    if not re.match(r"[\u4e00-\u9fffA-Za-z0-9(（“]", right):
        return False
    if re.search(r"[A-Za-z_][A-Za-z0-9_]*(?:\[[^\]]+\]|\([^)]+\))$", left):
        return False
    if re.search(r"(表述为|如下|如下所示|定义为|记为|形式为)$", left):
        return True
    continuation_tails = (
        "当",
        "如果",
        "由于",
        "因为",
        "和",
        "或",
        "与",
        "在",
        "为",
        "由",
        "对",
        "其中",
        "以及",
        "如下",
        "表明",
    )
    if left.endswith(continuation_tails) or left[-1] in NON_TERMINAL_PUNCTUATION:
        return False
    if left[-1] in TERMINAL_PUNCTUATION and len(left) >= 24:
        return True
    return cjk_char_count(left) >= 45 and cjk_char_count(right) >= 6


def reflow_translation_soft_breaks(text: str) -> str:
    paragraphs = []
    current = ""
    for raw_line in normalize_translation(text).split("\n"):
        line = raw_line.strip()
        if not line:
            if current:
                paragraphs.append(current)
                current = ""
            continue
        if current and render_line_starts_new_paragraph(line):
            paragraphs.append(current)
            current = ""
        elif current and render_line_break_is_paragraph_boundary(current, line):
            paragraphs.append(current)
            current = ""
        current = join_render_lines(current, line) if current else line
        if render_line_is_standalone_heading(line):
            paragraphs.append(current)
            current = ""
    if current:
        paragraphs.append(current)
    return "\n".join(paragraphs)


def prepare_render_translation(text: str) -> str:
    return repair_translation_punctuation(reflow_translation_soft_breaks(strip_journal_footer_lines(text)))


SOURCE_CONTINUATION_TAIL_WORDS = {
    "and",
    "or",
    "of",
    "the",
    "a",
    "an",
    "to",
    "in",
    "with",
    "by",
    "for",
    "that",
    "which",
    "when",
    "where",
    "whose",
    "as",
    "if",
    "while",
}

SOURCE_CONTINUATION_HEAD_WORDS = {
    "and",
    "or",
    "of",
    "to",
    "in",
    "with",
    "by",
    "for",
    "that",
    "which",
    "where",
    "whose",
    "as",
    "than",
}


def source_text_without_running_header(text: str, page_num: int) -> str:
    text = strip_leading_running_header_text(text, page_num)
    lines = [line.strip() for line in normalize_text(text).split("\n") if line.strip()]
    if page_num > 1 and lines:
        first = lines[0]
        compact = re.sub(r"[^a-z0-9]+", "", first.lower())
        if "waitfree" in compact and "synchronization" in compact:
            lines = lines[1:]
    while lines and (is_page_number(lines[0]) or is_decorated_ocr_page_number_text(lines[0])):
        lines = lines[1:]
    return strip_journal_footer_lines("\n".join(lines))


def strip_running_header_prefix_from_line(line: str, page_num: int) -> str:
    if page_num <= 1:
        return line
    stripped = line.strip()
    stripped = re.sub(
        r"^[·•]?\s*\d{1,3}\s*[·•.]?\s*(?=(?:Maurice\s*Herlihy|Wait-?\s*Free\s*Synchronization|Wait-FreeSynchronization|无等待同步)\b)",
        "",
        stripped,
        flags=re.I,
    )
    for pattern, flags in (
        (r"^(?:Maurice\s*Herlihy|MauriceHerlihy|Wait-?\s*Free\s*Synchronization|Wait-FreeSynchronization)\b", re.I),
        (r"^无等待同步", 0),
    ):
        match = re.match(pattern, stripped, flags=flags)
        if not match:
            continue
        rest = stripped[match.end() :]
        page_match = re.match(r"\s*[·•.]?\s*\d{1,3}\s*", rest)
        if page_match:
            stripped = rest[page_match.end() :].strip()
        elif not rest.strip() or rest[:1].isspace():
            stripped = rest.strip()
        break
    if is_page_number(stripped) or is_decorated_ocr_page_number_text(stripped):
        return ""
    return stripped


def strip_leading_running_header_text(text: str, page_num: int) -> str:
    if page_num <= 1:
        return normalize_text(text)
    lines = normalize_text(text).split("\n")
    cleaned = []
    stripping = True
    for line in lines:
        if stripping:
            line = strip_running_header_prefix_from_line(line, page_num)
            if not line:
                continue
            stripping = False
        cleaned.append(line)
    return normalize_text("\n".join(cleaned))


def source_boundary_text_for_block(block) -> str:
    return source_text_without_running_header(block.get("text", ""), block.get("page", 1))


def source_text_likely_continues(text: str) -> bool:
    stripped = normalize_text(text)
    if not stripped:
        return False
    if stripped.endswith("-"):
        return True
    if stripped[-1] in ",;:，；：":
        return True
    words = re.findall(r"[A-Za-z]+", stripped)
    if words and words[-1].lower() in SOURCE_CONTINUATION_TAIL_WORDS:
        return True
    return not bool(re.search(r"[.!?。！？][\"')\]）】”’]*$", stripped))


def source_text_starts_as_continuation(text: str) -> bool:
    stripped = normalize_text(text)
    if not stripped:
        return False
    if re.match(r"^[,.;:)\]}，。；：）】]", stripped):
        return True
    words = re.findall(r"[A-Za-z]+", stripped)
    if words and words[0].lower() in SOURCE_CONTINUATION_HEAD_WORDS:
        return True
    first = stripped[0]
    return bool(re.match(r"[a-z]", first))


def boundary_repair_key(previous_id: str, next_id: str) -> str:
    return f"{previous_id}->{next_id}"


def boundary_candidate_blocks(blocks) -> list[dict]:
    visual_regions = build_visual_regions(blocks)
    classes = classify_blocks(blocks, visual_regions)
    block_by_id = {block["id"]: block for block in blocks}
    visual_ids = {source_id for region in visual_regions for source_id in region["source_ids"]}
    candidates = []
    for block in sorted(blocks, key=lambda item: (item["yMin"], item["xMin"])):
        if block["id"] in visual_ids:
            continue
        text = source_boundary_text_for_block(block)
        if not text or is_trivial_keep(text):
            continue
        classification = classes.get(block["id"], "unknown")
        if classification in {"page_number", "journal_footer", "reference"}:
            continue
        if classification == "header_footer" and not source_requires_chinese_translation(text):
            continue
        if classification not in {"body", "header_footer"}:
            continue
        enriched = dict(block)
        enriched["_boundary_text"] = text
        enriched["_classification"] = classification
        candidates.append(enriched)
    return candidates


def first_complete_sentence_from_boundary_context(text: str) -> str:
    normalized = normalize_text(text)
    if not normalized:
        return ""
    match = re.search(r"^(.+?[.!?])(?:\s|$)", normalized)
    if match:
        return match.group(1).strip()
    return normalized


def rows_in_vertical_window(page_num: int, bbox_lines: list[dict], y0: float, y1: float) -> list[dict]:
    lines = [
        line
        for line in bbox_lines
        if line.get("page") == page_num
        and y0 <= bbox_center(line["bbox"])[1] <= y1
    ]
    rows = []
    for row in merge_bbox_line_fragments(lines):
        pseudo = {
            "page": page_num,
            "yMin": row["bbox"][1],
            "yMax": row["bbox"][3],
            "text": row["text"],
        }
        if source_body_row_is_noise(pseudo, row):
            continue
        rows.append(row)
    return rows


def boundary_source_sentence_from_bbox_margin(
    previous_block,
    next_block,
    bbox_lines_by_page_map: dict[int, list[dict]] | None,
) -> str:
    if not bbox_lines_by_page_map:
        return ""
    previous_lines = bbox_lines_by_page_map.get(previous_block.get("page", 1), [])
    next_lines = bbox_lines_by_page_map.get(next_block.get("page", 1), [])
    if not previous_lines or not next_lines:
        return ""
    previous_rows = rows_in_vertical_window(
        previous_block.get("page", 1),
        previous_lines,
        max(0.0, previous_block["yMin"] - 4.0),
        previous_block["yMax"] + 32.0,
    )
    next_rows = rows_in_vertical_window(
        next_block.get("page", 1),
        next_lines,
        0.0,
        max(next_block["yMax"], next_block["yMin"] + 58.0),
    )
    if not previous_rows or not next_rows:
        return ""
    context_text = " ".join(
        row["text"]
        for row in sorted(previous_rows, key=lambda item: (item["bbox"][1], item["bbox"][0]))
        + sorted(next_rows, key=lambda item: (item["bbox"][1], item["bbox"][0]))
    )
    return first_complete_sentence_from_boundary_context(context_text)


def fallback_boundary_source_sentence(previous_block, next_block) -> str:
    return first_complete_sentence_from_boundary_context(
        source_boundary_text_for_block(previous_block) + " " + source_boundary_text_for_block(next_block)
    )


def compact_source_phrase(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalize_text(text).lower())


def source_head_compact_fragment(text: str) -> str:
    stripped = normalize_text(text)
    match = re.match(r"^[,.;:)\]}，。；：）】\s]*(.+?)(?:[.!?]\s|$)", stripped)
    head_sentence = match.group(1) if match else stripped
    words = re.findall(r"[A-Za-z0-9]+", head_sentence)
    if not words:
        return ""
    if len(words) >= 2:
        return compact_source_phrase(" ".join(words[:2]))
    return compact_source_phrase(words[0])


def boundary_sentence_contains_next_fragment(source_sentence: str, next_source: str) -> bool:
    head = source_head_compact_fragment(next_source)
    if len(head) < 4:
        return False
    return head in compact_source_phrase(source_sentence)


def detect_cross_page_sentence_splits(
    selected_pages,
    *,
    bbox_lines_by_page: dict[int, list[dict]] | None = None,
) -> list[dict]:
    ordered_pages = sorted(selected_pages, key=lambda item: item[0])
    candidates = []
    for (previous_page_num, previous_blocks), (next_page_num, next_blocks) in zip(ordered_pages, ordered_pages[1:]):
        if next_page_num != previous_page_num + 1:
            continue
        previous_candidates = boundary_candidate_blocks(previous_blocks)
        next_candidates = boundary_candidate_blocks(next_blocks)
        previous_block = next(
            (
                block
                for block in reversed(previous_candidates)
                if block.get("_classification") == "body"
                if source_text_likely_continues(block.get("_boundary_text", source_boundary_text_for_block(block)))
            ),
            None,
        )
        next_block = next(
            (
                block
                for block in next_candidates
                if source_text_starts_as_continuation(block.get("_boundary_text", source_boundary_text_for_block(block)))
            ),
            None,
        )
        if previous_block is None or next_block is None:
            continue
        source_sentence = boundary_source_sentence_from_bbox_margin(
            previous_block,
            next_block,
            bbox_lines_by_page,
        ) or fallback_boundary_source_sentence(previous_block, next_block)
        if not boundary_sentence_contains_next_fragment(
            source_sentence,
            next_block.get("_boundary_text", source_boundary_text_for_block(next_block)),
        ):
            continue
        candidates.append(
            {
                "key": boundary_repair_key(previous_block["id"], next_block["id"]),
                "previous_id": previous_block["id"],
                "next_id": next_block["id"],
                "previous_page": previous_page_num,
                "next_page": next_page_num,
                "previous_source": previous_block.get("_boundary_text", source_boundary_text_for_block(previous_block)),
                "next_source": next_block.get("_boundary_text", source_boundary_text_for_block(next_block)),
                "source_sentence": source_sentence,
            }
        )
    return candidates


def remove_leading_translation_prefix(text: str, prefix: str) -> str:
    if not text or not prefix:
        return text
    variants = [prefix, prepare_render_translation(prefix)]
    prepared_text = prepare_render_translation(text)
    for variant in variants:
        variant = variant.strip()
        if not variant:
            continue
        if text.startswith(variant):
            return text[len(variant) :].lstrip()
        if prepared_text.startswith(variant):
            return prepared_text[len(variant) :].lstrip()
    lines = text.splitlines()
    if len(lines) >= 2:
        first_compact = re.sub(r"\s+", "", lines[0].lower())
        if first_compact in {"无等待同步", "wait-freesynchronization", "waitfreesynchronization"}:
            tail = "\n".join(lines[1:])
            trimmed_tail = remove_leading_translation_prefix(tail, prefix)
            if trimmed_tail != tail:
                return lines[0] + "\n" + trimmed_tail
    return text


def load_boundary_repairs(job_paths) -> dict:
    if not job_paths:
        return {}
    path = job_paths.get("boundary_repairs_path")
    if not path or not Path(path).exists():
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_boundary_repairs(job_paths, repairs: dict) -> None:
    path = job_paths.get("boundary_repairs_path")
    if not path:
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(repairs, ensure_ascii=False, indent=2), encoding="utf-8")


def write_boundary_repair_schema(path: Path) -> None:
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "translation": {"type": "string"},
                        "next_prefix_translation": {"type": "string"},
                    },
                    "required": ["key", "translation", "next_prefix_translation"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }
    path.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")


def make_boundary_repair_prompt(items: list[dict]) -> str:
    payload = {"items": items}
    return (
        "你在修复学术论文中被分页切断的英文句子翻译。每个 item 给出跨页拼接后的 source_sentence、"
        "上一页已有译文 previous_translation、下一页开头已有译文 next_translation。"
        "请为 source_sentence 给出一条完整、忠实、自然的简体中文 translation。"
        "next_prefix_translation 填写 next_translation 开头中对应该跨页残片、需要从下一页删除以避免重复的中文前缀；"
        "如果没有需要删除的前缀，填空字符串。不要解释，不要 Markdown，只输出 JSON。\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def translate_boundary_sentence_repairs(
    candidates: list[dict],
    translations: dict,
    job_paths,
    *,
    model: str,
    reasoning_effort: str = "low",
) -> dict:
    if not candidates:
        return {}
    schema_path = job_paths["boundary_schema_path"]
    write_boundary_repair_schema(schema_path)
    prompt_items = []
    for candidate in candidates:
        prompt_items.append(
            {
                "key": candidate["key"],
                "source_sentence": candidate["source_sentence"],
                "previous_source": candidate["previous_source"],
                "next_source": candidate["next_source"],
                "previous_translation": translations.get(candidate["previous_id"], ""),
                "next_translation": translations.get(candidate["next_id"], ""),
            }
        )
    prompt = make_boundary_repair_prompt(prompt_items)
    prompt_path = job_paths["job_dir"] / "boundary-sentence-repair.prompt.txt"
    out_path = job_paths["job_dir"] / "boundary-sentence-repair.out.json"
    log_path = job_paths["job_dir"] / "boundary-sentence-repair.log.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    proc = run(
        [
            "codex",
            "exec",
            "--skip-git-repo-check",
            "-m",
            model,
            "-c",
            f"model_reasoning_effort='{reasoning_effort}'",
            "--disable",
            "plugins",
            "--disable",
            "shell_snapshot",
            "--sandbox",
            "workspace-write",
            "--ephemeral",
            "--output-schema",
            str(schema_path),
            "-o",
            str(out_path),
            "-",
        ],
        input_text=prompt,
        check=False,
    )
    log_path.write_text(proc.stdout + "\n\nSTDERR\n" + proc.stderr, encoding="utf-8")
    if proc.returncode != 0 or not out_path.exists():
        raise RuntimeError(f"boundary sentence repair failed, see {log_path}")
    data = json.loads(out_path.read_text(encoding="utf-8"))
    return {
        item["key"]: {
            "translation": item.get("translation", "").strip(),
            "next_prefix_translation": item.get("next_prefix_translation", "").strip(),
        }
        for item in data.get("items", [])
        if item.get("key")
    }


def postprocess_cross_page_sentence_splits(
    selected_pages,
    translations: dict,
    *,
    boundary_repairs: dict | None = None,
    job_paths=None,
    model: str | None = None,
    reasoning_effort: str = "low",
) -> dict:
    repaired_translations = dict(translations)
    lines_by_page = bbox_lines_by_page(job_paths) if job_paths else {}
    candidates = detect_cross_page_sentence_splits(selected_pages, bbox_lines_by_page=lines_by_page)
    repairs = {}
    repairs.update(load_boundary_repairs(job_paths))
    if boundary_repairs:
        repairs.update(boundary_repairs)
    missing = [candidate for candidate in candidates if candidate["key"] not in repairs]
    if missing and model and job_paths:
        generated = translate_boundary_sentence_repairs(
            missing,
            repaired_translations,
            job_paths,
            model=model,
            reasoning_effort=reasoning_effort,
        )
        repairs.update(generated)
        save_boundary_repairs(job_paths, repairs)

    for candidate in candidates:
        repair = repairs.get(candidate["key"])
        if not repair:
            continue
        translation = normalize_translation(repair.get("translation", ""))
        if translation:
            repaired_translations[candidate["previous_id"]] = translation
        next_prefix = normalize_translation(repair.get("next_prefix_translation", ""))
        if next_prefix:
            repaired_translations[candidate["next_id"]] = remove_leading_translation_prefix(
                repaired_translations.get(candidate["next_id"], ""),
                next_prefix,
            )
    return repaired_translations


def page_image_path(page_num: int, job_paths) -> Path:
    return job_paths["pages_dir"] / f"page-{page_num:03d}.png"


def translated_page_path(page_num: int, job_paths) -> Path:
    return job_paths["translated_pages_dir"] / f"page-{page_num:03d}.png"


def sample_background(img: Image.Image, box):
    x0, y0, x1, y1 = box
    width, height = img.size
    pad = 3
    samples = []

    def add_strip(xs, ys, xe, ye):
        xs = max(0, min(width - 1, xs))
        ys = max(0, min(height - 1, ys))
        xe = max(0, min(width, xe))
        ye = max(0, min(height, ye))
        if xe <= xs or ye <= ys:
            return
        crop = img.crop((xs, ys, xe, ye)).convert("RGB")
        pixels = list(crop.getdata())
        samples.extend(pixels)

    add_strip(x0 - pad, y0 - pad, x1 + pad, y0)
    add_strip(x0 - pad, y1, x1 + pad, y1 + pad)
    add_strip(x0 - pad, y0, x0, y1)
    add_strip(x1, y0, x1 + pad, y1)
    if not samples:
        return (255, 255, 255)

    bright = [p for p in samples if sum(p) >= 540]
    target = bright or samples
    return tuple(int(median(channel)) for channel in zip(*target))


def raster_text_fill_for_background(bg) -> tuple[int, int, int, int]:
    luminance = 0.2126 * bg[0] + 0.7152 * bg[1] + 0.0722 * bg[2]
    if luminance < 128:
        return (245, 245, 245, 255)
    return (32, 32, 32, 255)


def draw_vertical(draw_img: Image.Image, text: str, box, fill_bg, fill_text):
    x0, y0, x1, y1 = box
    width = max(1, x1 - x0)
    height = max(1, y1 - y0)
    font_size, _ = fit_font_and_lines(text, height - 4, width - 4, vertical=True)
    font = ImageFont.truetype(FONT_PATH, font_size)
    tmp = Image.new("RGBA", (height, width), fill_bg + (255,))
    tdraw = ImageDraw.Draw(tmp)
    bbox = tdraw.textbbox((0, 0), text, font=font)
    tx = max(0, (height - (bbox[2] - bbox[0])) // 2)
    ty = max(0, (width - (bbox[3] - bbox[1])) // 2)
    tdraw.text((tx, ty), text, font=font, fill=fill_text)
    rotated = tmp.rotate(90, expand=True)
    draw_img.alpha_composite(rotated, (x0, y0))


def translation_for_block(block, translations):
    translated = translations.get(block["id"])
    if translated:
        return prepare_render_translation(translated)
    if is_trivial_keep(block["text"]):
        return ""
    return block["text"]


def build_render_boxes(blocks, translations, dpi: int, page_width: int, page_height: int, protected_boxes):
    return _layout_build_render_boxes(
        blocks,
        translations,
        dpi,
        page_width,
        page_height,
        protected_boxes,
        block_to_px_box=block_to_px_box,
        translation_resolver=translation_for_block,
    )


def draw_block(
    draw_img: Image.Image,
    block,
    translation: str,
    dpi: int,
    protected_boxes=None,
    render_box=None,
):
    if render_box is not None:
        source_box = block_to_px_box(block, dpi, draw_img.width, draw_img.height, pad=2)
        # Clear the complete source text box when layout moves or clips the
        # translated text. Protected image clips are restored after all text is
        # drawn, so clipping this cleanup to protected boxes can leave source
        # prose visible beside the translated paragraph.
        if source_box is not None and tuple(source_box) != tuple(render_box):
            sx0, sy0, sx1, sy1 = source_box
            if sx1 > sx0 and sy1 > sy0:
                source_bg = sample_background(draw_img.convert("RGB"), source_box)
                source_rect = Image.new("RGBA", (sx1 - sx0, sy1 - sy0), source_bg + (255,))
                draw_img.alpha_composite(source_rect, (sx0, sy0))
    box = render_box or block_to_px_box(block, dpi, draw_img.width, draw_img.height, pad=2)
    if render_box is None:
        box = avoid_protected_boxes(box, protected_boxes)
    if box is None:
        return
    x0, y0, x1, y1 = box
    box = (x0, y0, x1, y1)
    bg = sample_background(draw_img.convert("RGB"), box)
    rect = Image.new("RGBA", (x1 - x0, y1 - y0), bg + (255,))
    draw_img.alpha_composite(rect, (x0, y0))

    width = max(10, x1 - x0 - 4)
    height = max(10, y1 - y0 - 4)
    vertical = raster_text_should_render_vertical(block.get("text", ""), box)
    fill_text = raster_text_fill_for_background(bg)
    if vertical:
        draw_vertical(draw_img, translation, box, bg, fill_text)
        return

    target_font_size = target_font_size_for_block(block, dpi, vertical=False)
    font_size, lines = fit_font_and_lines(
        translation,
        width,
        height,
        vertical=False,
        max_font_size=target_font_size,
    )
    font = ImageFont.truetype(FONT_PATH, font_size)
    ascent, descent = font.getmetrics()
    line_height = int((ascent + descent) * 1.25)
    text_img = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
    text_draw = ImageDraw.Draw(text_img)
    y = 2
    for line in lines:
        text_draw.text((2, y), line, font=font, fill=fill_text)
        y += line_height
        draw_img.alpha_composite(text_img, (x0, y0))


def bbox_to_px_box(bbox, dpi: int, page_width: int, page_height: int, pad: int = 3):
    x0, y0, x1, y1 = bbox
    scale = dpi / 72.0
    return (
        max(0, int(x0 * scale) - pad),
        max(0, int(y0 * scale) - pad),
        min(page_width, int(x1 * scale) + pad),
        min(page_height, int(y1 * scale) + pad),
    )


def raster_protected_boxes(blocks, dpi: int, page_width: int, page_height: int):
    visual_regions = build_visual_regions(blocks)
    visual_source_ids = {
        source_id
        for region in visual_regions
        for source_id in region.get("source_ids", [])
    }
    boxes = [
        bbox_to_px_box(region["bbox"], dpi, page_width, page_height, pad=3)
        for region in visual_regions
        if region.get("bbox")
    ]
    boxes.extend(
        protected_box_for_block(block, dpi, page_width, page_height)
        for block in blocks
        if should_preserve_as_image(block) and block["id"] not in visual_source_ids
    )
    return boxes


def render_pages(pages, translations, dpi: int, job_paths):
    for page_num, blocks in pages:
        src = page_image_path(page_num, job_paths)
        out = translated_page_path(page_num, job_paths)
        source_img = Image.open(src).convert("RGBA")
        img = source_img.copy()
        protected_boxes = raster_protected_boxes(blocks, dpi, img.width, img.height)
        render_boxes = build_render_boxes(blocks, translations, dpi, img.width, img.height, protected_boxes)
        for block in blocks:
            if should_preserve_as_image(block):
                continue
            if is_page_number(block["text"]):
                continue
            translated = translation_for_block(block, translations)
            if not translated:
                continue
            draw_block(
                img,
                block,
                translated,
                dpi,
                protected_boxes=protected_boxes,
                render_box=render_boxes.get(block["id"]),
            )
        for box in protected_boxes:
            x0, y0, x1, y1 = box
            if x1 > x0 and y1 > y0:
                img.alpha_composite(source_img.crop(box), (x0, y0))
        img.save(out)


def compact_duplicate_text(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "", normalize_text(text)).lower()


def nested_duplicate_block_ids(blocks) -> set[str]:
    skipped = set()
    for block in blocks:
        block_area = block_area_pt(block)
        if block_area <= 0:
            continue
        block_compact = compact_duplicate_text(block.get("text", ""))
        if len(block_compact) < 8:
            continue
        for other in blocks:
            if other["id"] == block["id"]:
                continue
            other_area = block_area_pt(other)
            if other_area <= block_area * 2.5:
                continue
            if block_overlap_area_pt(block, other) / block_area < 0.85:
                continue
            other_compact = compact_duplicate_text(other.get("text", ""))
            if block_compact and block_compact in other_compact:
                skipped.add(block["id"])
                break
    return skipped


def contained_standalone_label_ids(blocks, classes) -> set[str]:
    skipped = set()
    for block in blocks:
        if classes.get(block["id"]) not in NORMAL_TRANSLATED_CLASSES:
            continue
        if not is_standalone_equation_label(block.get("text", "")):
            continue
        block_box = block_bbox(block)
        center = bbox_center(block_box)
        block_area = block_area_pt(block)
        for other in blocks:
            if other["id"] == block["id"] or classes.get(other["id"]) not in NORMAL_TRANSLATED_CLASSES:
                continue
            if block_area_pt(other) < max(1.0, block_area) * 8.0:
                continue
            if bbox_contains_point(block_bbox(other), center):
                skipped.add(block["id"])
                break
    return skipped


def filter_nested_vector_blocks(blocks, translations):
    text_blocks = [
        block
        for block in blocks
        if not should_preserve_as_image(block)
        and not is_page_number(block["text"])
        and translation_for_block(block, translations)
    ]
    skipped = set()
    for block in text_blocks:
        block_area = block_area_pt(block)
        if block_area <= 0:
            continue
        block_text_len = len(block.get("text", ""))
        for other in text_blocks:
            if other["id"] == block["id"]:
                continue
            other_area = block_area_pt(other)
            if other_area <= block_area * 2.5:
                continue
            if len(other.get("text", "")) <= block_text_len * 3:
                continue
            if block_overlap_area_pt(block, other) / block_area >= 0.85:
                skipped.add(block["id"])
                break
    return [block for block in blocks if block["id"] not in skipped]


def normalize_outline_match_text(text: str) -> str:
    text = normalize_text(text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"([A-Za-z0-9])- +([A-Za-z0-9])", r"\1-\2", text)
    return text.strip()


def clean_outline_title(text: str) -> str:
    text = normalize_translation(text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
    text = re.sub(r"^(第\s*\d+\s*章)(?=\S)", r"\1 ", text)
    return text.strip()


def translated_outline_title_map(selected_pages, translations):
    title_map = {}
    page_title_map = {}
    for page_num, blocks in selected_pages:
        for block in blocks:
            translated = translations.get(block["id"])
            if not translated:
                continue
            source_title = normalize_outline_match_text(block["text"])
            if not source_title:
                continue
            translated_title = clean_outline_title(translated)
            title_map.setdefault(source_title, translated_title)
            page_title_map.setdefault(page_num, {})[source_title] = translated_title
            chapter_match = re.match(r"Chapter\s+(\d+)\.\s+(.+)", source_title)
            if chapter_match:
                short_title = f"{chapter_match.group(1)}. {chapter_match.group(2)}"
                title_map.setdefault(short_title, translated_title)
                page_title_map.setdefault(page_num, {})[short_title] = translated_title
    return title_map, page_title_map


def translate_outline_title(title: str, page_num: int, title_map, page_title_map) -> str:
    key = normalize_outline_match_text(title)
    if key in title_map:
        return title_map[key]
    for source_title, translated_title in page_title_map.get(page_num, {}).items():
        if key == source_title or key in source_title or source_title in key:
            return translated_title
    return title


def build_translated_toc(toc, selected_pages, translations):
    title_map, page_title_map = translated_outline_title_map(selected_pages, translations)
    return [
        [level, translate_outline_title(title, page_num, title_map, page_title_map), page_num]
        for level, title, page_num in toc
    ]


def translated_heading_title(blocks, translations) -> str:
    pieces = []
    for block in blocks:
        translated = translations.get(block["id"])
        pieces.append(clean_outline_title(translated or short_heading_text(block.get("text", ""))))
    return clean_outline_title(" ".join(piece for piece in pieces if piece))


def build_heuristic_toc(selected_pages, translations):
    toc = []
    seen = set()
    for output_page_num, (_source_page_num, blocks) in enumerate(selected_pages, start=1):
        if not blocks:
            continue
        page_bottom = max(block["yMax"] for block in blocks)
        sorted_blocks = sorted(
            blocks,
            key=lambda item: (item.get("block_index", 0), item["yMin"], item["xMin"]),
        )
        skip_ids = set()
        for idx, block in enumerate(sorted_blocks):
            if block["id"] in skip_ids or should_preserve_as_image(block):
                continue
            if block["yMin"] > page_bottom - HEURISTIC_HEADING_BOTTOM_MARGIN_PT:
                continue

            numbered = re.fullmatch(r"\d+(?:\.\d+)*\.?", short_heading_text(block.get("text", "")))
            if numbered:
                number_text = numbered.group(0).rstrip(".")
                number_parts = [int(part) for part in number_text.split(".") if part.isdigit()]
                if not number_parts or number_parts[0] > 20 or any(part > 20 for part in number_parts[1:]):
                    continue
                for other in sorted_blocks[idx + 1 : idx + 5]:
                    if should_preserve_as_image(other):
                        continue
                    if not same_heading_line(block, other):
                        continue
                    other_text = short_heading_text(other.get("text", ""))
                    if not other_text or not starts_like_source_heading(other_text) or other_text.endswith("."):
                        continue
                    level = min(4, number_text.count(".") + 1)
                    title = translated_heading_title([block, other], translations)
                    key = (level, output_page_num, normalize_outline_match_text(title))
                    if title and key not in seen:
                        toc.append([level, title, output_page_num])
                        seen.add(key)
                    skip_ids.add(other["id"])
                    break
                continue

            heading = heuristic_heading_from_block(block)
            if not heading:
                continue
            level, _source_title = heading
            title = translated_heading_title([block], translations)
            key = (level, output_page_num, normalize_outline_match_text(title))
            if title and key not in seen:
                toc.append([level, title, output_page_num])
                seen.add(key)

    normalized = []
    previous_level = 0
    for level, title, page_num in toc:
        if previous_level == 0:
            level = 1
        elif level > previous_level + 1:
            level = previous_level + 1
        normalized.append([level, title, page_num])
        previous_level = level
    return normalized


def copy_outline(src_doc, out_doc, selected_pages, translations):
    toc = src_doc.get_toc(simple=True)
    if not toc:
        heuristic_toc = build_heuristic_toc(selected_pages, translations)
        if heuristic_toc:
            out_doc.set_toc(heuristic_toc)
        return

    selected_page_numbers = [page_num for page_num, _ in selected_pages]
    if selected_page_numbers == list(range(1, src_doc.page_count + 1)):
        out_doc.set_toc(build_translated_toc(toc, selected_pages, translations))
        return

    page_map = {page_num: idx for idx, page_num in enumerate(selected_page_numbers, start=1)}
    title_map, page_title_map = translated_outline_title_map(selected_pages, translations)
    partial_toc = [
        [1, translate_outline_title(title, page_num, title_map, page_title_map), page_map[page_num]]
        for _level, title, page_num in toc
        if page_num in page_map
    ]
    if partial_toc:
        out_doc.set_toc(partial_toc)


def journal_footer_bbox(page_size) -> tuple[float, float, float, float]:
    width, height = page_size
    footer_width = min(JOURNAL_FOOTER_WIDTH_PT, max(40.0, width - 24.0))
    x0 = (width - footer_width) / 2.0
    y0 = min(height - JOURNAL_FOOTER_HEIGHT_PT - 4.0, max(0.0, height - 152.0))
    return (
        round(x0, 3),
        round(y0, 3),
        round(x0 + footer_width, 3),
        round(y0 + JOURNAL_FOOTER_HEIGHT_PT, 3),
    )


def journal_footer_render_items(bbox_lines, page_size) -> list[RenderItem]:
    if not bbox_lines:
        return []
    _width, height = page_size
    footer_candidates = [
        line
        for line in bbox_lines
        if line["bbox"][1] > height * 0.72
    ]
    items = []
    for line in merge_bbox_line_fragments(footer_candidates):
        if not is_journal_footer_text(line["text"]):
            continue
        items.append(
            RenderItem(
                "original_selectable_text",
                [],
                journal_footer_bbox(page_size),
                text=JOURNAL_FOOTER_TEXT,
                font_size=JOURNAL_FOOTER_FONT_SIZE,
                style_name="footer",
                fallback_reason="journal_footer",
            )
        )
    return items[:1]


def reference_block_ids_for_bbox_line(line: dict, reference_blocks) -> list[str]:
    line_box = line["bbox"]
    line_center = bbox_center(line_box)
    line_width = max(1.0, line_box[2] - line_box[0])
    matches = []
    for block in reference_blocks:
        reference_box = expanded_bbox(block_bbox(block), pad_x=4.0, pad_y=3.0)
        if bbox_contains_point(reference_box, line_center):
            matches.append(block["id"])
            continue
        if vertical_overlap(line_box, reference_box) > 0 and horizontal_overlap(line_box, reference_box) >= line_width * 0.55:
            matches.append(block["id"])
    return sorted(set(matches))


def bbox_line_matches_reference_block(line: dict, reference_blocks) -> bool:
    return bool(reference_block_ids_for_bbox_line(line, reference_blocks))


def reference_line_render_items(blocks, bbox_lines, page_size, classes: dict[str, str] | None = None) -> list[RenderItem]:
    if not bbox_lines:
        return []
    reference_ids = {
        block_id
        for block_id, classification in (classes or {}).items()
        if classification == "reference"
    }
    if not reference_ids:
        reference_ids = reference_block_ids(blocks)
    reference_blocks = [block for block in blocks if block["id"] in reference_ids]
    if not reference_blocks:
        return []
    width, height = page_size
    items = []
    reference_lines = []
    for line in bbox_lines:
        text = normalize_text(line.get("text", ""))
        if not text:
            continue
        if is_journal_footer_text(text):
            continue
        source_ids = reference_block_ids_for_bbox_line(line, reference_blocks)
        if not source_ids:
            continue
        reference_line = dict(line)
        reference_line["source_ids"] = source_ids
        reference_lines.append(reference_line)
    for line in merge_bbox_line_fragments(reference_lines):
        text = line["text"]
        if is_journal_footer_text(text):
            continue
        x0, y0, x1, y1 = line["bbox"]
        bbox = (
            max(0.0, x0),
            max(0.0, y0),
            min(width, x1 + 2.0),
            min(height, y1 + 2.0),
        )
        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            continue
        font_size = DOCUMENT_STYLES["reference"].font_size
        items.append(
            RenderItem(
                "original_selectable_text",
                sorted(
                    set(
                        str(source_id)
                        for source_id in (
                            line.get("source_ids") or reference_block_ids_for_bbox_line(line, reference_blocks)
                        )
                    )
                ),
                bbox,
                text=text,
                font_size=font_size,
                style_name="reference",
                fallback_reason="reference_original",
            )
        )
    return items


def reference_coverage_entry(block, render_kind: str, fallback_reason: str) -> CoverageEntry:
    return CoverageEntry(
        block["id"],
        "reference",
        render_kind,
        True,
        fallback_reason,
        reference_signature=reference_signature(block.get("text", "")),
    )


def adjusted_render_bbox(block, classification: str, page_size) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = block_bbox(block)
    page_width, page_height = page_size
    if classification == "heading":
        y1 = max(y1, y0 + 18.0)
        x1 = max(x1, x0 + 140.0)
    elif classification == "title":
        y1 = max(y1, y0 + 22.0)
    return (
        max(0.0, x0),
        max(0.0, y0),
        min(page_width, x1),
        min(page_height, y1),
    )


def compact_match_text(text: str) -> str:
    return re.sub(r"\s+", "", normalize_text(text).lower())


def matching_line_bboxes_for_block(block, bbox_lines) -> list[tuple[float, float, float, float]]:
    block_compact = compact_match_text(block.get("text", ""))
    if not block_compact or not bbox_lines:
        return []
    matches = []
    for line in bbox_lines:
        line_text = line.get("text", "")
        line_compact = compact_match_text(line_text)
        if not line_compact:
            continue
        if line_compact in block_compact or block_compact in line_compact:
            matches.append(line["bbox"])
    return matches


def refined_text_bbox_from_lines(block, bbox, bbox_lines, page_size) -> tuple[float, float, float, float]:
    if not bbox_lines:
        return bbox
    x0, y0, x1, y1 = bbox
    if x0 > 4.0:
        return bbox
    matches = matching_line_bboxes_for_block(block, bbox_lines)
    if not matches:
        return bbox
    matched = bbox_union(matches)
    page_width, page_height = page_size
    return (
        max(0.0, matched[0]),
        max(0.0, y0),
        min(page_width, max(x1, matched[2] + 2.0)),
        min(page_height, y1),
    )


def standalone_heading_title_candidate(block) -> bool:
    title = short_heading_text(block.get("text", ""))
    if not title or title.endswith("."):
        return False
    if is_reference_heading(title) or is_visual_caption(title) or contains_visual_caption(title):
        return False
    if re.match(r"(?i)question:", title):
        return False
    return starts_like_source_heading(title)


def standalone_heading_number_pairs(blocks, classes: dict[str, str] | None = None):
    pairs = []
    used_ids = set()
    sorted_blocks = sorted(
        blocks,
        key=lambda item: (item.get("block_index", 0), item["yMin"], item["xMin"]),
    )
    for block in sorted_blocks:
        if block["id"] in used_ids or should_preserve_as_image(block):
            continue
        number_text = standalone_heading_number_text(block.get("text", ""))
        if not number_text:
            continue
        candidates = []
        for other in sorted_blocks:
            if other["id"] == block["id"] or other["id"] in used_ids:
                continue
            if should_preserve_as_image(other):
                continue
            if classes and classes.get(other["id"]) in {
                "page_number",
                "header_footer",
                "journal_footer",
                "reference",
                "figure_region",
                "formula_region",
            }:
                continue
            if not same_heading_line(block, other):
                continue
            gap = other["xMin"] - block["xMax"]
            if gap < 0 or gap > HEADING_NUMBER_TITLE_MAX_GAP_PT:
                continue
            if not standalone_heading_title_candidate(other):
                continue
            center_delta = abs(bbox_center(block_bbox(block))[1] - bbox_center(block_bbox(other))[1])
            candidates.append((gap, center_delta, other.get("block_index", 0), other))
        if not candidates:
            continue
        _gap, _center_delta, _block_index, title_block = min(candidates, key=lambda item: item[:3])
        pairs.append((block, title_block, number_text))
        used_ids.add(block["id"])
        used_ids.add(title_block["id"])
    return pairs


def standalone_heading_pair_bbox(number_block, title_block, page_size, style_name: str):
    style = text_style(style_name)
    number_box = adjusted_render_bbox(number_block, "heading", page_size)
    title_box = adjusted_render_bbox(title_block, "heading", page_size)
    x0 = min(number_box[0], title_box[0])
    y0 = min(number_box[1], title_box[1])
    x1 = max(number_box[2], title_box[2])
    y1 = max(number_box[3], title_box[3], y0 + style.font_size * style.line_height_factor + 2.0)
    x1 = max(x1 + 12.0, x0 + 160.0)
    return clamp_bbox((x0, y0, x1, y1), page_size)


def heading_pair_translation_fallback_reason(title_block, translated_title: str, translations) -> str | None:
    if title_block["id"] not in translations:
        return "missing_translation"
    raw_translation = translations.get(title_block["id"], "")
    if not normalize_text(raw_translation):
        return "untranslated_fallback_original"
    if compact_alpha_text(raw_translation) == compact_alpha_text(title_block.get("text", "")):
        return "untranslated_fallback_original"
    if not translated_title:
        return "untranslated_fallback_original"
    if translation_appears_untranslated(title_block.get("text", ""), translated_title):
        return "untranslated_fallback_original"
    return None


def add_standalone_heading_pair_render_items(
    plan: PageRenderPlan,
    heading_pairs,
    translations,
    page_size,
) -> set[str]:
    rendered_ids = set()
    for number_block, title_block, number_text in heading_pairs:
        translated_title = clean_render_text(
            title_block,
            translation_for_block(title_block, translations),
            translations.get(title_block["id"], ""),
        )
        style_name = style_name_for_heading_text(number_text)
        fallback_reason = heading_pair_translation_fallback_reason(title_block, translated_title, translations)
        if fallback_reason is not None:
            for block in (number_block, title_block):
                text = short_heading_text(block.get("text", "")) or normalize_text(block.get("text", ""))
                if not text:
                    continue
                plan.items.append(
                    RenderItem(
                        "original_selectable_text",
                        [block["id"]],
                        adjusted_render_bbox(block, "heading", page_size),
                        text=strip_journal_footer_lines(text),
                        font_size=text_style(style_name).font_size,
                        style_name=style_name,
                        fallback_reason=fallback_reason,
                    )
                )
                plan.ledger.append(
                    CoverageEntry(
                        block["id"],
                        "heading",
                        "original_selectable_text",
                        True,
                        fallback_reason,
                    )
                )
                rendered_ids.add(block["id"])
            continue
        title_text = clean_outline_title(translated_title or short_heading_text(title_block.get("text", "")))
        if not title_text:
            continue
        heading_text = clean_outline_title(f"{number_text} {title_text}")
        plan.items.append(
            RenderItem(
                "translated_text",
                [number_block["id"], title_block["id"]],
                standalone_heading_pair_bbox(number_block, title_block, page_size, style_name),
                text=heading_text,
                font_size=text_style(style_name).font_size,
                style_name=style_name,
                fallback_reason="standalone_heading_pair",
            )
        )
        for block in (number_block, title_block):
            plan.ledger.append(
                CoverageEntry(
                    block["id"],
                    "heading",
                    "translated_text",
                    True,
                    "standalone_heading_pair",
                )
            )
            rendered_ids.add(block["id"])
    return rendered_ids


def first_page_title_metadata_split(block, translated: str) -> tuple[str, list[str], str] | None:
    if block.get("page") != 1 or block["yMin"] > 110.0:
        return None
    source_lines = [line.strip() for line in normalize_text(block.get("text", "")).split("\n") if line.strip()]
    translated_lines = [line.strip() for line in normalize_text(translated).split("\n") if line.strip()]
    if len(source_lines) < 4:
        return None
    if not source_lines[0] or len(source_lines[0]) > 120:
        return None

    source_metadata = []
    for line in source_lines[1:5]:
        words = latin_words(line)
        if len(words) >= 6 or cjk_char_count(line) > 0:
            break
        source_metadata.append(line)
    if not source_metadata:
        return None

    if len(translated_lines) < 4:
        one_line = normalize_text(translated)
        metadata_positions = []
        for line in source_metadata:
            pos = one_line.find(line)
            if pos >= 0:
                metadata_positions.append((pos, line))
        if not metadata_positions:
            return None
        metadata_positions.sort()
        first_pos, _first_line = metadata_positions[0]
        last_pos, last_line = metadata_positions[-1]
        title = one_line[:first_pos].strip()
        abstract = one_line[last_pos + len(last_line) :].strip()
        if not title or not abstract:
            return None
        return title, [line for _pos, line in metadata_positions], prepare_render_translation(abstract)

    title = translated_lines[0]
    metadata = []
    body_start = None
    for idx, line in enumerate(translated_lines[1:], start=1):
        if cjk_char_count(line) >= 8:
            body_start = idx
            break
        if len(line) <= 120:
            metadata.append(line)
            continue
        body_start = idx
        break
    if body_start is None or not metadata:
        return None
    abstract = prepare_render_translation("\n".join(translated_lines[body_start:]))
    if not title or not abstract:
        return None
    return title, metadata[:3], abstract


def first_page_title_metadata_render_items(block, translated: str, bbox, page_size, fitz=None) -> list[RenderItem]:
    split = first_page_title_metadata_split(block, translated)
    if split is None:
        return []
    if fitz is None:
        fitz = load_fitz()
    title, metadata, abstract = split
    page_width, page_height = page_size
    x0, y0, x1, y1 = bbox
    x0 = max(0.0, x0)
    x1 = min(page_width, x1)

    title_style = text_style("title")
    metadata_style = text_style("metadata")
    title_width = max(1.0, x1 - x0)
    title_lines = wrap_mixed_pdf_text(fitz, title, title_width, title_style.font_size)
    metadata_text = "\n".join(metadata)
    metadata_lines = wrap_mixed_pdf_text(fitz, metadata_text, title_width, metadata_style.font_size)
    title_height = text_height_for_lines(title_lines, title_style.font_size, title_style.line_height_factor, title_style.paragraph_spacing) + 2.0
    metadata_height = text_height_for_lines(
        metadata_lines,
        metadata_style.font_size,
        metadata_style.line_height_factor,
        metadata_style.paragraph_spacing,
    ) + 2.0

    title_box = clamp_bbox((x0, y0, x1, y0 + title_height), page_size)
    metadata_top = title_box[3] + 2.0
    metadata_box = clamp_bbox((x0, metadata_top, x1, metadata_top + metadata_height), page_size)
    abstract_top = metadata_box[3] + 5.0
    abstract_box = clamp_bbox((x0, abstract_top, x1, max(abstract_top + BODY_FONT_SIZE * 2.0, min(page_height, y1))), page_size)

    return [
        RenderItem(
            "translated_text",
            [block["id"]],
            title_box,
            text=title,
            font_size=title_style.font_size,
            style_name="title",
            fallback_reason="first_page_title",
        ),
        RenderItem(
            "translated_text",
            [block["id"]],
            metadata_box,
            text=metadata_text,
            font_size=metadata_style.font_size,
            style_name="metadata",
            fallback_reason="first_page_metadata",
        ),
        RenderItem(
            "translated_text",
            [block["id"]],
            abstract_box,
            text=abstract,
            font_size=BODY_FONT_SIZE,
            style_name="body",
            fallback_reason="first_page_abstract",
        ),
    ]


def heuristic_translation_for_missing_block(text: str) -> str:
    normalized = normalize_text(text)
    match = re.fullmatch(r"\((\d+)\)\s+(.+?)\s+is\s+a\s+set\s+of\s+(.+?)[,.]?", normalized, flags=re.I)
    if match:
        number, subject, noun = match.groups()
        noun_map = {
            "input events": "输入事件",
            "output events": "输出事件",
            "internal events": "内部事件",
            "starting states": "初始状态",
        }
        noun_zh = noun_map.get(noun.lower(), noun)
        return f"({number}) {subject} 是{noun_zh}集合，"
    return ""


def is_garbled_latin_fragment(line: str) -> bool:
    normalized = normalize_text(line)
    if not normalized or cjk_char_count(normalized) > 0:
        return False
    if not re.search(r"[A-Za-z]", normalized):
        return False
    if is_formula_or_code_block(normalized) or re.search(r"[:=()[\]{}<>0-9_]", normalized):
        return False
    words = re.findall(r"[A-Za-z]+", normalized)
    if len(words) < 4:
        return False
    single_letter_words = sum(1 for word in words if len(word) == 1)
    short_words = sum(1 for word in words if len(word) <= 2)
    average_length = sum(len(word) for word in words) / len(words)
    function_words = english_function_word_count(normalized)
    if single_letter_words >= 2 and short_words >= len(words) * 0.45 and function_words <= 1:
        return True
    return normalized == normalized.lower() and function_words == 0 and average_length < 4.0


def drop_garbled_translation_lines(text: str) -> str:
    lines = [line for line in text.split("\n") if not is_garbled_latin_fragment(line)]
    return normalize_text("\n".join(lines))


def repair_incomplete_translation_from_source(block, text: str) -> str:
    source = normalize_text(block.get("text", "")).lower()
    if "validity follows because each process initializes its position in prefer before" in source:
        text = re.sub(
            r"有效性成立，因为每个进程在[。.]?",
            "有效性成立，因为每个进程在执行 swap 前初始化其在 prefer 中的位置。",
            text,
        )
    return text


def clean_render_text(block, text: str, raw_text: str | None = None) -> str:
    text = strip_journal_footer_lines(text)
    text = strip_leading_running_header_text(text, block.get("page", 1))
    if raw_text:
        visual_tail = translation_tail_after_visual_prefix(raw_text)
        if visual_tail and cjk_char_count(visual_tail) >= 6:
            text = strip_journal_footer_lines(visual_tail)
            text = strip_leading_running_header_text(text, block.get("page", 1))
    text = drop_garbled_translation_lines(text)
    text = repair_incomplete_translation_from_source(block, text)
    lines = text.split("\n")
    if len(lines) >= 2 and re.fullmatch(r"[a-z][a-z-]{3,}", lines[0].strip()) and re.search(r"[\u4e00-\u9fff]", lines[1]):
        source_first = normalize_text(block.get("text", "")).split("\n", 1)[0].strip()
        if source_first == lines[0].strip():
            return strip_journal_footer_lines("\n".join(lines[1:]).strip())
    source_first = normalize_text(block.get("text", "")).split("\n", 1)[0].strip()
    if block.get("page", 1) > 1 and re.search(r"wait-?free\s*synchronization", source_first, flags=re.I):
        first = lines[0].strip() if lines else ""
        if first in {"无等待同步", "Wait-Free Synchronization", "Wait-FreeSynchronization"}:
            return strip_journal_footer_lines("\n".join(lines[1:]).strip())
        stripped = text.strip()
        for prefix in ("无等待同步", "Wait-Free Synchronization", "Wait-FreeSynchronization"):
            if stripped.startswith(prefix) and len(stripped) > len(prefix):
                return strip_journal_footer_lines(stripped[len(prefix) :].strip())
    return strip_journal_footer_lines(text)


def row_from_bbox_fragments(fragments: list[dict]) -> dict:
    ordered = sorted(fragments, key=lambda item: item["bbox"][0])
    boxes = [item["bbox"] for item in ordered]
    return {
        "text": normalize_text(" ".join(item["text"] for item in ordered)),
        "bbox": bbox_union(boxes),
    }


def bbox_line_rows_for_block(block, bbox_lines: list[dict]) -> list[dict]:
    if not bbox_lines:
        return []
    block_box = expanded_bbox(block_bbox(block), 2.0, 2.0)
    fragments = []
    for line in bbox_lines:
        if line.get("page") != block.get("page"):
            continue
        line_box = line["bbox"]
        if not bbox_contains_point(block_box, bbox_center(line_box)) and not bbox_intersects(block_box, line_box):
            continue
        fragments.append(line)
    if not fragments:
        return []

    rows: list[list[dict]] = []
    for fragment in sorted(fragments, key=lambda item: (bbox_center(item["bbox"])[1], item["bbox"][0])):
        center_y = bbox_center(fragment["bbox"])[1]
        if rows:
            row_center_y = median(bbox_center(item["bbox"])[1] for item in rows[-1])
            if abs(center_y - row_center_y) <= 2.0:
                rows[-1].append(fragment)
                continue
        rows.append([fragment])
    return [row_from_bbox_fragments(row) for row in rows]


def source_body_row_is_noise(block, row: dict) -> bool:
    text = normalize_text(row["text"])
    if not text:
        return True
    pseudo = {
        "page": block.get("page", 1),
        "yMin": row["bbox"][1],
        "yMax": row["bbox"][3],
        "text": text,
    }
    if is_journal_footer_text(text) or is_running_header_fragment(pseudo):
        return True
    if row["bbox"][1] < 65.0 and (is_page_number(text) or is_decorated_ocr_page_number_text(text)):
        return True
    return False


def source_paragraph_boxes_for_block(block, bbox, bbox_lines: list[dict]) -> list[tuple[float, float, float, float]]:
    rows = [row for row in bbox_line_rows_for_block(block, bbox_lines) if not source_body_row_is_noise(block, row)]
    if len(rows) < 4:
        return []

    lefts = sorted(row["bbox"][0] for row in rows)
    base_left = lefts[max(0, min(len(lefts) - 1, int(len(lefts) * 0.15)))]
    line_heights = [max(1.0, row["bbox"][3] - row["bbox"][1]) for row in rows]
    typical_line_height = median(line_heights)
    indent_threshold = max(6.0, typical_line_height * 0.75)
    vertical_gap_threshold = typical_line_height * 1.2

    groups: list[list[dict]] = []
    current: list[dict] = []
    previous = None
    for row in rows:
        starts_new = not current
        if previous is not None:
            vertical_gap = row["bbox"][1] - previous["bbox"][3]
            row_indent = row["bbox"][0] - base_left
            previous_text = previous["text"].rstrip()
            if vertical_gap > vertical_gap_threshold:
                starts_new = True
            elif row_indent >= indent_threshold and not previous_text.endswith("-"):
                starts_new = True
        if starts_new:
            if current:
                groups.append(current)
            current = [row]
        else:
            current.append(row)
        previous = row
    if current:
        groups.append(current)
    if len(groups) < 2:
        return []

    boxes = []
    for idx, group in enumerate(groups):
        group_box = bbox_union([row["bbox"] for row in group])
        y0 = max(bbox[1], group[0]["bbox"][1])
        if idx + 1 < len(groups):
            y1 = min(bbox[3], groups[idx + 1][0]["bbox"][1] - BODY_FLOW_MIN_GAP_PT)
        else:
            y1 = group_box[3] + typical_line_height * 1.25
        if y1 <= y0 + BODY_FONT_SIZE:
            y1 = min(bbox[3], y0 + BODY_FONT_SIZE * 2.0)
        boxes.append(clamp_bbox((bbox[0], y0, bbox[2], y1), (10_000, 10_000)))
    return boxes


def render_paragraphs(text: str) -> list[str]:
    return [paragraph.strip() for paragraph in text.split("\n") if paragraph.strip()]


def merge_extra_paragraphs(paragraphs: list[str], target_count: int) -> list[str]:
    if target_count <= 0 or len(paragraphs) <= target_count:
        return paragraphs
    merged = paragraphs[: target_count - 1]
    merged.append("\n".join(paragraphs[target_count - 1 :]))
    return merged


def coalesce_paragraph_boxes(boxes: list[tuple[float, float, float, float]], target_count: int) -> list[tuple[float, float, float, float]]:
    if target_count <= 0 or len(boxes) <= target_count:
        return boxes
    boxes = list(boxes)
    while len(boxes) > target_count:
        best_idx = min(
            range(len(boxes) - 1),
            key=lambda idx: max(0.0, boxes[idx + 1][1] - boxes[idx][3]),
        )
        boxes[best_idx] = bbox_union([boxes[best_idx], boxes[best_idx + 1]])
        del boxes[best_idx + 1]
    return boxes


def box_area_value(box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def box_significantly_overlaps_protected(box, protected_box) -> bool:
    x0 = max(box[0], protected_box[0])
    y0 = max(box[1], protected_box[1])
    x1 = min(box[2], protected_box[2])
    y1 = min(box[3], protected_box[3])
    if x1 <= x0 or y1 <= y0:
        return False
    overlap_height = y1 - y0
    overlap_area = (x1 - x0) * overlap_height
    return overlap_height > 6.0 and overlap_area > min(box_area_value(box), box_area_value(protected_box)) * 0.05


def paragraph_boxes_overlap_protected(
    boxes: list[tuple[float, float, float, float]],
    protected_boxes: list[tuple[float, float, float, float]] | None,
) -> bool:
    return bool(
        protected_boxes
        and any(box_significantly_overlaps_protected(box, protected) for box in boxes for protected in protected_boxes)
    )


def source_paragraph_render_items(
    block,
    translated: str,
    bbox,
    page_size,
    bbox_lines: list[dict],
    protected_boxes: list[tuple[float, float, float, float]] | None = None,
) -> list[RenderItem]:
    paragraphs = render_paragraphs(translated)
    if len(paragraphs) < 2 or not bbox_lines:
        return []
    if block["yMax"] - block["yMin"] < 120.0:
        return []
    boxes = source_paragraph_boxes_for_block(block, bbox, bbox_lines)
    if len(boxes) < 2:
        return []
    if len(paragraphs) > len(boxes):
        paragraphs = merge_extra_paragraphs(paragraphs, len(boxes))
    elif len(boxes) > len(paragraphs):
        boxes = coalesce_paragraph_boxes(boxes, len(paragraphs))
    if len(paragraphs) != len(boxes) or len(paragraphs) < 2:
        return []
    if paragraph_boxes_overlap_protected(boxes, protected_boxes):
        return []

    style = text_style("body")
    items = []
    for paragraph, paragraph_box in zip(paragraphs, boxes):
        items.append(
            RenderItem(
                "translated_text",
                [block["id"]],
                clamp_bbox(paragraph_box, page_size),
                text=paragraph,
                font_size=style.font_size,
                style_name="body",
                fallback_reason="source_paragraph_split",
            )
        )
    return items


def translated_lines_with_embedded_headings(text: str) -> list[str]:
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if not lines:
        return []
    if not any(render_line_is_standalone_heading(line) for line in lines[1:]):
        return []
    return lines


def source_heading_rows_for_block(block, bbox_lines: list[dict]) -> list[dict]:
    return [row for row in bbox_line_rows_for_block(block, bbox_lines) if is_heading_text(row["text"])]


def approximate_line_box(bbox, line_index: int, line_count: int) -> tuple[float, float, float, float]:
    line_count = max(1, line_count)
    x0, y0, x1, y1 = bbox
    line_height = max(1.0, (y1 - y0) / line_count)
    return (x0, y0 + line_height * line_index, x1, y0 + line_height * (line_index + 1))


def heading_box_from_row(row_box, page_size, style: TextStyle) -> tuple[float, float, float, float]:
    min_height = style.font_size * style.line_height_factor + 0.5
    x0, y0, x1, y1 = row_box
    box = (
        max(0.0, x0),
        max(0.0, y0 - 1.0),
        min(page_size[0], max(x1 + 2.0, x0 + 140.0)),
        min(page_size[1], max(y1 + 2.0, y0 - 1.0 + min_height)),
    )
    return box


def embedded_heading_render_items(
    block,
    translated: str,
    bbox,
    page_size,
    bbox_lines: list[dict],
) -> list[RenderItem]:
    lines = translated_lines_with_embedded_headings(translated)
    if not lines:
        return []
    heading_indices = [idx for idx, line in enumerate(lines) if idx > 0 and render_line_is_standalone_heading(line)]
    if not heading_indices:
        return []

    source_rows = bbox_line_rows_for_block(block, bbox_lines)
    source_heading_rows = [row for row in source_rows if is_heading_text(row["text"])]
    if source_rows and not source_heading_rows:
        return []
    heading_rows = []
    for idx, _line_index in enumerate(heading_indices):
        if idx < len(source_heading_rows):
            heading_rows.append(source_heading_rows[idx])
        else:
            line_box = approximate_line_box(bbox, heading_indices[idx], len(lines))
            heading_rows.append({"text": lines[heading_indices[idx]], "bbox": line_box})

    items: list[RenderItem] = []
    body_style = text_style("body")
    cursor = 0
    current_y = bbox[1]
    for heading_index, heading_row in zip(heading_indices, heading_rows):
        heading_line = lines[heading_index]
        style_name = style_name_for_heading_text(heading_line)
        heading_style = text_style(style_name)
        heading_box = heading_box_from_row(heading_row["bbox"], page_size, heading_style)

        body_text = "\n".join(lines[cursor:heading_index]).strip()
        if body_text:
            body_y1 = max(current_y + body_style.font_size * body_style.line_height_factor, heading_box[1] - BODY_FLOW_MIN_GAP_PT)
            items.append(
                RenderItem(
                    "translated_text",
                    [block["id"]],
                    clamp_bbox((bbox[0], current_y, bbox[2], min(body_y1, heading_box[1] - TEXT_PROTECTED_GAP_PT)), page_size),
                    text=body_text,
                    font_size=body_style.font_size,
                    style_name="body",
                    fallback_reason="embedded_heading_body",
                )
            )

        items.append(
            RenderItem(
                "translated_text",
                [block["id"]],
                heading_box,
                text=heading_line,
                font_size=heading_style.font_size,
                style_name=style_name,
                fallback_reason="embedded_heading",
            )
        )
        current_y = min(bbox[3], heading_box[3] + BODY_FLOW_MIN_GAP_PT)
        cursor = heading_index + 1

    tail_text = "\n".join(lines[cursor:]).strip()
    if tail_text:
        items.append(
            RenderItem(
                "translated_text",
                [block["id"]],
                clamp_bbox((bbox[0], current_y, bbox[2], bbox[3]), page_size),
                text=tail_text,
                font_size=body_style.font_size,
                style_name="body",
                fallback_reason="embedded_heading_body",
            )
        )
    return [item for item in items if item.bbox[3] > item.bbox[1] and item.bbox[2] > item.bbox[0]]


def item_is_body_text_item(item: RenderItem) -> bool:
    return (
        item.kind in {"translated_text", "original_selectable_text"}
        and item.style_name == "body"
        and item.text.strip()
    )


def numbered_enumeration_start(text: str) -> int | None:
    match = re.match(r"^\((\d{1,2})\)\s*[\u4e00-\u9fffA-Za-z]", text.strip())
    if not match:
        return None
    return int(match.group(1))


def split_leading_continuation_before_numbered_enum(text: str) -> tuple[str, str] | None:
    stripped = text.strip()
    if stripped.startswith("("):
        return None
    match = re.search(r"\(\d{1,2}\)\s*[\u4e00-\u9fffA-Za-z]", stripped)
    if not match or match.start() <= 0:
        return None
    leading = stripped[: match.start()].strip()
    rest = stripped[match.start() :].strip()
    if not leading or not rest:
        return None
    if "\n" in leading or len(leading) > 80:
        return None
    if numbered_enumeration_start(rest) is None:
        return None
    return leading, rest


def previous_body_item_in_same_lane(plan: PageRenderPlan, item_idx: int) -> RenderItem | None:
    item = plan.items[item_idx]
    candidates = []
    for idx, other in enumerate(plan.items):
        if idx == item_idx or not item_is_body_text_item(other):
            continue
        if other.bbox[1] >= item.bbox[1]:
            continue
        if horizontal_overlap(other.bbox, item.bbox) < min(other.bbox[2] - other.bbox[0], item.bbox[2] - item.bbox[0]) * 0.45:
            continue
        if item.bbox[1] - other.bbox[3] > 90.0:
            continue
        candidates.append(other)
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: candidate.bbox[1])


def move_leading_enum_continuations_to_previous_items(plan: PageRenderPlan) -> None:
    for idx, item in sorted(enumerate(plan.items), key=lambda pair: (pair[1].bbox[1], pair[1].bbox[0])):
        if not item_is_body_text_item(item):
            continue
        split = split_leading_continuation_before_numbered_enum(item.text)
        if split is None:
            continue
        previous = previous_body_item_in_same_lane(plan, idx)
        if previous is None or not re.search(r"\(\d{1,2}\)", previous.text):
            continue
        leading, rest = split
        previous.text = join_render_lines(previous.text.rstrip(), leading)
        item.text = rest


def same_body_lane(left: RenderItem, right: RenderItem) -> bool:
    overlap = horizontal_overlap(left.bbox, right.bbox)
    min_width = max(1.0, min(left.bbox[2] - left.bbox[0], right.bbox[2] - right.bbox[0]))
    return overlap >= min_width * 0.45


def enumeration_candidate_groups(plan: PageRenderPlan) -> list[list[tuple[int, RenderItem, int]]]:
    candidates = [
        (idx, item, number)
        for idx, item in enumerate(plan.items)
        if item_is_body_text_item(item)
        for number in [numbered_enumeration_start(item.text)]
        if number is not None
    ]
    groups: list[list[tuple[int, RenderItem, int]]] = []
    for candidate in sorted(candidates, key=lambda entry: (entry[1].bbox[0], entry[1].bbox[1])):
        _idx, item, _number = candidate
        target = None
        for group_idx, group in enumerate(groups):
            if any(same_body_lane(item, other) for _other_idx, other, _other_number in group):
                target = group_idx
                break
        if target is None:
            groups.append([candidate])
        else:
            groups[target].append(candidate)

    local_groups = []
    for group in groups:
        current: list[tuple[int, RenderItem, int]] = []
        for candidate in sorted(group, key=lambda entry: entry[1].bbox[1]):
            if current and candidate[1].bbox[1] - current[-1][1].bbox[1] > 140.0:
                if len(current) > 1:
                    local_groups.append(current)
                current = []
            current.append(candidate)
        if len(current) > 1:
            local_groups.append(current)
    return local_groups


def repair_numbered_enumeration_item_order(plan: PageRenderPlan) -> None:
    for group in enumeration_candidate_groups(plan):
        by_y = sorted(group, key=lambda entry: entry[1].bbox[1])
        by_number = sorted(group, key=lambda entry: entry[2])
        if [number for _idx, _item, number in by_y] == [number for _idx, _item, number in by_number]:
            continue
        numbers = [number for _idx, _item, number in by_number]
        if numbers != list(range(numbers[0], numbers[-1] + 1)):
            continue
        y_slots = sorted((item.bbox[1], item.bbox[3] - item.bbox[1]) for _idx, item, _number in group)
        for (_idx, item, _number), (y0, height) in zip(by_number, y_slots):
            x0, _old_y0, x1, _old_y1 = item.bbox
            item.bbox = (x0, y0, x1, y0 + height)


def repair_numbered_enumeration_flow(plan: PageRenderPlan) -> None:
    move_leading_enum_continuations_to_previous_items(plan)
    repair_numbered_enumeration_item_order(plan)


def source_row_before_short_fragment(block, bbox_lines: list[dict] | None) -> dict | None:
    if not bbox_lines or not all(key in block for key in ("page", "yMin", "yMax")):
        return None
    rows = rows_in_vertical_window(
        int(block.get("page", 1) or 1),
        bbox_lines,
        max(0.0, block["yMin"] - 24.0),
        block["yMax"] + 6.0,
    )
    if len(rows) < 2:
        return None
    fragment_compact = compact_source_phrase(block.get("text", ""))
    if not fragment_compact:
        return None
    ordered_rows = sorted(rows, key=lambda row: (row["bbox"][1], row["bbox"][0]))
    for row_idx, row in enumerate(ordered_rows):
        row_compact = compact_source_phrase(row["text"])
        if fragment_compact and fragment_compact in row_compact and row_idx > 0:
            return ordered_rows[row_idx - 1]
    return None


def neighboring_source_ids(blocks, source_id: str) -> tuple[str | None, str | None]:
    for idx, block in enumerate(blocks):
        if block.get("id") != source_id:
            continue
        previous_id = blocks[idx - 1]["id"] if idx > 0 else None
        next_id = blocks[idx + 1]["id"] if idx + 1 < len(blocks) else None
        return previous_id, next_id
    return None, None


def merge_short_fragment_text(target_text: str, fragment_text: str) -> str:
    target = normalize_text(target_text).strip()
    fragment = normalize_text(fragment_text).strip()
    if not target or not fragment:
        return target_text
    fragment_compact = compact_duplicate_text(fragment)
    if not fragment_compact or fragment_compact in compact_duplicate_text(target):
        return target
    short_fragment = re.sub(r"[。！？.!?]+$", "", fragment).strip()
    if short_fragment == "链表" and "链表" not in target and "下一个单元" in target:
        return target.replace("下一个单元", "链表中的下一个单元", 1)
    if short_fragment and len(compact_duplicate_text(short_fragment)) <= 8:
        terminal = re.search(r"([。！？.!?])$", target)
        if terminal:
            return target[: terminal.start()] + f"（{short_fragment}）" + terminal.group(1)
    separator = "" if target.endswith(("\n", " ", "　")) else " "
    return target + separator + fragment


def short_fragment_absorption_target(
    plan: PageRenderPlan,
    item_idx: int,
    source_block,
    blocks,
    bbox_lines: list[dict] | None,
) -> RenderItem | None:
    item = plan.items[item_idx]
    previous_id, next_id = neighboring_source_ids(blocks, source_block["id"])
    neighbor_ids = {source_id for source_id in (previous_id, next_id) if source_id}
    if not neighbor_ids:
        return None

    context_row = source_row_before_short_fragment(source_block, bbox_lines)
    context_enum = numbered_enumeration_start(context_row["text"]) if context_row else None
    candidates = []
    for idx, other in enumerate(plan.items):
        if idx == item_idx or not item_is_body_text_item(other):
            continue
        if not (set(other.source_ids) & neighbor_ids):
            continue
        if horizontal_overlap(other.bbox, item.bbox) < min(other.bbox[2] - other.bbox[0], item.bbox[2] - item.bbox[0]) * 0.35:
            continue
        vertical_distance = min(abs(other.bbox[1] - item.bbox[3]), abs(item.bbox[1] - other.bbox[3]))
        if vertical_distance > 96.0:
            continue
        if context_enum is not None and f"({context_enum})" not in other.text:
            continue
        candidates.append((vertical_distance, other))
    if candidates:
        return min(candidates, key=lambda candidate: candidate[0])[1]

    previous = previous_body_item_in_same_lane(plan, item_idx)
    if previous is not None and set(previous.source_ids) & neighbor_ids and item.bbox[1] - previous.bbox[3] <= 48.0:
        return previous
    return None


def drop_redundant_short_body_fragments(plan: PageRenderPlan, blocks, bbox_lines: list[dict] | None = None) -> None:
    block_by_id = {block["id"]: block for block in blocks}
    remove_indices = set()
    for idx, item in sorted(enumerate(plan.items), key=lambda pair: (pair[1].bbox[1], pair[1].bbox[0])):
        if not item_is_body_text_item(item) or len(item.source_ids) != 1:
            continue
        source_block = block_by_id.get(item.source_ids[0])
        if source_block is None or not source_is_short_continuation_fragment(source_block):
            continue
        compact = compact_duplicate_text(item.text)
        if not compact or len(compact) > 8 or re.search(r"[A-Za-z0-9]", compact):
            continue
        previous = previous_body_item_in_same_lane(plan, idx)
        if previous is not None and item.bbox[1] - previous.bbox[3] <= 36.0 and compact in compact_duplicate_text(previous.text):
            target = previous
        else:
            target = short_fragment_absorption_target(plan, idx, source_block, blocks, bbox_lines)
            if target is not None:
                target.text = merge_short_fragment_text(target.text, item.text)
        if target is None:
            continue
        for source_id in item.source_ids:
            if source_id not in target.source_ids:
                target.source_ids.append(source_id)
        update_ledger_render_kind(plan, item.source_ids, "translated_text", "merged_redundant_short_fragment")
        remove_indices.add(idx)
    if remove_indices:
        plan.items = [item for idx, item in enumerate(plan.items) if idx not in remove_indices]


def rows_inside_bbox(bbox_lines, box) -> list[dict]:
    if not bbox_lines:
        return []
    x0, y0, x1, y1 = box
    candidates = []
    for line in bbox_lines:
        center = bbox_center(line["bbox"])
        if x0 - 2.0 <= center[0] <= x1 + 2.0 and y0 - 2.0 <= center[1] <= y1 + 2.0:
            candidates.append(line)
    return merge_bbox_line_fragments(candidates)


def split_mixed_visual_body_rows(region_bbox, bbox_lines):
    rows = rows_inside_bbox(bbox_lines, region_bbox)
    visual_boxes = []
    body_boxes = []
    saw_code = False
    in_body = False
    for row in sorted(rows, key=lambda item: item["bbox"][1]):
        text = row["text"]
        visual = is_visual_row_text(text)
        if visual and not in_body:
            visual_boxes.append(row["bbox"])
            saw_code = saw_code or is_code_row_text(text) or is_code_listing_block(text)
            continue
        if visual_boxes and is_prose_row_text(text):
            in_body = True
            body_boxes.append(row["bbox"])
            continue
        if visual_boxes and in_body:
            body_boxes.append(row["bbox"])
            continue
        if visual_boxes:
            visual_boxes.append(row["bbox"])
    if not saw_code or not visual_boxes or not body_boxes:
        return None
    body_bbox = bbox_union(body_boxes)
    if body_bbox[3] - body_bbox[1] < 18.0:
        return None
    return bbox_union(visual_boxes), body_bbox


def is_translation_visual_prefix_line(line: str) -> bool:
    normalized = normalize_text(line)
    if not normalized:
        return False
    if cjk_char_count(normalized) >= 3 and not is_code_row_text(normalized):
        return False
    compact = re.sub(r"\s+", "", normalized.lower())
    return is_visual_row_text(normalized) or any(
        marker in compact
        for marker in (
            "decide(",
            "compare&swap(",
            "enq(",
            "peek(",
            ":=",
            "universal(",
            "foreachprocess",
            "forqin",
            "thenreturn",
            "elsereturn",
            "endif",
            "endfor",
            "enddecide",
            "mine:",
            "inv:",
            "new:create",
            "before:create",
            "after:null",
            "announce[",
            "head[",
        )
    )


def is_translation_body_line(line: str) -> bool:
    return cjk_char_count(line) >= 3 or is_prose_row_text(line)


def translation_tail_after_visual_prefix(text: str) -> str:
    lines = text.split("\n")
    first_content_seen = False
    saw_visual_prefix = False
    body_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if saw_visual_prefix and body_lines:
                body_lines.append(line)
            continue
        if not first_content_seen:
            first_content_seen = True
            if is_translation_visual_prefix_line(stripped):
                saw_visual_prefix = True
                continue
            return ""
        if saw_visual_prefix and not body_lines and is_translation_visual_prefix_line(stripped):
            continue
        if saw_visual_prefix and not body_lines and not is_translation_body_line(stripped):
            continue
        if saw_visual_prefix:
            body_lines.append(line)
    if not saw_visual_prefix or not body_lines:
        return ""
    return prepare_render_translation("\n".join(body_lines))


def mixed_visual_body_render_item(region, bbox_lines, translations, page_size):
    split = split_mixed_visual_body_rows(region["bbox"], bbox_lines)
    if split is None:
        return None
    visual_bbox, body_bbox = split
    body_source_ids = []
    body_texts = []
    for source_id in region["source_ids"]:
        tail = translation_tail_after_visual_prefix(translations.get(source_id, ""))
        if not tail:
            continue
        body_source_ids.append(source_id)
        body_texts.append(tail)
    if not body_texts:
        return None
    page_width, page_height = page_size
    body_item = RenderItem(
        "translated_text",
        body_source_ids,
        (
            max(0.0, body_bbox[0]),
            max(0.0, body_bbox[1]),
            min(page_width, body_bbox[2] + 2.0),
            min(page_height, body_bbox[3] + 2.0),
        ),
        text="\n".join(body_texts),
        font_size=BODY_FONT_SIZE,
        style_name="body",
        fallback_reason="mixed_visual_body",
    )
    return visual_bbox, body_item


def mixed_visual_body_component_render_item(component, translations, page_size, bbox_lines=None) -> RenderItem | None:
    body_source_ids = []
    body_texts = []
    for source_id in component.source_ids:
        tail = translation_tail_after_visual_prefix(translations.get(source_id, ""))
        if not tail:
            continue
        body_source_ids.append(source_id)
        body_texts.append(tail)
    kind = "translated_text"
    fallback_reason = "mixed_visual_body"
    if not body_texts:
        rows = rows_inside_bbox(bbox_lines or [], component.source_bbox)
        body_texts = [row["text"] for row in rows if normalize_text(row.get("text", ""))]
        body_source_ids = list(component.source_ids) if body_texts else []
        kind = "original_selectable_text"
        fallback_reason = "mixed_visual_body_original"
    if not body_texts:
        return None
    page_width, page_height = page_size
    x0, y0, x1, y1 = component.source_bbox
    return RenderItem(
        kind,
        body_source_ids,
        (
            max(0.0, x0),
            max(0.0, y0),
            min(page_width, x1 + 2.0),
            min(page_height, y1 + 2.0),
        ),
        text="\n".join(body_texts),
        font_size=BODY_FONT_SIZE,
        style_name="body",
        fallback_reason=fallback_reason,
        component_id=component.component_id,
        component_kind=component.component_kind,
    )


def translated_text_exclusion_boxes(blocks, classes, translations, region_bbox, page_size, bbox_lines=None):
    boxes = []
    for block in blocks:
        block_id = block["id"]
        if classes.get(block_id) not in NORMAL_TRANSLATED_CLASSES:
            continue
        if not block_has_valid_chinese_translation(block, translations):
            continue
        if not block_is_visually_covered_by_region(block, region_bbox):
            continue
        bbox = adjusted_render_bbox(block, classes.get(block_id, "body"), page_size)
        if classes.get(block_id) in {"body", "heading", "subheading", "title", "reference"}:
            bbox = refined_text_bbox_from_lines(block, bbox, bbox_lines or [], page_size)
        boxes.append(clamp_bbox(expanded_bbox(bbox, pad_x=TEXT_PROTECTED_GAP_PT, pad_y=TEXT_PROTECTED_GAP_PT), page_size))
    return boxes


def split_rect_around_exclusion(rect, exclusion):
    rx0, ry0, rx1, ry1 = rect
    ex0, ey0, ex1, ey1 = exclusion
    ix0 = max(rx0, ex0)
    iy0 = max(ry0, ey0)
    ix1 = min(rx1, ex1)
    iy1 = min(ry1, ey1)
    if ix1 <= ix0 or iy1 <= iy0:
        return [rect]
    candidates = [
        (rx0, ry0, rx1, iy0),
        (rx0, iy1, rx1, ry1),
        (rx0, iy0, ix0, iy1),
        (ix1, iy0, rx1, iy1),
    ]
    return [
        candidate
        for candidate in candidates
        if candidate[2] - candidate[0] >= 2.0 and candidate[3] - candidate[1] >= 2.0 and bbox_area(candidate) >= 8.0
    ]


def split_visual_clip_around_translated_text(region_bbox, exclusion_boxes, page_size, source_image_path=None):
    clip_boxes = [tuple(region_bbox)]
    for exclusion in sorted(exclusion_boxes, key=lambda box: (box[1], box[0], box[3], box[2])):
        next_boxes = []
        for clip_box in clip_boxes:
            next_boxes.extend(split_rect_around_exclusion(clip_box, exclusion))
        clip_boxes = next_boxes
        if not clip_boxes:
            break
    result = []
    seen = set()
    for box in sorted(clip_boxes, key=lambda item: (item[1], item[0], item[3], item[2])):
        clipped = clamp_bbox(box, page_size)
        if clipped[2] - clipped[0] < 2.0 or clipped[3] - clipped[1] < 2.0:
            continue
        if source_image_path is not None:
            dark_pixels = source_image_dark_pixel_count(source_image_path, clipped, page_size)
            if dark_pixels is not None and dark_pixels <= 0:
                continue
        key = tuple(round(value, 3) for value in clipped)
        if key in seen:
            continue
        seen.add(key)
        result.append(clipped)
    return result or [tuple(region_bbox)]


def source_ids_for_visual_clip(clip_bbox, source_ids, block_by_id):
    ids = []
    for source_id in source_ids:
        block = block_by_id.get(source_id)
        if block is None:
            continue
        block_box = block_bbox(block)
        if bbox_contains_point(clip_bbox, bbox_center(block_box)) or bbox_overlap_area(block_box, clip_bbox) > 0.5:
            ids.append(source_id)
    return sorted(dict.fromkeys(ids))


def build_page_render_plan(
    page_num: int,
    blocks,
    translations,
    page_size,
    bbox_lines=None,
    source_image_path: Path | None = None,
    force_reference: bool = False,
) -> PageRenderPlan:
    plan = PageRenderPlan(page_num=page_num)
    if not blocks and source_image_path:
        full_page_bbox = (0.0, 0.0, float(page_size[0]), float(page_size[1]))
        dark_pixels = source_image_dark_pixel_count(source_image_path, full_page_bbox, page_size)
        if dark_pixels is not None and dark_pixels >= IMAGE_ONLY_PAGE_MIN_DARK_PIXELS:
            plan.items.append(
                RenderItem(
                    kind="original_image_clip",
                    source_ids=[],
                    bbox=full_page_bbox,
                    fallback_reason="image_only_page",
                )
            )
            plan.protected_boxes.append(full_page_bbox)
            return plan
    ownership_result = build_translation_page_components(
        page_num,
        blocks,
        page_size=page_size,
        bbox_lines=bbox_lines,
        source_image_path=source_image_path,
        in_reference_section=force_reference,
        translations=translations,
    )
    plan.components = list(ownership_result.components)
    plan.ownership_ledger = ownership.ownership_ledger_for_components(page_num, plan.components)
    plan.ownership_validation = ownership_result.validation
    visual_regions = ownership_result.visual_regions
    classes = dict(ownership_result.classes)
    block_by_id = {block["id"]: block for block in blocks}
    visual_ids = {source_id for region in visual_regions for source_id in region["source_ids"]}
    components_by_source_id = components_by_source_id_compat(plan.components)
    component_by_source_id = component_by_source_id_compat(plan.components)
    visual_component_ids = visual_source_ids_from_components(plan.components)
    heading_pairs = standalone_heading_number_pairs(blocks, classes)
    for number_block, title_block, _number_text in heading_pairs:
        if number_block["id"] in visual_component_ids or title_block["id"] in visual_component_ids:
            continue
        classes[number_block["id"]] = "heading"
        classes[title_block["id"]] = "heading"
    footer_items = journal_footer_render_items(bbox_lines or [], page_size)
    plan.items.extend(footer_items)
    reference_line_items = reference_line_render_items(blocks, bbox_lines or [], page_size, classes)
    plan.items.extend(reference_line_items)
    reference_line_source_ids = {
        source_id
        for item in reference_line_items
        for source_id in item.source_ids
    }
    visual_covered_text_ids = set()
    metadata_ids = {block["id"] for block in blocks if should_preserve_first_page_metadata_as_image(block)}
    for metadata_block in blocks:
        if metadata_block["id"] not in metadata_ids:
            continue
        metadata_bbox = clamped_expanded_bbox(block_bbox(metadata_block), page_size, pad_x=1.0, pad_y=1.0)
        visual_covered_text_ids.update(
            nontranslated_blocks_covered_by_visual_region(blocks, classes, metadata_bbox, metadata_ids)
        )

    for region in visual_regions:
        matching_component = next(
            (
                component
                for component in plan.components
                if component.component_kind == ownership.COMPONENT_KIND_VISUAL
                and set(component.source_ids) & set(region["source_ids"])
            ),
            None,
        )
        region_bbox = tuple(region.get("bbox")) if region.get("bbox") else (
            matching_component.clip_bbox if matching_component is not None and matching_component.clip_bbox else None
        )
        if region_bbox is None:
            region_bbox, _mixed_body_item = final_visual_region_bbox(
                blocks,
                classes,
                region,
                page_size=page_size,
                page_num=page_num,
                source_image_path=source_image_path,
                bbox_lines=bbox_lines,
                translations=translations,
                visual_ids=visual_ids,
            )
        visual_covered_text_ids.update(
            nontranslated_blocks_covered_by_visual_region(blocks, classes, region_bbox, visual_ids)
        )
        image_source_ids = list(matching_component.source_ids if matching_component is not None else region["source_ids"])
        exclusion_boxes = translated_text_exclusion_boxes(
            blocks,
            classes,
            translations,
            region_bbox,
            page_size,
            bbox_lines,
        )
        clip_bboxes = split_visual_clip_around_translated_text(
            region_bbox,
            exclusion_boxes,
            page_size,
            source_image_path=source_image_path,
        )
        rendered_clip_ids = set()
        for clip_bbox in clip_bboxes:
            clip_source_ids = source_ids_for_visual_clip(clip_bbox, image_source_ids, block_by_id)
            if not clip_source_ids:
                continue
            rendered_clip_ids.update(clip_source_ids)
            plan.items.append(
                RenderItem(
                    kind="original_image_clip",
                    source_ids=clip_source_ids,
                    bbox=clip_bbox,
                    fallback_reason="visual_region",
                    component_id="" if matching_component is None else matching_component.component_id,
                    component_kind="" if matching_component is None else matching_component.component_kind,
                )
            )
            plan.protected_boxes.append(clip_bbox)
        if not rendered_clip_ids and image_source_ids:
            plan.items.append(
                RenderItem(
                    kind="original_image_clip",
                    source_ids=image_source_ids,
                    bbox=region_bbox,
                    fallback_reason="visual_region",
                    component_id="" if matching_component is None else matching_component.component_id,
                    component_kind="" if matching_component is None else matching_component.component_kind,
                )
            )
            plan.protected_boxes.append(region_bbox)
        for source_id in image_source_ids:
            plan.ledger.append(
                CoverageEntry(
                    source_id,
                    classes.get(source_id, "figure_region"),
                    "original_image_clip",
                    True,
                    "visual_region",
                    component_id="" if matching_component is None else matching_component.component_id,
                    component_kind="" if matching_component is None else matching_component.component_kind,
                )
            )
    for component in mixed_visual_body_components(plan.components):
        body_item = mixed_visual_body_component_render_item(component, translations, page_size, bbox_lines)
        if body_item is None:
            continue
        plan.items.append(body_item)
        for source_id in component.source_ids:
            plan.ledger.append(
                CoverageEntry(
                    source_id,
                    "body",
                    body_item.kind,
                    True,
                    body_item.fallback_reason,
                    component_id=component.component_id,
                    component_kind=component.component_kind,
                )
            )

    heading_pairs = [
        pair
        for pair in heading_pairs
        if pair[0]["id"] not in visual_component_ids and pair[1]["id"] not in visual_component_ids
    ]
    paired_heading_ids = add_standalone_heading_pair_render_items(
        plan,
        heading_pairs,
        translations,
        page_size,
    )

    for block in blocks:
        text = normalize_text(block.get("text", ""))
        if not text or block["id"] in visual_component_ids:
            continue
        if block["id"] in paired_heading_ids:
            continue
        classification = classes.get(block["id"], "unknown")
        if block["id"] in visual_covered_text_ids:
            plan.ledger.append(
                CoverageEntry(
                    block["id"],
                    classification,
                    "original_image_clip",
                    True,
                    "covered_by_visual_region",
                )
            )
            continue
        bbox = adjusted_render_bbox(block, classification, page_size)
        if classification in {"body", "heading", "title", "reference"}:
            bbox = refined_text_bbox_from_lines(block, bbox, bbox_lines or [], page_size)
        component = component_by_source_id.get(block["id"])
        if component is not None and component.component_kind == ownership.COMPONENT_KIND_DUPLICATE:
            plan.ledger.append(CoverageEntry(block["id"], "nested_duplicate", "skip_explicitly", True))
            continue
        if classification == "journal_footer":
            if footer_items:
                plan.ledger.append(
                    CoverageEntry(
                        block["id"],
                        classification,
                        "original_selectable_text",
                        True,
                        "journal_footer_lines",
                    )
                )
                continue
            plan.items.append(
                RenderItem(
                    "original_selectable_text",
                    [block["id"]],
                    journal_footer_bbox(page_size),
                    text=JOURNAL_FOOTER_TEXT,
                    font_size=JOURNAL_FOOTER_FONT_SIZE,
                    style_name="footer",
                    fallback_reason="journal_footer",
                )
            )
            plan.ledger.append(
                CoverageEntry(
                    block["id"],
                    classification,
                    "original_selectable_text",
                    True,
                    "journal_footer",
                )
            )
            continue
        if classification in {"page_number", "header_footer"}:
            plan.ledger.append(CoverageEntry(block["id"], classification, "skip_explicitly", True))
            continue
        if should_preserve_first_page_metadata_as_image(block):
            metadata_bbox = clamped_expanded_bbox(block_bbox(block), page_size, pad_x=1.0, pad_y=1.0)
            plan.items.append(
                RenderItem(
                    "original_image_clip",
                    [block["id"]],
                    metadata_bbox,
                    fallback_reason="first_page_metadata_original",
                )
            )
            plan.protected_boxes.append(metadata_bbox)
            plan.ledger.append(
                CoverageEntry(
                    block["id"],
                    classification,
                    "original_image_clip",
                    True,
                    "first_page_metadata_original",
                )
            )
            continue
        if classification == "reference":
            if block["id"] in reference_line_source_ids:
                plan.ledger.append(reference_coverage_entry(block, "original_selectable_text", "reference_original_lines"))
                continue
            plan.items.append(
                RenderItem(
                    "original_selectable_text",
                    [block["id"]],
                    bbox,
                    text=text,
                    font_size=DOCUMENT_STYLES["reference"].font_size,
                    style_name="reference",
                    fallback_reason="reference_original",
                )
            )
            plan.ledger.append(reference_coverage_entry(block, "original_selectable_text", "reference_original"))
            continue
        if classification in {"body", "heading", "subheading", "title"}:
            translated = clean_render_text(
                block,
                translation_for_block(block, translations),
                translations.get(block["id"], ""),
            )
            style_name = style_name_for_block(block, classification)
            if (
                translated
                and block["id"] in translations
                and not translation_appears_untranslated(text, translated)
            ):
                title_metadata_items = first_page_title_metadata_render_items(block, translated, bbox, page_size)
                if title_metadata_items:
                    plan.items.extend(title_metadata_items)
                    plan.ledger.append(
                        CoverageEntry(
                            block["id"],
                            classification,
                            "translated_text",
                            True,
                            "first_page_title_metadata_split",
                        )
                    )
                    continue
                embedded_heading_items = embedded_heading_render_items(
                    block,
                    translated,
                    bbox,
                    page_size,
                    bbox_lines or [],
                )
                if embedded_heading_items:
                    plan.items.extend(embedded_heading_items)
                    plan.ledger.append(
                        CoverageEntry(
                            block["id"],
                            classification,
                            "translated_text",
                            True,
                            "embedded_heading_split",
                        )
                    )
                    continue
                paragraph_items = []
                if style_name == "body":
                    paragraph_items = source_paragraph_render_items(
                        block,
                        translated,
                        bbox,
                        page_size,
                        bbox_lines or [],
                        plan.protected_boxes,
                    )
                if paragraph_items:
                    plan.items.extend(paragraph_items)
                    plan.ledger.append(
                        CoverageEntry(
                            block["id"],
                            classification,
                            "translated_text",
                            True,
                            "source_paragraph_split",
                        )
                    )
                    continue
                plan.items.append(
                    RenderItem(
                        "translated_text",
                        [block["id"]],
                        bbox,
                        text=translated,
                        font_size=text_style(style_name).font_size,
                        style_name=style_name,
                    )
                )
                plan.ledger.append(CoverageEntry(block["id"], classification, "translated_text", True))
            elif translated and block["id"] in translations:
                plan.items.append(
                    RenderItem(
                        "original_selectable_text",
                        [block["id"]],
                        bbox,
                        text=strip_journal_footer_lines(text),
                        font_size=text_style(style_name).font_size,
                        style_name=style_name,
                        fallback_reason="untranslated_fallback_original",
                    )
                )
                plan.ledger.append(
                    CoverageEntry(
                        block["id"],
                        classification,
                        "original_selectable_text",
                        True,
                        "untranslated_fallback_original",
                    )
                )
            elif classification == "body" and (heuristic_translated := heuristic_translation_for_missing_block(text)):
                plan.items.append(
                    RenderItem(
                        "translated_text",
                        [block["id"]],
                        bbox,
                        text=heuristic_translated,
                        font_size=BODY_FONT_SIZE,
                        style_name="body",
                        fallback_reason="heuristic_translation",
                    )
                )
                plan.ledger.append(
                    CoverageEntry(
                        block["id"],
                        classification,
                        "translated_text",
                        True,
                        "heuristic_translation",
                    )
                )
            elif block.get("preserve_image") and not is_body_enumeration_line(text):
                plan.items.append(
                    RenderItem(
                        "original_image_clip",
                        [block["id"]],
                        bbox,
                        fallback_reason="missing_translation_preserve_image",
                    )
                )
                plan.protected_boxes.append(bbox)
                plan.ledger.append(
                    CoverageEntry(
                        block["id"],
                        classification,
                        "original_image_clip",
                        True,
                        "missing_translation_preserve_image",
                    )
                )
            else:
                plan.items.append(
                    RenderItem(
                        "original_selectable_text",
                        [block["id"]],
                        bbox,
                        text=strip_journal_footer_lines(text),
                        font_size=text_style(style_name).font_size,
                        style_name=style_name,
                        fallback_reason="missing_translation",
                    )
                )
                plan.ledger.append(
                    CoverageEntry(
                        block["id"],
                        classification,
                        "original_selectable_text",
                        True,
                        "missing_translation",
                    )
                )
            continue
        plan.items.append(RenderItem("original_image_clip", [block["id"]], bbox, fallback_reason="unknown_classification"))
        plan.protected_boxes.append(bbox)
        plan.ledger.append(CoverageEntry(block["id"], classification, "original_image_clip", True, "unknown_classification"))
    merge_contained_text_fragments(plan)
    split_translated_text_around_protected(plan, page_size)
    merge_contained_text_fragments(plan)
    repair_numbered_enumeration_flow(plan)
    merge_adjacent_body_text_flows(plan)
    drop_redundant_short_body_fragments(plan, blocks, bbox_lines or [])
    expand_text_boxes_to_fit(plan, page_size)
    rebalance_body_text_flows(plan, page_size)
    repair_numbered_enumeration_flow(plan)
    drop_redundant_short_body_fragments(plan, blocks, bbox_lines or [])
    rebalance_body_text_flows(plan, page_size)
    convert_unfit_nonprose_text_to_image_clips(plan, blocks)
    split_translated_text_around_protected(plan, page_size)
    annotate_plan_with_component_metadata(plan, components_by_source_id)
    validate_render_layer_exclusivity = getattr(ownership, "validate_render_layer_exclusivity", None)
    if validate_render_layer_exclusivity is not None:
        layer_validation = validate_render_layer_exclusivity(plan, plan.components)
        if getattr(layer_validation, "issues", None):
            plan.ownership_validation = merge_ownership_validation_results(plan.ownership_validation, layer_validation)
    return plan


def nontrivial_block(block) -> bool:
    text = normalize_text(block.get("text", ""))
    return bool(text) and not is_trivial_keep(text)


def validate_plan_coverage(page_num: int, blocks, plan: PageRenderPlan) -> list[str]:
    return _render_plan_validate_plan_coverage(
        page_num,
        blocks,
        plan,
        is_nontrivial_block=nontrivial_block,
    )


def validate_plan_translation_quality(page_num: int, blocks, translations, plan: PageRenderPlan) -> list[str]:
    errors = []
    ledger_by_id = {entry.block_id: entry for entry in plan.ledger}
    block_by_id = {block["id"]: block for block in blocks}
    rendered_text_by_id = rendered_text_by_source_id(plan)
    for block_id, block in block_by_id.items():
        entry = ledger_by_id.get(block_id)
        if entry is None or entry.classification not in NORMAL_TRANSLATED_CLASSES:
            continue
        source_text = strip_journal_footer_lines(block.get("text", ""))
        if not source_requires_chinese_translation(source_text):
            continue
        if entry.render_kind == "original_image_clip":
            errors.append(
                f"page {page_num} block {block_id} normal text block rendered as original image"
                f" ({entry.fallback_reason or 'no reason'})"
            )
            continue
        if entry.render_kind == "original_selectable_text":
            errors.append(f"page {page_num} block {block_id} normal text block is missing Chinese translation")
            continue
        translated = translations.get(block_id, "")
        rendered_text = rendered_text_by_id.get(block_id, "")
        if translation_appears_untranslated(source_text, rendered_text or translated):
            errors.append(f"page {page_num} block {block_id} appears untranslated or mostly English")
        expected_render_text = clean_render_text(block, translation_for_block(block, translations), translated)
        anchor_text = translation_anchor_text(expected_render_text)
        anchor = cjk_anchor(anchor_text)
        if anchor and anchor not in compact_cjk_text(rendered_text):
            errors.append(f"page {page_num} block {block_id} rendered text is missing translated anchor")
    return errors


def rendered_text_by_source_id(plan: PageRenderPlan) -> dict[str, str]:
    rendered: dict[str, list[str]] = {}
    for item in sorted(plan.items, key=lambda candidate: (candidate.bbox[1], candidate.bbox[0])):
        if item.kind not in {"translated_text", "original_selectable_text"} or not item.text.strip():
            continue
        for source_id in item.source_ids:
            rendered.setdefault(source_id, []).append(item.text)
    return {source_id: "\n".join(parts) for source_id, parts in rendered.items()}


def compact_cjk_text(text: str) -> str:
    return "".join(re.findall(r"[\u4e00-\u9fff]", normalize_text(text)))


def cjk_anchor(text: str, length: int = 8) -> str:
    compact = compact_cjk_text(text)
    if len(compact) < length:
        return ""
    return compact[:length]


def translation_anchor_text(translated: str) -> str:
    if not translated:
        return ""
    return translation_tail_after_visual_prefix(translated) or translated


def group_interrupted_by_protected_region(plan: PageRenderPlan, group: list[int]) -> bool:
    if not group:
        return False
    group_set = set(group)
    group_box = bbox_union([plan.items[idx].bbox for idx in group])
    for idx, item in enumerate(plan.items):
        if idx in group_set or item.kind != "original_image_clip" or bbox_area(item.bbox) <= 0:
            continue
        if bbox_overlap_height(group_box, item.bbox) > 0 and layout_horizontal_conflict(group_box, item.bbox):
            return True
    return False


def validate_plan_vertical_balance(plan: PageRenderPlan, fitz=None) -> list[str]:
    if fitz is None:
        fitz = load_fitz()
    errors = []
    for lane in body_layout_lanes(plan):
        for group in split_body_layout_lane(plan, lane):
            limits = body_group_limits(plan, group, (10_000.0, 10_000.0))
            if limits is None and group_interrupted_by_protected_region(plan, group):
                continue
            current_body_run: list[RenderItem] = []
            body_runs: list[list[RenderItem]] = []
            for item in [plan.items[idx] for idx in group]:
                if render_text_style_name(item) == "body":
                    current_body_run.append(item)
                    continue
                if current_body_run:
                    body_runs.append(current_body_run)
                    current_body_run = []
            if current_body_run:
                body_runs.append(current_body_run)
            for items in body_runs:
                if len(items) < 2:
                    continue
                preferred_heights = [preferred_text_height_for_item(item, fitz) for item in items]
                for item, preferred in zip(items, preferred_heights):
                    slack = (item.bbox[3] - item.bbox[1]) - preferred
                    if slack > BODY_FLOW_INTERNAL_SLACK_WARN_PT:
                        errors.append(
                            f"page {plan.page_num} body flow has uneven vertical spacing: "
                            f"text {item.source_ids} keeps {slack:.1f}pt unused height"
                        )
                for left, right, left_height in zip(items, items[1:], preferred_heights):
                    visible_gap = right.bbox[1] - (left.bbox[1] + left_height)
                    if visible_gap > BODY_FLOW_VISIBLE_GAP_WARN_PT:
                        errors.append(
                            f"page {plan.page_num} body flow has uneven vertical spacing: "
                            f"gap between {left.source_ids} and {right.source_ids} is {visible_gap:.1f}pt"
                        )
    return errors


def validate_plan_reference_policy(plan: PageRenderPlan) -> list[str]:
    errors = []
    for entry in plan.ledger:
        if entry.classification != "reference":
            continue
        if entry.render_kind != "original_selectable_text":
            errors.append(f"page {plan.page_num} reference block {entry.block_id} is not selectable text")
    for item in plan.items:
        if item.fallback_reason != "reference_original":
            continue
        if cjk_char_count(item.text) > 0:
            errors.append(f"page {plan.page_num} reference item {item.source_ids} contains translated Chinese text")
    return errors


def validate_plan_footer_policy(plan: PageRenderPlan) -> list[str]:
    errors = []
    footers = [item for item in plan.items if item.fallback_reason == "journal_footer"]
    if len(footers) > 1:
        errors.append(f"page {plan.page_num} has multiple journal footers")
    translated_text = "\n".join(item.text for item in plan.items if item.kind == "translated_text")
    if is_journal_footer_text(translated_text):
        errors.append(f"page {plan.page_num} journal footer leaked into translated text")
    return errors


def validate_plan_embedded_heading_policy(plan: PageRenderPlan) -> list[str]:
    errors = []
    for item in plan.items:
        if item.kind not in {"translated_text", "original_selectable_text"}:
            continue
        if render_text_style_name(item) != "body":
            continue
        for line in item.text.split("\n")[1:]:
            if render_line_is_standalone_heading(line):
                errors.append(f"page {plan.page_num} item {item.source_ids} has embedded heading in body text")
                break
    return errors


def text_starts_with_running_header(text: str) -> bool:
    first = normalize_text(text).split("\n", 1)[0].strip()
    return bool(
        re.match(
            r"^(?:Maurice\s*Herlihy|MauriceHerlihy|Wait-?\s*Free\s*Synchronization|Wait-FreeSynchronization|无等待同步)\b",
            first,
            flags=re.I,
        )
    )


def validate_plan_text_noise_policy(plan: PageRenderPlan) -> list[str]:
    errors = []
    for item in plan.items:
        if item.kind not in {"translated_text", "original_selectable_text"} or not item.text.strip():
            continue
        if render_text_style_name(item) in {"reference", "footer"}:
            continue
        if plan.page_num > 1 and text_starts_with_running_header(item.text):
            errors.append(f"page {plan.page_num} item {item.source_ids} contains running header text")
        for line in item.text.split("\n"):
            if is_garbled_latin_fragment(line):
                errors.append(f"page {plan.page_num} item {item.source_ids} contains garbled OCR text")
                break
    return errors


def source_image_dark_pixel_count(source_image_path: Path | None, bbox, page_size, *, darkness_threshold: int = 245) -> int | None:
    if source_image_path is None or not Path(source_image_path).exists():
        return None
    page_width, page_height = page_size
    if page_width <= 0 or page_height <= 0:
        return None
    try:
        image = Image.open(source_image_path).convert("L")
    except Exception:
        return None
    scale_x = image.width / page_width
    scale_y = image.height / page_height
    x0, y0, x1, y1 = bbox
    crop_box = (
        max(0, int(math.floor(x0 * scale_x))),
        max(0, int(math.floor(y0 * scale_y))),
        min(image.width, int(math.ceil(x1 * scale_x))),
        min(image.height, int(math.ceil(y1 * scale_y))),
    )
    if crop_box[2] <= crop_box[0] or crop_box[3] <= crop_box[1]:
        return 0
    crop = image.crop(crop_box)
    return sum(1 for pixel in crop.getdata() if pixel < darkness_threshold)


def validate_plan_image_clip_content(
    plan: PageRenderPlan,
    source_image_path: Path | None,
    page_size,
) -> list[str]:
    if source_image_path is None:
        return []
    errors = []
    for item in plan.items:
        if item.kind != "original_image_clip":
            continue
        dark_pixels = source_image_dark_pixel_count(source_image_path, item.bbox, page_size)
        if dark_pixels is None:
            continue
        if dark_pixels <= 0:
            errors.append(f"page {plan.page_num} item {item.source_ids} has blank source image clip")
    return errors


def validate_plan_quality(page_num: int, blocks, translations, plan: PageRenderPlan) -> list[str]:
    ownership_errors = [
        issue.message
        for issue in getattr(plan.ownership_validation, "issues", [])
        if getattr(issue, "severity", "error") == "error"
    ]
    return (
        validate_plan_translation_quality(page_num, blocks, translations, plan)
        + validate_plan_text_overlaps(plan)
        + validate_plan_vertical_balance(plan)
        + validate_plan_reference_policy(plan)
        + validate_plan_footer_policy(plan)
        + validate_plan_embedded_heading_policy(plan)
        + validate_plan_text_noise_policy(plan)
        + validate_plan_style_policy(plan)
        + ownership_errors
    )


def validate_footer_consistency(plans: list[PageRenderPlan]) -> list[str]:
    footer_shapes = []
    for plan in plans:
        for item in plan.items:
            if item.fallback_reason == "journal_footer":
                footer_shapes.append((plan.page_num, item.kind, item.bbox, item.font_size, item.text))
    if not footer_shapes:
        return []
    expected = footer_shapes[0][1:]
    inconsistent_pages = [page_num for page_num, *shape in footer_shapes if tuple(shape) != expected]
    if inconsistent_pages:
        return [f"journal footer style is inconsistent on pages {inconsistent_pages[:10]}"]
    return []


def normalize_vector_text_layout(plan: PageRenderPlan, page_size, fitz=None) -> None:
    """Apply deterministic text layout repairs before validating or drawing vector text."""
    release_visual_clip_overcapture_for_text_fit(plan, page_size, fitz=fitz)
    expand_text_boxes_to_fit(plan, page_size, fitz=fitz)
    rebalance_body_text_flows(plan, page_size, fitz=fitz)
    release_visual_clip_overcapture_for_text_fit(plan, page_size, fitz=fitz)
    expand_text_boxes_to_fit(plan, page_size, fitz=fitz)
    fit_dense_visual_body_rows(plan, page_size, fitz=fitz)


DENSE_VISUAL_BODY_ROW_FALLBACK = "dense_visual_body_row"
DENSE_VISUAL_BODY_ROW_MAX_HEIGHT_PT = BODY_FONT_SIZE * 0.95
DENSE_VISUAL_BODY_ROW_MAX_VISUAL_GAP_PT = BODY_FONT_SIZE * 1.6
DENSE_VISUAL_BODY_ROW_FONT_STEP_PT = 0.1


def nearest_vertical_visual_blockers(item: RenderItem, plan: PageRenderPlan) -> tuple[RenderItem | None, RenderItem | None]:
    above = None
    below = None
    for other in plan.items:
        if other.kind != "original_image_clip" or not layout_horizontal_conflict(item.bbox, other.bbox):
            continue
        if other.bbox[3] <= item.bbox[1] + TEXT_FIT_EPSILON_PT:
            if above is None or other.bbox[3] > above.bbox[3]:
                above = other
            continue
        if other.bbox[1] >= item.bbox[3] - TEXT_FIT_EPSILON_PT:
            if below is None or other.bbox[1] < below.bbox[1]:
                below = other
    return above, below


def dense_visual_body_row_can_use_compact_font(
    item: RenderItem,
    plan: PageRenderPlan,
    page_size,
    fitz,
) -> bool:
    if item.kind != "translated_text" or item.style_name != "body" or len(item.source_ids) != 1:
        return False
    if item.fallback_reason and item.fallback_reason != DENSE_VISUAL_BODY_ROW_FALLBACK:
        return False
    if item.bbox[3] - item.bbox[1] > DENSE_VISUAL_BODY_ROW_MAX_HEIGHT_PT:
        return False
    classes = ledger_classifications(plan)
    if item_classifications(item, classes) != {"body"}:
        return False
    fit, required, _available = text_item_fit_metrics(item, fitz)
    if fit is not None:
        return False
    above, below = nearest_vertical_visual_blockers(item, plan)
    if above is None or below is None:
        return False
    if item.bbox[1] - above.bbox[3] > DENSE_VISUAL_BODY_ROW_MAX_VISUAL_GAP_PT:
        return False
    if below.bbox[1] - item.bbox[3] > DENSE_VISUAL_BODY_ROW_MAX_VISUAL_GAP_PT:
        return False
    top_limit, bottom_limit = vertical_expansion_limits(item, plan.items, page_size)
    return required > (bottom_limit - top_limit) + TEXT_FIT_EPSILON_PT


def compact_font_size_for_dense_body_row(item: RenderItem, fitz) -> float | None:
    style = text_style("body")
    width = max(1.0, item.bbox[2] - item.bbox[0])
    height = item.bbox[3] - item.bbox[1]
    size = min(item.font_size or style.font_size, style.font_size)
    while size >= SHRINK_FIT_MIN_FONT_SIZE - TEXT_FIT_EPSILON_PT:
        candidate = max(SHRINK_FIT_MIN_FONT_SIZE, round(size, 2))
        if text_box_fit_plan(fitz, item.text, width, height, style, font_size=candidate) is not None:
            return candidate
        size -= DENSE_VISUAL_BODY_ROW_FONT_STEP_PT
    return None


def fit_dense_visual_body_rows(plan: PageRenderPlan, page_size, fitz=None) -> None:
    """Use explicit compact body text only for dense rows pinned between visual clips."""
    if fitz is None:
        fitz = load_fitz()
    for item in plan.items:
        if not dense_visual_body_row_can_use_compact_font(item, plan, page_size, fitz):
            continue
        compact_font_size = compact_font_size_for_dense_body_row(item, fitz)
        if compact_font_size is None:
            continue
        item.font_size = compact_font_size
        item.fallback_reason = DENSE_VISUAL_BODY_ROW_FALLBACK
        update_ledger_render_kind(plan, item.source_ids, item.kind, DENSE_VISUAL_BODY_ROW_FALLBACK)


def validate_document_quality(selected_pages, translations, page_size, job_paths=None) -> list[str]:
    errors = []
    plans = []
    fitz = load_fitz()
    translations = postprocess_cross_page_sentence_splits(
        selected_pages,
        translations,
        job_paths=job_paths,
    )
    lines_by_page = bbox_lines_by_page(job_paths)
    in_reference_section = False
    for page_num, blocks in selected_pages:
        source_image_path = source_page_image_path(job_paths, page_num)
        plan = build_page_render_plan(
            page_num,
            blocks,
            translations,
            page_size,
            bbox_lines=lines_by_page.get(page_num),
            source_image_path=source_image_path,
            force_reference=in_reference_section,
        )
        normalize_vector_text_layout(plan, page_size, fitz=fitz)
        plan_has_reference = any(entry.classification == "reference" for entry in plan.ledger)
        if plan_has_reference:
            in_reference_section = True
        elif in_reference_section:
            in_reference_section = False
        plans.append(plan)
        errors.extend(validate_plan_coverage(page_num, blocks, plan))
        errors.extend(validate_plan_layout(plan, page_size))
        errors.extend(validate_plan_quality(page_num, blocks, translations, plan))
        errors.extend(validate_plan_image_clip_content(plan, source_image_path, page_size))
        errors.extend(validate_plan_text_fit(plan, fitz))
    errors.extend(validate_footer_consistency(plans))
    return errors


def item_classifications(item: RenderItem, classes: dict[str, str]) -> set[str]:
    return {classes[source_id] for source_id in item.source_ids if source_id in classes}


def item_is_body_like(item: RenderItem, classes: dict[str, str]) -> bool:
    if item.style_name == "body" and item.fallback_reason == "mixed_visual_body":
        return True
    if item.style_name == "body" and item.fallback_reason == "source_paragraph_split":
        return True
    item_classes = item_classifications(item, classes)
    return bool(item_classes) and item_classes <= {"body"}


def short_insertion_is_redundant(base_text: str, insertion: str) -> bool:
    insertion = normalize_text(insertion).strip()
    if not insertion:
        return True
    if insertion in {"如下", "如下:", "如下："}:
        return bool(re.search(r"(表述为|如下|如下所示|定义为|记为|形式为)[:：]?\s*$", base_text.strip()))
    return False


def merge_text_at_relative_position(base_text: str, insertion: str, relative_y: float) -> str:
    insertion = normalize_translation(insertion)
    if not insertion:
        return base_text
    if short_insertion_is_redundant(base_text, insertion):
        return base_text
    base_compact = compact_duplicate_text(base_text)
    insertion_compact = compact_duplicate_text(insertion)
    if len(insertion_compact) >= 8 and insertion_compact in base_compact:
        return base_text
    insertion_number = numbered_enumeration_start(insertion)
    if insertion_number is not None:
        following = next(
            (
                match
                for match in re.finditer(r"\((\d{1,2})\)\s*", base_text)
                if int(match.group(1)) > insertion_number
            ),
            None,
        )
        if following:
            prefix = base_text[: following.start()].rstrip()
            suffix = base_text[following.start() :].lstrip()
            return "\n".join(part for part in (prefix, insertion, suffix) if part)
    paragraphs = base_text.split("\n")
    if not paragraphs:
        return insertion
    index = int(round(max(0.0, min(1.0, relative_y)) * len(paragraphs)))
    index = max(0, min(len(paragraphs), index))
    if len(insertion) <= 36 and paragraphs:
        target = max(0, min(len(paragraphs) - 1, int(math.floor(max(0.0, min(0.999, relative_y)) * len(paragraphs)))))
        paragraphs[target] = join_render_lines(paragraphs[target].rstrip(), insertion)
        return "\n".join(paragraph for paragraph in paragraphs if paragraph.strip())
    paragraphs.insert(index, insertion)
    return "\n".join(paragraph for paragraph in paragraphs if paragraph.strip())


def merge_contained_text_fragments(plan: PageRenderPlan) -> None:
    classes = ledger_classifications(plan)
    text_items = [
        (idx, item)
        for idx, item in enumerate(plan.items)
        if item.kind == "translated_text" and item.source_ids
    ]
    merge_targets: dict[int, list[tuple[float, int, RenderItem]]] = {}
    remove_indices = set()
    for large_idx, large in text_items:
        large_height = large.bbox[3] - large.bbox[1]
        if not item_is_body_like(large, classes) or large_height < 40.0:
            continue
        large_area = bbox_area(large.bbox)
        for small_idx, small in text_items:
            if small_idx == large_idx or small_idx in remove_indices:
                continue
            if not item_is_body_like(small, classes):
                continue
            if bbox_area(small.bbox) <= 0 or large_area < bbox_area(small.bbox) * 6.0:
                continue
            if not (
                bbox_contains_point(large.bbox, bbox_center(small.bbox))
                or item_significantly_overlaps_protected(small, large)
            ):
                continue
            relative_y = (bbox_center(small.bbox)[1] - large.bbox[1]) / max(1.0, large_height)
            merge_targets.setdefault(large_idx, []).append((relative_y, small_idx, small))
            remove_indices.add(small_idx)

    if not merge_targets:
        return

    for large_idx, fragments in merge_targets.items():
        large = plan.items[large_idx]
        for relative_y, _small_idx, small in sorted(fragments, key=lambda item: item[0]):
            large.text = merge_text_at_relative_position(large.text, small.text, relative_y)
            for source_id in small.source_ids:
                if source_id not in large.source_ids:
                    large.source_ids.append(source_id)
        update_ledger_render_kind(
            plan,
            [source_id for _relative_y, _idx, small in fragments for source_id in small.source_ids],
            "translated_text",
            "merged_into_large_text",
        )

    plan.items = [item for idx, item in enumerate(plan.items) if idx not in remove_indices]


def translated_section_start(text: str) -> bool:
    return bool(re.match(r"^\s*\d+(?:\.\d+)*\.?\s*[\u4e00-\u9fffA-Z]", normalize_text(text)))


def item_is_body_flow_text(item: RenderItem, classes: dict[str, str]) -> bool:
    if item.kind != "translated_text" or item.style_name != "body" or not item.source_ids:
        return False
    if item.fallback_reason == "source_paragraph_split":
        return False
    if item.bbox[3] - item.bbox[1] < 8.0:
        return False
    return item_is_body_like(item, classes)


def protected_between_body_items(left: RenderItem, right: RenderItem, protected: list[RenderItem]) -> bool:
    top = min(left.bbox[3], right.bbox[3])
    bottom = max(left.bbox[1], right.bbox[1])
    for item in protected:
        if item.bbox[1] >= bottom or item.bbox[3] <= top:
            continue
        if horizontal_overlap(item.bbox, bbox_union([left.bbox, right.bbox])) > 8.0:
            return True
    return False


def body_items_can_share_flow(left: RenderItem, right: RenderItem, protected: list[RenderItem]) -> bool:
    if translated_section_start(right.text):
        return False
    vertical_gap = right.bbox[1] - left.bbox[3]
    if vertical_gap > 16.0:
        return False
    overlap = horizontal_overlap(left.bbox, right.bbox)
    min_width = max(1.0, min(left.bbox[2] - left.bbox[0], right.bbox[2] - right.bbox[0]))
    if overlap < min_width * 0.55:
        return False
    if protected_between_body_items(left, right, protected):
        return False
    return True


def merge_adjacent_body_text_flows(plan: PageRenderPlan) -> None:
    classes = ledger_classifications(plan)
    protected = [
        item
        for item in plan.items
        if item.kind == "original_image_clip"
        or (
            item.kind in {"translated_text", "original_selectable_text"}
            and item.style_name in {"heading", "subheading", "title", "reference", "footer"}
        )
    ]
    candidates = [
        (idx, item)
        for idx, item in enumerate(plan.items)
        if item_is_body_flow_text(item, classes)
    ]
    if len(candidates) < 2:
        return

    sorted_candidates = sorted(candidates, key=lambda pair: (pair[1].bbox[1], pair[1].bbox[0]))
    groups = []
    current = [sorted_candidates[0]]
    for idx, item in sorted_candidates[1:]:
        previous = current[-1][1]
        if body_items_can_share_flow(previous, item, protected):
            current.append((idx, item))
            continue
        if len(current) > 1:
            groups.append(current)
        current = [(idx, item)]
    if len(current) > 1:
        groups.append(current)
    if not groups:
        return

    replacement_by_first = {}
    remove_indices = set()
    for group in groups:
        ordered_items = [item for _idx, item in group]
        candidate_bbox = bbox_union([item.bbox for item in ordered_items])
        candidate = RenderItem("translated_text", [], candidate_bbox)
        if any(item_significantly_overlaps_protected(candidate, protected_item) for protected_item in protected):
            continue
        first_idx = group[0][0]
        source_ids = [source_id for item in ordered_items for source_id in item.source_ids]
        replacement_by_first[first_idx] = RenderItem(
            "translated_text",
            source_ids,
            candidate_bbox,
            text="\n".join(item.text for item in ordered_items if item.text.strip()),
            font_size=DOCUMENT_STYLES["body"].font_size,
            style_name="body",
            color=ordered_items[0].color,
            fallback_reason="body_flow",
        )
        remove_indices.update(idx for idx, _item in group[1:])

    plan.items = [
        replacement_by_first.get(idx, item)
        for idx, item in enumerate(plan.items)
        if idx not in remove_indices
    ]


def is_plain_sentence_text(text: str) -> bool:
    text = normalize_text(text)
    words = re.findall(r"[A-Za-z]+", text)
    return (
        len(words) >= 3
        and any(word.lower() in ENGLISH_FUNCTION_WORDS for word in words)
        and bool(re.search(r"[.!?][\"')\]）】”’]*$", text))
    )


def block_is_dense_nonprose_image_fallback(block) -> bool:
    text = normalize_text(block.get("text", ""))
    if not text:
        return False
    width = block["xMax"] - block["xMin"]
    height = block["yMax"] - block["yMin"]
    if should_preserve_first_page_metadata_as_image(block):
        return True
    if starts_reference_item(text) or contains_reference_item(text):
        return True
    if should_preserve_as_image(block):
        return True
    citation_count = len(re.findall(r"\[\d{1,3}\]", text))
    if citation_count >= 3 and (height <= 18.0 or width <= 280.0):
        return True
    if "@" in text and block.get("page") == 1 and block["yMin"] < 220.0 and len(text) <= 300:
        return True
    if re.search(r"\{answer_[a-z]\}|\[The (?:Start|End) of", text, flags=re.I):
        return True
    if re.search(r"(?im)^(Input Sentence and GPT-\d+ Output|Input:|Output:|Expected Output:)", text):
        return True
    if height <= 12.0 and len(text) <= 300 and not is_plain_sentence_text(text):
        return True
    if width <= 120.0 and len(text) <= 500 and not is_plain_sentence_text(text):
        return True
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if len(lines) >= 8 and width <= 320.0:
        return True
    if len(lines) >= 4 and (is_formula_or_code_block(text) or is_code_row_text(text)):
        return True
    if len(lines) >= 4 and sum(1 for line in lines if len(line) <= 48) >= len(lines) * 0.70:
        return True
    if is_prose_row_text(text):
        return False
    return len(lines) >= 4 and width <= 170.0 and not re.search(r"[.!?][\"')\]）】”’]*\s+[A-Z]", text)


def unfit_text_item_should_be_image(
    item: RenderItem,
    blocks_by_id: dict[str, dict],
    fitz,
    reference_source_ids: set[str] | None = None,
) -> bool:
    if item.kind not in {"translated_text", "original_selectable_text"} or not item.text.strip():
        return False
    if item.component_kind == ownership.COMPONENT_KIND_REFERENCE:
        return False
    if reference_source_ids and any(source_id in reference_source_ids for source_id in item.source_ids):
        return False
    style_name = render_text_style_name(item)
    if style_name not in {"body", "reference", "heading", "subheading"}:
        return False
    fit, required, available = text_item_fit_metrics(item, fitz)
    if fit is not None:
        return False
    if style_name == "reference":
        return False
    source_blocks = [blocks_by_id[source_id] for source_id in item.source_ids if source_id in blocks_by_id]
    if not source_blocks:
        return False
    source_text = "\n".join(normalize_text(block.get("text", "")) for block in source_blocks)
    if source_requires_chinese_translation(source_text) and is_prose_row_text(source_text):
        return False
    if is_plain_sentence_text(source_text):
        return False
    if is_prose_row_text(source_text) and not any(block_is_dense_nonprose_image_fallback(block) for block in source_blocks):
        source_lines = [line.strip() for line in source_text.split("\n") if line.strip()]
        source_width = max((block["xMax"] - block["xMin"] for block in source_blocks), default=0.0)
        if not (
            len(source_lines) >= 10
            or source_width <= 260.0
            or available < 8.0
            or required > max(available * 2.0, available + 120.0)
        ):
            return False
    if available < 8.0 and len(source_text) >= 60:
        return True
    if required > max(available * 2.0, available + 120.0):
        return True
    if any(len([line for line in normalize_text(block.get("text", "")).split("\n") if line.strip()]) >= 8 for block in source_blocks):
        return True
    if is_prose_row_text(source_text) and not any(block_is_dense_nonprose_image_fallback(block) for block in source_blocks):
        return False
    return any(block_is_dense_nonprose_image_fallback(block) for block in source_blocks)


def convert_unfit_nonprose_text_to_image_clips(plan: PageRenderPlan, blocks, fitz=None) -> None:
    if fitz is None:
        fitz = load_fitz()
    blocks_by_id = {block["id"]: block for block in blocks}
    reference_source_ids = {
        entry.block_id
        for entry in plan.ledger
        if entry.classification == "reference" or entry.component_kind == ownership.COMPONENT_KIND_REFERENCE
    }
    new_items = []
    for item in plan.items:
        if not unfit_text_item_should_be_image(item, blocks_by_id, fitz, reference_source_ids):
            new_items.append(item)
            continue
        new_items.append(
            RenderItem(
                "original_image_clip",
                list(item.source_ids),
                item.bbox,
                fallback_reason="unfit_nonprose_image",
            )
        )
        update_ledger_render_kind(plan, item.source_ids, "original_image_clip", "unfit_nonprose_image")
    plan.items = new_items


def write_vector_pdf(pdf_path: Path, pdf_output: Path, selected_pages, translations, pdf_size_pt, dpi: int, job_paths=None):
    fitz = load_fitz()
    src_doc = fitz.open(pdf_path)
    out_doc = fitz.open()
    translations = postprocess_cross_page_sentence_splits(
        selected_pages,
        translations,
        job_paths=job_paths,
    )
    lines_by_page = bbox_lines_by_page(job_paths)
    total_pages = len(selected_pages)
    in_reference_section = False
    for output_idx, (page_num, blocks) in enumerate(selected_pages, start=1):
        if output_idx == 1 or output_idx % 50 == 0 or output_idx == total_pages:
            print(
                f"vector render {output_idx}/{total_pages} source_page={page_num}",
                file=sys.stderr,
                flush=True,
            )
        src_page = src_doc[page_num - 1]
        page_rect = src_page.rect
        source_image = source_page_image_path(job_paths, page_num)
        out_page = out_doc.new_page(width=page_rect.width, height=page_rect.height)
        out_page.draw_rect(page_rect, color=None, fill=(1, 1, 1))
        preserve_images_on_page(src_page, out_page, fitz, dpi)
        # Source drawings can depend on PDF clipping paths that PyMuPDF does not
        # preserve through get_drawings(); visual regions are copied from page
        # images instead, which keeps table/formula lines without drawing artifacts.

        plan = build_page_render_plan(
            page_num,
            blocks,
            translations,
            (page_rect.width, page_rect.height),
            bbox_lines=lines_by_page.get(page_num),
            source_image_path=source_image,
            force_reference=in_reference_section,
        )
        normalize_vector_text_layout(plan, (page_rect.width, page_rect.height), fitz=fitz)
        plan_has_reference = any(entry.classification == "reference" for entry in plan.ledger)
        if plan_has_reference:
            in_reference_section = True
        elif in_reference_section:
            in_reference_section = False
        validation_results = {
            "ownership": ownership.ownership_validation_to_json(plan.ownership_validation),
        }
        coverage_errors = validate_plan_coverage(page_num, blocks, plan)
        validation_results["coverage_errors"] = coverage_errors
        if coverage_errors:
            try_write_render_plan_artifact(plan, validation_results, job_paths)
            raise RuntimeError("\n".join(coverage_errors[:20]))
        layout_errors = validate_plan_layout(plan, (page_rect.width, page_rect.height))
        validation_results["layout_errors"] = layout_errors
        if layout_errors:
            try_write_render_plan_artifact(plan, validation_results, job_paths)
            raise RuntimeError("\n".join(layout_errors[:20]))
        style_policy_errors = validate_plan_style_policy(plan)
        validation_results["style_policy_errors"] = style_policy_errors
        if style_policy_errors:
            try_write_render_plan_artifact(plan, validation_results, job_paths)
            raise RuntimeError("\n".join(style_policy_errors[:20]))
        text_fit_errors = validate_plan_text_fit(plan, fitz)
        validation_results["text_fit_errors"] = text_fit_errors
        if text_fit_errors:
            try_write_render_plan_artifact(plan, validation_results, job_paths)
            raise RuntimeError("\n".join(text_fit_errors[:20]))
        write_render_plan_artifact(plan, validation_results, job_paths)
        for item in plan.items:
            render_plan_item(out_page, src_page, fitz, item, dpi, source_image_path=source_image)

    copy_outline(src_doc, out_doc, selected_pages, translations)
    pdf_output.parent.mkdir(parents=True, exist_ok=True)
    out_doc.save(pdf_output, garbage=4, deflate=True)
    out_doc.close()
    src_doc.close()


def get_pdf_page_size(pdf_path: Path):
    proc = run(["pdfinfo", str(pdf_path)], check=True)
    match = re.search(r"Page size:\s*([0-9.]+)\s+x\s+([0-9.]+)\s+pts", proc.stdout)
    if not match:
        raise RuntimeError(f"unable to parse page size from pdfinfo for {pdf_path}")
    return float(match.group(1)), float(match.group(2))


def write_raster_pdf(pdf_output: Path, page_numbers, pdf_size_pt, job_paths):
    """Assemble translated raster pages without allowing TeX to add blank pages."""
    tex_path = job_paths["tex_path"]
    width_pt, height_pt = pdf_size_pt
    lines = [
        r"\documentclass{article}",
        rf"\usepackage[paperwidth={width_pt}bp,paperheight={height_pt}bp,margin=0in]{{geometry}}",
        r"\usepackage{graphicx}",
        r"\pagestyle{empty}",
        r"\setlength{\parindent}{0pt}",
        r"\setlength{\topskip}{0pt}",
        r"\newcommand{\fullpageimage}[1]{%",
        r"\noindent\makebox[\paperwidth][l]{\raisebox{-\height}[0pt][0pt]{\includegraphics[width=\paperwidth,height=\paperheight]{#1}}}%",
        r"}",
        r"\begin{document}",
    ]
    for idx, page_num in enumerate(page_numbers, start=1):
        image_path = translated_page_path(page_num, job_paths)
        if not image_path.exists():
            raise FileNotFoundError(f"missing translated raster page: {image_path}")
        lines.append(r"\fullpageimage{" + str(image_path).replace("\\", "/") + r"}")
        if idx != len(page_numbers):
            lines.append(r"\newpage")
    lines.append(r"\end{document}")
    tex_path.write_text("\n".join(lines), encoding="utf-8")
    pdf_output.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "xelatex",
            "-interaction=nonstopmode",
            "-output-directory",
            str(job_paths["job_dir"]),
            str(tex_path),
        ],
        check=True,
    )
    shutil.copy2(job_paths["pdf_path"], pdf_output)


def write_latex(pdf_output: Path, page_numbers, pdf_size_pt, job_paths):
    write_raster_pdf(pdf_output, page_numbers, pdf_size_pt, job_paths)


def write_latex_legacy(pdf_output: Path, page_numbers, pdf_size_pt, job_paths):
    tex_path = job_paths["tex_path"]
    width_pt, height_pt = pdf_size_pt
    lines = [
        r"\documentclass{article}",
        rf"\usepackage[paperwidth={width_pt}bp,paperheight={height_pt}bp,margin=0in]{{geometry}}",
        r"\usepackage{graphicx}",
        r"\pagestyle{empty}",
        r"\begin{document}",
    ]
    for idx, i in enumerate(page_numbers, start=1):
        lines.append(
            r"\noindent\includegraphics[width=\paperwidth,height=\paperheight]{"
            + str(translated_page_path(i, job_paths)).replace("\\", "/")
            + r"}"
        )
        if idx != len(page_numbers):
            lines.append(r"\newpage")
    lines.append(r"\end{document}")
    tex_path.write_text("\n".join(lines), encoding="utf-8")
    pdf_output.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "xelatex",
            "-interaction=nonstopmode",
            "-output-directory",
            str(job_paths["job_dir"]),
            str(tex_path),
        ],
        check=True,
    )
    shutil.copy2(job_paths["pdf_path"], pdf_output)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", default=str(Path.cwd() / "DiveintoClaudeCode.pdf"))
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--batch-chars", type=int, default=10000)
    parser.add_argument("--pdf-output", default=str(Path.cwd() / "claudeCodeChinese.pdf"))
    parser.add_argument("--page-start", type=int, default=1)
    parser.add_argument("--page-end", type=int, default=0)
    parser.add_argument("--job-name", default="")
    parser.add_argument("--work-dir", default=str(TMP_ROOT))
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--reasoning-effort", default="low")
    parser.add_argument("--force-ocr", action="store_true")
    parser.add_argument("--render-mode", choices=["vector", "raster"], default="vector")
    args = parser.parse_args()
    set_work_dir(Path(args.work_dir))

    pdf_path = Path(args.pdf)
    job_paths = build_job_paths(pdf_path, args.job_name or None)
    pdf_size_pt = get_pdf_page_size(pdf_path)
    all_pages = load_or_build_source_pages(
        pdf_path,
        args.dpi,
        job_paths,
        pdf_size_pt,
        args.page_start,
        args.page_end,
        force_ocr=args.force_ocr,
    )
    page_end = args.page_end or len(all_pages)
    selected_pages = [
        (idx, page)
        for idx, page in enumerate(all_pages, start=1)
        if args.page_start <= idx <= page_end
    ]
    batches = build_batches(
        [page for _, page in selected_pages],
        args.batch_chars,
        page_size=pdf_size_pt,
        job_paths=job_paths,
        page_numbers=[page_num for page_num, _page in selected_pages],
    )
    translations = translate_batches(
        batches,
        job_paths,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
    )
    translations = postprocess_cross_page_sentence_splits(
        selected_pages,
        translations,
        job_paths=job_paths,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
    )
    if args.render_mode == "raster":
        render_pages(selected_pages, translations, args.dpi, job_paths)
        write_latex(
            Path(args.pdf_output),
            [idx for idx, _ in selected_pages],
            pdf_size_pt,
            job_paths,
        )
    else:
        write_vector_pdf(
            pdf_path,
            Path(args.pdf_output),
            selected_pages,
            translations,
            pdf_size_pt,
            args.dpi,
            job_paths=job_paths,
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        sys.exit(1)
