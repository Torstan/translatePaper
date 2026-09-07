import re
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from classify import cjk_char_count, is_page_number, normalize_text, should_preserve_as_image
from render_plan import (
    PageRenderPlan,
    RenderItem,
    bbox_area,
    bbox_significantly_overlaps_protected,
    update_ledger_render_kind,
)
from regions import (
    TEXT_PROTECTED_GAP_PT,
    bbox_center,
    bbox_union,
    clamp_bbox,
    horizontal_overlap,
)


TOOL_ROOT = Path(__file__).resolve().parent
VENDOR_ROOT = TOOL_ROOT / "vendor"
DEFAULT_RASTER_FONT_PATH = "/usr/share/fonts/truetype/arphic/uming.ttc"
RASTER_FONT_PATHS = (
    DEFAULT_RASTER_FONT_PATH,
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
)
RASTER_FONT_FACE_INDEXES = {
    "/System/Library/Fonts/STHeiti Light.ttc": 1,  # Heiti SC Light
    "/System/Library/Fonts/Supplemental/Songti.ttc": 3,  # Songti SC Light
    "/System/Library/Fonts/STHeiti Medium.ttc": 1,  # Heiti SC Medium, last-resort only
}
FONT_PATH = DEFAULT_RASTER_FONT_PATH
SOURCE_FONT_SCALE = 0.94
RASTER_LINE_HEIGHT_FACTOR = 1.25 * 1.20
TEXT_BOX_MARGIN_PX = 8
RASTER_VERTICAL_TEXT_MAX_SOURCE_CHARS = 80

VECTOR_FONT = "china-s"
MATH_VECTOR_FONT = "MathF"
MATH_VECTOR_FONT_PATHS = (
    "/System/Library/Fonts/Supplemental/STIXTwoMath.otf",
    "/usr/share/fonts/opentype/stix-word/STIXMath-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansMath-Regular.ttf",
    "/usr/share/texmf/fonts/opentype/public/lm-math/latinmodern-math.otf",
)
VECTOR_BODY_COLOR = (0, 0, 0)
BODY_TEXT_BOX_CUSHION_PT = 5.0
BODY_FLOW_MIN_GAP_PT = 3.0
BODY_FLOW_TARGET_GAP_PT = 8.0
BODY_FLOW_MAX_GAP_PT = 12.0
SOURCE_PARAGRAPH_FLOW_MAX_GAP_PT = 26.0
BODY_FLOW_TOP_MAX_GAP_PT = 14.0
BODY_FLOW_INTERNAL_SLACK_WARN_PT = 24.0
BODY_FLOW_VISIBLE_GAP_WARN_PT = 32.0
TEXT_FIT_EPSILON_PT = 0.01
TEXT_VERTICAL_EXPANSION_GAP_PT = 2.0
SHRINK_FIT_MIN_FONT_SIZE = 5.0
EXPANDABLE_TEXT_STYLE_NAMES = {"body", "heading", "subheading", "title"}
SOURCE_ADAPTED_FONT_MIN_DELTA_PT = 0.35
SOURCE_ADAPTED_BODY_MIN_LINES = 3
SOURCE_ADAPTED_HEADING_MIN_HEIGHT_FACTOR = 1.65
SOURCE_ADAPTED_FONT_MAX_BY_STYLE = {
    "body": 11.2,
    "subheading": 12.4,
    "heading": 13.2,
    "title": 15.4,
}
TEXT_FLOW_EXCLUDED_FALLBACK_REASONS = {"callout_heading", "callout_body"}


@dataclass(frozen=True)
class TextStyle:
    font_size: float
    line_height_factor: float
    paragraph_spacing: float = 0.0
    letter_spacing: float = 0.0
    color: tuple[float, float, float] = VECTOR_BODY_COLOR
    min_line_height_factor: float | None = None
    min_paragraph_spacing: float | None = None


@dataclass(frozen=True)
class TextBoxFitPlan:
    lines: list[list[str]]
    font_size: float
    line_height_factor: float
    paragraph_spacing: float
    required_height: float
    available_height: float


DOCUMENT_STYLES = {
    "body": TextStyle(font_size=9.2, line_height_factor=1.22, paragraph_spacing=2.0, min_line_height_factor=1.08, min_paragraph_spacing=0.0),
    "heading": TextStyle(font_size=11.2, line_height_factor=1.18, paragraph_spacing=2.0, min_line_height_factor=1.12, min_paragraph_spacing=0.0),
    "subheading": TextStyle(font_size=10.2, line_height_factor=1.18, paragraph_spacing=1.5, min_line_height_factor=1.12, min_paragraph_spacing=0.0),
    "title": TextStyle(font_size=13.6, line_height_factor=1.15, paragraph_spacing=2.0, min_line_height_factor=1.10, min_paragraph_spacing=0.0),
    "metadata": TextStyle(font_size=8.4, line_height_factor=1.15, paragraph_spacing=0.8, min_line_height_factor=1.10, min_paragraph_spacing=0.0),
    "footer": TextStyle(font_size=5.2, line_height_factor=1.05),
    "reference": TextStyle(font_size=5.9, line_height_factor=1.10, min_line_height_factor=1.0),
}

BODY_FONT_SIZE = DOCUMENT_STYLES["body"].font_size
HEADING_FONT_SIZE = DOCUMENT_STYLES["heading"].font_size
TITLE_FONT_SIZE = DOCUMENT_STYLES["title"].font_size
JOURNAL_FOOTER_FONT_SIZE = DOCUMENT_STYLES["footer"].font_size
TEXT_FLOW_STYLE_NAMES = {"body", "heading", "subheading"}
STYLE_POLICY_CLASSIFICATION_STYLES = {
    "title": {"title"},
    "heading": {"heading", "subheading"},
    "subheading": {"subheading"},
    "body": {"body"},
    "reference": {"reference"},
    "footer": {"footer"},
    "header_footer": {"footer"},
    "journal_footer": {"footer"},
    "page_number": {"footer"},
}
STYLE_POLICY_ROLE_SPLIT_EXCEPTIONS = {
    "embedded_heading",
    "embedded_heading_body",
    "first_page_abstract",
    "first_page_metadata",
    "first_page_title",
    "body_flow_compact",
    "dense_visual_body_row",
    "mixed_visual_body",
    "source_adapted_font",
    "body_flow_source_adapted_font",
    "callout_heading",
    "callout_body",
}


