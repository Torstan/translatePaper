from dataclasses import dataclass
import json
from pathlib import Path

import classify
import render_plan
from classify import mark_running_headers
import translate_pdf_via_codex as pdf


@dataclass(frozen=True)
class RenderPlanFixture:
    page_num: int
    blocks: list[dict]
    translations: dict[str, str]
    expected_plan: dict
    plan: render_plan.PageRenderPlan


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _fixture_path(root: Path, fixture_type: str, document_slug: str, page_num: int) -> Path:
    return root / fixture_type / document_slug / f"page-{page_num:03d}.json"


def iter_render_plan_fixtures(
    fixture_root: Path,
    document_slug: str,
    *,
    page_size=(623, 801),
):
    source_dir = fixture_root / "source_pages" / document_slug
    sources = [(path, _load_json(path)) for path in sorted(source_dir.glob("page-*.json"))]
    marked_pages = dict(mark_running_headers([(int(source["page"]), source["blocks"]) for _, source in sources]))
    for source_path, source in sources:
        page_num = int(source["page"])
        translation_path = _fixture_path(fixture_root, "translations", document_slug, page_num)
        expected_plan_path = _fixture_path(fixture_root, "expected_plans", document_slug, page_num)

        translations = _load_json(translation_path)
        expected_plan = _load_json(expected_plan_path)
        for companion_name, companion in (
            ("translations", translations),
            ("expected_plans", expected_plan),
        ):
            if companion.get("document") != source.get("document") or int(companion.get("page", -1)) != page_num:
                raise AssertionError(f"{companion_name} fixture does not match {source_path.name}")

        blocks = marked_pages[page_num]
        cached_translations = translations["translations"]
        plan = pdf.build_page_render_plan(
            page_num,
            blocks,
            cached_translations,
            page_size=page_size,
            bbox_lines=None,
            source_image_path=None,
        )

        yield RenderPlanFixture(
            page_num=page_num,
            blocks=blocks,
            translations=cached_translations,
            expected_plan=expected_plan,
            plan=plan,
        )


def iter_wait_free_render_plan_fixtures(fixture_root: Path, *, page_size=(623, 801)):
    yield from iter_render_plan_fixtures(fixture_root, "wait-free", page_size=page_size)


def assert_render_plan_fixture(fixture: RenderPlanFixture) -> None:
    plan_json = render_plan.render_plan_to_json(fixture.plan)
    _assert_coverage_ledger_complete(fixture, plan_json)

    assertions = fixture.expected_plan.get("assertions") or []
    if not assertions:
        raise AssertionError(f"page {fixture.page_num} has no expected plan assertions")

    for assertion in assertions:
        expect = assertion.get("expect")
        if expect == "coverage_ledger_complete":
            _assert_coverage_ledger_complete(fixture, plan_json)
        elif expect == "ledger_entry":
            _assert_ledger_entry(fixture, plan_json, assertion)
        elif expect == "ledger_entries":
            for source_id in _source_ids(assertion):
                _assert_ledger_entry(fixture, plan_json, {**assertion, "source_id": source_id})
        elif expect == "translated_text_style":
            _assert_translated_text_style(fixture, plan_json, assertion)
        elif expect == "translated_text":
            _assert_translated_text(fixture, plan_json, assertion)
        elif expect == "original_image_clip":
            _assert_original_image_clip(fixture, plan_json, assertion)
        elif expect == "not_in_source_ids":
            _assert_not_in_source_ids(fixture, plan_json, assertion)
        elif expect == "reference_original_text":
            _assert_reference_original_text(fixture, plan_json, assertion)
        elif expect == "translated_text_excludes":
            _assert_translated_text_excludes(fixture, plan_json, assertion)
        elif expect == "max_visible_gap":
            _assert_max_visible_gap(fixture, plan_json, assertion)
        elif expect == "ownership_validation_ok":
            _assert_ownership_validation_ok(fixture, plan_json, assertion)
        elif expect == "component_contains":
            _assert_component_contains(fixture, plan_json, assertion)
        elif expect == "component_kind_for_source":
            _assert_component_kind_for_source(fixture, plan_json, assertion)
        elif expect == "source_not_rendered_as":
            _assert_source_not_rendered_as(fixture, plan_json, assertion)
        elif expect == "no_text_over_component":
            _assert_no_text_over_component(fixture, plan_json, assertion)
        else:
            _fail(fixture, assertion, f"unsupported expected plan assertion: {expect!r}")


