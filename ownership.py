import json
import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field


COMPONENT_KIND_TRANSLATED_TEXT = "translated_text"
COMPONENT_KIND_VISUAL = "visual"
COMPONENT_KIND_REFERENCE = "reference"
COMPONENT_KIND_HEADER_FOOTER = "header_footer"
COMPONENT_KIND_PAGE_NUMBER = "page_number"
COMPONENT_KIND_UNKNOWN = "unknown"
COMPONENT_KIND_DUPLICATE = "duplicate"
COMPONENT_KIND_SKIP = "skip"

COMPONENT_KINDS = {
    COMPONENT_KIND_TRANSLATED_TEXT,
    COMPONENT_KIND_VISUAL,
    COMPONENT_KIND_REFERENCE,
    COMPONENT_KIND_HEADER_FOOTER,
    COMPONENT_KIND_PAGE_NUMBER,
    COMPONENT_KIND_UNKNOWN,
    COMPONENT_KIND_DUPLICATE,
    COMPONENT_KIND_SKIP,
}
NON_DUPLICATE_COMPONENT_KINDS = COMPONENT_KINDS - {COMPONENT_KIND_DUPLICATE, COMPONENT_KIND_SKIP}

CONFIDENCE_DETERMINISTIC = "deterministic"
CONFIDENCE_INFERRED = "inferred"
CONFIDENCE_CONSERVATIVE = "conservative"
CONFIDENCE_LEVELS = {CONFIDENCE_DETERMINISTIC, CONFIDENCE_INFERRED, CONFIDENCE_CONSERVATIVE}

NORMAL_TRANSLATED_CLASSES = {"title", "heading", "subheading", "body"}
VISUAL_CLASSES = {"figure_region", "table_region", "formula_region", "code_region"}
HEADER_FOOTER_CLASSES = {"header_footer", "journal_footer"}
TEXT_RENDER_KINDS = {"translated_text", "original_selectable_text"}
IMAGE_RENDER_KINDS = {"original_image_clip"}
REASON_MIXED_VISUAL_BODY_SPLIT = "mixed_visual_body_split"
VISUAL_CONTAINMENT_TOLERANCE = 1.5
MIXED_SPLIT_SOURCE_BOUNDARY_TOLERANCE = 4.0
MIXED_SPLIT_TEXT_LAYOUT_TOLERANCE = 12.0


@dataclass(frozen=True)
class PageComponent:
    component_id: str
    component_kind: str
    source_ids: list[str]
    source_bbox: tuple[float, float, float, float]
    clip_bbox: tuple[float, float, float, float] | None
    confidence: str
    reason_codes: list[str] = field(default_factory=list)
    render_strategy: str = ""
    parent_component_id: str = ""


@dataclass(frozen=True)
class OwnershipLedgerEntry:
    page_number: int
    source_id: str
    component_id: str
    component_kind: str
    ownership_role: str
    confidence: str
    reason_codes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class OwnershipIssue:
    issue_code: str
    severity: str
    page_num: int
    message: str
    source_ids: list[str] = field(default_factory=list)
    component_ids: list[str] = field(default_factory=list)
    bboxes: list[tuple[float, float, float, float]] = field(default_factory=list)


