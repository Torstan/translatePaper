import json
import math
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import ownership
from classify import is_trivial_keep, normalize_text
from geometry import bbox_overlap_area, subtract_bbox


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
    # Layout relationships and font permissions are independent of diagnostics.
    layout_role: Literal["normal", "body_flow", "source_paragraph", "callout", "mixed_visual_body",
                         "embedded_heading", "title_metadata", "journal_footer"] = "normal"
    font_policy: Literal["document", "source_adapted", "compact_body_flow", "dense_visual_row"] = "document"
    raster_lines: list[str] = field(default_factory=list)
    raster_vertical: bool = False
    # Pixel region to erase before drawing this text; mixed blocks erase only their text component.
    raster_source_bbox: tuple[int, int, int, int] | None = None
    # None: existing visual clip; 0: explicit PDF page crop; positive: native image resource.
    source_image_xref: int | None = None


@dataclass(frozen=True)
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
class CoverageSource:
    """Source obligation; drawing outcomes belong exclusively to RenderItem."""
    block_id: str
    classification: str
    skipped: bool = False
    reference_signature: dict | None = None
    component_id: str = ""
    component_kind: str = ""


@dataclass
class PageRenderPlan:
    page_num: int
    items: list[RenderItem] = field(default_factory=list)
    coverage: list[CoverageSource] = field(default_factory=list)
    components: list[ownership.PageComponent] = field(default_factory=list)
    ownership_validation: ownership.OwnershipValidationResult = field(default_factory=ownership.OwnershipValidationResult)
    page_size: tuple[float, float] | None = None
    output_page_num: int | None = None
    coordinate_space: Literal["points", "pixels"] = "points"
    raster_size: tuple[int, int] | None = None

    @property
    def protected_boxes(self) -> list[tuple[float, float, float, float]]:
        return [item.bbox for item in self.items if item.kind == "original_image_clip"]

    @property
    def ledger(self) -> list[CoverageEntry]:
        """Report current drawing outcomes without losing absent source obligations.

        A component ID distinguishes fragments of one source block. A merged item
        without a single ID covers only the unique component of its kind; it must
        not hide a missing image or an ambiguous same-kind source fragment.
        """
        items_by_source = {}
        for item in self.items:
            for source_id in item.source_ids:
                items_by_source.setdefault(source_id, []).append(item)
        components_by_source = {}
        for source in self.coverage:
            if source.component_id:
                components_by_source.setdefault(source.block_id, {})[source.component_id] = source.component_kind
        result = []
        for source in self.coverage:
            outcomes = {}
            for item in items_by_source.get(source.block_id, []):
                if source.component_id and item.component_id and source.component_id != item.component_id:
                    continue
                components = components_by_source.get(source.block_id, {})
                if source.component_id and not item.component_id and len(components) > 1:
                    matching_ids = [key for key, kind in components.items() if kind and kind == item.component_kind]
                    if matching_ids != [source.component_id]:
                        continue
                outcomes.setdefault(item.kind, []).append(item.fallback_reason)
            if source.skipped:
                outcomes = {"skip_explicitly": [""]}
            for kind, reasons in (outcomes or {"unrendered": [""]}).items():
                result.append(CoverageEntry(
                    source.block_id, source.classification, kind, kind != "unrendered",
                    "; ".join(dict.fromkeys(reason for reason in reasons if reason)),
                    source.reference_signature, source.component_id, source.component_kind,
                ))
        return result


@dataclass
class DocumentRenderResult:
    plans: list[PageRenderPlan]
    translations: dict[str, str]


def bbox_to_json(box) -> list[float]:
    return [float(value) for value in box]


def render_item_to_json(item: RenderItem) -> dict:
    data = {
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
        "layout_role": item.layout_role,
        "font_policy": item.font_policy,
    }
    if item.raster_lines:
        data["raster_lines"] = list(item.raster_lines)
        data["raster_vertical"] = item.raster_vertical
    if item.raster_source_bbox is not None:
        data["raster_source_bbox"] = bbox_to_json(item.raster_source_bbox)
    if item.source_image_xref is not None:
        data["source_image_xref"] = item.source_image_xref
    return data


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
        "coordinate_space": plan.coordinate_space,
        "raster_size": None if plan.raster_size is None else list(plan.raster_size),
        "render_items": [render_item_to_json(item) for item in plan.items],
        "coverage_ledger": [coverage_entry_to_json(entry) for entry in plan.ledger],
        "components": ownership.components_to_json(plan.components),
        "ownership_ledger": ownership.ownership_ledger_to_json(plan.page_num, plan.components),
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


