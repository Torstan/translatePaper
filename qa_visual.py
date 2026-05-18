import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from PIL import Image


SEVERITY_ORDER = {
    "info": 0,
    "warning": 1,
    "error": 2,
}

EXPECTED_STYLES_BY_CLASSIFICATION = {
    "title": {"title"},
    "heading": {"heading", "subheading"},
    "subheading": {"subheading"},
    "body": {"body"},
    "reference": {"reference"},
    "header_footer": {"footer"},
    "journal_footer": {"footer"},
    "page_number": {"footer"},
}

TEXT_RENDER_KINDS = {"translated_text", "original_selectable_text"}
BODY_FLOW_TEXT_BARRIER_STYLES = {"title", "heading", "subheading"}
MIN_BODY_FLOW_BARRIER_HEIGHT = 6.0


@dataclass(frozen=True)
class DarkPixelAnalysis:
    image_path: str
    bbox: tuple[float, float, float, float]
    crop_box: tuple[int, int, int, int]
    dark_pixel_count: int
    total_pixel_count: int

    @property
    def is_blank(self) -> bool:
        return self.dark_pixel_count <= 0


@dataclass(frozen=True)
class PageImagePair:
    source_path: str
    destination_path: str
    source: Image.Image
    destination: Image.Image


@dataclass(frozen=True)
class VisualQaIssue:
    category: str
    severity: str
    page_num: int
    message: str
    source_ids: list[str] = field(default_factory=list)
    bbox: tuple[float, float, float, float] | None = None
    render_kind: str = ""
    artifact_paths: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class VisualQaReport:
    json_path: Path
    markdown_path: Path
    issue_count: int
    error_count: int
    warning_count: int


def load_fitz():
    try:
        import fitz
    except ImportError as exc:
        raise SystemExit(
            "PyMuPDF is required for visual QA. Install with: python3 -m pip install pymupdf"
        ) from exc
    return fitz


def load_page_image(path: str | Path) -> Image.Image:
    with Image.open(path) as image:
        return image.convert("RGB")


def load_page_image_pair(source_path: str | Path, destination_path: str | Path) -> PageImagePair:
    return PageImagePair(
        source_path=str(source_path),
        destination_path=str(destination_path),
        source=load_page_image(source_path),
        destination=load_page_image(destination_path),
    )


def _page_to_pixel_crop_box(image: Image.Image, bbox, page_size) -> tuple[int, int, int, int]:
    page_width, page_height = page_size
    if page_width <= 0 or page_height <= 0:
        return (0, 0, 0, 0)

    scale_x = image.width / page_width
    scale_y = image.height / page_height
    x0, y0, x1, y1 = bbox
    return (
        max(0, int(math.floor(x0 * scale_x))),
        max(0, int(math.floor(y0 * scale_y))),
        min(image.width, int(math.ceil(x1 * scale_x))),
        min(image.height, int(math.ceil(y1 * scale_y))),
    )


def analyze_dark_pixels(
    image_path: str | Path,
    bbox,
    page_size,
    *,
    darkness_threshold: int = 245,
) -> DarkPixelAnalysis:
    image = load_page_image(image_path).convert("L")
    crop_box = _page_to_pixel_crop_box(image, bbox, page_size)
    if crop_box[2] <= crop_box[0] or crop_box[3] <= crop_box[1]:
        return DarkPixelAnalysis(
            image_path=str(image_path),
            bbox=tuple(float(value) for value in bbox),
            crop_box=crop_box,
            dark_pixel_count=0,
            total_pixel_count=0,
        )

    crop = image.crop(crop_box)
    dark_pixels = sum(1 for pixel in crop.getdata() if pixel < darkness_threshold)
    return DarkPixelAnalysis(
        image_path=str(image_path),
        bbox=tuple(float(value) for value in bbox),
        crop_box=crop_box,
        dark_pixel_count=dark_pixels,
        total_pixel_count=crop.width * crop.height,
    )


