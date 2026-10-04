#!/usr/bin/env python3

import argparse
import sys
from pathlib import Path

import pipeline


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", default=str(Path.cwd() / "DiveintoClaudeCode.pdf"))
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--batch-chars", type=int, default=10000)
    parser.add_argument("--pdf-output", default=str(Path.cwd() / "claudeCodeChinese.pdf"))
    parser.add_argument("--page-start", type=int, default=1)
    parser.add_argument("--page-end", type=int, default=0)
    parser.add_argument("--job-name", default="")
    parser.add_argument("--work-dir", default=str(pipeline.TMP_ROOT))
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--reasoning-effort", default="low")
    parser.add_argument("--force-ocr", action="store_true")
    parser.add_argument("--render-mode", choices=["vector", "raster"], default="vector")
    args = parser.parse_args()
    pipeline.set_work_dir(Path(args.work_dir))

    pipeline.translate_document(
        Path(args.pdf), Path(args.pdf_output),
        pipeline.DocumentOptions(
            dpi=args.dpi, batch_chars=args.batch_chars, page_start=args.page_start,
            page_end=args.page_end, model=args.model, reasoning_effort=args.reasoning_effort,
            force_ocr=args.force_ocr, render_mode=args.render_mode,
        ),
        job_name=args.job_name or None,
    )

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        sys.exit(1)