def load_render_plan_artifact(path: str | Path) -> PageRenderPlan:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"invalid render plan {path}: {exc}") from exc
    return render_plan_from_json(data, source=str(path))


def render_plan_from_json(plan: dict, *, source: str = "<memory>") -> PageRenderPlan:
    """Validate drawing data at the JSON boundary, preserving diagnostic severity.

    Missing drawing data is a broken artifact, not an empty page. Optional report
    fields retain their defaults, but malformed supplied fields never disappear.
    """
    def require(condition, field):
        if not condition:
            raise ValueError(f"invalid render plan {source}: {field}")

    def numbers(value, length):
        return (isinstance(value, list) and len(value) == length
                and all(type(v) in (int, float) and math.isfinite(v) for v in value))

    def box(value, field):
        require(numbers(value, 4), field)
        require(value[0] <= value[2] and value[1] <= value[3], field)

    require(isinstance(plan, dict), "expected object")
    require(type(plan.get("page_num")) is int and plan["page_num"] > 0, "page_num")
    require(isinstance(plan.get("render_items"), list), "render_items")
    for index, item in enumerate(plan["render_items"]):
        field = f"render_items[{index}]"
        require(isinstance(item, dict), field)
        require(item.get("kind") in {"translated_text", "original_selectable_text", "original_image_clip"}, field + ".kind")
        require(isinstance(item.get("source_ids"), list)
                and all(isinstance(v, str) for v in item["source_ids"]), field + ".source_ids")
        box(item.get("bbox"), field + ".bbox")
        if item["kind"] != "original_image_clip":
            require(isinstance(item.get("text"), str), field + ".text")
        for name in ("text", "style_name", "fallback_reason", "component_id", "component_kind", "layout_role", "font_policy"):
            require(isinstance(item.get(name, ""), str), field + "." + name)
        size = item.get("font_size")
        require(size is None or (type(size) in (int, float) and math.isfinite(size) and size > 0), field + ".font_size")
        xref = item.get("source_image_xref")
        require(xref is None or (type(xref) is int and xref >= 0
                                and item["kind"] == "original_image_clip"), field + ".source_image_xref")
        require(numbers(item.get("color", [0, 0, 0]), 3), field + ".color")
        require(isinstance(item.get("raster_lines", []), list)
                and all(isinstance(line, str) for line in item.get("raster_lines", [])), field + ".raster_lines")
        require(type(item.get("raster_vertical", False)) is bool, field + ".raster_vertical")
        if item.get("raster_source_bbox") is not None:
            box(item["raster_source_bbox"], field + ".raster_source_bbox")
    for name in ("coverage_ledger", "protected_regions", "components"):
        records = plan.get(name, [])
        require(isinstance(records, list) and all(isinstance(v, dict) for v in records), name)
    for region in plan.get("protected_regions", []):
        box(region.get("bbox"), "protected_regions.bbox")
    for entry in plan.get("coverage_ledger", []):
        for name in ("block_id", "classification", "render_kind"):
            require(isinstance(entry.get(name), str) and bool(entry[name]), "coverage_ledger." + name)
        require(type(entry.get("rendered")) is bool, "coverage_ledger.rendered")
        for name in ("component_id", "component_kind"):
            require(isinstance(entry.get(name, ""), str), "coverage_ledger." + name)
        if entry["rendered"] and entry["render_kind"] != "skip_explicitly":
            require(any(item["kind"] == entry["render_kind"]
                        and entry["block_id"] in item["source_ids"]
                        for item in plan["render_items"]),
                    "coverage_ledger has rendered entry without matching render item")
    for component in plan.get("components", []):
        require(isinstance(component.get("component_id"), str), "components.component_id")
        require(component.get("component_kind") in ownership.COMPONENT_KINDS, "components.component_kind")
        require(isinstance(component.get("source_ids"), list)
                and all(isinstance(v, str) for v in component["source_ids"]), "components.source_ids")
        box(component.get("source_bbox"), "components.source_bbox")
        if component.get("clip_bbox") is not None:
            box(component["clip_bbox"], "components.clip_bbox")
        require(isinstance(component.get("reason_codes", []), list)
                and all(isinstance(code, str) for code in component.get("reason_codes", [])),
                "components.reason_codes")
    if plan.get("page_size") is not None:
        require(numbers(plan["page_size"], 2) and min(plan["page_size"]) > 0, "page_size")
    if plan.get("output_page_num") is not None:
        require(type(plan["output_page_num"]) is int and plan["output_page_num"] > 0, "output_page_num")
    coordinate_space = plan.get("coordinate_space", "points")
    require(isinstance(coordinate_space, str) and coordinate_space in {"points", "pixels"}, "coordinate_space")
    if plan.get("raster_size") is not None:
        require(numbers(plan["raster_size"], 2) and min(plan["raster_size"]) > 0, "raster_size")
    validation = plan.get("ownership_validation", {})
    require(isinstance(validation, dict), "ownership_validation")
    issues = validation.get("issues", validation.get("errors", []))
    require(isinstance(issues, list), "ownership_validation.issues")
    for issue in issues:
        require(isinstance(issue, dict), "ownership issue")
        require(issue.get("severity") in {"error", "warning"}, "ownership issue severity")
        require(isinstance(issue.get("issue_code"), str) and isinstance(issue.get("message"), str), "ownership issue identity")
        require(isinstance(issue.get("bboxes", []), list), "ownership issue bboxes")
        for value in issue.get("bboxes", []):
            box(value, "ownership issue bbox")
    items = []
    for value in plan["render_items"]:
        fields = {key: value[key] for key in RenderItem.__dataclass_fields__ if key in value}
        for name in ("bbox", "color", "raster_source_bbox"):
            if fields.get(name) is not None:
                fields[name] = tuple(fields[name])
        items.append(RenderItem(**fields))
    coverage = []
    for entry in plan.get("coverage_ledger", []):
        obligation = CoverageSource(
            entry["block_id"], entry["classification"], entry["render_kind"] == "skip_explicitly",
            entry.get("reference_signature"), entry.get("component_id", ""), entry.get("component_kind", ""),
        )
        # One source obligation may produce multiple render kinds (title + metadata).
        if obligation not in coverage:
            coverage.append(obligation)
    return PageRenderPlan(
        page_num=plan["page_num"], items=items,
        coverage=coverage,
        components=[ownership.PageComponent(
            component_id=c["component_id"], component_kind=c["component_kind"], source_ids=list(c["source_ids"]),
            source_bbox=tuple(c["source_bbox"]), clip_bbox=tuple(c["clip_bbox"]) if c.get("clip_bbox") else None,
            confidence=c.get("confidence", ownership.CONFIDENCE_CONSERVATIVE),
            reason_codes=list(c.get("reason_codes", [])), parent_component_id=c.get("parent_component_id", ""),
        ) for c in plan.get("components", [])],
        ownership_validation=ownership.OwnershipValidationResult([
            ownership.OwnershipIssue(
                issue["issue_code"], issue["severity"], issue.get("page_num", plan["page_num"]), issue["message"],
                list(issue.get("source_ids", [])), list(issue.get("component_ids", [])),
                [tuple(bbox) for bbox in issue.get("bboxes", [])],
            ) for issue in issues]),
        page_size=tuple(plan["page_size"]) if plan.get("page_size") else None,
        output_page_num=plan.get("output_page_num"), coordinate_space=plan.get("coordinate_space", "points"),
        raster_size=tuple(plan["raster_size"]) if plan.get("raster_size") else None,
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


def validate_plan_coverage(
    page_num: int,
    blocks,
    plan: PageRenderPlan,
) -> list[str]:
    errors = []
    ledger_by_id: dict[str, list[CoverageEntry]] = {}
    items_by_id: dict[str, list[RenderItem]] = {}
    for item in plan.items:
        for source_id in item.source_ids:
            items_by_id.setdefault(source_id, []).append(item)
    for entry in plan.ledger:
        ledger_by_id.setdefault(entry.block_id, []).append(entry)
        if entry.component_kind == ownership.COMPONENT_KIND_VISUAL and entry.render_kind != "original_image_clip":
            errors.append(f"page {page_num} block {entry.block_id} visual component is not an image clip")
    for block in blocks:
        if is_trivial_keep(normalize_text(block.get("text", ""))):
            continue
        source_box = None
        if all(key in block for key in ("xMin", "yMin", "xMax", "yMax")):
            source_box = tuple(float(block[key]) for key in ("xMin", "yMin", "xMax", "yMax"))
            if plan.coordinate_space == "pixels" and plan.page_size and plan.raster_size:
                scale_x = plan.raster_size[0] / plan.page_size[0]
                scale_y = plan.raster_size[1] / plan.page_size[1]
                source_box = (source_box[0] * scale_x, source_box[1] * scale_y,
                              source_box[2] * scale_x, source_box[3] * scale_y)
        entries = ledger_by_id.get(block["id"], [])
        if not entries:
            errors.append(f"page {page_num} block {block['id']} has no coverage entry")
            continue
        for entry in entries:
            if entry.component_kind == ownership.COMPONENT_KIND_VISUAL and entry.render_kind != "original_image_clip":
                continue
            if not entry.rendered:
                errors.append(f"page {page_num} block {block['id']} is marked unrendered")
                continue
            if entry.render_kind == "skip_explicitly" and entry.classification not in ALLOWED_SKIP_CLASSES:
                errors.append(f"page {page_num} block {block['id']} has illegal skip class {entry.classification}")
            elif entry.render_kind != "skip_explicitly" and not any(
                item.kind == entry.render_kind for item in items_by_id.get(block["id"], [])
            ):
                errors.append(
                    f"page {page_num} block {block['id']} has no matching {entry.render_kind} render item"
                )
            elif entry.render_kind == "original_image_clip" and source_box and bbox_area(source_box) > 0:
                clips = [item for item in items_by_id.get(block["id"], [])
                         if item.kind == "original_image_clip"]
                if not any(bbox_overlap_area(source_box, item.bbox) > 0.5 for item in clips):
                    errors.append(f"page {page_num} block {block['id']} is outside its image clip")
    return errors


def validate_source_image_coverage(plan: PageRenderPlan, source_images: list[RenderItem]) -> list[str]:
    """Check original image obligations after layout, including split visual crops."""
    errors = []
    for source in source_images:
        for source_id in source.source_ids:
            entries = [entry for entry in plan.ledger if entry.block_id == source_id]
            remaining = [source.bbox]
            for item in plan.items:
                if item.kind == "original_image_clip" and source_id in item.source_ids:
                    remaining = [piece for box in remaining for piece in subtract_bbox(box, item.bbox)]
            if (not entries or any(not entry.rendered or entry.render_kind != "original_image_clip"
                                   for entry in entries) or sum(bbox_area(box) for box in remaining) > 0.01):
                errors.append(f"page {plan.page_num} source image {source_id} is not fully covered")
    return errors


def bbox_area(box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def bbox_overlap_height(a, b) -> float:
    return max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def bbox_significantly_overlaps_protected(box, protected_box) -> bool:
    """Ignore edge contact up to 6pt or 5% of the smaller box in layout and QA."""
    if bbox_overlap_height(box, protected_box) <= 6.0:
        return False
    overlap = bbox_overlap_area(box, protected_box)
    return overlap > min(bbox_area(box), bbox_area(protected_box)) * 0.05


def validate_plan_ownership(plan: PageRenderPlan, blocks) -> ownership.OwnershipValidationResult:
    """Check source ownership and render layers against the current plan."""
    source_result = ownership.validate_ownership(plan.page_num, blocks, plan.components)
    layer_result = ownership.validate_render_layer_exclusivity(plan, plan.components)
    return ownership.OwnershipValidationResult(source_result.issues + layer_result.issues)


def validate_plan_layout(plan: PageRenderPlan, page_size) -> list[str]:
    """Check page bounds and protected geometry; ownership is checked separately."""
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
            if not text_boxes_significantly_overlap(left.bbox, right.bbox):
                continue
            errors.append(
                f"page {plan.page_num} text {left.source_ids or left.fallback_reason}"
                f" overlaps text {right.source_ids or right.fallback_reason}"
            )
    return errors


def text_boxes_significantly_overlap(left, right) -> bool:
    """Source identity never exempts geometry; valid fragments occupy separate boxes."""
    return (bbox_overlap_height(left, right) > 3.0
            and bbox_overlap_area(left, right) > min(bbox_area(left), bbox_area(right)) * 0.12)


def ledger_classifications(plan: PageRenderPlan) -> dict[str, str]:
    return {entry.block_id: entry.classification for entry in plan.coverage}
