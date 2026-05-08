#!/usr/bin/env python3

import argparse
import json
import os
import shutil
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path


TOOL_ROOT = Path(__file__).resolve().parent
TMP_DIR = Path(os.environ.get("TRANSLATE_PDF_WORK_DIR", TOOL_ROOT / "work"))
SUMMARY_JSON = TMP_DIR / "parallel_translation_summary.json"
SUMMARY_MD = TMP_DIR / "parallel_translation_summary.md"

sys.path.insert(0, str(TOOL_ROOT))
import backtranslate_check as qa  # noqa: E402
import translate_pdf_via_codex as pipeline  # noqa: E402


def set_work_dir(work_dir: Path):
    global TMP_DIR, SUMMARY_JSON, SUMMARY_MD
    TMP_DIR = work_dir
    SUMMARY_JSON = TMP_DIR / "parallel_translation_summary.json"
    SUMMARY_MD = TMP_DIR / "parallel_translation_summary.md"
    pipeline.set_work_dir(work_dir)


@dataclass(frozen=True)
class PageBatch:
    page_num: int
    chunk_idx: int
    items: list[dict]


def build_output_path(pdf_path: Path, output_dir: Path, suffix: str) -> Path:
    return output_dir / f"{pdf_path.stem}{suffix}.pdf"


def atomic_write_json(path: Path, payload):
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def backup_if_exists(path: Path, label: str):
    if not path.exists():
        return
    backup = path.with_name(f"{path.stem}.{label}-{int(time.time())}{path.suffix}")
    shutil.copy2(path, backup)


def load_cached_translations(path: Path, valid_ids: set[str], *, retranslate: bool) -> dict[str, str]:
    if retranslate or not path.exists():
        if retranslate:
            backup_if_exists(path, "backup")
        return {}

    existing = json.loads(path.read_text(encoding="utf-8"))
    overlap = len(valid_ids & set(existing))
    if valid_ids and overlap == 0:
        backup_if_exists(path, "stale")
        return {}
    return {key: value for key, value in existing.items() if key in valid_ids}


def build_page_batches(selected_pages, max_chars: int) -> list[PageBatch]:
    batches = []
    for page_num, page_blocks in selected_pages:
        visual_regions = pipeline.build_visual_regions(page_blocks)
        classes = pipeline.classify_blocks(page_blocks, visual_regions)
        current = []
        current_chars = 0
        chunk_idx = 1
        for block in page_blocks:
            if classes.get(block["id"]) not in {"body", "heading", "title"}:
                continue
            if pipeline.should_preserve_as_image(block):
                continue
            if pipeline.is_trivial_keep(block["text"]):
                continue
            item = {"id": block["id"], "text": block["text"]}
            item_chars = len(block["text"])
            if current and current_chars + item_chars > max_chars:
                batches.append(PageBatch(page_num=page_num, chunk_idx=chunk_idx, items=current))
                chunk_idx += 1
                current = []
                current_chars = 0
            current.append(item)
            current_chars += item_chars
        if current:
            batches.append(PageBatch(page_num=page_num, chunk_idx=chunk_idx, items=current))
    return batches