def raster_font_path() -> str | None:
    for path in RASTER_FONT_PATHS:
        if Path(path).exists():
            return path
    return None


def raster_image_font(font_size: int):
    attempted = []
    for path in RASTER_FONT_PATHS:
        if not Path(path).exists():
            continue
        attempted.append(path)
        try:
            return ImageFont.truetype(path, font_size, index=RASTER_FONT_FACE_INDEXES.get(path, 0))
        except OSError:
            continue
    checked = ", ".join(attempted or RASTER_FONT_PATHS)
    raise OSError(f"cannot open raster CJK font resource; checked: {checked}")


def raster_line_height(font) -> int:
    ascent, descent = font.getmetrics()
    return int((ascent + descent) * RASTER_LINE_HEIGHT_FACTOR)


def _load_fitz():
    if str(VENDOR_ROOT) not in sys.path:
        sys.path.insert(0, str(VENDOR_ROOT))
    try:
        import fitz
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Vector PDF rendering requires PyMuPDF. "
            "Install it with: python3 -m pip install --target vendor pymupdf"
        ) from exc
    return fitz


def text_style(style_name: str) -> TextStyle:
    return DOCUMENT_STYLES.get(style_name, DOCUMENT_STYLES["body"])


def text_height_for_lines(lines, font_size: float, line_height_factor: float, paragraph_spacing: float = 0.0) -> float:
    if not lines:
        return 0.0
    line_height = font_size * line_height_factor
    total = 0.0
    for line in lines:
        total += line_height
        if line == []:
            total += paragraph_spacing
    return total


def fitted_text_spacing(lines, font_size: float, rect_height: float, style: TextStyle) -> tuple[float, float] | None:
    line_factor_candidates = [style.line_height_factor]
    min_line_factor = style.min_line_height_factor if style.min_line_height_factor is not None else style.line_height_factor
    if min_line_factor < style.line_height_factor:
        line_factor_candidates.append(min_line_factor)
    paragraph_spacing_candidates = [style.paragraph_spacing]
    min_paragraph_spacing = style.min_paragraph_spacing if style.min_paragraph_spacing is not None else style.paragraph_spacing
    if min_paragraph_spacing < style.paragraph_spacing:
        paragraph_spacing_candidates.append(min_paragraph_spacing)

    for paragraph_spacing in paragraph_spacing_candidates:
        for line_factor in line_factor_candidates:
            if text_height_for_lines(lines, font_size, line_factor, paragraph_spacing) <= rect_height + TEXT_FIT_EPSILON_PT:
                return line_factor, paragraph_spacing
    return None


def text_box_fit_plan(
    fitz,
    text: str,
    width: float,
    height: float,
    style: TextStyle,
    *,
    font_size: float | None = None,
) -> TextBoxFitPlan | None:
    size = font_size or style.font_size
    lines = wrap_mixed_pdf_text(fitz, text, max(1.0, width), size)
    fit = fitted_text_spacing(lines, size, height, style)
    if fit is None:
        return None
    line_factor, paragraph_spacing = fit
    return TextBoxFitPlan(
        lines=lines,
        font_size=size,
        line_height_factor=line_factor,
        paragraph_spacing=paragraph_spacing,
        required_height=text_height_for_lines(lines, size, line_factor, paragraph_spacing),
        available_height=height,
    )


def pdf_token_font(token: str) -> str:
    if pdf_token_uses_math_font(token):
        if math_vector_font_path() is not None:
            return MATH_VECTOR_FONT
        return "helv"
    if re.fullmatch(r"[A-Za-z0-9._+:/%#?=&~×,;()[\]'\" -]+", token):
        return "helv"
    return VECTOR_FONT


def math_vector_font_path() -> str | None:
    for path in MATH_VECTOR_FONT_PATHS:
        if Path(path).exists():
            return path
    return None


def pdf_token_uses_math_font(token: str) -> bool:
    if not token or token.isspace() or cjk_char_count(token) > 0:
        return False
    return any(
        0x1D400 <= ord(char) <= 0x1D7FF
        or char in "∗†‡≤≥∑∏√∞≈≠⊕⊗∈∉∧∨∂∇"
        for char in token
    )


_MATH_FITZ_FONT = None


def math_fitz_font(fitz):
    global _MATH_FITZ_FONT
    if _MATH_FITZ_FONT is None:
        path = math_vector_font_path()
        if path is None:
            return None
        _MATH_FITZ_FONT = fitz.Font(fontfile=path)
    return _MATH_FITZ_FONT


def pdf_token_fontfile(token: str) -> str | None:
    if pdf_token_uses_math_font(token):
        return math_vector_font_path()
    return None


def pdf_token_width(fitz, token: str, font_size: float) -> float:
    if pdf_token_uses_math_font(token):
        font = math_fitz_font(fitz)
        if font is not None:
            return font.text_length(token, fontsize=font_size)
    return fitz.get_text_length(token, fontname=pdf_token_font(token), fontsize=font_size)


def pdf_text_width(fitz, text: str, font_size: float) -> float:
    if not text:
        return 0.0
    return sum(
        pdf_token_width(fitz, token, font_size)
        for token in drawable_pdf_line_tokens([text])
    )


def split_pdf_text_tokens(text: str) -> list[str]:
    return re.findall(
        r"\s+|[A-Za-z0-9][A-Za-z0-9._+:/%#?=&~×,;()'\"-]*|[!-/:-@\[-`{-~]+|[\u4e00-\u9fff]+|[^\sA-Za-z0-9\u4e00-\u9fff]",
        text,
        flags=re.S,
    )


def token_list_width(fitz, tokens: list[str], font_size: float) -> float:
    return sum(
        pdf_token_width(fitz, token, font_size)
        for token in drawable_pdf_line_tokens(tokens)
    )


def strip_trailing_space_tokens(tokens: list[str]) -> list[str]:
    while tokens and tokens[-1].isspace():
        tokens = tokens[:-1]
    return tokens


def break_current_line(lines, current):
    lines.append(strip_trailing_space_tokens(current))
    return []


