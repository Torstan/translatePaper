#!/usr/bin/env python3

import argparse
import subprocess
import sys
from pathlib import Path


TOOL_ROOT = Path(__file__).resolve().parent
SCRIPT = TOOL_ROOT / "translate_pdf_via_codex.py"


def build_output_path(pdf_path: Path, output_dir: Path, suffix: str) -> Path:
    return output_dir / f"{pdf_path.stem}{suffix}.pdf"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--target-dir", required=True)
    parser.add_argument("--suffix", default="-Chinese")
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--batch-chars", type=int, default=7000)
    parser.add_argument("--include", action="append", default=[])
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--reasoning-effort", default="low")
    args = parser.parse_args()

    source_dir = Path(args.source_dir)
    output_dir = Path(args.target_dir)
    pdf_paths = sorted(source_dir.glob("*.pdf"))
    if args.include:
        includes = set(args.include)
        pdf_paths = [pdf for pdf in pdf_paths if pdf.name in includes or pdf.stem in includes]
    if not pdf_paths:
        raise RuntimeError(f"no pdf files found in {source_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    for pdf_path in pdf_paths:
        output_path = build_output_path(pdf_path, output_dir, args.suffix)
        if output_path.exists() and not args.force:
            continue
        cmd = [
            sys.executable,
            str(SCRIPT),
            "--pdf",
            str(pdf_path),
            "--pdf-output",
            str(output_path),
            "--job-name",
            pdf_path.stem,
            "--dpi",
            str(args.dpi),
            "--batch-chars",
            str(args.batch_chars),
            "--model",
            args.model,
            "--reasoning-effort",
            args.reasoning_effort,
        ]
        proc = subprocess.run(cmd, cwd=TOOL_ROOT, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"translation failed for {pdf_path}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        sys.exit(1)
