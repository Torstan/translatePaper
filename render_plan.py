import json
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import ownership


VECTOR_BODY_COLOR = (0, 0, 0)
ALLOWED_SKIP_CLASSES = {"page_number", "header_footer", "nested_duplicate"}


@dataclass
class RenderItem:
    kind: str
    source_ids: list[str]
    bbox: tuple[float, float, float, float]
    text: str = ""
    font_size: float | None = None
    style_name: str = ""
    color: tuple[float, float, float] = VECTOR_BODY_COLOR
    fallback_reason: str = ""
    component_id: str = ""
    component_kind: str = ""


@dataclass
class CoverageEntry:
    block_id: str
    classification: str
    render_kind: str
    rendered: bool
    fallback_reason: str = ""
    reference_signature: dict | None = None
    component_id: str = ""
    component_kind: str = ""


@dataclass
class PageRenderPlan:
    page_num: int
    items: list[RenderItem] = field(default_factory=list)
    ledger: list[CoverageEntry] = field(default_factory=list)
    protected_boxes: list[tuple[float, float, float, float]] = field(default_factory=list)
    components: list[ownership.PageComponent] = field(default_factory=list)
    ownership_ledger: list[ownership.OwnershipLedgerEntry] = field(default_factory=list)
    ownership_validation: ownership.OwnershipValidationResult = field(default_factory=ownership.OwnershipValidationResult)
    page_size: tuple[float, float] | None = None
    output_page_num: int | None = None


@dataclass
class DocumentRenderResult:
    plans: list[PageRenderPlan]
    translations: dict[str, str]


def bbox_to_json(box) -> list[float]:
    return [float(value) for value in box]


def render_item_to_json(item: RenderItem) -> dict:
    return {
        "kind": item.kind,
        "source_ids": list(item.source_ids),
        "bbox": bbox_to_json(item.bbox),
        "text": item.text,
        "font_size": None if item.font_size is None else float(item.font_size),
        "style_name": item.style_name,
        "color": [float(value) for value in item.color],
        "fallback_reason": item.fallback_reason,
        "component_id": item.component_id,
        "component_kind": item.component_kind,
    }


def coverage_entry_to_json(entry: CoverageEntry) -> dict:
    data = {
        "block_id": entry.block_id,
        "classification": entry.classification,
        "render_kind": entry.render_kind,
        "rendered": bool(entry.rendered),
        "fallback_reason": entry.fallback_reason,
        "component_id": entry.component_id,
        "component_kind": entry.component_kind,
    }
    if entry.reference_signature is not None:
        data["reference_signature"] = _stable_json_value(entry.reference_signature)
    return data