def split_pdf_wrap_units(paragraph: str) -> list[str]:
    units = []
    split_latin_tokens = bool(re.search(r"[\u4e00-\u9fff]", paragraph))
    for token in split_pdf_text_tokens(paragraph):
        if token.isspace():
            units.append(" ")
        elif split_latin_tokens and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+:/%#?=&~×,;()'\"-]*", token):
            units.extend(token)
        else:
            units.append(token)
    return units


def small_overflow_tolerance(max_width: float, font_size: float) -> float:
    return max(font_size * 1.1, max_width * 0.02)


def wrap_mixed_pdf_text(fitz, text: str, max_width: float, font_size: float):
    lines = []
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    allowed_width = max_width + small_overflow_tolerance(max_width, font_size)
    for paragraph_idx, paragraph in enumerate(paragraphs):
        current = []
        units = split_pdf_wrap_units(paragraph)
        idx = 0
        while idx < len(units):
            token = units[idx]
            if token.isspace():
                token = " "
                if not current:
                    idx += 1
                    continue
            candidate_width = token_list_width(fitz, current + [token], font_size)
            if current and candidate_width > allowed_width:
                current = break_current_line(lines, current)
                if token.isspace():
                    idx += 1
                    continue
                continue
            current.append(token)
            idx += 1
        if current:
            lines.append(current)
        if paragraph_idx != len(paragraphs) - 1:
            lines.append([])
    return lines


def merge_pdf_line_tokens(line: list[str]) -> list[str]:
    merged = []
    for token in line:
        if not token:
            continue
        for part in split_pdf_text_tokens(token):
            if not part:
                continue
            if merged and pdf_token_font(merged[-1]) == pdf_token_font(part):
                merged[-1] += part
            else:
                merged.append(part)
    return merged


def clean_pdf_draw_tokens(tokens: list[str]) -> list[str]:
    cleaned = []
    for token in tokens:
        if token.isspace() and cleaned and cleaned[-1].isspace():
            continue
        if token:
            cleaned.append(token)
    return cleaned


def drawable_pdf_line_tokens(line: list[str]) -> list[str]:
    merged = merge_pdf_line_tokens(line)
    return clean_pdf_draw_tokens(merged)


def render_text_style_name(item: RenderItem) -> str:
    if item.style_name:
        return item.style_name
    if item.fallback_reason == "journal_footer":
        return "footer"
    if item.fallback_reason == "reference_original":
        return "reference"
    return "body"


def text_item_fit_metrics(item: RenderItem, fitz) -> tuple[tuple[float, float] | None, float, float]:
    style = text_style(render_text_style_name(item))
    font_size = item.font_size or style.font_size
    width = max(1.0, item.bbox[2] - item.bbox[0])
    lines = wrap_mixed_pdf_text(fitz, item.text, width, font_size)
    available = item.bbox[3] - item.bbox[1]
    plan = text_box_fit_plan(fitz, item.text, width, available, style, font_size=font_size)
    fit = (plan.line_height_factor, plan.paragraph_spacing) if plan is not None else None
    required = text_height_for_lines(
        lines,
        font_size,
        style.min_line_height_factor or style.line_height_factor,
        style.min_paragraph_spacing if style.min_paragraph_spacing is not None else style.paragraph_spacing,
    )
    return fit, required, available


def item_is_expandable_body_text(item: RenderItem) -> bool:
    return (
        item.kind in {"translated_text", "original_selectable_text"}
        and item.text.strip()
        and render_text_style_name(item) in EXPANDABLE_TEXT_STYLE_NAMES
    )


def layout_horizontal_conflict(a, b) -> bool:
    overlap = horizontal_overlap(a, b)
    if overlap <= 0:
        return False
    min_width = max(1.0, min(a[2] - a[0], b[2] - b[0]))
    return overlap >= min_width * 0.15 or overlap >= 12.0


def vertical_expansion_limits(item: RenderItem, items: list[RenderItem], page_size) -> tuple[float, float]:
    _width, page_height = page_size
    top_limit = 0.0
    bottom_limit = page_height
    item_center_y = bbox_center(item.bbox)[1]
    for other in items:
        if other is item or not layout_horizontal_conflict(item.bbox, other.bbox):
            continue
        if other.bbox[3] <= item.bbox[1]:
            top_limit = max(top_limit, other.bbox[3] + TEXT_VERTICAL_EXPANSION_GAP_PT)
        elif other.bbox[1] >= item.bbox[3]:
            bottom_limit = min(bottom_limit, other.bbox[1] - TEXT_VERTICAL_EXPANSION_GAP_PT)
        elif bbox_center(other.bbox)[1] < item_center_y:
            top_limit = max(top_limit, other.bbox[3] + TEXT_VERTICAL_EXPANSION_GAP_PT)
        else:
            bottom_limit = min(bottom_limit, other.bbox[1] - TEXT_VERTICAL_EXPANSION_GAP_PT)
    return top_limit, bottom_limit


def _visual_component_source_boxes(plan: PageRenderPlan) -> dict[str, tuple[float, float, float, float]]:
    return {
        component.component_id: component.source_bbox
        for component in plan.components
        if component.component_kind == "visual" and component.component_id
    }


def _replace_protected_box(plan: PageRenderPlan, old_box, new_box) -> None:
    old_key = tuple(round(float(value), 6) for value in old_box)
    for idx, box in enumerate(plan.protected_boxes):
        if tuple(round(float(value), 6) for value in box) == old_key:
            plan.protected_boxes[idx] = new_box


