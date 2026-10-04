#!/usr/bin/env python3

import argparse
import json
import os
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pipeline

TOOL_ROOT = Path(__file__).resolve().parent
TMP_DIR = Path(os.environ.get("TRANSLATE_PDF_WORK_DIR", TOOL_ROOT / "work"))
SUMMARY_JSON = TMP_DIR / "parallel_translation_summary.json"
SUMMARY_MD = TMP_DIR / "parallel_translation_summary.md"

def set_work_dir(work_dir: Path):
    global TMP_DIR, SUMMARY_JSON, SUMMARY_MD
    TMP_DIR = work_dir
    SUMMARY_JSON = TMP_DIR / "parallel_translation_summary.json"
    SUMMARY_MD = TMP_DIR / "parallel_translation_summary.md"
    pipeline.set_work_dir(work_dir)


def build_output_path(pdf_path: Path, output_dir: Path, suffix: str) -> Path:
    return output_dir / f"{pdf_path.stem}{suffix}.pdf"


def select_pdfs(source_dir: Path, includes: list[str], max_documents: int) -> list[Path]:
    pdf_paths = sorted(source_dir.glob("*.pdf"))
    if includes:
        include_set = set(includes)
        pdf_paths = [pdf for pdf in pdf_paths if pdf.name in include_set or pdf.stem in include_set]
    if max_documents:
        pdf_paths = pdf_paths[:max_documents]
    return pdf_paths


def normalize_summary_results(results: list[dict]) -> list[dict]:
    normalized = []
    for item in results:
        normalized_item = dict(item)
        qa_report = normalized_item.get("qa")
        if isinstance(qa_report, dict):
            qa_report = dict(qa_report)
            if "plan_artifact_paths" in qa_report:
                qa_report["plan_artifact_paths"] = pipeline.normalize_artifact_paths(qa_report["plan_artifact_paths"])
            if "visual_checked_pages" in qa_report:
                qa_report["visual_checked_pages"] = sorted(
                    {int(page_num) for page_num in qa_report["visual_checked_pages"]}
                )
            normalized_item["qa"] = qa_report
        normalized.append(normalized_item)
    return normalized


def write_summary(results: list[dict]):
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    results = normalize_summary_results(results)
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
            for artifact_path in item["qa"].get("plan_artifact_paths", []):
                lines.append(f"- plan_artifact: {artifact_path}")
            if "visual_issue_count" in item["qa"]:
                lines.append(f"- visual_issue_count: {item['qa']['visual_issue_count']}")
                lines.append(f"- visual_error_count: {item['qa'].get('visual_error_count', 0)}")
                lines.append(f"- visual_warning_count: {item['qa'].get('visual_warning_count', 0)}")
                lines.append(f"- visual_report_json: {item['qa'].get('visual_report_json', '')}")
                lines.append(f"- visual_report_md: {item['qa'].get('visual_report_md', '')}")
                checked_pages = item["qa"].get("visual_checked_pages", [])
                if checked_pages:
                    lines.append(
                        "- visual_checked_pages: "
                        + ", ".join(str(page_num) for page_num in checked_pages)
                    )
                if "visual_highest_severity" in item["qa"]:
                    lines.append(f"- visual_highest_severity: {item['qa']['visual_highest_severity']}")
                for issue in item["qa"].get("visual_highest_severity_issues", [])[:5]:
                    source_ids = ", ".join(issue.get("source_ids", []))
                    suffix = f" source_ids={source_ids}" if source_ids else ""
                    lines.append(
                        f"- visual_issue: page {int(issue.get('page_num', 0)):03d}"
                        f" [{issue.get('severity', '')}] {issue.get('category', '')}:"
                        f" {issue.get('message', '')}{suffix}"
                    )
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

    options = pipeline.DocumentOptions(
        dpi=args.dpi, page_start=args.page_start, page_end=args.page_end,
        force_ocr=args.force_ocr, refresh_source=args.refresh_source, render_mode=args.render_mode,
        batch_chars=args.batch_chars, page_workers=args.page_workers,
        retranslate=args.retranslate,
        model=args.model, reasoning_effort=args.reasoning_effort, retries=args.retries,
        overwrite=args.force, qa=args.qa, qa_mode=args.qa_mode, qa_sample_size=args.qa_sample_size,
        qa_batch_chars=args.qa_batch_chars, strict_qa=args.strict_qa,
    )
    results = []
    with ThreadPoolExecutor(max_workers=args.document_workers) as executor:
        future_map = {
            executor.submit(pipeline.translate_document, pdf_path,
                            build_output_path(pdf_path, output_dir, args.suffix), options): pdf_path
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