def write_deterministic_quality_report(issues: list[str], job_dir: Path):
    (job_dir / "deterministic_quality_report.json").write_text(
        json.dumps({"issue_count": len(issues), "issues": issues}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = ["# Deterministic PDF Quality Report", "", f"- issue_count: {len(issues)}", ""]
    for issue in issues[:100]:
        lines.append(f"- {issue}")
    (job_dir / "deterministic_quality_report.md").write_text("\n".join(lines), encoding="utf-8")


def validate_translation_payload(payload, expected_ids: set[str]):
    items = payload["items"]
    seen = {item["id"] for item in items}
    if seen != expected_ids:
        missing = sorted(expected_ids - seen)
        extra = sorted(seen - expected_ids)
        raise ValueError(f"mismatched ids, missing={missing[:5]}, extra={extra[:5]}")
    empty_items = [item["id"] for item in items if not item["translation"].strip()]
    if empty_items:
        raise ValueError(f"empty translations: {empty_items[:10]}")
    return {
        item["id"]: pipeline.normalize_translation(item["translation"])
        for item in items
    }


def run_translation_batch(batch: PageBatch, job_paths, args) -> dict[str, str]:
    schema_path = job_paths["schema_path"]
    prompt = pipeline.make_prompt(batch.items)
    prefix = f"page-{batch.page_num:03d}-chunk-{batch.chunk_idx:02d}"
    prompt_path = job_paths["job_dir"] / f"{prefix}.prompt.txt"
    out_path = job_paths["job_dir"] / f"{prefix}.out.json"
    log_path = job_paths["job_dir"] / f"{prefix}.log.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    expected_ids = {item["id"] for item in batch.items}

    for attempt in range(1, args.retries + 1):
        proc = pipeline.run(
            [
                "codex",
                "exec",
                "--skip-git-repo-check",
                "-m",
                args.model,
                "-c",
                f"model_reasoning_effort='{args.reasoning_effort}'",
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
            time.sleep(3 * attempt)
            continue
        try:
            payload = json.loads(out_path.read_text(encoding="utf-8"))
            return validate_translation_payload(payload, expected_ids)
        except Exception as exc:  # noqa: BLE001
            log_path.write_text(
                log_path.read_text(encoding="utf-8") + f"\n\nVALIDATION ERROR\n{exc}\n",
                encoding="utf-8",
            )
            time.sleep(3 * attempt)

    raise RuntimeError(f"translation failed for {prefix}, see {log_path}")


def run_parallel_translation(batches: list[PageBatch], translations: dict[str, str], job_paths, args):
    valid_ids = {item["id"] for batch in batches for item in batch.items}
    todo = []
    for batch in batches:
        missing_items = [item for item in batch.items if item["id"] not in translations]
        if missing_items:
            todo.append(PageBatch(batch.page_num, batch.chunk_idx, missing_items))

    if not todo:
        return translations

    pipeline.write_schema(job_paths["schema_path"])
    with ThreadPoolExecutor(max_workers=args.page_workers) as executor:
        futures = [
            executor.submit(run_translation_batch, batch, job_paths, args)
            for batch in todo
        ]
        for future in as_completed(futures):
            translations.update(future.result())
            atomic_write_json(job_paths["translations_path"], translations)

    missing = valid_ids - set(translations)
    if missing:
        raise RuntimeError(f"missing translations after parallel run: {sorted(missing)[:10]}")
    return translations


def run_qa_for_job(selected_pages, translations: dict[str, str], job_paths, page_size, args) -> dict:
    pages = [page for _, page in selected_pages]
    original_map = {
        block["id"]: block["text"]
        for page in pages
        for block in page
    }
    deterministic_issues = pipeline.validate_document_quality(
        selected_pages,
        translations,
        page_size,
        job_paths=job_paths,
    )
    write_deterministic_quality_report(deterministic_issues, job_paths["job_dir"])
    if deterministic_issues and args.strict_qa:
        raise RuntimeError(
            f"deterministic QA found {len(deterministic_issues)} issue(s); "
            f"see {job_paths['job_dir'] / 'deterministic_quality_report.md'}"
        )

    items = qa.choose_items_for_qa(original_map, translations, args.qa_mode, args.qa_sample_size)
    sampled_translations = {item["id"]: item["translation"] for item in items}
    backtranslations = qa.run_backtranslation(
        items,
        args.qa_batch_chars,
        job_paths["job_dir"],
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        retries=args.retries,
    )
    report = qa.build_report(original_map, sampled_translations, backtranslations)
    report_path = job_paths["job_dir"] / "backtranslate_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    qa.write_markdown(report, job_paths["job_dir"] / "backtranslate_report.md")
    return {
        "deterministic_issue_count": len(deterministic_issues),
        "deterministic_issues": deterministic_issues[:10],
        "checked_blocks": len(report),
        "worst_score": report[0]["score"] if report else None,
        "worst_items": report[:5],
    }


def translate_one_pdf(pdf_path: Path, output_dir: Path, args) -> dict:
    output_path = build_output_path(pdf_path, output_dir, args.suffix)
    if output_path.exists() and not args.force:
        return {
            "pdf": str(pdf_path),
            "output": str(output_path),
            "status": "skipped",
            "reason": "output exists; use --force to regenerate",
        }

    job_paths = pipeline.build_job_paths(pdf_path, pdf_path.stem)
    job_paths["job_dir"].mkdir(parents=True, exist_ok=True)
    if args.refresh_source:
        backup_if_exists(job_paths["source_pages_path"], "source-backup")
        job_paths["source_pages_path"].unlink(missing_ok=True)

    pdf_size_pt = pipeline.get_pdf_page_size(pdf_path)
    pages = pipeline.load_or_build_source_pages(
        pdf_path,
        args.dpi,
        job_paths,
        pdf_size_pt,
        args.page_start,
        args.page_end,
        force_ocr=args.force_ocr,
    )
    page_end = args.page_end or len(pages)
    selected_pages = [
        (idx, page)
        for idx, page in enumerate(pages, start=1)
        if args.page_start <= idx <= page_end
    ]
    if not selected_pages:
        raise RuntimeError(f"no pages selected for {pdf_path}")

    batches = build_page_batches(selected_pages, args.batch_chars)
    valid_ids = {item["id"] for batch in batches for item in batch.items}
    translations = load_cached_translations(
        job_paths["translations_path"],
        valid_ids,
        retranslate=args.retranslate,
    )
    translations = run_parallel_translation(batches, translations, job_paths, args)

    output_dir.mkdir(parents=True, exist_ok=True)
    if args.render_mode == "raster":
        pipeline.render_pages(selected_pages, translations, args.dpi, job_paths)
        pipeline.write_latex(
            output_path,
            [idx for idx, _ in selected_pages],
            pdf_size_pt,
            job_paths,
        )
    else:
        pipeline.write_vector_pdf(
            pdf_path,
            output_path,
            selected_pages,
            translations,
            pdf_size_pt,
            args.dpi,
            job_paths=job_paths,
        )

    result = {
        "pdf": str(pdf_path),
        "output": str(output_path),
        "status": "translated",
        "job_slug": job_paths["job_slug"],
        "pages": len(selected_pages),
        "source_blocks": sum(len(page) for _, page in selected_pages),
        "translated_blocks": len(valid_ids),
    }
    if args.qa:
        result["qa"] = run_qa_for_job(
            selected_pages,
            translations,
            job_paths,
            pdf_size_pt,
            args,
        )
    return result


def select_pdfs(source_dir: Path, includes: list[str], max_documents: int) -> list[Path]:
    pdf_paths = sorted(source_dir.glob("*.pdf"))
    if includes:
        include_set = set(includes)
        pdf_paths = [pdf for pdf in pdf_paths if pdf.name in include_set or pdf.stem in include_set]
    if max_documents:
        pdf_paths = pdf_paths[:max_documents]
    return pdf_paths


def write_summary(results: list[dict]):
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    SUMMARY_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Parallel PDF Translation Summary", ""]
    for item in results:
        lines.append(f"## {Path(item['pdf']).name}")
        lines.append("")
        lines.append(f"- status: {item['status']}")
        if "output" in item:
            lines.append(f"- output: {item['output']}")
        if item.get("status") == "failed":
            lines.append(f"- error: {item.get('error', '')}")
        if item.get("qa"):
            lines.append(f"- deterministic_issue_count: {item['qa'].get('deterministic_issue_count', 0)}")
            for issue in item["qa"].get("deterministic_issues", [])[:5]:
                lines.append(f"- deterministic_issue: {issue}")
            lines.append(f"- checked_blocks: {item['qa']['checked_blocks']}")
            lines.append(f"- worst_score: {item['qa']['worst_score']}")
            for worst in item["qa"]["worst_items"]:
                lines.append(f"- {worst['id']} score={worst['score']}")
        lines.append("")
    SUMMARY_MD.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(
        description="Translate PDFs to Chinese with document-level and page-level concurrency, then run back-translation QA."
    )
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--target-dir", required=True)
    parser.add_argument("--work-dir", default=str(TMP_DIR))
    parser.add_argument("--include", action="append", default=[])
    parser.add_argument("--suffix", default="-Chinese")
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--batch-chars", type=int, default=7000)
    parser.add_argument("--document-workers", type=int, default=1)
    parser.add_argument("--page-workers", type=int, default=2)
    parser.add_argument("--page-start", type=int, default=1)
    parser.add_argument("--page-end", type=int, default=0)
    parser.add_argument("--max-documents", type=int, default=0)
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--reasoning-effort", default="low")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--retranslate", action="store_true")
    parser.add_argument("--refresh-source", action="store_true")
    parser.add_argument("--force-ocr", action="store_true")
    parser.add_argument("--render-mode", choices=["vector", "raster"], default="vector")
    parser.add_argument("--no-qa", dest="qa", action="store_false")
    parser.add_argument("--qa-mode", choices=["all", "sample"], default="sample")
    parser.add_argument("--qa-sample-size", type=int, default=100)
    parser.add_argument("--qa-batch-chars", type=int, default=7000)
    parser.add_argument("--strict-qa", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.set_defaults(qa=True)
    args = parser.parse_args()
    set_work_dir(Path(args.work_dir))

    source_dir = Path(args.source_dir)
    output_dir = Path(args.target_dir)
    pdf_paths = select_pdfs(source_dir, args.include, args.max_documents)
    if not pdf_paths:
        raise RuntimeError(f"no PDF files found in {source_dir}")

    results = []
    with ThreadPoolExecutor(max_workers=args.document_workers) as executor:
        future_map = {
            executor.submit(translate_one_pdf, pdf_path, output_dir, args): pdf_path
            for pdf_path in pdf_paths
        }
        for future in as_completed(future_map):
            pdf_path = future_map[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001
                if not args.continue_on_error:
                    raise
                result = {
                    "pdf": str(pdf_path),
                    "status": "failed",
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                }
            results.append(result)
            write_summary(sorted(results, key=lambda item: item["pdf"]))

    final_results = sorted(results, key=lambda item: item["pdf"])
    write_summary(final_results)
    failed = [item for item in final_results if item["status"] == "failed"]
    if failed:
        raise RuntimeError(f"{len(failed)} PDF(s) failed; see {SUMMARY_JSON}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        sys.exit(1)