def release_visual_clip_overcapture_for_text_fit(plan: PageRenderPlan, page_size, fitz=None) -> None:
    """Trim visual padding only where it blocks a translated text box from fitting."""
    source_by_component = _visual_component_source_boxes(plan)
    if not source_by_component:
        return
    if fitz is None:
        fitz = _load_fitz()
    visual_items = [
        item
        for item in plan.items
        if item.kind == "original_image_clip" and item.component_id in source_by_component
    ]
    if not visual_items:
        return
    for text_item in sorted(plan.items, key=lambda candidate: (candidate.bbox[1], candidate.bbox[0])):
        if not item_is_expandable_body_text(text_item):
            continue
        fit, required, _available = text_item_fit_metrics(text_item, fitz)
        if fit is not None:
            continue
        desired_bottom = text_item.bbox[1] + required + TEXT_FIT_EPSILON_PT
        desired_top = text_item.bbox[3] - required - TEXT_FIT_EPSILON_PT
        for visual in visual_items:
            if not layout_horizontal_conflict(text_item.bbox, visual.bbox):
                continue
            source_bbox = source_by_component[visual.component_id]
            x0, y0, x1, y1 = visual.bbox
            next_y0, next_y1 = y0, y1
            if (
                y0 >= text_item.bbox[3] - TEXT_FIT_EPSILON_PT
                and y0 < desired_bottom + TEXT_VERTICAL_EXPANSION_GAP_PT
                and source_bbox[1] > y0 + TEXT_FIT_EPSILON_PT
            ):
                next_y0 = min(source_bbox[1], y1)
            if (
                y1 <= text_item.bbox[1] + TEXT_FIT_EPSILON_PT
                and y1 > desired_top - TEXT_VERTICAL_EXPANSION_GAP_PT
                and source_bbox[3] < y1 - TEXT_FIT_EPSILON_PT
            ):
                next_y1 = max(source_bbox[3], y0)
            if next_y0 == y0 and next_y1 == y1:
                continue
            old_box = visual.bbox
            visual.bbox = clamp_bbox((x0, next_y0, x1, next_y1), page_size)
            _replace_protected_box(plan, old_box, visual.bbox)


def expand_text_boxes_to_fit(plan: PageRenderPlan, page_size, fitz=None) -> None:
    if fitz is None:
        fitz = _load_fitz()
    for item in sorted(plan.items, key=lambda candidate: (candidate.bbox[1], candidate.bbox[0])):
        if not item_is_expandable_body_text(item):
            continue
        fit, required, available = text_item_fit_metrics(item, fitz)
        if fit is not None:
            continue
        target_height = required + TEXT_FIT_EPSILON_PT
        needed = target_height - available
        if needed <= 0:
            continue
        top_limit, bottom_limit = vertical_expansion_limits(item, plan.items, page_size)
        x0, y0, x1, y1 = item.bbox
        grow_down = min(max(0.0, bottom_limit - y1), needed)
        y1 += grow_down
        needed -= grow_down
        grow_up = min(max(0.0, y0 - top_limit), needed)
        y0 -= grow_up
        item.bbox = clamp_bbox((x0, y0, x1, y1), page_size)


def preferred_text_height_for_item(item: RenderItem, fitz) -> float:
    style = text_style(render_text_style_name(item))
    font_size = item.font_size or style.font_size
    width = max(1.0, item.bbox[2] - item.bbox[0])
    lines = wrap_mixed_pdf_text(fitz, item.text, width, font_size)
    return text_height_for_lines(lines, font_size, style.line_height_factor, style.paragraph_spacing)


def minimum_text_height_for_item(item: RenderItem, fitz) -> float:
    style = text_style(render_text_style_name(item))
    font_size = item.font_size or style.font_size
    width = max(1.0, item.bbox[2] - item.bbox[0])
    lines = wrap_mixed_pdf_text(fitz, item.text, width, font_size)
    return text_height_for_lines(
        lines,
        font_size,
        style.min_line_height_factor or style.line_height_factor,
        style.min_paragraph_spacing if style.min_paragraph_spacing is not None else style.paragraph_spacing,
    )


def item_is_formula_intro_text(item: RenderItem) -> bool:
    text = normalize_text(item.text)
    return bool(
        re.match(r"^(引理|定理|命题|推论)\s*\d+", text)
        and ("：" in text or ":" in text or "不变式" in text or "断言" in text)
        and len(text) <= 80
    )


def item_is_body_layout_text(item: RenderItem) -> bool:
    return (
        item.kind in {"translated_text", "original_selectable_text"}
        and item.text.strip()
        and render_text_style_name(item) in TEXT_FLOW_STYLE_NAMES
        and item.fallback_reason not in TEXT_FLOW_EXCLUDED_FALLBACK_REASONS
        and not item_is_formula_intro_text(item)
    )


def lane_shares_item(lane_box, item: RenderItem) -> bool:
    overlap = horizontal_overlap(lane_box, item.bbox)
    min_width = max(1.0, min(lane_box[2] - lane_box[0], item.bbox[2] - item.bbox[0]))
    return overlap >= min_width * 0.50


def body_layout_lanes(plan: PageRenderPlan) -> list[list[int]]:
    candidates = [
        (idx, item)
        for idx, item in enumerate(plan.items)
        if item_is_body_layout_text(item)
    ]
    lanes: list[list[int]] = []
    lane_boxes: list[tuple[float, float, float, float]] = []
    for idx, item in sorted(candidates, key=lambda pair: (pair[1].bbox[0], pair[1].bbox[1])):
        target = None
        for lane_idx, lane_box in enumerate(lane_boxes):
            if lane_shares_item(lane_box, item):
                target = lane_idx
                break
        if target is None:
            lanes.append([idx])
            lane_boxes.append(item.bbox)
            continue
        lanes[target].append(idx)
        lane_boxes[target] = bbox_union([lane_boxes[target], item.bbox])
    return [sorted(lane, key=lambda idx: plan.items[idx].bbox[1]) for lane in lanes]


def anchor_between_body_items(left: RenderItem, right: RenderItem, anchors: list[RenderItem]) -> bool:
    if right.bbox[1] < left.bbox[1]:
        left, right = right, left
    lane_box = bbox_union([left.bbox, right.bbox])
    for anchor in anchors:
        if anchor.bbox[1] >= right.bbox[1] or anchor.bbox[3] <= left.bbox[3]:
            continue
        if layout_horizontal_conflict(lane_box, anchor.bbox):
            return True
    return False


def split_body_layout_lane(plan: PageRenderPlan, lane: list[int]) -> list[list[int]]:
    if not lane:
        return []
    body_indices = set(lane)
    anchors = [
        item
        for idx, item in enumerate(plan.items)
        if idx not in body_indices and bbox_area(item.bbox) > 0
    ]
    groups = []
    current = [lane[0]]
    for idx in lane[1:]:
        previous = plan.items[current[-1]]
        item = plan.items[idx]
        if anchor_between_body_items(previous, item, anchors):
            groups.append(current)
            current = [idx]
            continue
        current.append(idx)
    groups.append(current)
    return groups