def _dark_pixel_bbox(
    image_path: str | Path,
    bbox,
    page_size,
    *,
    darkness_threshold: int = 245,
) -> tuple[float, float, float, float] | None:
    image = load_page_image(image_path).convert("L")
    crop_box = _page_to_pixel_crop_box(image, bbox, page_size)
    if crop_box[2] <= crop_box[0] or crop_box[3] <= crop_box[1]:
        return None

    crop = image.crop(crop_box)
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

    page_width, page_height = page_size
    scale_x = image.width / page_width
    scale_y = image.height / page_height
    return (
        (crop_box[0] + min_x) / scale_x,
        (crop_box[1] + min_y) / scale_y,
        (crop_box[0] + max_x + 1) / scale_x,
        (crop_box[1] + max_y + 1) / scale_y,
    )


def _plan_page_num(plan) -> int:
    if isinstance(plan, Mapping):
        return int(plan["page_num"])
    return int(plan.page_num)


def _plan_render_items(plan) -> list:
    if isinstance(plan, Mapping):
        return list(plan.get("render_items", []))
    return list(plan.items)


def _plan_protected_regions(plan) -> list:
    if isinstance(plan, Mapping):
        return list(plan.get("protected_regions", []))
    return [{"bbox": bbox} for bbox in getattr(plan, "protected_boxes", [])]


def _plan_ledger_entries(plan) -> list:
    if isinstance(plan, Mapping):
        return list(plan.get("coverage_ledger", []))
    return list(getattr(plan, "ledger", []))


def _item_value(item, name: str, default=None):
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _bbox_tuple(bbox) -> tuple[float, float, float, float]:
    return tuple(float(value) for value in bbox)


