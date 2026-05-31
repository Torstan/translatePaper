import math
import re
from pathlib import Path
from statistics import median

from PIL import Image

from classify import (
    NORMAL_TRANSLATED_CLASSES,
    cjk_char_count,
    contains_visual_caption,
    english_function_word_count,
    is_body_enumeration_line,
    is_code_line_number_block,
    is_code_listing_block,
    is_code_row_text,
    is_formula_like,
    is_formula_or_code_block,
    is_fragmented_narrow_table_cell,
    is_heading_text,
    is_numeric_metric_cell,
    is_page_number,
    is_prose_row_text,
    is_running_header_fragment,
    is_standalone_equation_label,
    is_table_caption,
    is_non_prose_identifier_text,
    is_reference_heading,
    is_visual_caption,
    is_visual_row_text,
    latin_words,
    normalize_text,
    reference_block_ids,
    should_preserve_as_image,
    source_requires_chinese_translation,
)


FORMULA_COLUMN_FRACTION = 0.52
FORMULA_PAD_TOP_PX = 8
FORMULA_PAD_BOTTOM_PX = 4
FULL_PAGE_IMAGE_AREA_FRACTION = 0.70
EDGE_ICON_MAX_SIZE_PT = 40.0
HEURISTIC_HEADING_MAX_CHARS = 120
HEADING_NUMBER_TITLE_MAX_GAP_PT = 90.0
IMAGE_ROW_GROUP_MIN_COUNT = 3
IMAGE_ROW_GROUP_MIN_SPAN_PT = 180.0
IMAGE_ROW_GROUP_CENTER_TOLERANCE_PT = 18.0
IMAGE_ROW_CLIP_PAD_X_PT = 2.0
IMAGE_ROW_CLIP_PAD_TOP_PT = 22.0
IMAGE_ROW_CLIP_PAD_BOTTOM_PT = 6.0
TEXT_PROTECTED_GAP_PT = 0.75
LARGE_PROSE_VISUAL_AVOID_AREA_PT = 12000.0
LARGE_PROSE_VISUAL_AVOID_HEIGHT_PT = 58.0
DIAGRAM_LABEL_VERTICAL_CLUSTER_GAP_PT = 140.0
VISUAL_CLIP_PIXEL_SEARCH_PAD_X_PT = 40.0
VISUAL_CLIP_PIXEL_SEARCH_PAD_Y_PT = 20.0
VISUAL_CLIP_PIXEL_FINAL_PAD_PT = 3.0


def block_to_px_box(block, dpi: int, page_width: int, page_height: int, pad: int = 2):
    scale = dpi / 72.0
    x0 = int(block["xMin"] * scale) - pad
    y0 = int(block["yMin"] * scale) - pad
    x1 = int(block["xMax"] * scale) + pad
    y1 = int(block["yMax"] * scale) + pad
    return (
        max(0, x0),
        max(0, y0),
        min(page_width, x1),
        min(page_height, y1),
    )


def protected_box_for_block(block, dpi: int, page_width: int, page_height: int):
    x0, y0, x1, y1 = block_to_px_box(block, dpi, page_width, page_height, pad=3)
    if is_formula_like(block.get("text", "")):
        width = x1 - x0
        center_x = (x0 + x1) / 2
        if width < page_width * 0.45:
            if center_x < page_width / 2:
                x0, x1 = 0, int(page_width * FORMULA_COLUMN_FRACTION)
            else:
                x0, x1 = int(page_width * (1 - FORMULA_COLUMN_FRACTION)), page_width
            y0 -= FORMULA_PAD_TOP_PX
            y1 += FORMULA_PAD_BOTTOM_PX
        else:
            y0 -= 4
            y1 += 4
    return (
        max(0, x0),
        max(0, y0),
        min(page_width, x1),
        min(page_height, y1),
    )


def overlap_area(box_a, box_b):
    x0 = max(box_a[0], box_b[0])
    y0 = max(box_a[1], box_b[1])
    x1 = min(box_a[2], box_b[2])
    y1 = min(box_a[3], box_b[3])
    if x1 <= x0 or y1 <= y0:
        return 0
    return (x1 - x0) * (y1 - y0)


def horizontal_overlap(box_a, box_b):
    x0 = max(box_a[0], box_b[0])
    x1 = min(box_a[2], box_b[2])
    return max(0, x1 - x0)


def vertical_overlap(box_a, box_b):
    y0 = max(box_a[1], box_b[1])
    y1 = min(box_a[3], box_b[3])
    return max(0, y1 - y0)


def build_ocr_preserve_groups(ocr_lines, page_width_px: int, page_height_px: int):
    groups = []
    preserved = set()

    for line in ocr_lines:
        if not is_visual_caption(line["text"]):
            continue
        if line["width_px"] >= page_width_px * 0.45:
            x0, x1 = 0, page_width_px
        elif line["center_x_px"] < page_width_px / 2:
            x0, x1 = 0, page_width_px * 0.52
        else:
            x0, x1 = page_width_px * 0.48, page_width_px
        y0 = max(0, line["y0_px"] - page_height_px * 0.16)
        y1 = line["y1_px"] + 24
        group = [
            candidate
            for candidate in ocr_lines
            if y0 <= candidate["y0_px"] <= y1
            and x0 <= candidate["center_x_px"] <= x1
        ]
        if group:
            groups.append(group)
            preserved.update(item["idx"] for item in group)

    for line in ocr_lines:
        if not is_formula_like(line["text"]):
            continue
        if line["center_x_px"] < page_width_px / 2:
            x0, x1 = 0, page_width_px * FORMULA_COLUMN_FRACTION
        else:
            x0, x1 = page_width_px * (1 - FORMULA_COLUMN_FRACTION), page_width_px
        y0 = max(0, line["y0_px"] - FORMULA_PAD_TOP_PX)
        y1 = min(page_height_px, line["y1_px"] + FORMULA_PAD_BOTTOM_PX)
        group = [
            candidate
            for candidate in ocr_lines
            if is_formula_like(candidate["text"])
            if y0 <= candidate["y0_px"] <= y1
            and min(candidate["x1_px"], x1) > max(candidate["x0_px"], x0)
        ]
        if group:
            group.append(
                {
                    "idx": -1,
                    "text": "",
                    "x0_px": x0,
                    "x1_px": x1,
                    "y0_px": y0,
                    "y1_px": y1,
                    "width_px": x1 - x0,
                    "center_x_px": (x0 + x1) / 2,
                }
            )
            groups.append(group)
            preserved.update(item["idx"] for item in group)

    return groups, preserved