def body_group_limits(plan: PageRenderPlan, group: list[int], page_size) -> tuple[float, float, bool, bool] | None:
    _width, page_height = page_size
    group_set = set(group)
    group_items = [plan.items[idx] for idx in group]
    group_box = bbox_union([item.bbox for item in group_items])
    top_limit = 0.0
    bottom_limit = page_height
    has_top_anchor = False
    has_bottom_anchor = False
    for idx, other in enumerate(plan.items):
        if idx in group_set or bbox_area(other.bbox) <= 0:
            continue
        if not layout_horizontal_conflict(group_box, other.bbox):
            continue
        if other.bbox[3] <= group_box[1]:
            top_limit = max(top_limit, other.bbox[3] + TEXT_PROTECTED_GAP_PT)
            has_top_anchor = True
            continue
        if other.bbox[1] >= group_box[3]:
            bottom_limit = min(bottom_limit, other.bbox[1] - TEXT_PROTECTED_GAP_PT)
            has_bottom_anchor = True
            continue
        if other.bbox[3] <= group_box[3] and other.bbox[1] <= group_box[1]:
            top_limit = max(top_limit, other.bbox[3] + TEXT_PROTECTED_GAP_PT)
            has_top_anchor = True
            continue
        if other.bbox[1] >= group_box[1] and other.bbox[3] >= group_box[3]:
            bottom_limit = min(bottom_limit, other.bbox[1] - TEXT_PROTECTED_GAP_PT)
            has_bottom_anchor = True
            continue
        return None
    if bottom_limit <= top_limit:
        return None
    return top_limit, bottom_limit, has_top_anchor, has_bottom_anchor


def body_group_max_gap(items: list[RenderItem]) -> float:
    if items and all(item.fallback_reason == "source_paragraph_split" for item in items):
        return SOURCE_PARAGRAPH_FLOW_MAX_GAP_PT
    return BODY_FLOW_MAX_GAP_PT


def body_group_heights_and_gap(items: list[RenderItem], available_span: float, fitz) -> tuple[list[float], float]:
    preferred = [preferred_text_height_for_item(item, fitz) + BODY_TEXT_BOX_CUSHION_PT for item in items]
    minimum = [minimum_text_height_for_item(item, fitz) for item in items]
    max_gap = body_group_max_gap(items)
    if len(items) <= 1:
        if preferred[0] <= available_span:
            return preferred, 0.0
        if minimum[0] <= available_span:
            return [available_span], 0.0
        return minimum, 0.0

    preferred_gap_capacity = (available_span - sum(preferred)) / (len(items) - 1)
    if preferred_gap_capacity >= BODY_FLOW_MIN_GAP_PT:
        return preferred, min(max_gap, preferred_gap_capacity)

    minimum_gap_capacity = (available_span - sum(minimum)) / (len(items) - 1)
    if minimum_gap_capacity >= BODY_FLOW_MIN_GAP_PT:
        return minimum, min(max_gap, minimum_gap_capacity)
    return minimum, max(0.0, minimum_gap_capacity)


def backfill_body_group_before_bottom_anchor(
    items: list[RenderItem],
    top_limit: float,
    bottom_limit: float,
    original_start: float,
    fitz,
) -> tuple[float, list[float], float] | None:
    """Move an unanchored text run upward just enough to fit before a protected region."""
    minimum = [minimum_text_height_for_item(item, fitz) for item in items]
    required_text_height = sum(minimum)
    full_span = bottom_limit - top_limit
    if required_text_height > full_span + TEXT_FIT_EPSILON_PT:
        return None
    if len(items) <= 1:
        gap = 0.0
    else:
        gap_capacity = (full_span - required_text_height) / (len(items) - 1)
        gap = min(BODY_FLOW_MIN_GAP_PT, max(0.0, gap_capacity))
    required_span = required_text_height + gap * max(0, len(items) - 1)
    if required_span > full_span + TEXT_FIT_EPSILON_PT:
        return None
    start_y = min(original_start, bottom_limit - required_span)
    start_y = max(top_limit, start_y)
    return start_y, minimum, gap


def rebalance_body_text_flows(plan: PageRenderPlan, page_size, fitz=None) -> None:
    if fitz is None:
        fitz = _load_fitz()
    for lane in body_layout_lanes(plan):
        for group in split_body_layout_lane(plan, lane):
            items = [plan.items[idx] for idx in group]
            limits = body_group_limits(plan, group, page_size)
            if limits is None:
                continue
            top_limit, bottom_limit, has_top_anchor, has_bottom_anchor = limits
            original_start = min(item.bbox[1] for item in items)
            if has_top_anchor:
                start_y = max(top_limit, min(original_start, top_limit + BODY_FLOW_TOP_MAX_GAP_PT))
            else:
                start_y = original_start

            available_span = bottom_limit - start_y
            heights, gap = body_group_heights_and_gap(items, available_span, fitz)
            if sum(heights) + gap * max(0, len(items) - 1) > available_span and has_top_anchor:
                start_y = top_limit
                available_span = bottom_limit - start_y
                heights, gap = body_group_heights_and_gap(items, available_span, fitz)
            if (
                sum(heights) + gap * max(0, len(items) - 1) > available_span + TEXT_FIT_EPSILON_PT
                and not has_top_anchor
                and has_bottom_anchor
            ):
                backfilled = backfill_body_group_before_bottom_anchor(
                    items,
                    top_limit,
                    bottom_limit,
                    original_start,
                    fitz,
                )
                if backfilled is not None:
                    start_y, heights, gap = backfilled
                    available_span = bottom_limit - start_y
            if sum(heights) + gap * max(0, len(items) - 1) > available_span + TEXT_FIT_EPSILON_PT:
                continue

            y = start_y
            for item, height in zip(items, heights):
                x0, _y0, x1, _y1 = item.bbox
                item.bbox = clamp_bbox((x0, y, x1, y + height), page_size)
                y += height + gap


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int):
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    lines = []
    dummy = ImageDraw.Draw(Image.new("RGB", (10, 10), "white"))

    def text_width(value: str) -> int:
        bbox = dummy.textbbox((0, 0), value, font=font)
        return bbox[2] - bbox[0]

    def tokens_for(paragraph: str) -> list[str]:
        return re.findall(
            r"\s+|[A-Za-z0-9][A-Za-z0-9._+:/%#?=&~×-]*|.",
            paragraph,
            flags=re.S,
        )

    def append_long_token(token: str, current: str):
        for ch in token:
            candidate = current + ch
            if text_width(candidate) <= max_width or not current:
                current = candidate
            else:
                lines.append(current.rstrip())
                current = ch
        return current

    for paragraph in paragraphs:
        current = ""
        for token in tokens_for(paragraph):
            if token.isspace() and not current:
                continue
            candidate = current + token
            if text_width(candidate) <= max_width or not current:
                current = candidate
            else:
                lines.append(current.rstrip())
                token = token.lstrip()
                if not token:
                    current = ""
                elif text_width(token) <= max_width:
                    current = token
                else:
                    current = append_long_token(token, "")
            if current and text_width(current) > max_width:
                current = append_long_token(current, "")
        if current:
            lines.append(current.rstrip())
    return lines or [text]