def _bbox_area(bbox) -> float:
    x0, y0, x1, y1 = bbox
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def _bbox_overlap_area(left, right) -> float:
    x0 = max(left[0], right[0])
    y0 = max(left[1], right[1])
    x1 = min(left[2], right[2])
    y1 = min(left[3], right[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def _bbox_overlap_height(left, right) -> float:
    return max(0.0, min(left[3], right[3]) - max(left[1], right[1]))


def _significant_protected_overlap(text_bbox, protected_bbox) -> bool:
    if _bbox_overlap_height(text_bbox, protected_bbox) <= 6.0:
        return False
    overlap = _bbox_overlap_area(text_bbox, protected_bbox)
    return overlap > min(_bbox_area(text_bbox), _bbox_area(protected_bbox)) * 0.05


def _significant_text_overlap(left_bbox, right_bbox) -> bool:
    if _bbox_overlap_height(left_bbox, right_bbox) <= 3.0:
        return False
    overlap = _bbox_overlap_area(left_bbox, right_bbox)
    return overlap > min(_bbox_area(left_bbox), _bbox_area(right_bbox)) * 0.12


def _source_ids_for_item(item) -> list[str]:
    return [str(source_id) for source_id in _item_value(item, "source_ids", [])]


def _ledger_value(entry, name: str, default=None):
    if isinstance(entry, Mapping):
        return entry.get(name, default)
    return getattr(entry, name, default)


def _ledger_by_block_id(plan) -> dict[str, object]:
    return {str(_ledger_value(entry, "block_id")): entry for entry in _plan_ledger_entries(plan)}


def _classifications_by_block_id(plan) -> dict[str, str]:
    return {
        block_id: str(_ledger_value(entry, "classification", ""))
        for block_id, entry in _ledger_by_block_id(plan).items()
    }


def _classification_for_item(item, classifications_by_id: Mapping[str, str]) -> str:
    for source_id in _source_ids_for_item(item):
        classification = classifications_by_id.get(source_id)
        if classification:
            return classification
    return ""


def _block_bbox(block) -> tuple[float, float, float, float]:
    if "bbox" in block:
        return _bbox_tuple(block["bbox"])
    return (
        float(block["xMin"]),
        float(block["yMin"]),
        float(block["xMax"]),
        float(block["yMax"]),
    )


def _body_source_block_overcaptured(block_bbox, clip_bbox) -> bool:
    area = _bbox_area(block_bbox)
    return area > 0 and _bbox_overlap_area(block_bbox, clip_bbox) >= area * 0.35


def _expanded_bbox(bbox, page_size, amount: float) -> tuple[float, float, float, float]:
    width, height = page_size
    return (
        max(0.0, bbox[0] - amount),
        max(0.0, bbox[1] - amount),
        min(float(width), bbox[2] + amount),
        min(float(height), bbox[3] + amount),
    )


def _dedupe_protected_regions(regions: list[dict]) -> list[dict]:
    by_bbox = {}
    for region in regions:
        key = tuple(round(value, 3) for value in region["bbox"])
        if key not in by_bbox:
            by_bbox[key] = {
                "bbox": region["bbox"],
                "source_ids": list(region["source_ids"]),
            }
            continue
        source_ids = set(by_bbox[key]["source_ids"])
        source_ids.update(region["source_ids"])
        by_bbox[key]["source_ids"] = sorted(source_ids)
    return list(by_bbox.values())


def detect_blank_image_clips(
    plan,
    source_image_path: str | Path,
    page_size,
    *,
    darkness_threshold: int = 245,
) -> list[VisualQaIssue]:
    page_num = _plan_page_num(plan)
    issues = []
    for item in _plan_render_items(plan):
        if _item_value(item, "kind") != "original_image_clip":
            continue
        bbox = tuple(float(value) for value in _item_value(item, "bbox"))
        analysis = analyze_dark_pixels(
            source_image_path,
            bbox,
            page_size,
            darkness_threshold=darkness_threshold,
        )
        if not analysis.is_blank:
            continue
        source_ids = [str(source_id) for source_id in _item_value(item, "source_ids", [])]
        issues.append(
            VisualQaIssue(
                category="blank_clip",
                severity="error",
                page_num=page_num,
                message="blank source image clip",
                source_ids=source_ids,
                bbox=bbox,
                render_kind="original_image_clip",
                artifact_paths={"source_png": str(source_image_path)},
            )
        )
    return issues


def _dark_bbox_touches_clip_edge(dark_bbox, clip_bbox, tolerance: float) -> bool:
    return (
        dark_bbox[0] <= clip_bbox[0] + tolerance
        or dark_bbox[1] <= clip_bbox[1] + tolerance
        or dark_bbox[2] >= clip_bbox[2] - tolerance
        or dark_bbox[3] >= clip_bbox[3] - tolerance
    )


def detect_image_clip_boundary_issues(
    plan,
    source_image_path: str | Path,
    page_size,
    *,
    source_blocks=None,
    search_margin: float = 2.0,
    edge_tolerance: float = 1.0,
    darkness_threshold: int = 245,
) -> list[VisualQaIssue]:
    page_num = _plan_page_num(plan)
    issues = []
    ledger_by_id = _ledger_by_block_id(plan)
    blocks_by_id = {str(block["id"]): block for block in (source_blocks or [])}
    body_block_ids = {
        block_id
        for block_id, entry in ledger_by_id.items()
        if _ledger_value(entry, "classification") == "body"
    }
    for item in _plan_render_items(plan):
        if _item_value(item, "kind") != "original_image_clip":
            continue
        clip_bbox = _bbox_tuple(_item_value(item, "bbox"))
        search_bbox = _expanded_bbox(clip_bbox, page_size, search_margin)
        dark_bbox = _dark_pixel_bbox(
            source_image_path,
            search_bbox,
            page_size,
            darkness_threshold=darkness_threshold,
        )
        source_ids = _source_ids_for_item(item)
        if dark_bbox is not None and _dark_bbox_touches_clip_edge(dark_bbox, clip_bbox, edge_tolerance):
            issues.append(
                VisualQaIssue(
                    category="clipped_content",
                    severity="error",
                    page_num=page_num,
                    message="dark source content touches image clip boundary",
                    source_ids=source_ids,
                    bbox=clip_bbox,
                    render_kind="original_image_clip",
                    artifact_paths={"source_png": str(source_image_path)},
                )
            )

        for source_id in sorted(body_block_ids):
            block = blocks_by_id.get(source_id)
            if block is None:
                if source_id not in source_ids:
                    continue
                overcapture_bbox = clip_bbox
            else:
                overcapture_bbox = _block_bbox(block)
                if not _body_source_block_overcaptured(overcapture_bbox, clip_bbox):
                    continue
            issues.append(
                VisualQaIssue(
                    category="region_overcapture",
                    severity="error",
                    page_num=page_num,
                    message="image clip captures translated-body source block",
                    source_ids=[source_id],
                    bbox=overcapture_bbox,
                    render_kind="original_image_clip",
                    artifact_paths={"source_png": str(source_image_path)},
                )
            )
    return issues


def detect_geometry_issues(
    plan,
    page_size,
    *,
    page_tolerance: float = 6.0,
) -> list[VisualQaIssue]:
    page_num = _plan_page_num(plan)
    width, height = page_size
    issues = []
    items = _plan_render_items(plan)
    protected_items = [
        item
        for item in items
        if _item_value(item, "kind") == "original_image_clip"
    ]
    protected_regions = [
        {"source_ids": [], "bbox": _bbox_tuple(_item_value(region, "bbox"))}
        for region in _plan_protected_regions(plan)
    ]
    protected_regions.extend(
        {
            "source_ids": _source_ids_for_item(item),
            "bbox": _bbox_tuple(_item_value(item, "bbox")),
        }
        for item in protected_items
    )
    protected_regions = _dedupe_protected_regions(protected_regions)
    text_items = [
        item
        for item in items
        if _item_value(item, "kind") in {"translated_text", "original_selectable_text"}
        and _bbox_area(_bbox_tuple(_item_value(item, "bbox"))) > 0
    ]

    for item in items:
        bbox = _bbox_tuple(_item_value(item, "bbox"))
        if (
            bbox[0] < -page_tolerance
            or bbox[1] < -page_tolerance
            or bbox[2] > width + page_tolerance
            or bbox[3] > height + page_tolerance
        ):
            issues.append(
                VisualQaIssue(
                    category="page_bounds",
                    severity="error",
                    page_num=page_num,
                    message="render item outside page bounds",
                    source_ids=_source_ids_for_item(item),
                    bbox=bbox,
                    render_kind=str(_item_value(item, "kind", "")),
                )
            )

    for item in text_items:
        item_bbox = _bbox_tuple(_item_value(item, "bbox"))
        for protected_region in protected_regions:
            protected_bbox = protected_region["bbox"]
            if not _significant_protected_overlap(item_bbox, protected_bbox):
                continue
            protected_source_ids = protected_region["source_ids"]
            issues.append(
                VisualQaIssue(
                    category="text_protected_overlap",
                    severity="error",
                    page_num=page_num,
                    message="text overlaps protected region",
                    source_ids=_source_ids_for_item(item),
                    bbox=item_bbox,
                    render_kind=str(_item_value(item, "kind", "")),
                    artifact_paths={
                        "protected_source_ids": ",".join(protected_source_ids)
                    },
                )
            )

    for left_idx, left in enumerate(text_items):
        left_bbox = _bbox_tuple(_item_value(left, "bbox"))
        for right in text_items[left_idx + 1 :]:
            left_ids = _source_ids_for_item(left)
            right_ids = _source_ids_for_item(right)
            if left_ids and right_ids and set(left_ids) == set(right_ids):
                continue
            right_bbox = _bbox_tuple(_item_value(right, "bbox"))
            if not _significant_text_overlap(left_bbox, right_bbox):
                continue
            issues.append(
                VisualQaIssue(
                    category="text_overlap",
                    severity="error",
                    page_num=page_num,
                    message="text overlaps unrelated text",
                    source_ids=left_ids + right_ids,
                    bbox=(
                        min(left_bbox[0], right_bbox[0]),
                        min(left_bbox[1], right_bbox[1]),
                        max(left_bbox[2], right_bbox[2]),
                        max(left_bbox[3], right_bbox[3]),
                    ),
                    render_kind="text",
                )
            )
    return issues


def detect_style_issues(plan, *, font_tolerance: float = 0.01) -> list[VisualQaIssue]:
    page_num = _plan_page_num(plan)
    issues = []
    classifications_by_id = _classifications_by_block_id(plan)
    body_items = []
    for item in _plan_render_items(plan):
        kind = _item_value(item, "kind")
        if kind not in {"translated_text", "original_selectable_text"}:
            continue
        classification = _classification_for_item(item, classifications_by_id)
        style_name = str(_item_value(item, "style_name", "") or "")
        expected_styles = EXPECTED_STYLES_BY_CLASSIFICATION.get(classification)
        if expected_styles and style_name and style_name not in expected_styles:
            issues.append(
                VisualQaIssue(
                    category="style_hierarchy",
                    severity="error",
                    page_num=page_num,
                    message=f"{classification} item uses {style_name} style",
                    source_ids=_source_ids_for_item(item),
                    bbox=_bbox_tuple(_item_value(item, "bbox")),
                    render_kind=str(kind),
                )
            )

        if (
            classification == "body"
            and style_name == "body"
            and _item_value(item, "font_size") is not None
            and not str(_item_value(item, "fallback_reason", "") or "")
        ):
            body_items.append(item)

    if not body_items:
        return issues

    baseline_size = float(_item_value(body_items[0], "font_size"))
    for item in body_items[1:]:
        font_size = float(_item_value(item, "font_size"))
        if abs(font_size - baseline_size) <= font_tolerance:
            continue
        issues.append(
            VisualQaIssue(
                category="body_font_consistency",
                severity="error",
                page_num=page_num,
                message=f"body font size {font_size:g} differs from baseline {baseline_size:g}",
                source_ids=_source_ids_for_item(item),
                bbox=_bbox_tuple(_item_value(item, "bbox")),
                render_kind=str(_item_value(item, "kind", "")),
            )
        )
    return issues


def _is_body_flow_text_item(item, classifications_by_id: Mapping[str, str]) -> bool:
    return (
        _item_value(item, "kind") in TEXT_RENDER_KINDS
        and _classification_for_item(item, classifications_by_id) == "body"
        and str(_item_value(item, "style_name", "") or "") == "body"
    )


def _body_text_items(plan) -> list:
    classifications_by_id = _classifications_by_block_id(plan)
    return [
        item
        for item in _plan_render_items(plan)
        if _is_body_flow_text_item(item, classifications_by_id)
    ]


def _same_body_flow_column(left_bbox, right_bbox) -> bool:
    overlap = max(0.0, min(left_bbox[2], right_bbox[2]) - max(left_bbox[0], right_bbox[0]))
    min_width = min(max(0.0, left_bbox[2] - left_bbox[0]), max(0.0, right_bbox[2] - right_bbox[0]))
    return min_width > 0 and overlap >= min_width * 0.55


def _bbox_union(bboxes) -> tuple[float, float, float, float]:
    normalized = [_bbox_tuple(bbox) for bbox in bboxes]
    return (
        min(bbox[0] for bbox in normalized),
        min(bbox[1] for bbox in normalized),
        max(bbox[2] for bbox in normalized),
        max(bbox[3] for bbox in normalized),
    )


def _body_flow_lanes(body_items) -> list[list]:
    lanes: list[list] = []
    lane_boxes: list[tuple[float, float, float, float]] = []
    for item in sorted(
        body_items,
        key=lambda candidate: (
            _bbox_tuple(_item_value(candidate, "bbox"))[0],
            _bbox_tuple(_item_value(candidate, "bbox"))[1],
        ),
    ):
        item_bbox = _bbox_tuple(_item_value(item, "bbox"))
        target_idx = None
        for lane_idx, lane_bbox in enumerate(lane_boxes):
            if _same_body_flow_column(lane_bbox, item_bbox):
                target_idx = lane_idx
                break
        if target_idx is None:
            lanes.append([item])
            lane_boxes.append(item_bbox)
            continue
        lanes[target_idx].append(item)
        lane_boxes[target_idx] = _bbox_union([lane_boxes[target_idx], item_bbox])
    return [
        sorted(lane, key=lambda candidate: _bbox_tuple(_item_value(candidate, "bbox"))[1])
        for lane in lanes
    ]


def _body_flow_gap_overlap_height(candidate_bbox, gap_bbox) -> float:
    return max(
        0.0,
        min(candidate_bbox[3], gap_bbox[3]) - max(candidate_bbox[1], gap_bbox[1]),
    )


def _bbox_blocks_body_flow_gap(candidate_bbox, gap_bbox) -> bool:
    vertical_overlap = max(
        0.0,
        _body_flow_gap_overlap_height(candidate_bbox, gap_bbox),
    )
    return (
        vertical_overlap >= MIN_BODY_FLOW_BARRIER_HEIGHT
        and _same_body_flow_column(gap_bbox, candidate_bbox)
    )


def _item_blocks_body_flow_gap(item, left, right, gap_bbox, classifications_by_id) -> bool:
    if item is left or item is right:
        return False
    candidate_bbox = _bbox_tuple(_item_value(item, "bbox"))
    if _bbox_area(candidate_bbox) <= 0 or not _bbox_blocks_body_flow_gap(candidate_bbox, gap_bbox):
        return False
    kind = _item_value(item, "kind")
    if kind == "original_image_clip":
        return True
    if kind in TEXT_RENDER_KINDS:
        style_name = str(_item_value(item, "style_name", "") or "")
        classification = _classification_for_item(item, classifications_by_id)
        return (
            style_name in BODY_FLOW_TEXT_BARRIER_STYLES
            or classification in BODY_FLOW_TEXT_BARRIER_STYLES
        )
    return False


def _protected_region_blocks_body_flow_gap(region, gap_bbox) -> bool:
    protected_bbox = _bbox_tuple(_item_value(region, "bbox"))
    return _bbox_area(protected_bbox) > 0 and _bbox_blocks_body_flow_gap(
        protected_bbox,
        gap_bbox,
    )


def _body_flow_gap_has_barrier(plan, left, right, gap_bbox, classifications_by_id) -> bool:
    return any(
        _item_blocks_body_flow_gap(item, left, right, gap_bbox, classifications_by_id)
        for item in _plan_render_items(plan)
    ) or any(
        _protected_region_blocks_body_flow_gap(region, gap_bbox)
        for region in _plan_protected_regions(plan)
    )


def detect_body_flow_whitespace_issues(
    plan,
    *,
    strict: bool = False,
    visible_gap_threshold: float = 32.0,
) -> list[VisualQaIssue]:
    page_num = _plan_page_num(plan)
    severity = "error" if strict else "warning"
    issues = []
    classifications_by_id = _classifications_by_block_id(plan)
    body_items = [
        item
        for item in _plan_render_items(plan)
        if _is_body_flow_text_item(item, classifications_by_id)
    ]
    for lane in _body_flow_lanes(body_items):
        for left, right in zip(lane, lane[1:]):
            left_bbox = _bbox_tuple(_item_value(left, "bbox"))
            right_bbox = _bbox_tuple(_item_value(right, "bbox"))
            visible_gap = right_bbox[1] - left_bbox[3]
            if visible_gap <= visible_gap_threshold:
                continue
            gap_bbox = (
                min(left_bbox[0], right_bbox[0]),
                left_bbox[3],
                max(left_bbox[2], right_bbox[2]),
                right_bbox[1],
            )
            if _body_flow_gap_has_barrier(plan, left, right, gap_bbox, classifications_by_id):
                continue
            issues.append(
                VisualQaIssue(
                    category="body_flow_whitespace",
                    severity=severity,
                    page_num=page_num,
                    message=f"body flow gap is {visible_gap:.1f}pt",
                    source_ids=_source_ids_for_item(left) + _source_ids_for_item(right),
                    bbox=gap_bbox,
                    render_kind="text",
                )
            )
    return issues


def render_pdf_pages_to_png(
    pdf_path: str | Path,
    output_dir: str | Path,
    pages,
    *,
    dpi: int = 150,
) -> list[Path]:
    fitz = load_fitz()
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    requested_pages = sorted({int(page_num) for page_num in pages})

    rendered_paths = []
    doc = fitz.open(pdf_path)
    try:
        matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
        for page_num in requested_pages:
            if page_num < 1 or page_num > doc.page_count:
                continue
            page = doc.load_page(page_num - 1)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            image_path = output_path / f"page-{page_num:03d}.png"
            pix.save(image_path)
            rendered_paths.append(image_path)
    finally:
        doc.close()
    return rendered_paths


def _severity_rank(severity: str) -> int:
    return SEVERITY_ORDER.get(severity, -1)


def _highest_severity(issues: list[VisualQaIssue]) -> str:
    if not issues:
        return "none"
    return max((issue.severity for issue in issues), key=_severity_rank)


def _issue_to_json(issue: VisualQaIssue) -> dict:
    return {
        "category": issue.category,
        "severity": issue.severity,
        "page_num": int(issue.page_num),
        "message": issue.message,
        "source_ids": sorted(str(source_id) for source_id in issue.source_ids),
        "bbox": None if issue.bbox is None else [float(value) for value in issue.bbox],
        "render_kind": issue.render_kind,
        "artifact_paths": {
            str(key): str(issue.artifact_paths[key])
            for key in sorted(issue.artifact_paths, key=str)
        },
    }


def _sorted_issues(issues: list[VisualQaIssue]) -> list[VisualQaIssue]:
    return sorted(
        issues,
        key=lambda issue: (
            int(issue.page_num),
            -_severity_rank(issue.severity),
            issue.category,
            issue.message,
            tuple(issue.source_ids),
        ),
    )


def visual_qa_report_payload(
    issues: list[VisualQaIssue],
    *,
    checked_pages=None,
    png_paths: Mapping[int | str, str | Path] | None = None,
    plan_artifact_paths=None,
) -> dict:
    sorted_issues = _sorted_issues(list(issues))
    normalized_pages = sorted({int(page_num) for page_num in (checked_pages or [])})
    normalized_png_paths = {
        str(int(page_num)): str(path)
        for page_num, path in sorted((png_paths or {}).items(), key=lambda item: int(item[0]))
    }
    normalized_plan_paths = sorted(str(path) for path in (plan_artifact_paths or []))
    return {
        "checked_pages": normalized_pages,
        "error_count": sum(1 for issue in sorted_issues if issue.severity == "error"),
        "highest_severity": _highest_severity(sorted_issues),
        "issue_count": len(sorted_issues),
        "issues": [_issue_to_json(issue) for issue in sorted_issues],
        "plan_artifact_paths": normalized_plan_paths,
        "rendered_png_paths": normalized_png_paths,
        "warning_count": sum(1 for issue in sorted_issues if issue.severity == "warning"),
    }


def visual_qa_report_json(
    issues: list[VisualQaIssue],
    *,
    checked_pages=None,
    png_paths: Mapping[int | str, str | Path] | None = None,
    plan_artifact_paths=None,
) -> str:
    return json.dumps(
        visual_qa_report_payload(
            issues,
            checked_pages=checked_pages,
            png_paths=png_paths,
            plan_artifact_paths=plan_artifact_paths,
        ),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )


def _visual_qa_report_markdown(payload: dict) -> str:
    lines = [
        "# Visual QA Report",
        "",
        f"- issue_count: {payload['issue_count']}",
        f"- error_count: {payload['error_count']}",
        f"- warning_count: {payload['warning_count']}",
        f"- highest_severity: {payload['highest_severity']}",
        "",
    ]
    if payload["checked_pages"]:
        pages = ", ".join(str(page_num) for page_num in payload["checked_pages"])
        lines.extend(["## Checked Pages", "", pages, ""])
    if payload["rendered_png_paths"]:
        lines.extend(["## Rendered PNGs", ""])
        for page_num, path in payload["rendered_png_paths"].items():
            lines.append(f"- page {int(page_num):03d}: {path}")
        lines.append("")
    if payload["plan_artifact_paths"]:
        lines.extend(["## Plan Artifacts", ""])
        for path in payload["plan_artifact_paths"]:
            lines.append(f"- {path}")
        lines.append("")
    if payload["issues"]:
        lines.extend(["## Issues", ""])
        for issue in payload["issues"]:
            source_ids = ", ".join(issue["source_ids"]) if issue["source_ids"] else "-"
            lines.append(
                f"- page {issue['page_num']:03d} [{issue['severity']}]"
                f" {issue['category']}: {issue['message']} source_ids={source_ids}"
            )
    return "\n".join(lines).rstrip() + "\n"


def write_visual_qa_report(
    issues: list[VisualQaIssue],
    output_dir: str | Path,
    *,
    checked_pages=None,
    png_paths: Mapping[int | str, str | Path] | None = None,
    plan_artifact_paths=None,
) -> VisualQaReport:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    payload = visual_qa_report_payload(
        issues,
        checked_pages=checked_pages,
        png_paths=png_paths,
        plan_artifact_paths=plan_artifact_paths,
    )
    json_path = output_path / "visual_qa_report.json"
    markdown_path = output_path / "visual_qa_report.md"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(_visual_qa_report_markdown(payload), encoding="utf-8")
    return VisualQaReport(
        json_path=json_path,
        markdown_path=markdown_path,
        issue_count=payload["issue_count"],
        error_count=payload["error_count"],
        warning_count=payload["warning_count"],
    )


def _load_plan_artifact(plan_artifact_path: str | Path) -> dict:
    return json.loads(Path(plan_artifact_path).read_text(encoding="utf-8"))


def _load_plan_page_num(plan_artifact_path: str | Path) -> int:
    return int(_load_plan_artifact(plan_artifact_path)["page_num"])


def generate_visual_qa_report(
    plan_artifact_paths,
    *,
    output_dir: str | Path,
    translated_pdf_path: str | Path | None = None,
    source_png_paths: Mapping[int | str, str | Path] | None = None,
    source_blocks_by_page: Mapping[int | str, list[dict]] | None = None,
    page_size=None,
    strict_body_flow: bool = False,
    issues: list[VisualQaIssue] | None = None,
    dpi: int = 150,
) -> VisualQaReport:
    normalized_plan_paths = sorted(Path(path) for path in plan_artifact_paths)
    loaded_plans = [_load_plan_artifact(path) for path in normalized_plan_paths]
    checked_pages = sorted({int(plan["page_num"]) for plan in loaded_plans})
    png_paths = {}
    if translated_pdf_path is not None:
        rendered_paths = render_pdf_pages_to_png(
            translated_pdf_path,
            Path(output_dir) / "rendered_png",
            checked_pages,
            dpi=dpi,
        )
        for path in rendered_paths:
            page_num = int(path.stem.split("-")[-1])
            png_paths[page_num] = path

    report_issues = list(issues or [])
    normalized_source_png_paths = {
        int(page_num): Path(path)
        for page_num, path in (source_png_paths or {}).items()
    }
    normalized_source_blocks = {
        int(page_num): list(blocks)
        for page_num, blocks in (source_blocks_by_page or {}).items()
    }
    if page_size is not None:
        for plan in loaded_plans:
            page_num = int(plan["page_num"])
            report_issues.extend(detect_geometry_issues(plan, page_size))
            report_issues.extend(detect_style_issues(plan))
            report_issues.extend(
                detect_body_flow_whitespace_issues(plan, strict=strict_body_flow)
            )
            source_png = normalized_source_png_paths.get(page_num)
            if source_png is None:
                continue
            report_issues.extend(
                detect_blank_image_clips(plan, source_png, page_size)
            )
            report_issues.extend(
                detect_image_clip_boundary_issues(
                    plan,
                    source_png,
                    page_size,
                    source_blocks=normalized_source_blocks.get(page_num, []),
                )
            )

    return write_visual_qa_report(
        report_issues,
        output_dir,
        checked_pages=checked_pages,
        png_paths=png_paths,
        plan_artifact_paths=normalized_plan_paths,
    )