def make_px_box(lines):
    return {
        "x0_px": min(line["x0_px"] for line in lines),
        "y0_px": min(line["y0_px"] for line in lines),
        "x1_px": max(line["x1_px"] for line in lines),
        "y1_px": max(line["y1_px"] for line in lines),
    }


def image_info_preserve_bbox(info, page_area: float):
    bbox = tuple(info["bbox"])
    x0, y0, x1, y1 = bbox
    if x1 <= x0 or y1 <= y0:
        return None
    if ((x1 - x0) * (y1 - y0)) / page_area >= FULL_PAGE_IMAGE_AREA_FRACTION:
        return None
    if x0 <= 2 and (x1 - x0) <= EDGE_ICON_MAX_SIZE_PT and (y1 - y0) <= EDGE_ICON_MAX_SIZE_PT:
        return None
    return bbox


def valid_image_insert_bbox(bbox) -> bool:
    try:
        x0, y0, x1, y1 = (float(value) for value in bbox)
    except Exception:
        return False
    return all(math.isfinite(value) for value in (x0, y0, x1, y1)) and x1 > x0 and y1 > y0


def grouped_image_row_clips(image_entries, page_size) -> list[dict]:
    rows = []
    for entry in sorted(image_entries, key=lambda item: (bbox_center(item["bbox"])[1], item["bbox"][0])):
        center_y = bbox_center(entry["bbox"])[1]
        target = None
        for row in rows:
            if abs(center_y - row["center_y"]) <= IMAGE_ROW_GROUP_CENTER_TOLERANCE_PT:
                target = row
                break
        if target is None:
            target = {"entries": [], "center_y": center_y}
            rows.append(target)
        target["entries"].append(entry)
        target["center_y"] = median(bbox_center(item["bbox"])[1] for item in target["entries"])

    clips = []
    for row in rows:
        entries = sorted(row["entries"], key=lambda item: item["bbox"][0])
        if len(entries) < IMAGE_ROW_GROUP_MIN_COUNT:
            continue
        row_bbox = bbox_union([entry["bbox"] for entry in entries])
        if row_bbox[2] - row_bbox[0] < IMAGE_ROW_GROUP_MIN_SPAN_PT:
            continue
        base = clamped_expanded_bbox(
            row_bbox,
            page_size,
            pad_x=IMAGE_ROW_CLIP_PAD_X_PT,
            pad_y=0.0,
        )
        clips.append(
            {
                "bbox": clamp_bbox(
                    (
                        base[0],
                        row_bbox[1] - IMAGE_ROW_CLIP_PAD_TOP_PT,
                        base[2],
                        row_bbox[3] + IMAGE_ROW_CLIP_PAD_BOTTOM_PT,
                    ),
                    page_size,
                ),
                "indices": {entry["index"] for entry in entries},
            }
        )
    return clips


def block_area_pt(block) -> float:
    return max(0.0, block["xMax"] - block["xMin"]) * max(0.0, block["yMax"] - block["yMin"])


def block_overlap_area_pt(a, b) -> float:
    x0 = max(a["xMin"], b["xMin"])
    y0 = max(a["yMin"], b["yMin"])
    x1 = min(a["xMax"], b["xMax"])
    y1 = min(a["yMax"], b["yMax"])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def block_bbox(block) -> tuple[float, float, float, float]:
    return (block["xMin"], block["yMin"], block["xMax"], block["yMax"])