@dataclass(frozen=True)
class OwnershipValidationResult:
    issues: list[OwnershipIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues


def _stable_value(value):
    if isinstance(value, Mapping):
        return {str(key): _stable_value(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, tuple):
        return [_stable_value(item) for item in value]
    if isinstance(value, list):
        return [_stable_value(item) for item in value]
    return value


def stable_json_dumps(value) -> str:
    return json.dumps(_stable_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def bbox_to_json(bbox) -> list[float]:
    return [round(float(value), 3) for value in bbox]


def block_bbox(block) -> tuple[float, float, float, float]:
    return (
        float(block.get("xMin", 0.0)),
        float(block.get("yMin", 0.0)),
        float(block.get("xMax", 0.0)),
        float(block.get("yMax", 0.0)),
    )


def bbox_union(boxes) -> tuple[float, float, float, float]:
    normalized = [tuple(float(value) for value in box) for box in boxes if box is not None]
    if not normalized:
        return (0.0, 0.0, 0.0, 0.0)
    return (
        min(box[0] for box in normalized),
        min(box[1] for box in normalized),
        max(box[2] for box in normalized),
        max(box[3] for box in normalized),
    )


def bbox_area(box) -> float:
    return max(0.0, float(box[2]) - float(box[0])) * max(0.0, float(box[3]) - float(box[1]))


def bbox_overlap_area(left, right) -> float:
    x0 = max(float(left[0]), float(right[0]))
    y0 = max(float(left[1]), float(right[1]))
    x1 = min(float(left[2]), float(right[2]))
    y1 = min(float(left[3]), float(right[3]))
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def bbox_overlap_height(left, right) -> float:
    return max(0.0, min(float(left[3]), float(right[3])) - max(float(left[1]), float(right[1])))


def bbox_horizontal_overlap(left, right) -> float:
    return max(0.0, min(float(left[2]), float(right[2])) - max(float(left[0]), float(right[0])))


def bboxes_are_same_visual_cluster(left, right) -> bool:
    horizontal = bbox_horizontal_overlap(left, right)
    if horizontal <= 0:
        return False
    min_width = max(1.0, min(float(left[2]) - float(left[0]), float(right[2]) - float(right[0])))
    if horizontal < min_width * 0.45:
        return False
    if bbox_overlap_height(left, right) > 0:
        return True
    vertical_gap = max(float(right[1]) - float(left[3]), float(left[1]) - float(right[3]))
    return 0 <= vertical_gap <= 10.0


def is_trivial_block(block) -> bool:
    text = re.sub(r"\s+", " ", str(block.get("text", ""))).strip()
    if not text:
        return True
    if re.fullmatch(r"\d+", text):
        return True
    if re.fullmatch(r"https?://\S+", text):
        return True
    return False


def page_component_to_json(component: PageComponent) -> dict:
    return {
        "clip_bbox": None if component.clip_bbox is None else bbox_to_json(component.clip_bbox),
        "component_id": component.component_id,
        "component_kind": component.component_kind,
        "confidence": component.confidence,
        "parent_component_id": component.parent_component_id,
        "reason_codes": sorted(str(code) for code in component.reason_codes),
        "render_strategy": component.render_strategy,
        "source_bbox": bbox_to_json(component.source_bbox),
        "source_ids": sorted(str(source_id) for source_id in component.source_ids),
    }


def ownership_issue_to_json(issue: OwnershipIssue) -> dict:
    return {
        "bboxes": [bbox_to_json(box) for box in issue.bboxes],
        "component_ids": sorted(str(component_id) for component_id in issue.component_ids),
        "issue_code": issue.issue_code,
        "message": issue.message,
        "page_num": int(issue.page_num),
        "severity": issue.severity,
        "source_ids": sorted(str(source_id) for source_id in issue.source_ids),
    }


def ownership_validation_to_json(result: OwnershipValidationResult | list[OwnershipIssue]) -> dict:
    issues = result.issues if isinstance(result, OwnershipValidationResult) else list(result)
    return {"ok": not issues, "issues": [ownership_issue_to_json(issue) for issue in issues]}


def components_to_json(components: list[PageComponent]) -> list[dict]:
    return [page_component_to_json(component) for component in sorted(components, key=lambda item: item.component_id)]


def ownership_ledger_entry_to_json(entry: OwnershipLedgerEntry) -> dict:
    return {
        "component_id": entry.component_id,
        "component_kind": entry.component_kind,
        "confidence": entry.confidence,
        "ownership_role": entry.ownership_role,
        "page_number": int(entry.page_number),
        "reason_codes": sorted(str(code) for code in entry.reason_codes),
        "source_id": entry.source_id,
    }


def ownership_ledger_for_components(page_number: int, components: list[PageComponent]) -> list[OwnershipLedgerEntry]:
    entries = []
    for component in sorted(components, key=lambda item: item.component_id):
        for source_id in sorted(set(component.source_ids)):
            entries.append(
                OwnershipLedgerEntry(
                    page_number=page_number,
                    source_id=source_id,
                    component_id=component.component_id,
                    component_kind=component.component_kind,
                    ownership_role="owner",
                    confidence=component.confidence,
                    reason_codes=list(component.reason_codes),
                )
            )
    return entries


def component_by_source_id(components: list[PageComponent]) -> dict[str, PageComponent]:
    result = {}
    for component in components:
        for source_id in component.source_ids:
            result[str(source_id)] = component
    return result


def components_by_source_id(components: list[PageComponent]) -> dict[str, list[PageComponent]]:
    result: dict[str, list[PageComponent]] = defaultdict(list)
    for component in components:
        for source_id in component.source_ids:
            result[str(source_id)].append(component)
    return dict(result)


def validate_ownership(page_num: int, blocks, components: list[PageComponent]) -> OwnershipValidationResult:
    issues: list[OwnershipIssue] = []
    block_by_id = {str(block["id"]): block for block in blocks if not is_trivial_block(block)}
    non_duplicate_owners: dict[str, list[PageComponent]] = defaultdict(list)
    explicit_owners: dict[str, list[PageComponent]] = defaultdict(list)

    for component in components:
        if component.component_kind not in COMPONENT_KINDS:
            issues.append(
                OwnershipIssue(
                    issue_code="invalid_component_kind",
                    severity="error",
                    page_num=page_num,
                    message=f"component {component.component_id} has invalid kind {component.component_kind}",
                    component_ids=[component.component_id],
                    bboxes=[component.source_bbox],
                )
            )
            continue
        if component.confidence not in CONFIDENCE_LEVELS:
            issues.append(
                OwnershipIssue(
                    issue_code="invalid_confidence",
                    severity="error",
                    page_num=page_num,
                    message=f"component {component.component_id} has invalid confidence {component.confidence}",
                    component_ids=[component.component_id],
                    bboxes=[component.source_bbox],
                )
            )
        for source_id in sorted(set(str(source_id) for source_id in component.source_ids)):
            explicit_owners[source_id].append(component)
            if component.component_kind in NON_DUPLICATE_COMPONENT_KINDS:
                non_duplicate_owners[source_id].append(component)
        if component.component_kind == COMPONENT_KIND_VISUAL and component.clip_bbox is not None:
            if not _bbox_contained(component.source_bbox, component.clip_bbox, tolerance=VISUAL_CONTAINMENT_TOLERANCE):
                issues.append(
                    OwnershipIssue(
                        issue_code="visual_clip_undercaptures_source",
                        severity="warning",
                        page_num=page_num,
                        message=f"visual component {component.component_id} clip does not cover its source bbox",
                        source_ids=sorted(str(source_id) for source_id in component.source_ids),
                        component_ids=[component.component_id],
                        bboxes=[component.clip_bbox, component.source_bbox],
                    )
                )
            if not _component_has_reason(component, REASON_MIXED_VISUAL_BODY_SPLIT):
                for source_id in sorted(set(str(source_id) for source_id in component.source_ids)):
                    block = block_by_id.get(source_id)
                    if block is None:
                        continue
                    owned_block_bbox = block_bbox(block)
                    if _bbox_contained(owned_block_bbox, component.clip_bbox, tolerance=VISUAL_CONTAINMENT_TOLERANCE):
                        continue
                    issues.append(
                        OwnershipIssue(
                            issue_code="visual_clip_undercaptures_owned_block",
                            severity="warning",
                            page_num=page_num,
                            message=f"visual component {component.component_id} clip does not cover owned block {source_id}",
                            source_ids=[source_id],
                            component_ids=[component.component_id],
                            bboxes=[component.clip_bbox, owned_block_bbox],
                        )
                    )

    for source_id, block in sorted(block_by_id.items()):
        if not explicit_owners.get(source_id):
            issues.append(
                OwnershipIssue(
                    issue_code="missing_owner",
                    severity="error",
                    page_num=page_num,
                    message=f"source block {source_id} has no component owner",
                    source_ids=[source_id],
                    bboxes=[block_bbox(block)],
                )
            )
            continue
        owner_components = non_duplicate_owners.get(source_id, [])
        if len(owner_components) > 1 and not _components_are_explicit_disjoint_split(owner_components):
            issues.append(
                OwnershipIssue(
                    issue_code="duplicate_owner",
                    severity="error",
                    page_num=page_num,
                    message=f"source block {source_id} has multiple non-duplicate owners",
                    source_ids=[source_id],
                    component_ids=sorted(component.component_id for component in owner_components),
                    bboxes=[block_bbox(block)],
                )
            )

    return OwnershipValidationResult(issues=issues)


def _next_component_id(page_num: int, index: int) -> str:
    return f"p{int(page_num):03d}c{int(index):04d}"


def _make_component(
    page_num: int,
    index: int,
    component_kind: str,
    source_ids: list[str],
    block_by_id: dict[str, dict],
    *,
    source_bbox=None,
    clip_bbox=None,
    confidence=CONFIDENCE_INFERRED,
    reason_codes=None,
    render_strategy="",
    parent_component_id="",
) -> PageComponent:
    unique_ids = sorted({str(source_id) for source_id in source_ids if str(source_id) in block_by_id})
    owned_source_bbox = (
        bbox_union(block_bbox(block_by_id[source_id]) for source_id in unique_ids)
        if source_bbox is None
        else tuple(float(value) for value in source_bbox)
    )
    return PageComponent(
        component_id=_next_component_id(page_num, index),
        component_kind=component_kind,
        source_ids=unique_ids,
        source_bbox=owned_source_bbox,
        clip_bbox=None if clip_bbox is None else tuple(float(value) for value in clip_bbox),
        confidence=confidence,
        reason_codes=sorted(str(code) for code in (reason_codes or [])),
        render_strategy=render_strategy,
        parent_component_id=parent_component_id,
    )


def _component_has_reason(component: PageComponent, reason: str) -> bool:
    return reason in {str(code) for code in component.reason_codes}


def _components_are_explicit_disjoint_split(components: list[PageComponent]) -> bool:
    if len(components) != 2:
        return False
    if not all(_component_has_reason(component, REASON_MIXED_VISUAL_BODY_SPLIT) for component in components):
        return False
    visual_components = [component for component in components if component.component_kind == COMPONENT_KIND_VISUAL]
    text_components = [component for component in components if component.component_kind == COMPONENT_KIND_TRANSLATED_TEXT]
    if len(visual_components) != 1 or len(text_components) != 1:
        return False
    visual_component = visual_components[0]
    text_component = text_components[0]
    if visual_component.parent_component_id:
        return False
    if text_component.parent_component_id != visual_component.component_id:
        return False
    for index, left in enumerate(components):
        for right in components[index + 1 :]:
            overlap_height = bbox_overlap_height(left.source_bbox, right.source_bbox)
            if overlap_height > MIXED_SPLIT_SOURCE_BOUNDARY_TOLERANCE:
                return False
    return True


def _bbox_contained(inner, outer, *, tolerance: float = 1.0) -> bool:
    return (
        float(inner[0]) >= float(outer[0]) - tolerance
        and float(inner[1]) >= float(outer[1]) - tolerance
        and float(inner[2]) <= float(outer[2]) + tolerance
        and float(inner[3]) <= float(outer[3]) + tolerance
    )


def _explicit_split_components(components: list[PageComponent]) -> tuple[PageComponent, PageComponent] | None:
    if not _components_are_explicit_disjoint_split(components):
        return None
    visual_component = next(component for component in components if component.component_kind == COMPONENT_KIND_VISUAL)
    text_component = next(component for component in components if component.component_kind == COMPONENT_KIND_TRANSLATED_TEXT)
    return visual_component, text_component


def _split_render_items_match_components(source_id: str, image_items, text_items, components: list[PageComponent]) -> bool:
    split = _explicit_split_components(components)
    if split is None:
        return False
    visual_component, text_component = split
    source_image_items = [item for item in image_items if source_id in _item_source_ids(item)]
    source_text_items = [item for item in text_items if source_id in _item_source_ids(item)]
    if not source_image_items or not source_text_items:
        return False
    visual_box = visual_component.clip_bbox or visual_component.source_bbox
    for item in source_image_items:
        if _item_component_id(item) != visual_component.component_id:
            return False
        if str(_item_attr(item, "component_kind", "") or "") != COMPONENT_KIND_VISUAL:
            return False
        if not _bbox_contained(_item_bbox(item), visual_box):
            return False
    for item in source_text_items:
        if _item_component_id(item) != text_component.component_id:
            return False
        if str(_item_attr(item, "component_kind", "") or "") != COMPONENT_KIND_TRANSLATED_TEXT:
            return False
        if not _bbox_contained(_item_bbox(item), text_component.source_bbox, tolerance=MIXED_SPLIT_TEXT_LAYOUT_TOLERANCE):
            return False
    return True


def _allowed_visual_text_split(visual_component: PageComponent, text_component: PageComponent) -> bool:
    if visual_component.component_kind != COMPONENT_KIND_VISUAL:
        return False
    if text_component.component_kind != COMPONENT_KIND_TRANSLATED_TEXT:
        return False
    if not _component_has_reason(visual_component, REASON_MIXED_VISUAL_BODY_SPLIT):
        return False
    if not _component_has_reason(text_component, REASON_MIXED_VISUAL_BODY_SPLIT):
        return False
    return text_component.parent_component_id == visual_component.component_id


def _best_visual_region_for_block(block, visual_regions):
    block_box = block_bbox(block)
    best_index = None
    best_overlap = 0.0
    for index, region in enumerate(visual_regions):
        region_box = tuple(float(value) for value in (region.get("bbox") or block_box))
        overlap = bbox_overlap_area(block_box, region_box)
        if overlap > best_overlap:
            best_index = index
            best_overlap = overlap
    return best_index


def _merge_visual_regions(regions: list[dict]) -> list[dict]:
    merged: list[dict] = []
    for region in regions:
        current = dict(region)
        current["source_ids"] = sorted({str(source_id) for source_id in current.get("source_ids", [])})
        current_bbox = current.get("source_bbox") or current.get("bbox")
        if current_bbox is not None:
            current["source_bbox"] = tuple(float(value) for value in current_bbox)
        if current.get("bbox") is not None:
            current["bbox"] = tuple(float(value) for value in current["bbox"])
        if current.get("mixed_body_bbox") is not None:
            current["mixed_body_bbox"] = tuple(float(value) for value in current["mixed_body_bbox"])
        if current.get("mixed_body_source_ids") is not None:
            current["mixed_body_source_ids"] = sorted(
                {str(source_id) for source_id in current.get("mixed_body_source_ids", [])}
            )

        merged_into_existing = False
        for existing in merged:
            existing_bbox = existing.get("source_bbox") or existing.get("bbox")
            candidate_bbox = current.get("source_bbox") or current.get("bbox")
            if existing_bbox is None or candidate_bbox is None:
                continue
            if not bboxes_are_same_visual_cluster(existing_bbox, candidate_bbox):
                continue
            existing["source_ids"] = sorted(set(existing.get("source_ids", [])) | set(current.get("source_ids", [])))
            if existing.get("mixed_body_source_ids") is not None or current.get("mixed_body_source_ids") is not None:
                existing["mixed_body_source_ids"] = sorted(
                    set(existing.get("mixed_body_source_ids", [])) | set(current.get("mixed_body_source_ids", []))
                )
            existing["source_bbox"] = bbox_union([existing_bbox, candidate_bbox])
            if existing.get("bbox") is not None or current.get("bbox") is not None:
                existing["bbox"] = bbox_union(
                    [
                        existing.get("bbox") or existing["source_bbox"],
                        current.get("bbox") or current["source_bbox"],
                    ]
                )
            if existing.get("mixed_body_bbox") is not None or current.get("mixed_body_bbox") is not None:
                body_boxes = [
                    box
                    for box in (existing.get("mixed_body_bbox"), current.get("mixed_body_bbox"))
                    if box is not None
                ]
                existing["mixed_body_bbox"] = bbox_union(body_boxes)
            merged_into_existing = True
            break
        if not merged_into_existing:
            merged.append(current)
    return merged


def merge_visual_regions(regions: list[dict]) -> list[dict]:
    return _merge_visual_regions(regions)


def build_page_components(
    page_num: int,
    blocks,
    classes: dict[str, str],
    *,
    visual_regions: list[dict],
    visual_covered_text_ids: set[str] | None = None,
    duplicate_ids: set[str] | None = None,
    skip_ids: set[str] | None = None,
) -> list[PageComponent]:
    visual_regions = _merge_visual_regions(visual_regions)
    visual_covered_text_ids = set(visual_covered_text_ids or ())
    duplicate_ids = set(duplicate_ids or ())
    skip_ids = set(skip_ids or ())

    block_by_id = {str(block["id"]): block for block in blocks}
    assigned: set[str] = set()
    components: list[PageComponent] = []
    component_index = 1

    for region_index, region in enumerate(visual_regions):
        source_ids = {str(source_id) for source_id in region.get("source_ids", []) if str(source_id) in block_by_id}
        mixed_body_source_ids = {
            str(source_id)
            for source_id in region.get("mixed_body_source_ids", [])
            if str(source_id) in block_by_id
        }
        for source_id in sorted(visual_covered_text_ids):
            block = block_by_id.get(source_id)
            if block is None:
                continue
            if _best_visual_region_for_block(block, visual_regions) == region_index:
                source_ids.add(source_id)
        source_ids = sorted(source_ids - duplicate_ids)
        if not source_ids:
            continue
        clip_bbox = tuple(region.get("bbox")) if region.get("bbox") else bbox_union(block_bbox(block_by_id[source_id]) for source_id in source_ids)
        source_bbox = tuple(region.get("source_bbox")) if region.get("source_bbox") else bbox_union(
            block_bbox(block_by_id[source_id]) for source_id in source_ids
        )
        visual_reason_codes = [str(region.get("kind") or "visual_region"), "visual_region"]
        if mixed_body_source_ids:
            visual_reason_codes.append(REASON_MIXED_VISUAL_BODY_SPLIT)
        visual_component = _make_component(
            page_num,
            component_index,
            COMPONENT_KIND_VISUAL,
            source_ids,
            block_by_id,
            source_bbox=source_bbox,
            clip_bbox=clip_bbox,
            confidence=CONFIDENCE_CONSERVATIVE,
            reason_codes=visual_reason_codes,
            render_strategy="original_image_clip",
        )
        components.append(visual_component)
        component_index += 1
        if mixed_body_source_ids:
            body_bbox = tuple(region.get("mixed_body_bbox")) if region.get("mixed_body_bbox") else bbox_union(
                block_bbox(block_by_id[source_id]) for source_id in mixed_body_source_ids
            )
            components.append(
                _make_component(
                    page_num,
                    component_index,
                    COMPONENT_KIND_TRANSLATED_TEXT,
                    sorted(mixed_body_source_ids),
                    block_by_id,
                    source_bbox=body_bbox,
                    clip_bbox=None,
                    confidence=CONFIDENCE_INFERRED,
                    reason_codes=["body", REASON_MIXED_VISUAL_BODY_SPLIT],
                    render_strategy="translated_text",
                    parent_component_id=visual_component.component_id,
                )
            )
            component_index += 1
        assigned.update(source_ids)

    for block in blocks:
        source_id = str(block["id"])
        if source_id in assigned:
            continue
        classification = classes.get(source_id, "unknown")
        if source_id in duplicate_ids:
            component_kind = COMPONENT_KIND_DUPLICATE
            confidence = CONFIDENCE_DETERMINISTIC
            render_strategy = "skip_explicitly"
            reason_codes = ["duplicated_extraction"]
        elif source_id in skip_ids:
            component_kind = COMPONENT_KIND_SKIP
            confidence = CONFIDENCE_DETERMINISTIC
            render_strategy = "skip_explicitly"
            reason_codes = ["skip_explicitly"]
        elif classification == "page_number":
            component_kind = COMPONENT_KIND_PAGE_NUMBER
            confidence = CONFIDENCE_DETERMINISTIC
            render_strategy = "skip_explicitly"
            reason_codes = ["page_number"]
        elif classification in HEADER_FOOTER_CLASSES:
            component_kind = COMPONENT_KIND_HEADER_FOOTER
            confidence = CONFIDENCE_DETERMINISTIC
            render_strategy = "skip_explicitly" if classification == "header_footer" else "original_selectable_text"
            reason_codes = [classification]
        elif classification == "reference":
            component_kind = COMPONENT_KIND_REFERENCE
            confidence = CONFIDENCE_INFERRED
            render_strategy = "original_selectable_text"
            reason_codes = ["reference"]
        elif classification in NORMAL_TRANSLATED_CLASSES:
            component_kind = COMPONENT_KIND_TRANSLATED_TEXT
            confidence = CONFIDENCE_INFERRED
            render_strategy = "translated_text"
            reason_codes = [classification]
        elif classification in VISUAL_CLASSES:
            component_kind = COMPONENT_KIND_VISUAL
            confidence = CONFIDENCE_CONSERVATIVE
            render_strategy = "original_image_clip"
            reason_codes = [classification, "visual_classification"]
        elif is_trivial_block(block):
            component_kind = COMPONENT_KIND_SKIP
            confidence = CONFIDENCE_DETERMINISTIC
            render_strategy = "skip_explicitly"
            reason_codes = ["trivial_block"]
        else:
            component_kind = COMPONENT_KIND_UNKNOWN
            confidence = CONFIDENCE_CONSERVATIVE
            render_strategy = "original_image_clip"
            reason_codes = [classification, "unknown_preserve"]

        components.append(
            _make_component(
                page_num,
                component_index,
                component_kind,
                [source_id],
                block_by_id,
                clip_bbox=None,
                confidence=confidence,
                reason_codes=reason_codes,
                render_strategy=render_strategy,
            )
        )
        component_index += 1
        assigned.add(source_id)

    return components


def translation_items_from_components(blocks, components: list[PageComponent], *, classes: dict[str, str]) -> list[dict]:
    block_by_id = {str(block["id"]): block for block in blocks}
    items = []
    for component in sorted(components, key=lambda item: (item.source_bbox[1], item.source_bbox[0], item.component_id)):
        if component.component_kind != COMPONENT_KIND_TRANSLATED_TEXT:
            continue
        for source_id in component.source_ids:
            block = block_by_id.get(str(source_id))
            if block is None:
                continue
            text = str(block.get("text", ""))
            if not text.strip():
                continue
            items.append({"id": str(source_id), "text": text})
    return items


def _item_attr(item, name, default=None):
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _item_bbox(item):
    return tuple(float(value) for value in _item_attr(item, "bbox", (0, 0, 0, 0)))


def _item_source_ids(item) -> list[str]:
    return [str(source_id) for source_id in (_item_attr(item, "source_ids", []) or [])]


def _item_component_id(item) -> str:
    return str(_item_attr(item, "component_id", "") or "")


def _significant_overlap(left, right, *, min_overlap_ratio: float, min_overlap_height: float) -> bool:
    if bbox_overlap_height(left, right) < min_overlap_height:
        return False
    overlap = bbox_overlap_area(left, right)
    if overlap <= 0:
        return False
    return overlap > min(bbox_area(left), bbox_area(right)) * min_overlap_ratio


def validate_render_layer_exclusivity(
    plan,
    components: list[PageComponent],
    *,
    min_overlap_ratio: float = 0.05,
    min_overlap_height: float = 4.0,
    max_visual_overcapture_ratio: float = 0.02,
) -> OwnershipValidationResult:
    page_num = int(getattr(plan, "page_num", 0))
    issues: list[OwnershipIssue] = []
    items = list(getattr(plan, "items", []))
    image_items = [item for item in items if _item_attr(item, "kind") in IMAGE_RENDER_KINDS]
    text_items = [item for item in items if _item_attr(item, "kind") in TEXT_RENDER_KINDS]
    image_ids = {source_id for item in image_items for source_id in _item_source_ids(item)}
    text_ids = {source_id for item in text_items for source_id in _item_source_ids(item)}
    components_by_source_id: dict[str, list[PageComponent]] = defaultdict(list)
    for component in components:
        for source_id in component.source_ids:
            components_by_source_id[str(source_id)].append(component)

    for source_id in sorted(image_ids & text_ids):
        if _split_render_items_match_components(
            source_id,
            image_items,
            text_items,
            components_by_source_id.get(source_id, []),
        ):
            continue
        issues.append(
            OwnershipIssue(
                issue_code="source_rendered_as_image_and_text",
                severity="error",
                page_num=page_num,
                message=f"source block {source_id} is rendered by both image and text layers",
                source_ids=[source_id],
            )
        )

    visual_components = [component for component in components if component.component_kind == COMPONENT_KIND_VISUAL]
    translated_components = [component for component in components if component.component_kind == COMPONENT_KIND_TRANSLATED_TEXT]
    component_by_id = {component.component_id: component for component in components}
    visual_render_boxes_by_component_id: dict[str, list[tuple[float, float, float, float]]] = defaultdict(list)
    for image_item in image_items:
        image_component_id = _item_component_id(image_item)
        if image_component_id:
            visual_render_boxes_by_component_id[image_component_id].append(_item_bbox(image_item))

    for image_item in image_items:
        image_component_id = _item_component_id(image_item)
        image_component = component_by_id.get(image_component_id)
        if image_component is None or image_component.component_kind != COMPONENT_KIND_VISUAL:
            continue
        image_box = _item_bbox(image_item)
        for translated_component in translated_components:
            if translated_component.component_id == image_component_id:
                continue
            if not (set(translated_component.source_ids) & text_ids):
                continue
            if _allowed_visual_text_split(image_component, translated_component):
                continue
            overlap = bbox_overlap_area(image_box, translated_component.source_bbox)
            source_overlap = bbox_overlap_area(image_component.source_bbox, translated_component.source_bbox)
            captured_beyond_visual_source = max(0.0, overlap - source_overlap)
            if captured_beyond_visual_source > bbox_area(translated_component.source_bbox) * max_visual_overcapture_ratio:
                issues.append(
                    OwnershipIssue(
                        issue_code="visual_clip_overcaptures_translated_component",
                        severity="error",
                        page_num=page_num,
                        message=f"visual component {image_component_id} captures translated component {translated_component.component_id}",
                        source_ids=translated_component.source_ids,
                        component_ids=[image_component_id, translated_component.component_id],
                        bboxes=[image_box, translated_component.source_bbox],
                    )
                )

    for text_item in text_items:
        text_box = _item_bbox(text_item)
        text_component_id = _item_component_id(text_item)
        for visual_component in visual_components:
            if visual_component.component_id == text_component_id:
                continue
            visual_boxes = visual_render_boxes_by_component_id.get(visual_component.component_id) or [
                visual_component.clip_bbox or visual_component.source_bbox
            ]
            for visual_box in visual_boxes:
                if _significant_overlap(
                    text_box,
                    visual_box,
                    min_overlap_ratio=min_overlap_ratio,
                    min_overlap_height=min_overlap_height,
                ):
                    issues.append(
                        OwnershipIssue(
                            issue_code="text_over_visual_component",
                            severity="error",
                            page_num=page_num,
                            message=f"text item overlaps unrelated visual component {visual_component.component_id}",
                            source_ids=_item_source_ids(text_item),
                            component_ids=[text_component_id, visual_component.component_id],
                            bboxes=[text_box, visual_box],
                        )
                    )
                    break

    return OwnershipValidationResult(issues=issues)
