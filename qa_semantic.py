import difflib
import json
import math
import re
import textwrap
from pathlib import Path

from translation_batch import execute_json_task


def normalize_english(text: str) -> str:
    text = text.lower()
    text = text.replace("’", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*([,.;:!?()])\s*", r"\1", text)
    return text.strip()


def make_schema(path: Path):
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "back_translation": {"type": "string"},
                    },
                    "required": ["id", "back_translation"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }
    path.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")


def make_prompt(batch):
    intro = textwrap.dedent(
        """
        You are performing a back-translation quality check.
        Translate each Simplified Chinese academic text back into precise English.

        Requirements:
        1. Preserve technical meaning exactly.
        2. Preserve names, system names, URLs, emails, code identifiers, citations, versions, and file paths.
        3. Do not summarize or explain.
        4. Return strict JSON with this shape:
           {"items":[{"id":"...","back_translation":"..."}]}
        5. Do not omit any item.

        Items:
        """
    ).strip()
    return intro + "\n" + json.dumps({"items": batch}, ensure_ascii=False, indent=2)


def validate_backtranslation_payload(payload, expected_ids: set[str]) -> dict[str, str]:
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("expected an items array")
    result = {}
    for item in payload["items"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ValueError("expected a string item id")
        block_id = item["id"]
        if block_id in result:
            raise ValueError(f"duplicate id: {block_id}")
        text = item.get("back_translation")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"empty or invalid back translation: {block_id}")
        result[block_id] = text.strip()
    if set(result) != expected_ids:
        raise ValueError(
            f"mismatched ids, missing={sorted(expected_ids - result.keys())[:5]}, "
            f"extra={sorted(result.keys() - expected_ids)[:5]}"
        )
    return result


def run_backtranslation(
    items,
    batch_chars: int,
    job_dir: Path,
    *,
    model: str = "gpt-5.5",
    reasoning_effort: str = "low",
    retries: int = 3,
):
    schema_path = job_dir / "backtranslate_schema.json"
    make_schema(schema_path)
    results = {}
    batches = []
    current = []
    current_chars = 0
    for item in items:
        item_chars = len(item["translation"])
        if current and current_chars + item_chars > batch_chars:
            batches.append(current)
            current = []
            current_chars = 0
        current.append(item)
        current_chars += item_chars
    if current:
        batches.append(current)

    for idx, batch in enumerate(batches, start=1):
        expected_ids = {item["id"] for item in batch}
        results.update(execute_json_task(
            make_prompt(batch), job_dir, f"backtranslate-{idx:02d}", schema_path,
            validate=lambda payload: validate_backtranslation_payload(payload, expected_ids),
            model=model, reasoning_effort=reasoning_effort, retries=retries,
        ))
    return results


def build_report(original_map, translations, backtranslations):
    report = []
    for block_id, zh in translations.items():
        orig = original_map.get(block_id)
        back = backtranslations.get(block_id, "")
        if not orig:
            continue
        score = difflib.SequenceMatcher(
            None,
            normalize_english(orig),
            normalize_english(back),
        ).ratio()
        report.append(
            {
                "id": block_id,
                "score": round(score, 4),
                "original": orig,
                "translation": zh,
                "back_translation": back,
            }
        )
    report.sort(key=lambda item: item["score"])
    return report


def classify_block(text: str):
    stripped = text.strip()
    if not stripped:
        return "empty"
    if len(stripped) <= 80 and "\n" not in stripped:
        return "short"
    if re.match(r"^(abstract|introduction|conclusion|references)\b", stripped, re.I):
        return "section_header"
    if stripped.endswith(":") and len(stripped) <= 120:
        return "section_header"
    return "body"


def choose_items_for_qa(original_map, translations, mode: str, sample_size: int):
    all_items = [{"id": block_id, "translation": text} for block_id, text in translations.items()]
    if mode == "all" or len(all_items) <= sample_size:
        return all_items

    scored = []
    for block_id, text in translations.items():
        original = original_map.get(block_id, "")
        page_match = re.match(r"p(\d{3})b", block_id)
        page_num = int(page_match.group(1)) if page_match else 0
        block_type = classify_block(original)
        priority = 0
        if page_num <= 2:
            priority += 5
        if block_type == "section_header":
            priority += 4
        elif block_type == "short":
            priority += 2
        if "abstract" in original.lower():
            priority += 4
        scored.append((priority, len(original), page_num, block_id, text))

    scored.sort(key=lambda item: (-item[0], item[2], item[1], item[3]))
    chosen = []
    chosen_ids = set()
    head_count = min(sample_size // 2, len(scored))
    for _, _, _, block_id, text in scored[:head_count]:
        chosen.append({"id": block_id, "translation": text})
        chosen_ids.add(block_id)

    if len(chosen) < sample_size:
        remaining = [item for item in scored if item[3] not in chosen_ids]
        stride = max(1, math.ceil(len(remaining) / max(1, sample_size - len(chosen))))
        for item in remaining[::stride]:
            chosen.append({"id": item[3], "translation": item[4]})
            if len(chosen) >= sample_size:
                break
    return chosen


def write_markdown(report, path: Path):
    lines = ["# Back-Translation QA Report", ""]
    for item in report[:20]:
        lines.extend(
            [
                f"## {item['id']}  score={item['score']}",
                "",
                f"Original: {item['original']}",
                "",
                f"Chinese: {item['translation']}",
                "",
                f"Back translation: {item['back_translation']}",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")