def bbox_area(box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def bbox_overlap_area(box_a, box_b) -> float:
    x0 = max(box_a[0], box_b[0])
    y0 = max(box_a[1], box_b[1])
    x1 = min(box_a[2], box_b[2])
    y1 = min(box_a[3], box_b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def bbox_union(boxes) -> tuple[float, float, float, float]:
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def bbox_intersects(a, b) -> bool:
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def bbox_center(box) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def bbox_contains_point(box, point) -> bool:
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def expanded_bbox(box, pad_x: float = 0.0, pad_y: float = 0.0):
    return (box[0] - pad_x, box[1] - pad_y, box[2] + pad_x, box[3] + pad_y)


def merge_bbox_line_fragments(lines) -> list[dict]:
    rows = []
    for line in sorted(lines, key=lambda item: ((item["bbox"][1] + item["bbox"][3]) / 2.0, item["bbox"][0])):
        text = normalize_text(line.get("text", ""))
        if not text:
            continue
        x0, y0, x1, y1 = line["bbox"]
        center_y = (y0 + y1) / 2.0
        target = None
        for row in reversed(rows[-4:]):
            if abs(center_y - row["center_y"]) <= 2.2:
                target = row
                break
        if target is None:
            rows.append({"center_y": center_y, "parts": [(x0, text, (x0, y0, x1, y1))]})
        else:
            target["parts"].append((x0, text, (x0, y0, x1, y1)))
            centers = [(part[2][1] + part[2][3]) / 2.0 for part in target["parts"]]
            target["center_y"] = median(centers)

    merged = []
    for row in rows:
        parts = sorted(row["parts"], key=lambda item: item[0])
        boxes = [part[2] for part in parts]
        merged.append(
            {
                "text": normalize_text(" ".join(part[1] for part in parts)),
                "bbox": bbox_union(boxes),
            }
        )
    return merged


def clamped_expanded_bbox(box, page_size, pad_x: float = 0.0, pad_y: float = 0.0) -> tuple[float, float, float, float]:
    page_width, page_height = page_size
    x0, y0, x1, y1 = expanded_bbox(box, pad_x=pad_x, pad_y=pad_y)
    return (
        max(0.0, x0),
        max(0.0, y0),
        min(page_width, x1),
        min(page_height, y1),
    )


def clamp_bbox(box, page_size) -> tuple[float, float, float, float]:
    page_width, page_height = page_size
    x0, y0, x1, y1 = box
    return (
        max(0.0, x0),
        max(0.0, y0),
        min(page_width, x1),
        min(page_height, y1),
    )


def pixel_search_bbox(box, page_size, pad_x: float, pad_y: float) -> tuple[float, float, float, float]:
    return clamped_expanded_bbox(box, page_size, pad_x=pad_x, pad_y=pad_y)


def dark_pixel_bbox_in_source_image(
    source_image_path: Path | None,
    search_box,
    page_size,
    *,
    darkness_threshold: int = 245,
) -> tuple[float, float, float, float] | None:
    if source_image_path is None or not source_image_path.exists():
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
    x0, y0, x1, y1 = search_box
    px0 = max(0, int(math.floor(x0 * scale_x)))
    py0 = max(0, int(math.floor(y0 * scale_y)))
    px1 = min(image.width, int(math.ceil(x1 * scale_x)))
    py1 = min(image.height, int(math.ceil(y1 * scale_y)))
    if px1 <= px0 or py1 <= py0:
        return None

    crop = image.crop((px0, py0, px1, py1))
    min_x = crop.width
    min_y = crop.height
    max_x = -1
    max_y = -1
    pixels = crop.load()
    for y in range(crop.height):
        for x in range(crop.width):
            if pixels[x, y] >= darkness_threshold:
                continue
            min_x = min(min_x, x)
            min_y = min(min_y, y)
            max_x = max(max_x, x)
            max_y = max(max_y, y)
    if max_x < min_x or max_y < min_y:
        return None
    return clamp_bbox(
        (
            (px0 + min_x) / scale_x,
            (py0 + min_y) / scale_y,
            (px0 + max_x + 1) / scale_x,
            (py0 + max_y + 1) / scale_y,
        ),
        page_size,
    )


def visual_clip_bbox(
    region_bbox,
    page_size,
    pad_x: float,
    pad_y: float,
    *,
    page_num: int = 1,
    source_image_path: Path | None = None,
    use_pixel_bounds: bool = False,
    pixel_search_pad_x: float = VISUAL_CLIP_PIXEL_SEARCH_PAD_X_PT,
    pixel_search_pad_y: float = VISUAL_CLIP_PIXEL_SEARCH_PAD_Y_PT,
    pixel_final_pad: float = VISUAL_CLIP_PIXEL_FINAL_PAD_PT,
) -> tuple[float, float, float, float]:
    base_bbox = clamped_expanded_bbox(region_bbox, page_size, pad_x=pad_x, pad_y=pad_y)
    if not use_pixel_bounds:
        return base_bbox
    search_box = pixel_search_bbox(
        base_bbox,
        page_size,
        pad_x=pixel_search_pad_x,
        pad_y=pixel_search_pad_y,
    )
    if page_num > 1 and search_box[1] < 62.0 < search_box[3]:
        search_box = (search_box[0], 62.0, search_box[2], search_box[3])
    pixel_bbox = dark_pixel_bbox_in_source_image(source_image_path, search_box, page_size)
    if pixel_bbox is None:
        return base_bbox
    return clamped_expanded_bbox(
        pixel_bbox,
        page_size,
        pad_x=pixel_final_pad,
        pad_y=pixel_final_pad,
    )


def cap_visual_bbox_before_following_text(region_bbox, visual_bbox, blocks, classes, visual_ids):
    capped = visual_bbox
    for block in blocks:
        if block["id"] in visual_ids:
            continue
        classification = classes.get(block["id"], "unknown")
        if classification not in NORMAL_TRANSLATED_CLASSES:
            continue
        text = normalize_text(block.get("text", ""))
        if not text:
            continue
        if not source_requires_chinese_translation(text):
            continue
        block_box = block_bbox(block)
        if block_box[1] <= region_bbox[1] + 1.0:
            continue
        if block_box[1] >= capped[3]:
            continue
        if horizontal_overlap(capped, block_box) < min(capped[2] - capped[0], block_box[2] - block_box[0]) * 0.15:
            continue
        capped = (capped[0], capped[1], capped[2], max(capped[1], block_box[1] - 2.0))
    return capped


def cap_visual_bbox_after_preceding_text(region_bbox, visual_bbox, blocks, classes, visual_ids):
    capped = visual_bbox
    for block in blocks:
        if block["id"] in visual_ids:
            continue
        classification = classes.get(block["id"], "unknown")
        if classification not in NORMAL_TRANSLATED_CLASSES:
            continue
        text = normalize_text(block.get("text", ""))
        if not text:
            continue
        block_box = block_bbox(block)
        if block_box[3] > region_bbox[1] + 1.0:
            continue
        if block_box[3] <= capped[1]:
            continue
        if horizontal_overlap(capped, block_box) < min(capped[2] - capped[0], block_box[2] - block_box[0]) * 0.15:
            continue
        capped = (capped[0], min(capped[3], block_box[3]), capped[2], capped[3])
    return capped


def cap_visual_bbox_against_adjacent_translated_text(region_bbox, visual_bbox, blocks, classes, visual_ids, page_size):
    capped = visual_bbox
    region_center_x = bbox_center(region_bbox)[0]
    for block in blocks:
        if block["id"] in visual_ids:
            continue
        classification = classes.get(block["id"])
        if classification not in NORMAL_TRANSLATED_CLASSES:
            continue
        text = normalize_text(block.get("text", ""))
        if not text:
            continue
        if classification == "body" and is_non_prose_identifier_text(text):
            continue
        block_box = block_bbox(block)
        if vertical_overlap(capped, block_box) <= 6.0:
            continue
        if horizontal_overlap(capped, block_box) <= 0.0:
            continue
        x0, y0, x1, y1 = capped
        block_center_x = bbox_center(block_box)[0]
        if block_center_x >= region_center_x:
            x1 = min(x1, block_box[0] - TEXT_PROTECTED_GAP_PT)
        else:
            x0 = max(x0, block_box[2] + TEXT_PROTECTED_GAP_PT)
        next_box = clamp_bbox((x0, y0, x1, y1), page_size)
        if next_box[2] - next_box[0] >= 20.0 and next_box[3] - next_box[1] >= 8.0:
            capped = next_box
    return capped


def formula_region_bbox_from_lines(region, bbox_lines, page_size) -> tuple[float, float, float, float] | None:
    if not bbox_lines:
        return None
    region_box = region["bbox"]
    search = expanded_bbox(region_box, pad_x=24.0, pad_y=4.0)
    candidates = []
    for line in bbox_lines:
        center = bbox_center(line["bbox"])
        if not (search[1] <= center[1] <= search[3]):
            continue
        if not (search[0] <= center[0] <= search[2]):
            continue
        candidates.append(line)
    if not candidates:
        return None
    formula_rows = [
        row
        for row in merge_bbox_line_fragments(candidates)
        if is_formula_or_code_block(row["text"]) and not is_body_enumeration_line(row["text"])
    ]
    if not formula_rows:
        return None
    return clamp_bbox(bbox_union([row["bbox"] for row in formula_rows]), page_size)


def block_is_visually_covered_by_region(block, region_bbox) -> bool:
    block_box = block_bbox(block)
    if bbox_contains_point(region_bbox, bbox_center(block_box)):
        return True
    block_area = bbox_area(block_box)
    return block_area > 0 and bbox_overlap_area(block_box, region_bbox) >= block_area * 0.35


def is_large_prose_block(block, text: str) -> bool:
    if not source_requires_chinese_translation(text) or not is_prose_row_text(text):
        return False
    block_box = block_bbox(block)
    return (
        bbox_area(block_box) >= LARGE_PROSE_VISUAL_AVOID_AREA_PT
        or block_box[3] - block_box[1] >= LARGE_PROSE_VISUAL_AVOID_HEIGHT_PT
        or len(normalize_text(text)) >= 220
    )


def nontranslated_blocks_covered_by_visual_region(blocks, classes, region_bbox, visual_ids) -> set[str]:
    covered = set()
    for block in blocks:
        block_id = block["id"]
        if block_id in visual_ids:
            continue
        text = normalize_text(block.get("text", ""))
        if not text:
            continue
        if classes.get(block_id) in {"page_number", "header_footer", "journal_footer", "reference"}:
            continue
        if classes.get(block_id) in {"title", "heading", "subheading", "body"} and source_requires_chinese_translation(text):
            continue
        if is_large_prose_block(block, text):
            continue
        if block_is_visually_covered_by_region(block, region_bbox):
            covered.add(block_id)
    return covered


def translated_blocks_structurally_covered_by_visual_region(blocks, classes, region_bbox, visual_ids) -> set[str]:
    visual_boxes = [
        block_bbox(block)
        for block in blocks
        if block["id"] in visual_ids and block_is_visually_covered_by_region(block, region_bbox)
    ]
    if not visual_boxes:
        return set()
    visual_bottom = max(box[3] for box in visual_boxes)

    covered = set()
    for block in blocks:
        block_id = block["id"]
        if block_id in visual_ids:
            continue
        if classes.get(block_id) not in NORMAL_TRANSLATED_CLASSES:
            continue
        text = normalize_text(block.get("text", ""))
        if not text or not source_requires_chinese_translation(text):
            continue
        block_box = block_bbox(block)
        if block_box[1] >= visual_bottom - 0.5:
            continue
        if block_is_visually_covered_by_region(block, region_bbox):
            covered.add(block_id)
    return covered


def cap_visual_bbox_around_large_prose(region_bbox, blocks, classes, visual_ids, page_size):
    capped = region_bbox
    for block in blocks:
        block_id = block["id"]
        if block_id in visual_ids or classes.get(block_id) in {"page_number", "header_footer", "journal_footer", "reference"}:
            continue
        text = normalize_text(block.get("text", ""))
        if not is_large_prose_block(block, text):
            continue
        block_box = block_bbox(block)
        if bbox_overlap_area(capped, block_box) <= min(bbox_area(capped), bbox_area(block_box)) * 0.05:
            continue
        region_center = bbox_center(capped)
        block_center = bbox_center(block_box)
        x0, y0, x1, y1 = capped
        if block_center[0] >= region_center[0] and block_box[0] > x0 + 12.0:
            x1 = min(x1, block_box[0] - TEXT_PROTECTED_GAP_PT)
        elif block_center[0] < region_center[0] and block_box[2] < x1 - 12.0:
            x0 = max(x0, block_box[2] + TEXT_PROTECTED_GAP_PT)
        elif block_center[1] >= region_center[1] and block_box[1] > y0 + 12.0:
            y1 = min(y1, block_box[1] - TEXT_PROTECTED_GAP_PT)
        elif block_center[1] < region_center[1] and block_box[3] < y1 - 12.0:
            y0 = max(y0, block_box[3] + TEXT_PROTECTED_GAP_PT)
        next_box = clamp_bbox((x0, y0, x1, y1), page_size)
        if next_box[2] - next_box[0] >= 20.0 and next_box[3] - next_box[1] >= 8.0:
            capped = next_box
    return capped


def same_heading_line(block, other) -> bool:
    vertical = min(block["yMax"], other["yMax"]) - max(block["yMin"], other["yMin"])
    min_height = max(1.0, min(block["yMax"] - block["yMin"], other["yMax"] - other["yMin"]))
    return vertical / min_height >= 0.45 and other["xMin"] >= block["xMax"]


def short_heading_text(text: str) -> str:
    first_line = normalize_text(text).split("\n", 1)[0].strip()
    if not first_line or len(first_line) > HEURISTIC_HEADING_MAX_CHARS:
        return ""
    if first_line.count(".") > 2 and not re.match(r"^\d+(?:\.\d+)*\.?\s+", first_line):
        return ""
    return first_line


def starts_like_source_heading(text: str) -> bool:
    return bool(re.match(r"[A-Z]", text.strip()))


def heuristic_heading_from_block(block):
    text = short_heading_text(block.get("text", ""))
    if not text:
        return None

    if re.fullmatch(r"(?i)abstract|acknowledg(?:e)?ments?|references|bibliography", text):
        return 1, text
    if re.match(r"(?i)^appendix(?:\s|$)", text):
        return 1, text

    match = re.match(r"^(\d+(?:\.\d+)*)(?:\.)?\s+(.+)$", text)
    if not match:
        return None
    title = match.group(2).strip()
    if not title or not starts_like_source_heading(title) or title.endswith("."):
        return None
    if re.match(r"(?i)question:", title):
        return None
    return min(4, match.group(1).count(".") + 1), text


def standalone_heading_number_text(text: str) -> str:
    candidate = normalize_text(text).split("\n", 1)[0].strip()
    if not re.fullmatch(r"\d+(?:\.\d+)*\.?", candidate):
        return ""
    number = candidate.rstrip(".")
    parts = [int(part) for part in number.split(".") if part.isdigit()]
    if not parts or parts[0] <= 0 or parts[0] > 30:
        return ""
    if any(part > 99 for part in parts[1:]):
        return ""
    return number


def standalone_appendix_heading_marker_text(text: str) -> str:
    candidate = normalize_text(text).split("\n", 1)[0].strip().rstrip(".")
    if not re.fullmatch(r"[A-Z](?:\.\d+){0,3}", candidate):
        return ""
    return candidate


def has_appendix_heading_title_to_right(block, blocks) -> bool:
    if not standalone_appendix_heading_marker_text(block.get("text", "")):
        return False
    for other in blocks:
        if other["id"] == block["id"]:
            continue
        if not same_heading_line(block, other):
            continue
        gap = other["xMin"] - block["xMax"]
        if gap < 0 or gap > HEADING_NUMBER_TITLE_MAX_GAP_PT:
            continue
        title = short_heading_text(other.get("text", ""))
        if title and starts_like_source_heading(title) and not title.endswith("."):
            return True
    return False


def is_standalone_heading_pair_member(block, blocks) -> bool:
    text = normalize_text(block.get("text", ""))
    if standalone_heading_number_text(text) and any(
        same_heading_line(block, other)
        and 0 <= other["xMin"] - block["xMax"] <= HEADING_NUMBER_TITLE_MAX_GAP_PT
        and short_heading_text(other.get("text", ""))
        and starts_like_source_heading(short_heading_text(other.get("text", "")))
        for other in blocks
        if other["id"] != block["id"]
    ):
        return True
    return has_standalone_heading_number_to_left(block, blocks)


def source_is_short_continuation_fragment(block) -> bool:
    text = normalize_text(block.get("text", ""))
    if not text or "\n" in text or len(text) > 30:
        return False
    words = latin_words(text)
    return 0 < len(words) <= 2 and not source_requires_chinese_translation(text)


def is_first_page_translatable_title_area_block(block) -> bool:
    if block.get("page") != 1:
        return False
    text = normalize_text(block.get("text", ""))
    if not text:
        return False
    if is_heading_text(text) or source_requires_chinese_translation(text):
        return True
    words = latin_words(text)
    return (
        90.0 <= block["yMin"] <= 340.0
        and len(words) >= 4
        and block["xMax"] - block["xMin"] >= 220.0
        and not re.fullmatch(r"[A-Z][A-Z0-9 /&-]{3,80}", text)
    )


def is_table_body_candidate(seed_box, candidate_box, text: str) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return False
    width = candidate_box[2] - candidate_box[0]
    height = candidate_box[3] - candidate_box[1]
    seed_width = max(1.0, seed_box[2] - seed_box[0])
    wide_row = width >= max(240.0, seed_width * 0.65)
    far_below_caption = candidate_box[1] > seed_box[3] + 220.0
    indented_from_caption = candidate_box[0] > seed_box[0] + 40.0
    sentence_count = len(re.findall(r"[.!?][\"')\]）】”’]*", normalized))

    if wide_row and is_heading_text(normalized):
        return False
    if wide_row and indented_from_caption:
        return len(normalized) <= 800
    if wide_row and far_below_caption and is_prose_row_text(normalized):
        return False
    if wide_row and (sentence_count >= 2 or height >= 30.0 or (is_prose_row_text(normalized) and len(normalized) > 110)):
        return False
    if is_formula_or_code_block(normalized) or is_code_row_text(normalized) or is_code_line_number_block(normalized):
        return True
    if should_preserve_as_image({"text": normalized}):
        return True
    if indented_from_caption and width <= 260.0 and height <= 180.0 and len(normalized) <= 520:
        right_column_cell = candidate_box[0] > seed_box[0] + 160.0
        if is_prose_row_text(normalized) and not right_column_cell:
            return False
        return True
    if not wide_row:
        if is_prose_row_text(normalized):
            return False
        return len(normalized) <= 220 and height <= 80.0
    return len(normalized) <= 220 and height <= 120.0


def is_table_region_terminator(seed_box, candidate_box, text: str) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return False
    if candidate_box[1] <= seed_box[3] + 12.0:
        return False
    left_aligned = candidate_box[0] <= seed_box[0] + 16.0
    if not left_aligned:
        return False
    if standalone_heading_number_text(normalized) or heuristic_heading_from_block({"text": normalized}):
        return True
    width = candidate_box[2] - candidate_box[0]
    height = candidate_box[3] - candidate_box[1]
    seed_width = max(1.0, seed_box[2] - seed_box[0])
    wide_row = width >= max(240.0, seed_width * 0.65)
    if not wide_row:
        return False
    sentence_count = len(re.findall(r"[.!?][\"')\]）】”’]*", normalized))
    return is_prose_row_text(normalized) and (height >= 30.0 or sentence_count >= 1 or len(normalized) >= 80)


def is_adjacent_visual_table_row_cell(seed_box, candidate_box, text: str) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return False
    if standalone_heading_number_text(normalized) or heuristic_heading_from_block({"text": normalized}):
        return False
    if is_prose_row_text(normalized):
        return False
    min_height = max(1.0, min(seed_box[3] - seed_box[1], candidate_box[3] - candidate_box[1]))
    if vertical_overlap(seed_box, candidate_box) < min_height * 0.45:
        return False
    horizontal_gap = max(0.0, max(seed_box[0], candidate_box[0]) - min(seed_box[2], candidate_box[2]))
    if horizontal_gap > 130.0:
        return False
    if candidate_box[2] - candidate_box[0] > 360.0:
        return False
    if is_table_region_terminator(seed_box, candidate_box, normalized):
        return False
    return len(normalized) <= 520


def has_intervening_wide_prose_block(seed_box, candidate_box, blocks, excluded_ids: set[str]) -> bool:
    if vertical_overlap(seed_box, candidate_box) > 0:
        return False
    top = min(seed_box[3], candidate_box[3])
    bottom = max(seed_box[1], candidate_box[1])
    if bottom <= top:
        return False
    for block in blocks:
        if block["id"] in excluded_ids:
            continue
        text = normalize_text(block.get("text", ""))
        if not text:
            continue
        box = block_bbox(block)
        wide_text = box[2] - box[0] >= 240.0 and len(text) >= 40
        if not (is_prose_row_text(text) or wide_text):
            continue
        center_y = (box[1] + box[3]) / 2.0
        if top <= center_y <= bottom and box[2] - box[0] >= 240.0:
            return True
    return False


def is_nearby_table_header_cell(region_box, candidate_box, text: str) -> bool:
    normalized = normalize_text(text)
    if not normalized or len(normalized) > 100 or is_prose_row_text(normalized):
        return False
    if candidate_box[3] < region_box[1] - 48.0 or candidate_box[3] > region_box[1] + 6.0:
        return False
    if candidate_box[3] - candidate_box[1] > 56.0:
        return False
    return horizontal_overlap(region_box, candidate_box) >= 8.0


def table_cells_already_seen_above_caption(seed_box, blocks, consumed: set[str]) -> bool:
    for other in blocks:
        if other["id"] not in consumed:
            continue
        other_box = block_bbox(other)
        if other_box[3] > seed_box[1] + 8.0 or other_box[3] < seed_box[1] - 140.0:
            continue
        if horizontal_overlap(seed_box, other_box) <= 0:
            continue
        text = normalize_text(other.get("text", ""))
        if is_table_body_candidate(seed_box, other_box, text) or is_numeric_metric_cell(text):
            return True
    return False


def table_cells_present_above_caption(seed_box, blocks, consumed: set[str]) -> bool:
    for other in blocks:
        if other["id"] in consumed:
            continue
        other_box = block_bbox(other)
        if other_box[3] > seed_box[1] + 8.0 or other_box[3] < seed_box[1] - 180.0:
            continue
        if horizontal_overlap(seed_box, other_box) <= 0:
            continue
        text = normalize_text(other.get("text", ""))
        if not text:
            continue
        if (
            is_table_body_candidate(seed_box, other_box, text)
            or is_numeric_metric_cell(text)
            or is_multiline_numeric_table_column(text)
            or visual_label_like_text(other)
        ):
            return True
    return False


def has_standalone_heading_number_to_left(block, blocks) -> bool:
    for other in blocks:
        if other["id"] == block["id"] or not standalone_heading_number_text(other.get("text", "")):
            continue
        if same_heading_line(other, block) and 0 <= block["xMin"] - other["xMax"] <= HEADING_NUMBER_TITLE_MAX_GAP_PT:
            return True
    return False


def merge_adjacent_code_visual_regions(regions: list[dict]) -> list[dict]:
    if len(regions) < 2:
        return regions
    merged: list[dict] = []
    for region in sorted(regions, key=lambda item: (item["bbox"][1], item["bbox"][0])):
        if (
            merged
            and merged[-1].get("has_code_seed")
            and region.get("has_code_seed")
            and region["bbox"][1] <= merged[-1]["bbox"][3] + 4.0
            and horizontal_overlap(merged[-1]["bbox"], region["bbox"]) >= 20.0
        ):
            previous = merged[-1]
            seen = set(previous["source_ids"])
            previous["source_ids"].extend(source_id for source_id in region["source_ids"] if source_id not in seen)
            previous["bbox"] = bbox_union([previous["bbox"], region["bbox"]])
            previous["has_code_seed"] = True
            continue
        merged.append(dict(region))
    return merged


def visual_label_like_text(block) -> bool:
    text = normalize_text(block.get("text", ""))
    if not text or is_visual_caption(text) or contains_visual_caption(text):
        return False
    if is_first_page_translatable_title_area_block(block):
        return False
    box = block_bbox(block)
    width = box[2] - box[0]
    height = box[3] - box[1]
    if is_multiline_numeric_table_column(text):
        return True
    if is_running_header_fragment(block) or is_reference_heading(text) or standalone_heading_number_text(text):
        return False
    if is_heading_text(text) and not (height >= width * 1.6 or (len(text) <= 40 and width <= 180.0 and english_function_word_count(text) == 0)):
        return False
    if is_prose_row_text(text):
        return False
    if is_numeric_metric_cell(text) or is_fragmented_narrow_table_cell(block):
        return True
    compact = re.sub(r"\s+", "", text)
    if re.fullmatch(r"[\d,.]+", compact):
        return True
    if re.search(r"[.!?][\"')\]）】”’]*\s*$", text):
        return False
    if len(compact) <= 28 and re.fullmatch(r"[\w\[\]#,+\-_/().·•]+", compact):
        return True
    if "\n" in text and len(text) <= 180 and english_function_word_count(text) == 0:
        return True
    if is_non_prose_identifier_text(text):
        return True
    words = latin_words(text)
    return len(words) <= 8 and english_function_word_count(text) == 0 and len(text) <= 120


def is_multiline_numeric_table_column(text: str) -> bool:
    lines = [line.strip() for line in normalize_text(text).split("\n") if line.strip()]
    if len(lines) < 2:
        return False
    numeric_lines = [
        line
        for line in lines
        if re.fullmatch(r"[+-]?\d+(?:\.\d+)?(?:\s+[+-]?\d+(?:\.\d+)?){0,4}", line)
    ]
    return len(numeric_lines) == len(lines)


def expand_visual_regions_with_upper_labels(blocks, regions: list[dict], *, max_gap: float = 100.0) -> list[dict]:
    if not regions:
        return regions
    sorted_blocks = sorted(blocks, key=lambda item: (item["yMin"], item["xMin"]))
    consumed = {source_id for region in regions for source_id in region["source_ids"]}
    changed = True
    while changed:
        changed = False
        for region in sorted(regions, key=lambda item: (item["bbox"][1], item["bbox"][0])):
            region_box = region["bbox"]
            group = []
            for block in sorted_blocks:
                if block["id"] in consumed or block["id"] in region["source_ids"]:
                    continue
                if is_standalone_heading_pair_member(block, sorted_blocks):
                    continue
                if not visual_label_like_text(block):
                    continue
                box = block_bbox(block)
                if box[3] > region_box[1] + 8.0:
                    continue
                if region_box[1] - box[3] > max_gap:
                    continue
                required_overlap = min(4.0, max(0.5, (box[2] - box[0]) * 0.5))
                if horizontal_overlap(region_box, box) < required_overlap:
                    continue
                group.append(block)
            if not group:
                continue
            group.sort(key=lambda item: (item["yMin"], item["xMin"]))
            region["source_ids"].extend(block["id"] for block in group)
            region["source_ids"] = list(dict.fromkeys(region["source_ids"]))
            region["bbox"] = bbox_union([region["bbox"], *(block_bbox(block) for block in group)])
            consumed.update(block["id"] for block in group)
            changed = True
    return regions


def diagram_label_candidate_above_caption(block, caption_box) -> bool:
    text = normalize_text(block.get("text", ""))
    if not text or is_page_number(text) or is_visual_caption(text) or contains_visual_caption(text):
        return False
    if is_running_header_fragment(block):
        return False
    if re.search(r"(?i)\b(abstract|introduction|references)\b", text):
        return False
    box = block_bbox(block)
    if box[3] >= caption_box[1] - 2.0:
        return False
    width = box[2] - box[0]
    height = box[3] - box[1]
    if width > 180.0 or height > 92.0 or len(text) > 120:
        return False
    if is_formula_or_code_block(text) or is_formula_like(text):
        return True
    if any(0x1D400 <= ord(char) <= 0x1D7FF for char in text):
        return True
    words = latin_words(text)
    if len(words) <= 1 and re.fullmatch(r"[A-Za-z,.;:'\"!?()<>_\-/\s]{1,18}", text):
        return True
    if 1 <= len(words) <= 4 and width <= 120.0 and height <= 55.0:
        return True
    if 1 <= len(words) <= 4 and english_function_word_count(text) == 0:
        return True
    if re.fullmatch(r"[A-Za-z0-9 ._+:/%#?=&~×,;()[\]'\"!-]{1,40}", text):
        return True
    return text in {"…", "⊕"}


def nearest_caption_label_cluster(group: list[dict], caption_box) -> list[dict]:
    if not group:
        return []
    clusters = []
    current = []
    current_bottom = None
    for block in sorted(group, key=lambda item: (item["yMin"], item["xMin"])):
        box = block_bbox(block)
        if (
            current
            and current_bottom is not None
            and box[1] - current_bottom > DIAGRAM_LABEL_VERTICAL_CLUSTER_GAP_PT
        ):
            clusters.append(current)
            current = []
            current_bottom = None
        current.append(block)
        current_bottom = max(current_bottom if current_bottom is not None else box[3], box[3])
    if current:
        clusters.append(current)
    return max(clusters, key=lambda items: bbox_union([block_bbox(block) for block in items])[3])


def diagram_regions_above_visual_captions(blocks, existing_regions: list[dict]) -> list[dict]:
    existing_ids = {source_id for region in existing_regions for source_id in region["source_ids"]}
    sorted_blocks = sorted(blocks, key=lambda item: (item["yMin"], item["xMin"]))
    regions = []
    for caption in sorted_blocks:
        caption_text = normalize_text(caption.get("text", ""))
        if not (is_visual_caption(caption_text) or contains_visual_caption(caption_text)):
            continue
        caption_box = block_bbox(caption)
        top_limit = max(0.0, caption_box[1] - 520.0)
        search = (
            caption_box[0] - 60.0,
            top_limit,
            caption_box[2] + 60.0,
            caption_box[1] - 2.0,
        )
        group = []
        for block in sorted_blocks:
            if block["id"] in existing_ids or block["id"] == caption["id"]:
                continue
            box = block_bbox(block)
            center = bbox_center(box)
            if not (search[0] <= center[0] <= search[2] and search[1] <= center[1] <= search[3]):
                continue
            if diagram_label_candidate_above_caption(block, caption_box):
                group.append(block)
        group = nearest_caption_label_cluster(group, caption_box)
        if len(group) < 3:
            continue
        region_box = bbox_union([block_bbox(block) for block in group])
        region_width = region_box[2] - region_box[0]
        region_height = region_box[3] - region_box[1]
        gap_to_caption = caption_box[1] - region_box[3]
        if len(group) < 8:
            if region_width < 160.0 or gap_to_caption < 60.0 or region_height > 180.0:
                continue
        else:
            if region_width < 160.0 or region_height < 80.0:
                continue
            if region_box[1] > caption_box[1] - 260.0 and region_height < 180.0:
                continue
        source_ids = [block["id"] for block in group]
        existing_ids.update(source_ids)
        regions.append(
            {
                "source_ids": source_ids,
                "bbox": region_box,
                "has_code_seed": False,
                "has_caption_seed": False,
                "has_row_cell_seed": False,
                "has_diagram_seed": True,
            }
        )
    return regions


def build_visual_regions(blocks) -> list[dict]:
    regions = []
    consumed = set()
    references = reference_block_ids(blocks)
    sorted_blocks = sorted(blocks, key=lambda item: (item["yMin"], item["xMin"]))
    for block in sorted_blocks:
        if block["id"] in consumed or block["id"] in references:
            continue
        text = normalize_text(block.get("text", ""))
        if not text:
            continue
        short_source_fragment = source_is_short_continuation_fragment(block)
        appendix_heading_marker = has_appendix_heading_title_to_right(block, sorted_blocks)
        explicit_image = (
            (block.get("preserve_image") or should_preserve_as_image(block))
            and not is_standalone_equation_label(text)
        ) and not is_body_enumeration_line(text) and (not short_source_fragment or is_numeric_metric_cell(text))
        explicit_image = explicit_image and not appendix_heading_marker
        has_caption = is_visual_caption(text) or contains_visual_caption(text)
        caption_seed = has_caption and not is_large_prose_block(block, text)
        row_cell_seed = is_numeric_metric_cell(text) or is_fragmented_narrow_table_cell(block)
        formula_like = is_formula_like(text)
        code_seed = (
            is_code_listing_block(text)
            or (is_code_row_text(text) and not formula_like)
        ) and not is_body_enumeration_line(text) and not short_source_fragment and not appendix_heading_marker
        code_seed = code_seed and not is_first_page_translatable_title_area_block(block)
        formula_seed = (
            (is_formula_or_code_block(text) or is_code_row_text(text))
            and not is_standalone_equation_label(text)
            and not is_body_enumeration_line(text)
            and not short_source_fragment
            and not appendix_heading_marker
        )
        formula_seed = formula_seed and not is_first_page_translatable_title_area_block(block)
        is_visual_seed = explicit_image or caption_seed or formula_seed
        if not is_visual_seed:
            continue
        seed_box = block_bbox(block)
        table_seed = is_table_caption(text) and caption_seed
        if table_seed:
            table_cells_above_caption = table_cells_already_seen_above_caption(
                seed_box,
                sorted_blocks,
                consumed,
            ) or table_cells_present_above_caption(
                seed_box,
                sorted_blocks,
                consumed | {block["id"]},
            )
            if table_cells_above_caption:
                search = (seed_box[0] - 260.0, seed_box[1] - 140.0, seed_box[2] + 260.0, seed_box[3] + 28.0)
            else:
                search = (seed_box[0] - 260.0, seed_box[1] - 20.0, seed_box[2] + 260.0, seed_box[3] + 640.0)
        elif has_caption:
            search = (seed_box[0] - 180.0, seed_box[1] - 60.0, seed_box[2] + 180.0, seed_box[3] + 28.0)
        elif row_cell_seed:
            search = expanded_bbox(seed_box, pad_x=520.0, pad_y=110.0)
        elif code_seed:
            search = expanded_bbox(seed_box, pad_x=130.0, pad_y=28.0)
        elif is_formula_or_code_block(text):
            search = expanded_bbox(seed_box, pad_x=35.0, pad_y=18.0)
        else:
            search = expanded_bbox(seed_box, pad_x=10.0, pad_y=8.0)
        group = []
        for other in sorted_blocks:
            other_text = normalize_text(other.get("text", ""))
            if not other_text or other["id"] in consumed:
                continue
            if other["id"] != block["id"] and other["id"] in references:
                continue
            if is_running_header_fragment(other):
                continue
            if other["id"] != block["id"] and contains_visual_caption(other_text):
                continue
            if other["id"] != block["id"] and is_first_page_translatable_title_area_block(other):
                continue
            other_box = block_bbox(other)
            if bbox_intersects(search, other_box) and bbox_contains_point(search, bbox_center(other_box)):
                if row_cell_seed and other["id"] != block["id"] and has_intervening_wide_prose_block(
                    seed_box,
                    other_box,
                    sorted_blocks,
                    {block["id"], other["id"]},
                ):
                    continue
                if row_cell_seed and is_adjacent_visual_table_row_cell(seed_box, other_box, other_text):
                    group.append(other)
                    continue
                if table_seed:
                    if other["id"] != block["id"]:
                        if is_standalone_heading_pair_member(other, sorted_blocks):
                            continue
                        if is_table_region_terminator(seed_box, other_box, other_text):
                            break
                        if table_cells_above_caption and is_large_prose_block(other, other_text):
                            continue
                        if not is_table_body_candidate(seed_box, other_box, other_text):
                            continue
                    group.append(other)
                    continue
                short_fragment = len(other_text) <= 140
                visual_text = (
                    is_visual_caption(other_text)
                    or contains_visual_caption(other_text)
                    or (
                        is_formula_or_code_block(other_text)
                        and not is_standalone_equation_label(other_text)
                        and not source_is_short_continuation_fragment(other)
                    )
                    or (
                        is_code_row_text(other_text)
                        and not is_standalone_equation_label(other_text)
                        and not source_is_short_continuation_fragment(other)
                    )
                    or (
                        should_preserve_as_image(other)
                        and not is_body_enumeration_line(other_text)
                        and not source_is_short_continuation_fragment(other)
                    )
                )
                code_line_number = code_seed and is_code_line_number_block(other_text)
                if (
                    other["id"] != block["id"]
                    and (standalone_heading_number_text(other_text) or heuristic_heading_from_block({"text": other_text}))
                    and not code_line_number
                ):
                    continue
                if other["id"] != block["id"] and has_standalone_heading_number_to_left(other, sorted_blocks):
                    continue
                if visual_text or code_line_number or (short_fragment and (code_seed or not formula_seed)):
                    group.append(other)
        if not group:
            group = [block]
        if row_cell_seed:
            region_box = bbox_union([block_bbox(item) for item in group])
            group_ids = {item["id"] for item in group}
            for other in sorted_blocks:
                if other["id"] in consumed or other["id"] in group_ids:
                    continue
                other_text = normalize_text(other.get("text", ""))
                if not other_text:
                    continue
                other_box = block_bbox(other)
                if is_adjacent_visual_table_row_cell(region_box, other_box, other_text):
                    group.append(other)
                    group_ids.add(other["id"])
                    continue
                if is_nearby_table_header_cell(region_box, other_box, other_text):
                    group.append(other)
                    group_ids.add(other["id"])
            group.sort(key=lambda item: (item["yMin"], item["xMin"]))
        group_ids = {item["id"] for item in group}
        consumed.update(group_ids)
        region_box = bbox_union([block_bbox(item) for item in group])
        regions.append(
            {
                "source_ids": [item["id"] for item in group],
                "bbox": region_box,
                "has_code_seed": code_seed,
                "has_caption_seed": caption_seed,
                "has_row_cell_seed": row_cell_seed,
            }
        )
    regions = merge_adjacent_code_visual_regions(regions)
    regions.extend(diagram_regions_above_visual_captions(blocks, regions))
    regions = expand_visual_regions_with_upper_labels(blocks, regions)
    return regions
