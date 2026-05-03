#!/usr/bin/env python3

import argparse
import json
import subprocess
import sys
from pathlib import Path


TOOL_ROOT = Path(__file__).resolve().parent
TMP_DIR = TOOL_ROOT / "work"
BACKTRANSLATE_SCRIPT = TOOL_ROOT / "backtranslate_check.py"

sys.path.insert(0, str(TOOL_ROOT))
import translate_pdf_via_codex as pipeline  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--target-dir", required=True)
    parser.add_argument("--mode", choices=["all", "sample"], default="sample")
    parser.add_argument("--sample-size", type=int, default=80)
    parser.add_argument("--batch-chars", type=int, default=7000)
    parser.add_argument("--include", action="append", default=[])
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--reasoning-effort", default="low")
    args = parser.parse_args()

    source_dir = Path(args.source_dir)
    target_dir = Path(args.target_dir)
    pdf_paths = sorted(source_dir.glob("*.pdf"))
    if args.include:
        includes = set(args.include)
        pdf_paths = [pdf for pdf in pdf_paths if pdf.name in includes or pdf.stem in includes]
    if not pdf_paths:
        raise RuntimeError(f"no pdf files found in {source_dir}")

    summary = []
    for pdf_path in pdf_paths:
        job = pipeline.build_job_paths(pdf_path, pdf_path.stem)
        translations_path = job["translations_path"]
        output_pdf = target_dir / f"{pdf_path.stem}-Chinese.pdf"
        if not translations_path.exists() or not output_pdf.exists():
            continue

        cmd = [
            sys.executable,
            str(BACKTRANSLATE_SCRIPT),
            "--job-name",
            job["job_slug"],
            "--mode",
            args.mode,
            "--sample-size",
            str(args.sample_size),
            "--batch-chars",
            str(args.batch_chars),
            "--model",
            args.model,
            "--reasoning-effort",
            args.reasoning_effort,
        ]
        proc = subprocess.run(cmd, cwd=TOOL_ROOT, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"back-translation QA failed for {pdf_path}")

        report_path = job["job_dir"] / "backtranslate_report.json"
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else []
        summary.append(
            {
                "pdf": pdf_path.name,
                "job_slug": job["job_slug"],
                "checked_blocks": len(report),
                "worst_score": report[0]["score"] if report else None,
                "worst_items": report[:5],
            }
        )

    TMP_DIR.mkdir(parents=True, exist_ok=True)
    summary_path = TMP_DIR / "qa_summary.json"
    summary_md_path = TMP_DIR / "qa_summary.md"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# Back-Translation QA Summary", ""]
    for item in summary:
        lines.append(f"## {item['pdf']}")
        lines.append("")
        lines.append(f"- checked_blocks: {item['checked_blocks']}")
        lines.append(f"- worst_score: {item['worst_score']}")
        for worst in item["worst_items"]:
            lines.append(f"- {worst['id']} score={worst['score']}")
        lines.append("")
    summary_md_path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        sys.exit(1)