def _fail(fixture: RenderPlanFixture, assertion: dict, message: str) -> None:
    summary = assertion.get("summary")
    prefix = f"page {fixture.page_num} assertion {assertion.get('expect')!r}"
    if summary:
        prefix = f"{prefix} ({summary})"
    raise AssertionError(f"{prefix}: {message}")


def _source_ids(assertion: dict) -> list[str]:
    if "source_ids" in assertion:
        return list(assertion["source_ids"])
    if "source_id" in assertion:
        return [assertion["source_id"]]
    raise AssertionError(f"assertion has no source_id/source_ids: {assertion}")


def _expected_strings(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


def _normalize_spaces(text: str) -> str:
    return " ".join(str(text).split())


def _contains_text(text: str, expected: str) -> bool:
    return expected in text or _normalize_spaces(expected) in _normalize_spaces(text)


def _contains_reference_text(text: str, expected: str) -> bool:
    if _contains_text(text, expected):
        return True
    compact_text = "".join(char.lower() for char in text if char.isalnum())
    compact_expected = "".join(char.lower() for char in expected if char.isalnum())
    return bool(compact_expected and compact_expected in compact_text)


def _ledger_by_id(plan_json: dict) -> dict[str, dict]:
    result = {}
    for entry in plan_json["coverage_ledger"]:
        result.setdefault(entry["block_id"], []).append(entry)
    return result


def _split_components_for_source(plan_json: dict, source_id: str) -> tuple[dict, dict] | None:
    components = [
        component
        for component in plan_json.get("components", [])
        if source_id in component.get("source_ids", [])
        and "mixed_visual_body_split" in component.get("reason_codes", [])
    ]
    visual = [component for component in components if component.get("component_kind") == "visual"]
    text = [component for component in components if component.get("component_kind") == "translated_text"]
    if len(visual) != 1 or len(text) != 1:
        return None
    if text[0].get("parent_component_id") != visual[0].get("component_id"):
        return None
    return visual[0], text[0]


def _source_has_valid_split_ledger(plan_json: dict, source_id: str) -> bool:
    split = _split_components_for_source(plan_json, source_id)
    if split is None:
        return False
    visual_component, text_component = split
    entries = [entry for entry in plan_json["coverage_ledger"] if entry["block_id"] == source_id]
    if len(entries) != 2:
        return False
    expected = {
        ("original_image_clip", visual_component["component_id"], "visual"),
        ("translated_text", text_component["component_id"], "translated_text"),
    }
    actual = {
        (entry.get("render_kind"), entry.get("component_id"), entry.get("component_kind"))
        for entry in entries
    }
    return actual == expected


def _assert_coverage_ledger_complete(fixture: RenderPlanFixture, plan_json: dict) -> None:
    expected_ids = {
        block["id"]
        for block in fixture.blocks
        if classify.normalize_text(block.get("text", ""))
    }
    ledger_ids = [entry["block_id"] for entry in plan_json["coverage_ledger"]]
    actual_ids = {source_id for source_id in ledger_ids if source_id}
    duplicates = sorted(
        source_id
        for source_id in actual_ids
        if ledger_ids.count(source_id) > 1 and not _source_has_valid_split_ledger(plan_json, source_id)
    )
    missing = sorted(expected_ids - actual_ids)
    extra = sorted(actual_ids - expected_ids)
    validation_errors = render_plan.validate_plan_coverage(fixture.page_num, fixture.blocks, fixture.plan)

    if duplicates or missing or extra or validation_errors:
        raise AssertionError(
            f"page {fixture.page_num} coverage ledger mismatch: "
            f"missing={missing}, extra={extra}, duplicates={duplicates}, "
            f"validation_errors={validation_errors}"
        )


def _assert_ledger_entry(fixture: RenderPlanFixture, plan_json: dict, assertion: dict) -> None:
    source_id = assertion["source_id"]
    entries = _ledger_by_id(plan_json).get(source_id, [])
    if not entries:
        _fail(fixture, assertion, f"missing coverage ledger entry for {source_id}")
    if len(entries) > 1 and not _source_has_valid_split_ledger(plan_json, source_id):
        _fail(fixture, assertion, f"{source_id} has invalid duplicate coverage entries: {entries}")

    for field in ("classification", "render_kind", "fallback_reason"):
        if field not in assertion:
            continue
        matching = [entry for entry in entries if entry.get(field) == assertion[field]]
        if matching:
            continue
        entry = entries[-1]
        if entry.get(field) != assertion[field]:
            _fail(
                fixture,
                assertion,
                f"{source_id} {field} is {entry.get(field)!r}, expected {assertion[field]!r}",
            )


def _render_items_for_source(plan_json: dict, source_id: str, *, kind: str | None = None) -> list[dict]:
    return [
        item
        for item in plan_json["render_items"]
        if source_id in item.get("source_ids", [])
        and (kind is None or item["kind"] == kind)
    ]


def _joined_item_text(items: list[dict]) -> str:
    return "\n".join(item.get("text", "") for item in items if item.get("text"))


def _assert_text_contains(fixture: RenderPlanFixture, assertion: dict, text: str) -> None:
    for expected in _expected_strings(assertion.get("text_contains")):
        if not _contains_text(text, expected):
            _fail(fixture, assertion, f"text does not contain {expected!r}; rendered text was {text!r}")


def _assert_translated_text_style(fixture: RenderPlanFixture, plan_json: dict, assertion: dict) -> None:
    source_id = assertion["source_id"]
    items = _render_items_for_source(plan_json, source_id, kind="translated_text")
    if not items:
        _fail(fixture, assertion, f"{source_id} is not rendered as translated_text")

    style_name = assertion.get("style_name")
    styled_items = [
        item
        for item in items
        if style_name is None or item.get("style_name") == style_name
    ]
    if not styled_items:
        actual_styles = sorted({item.get("style_name", "") for item in items})
        _fail(fixture, assertion, f"{source_id} styles are {actual_styles}, expected {style_name!r}")

    _assert_text_contains(fixture, assertion, _joined_item_text(styled_items))


def _assert_translated_text(fixture: RenderPlanFixture, plan_json: dict, assertion: dict) -> None:
    source_id = assertion["source_id"]
    items = _render_items_for_source(plan_json, source_id, kind="translated_text")
    if not items:
        _fail(fixture, assertion, f"{source_id} is not rendered as translated_text")
    _assert_text_contains(fixture, assertion, _joined_item_text(items))


def _matching_image_items(plan_json: dict, source_ids: list[str]) -> list[dict]:
    expected_ids = set(source_ids)
    return [
        item
        for item in plan_json["render_items"]
        if item["kind"] == "original_image_clip"
        and expected_ids <= set(item.get("source_ids", []))
    ]


def _bbox_satisfies_constraints(bbox: list[float], constraints: dict) -> bool:
    mins = constraints.get("min")
    maxes = constraints.get("max")
    if mins is not None and any(actual < expected for actual, expected in zip(bbox, mins)):
        return False
    if maxes is not None and any(actual > expected for actual, expected in zip(bbox, maxes)):
        return False

    names = ("x0", "y0", "x1", "y1")
    for index, name in enumerate(names):
        if f"{name}_min" in constraints and bbox[index] < constraints[f"{name}_min"]:
            return False
        if f"{name}_max" in constraints and bbox[index] > constraints[f"{name}_max"]:
            return False
    return True


def _same_bbox(left: list[float], right: list[float], *, tolerance: float = 0.01) -> bool:
    return all(abs(a - b) <= tolerance for a, b in zip(left, right))


def _has_matching_protected_region(plan_json: dict, bbox: list[float]) -> bool:
    return any(_same_bbox(region["bbox"], bbox) for region in plan_json["protected_regions"])


def _assert_original_image_clip(fixture: RenderPlanFixture, plan_json: dict, assertion: dict) -> None:
    source_ids = _source_ids(assertion)
    missing = [
        source_id
        for source_id in source_ids
        if not _render_items_for_source(plan_json, source_id, kind="original_image_clip")
    ]
    if missing:
        _fail(fixture, assertion, f"source IDs are not in original_image_clip items: {missing}")

    constraints = assertion.get("bbox_constraints")
    if not constraints:
        return

    matching_items = _matching_image_items(plan_json, source_ids)
    if not matching_items:
        _fail(fixture, assertion, f"no single original_image_clip contains all source IDs {source_ids}")

    matching_bboxes = [item["bbox"] for item in matching_items]
    if not any(
        _bbox_satisfies_constraints(item["bbox"], constraints)
        and _has_matching_protected_region(plan_json, item["bbox"])
        for item in matching_items
    ):
        _fail(
            fixture,
            assertion,
            "no protected image clip bbox satisfies "
            f"{constraints}; matching bboxes were {matching_bboxes}",
        )


def _assert_not_in_source_ids(fixture: RenderPlanFixture, plan_json: dict, assertion: dict) -> None:
    target_source_id = assertion["target_source_id"]
    target_items = _render_items_for_source(plan_json, target_source_id, kind="original_image_clip")
    if not target_items:
        _fail(fixture, assertion, f"{target_source_id} is not in an original_image_clip item")

    forbidden = set(_source_ids(assertion))
    for item in target_items:
        unexpected = sorted(forbidden & set(item.get("source_ids", [])))
        if unexpected:
            _fail(
                fixture,
                assertion,
                f"{target_source_id} clip unexpectedly includes source IDs {unexpected}",
            )


def _assert_reference_original_text(fixture: RenderPlanFixture, plan_json: dict, assertion: dict) -> None:
    reference_items = [
        item
        for item in plan_json["render_items"]
        if item["kind"] == "original_selectable_text"
        and (
            item.get("style_name") == "reference"
            or item.get("fallback_reason", "").startswith("reference_original")
        )
    ]
    if not reference_items:
        _fail(fixture, assertion, "no original selectable reference text items were rendered")

    text = _joined_item_text(reference_items)
    for expected in _expected_strings(assertion.get("text_contains")):
        if not _contains_reference_text(text, expected):
            _fail(fixture, assertion, f"reference text does not contain {expected!r}")


def _assert_translated_text_excludes(fixture: RenderPlanFixture, plan_json: dict, assertion: dict) -> None:
    source_id = assertion["source_id"]
    items = _render_items_for_source(plan_json, source_id, kind="translated_text")
    if not items:
        if assertion.get("allow_missing_translated_text"):
            return
        _fail(fixture, assertion, f"{source_id} is not rendered as translated_text")
    text = _joined_item_text(items)
    normalized_text = _normalize_spaces(text)
    for excluded in _expected_strings(assertion.get("text_excludes")):
        if excluded in text or _normalize_spaces(excluded) in normalized_text:
            _fail(fixture, assertion, f"translated text for {source_id} contains excluded text {excluded!r}")


def _assert_max_visible_gap(fixture: RenderPlanFixture, plan_json: dict, assertion: dict) -> None:
    selected_items = []
    seen_indexes = set()
    source_ids = set(_source_ids(assertion))
    for index, item in enumerate(plan_json["render_items"]):
        if source_ids & set(item.get("source_ids", [])):
            seen_indexes.add(index)
            selected_items.append(item)

    missing = [
        source_id
        for source_id in source_ids
        if not _render_items_for_source(plan_json, source_id)
    ]
    if missing:
        _fail(fixture, assertion, f"source IDs have no render items for gap check: {sorted(missing)}")

    if not seen_indexes or len(selected_items) < 2:
        return

    selected_items.sort(key=lambda item: (item["bbox"][1], item["bbox"][0]))
    gaps = [
        max(0.0, next_item["bbox"][1] - current_item["bbox"][3])
        for current_item, next_item in zip(selected_items, selected_items[1:])
    ]
    actual_gap = max(gaps, default=0.0)
    max_points = float(assertion["max_points"])
    if actual_gap > max_points:
        _fail(fixture, assertion, f"visible gap is {actual_gap:.2f}pt, expected at most {max_points:.2f}pt")


def _ownership_components(plan_json: dict) -> list[dict]:
    return list(plan_json["components"])


def _bbox_area(bbox) -> float:
    x0, y0, x1, y1 = (float(value) for value in bbox)
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def _bbox_overlap_area(left, right) -> float:
    left = [float(value) for value in left]
    right = [float(value) for value in right]
    x0 = max(left[0], right[0])
    y0 = max(left[1], right[1])
    x1 = min(left[2], right[2])
    y1 = min(left[3], right[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def _assert_ownership_validation_ok(fixture: RenderPlanFixture, plan_json: dict, assertion: dict) -> None:
    validation = plan_json.get("ownership_validation") or {}
    errors = validation.get("errors")
    if errors is None:
        errors = validation.get("issues", [])
    if errors:
        _fail(fixture, assertion, f"ownership validation has errors: {errors}")


def _assert_component_contains(fixture: RenderPlanFixture, plan_json: dict, assertion: dict) -> None:
    source_ids = set(_source_ids(assertion))
    component_kind = assertion.get("component_kind")
    matches = [
        component
        for component in _ownership_components(plan_json)
        if source_ids <= set(component.get("source_ids", []))
        and (component_kind is None or component.get("component_kind") == component_kind)
    ]
    if not matches:
        _fail(fixture, assertion, f"no {component_kind!r} component contains {sorted(source_ids)}")


def _assert_component_kind_for_source(fixture: RenderPlanFixture, plan_json: dict, assertion: dict) -> None:
    source_id = assertion["source_id"]
    expected_kind = assertion["component_kind"]
    actual = [
        component.get("component_kind")
        for component in _ownership_components(plan_json)
        if source_id in component.get("source_ids", [])
    ]
    if actual != [expected_kind]:
        _fail(fixture, assertion, f"{source_id} component kinds are {actual}, expected exactly {[expected_kind]}")


def _assert_source_not_rendered_as(fixture: RenderPlanFixture, plan_json: dict, assertion: dict) -> None:
    source_id = assertion["source_id"]
    forbidden_kind = assertion["render_kind"]
    matching = _render_items_for_source(plan_json, source_id, kind=forbidden_kind)
    if matching:
        _fail(fixture, assertion, f"{source_id} is unexpectedly rendered as {forbidden_kind}")


def _assert_no_text_over_component(fixture: RenderPlanFixture, plan_json: dict, assertion: dict) -> None:
    source_ids = set(_source_ids(assertion))
    components = [
        component
        for component in _ownership_components(plan_json)
        if source_ids <= set(component.get("source_ids", []))
    ]
    if not components:
        _fail(fixture, assertion, f"no component contains {sorted(source_ids)}")

    component_bbox = components[0].get("clip_bbox") or components[0].get("source_bbox")
    if component_bbox is None:
        _fail(fixture, assertion, f"component for {sorted(source_ids)} has no bbox")

    offenders = []
    for item in plan_json["render_items"]:
        if item.get("kind") not in {"translated_text", "original_selectable_text"}:
            continue
        if source_ids & set(item.get("source_ids", [])):
            continue
        if _bbox_overlap_area(item["bbox"], component_bbox) > min(_bbox_area(item["bbox"]), _bbox_area(component_bbox)) * 0.05:
            offenders.append(item.get("source_ids", []))
    if offenders:
        _fail(fixture, assertion, f"text items overlap component {sorted(source_ids)}: {offenders}")