def fit_font_and_lines(
    text: str,
    box_width: int,
    box_height: int,
    vertical: bool,
    max_font_size: int | None = None,
):
    if vertical:
        max_size = max(14, min(box_width, box_height // 2))
        return max_size, [text]

    low, high = 10, max(12, min(80, box_height))
    if max_font_size is not None:
        high = max(low, min(high, max_font_size))
    best = (10, wrap_text(text, raster_image_font(10), box_width))
    while low <= high:
        mid = (low + high) // 2
        font = raster_image_font(mid)
        lines = wrap_text(text, font, box_width)
        line_height = raster_line_height(font)
        total_height = line_height * max(1, len(lines))
        if total_height <= box_height:
            best = (mid, lines)
            low = mid + 1
        else:
            high = mid - 1
    return best


def source_line_count(text: str) -> int:
    return max(1, normalize_text(text).count("\n") + 1)


def target_font_size_for_block(block, dpi: int, vertical: bool) -> int | None:
    if vertical:
        return None
    scale = dpi / 72.0
    block_height_px = max(1.0, (block["yMax"] - block["yMin"]) * scale)
    source_line_height = block_height_px / source_line_count(block.get("text", ""))
    return max(10, int(round(source_line_height / RASTER_LINE_HEIGHT_FACTOR * SOURCE_FONT_SCALE)))


def raster_text_should_render_vertical(text: str, box) -> bool:
    x0, y0, x1, y1 = box
    normalized = normalize_text(text)
    if len(normalized) > RASTER_VERTICAL_TEXT_MAX_SOURCE_CHARS:
        return False
    if "\n" in normalized:
        return False
    return (y1 - y0) > (x1 - x0) * 3 and len(normalized) > 4


def target_font_size_points_for_block(block, *, max_size: float = 30.0) -> float:
    block_height_pt = max(1.0, block["yMax"] - block["yMin"])
    source_line_height = block_height_pt / source_line_count(block.get("text", ""))
    return max(6.0, min(max_size, source_line_height / 1.25 * SOURCE_FONT_SCALE))


def _overlap_area(box_a, box_b):
    x0 = max(box_a[0], box_b[0])
    y0 = max(box_a[1], box_b[1])
    x1 = min(box_a[2], box_b[2])
    y1 = min(box_a[3], box_b[3])
    if x1 <= x0 or y1 <= y0:
        return 0
    return (x1 - x0) * (y1 - y0)


def avoid_protected_boxes(box, protected_boxes):
    x0, y0, x1, y1 = box
    for protected in protected_boxes or []:
        if _overlap_area((x0, y0, x1, y1), protected) <= 0:
            continue
        protected_center_y = (protected[1] + protected[3]) / 2
        if y1 <= protected_center_y:
            y1 = min(y1, protected[1])
        elif y0 >= protected_center_y:
            y0 = max(y0, protected[3])
        else:
            top_space = protected[1] - y0
            bottom_space = y1 - protected[3]
            if top_space >= bottom_space:
                y1 = min(y1, protected[1])
            else:
                y0 = max(y0, protected[3])
        if x1 - x0 < 8 or y1 - y0 < 8:
            return None
    return x0, y0, x1, y1


def text_required_height(text: str, width: int, font_size: int) -> int:
    font = raster_image_font(font_size)
    lines = wrap_text(text, font, width)
    line_height = raster_line_height(font)
    return line_height * max(1, len(lines)) + 4


def boxes_horizontally_conflict(box_a, box_b) -> bool:
    overlap = horizontal_overlap(box_a, box_b)
    min_width = max(1, min(box_a[2] - box_a[0], box_b[2] - box_b[0]))
    return overlap >= min_width * 0.2


def build_render_boxes(
    blocks,
    translations,
    dpi: int,
    page_width: int,
    page_height: int,
    protected_boxes,
    *,
    block_to_px_box,
    translation_resolver,
):
    base_boxes = {}
    for block in blocks:
        if should_preserve_as_image(block):
            continue
        if is_page_number(block["text"]):
            continue
        translation = translation_resolver(block, translations)
        if not translation:
            continue
        box = block_to_px_box(block, dpi, page_width, page_height, pad=2)
        box = avoid_protected_boxes(box, protected_boxes)
        if box is not None:
            base_boxes[block["id"]] = box

    render_boxes = dict(base_boxes)
    occupied = list(base_boxes.items())
    protected_occupied = [(f"protected-{idx}", box) for idx, box in enumerate(protected_boxes or [])]
    for block in blocks:
        box = base_boxes.get(block["id"])
        if box is None:
            continue
        translation = translation_resolver(block, translations)
        x0, y0, x1, y1 = box
        width = max(10, x1 - x0 - 4)
        vertical = raster_text_should_render_vertical(block.get("text", ""), (x0, y0, x1, y1))
        target_font_size = target_font_size_for_block(block, dpi, vertical)
        if target_font_size is None:
            continue
        required_height = text_required_height(translation, width, target_font_size)
        current_height = y1 - y0
        if required_height <= current_height:
            continue

        top_limit = TEXT_BOX_MARGIN_PX
        bottom_limit = page_height - TEXT_BOX_MARGIN_PX
        for other_id, other_box in occupied + protected_occupied:
            if other_id == block["id"]:
                continue
            if not boxes_horizontally_conflict(box, other_box):
                continue
            if other_box[3] <= y0:
                top_limit = max(top_limit, other_box[3] + TEXT_BOX_MARGIN_PX)
            elif other_box[1] >= y1:
                bottom_limit = min(bottom_limit, other_box[1] - TEXT_BOX_MARGIN_PX)

        remaining = required_height - current_height
        grow_down = max(0, min(remaining, bottom_limit - y1))
        y1 += grow_down
        remaining -= grow_down
        grow_up = max(0, min(remaining, y0 - top_limit))
        y0 -= grow_up
        render_boxes[block["id"]] = (x0, y0, x1, y1)
    return render_boxes


def style_name_for_heading_text(text: str) -> str:
    normalized = normalize_text(text)
    if re.match(r"^\s*\d+\.\d+", normalized):
        return "subheading"
    return "heading"


def style_name_for_block(block, classification: str) -> str:
    if classification == "subheading":
        return "subheading"
    if classification == "heading":
        return style_name_for_heading_text(block.get("text", ""))
    if classification == "title":
        return "title"
    if classification == "reference":
        return "reference"
    if classification == "journal_footer":
        return "footer"
    return "body"


def render_font_size_for_block(block, classification: str) -> float:
    return render_font_size_for_style(block, style_name_for_block(block, classification))


def source_adapted_font_size_allowed(block, style_name: str) -> bool:
    if style_name not in SOURCE_ADAPTED_FONT_MAX_BY_STYLE:
        return False
    if style_name == "body":
        return (
            source_line_count(block.get("text", "")) >= SOURCE_ADAPTED_BODY_MIN_LINES
            and block["yMax"] - block["yMin"] >= BODY_FONT_SIZE * SOURCE_ADAPTED_BODY_MIN_LINES
        )
    return block["yMax"] - block["yMin"] >= text_style(style_name).font_size * SOURCE_ADAPTED_HEADING_MIN_HEIGHT_FACTOR


def render_font_size_for_style(block, style_name: str) -> float:
    style = text_style(style_name)
    if not source_adapted_font_size_allowed(block, style_name):
        return style.font_size
    target = target_font_size_points_for_block(
        block,
        max_size=SOURCE_ADAPTED_FONT_MAX_BY_STYLE[style_name],
    )
    if target < style.font_size + SOURCE_ADAPTED_FONT_MIN_DELTA_PT:
        return style.font_size
    return round(max(style.font_size, target), 1)


def usable_text_segment(segment) -> bool:
    return segment[2] - segment[0] >= 80.0 and segment[3] - segment[1] >= BODY_FONT_SIZE * 1.8


def text_box_overlaps_protected(box, protected_boxes) -> bool:
    return any(bbox_significantly_overlaps_protected(box, protected.bbox) for protected in protected_boxes)


def text_box_overlaps_text_anchor(box, anchors: list[RenderItem]) -> bool:
    return any(bbox_significantly_overlaps_protected(box, anchor.bbox) for anchor in anchors)


def clamp_preserving_box_size(box, page_size) -> tuple[float, float, float, float] | None:
    page_width, page_height = page_size
    x0, y0, x1, y1 = box
    width = x1 - x0
    height = y1 - y0
    if width <= 0.0 or height <= 0.0 or width > page_width or height > page_height:
        return None
    x0 = min(max(0.0, x0), page_width - width)
    y0 = min(max(0.0, y0), page_height - height)
    return (x0, y0, x0 + width, y0 + height)


def shifted_boxes_around_protected(box, protected_boxes, page_size) -> list[tuple[float, float, float, float]]:
    width = box[2] - box[0]
    height = box[3] - box[1]
    candidates = []
    for protected in protected_boxes:
        if not text_box_overlaps_protected(box, [protected]):
            continue
        px0, py0, px1, py1 = protected.bbox
        raw_candidates = [
            (px0 - TEXT_PROTECTED_GAP_PT - width, box[1], px0 - TEXT_PROTECTED_GAP_PT, box[3]),
            (px1 + TEXT_PROTECTED_GAP_PT, box[1], px1 + TEXT_PROTECTED_GAP_PT + width, box[3]),
            (box[0], py0 - TEXT_PROTECTED_GAP_PT - height, box[2], py0 - TEXT_PROTECTED_GAP_PT),
            (box[0], py1 + TEXT_PROTECTED_GAP_PT, box[2], py1 + TEXT_PROTECTED_GAP_PT + height),
        ]
        for candidate in raw_candidates:
            clamped = clamp_preserving_box_size(candidate, page_size)
            if clamped is not None:
                candidates.append(clamped)
    unique = []
    seen = set()
    for candidate in candidates:
        key = tuple(round(value, 3) for value in candidate)
        if key in seen or text_box_overlaps_protected(candidate, protected_boxes):
            continue
        seen.add(key)
        unique.append(candidate)
    return sorted(
        unique,
        key=lambda candidate: (
            abs(candidate[0] - box[0]) + abs(candidate[1] - box[1]),
            -bbox_area(candidate),
        ),
    )


def text_segments_around_protected(box, protected_boxes) -> list[tuple[float, float, float, float]]:
    segments = [box]
    for protected in sorted(protected_boxes, key=lambda item: item.bbox[1]):
        next_segments = []
        for segment in segments:
            if not bbox_significantly_overlaps_protected(segment, protected.bbox):
                next_segments.append(segment)
                continue
            top = (segment[0], segment[1], segment[2], min(segment[3], protected.bbox[1] - TEXT_PROTECTED_GAP_PT))
            bottom = (segment[0], max(segment[1], protected.bbox[3] + TEXT_PROTECTED_GAP_PT), segment[2], segment[3])
            left = (segment[0], segment[1], min(segment[2], protected.bbox[0] - TEXT_PROTECTED_GAP_PT), segment[3])
            right = (max(segment[0], protected.bbox[2] + TEXT_PROTECTED_GAP_PT), segment[1], segment[2], segment[3])
            if usable_text_segment(top):
                next_segments.append(top)
            if usable_text_segment(bottom):
                next_segments.append(bottom)
            if usable_text_segment(left):
                next_segments.append(left)
            if usable_text_segment(right):
                next_segments.append(right)
        segments = next_segments
        if not segments:
            return [box]
    unique = []
    seen = set()
    for segment in segments:
        key = tuple(round(value, 3) for value in segment)
        if key in seen:
            continue
        seen.add(key)
        unique.append(segment)
    return sorted(unique, key=lambda item: (item[1], item[0]))


def split_text_units(text: str) -> list[str]:
    paragraphs = [paragraph.strip() for paragraph in text.split("\n") if paragraph.strip()]
    if len(paragraphs) != 1:
        return paragraphs
    pieces = re.split(r"(?<=[。！？；;])\s*", paragraphs[0])
    return [piece.strip() for piece in pieces if piece.strip()] or paragraphs


def text_units_fit_segment(units: list[str], segment, style: TextStyle, fitz) -> bool:
    if not units:
        return True
    font_size = style.font_size
    lines = wrap_mixed_pdf_text(fitz, "\n".join(units), segment[2] - segment[0], font_size)
    return fitted_text_spacing(lines, font_size, segment[3] - segment[1], style) is not None


def distribute_text_across_segments(text: str, segments, style: TextStyle | None = None, fitz=None) -> list[str]:
    units = split_text_units(text)
    if len(segments) <= 1 or len(units) <= 1:
        return [text]
    if style is None:
        style = text_style("body")
    if fitz is None:
        fitz = _load_fitz()
    result = []
    start = 0
    for idx, segment in enumerate(segments):
        if idx == len(segments) - 1:
            result.append("\n".join(units[start:]))
            break
        best_end = start
        for end in range(start + 1, len(units) + 1):
            if not text_units_fit_segment(units[start:end], segment, style, fitz):
                break
            best_end = end
        result.append("\n".join(units[start:best_end]))
        start = best_end
    while len(result) < len(segments):
        result.append("")
    return result


def split_translated_text_around_protected(plan: PageRenderPlan, page_size=None, fitz=None) -> None:
    protected = [item for item in plan.items if item.kind == "original_image_clip"]
    if not protected:
        return
    new_items = []
    for item in plan.items:
        if item.kind != "translated_text":
            new_items.append(item)
            continue
        if not any(bbox_significantly_overlaps_protected(item.bbox, protected_item.bbox) for protected_item in protected):
            new_items.append(item)
            continue
        segments = []
        if page_size is not None:
            segments = shifted_boxes_around_protected(item.bbox, protected, page_size)
            if segments:
                anchors = [
                    other
                    for other in plan.items
                    if other is not item
                    and other.kind in {"translated_text", "original_selectable_text"}
                    and render_text_style_name(other) in {"footer", "reference", "heading", "subheading", "title"}
                ]
                segments = [segment for segment in segments if not text_box_overlaps_text_anchor(segment, anchors)]
        if not segments:
            segments = text_segments_around_protected(item.bbox, protected)
        if len(segments) == 1 and segments[0] == item.bbox:
            new_items.append(item)
            continue
        texts = distribute_text_across_segments(item.text, segments, text_style(item.style_name or "body"), fitz=fitz)
        replacement_items = []
        for segment, text in zip(segments, texts):
            if not text.strip():
                continue
            replacement_items.append(
                RenderItem(
                    item.kind,
                    list(item.source_ids),
                    segment,
                    text=text,
                    font_size=item.font_size,
                    style_name=item.style_name,
                    color=item.color,
                    fallback_reason=item.fallback_reason or "split_around_visual",
                )
            )
        if not replacement_items:
            new_items.append(item)
            continue
        new_items.extend(replacement_items)
        update_ledger_render_kind(plan, item.source_ids, "translated_text", "split_around_visual")
    plan.items = new_items


def validate_plan_style_policy(plan: PageRenderPlan) -> list[str]:
    errors = []
    hierarchy = [
        ("title", "heading"),
        ("heading", "subheading"),
        ("subheading", "body"),
        ("body", "footer"),
        ("body", "reference"),
    ]
    for larger, smaller in hierarchy:
        larger_size = text_style(larger).font_size
        smaller_size = text_style(smaller).font_size
        if larger_size <= smaller_size:
            errors.append(
                f"style hierarchy violation: {larger} font size {larger_size} must be greater than"
                f" {smaller} font size {smaller_size}"
            )

    ledger_by_id = {entry.block_id: entry for entry in plan.ledger}
    for item in plan.items:
        if item.kind not in {"translated_text", "original_selectable_text"}:
            continue
        item_ledger_entries = [ledger_by_id[source_id] for source_id in item.source_ids if source_id in ledger_by_id]
        explicit_exception = item.fallback_reason in STYLE_POLICY_ROLE_SPLIT_EXCEPTIONS
        allowed_styles = set().union(*(
            STYLE_POLICY_CLASSIFICATION_STYLES[entry.classification]
            for entry in item_ledger_entries
            if entry.classification in STYLE_POLICY_CLASSIFICATION_STYLES
        ))
        actual_name = render_text_style_name(item)
        if not allowed_styles:
            allowed_styles = {actual_name}
        expected_name = actual_name if actual_name in allowed_styles else sorted(allowed_styles)[0]
        style = text_style(actual_name)
        if not explicit_exception and actual_name not in allowed_styles:
            expected = "/".join(sorted(allowed_styles))
            errors.append(f"page {plan.page_num} item {item.source_ids} has style {actual_name}, expected {expected}")
        if not explicit_exception and (item.font_size is None or abs(item.font_size - style.font_size) > 0.01):
            errors.append(f"page {plan.page_num} item {item.source_ids} has font size {item.font_size}, expected {style.font_size}")
    return errors


def validate_plan_text_fit(plan: PageRenderPlan, fitz=None) -> list[str]:
    if fitz is None:
        fitz = _load_fitz()
    errors = []
    for item in plan.items:
        if item.kind not in {"translated_text", "original_selectable_text"} or not item.text.strip():
            continue
        fit, required, available = text_item_fit_metrics(item, fitz)
        if fit is None:
            errors.append(
                f"page {plan.page_num} text {item.source_ids or item.fallback_reason}"
                f" needs {required:.1f}pt height but has {available:.1f}pt for style {render_text_style_name(item)}"
            )
    return errors
