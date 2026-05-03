#!/usr/bin/env python3

import argparse
import difflib
import json
import math
import re
import sys
import textwrap
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parent
TMP_DIR = TOOL_ROOT / "work"

sys.path.insert(0, str(TOOL_ROOT))
import translate_pdf_via_codex as pipeline  # noqa: E402


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
        prompt = make_prompt(batch)
        out_path = job_dir / f"backtranslate-{idx:02d}.json"
        log_path = job_dir / f"backtranslate-{idx:02d}.log.txt"
        success = False
        for attempt in range(1, retries + 1):
            proc = pipeline.run(
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
                continue

            payload = json.loads(out_path.read_text(encoding="utf-8"))
            expected = {item["id"] for item in batch}
            seen = {item["id"] for item in payload["items"]}
            if seen != expected:
                continue

            empty_items = [item["id"] for item in payload["items"] if not item["back_translation"].strip()]
            if empty_items:
                log_path.write_text(
                    log_path.read_text(encoding="utf-8")
                    + f"\n\nVALIDATION ERROR\nempty back translations: {empty_items[:10]}\n",
                    encoding="utf-8",
                )
                continue

            for item in payload["items"]:
                results[item["id"]] = item["back_translation"].strip()
            success = True
            break

        if not success:
            raise RuntimeError(f"back-translation batch {idx} failed, see {log_path}")
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--translations", default="")
    parser.add_argument("--bbox", default="")
    parser.add_argument("--job-name", default="")
    parser.add_argument("--batch-chars", type=int, default=7000)
    parser.add_argument("--mode", choices=["all", "sample"], default="sample")
    parser.add_argument("--sample-size", type=int, default=80)
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--reasoning-effort", default="low")
    parser.add_argument("--retries", type=int, default=3)
    args = parser.parse_args()

    if args.job_name:
        job_dir = TMP_DIR / "jobs" / args.job_name
        translations_path = Path(args.translations) if args.translations else job_dir / "translations.json"
        bbox_path = Path(args.bbox) if args.bbox else job_dir / "source_bbox.html"
    else:
        job_dir = TMP_DIR
        translations_path = Path(args.translations) if args.translations else TMP_DIR / "translations.json"
        bbox_path = Path(args.bbox) if args.bbox else TMP_DIR / "source_bbox.html"

    source_pages_path = job_dir / "source_pages.json"
    if source_pages_path.exists():
        pages = json.loads(source_pages_path.read_text(encoding="utf-8"))
    else:
        pages = pipeline.parse_bbox(bbox_path)
    original_map = {
        block["id"]: block["text"]
        for page in pages
        for block in page
    }
    translations = json.loads(translations_path.read_text(encoding="utf-8"))
    items = choose_items_for_qa(original_map, translations, args.mode, args.sample_size)
    sampled_translations = {item["id"]: item["translation"] for item in items}
    backtranslations = run_backtranslation(
        items,
        args.batch_chars,
        job_dir,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        retries=args.retries,
    )
    report = build_report(original_map, sampled_translations, backtranslations)
    (job_dir / "backtranslate_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_markdown(report, job_dir / "backtranslate_report.md")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        sys.exit(1)
