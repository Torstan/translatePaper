# translatePaper

Translates English PDF papers to Chinese PDFs. It extracts text, uses Codex CLI, and renders Chinese back to pages. Supports batch jobs, resume, and QA.

## Scripts

- `translate_pdf_parallel.py`: batch entry.
- `translate_pdf_via_codex.py`: single-PDF pipeline.
- `backtranslate_check.py` / `qa_directory_backtranslation.py`: QA.

## Requirements

Python 3.10+, Codex CLI, Poppler, XeLaTeX, `Pillow` and a Chinese font. Default: `/usr/share/fonts/truetype/arphic/uming.ttc`.

## Usage

```bash
cd /mnt/d/ginobili/code/translatePaper
python3 translate_pdf_parallel.py --source-dir /path/to/pdfs --target-dir /path/to/output --document-workers 1 --page-workers 2 --continue-on-error
```

Output: `<original-name>-Chinese.pdf`.

Test two pages by adding `--include paper.pdf --page-start 1 --page-end 2 --no-qa`.

Common options: `--force`, `--retranslate`, `--refresh-source`, `--no-qa`.

Intermediate files: `work/jobs/<pdf-stem>/`. Summary: `work/parallel_translation_summary.md`.