def _stable_json_value(value):
    if isinstance(value, Mapping):
        return {str(key): _stable_json_value(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, tuple):
        return [_stable_json_value(item) for item in value]
    if isinstance(value, list):
        return [_stable_json_value(item) for item in value]
    return value


def render_plan_to_json(plan: PageRenderPlan, validation_results: dict | list | None = None) -> dict:
    return {
        "page_num": int(plan.page_num),
        "page_size": None if plan.page_size is None else [float(value) for value in plan.page_size],
        "output_page_num": plan.output_page_num,
        "render_items": [render_item_to_json(item) for item in plan.items],
        "coverage_ledger": [coverage_entry_to_json(entry) for entry in plan.ledger],
        "components": ownership.components_to_json(plan.components),
        "ownership_ledger": [
            ownership.ownership_ledger_entry_to_json(entry)
            for entry in sorted(plan.ownership_ledger, key=lambda item: (item.source_id, item.component_id))
        ],
        "ownership_validation": ownership.ownership_validation_to_json(plan.ownership_validation),
        "protected_regions": [{"bbox": bbox_to_json(box)} for box in plan.protected_boxes],
        "validation_results": _stable_json_value(validation_results) if validation_results is not None else [],
    }


def render_plan_json_dumps(plan: PageRenderPlan, validation_results: dict | list | None = None) -> str:
    return json.dumps(
        render_plan_to_json(plan, validation_results),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def render_plan_artifact_path(job_paths, page_num: int) -> Path | None:
    if not job_paths:
        return None
    plans_dir = job_paths.get("plans_dir")
    if plans_dir:
        return Path(plans_dir) / f"page-{page_num:03d}.render-plan.json"
    job_dir = job_paths.get("job_dir")
    if job_dir:
        return Path(job_dir) / "plans" / f"page-{page_num:03d}.render-plan.json"
    return None


def write_render_plan_artifact(plan: PageRenderPlan, validation_results: dict, job_paths) -> Path | None:
    path = render_plan_artifact_path(job_paths, plan.page_num)
    if path is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_plan_json_dumps(plan, validation_results=validation_results), encoding="utf-8")
    return path


def try_write_render_plan_artifact(plan: PageRenderPlan, validation_results: dict, job_paths) -> Path | None:
    try:
        return write_render_plan_artifact(plan, validation_results, job_paths)
    except Exception as exc:  # noqa: BLE001
        print(
            f"warning: failed to write render plan artifact for page {plan.page_num}: {exc}",
            file=sys.stderr,
        )
        return None


def is_render_plan_trivial_keep(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    if re.fullmatch(r"\d+", stripped):
        return True
    if re.fullmatch(r"https?://\S+", stripped):
        return True
    return False


def nontrivial_block(block) -> bool:
    text = re.sub(r"\s+", " ", block.get("text", "")).strip()
    return bool(text) and not is_render_plan_trivial_keep(text)


def validate_plan_coverage(
    page_num: int,
    blocks,
    plan: PageRenderPlan,
    *,
    is_nontrivial_block=nontrivial_block,
) -> list[str]:
    errors = []
    ledger_by_id: dict[str, list[CoverageEntry]] = {}
    for entry in plan.ledger:
        ledger_by_id.setdefault(entry.block_id, []).append(entry)
    for block in blocks:
        if not is_nontrivial_block(block):
            continue
        entries = ledger_by_id.get(block["id"], [])
        if not entries:
            errors.append(f"page {page_num} block {block['id']} has no coverage entry")
            continue
        for entry in entries:
            if not entry.rendered:
                errors.append(f"page {page_num} block {block['id']} is marked unrendered")
                continue
            if entry.render_kind == "skip_explicitly" and entry.classification not in ALLOWED_SKIP_CLASSES:
                errors.append(f"page {page_num} block {block['id']} has illegal skip class {entry.classification}")
    return errors


def bbox_area(box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def bbox_overlap_area(a, b) -> float:
    x0 = max(a[0], b[0])
    y0 = max(a[1], b[1])
    x1 = min(a[2], b[2])
    y1 = min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def bbox_overlap_height(a, b) -> float:
    return max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def bbox_significantly_overlaps_protected(box, protected_box) -> bool:
    """Ignore edge contact up to 6pt or 5% of the smaller box in layout and QA."""
    if bbox_overlap_height(box, protected_box) <= 6.0:
        return False
    overlap = bbox_overlap_area(box, protected_box)
    return overlap > min(bbox_area(box), bbox_area(protected_box)) * 0.05


def validate_plan_layout(plan: PageRenderPlan, page_size) -> list[str]:
    errors = []
    width, height = page_size
    protected = [item for item in plan.items if item.kind == "original_image_clip"]
    for item in plan.items:
        x0, y0, x1, y1 = item.bbox
        if x0 < -6.0 or y0 < -6.0 or x1 > width + 6.0 or y1 > height + 6.0:
            errors.append(f"page {plan.page_num} item {item.source_ids} outside page bounds")
        if item.kind != "translated_text":
            continue
        for protected_item in protected:
            if bbox_significantly_overlaps_protected(item.bbox, protected_item.bbox):
                errors.append(f"page {plan.page_num} text {item.source_ids} overlaps protected {protected_item.source_ids}")
    ownership_result = ownership.validate_render_layer_exclusivity(plan, plan.components)
    errors.extend(issue.message for issue in ownership_result.issues)
    return errors


def validate_plan_text_overlaps(plan: PageRenderPlan) -> list[str]:
    errors = []
    text_items = [
        item
        for item in plan.items
        if item.kind in {"translated_text", "original_selectable_text"} and bbox_area(item.bbox) > 0
    ]
    for left_idx, left in enumerate(text_items):
        for right in text_items[left_idx + 1 :]:
            if left.source_ids and right.source_ids and set(left.source_ids) == set(right.source_ids):
                continue
            if bbox_overlap_height(left.bbox, right.bbox) <= 3.0:
                continue
            overlap = bbox_overlap_area(left.bbox, right.bbox)
            if overlap <= min(bbox_area(left.bbox), bbox_area(right.bbox)) * 0.12:
                continue
            errors.append(
                f"page {plan.page_num} text {left.source_ids or left.fallback_reason}"
                f" overlaps text {right.source_ids or right.fallback_reason}"
            )
    return errors


def update_ledger_render_kind(plan: PageRenderPlan, source_ids: list[str], render_kind: str, fallback_reason: str):
    source_id_set = set(source_ids)
    for entry in plan.ledger:
        if entry.block_id not in source_id_set:
            continue
        entry.render_kind = render_kind
        entry.fallback_reason = fallback_reason


def ledger_classifications(plan: PageRenderPlan) -> dict[str, str]:
    return {entry.block_id: entry.classification for entry in plan.ledger}
