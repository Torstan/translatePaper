#!/usr/bin/env python3

import argparse
import json
import sys
from pathlib import Path

from qa_semantic import (
    build_report,
    choose_items_for_qa,
    classify_block,
    make_prompt,
    make_schema,
    normalize_english,
    run_backtranslation,
    write_markdown,
)


TOOL_ROOT = Path(__file__).resolve().parent
TMP_DIR = TOOL_ROOT / "work"


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
        import translate_pdf_via_codex as pipeline

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
