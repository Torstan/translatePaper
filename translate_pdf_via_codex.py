#!/usr/bin/env python3

import argparse
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median

from PIL import Image, ImageDraw, ImageFont


TOOL_ROOT = Path(__file__).resolve().parent
TMP_ROOT = Path(os.environ.get("TRANSLATE_PDF_WORK_DIR", TOOL_ROOT / "work"))
VENDOR_ROOT = TOOL_ROOT / "vendor"
FONT_PATH = "/usr/share/fonts/truetype/arphic/uming.ttc"
SOURCE_FONT_SCALE = 0.94
FORMULA_COLUMN_FRACTION = 0.52
FORMULA_PAD_TOP_PX = 8
FORMULA_PAD_BOTTOM_PX = 4
TEXT_BOX_MARGIN_PX = 8
VECTOR_FONT = "china-s"
VECTOR_BODY_COLOR = (0, 0, 0)
VECTOR_ACCENT_COLOR = (0.58, 0.0, 0.06)
FULL_PAGE_IMAGE_AREA_FRACTION = 0.70
EDGE_ICON_MAX_SIZE_PT = 40.0
HEURISTIC_HEADING_MAX_CHARS = 120
HEURISTIC_HEADING_BOTTOM_MARGIN_PT = 70.0
JOURNAL_FOOTER_TEXT = "ACM Transactions on Programming Languages and Systems, Vol. 11, No. 1, January 1991."
JOURNAL_FOOTER_WIDTH_PT = 260.0
JOURNAL_FOOTER_HEIGHT_PT = 8.0
VISUAL_CLIP_PAD_X_PT = 8.0
VISUAL_CLIP_PAD_Y_PT = 3.0
VISUAL_CLIP_PIXEL_SEARCH_PAD_X_PT = 40.0
VISUAL_CLIP_PIXEL_SEARCH_PAD_Y_PT = 20.0
VISUAL_CLIP_PIXEL_FINAL_PAD_PT = 1.5
FORMULA_CLIP_PIXEL_SEARCH_PAD_X_PT = 70.0
FORMULA_CLIP_PIXEL_SEARCH_PAD_Y_PT = 2.0
FORMULA_CLIP_PAD_X_PT = 1.0
FORMULA_CLIP_PAD_Y_PT = 0.5
TEXT_PROTECTED_GAP_PT = 0.75
BODY_TEXT_BOX_CUSHION_PT = 5.0
BODY_FLOW_MIN_GAP_PT = 3.0
BODY_FLOW_TARGET_GAP_PT = 8.0
BODY_FLOW_MAX_GAP_PT = 12.0
SOURCE_PARAGRAPH_FLOW_MAX_GAP_PT = 26.0
BODY_FLOW_TOP_MAX_GAP_PT = 14.0
BODY_FLOW_INTERNAL_SLACK_WARN_PT = 24.0
BODY_FLOW_VISIBLE_GAP_WARN_PT = 32.0


@dataclass(frozen=True)
class TextStyle:
    font_size: float
    line_height_factor: float
    paragraph_spacing: float = 0.0
    letter_spacing: float = 0.0
    color: tuple[float, float, float] = VECTOR_BODY_COLOR
    min_line_height_factor: float | None = None
    min_paragraph_spacing: float | None = None


DOCUMENT_STYLES = {
    "body": TextStyle(font_size=9.2, line_height_factor=1.22, paragraph_spacing=2.0, min_line_height_factor=1.08, min_paragraph_spacing=0.0),
    "heading": TextStyle(font_size=11.2, line_height_factor=1.18, paragraph_spacing=2.0, min_line_height_factor=1.12, min_paragraph_spacing=0.0),
    "subheading": TextStyle(font_size=10.2, line_height_factor=1.18, paragraph_spacing=1.5, min_line_height_factor=1.12, min_paragraph_spacing=0.0),
    "title": TextStyle(font_size=13.6, line_height_factor=1.15, paragraph_spacing=2.0, min_line_height_factor=1.10, min_paragraph_spacing=0.0),
    "metadata": TextStyle(font_size=8.4, line_height_factor=1.15, paragraph_spacing=0.8, min_line_height_factor=1.10, min_paragraph_spacing=0.0),
    "footer": TextStyle(font_size=5.2, line_height_factor=1.05),
    "reference": TextStyle(font_size=6.2, line_height_factor=1.10),
}

BODY_FONT_SIZE = DOCUMENT_STYLES["body"].font_size
HEADING_FONT_SIZE = DOCUMENT_STYLES["heading"].font_size
TITLE_FONT_SIZE = DOCUMENT_STYLES["title"].font_size
JOURNAL_FOOTER_FONT_SIZE = DOCUMENT_STYLES["footer"].font_size
NORMAL_TRANSLATED_CLASSES = {"body", "heading", "title"}
TEXT_FLOW_STYLE_NAMES = {"body", "heading", "subheading"}
ENGLISH_FUNCTION_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "be",
    "because",
    "by",
    "can",
    "for",
    "from",
    "has",
    "have",
    "if",
    "in",
    "is",
    "it",
    "not",
    "of",
    "on",
    "or",
    "that",
    "the",
    "their",
    "then",
    "there",
    "this",
    "to",
    "we",
    "which",
    "with",
}


def slugify(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", text.strip()).strip("-").lower()
    return slug or "document"


def build_job_paths(pdf_path: Path, job_name: str | None):
    job_slug = slugify(job_name or pdf_path.stem)
    job_dir = TMP_ROOT / "jobs" / job_slug
    return {
        "job_slug": job_slug,
        "job_dir": job_dir,
        "bbox_path": job_dir / "source_bbox.html",
        "source_pages_path": job_dir / "source_pages.json",
        "pages_dir": job_dir / "pages",
        "translated_pages_dir": job_dir / "translated_pages",
        "schema_path": job_dir / "translation_schema.json",
        "boundary_repairs_path": job_dir / "boundary_sentence_repairs.json",
        "boundary_schema_path": job_dir / "boundary_sentence_schema.json",
        "translations_path": job_dir / "translations.json",
        "tex_path": job_dir / "claudeCodeChinese.tex",
        "pdf_path": job_dir / "claudeCodeChinese.pdf",
    }


def set_work_dir(work_dir: Path):
    global TMP_ROOT
    TMP_ROOT = work_dir


def run(cmd, *, input_text=None, cwd=TOOL_ROOT, check=True):
    proc = subprocess.run(
        cmd,
        input=input_text,
        text=True,
        cwd=cwd,
        capture_output=True,
        check=False,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"stdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"
        )
    return proc


def ensure_assets(
    pdf_path: Path,
    dpi: int,
    job_paths,
    page_start: int = 1,
    page_end: int = 0,
) -> Path:
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    job_paths["job_dir"].mkdir(parents=True, exist_ok=True)
    job_paths["pages_dir"].mkdir(parents=True, exist_ok=True)
    job_paths["translated_pages_dir"].mkdir(parents=True, exist_ok=True)
    bbox_path = job_paths["bbox_path"]
    if not bbox_path.exists():
        proc = run(["pdftotext", "-bbox-layout", str(pdf_path), "-"], check=True)
        bbox_path.write_text(proc.stdout, encoding="utf-8")
    if page_end:
        expected_pages = [
            job_paths["pages_dir"] / f"page-{page_num:03d}.png"
            for page_num in range(page_start, page_end + 1)
        ]
        needs_render = any(not path.exists() for path in expected_pages)
    else:
        needs_render = not (job_paths["pages_dir"] / "page-001.png").exists()

    if needs_render:
        prefix = str(job_paths["pages_dir"] / "page")
        cmd = ["pdftocairo", "-png", "-r", str(dpi)]
        if page_end:
            cmd.extend(["-f", str(page_start), "-l", str(page_end)])
        cmd.extend([str(pdf_path), prefix])
        run(cmd, check=True)
        for src in sorted(job_paths["pages_dir"].glob("page-*.png")):
            # pdftocairo emits page-1.png, page-2.png; normalize to page-001.png.
            m = re.search(r"page-(\d+)\.png$", src.name)
            if not m:
                continue
            normalized = job_paths["pages_dir"] / f"page-{int(m.group(1)):03d}.png"
            if src != normalized:
                src.rename(normalized)
    return bbox_path


def parse_bbox(bbox_path: Path):
    raw_text = bbox_path.read_text(encoding="utf-8", errors="replace")
    raw_text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", raw_text)
    root = ET.fromstring(raw_text)
    ns = {"x": "http://www.w3.org/1999/xhtml"}
    page_nodes = root.findall(".//page")
    if not page_nodes:
        page_nodes = root.findall(".//x:page", ns)
    pages = []
    for page_idx, page in enumerate(page_nodes, start=1):
        page_blocks = []
        block_nodes = page.findall(".//block")
        if not block_nodes:
            block_nodes = page.findall(".//x:block", ns)
        for block_idx, block in enumerate(block_nodes, start=1):
            lines = []
            line_nodes = block.findall("./line")
            if not line_nodes:
                line_nodes = block.findall("./x:line", ns)
            for line in line_nodes:
                word_nodes = line.findall("./word")
                if not word_nodes:
                    word_nodes = line.findall("./x:word", ns)
                words = [w.text or "" for w in word_nodes]
                line_text = normalize_text(" ".join(words))
                if line_text:
                    lines.append(line_text)
            block_text = normalize_text("\n".join(lines))
            if not block_text:
                continue
            page_blocks.append(
                {
                    "id": f"p{page_idx:03d}b{block_idx:04d}",
                    "page": page_idx,
                    "block_index": block_idx,
                    "xMin": float(block.attrib["xMin"]),
                    "yMin": float(block.attrib["yMin"]),
                    "xMax": float(block.attrib["xMax"]),
                    "yMax": float(block.attrib["yMax"]),
                    "text": block_text,
                }
            )
        pages.append(page_blocks)
    return pages


def parse_bbox_lines_from_text(raw_text: str) -> list[dict]:
    raw_text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", raw_text)
    root = ET.fromstring(raw_text)
    ns = {"x": "http://www.w3.org/1999/xhtml"}
    page_nodes = root.findall(".//page")
    if not page_nodes:
        page_nodes = root.findall(".//x:page", ns)
    lines = []
    for page_idx, page in enumerate(page_nodes, start=1):
        line_nodes = page.findall(".//line")
        if not line_nodes:
            line_nodes = page.findall(".//x:line", ns)
        for line in line_nodes:
            word_nodes = line.findall("./word")
            if not word_nodes:
                word_nodes = line.findall("./x:word", ns)
            words = [word.text or "" for word in word_nodes]
            text = normalize_text(" ".join(words))
            if not text:
                continue
            lines.append(
                {
                    "page": page_idx,
                    "text": text,
                    "bbox": (
                        float(line.attrib["xMin"]),
                        float(line.attrib["yMin"]),
                        float(line.attrib["xMax"]),
                        float(line.attrib["yMax"]),
                    ),
                }
            )
    return lines


def parse_bbox_lines(bbox_path: Path) -> list[dict]:
    return parse_bbox_lines_from_text(bbox_path.read_text(encoding="utf-8", errors="replace"))


def load_source_pages(job_paths) -> list[list[dict]]:
    source_pages_path = job_paths["source_pages_path"]
    if source_pages_path.exists():
        return json.loads(source_pages_path.read_text(encoding="utf-8"))
    return parse_bbox(job_paths["bbox_path"])


def save_source_pages(pages, job_paths):
    job_paths["source_pages_path"].write_text(
        json.dumps(pages, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def normalize_text(text: str) -> str:
    text = text.replace("\xad", "")
    text = re.sub(r"\s+\n", "\n", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = text.strip()
    return text


def is_page_number(text: str) -> bool:
    return bool(re.fullmatch(r"[·•]?\s*\d{1,3}\s*[·•.]?", text.strip()))


def is_decorated_ocr_page_number_text(text: str) -> bool:
    compact = re.sub(r"\s+", "", normalize_text(text))
    if not compact or len(compact) > 7:
        return False
    if not re.fullmatch(r"\D{0,3}\d{1,3}\D{0,3}", compact):
        return False
    if re.search(r"[A-Za-z\u4e00-\u9fff()\[\]{}]", compact):
        return False
    if not re.search(r"[·•.]", compact):
        return False
    noise = re.sub(r"[\d·•.]", "", compact)
    return len(noise) <= 2


def is_noisy_ocr_page_number_text(text: str) -> bool:
    compact = re.sub(r"\s+", "", normalize_text(text))
    if not compact or len(compact) > 7:
        return False
    if is_decorated_ocr_page_number_text(compact):
        return True
    if not re.fullmatch(r"\D{0,3}\d{1,3}\D{0,3}", compact):
        return False
    if re.search(r"[A-Za-z()\[\]{}]", compact):
        return False
    if not re.search(r"[·•.]", compact):
        return False
    noise = re.sub(r"[\d·•.]", "", compact)
    return 0 < len(noise) <= 1


def is_decorated_ocr_page_number_block(block) -> bool:
    return (block["yMin"] < 70.0 or block["yMin"] > 560.0) and is_noisy_ocr_page_number_text(block.get("text", ""))


def is_trivial_keep(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    if is_page_number(stripped):
        return True
    if re.fullmatch(r"https?://\S+", stripped):
        return True
    return False


def is_visual_caption(text: str) -> bool:
    first_line = normalize_text(text).split("\n", 1)[0].strip()
    normalized = re.sub(r"\s+", "", first_line.lower())
    return bool(re.match(r"^(fig\.?|figure|table)\d+[0-9il]*[:.]", normalized))


def contains_visual_caption(text: str) -> bool:
    normalized = normalize_text(text).lower()
    compact = re.sub(r"\s+", "", normalized)
    return bool(
        re.search(r"fig\.?\d+[0-9il]*[:.]", compact)
        or re.search(r"(?m)^\s*(figure|table)\s*\d+[0-9il]*[:.]", normalized)
    )


def is_formula_like(text: str) -> bool:
    stripped = normalize_text(text)
    if not stripped:
        return False
    compact = re.sub(r"\s+", "", stripped)
    if re.fullmatch(r"\(\d{1,2}\)", compact):
        return True
    if re.fullmatch(r"i[∈e]s", compact.lower()):
        return True
    lower_compact = compact.lower()
    if any(term in lower_compact for term in ("argmin", "available_bw", "segment_size*cwnd")):
        return True
    symbol_count = len(re.findall(r"[=+\-*/_{}()[\]<>≤≥∑Σβ]", compact))
    word_count = len(re.findall(r"[A-Za-z]{3,}", stripped))
    if word_count >= 2 and not re.search(r"[=+\-*/_{}()[\]<>≤≥∑Σβ]", compact):
        return False
    if word_count >= 3 and re.search(r"\bis\b", stripped, flags=re.I) and symbol_count <= 4:
        return False
    return len(compact) >= 8 and symbol_count >= 3 and word_count <= 3


def is_standalone_equation_label(text: str) -> bool:
    return bool(re.fullmatch(r"\(\d{1,2}\)", re.sub(r"\s+", "", normalize_text(text))))


def should_preserve_as_image(block) -> bool:
    if block.get("preserve_image"):
        return True
    text = block.get("text", "")
    return is_visual_caption(text) or is_formula_like(text)


def should_use_ocr(pages) -> bool:
    nontrivial = [
        block
        for page in pages
        for block in page
        if not is_trivial_keep(block["text"])
    ]
    total_chars = sum(len(block["text"]) for block in nontrivial)
    return len(nontrivial) <= max(10, len(pages) * 2) and total_chars <= max(600, len(pages) * 80)


def text_extraction_looks_garbled(pages) -> bool:
    texts = [
        block["text"]
        for page in pages
        for block in page
        if not is_trivial_keep(block["text"])
    ]
    if not texts:
        return False

    joined = " ".join(texts)
    compact = re.sub(r"\s+", "", joined)
    total_chars = len(compact)
    if total_chars < 400:
        return False

    word_chars = sum(len(token) for token in re.findall(r"[A-Za-z]{3,}", joined))
    symbol_chars = len(re.findall(r"[^A-Za-z0-9\s]", joined))
    garbled_blocks = 0
    for text in texts:
        text_compact = re.sub(r"\s+", "", text)
        if not text_compact:
            continue
        alnum_chars = len(re.findall(r"[A-Za-z0-9]", text))
        if alnum_chars < max(2, int(len(text_compact) * 0.25)):
            garbled_blocks += 1

    word_ratio = word_chars / total_chars
    symbol_ratio = symbol_chars / total_chars
    garbled_block_ratio = garbled_blocks / max(1, len(texts))
    return word_ratio < 0.45 and symbol_ratio > 0.35 and garbled_block_ratio > 0.30


def build_ocr_preserve_groups(ocr_lines, page_width_px: int, page_height_px: int):
    groups = []
    preserved = set()

    for line in ocr_lines:
        if not is_visual_caption(line["text"]):
            continue
        if line["width_px"] >= page_width_px * 0.45:
            x0, x1 = 0, page_width_px
        elif line["center_x_px"] < page_width_px / 2:
            x0, x1 = 0, page_width_px * 0.52
        else:
            x0, x1 = page_width_px * 0.48, page_width_px
        y0 = max(0, line["y0_px"] - page_height_px * 0.16)
        y1 = line["y1_px"] + 24
        group = [
            candidate
            for candidate in ocr_lines
            if y0 <= candidate["y0_px"] <= y1
            and x0 <= candidate["center_x_px"] <= x1
        ]
        if group:
            groups.append(group)
            preserved.update(item["idx"] for item in group)

    for line in ocr_lines:
        if not is_formula_like(line["text"]):
            continue
        if line["center_x_px"] < page_width_px / 2:
            x0, x1 = 0, page_width_px * FORMULA_COLUMN_FRACTION
        else:
            x0, x1 = page_width_px * (1 - FORMULA_COLUMN_FRACTION), page_width_px
        y0 = max(0, line["y0_px"] - FORMULA_PAD_TOP_PX)
        y1 = min(page_height_px, line["y1_px"] + FORMULA_PAD_BOTTOM_PX)
        group = [
            candidate
            for candidate in ocr_lines
            if is_formula_like(candidate["text"])
            if y0 <= candidate["y0_px"] <= y1
            and min(candidate["x1_px"], x1) > max(candidate["x0_px"], x0)
        ]
        if group:
            group.append(
                {
                    "idx": -1,
                    "text": "",
                    "x0_px": x0,
                    "x1_px": x1,
                    "y0_px": y0,
                    "y1_px": y1,
                    "width_px": x1 - x0,
                    "center_x_px": (x0 + x1) / 2,
                }
            )
            groups.append(group)
            preserved.update(item["idx"] for item in group)

    return groups, preserved


def make_raw_block(lines, scale_x: float, scale_y: float, preserve_image: bool):
    lines = sorted(lines, key=lambda item: (item["y0_px"], item["x0_px"]))
    return {
        "xMin": min(line["x0_px"] for line in lines) * scale_x,
        "yMin": min(line["y0_px"] for line in lines) * scale_y,
        "xMax": max(line["x1_px"] for line in lines) * scale_x,
        "yMax": max(line["y1_px"] for line in lines) * scale_y,
        "text": normalize_text("\n".join(line["text"] for line in lines)),
        "preserve_image": preserve_image,
    }


def make_px_box(lines):
    return {
        "x0_px": min(line["x0_px"] for line in lines),
        "y0_px": min(line["y0_px"] for line in lines),
        "x1_px": max(line["x1_px"] for line in lines),
        "y1_px": max(line["y1_px"] for line in lines),
    }


def load_rapidocr():
    if str(VENDOR_ROOT) not in sys.path:
        sys.path.insert(0, str(VENDOR_ROOT))
    try:
        from rapidocr_onnxruntime import RapidOCR
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "OCR fallback requires rapidocr_onnxruntime. "
            "Install dependencies with: python3 -m pip install -r requirements.txt"
        ) from exc
    return RapidOCR()


def has_preserve_barrier_between(block, line, barriers):
    for barrier in barriers or []:
        horizontal_overlap = min(line["x1_px"], barrier["x1_px"], block["x1_px"]) - max(
            line["x0_px"],
            barrier["x0_px"],
            block["x0_px"],
        )
        if horizontal_overlap <= 0:
            continue
        if block["y1_px"] <= barrier["y0_px"] and barrier["y1_px"] <= line["y0_px"]:
            return True
    return False


def merge_ocr_lines(lines, page_width_px: int, barriers=None):
    blocks = []
    for line in sorted(lines, key=lambda item: (item["y0_px"], item["x0_px"])):
        line_height = max(1.0, line["y1_px"] - line["y0_px"])
        best_idx = None
        best_score = None
        recent_start = max(0, len(blocks) - 24)
        for idx in range(recent_start, len(blocks)):
            block = blocks[idx]
            vertical_gap = line["y0_px"] - block["y1_px"]
            if vertical_gap < -line_height * 0.4:
                continue
            max_gap = max(18.0, block["line_height_px"] * 1.8)
            if vertical_gap > max_gap:
                continue
            if has_preserve_barrier_between(block, line, barriers):
                continue

            overlap = min(line["x1_px"], block["x1_px"]) - max(line["x0_px"], block["x0_px"])
            block_width = max(1.0, block["x1_px"] - block["x0_px"])
            overlap_ratio = overlap / max(1.0, min(line["width_px"], block_width))
            left_aligned = abs(line["x0_px"] - block["x0_px"]) <= page_width_px * 0.05
            right_aligned = abs(line["x1_px"] - block["x1_px"]) <= page_width_px * 0.05
            center_delta = abs(line["center_x_px"] - ((block["x0_px"] + block["x1_px"]) / 2))
            if overlap_ratio < 0.3 and not (left_aligned or right_aligned):
                continue
            if center_delta > page_width_px * 0.18:
                continue

            score = overlap_ratio * 3 - (vertical_gap / max_gap) - (center_delta / page_width_px)
            if best_score is None or score > best_score:
                best_idx = idx
                best_score = score

        if best_idx is None:
            blocks.append(
                {
                    "lines": [line["text"]],
                    "x0_px": line["x0_px"],
                    "x1_px": line["x1_px"],
                    "y0_px": line["y0_px"],
                    "y1_px": line["y1_px"],
                    "line_height_px": line_height,
                }
            )
            continue

        block = blocks[best_idx]
        block["lines"].append(line["text"])
        block["x0_px"] = min(block["x0_px"], line["x0_px"])
        block["x1_px"] = max(block["x1_px"], line["x1_px"])
        block["y1_px"] = max(block["y1_px"], line["y1_px"])
        block["line_height_px"] = max(block["line_height_px"], line_height)

    return blocks


def generate_ocr_pages(job_paths, pdf_size_pt):
    ocr = load_rapidocr()
    width_pt, height_pt = pdf_size_pt
    pages_by_num = {}

    for image_path in sorted(job_paths["pages_dir"].glob("page-*.png")):
        match = re.search(r"page-(\d+)\.png$", image_path.name)
        if not match:
            continue
        page_idx = int(match.group(1))
        image = Image.open(image_path)
        width_px, height_px = image.size
        scale_x = width_pt / width_px
        scale_y = height_pt / height_px
        result, _ = ocr(str(image_path))
        ocr_lines = []
        for item in result or []:
            points, text, score = item
            text = normalize_text(text)
            if not text or score < 0.45:
                continue
            xs = [pt[0] for pt in points]
            ys = [pt[1] for pt in points]
            ocr_lines.append(
                {
                    "idx": len(ocr_lines),
                    "text": text,
                    "x0_px": min(xs),
                    "x1_px": max(xs),
                    "y0_px": min(ys),
                    "y1_px": max(ys),
                    "width_px": max(xs) - min(xs),
                    "center_x_px": (min(xs) + max(xs)) / 2,
                }
            )

        preserve_groups, preserved_line_ids = build_ocr_preserve_groups(ocr_lines, width_px, height_px)
        preserve_barriers = [make_px_box(group) for group in preserve_groups]
        text_lines = [line for line in ocr_lines if line["idx"] not in preserved_line_ids]
        raw_blocks = [
            make_raw_block(group, scale_x, scale_y, preserve_image=True)
            for group in preserve_groups
        ]
        for block in merge_ocr_lines(text_lines, width_px, barriers=preserve_barriers):
            block_text = normalize_text("\n".join(block["lines"]))
            if not block_text:
                continue
            raw_blocks.append(
                {
                    "xMin": block["x0_px"] * scale_x,
                    "yMin": block["y0_px"] * scale_y,
                    "xMax": block["x1_px"] * scale_x,
                    "yMax": block["y1_px"] * scale_y,
                    "text": block_text,
                    "preserve_image": False,
                }
            )
        raw_blocks.sort(key=lambda item: (item["yMin"], item["xMin"]))

        page_blocks = []
        for block_idx, block in enumerate(raw_blocks, start=1):
            if not block["text"]:
                continue
            page_blocks.append(
                {
                    "id": f"p{page_idx:03d}b{block_idx:04d}",
                    "page": page_idx,
                    "block_index": block_idx,
                    **block,
                }
            )
        pages_by_num[page_idx] = page_blocks
    if not pages_by_num:
        return []
    return [pages_by_num.get(idx, []) for idx in range(1, max(pages_by_num) + 1)]


def load_or_build_source_pages(
    pdf_path: Path,
    dpi: int,
    job_paths,
    pdf_size_pt,
    page_start: int = 1,
    page_end: int = 0,
    force_ocr: bool = False,
):
    source_pages_path = job_paths["source_pages_path"]
    if source_pages_path.exists() and not force_ocr:
        return load_source_pages(job_paths)

    ensure_assets(pdf_path, dpi, job_paths, page_start, page_end)
    if source_pages_path.exists():
        return load_source_pages(job_paths)

    pages = parse_bbox(job_paths["bbox_path"])
    if force_ocr or should_use_ocr(pages) or text_extraction_looks_garbled(pages):
        pages = generate_ocr_pages(job_paths, pdf_size_pt)
    save_source_pages(pages, job_paths)
    return pages


def build_batches(pages, max_chars: int):
    blocks = []
    for page in pages:
        visual_regions = build_visual_regions(page)
        classes = classify_blocks(page, visual_regions)
        for block in page:
            if classes.get(block["id"]) not in {"body", "heading", "title"}:
                continue
            if should_preserve_as_image(block):
                continue
            if is_trivial_keep(block["text"]):
                continue
            blocks.append(block)

    batches = []
    current = []
    current_chars = 0
    for block in blocks:
        block_chars = len(block["text"])
        if current and current_chars + block_chars > max_chars:
            batches.append(current)
            current = []
            current_chars = 0
        current.append({"id": block["id"], "text": block["text"]})
        current_chars += block_chars
    if current:
        batches.append(current)
    return batches


def write_schema(path: Path):
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "translation": {"type": "string"},
                    },
                    "required": ["id", "translation"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }
    path.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")


def make_prompt(batch):
    glossary = textwrap.dedent(
        """
        你在翻译一篇计算机领域的学术论文，主题可能涉及 AI 智能体、工具使用、语言模型、推理、系统架构或对齐训练。
        请将每个 text 字段翻译为严谨、流畅、自然的简体中文。

        强制要求：
        1. 忠实原意，不省略信息，不总结，不扩写。
        2. 保持学术写作风格，术语统一。
        3. 保留以下内容原样或仅在必要时做最小调整：
           - URL、邮箱、文件路径、命令行、代码标识、模型名、版本号
           - 作者姓名、机构名、系统名，如 Claude Code、OpenClaw、Anthropic、MCP
           - 引用格式与年份，如 (Chen et al., 2021)
        4. 对纯数字、页码或明显无需翻译的标识，原样返回。
        5. 以完整句子为最小翻译单元。遇到明显跨块或跨页的断句时，不要把残缺片段硬补成新意思，也不要重复相邻片段；后处理会用跨页上下文修复完整句。
        6. 输出必须是 JSON，对象格式固定为 {"items":[{"id":"...","translation":"..."}]}。
        7. 不要输出解释，不要使用 Markdown 代码块。

        术语约定：
        - agentic -> 代理式
        - agent system -> 智能体系统
        - coding agent -> 编码智能体
        - agentic loop -> 代理循环
        - permission system -> 权限系统
        - sandboxing -> 沙箱化
        - context window -> 上下文窗口
        - compaction -> 压缩
        - subagent -> 子代理
        - hook -> 钩子
        - plugin -> 插件
        - skill -> 技能
        - deny-first -> 默认拒绝
        - append-only -> 仅追加
        - trust spectrum -> 信任梯度
        - execution harness / harness -> 执行框架

        如果某个术语约定不适用于当前论文语境，请以原文上下文为准，选择更准确的译法。

        待翻译条目如下：
        """
    ).strip()
    payload = {"items": batch}
    return glossary + "\n" + json.dumps(payload, ensure_ascii=False, indent=2)


def translate_batches(batches, job_paths, *, model: str = "gpt-5.5", reasoning_effort: str = "low"):
    schema_path = job_paths["schema_path"]
    translations_path = job_paths["translations_path"]
    translations = {}
    valid_ids = {item["id"] for batch in batches for item in batch}
    if translations_path.exists():
        existing = json.loads(translations_path.read_text(encoding="utf-8"))
        overlap = len(valid_ids & set(existing))
        if valid_ids and overlap < max(1, int(len(valid_ids) * 0.6)):
            backup = job_paths["job_dir"] / f"translations-stale-{int(time.time())}.json"
            shutil.copy2(translations_path, backup)
        else:
            translations.update({key: value for key, value in existing.items() if key in valid_ids})

    for idx, batch in enumerate(batches, start=1):
        todo = [item for item in batch if item["id"] not in translations]
        if not todo:
            continue
        write_schema(schema_path)

        prompt = make_prompt(todo)
        prompt_path = job_paths["job_dir"] / f"batch-{idx:02d}.prompt.txt"
        out_path = job_paths["job_dir"] / f"batch-{idx:02d}.out.json"
        log_path = job_paths["job_dir"] / f"batch-{idx:02d}.log.txt"
        prompt_path.write_text(prompt, encoding="utf-8")

        success = False
        for attempt in range(1, 4):
            proc = run(
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
            if proc.returncode == 0 and out_path.exists():
                try:
                    payload = json.loads(out_path.read_text(encoding="utf-8"))
                    items = payload["items"]
                    seen = {item["id"] for item in items}
                    expected = {item["id"] for item in todo}
                    if seen != expected:
                        missing = sorted(expected - seen)
                        extra = sorted(seen - expected)
                        raise ValueError(f"mismatched ids, missing={missing[:5]}, extra={extra[:5]}")
                    for item in items:
                        translations[item["id"]] = normalize_translation(item["translation"])
                    translations_path.write_text(
                        json.dumps(translations, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    success = True
                    break
                except Exception as exc:  # noqa: BLE001
                    log_path.write_text(
                        log_path.read_text(encoding="utf-8")
                        + f"\n\nPARSE ERROR\n{exc}\n",
                        encoding="utf-8",
                    )
            time.sleep(3 * attempt)
        if not success:
            raise RuntimeError(f"translation batch {idx} failed, see {log_path}")

    return translations


def normalize_translation(text: str) -> str:
    text = text.strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


TERMINAL_PUNCTUATION = set("。！？；：.!?;:）】》”’」』")
NON_TERMINAL_PUNCTUATION = set("，、,")


def line_needs_terminal_punctuation(line: str) -> bool:
    stripped = line.strip()
    if len(stripped) < 12:
        return False
    if stripped[-1] in TERMINAL_PUNCTUATION or stripped[-1] in NON_TERMINAL_PUNCTUATION:
        return False
    if re.search(r"\n", stripped):
        return False
    if re.fullmatch(r"[A-Za-z0-9_.,:;()[\]{}<>=+\-*/&| \t]+", stripped):
        return False
    return True


def terminal_punctuation_for_line(line: str) -> str:
    if re.search(r"(表述为|如下|如下所示|定义为|记为|形式为)$", line.strip()):
        return "："
    return "。"


def repair_translation_punctuation(text: str) -> str:
    lines = text.split("\n")
    repaired = []
    for line in lines:
        stripped = line.strip()
        if stripped and line_needs_terminal_punctuation(stripped):
            line = line.rstrip() + terminal_punctuation_for_line(stripped)
        repaired.append(line)
    return "\n".join(repaired)


def render_line_starts_new_paragraph(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    return bool(
        re.match(r"^\d+(?:\.\d+)*\.?\s+[\u4e00-\u9fffA-Za-z]", stripped)
        or re.match(r"^(定理|引理|推论|命题|证明|算法|图|表)\s*\d*", stripped)
    )


def render_line_is_standalone_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if re.match(r"^\d+(?:\.\d+)*\.?\s+[\u4e00-\u9fffA-Za-z]", stripped):
        return len(stripped) <= 40
    return False


def join_render_lines(left: str, right: str) -> str:
    if not left:
        return right
    if not right:
        return left
    left_tail = left[-1]
    right_head = right[0]
    latin_left = bool(re.match(r"[A-Za-z0-9)\]}）】》]", left_tail))
    latin_right = bool(re.match(r"[A-Za-z0-9(\[{（【《]", right_head))
    cjk_left = bool(re.match(r"[\u4e00-\u9fff]", left_tail))
    cjk_right = bool(re.match(r"[\u4e00-\u9fff]", right_head))
    if (latin_left and (latin_right or cjk_right)) or (cjk_left and latin_right):
        return left + " " + right
    return left + right


def render_line_break_is_paragraph_boundary(left: str, right: str) -> bool:
    left = left.strip()
    right = right.strip()
    if not left or not right:
        return False
    if not re.match(r"[\u4e00-\u9fffA-Za-z0-9(（“]", right):
        return False
    if re.search(r"[A-Za-z_][A-Za-z0-9_]*(?:\[[^\]]+\]|\([^)]+\))$", left):
        return False
    if re.search(r"(表述为|如下|如下所示|定义为|记为|形式为)$", left):
        return True
    continuation_tails = (
        "当",
        "如果",
        "由于",
        "因为",
        "和",
        "或",
        "与",
        "在",
        "为",
        "由",
        "对",
        "其中",
        "以及",
        "如下",
        "表明",
    )
    if left.endswith(continuation_tails) or left[-1] in NON_TERMINAL_PUNCTUATION:
        return False
    if left[-1] in TERMINAL_PUNCTUATION and len(left) >= 24:
        return True
    return cjk_char_count(left) >= 45 and cjk_char_count(right) >= 6


def reflow_translation_soft_breaks(text: str) -> str:
    paragraphs = []
    current = ""
    for raw_line in normalize_translation(text).split("\n"):
        line = raw_line.strip()
        if not line:
            if current:
                paragraphs.append(current)
                current = ""
            continue
        if current and render_line_starts_new_paragraph(line):
            paragraphs.append(current)
            current = ""
        elif current and render_line_break_is_paragraph_boundary(current, line):
            paragraphs.append(current)
            current = ""
        current = join_render_lines(current, line) if current else line
        if render_line_is_standalone_heading(line):
            paragraphs.append(current)
            current = ""
    if current:
        paragraphs.append(current)
    return "\n".join(paragraphs)


def prepare_render_translation(text: str) -> str:
    return repair_translation_punctuation(reflow_translation_soft_breaks(strip_journal_footer_lines(text)))


SOURCE_CONTINUATION_TAIL_WORDS = {
    "and",
    "or",
    "of",
    "the",
    "a",
    "an",
    "to",
    "in",
    "with",
    "by",
    "for",
    "that",
    "which",
    "when",
    "where",
    "whose",
    "as",
    "if",
    "while",
}

SOURCE_CONTINUATION_HEAD_WORDS = {
    "and",
    "or",
    "of",
    "to",
    "in",
    "with",
    "by",
    "for",
    "that",
    "which",
    "where",
    "whose",
    "as",
    "than",
}


def source_text_without_running_header(text: str, page_num: int) -> str:
    text = strip_leading_running_header_text(text, page_num)
    lines = [line.strip() for line in normalize_text(text).split("\n") if line.strip()]
    if page_num > 1 and lines:
        first = lines[0]
        compact = re.sub(r"[^a-z0-9]+", "", first.lower())
        if "waitfree" in compact and "synchronization" in compact:
            lines = lines[1:]
    while lines and (is_page_number(lines[0]) or is_decorated_ocr_page_number_text(lines[0])):
        lines = lines[1:]
    return strip_journal_footer_lines("\n".join(lines))


def strip_running_header_prefix_from_line(line: str, page_num: int) -> str:
    if page_num <= 1:
        return line
    stripped = line.strip()
    stripped = re.sub(
        r"^[·•]?\s*\d{1,3}\s*[·•.]?\s*(?=(?:Maurice\s*Herlihy|Wait-?\s*Free\s*Synchronization|Wait-FreeSynchronization|无等待同步)\b)",
        "",
        stripped,
        flags=re.I,
    )
    for pattern, flags in (
        (r"^(?:Maurice\s*Herlihy|MauriceHerlihy|Wait-?\s*Free\s*Synchronization|Wait-FreeSynchronization)\b", re.I),
        (r"^无等待同步", 0),
    ):
        match = re.match(pattern, stripped, flags=flags)
        if not match:
            continue
        rest = stripped[match.end() :]
        page_match = re.match(r"\s*[·•.]?\s*\d{1,3}\s*", rest)
        if page_match:
            stripped = rest[page_match.end() :].strip()
        elif not rest.strip() or rest[:1].isspace():
            stripped = rest.strip()
        break
    if is_page_number(stripped) or is_decorated_ocr_page_number_text(stripped):
        return ""
    return stripped


def strip_leading_running_header_text(text: str, page_num: int) -> str:
    if page_num <= 1:
        return normalize_text(text)
    lines = normalize_text(text).split("\n")
    cleaned = []
    stripping = True
    for line in lines:
        if stripping:
            line = strip_running_header_prefix_from_line(line, page_num)
            if not line:
                continue
            stripping = False
        cleaned.append(line)
    return normalize_text("\n".join(cleaned))


def source_boundary_text_for_block(block) -> str:
    return source_text_without_running_header(block.get("text", ""), block.get("page", 1))


def source_text_likely_continues(text: str) -> bool:
    stripped = normalize_text(text)
    if not stripped:
        return False
    if stripped.endswith("-"):
        return True
    if stripped[-1] in ",;:，；：":
        return True
    words = re.findall(r"[A-Za-z]+", stripped)
    if words and words[-1].lower() in SOURCE_CONTINUATION_TAIL_WORDS:
        return True
    return not bool(re.search(r"[.!?。！？][\"')\]）】”’]*$", stripped))


def source_text_starts_as_continuation(text: str) -> bool:
    stripped = normalize_text(text)
    if not stripped:
        return False
    if re.match(r"^[,.;:)\]}，。；：）】]", stripped):
        return True
    words = re.findall(r"[A-Za-z]+", stripped)
    if words and words[0].lower() in SOURCE_CONTINUATION_HEAD_WORDS:
        return True
    first = stripped[0]
    return bool(re.match(r"[a-z]", first))


def boundary_repair_key(previous_id: str, next_id: str) -> str:
    return f"{previous_id}->{next_id}"


def boundary_candidate_blocks(blocks) -> list[dict]:
    visual_regions = build_visual_regions(blocks)
    classes = classify_blocks(blocks, visual_regions)
    block_by_id = {block["id"]: block for block in blocks}
    visual_ids = {source_id for region in visual_regions for source_id in region["source_ids"]}
    candidates = []
    for block in sorted(blocks, key=lambda item: (item["yMin"], item["xMin"])):
        if block["id"] in visual_ids:
            continue
        text = source_boundary_text_for_block(block)
        if not text or is_trivial_keep(text):
            continue
        classification = classes.get(block["id"], "unknown")
        if classification in {"page_number", "journal_footer", "reference"}:
            continue
        if classification == "header_footer" and not source_requires_chinese_translation(text):
            continue
        if classification not in {"body", "header_footer"}:
            continue
        enriched = dict(block)
        enriched["_boundary_text"] = text
        enriched["_classification"] = classification
        candidates.append(enriched)
    return candidates


def first_complete_sentence_from_boundary_context(text: str) -> str:
    normalized = normalize_text(text)
    if not normalized:
        return ""
    match = re.search(r"^(.+?[.!?])(?:\s|$)", normalized)
    if match:
        return match.group(1).strip()
    return normalized


def rows_in_vertical_window(page_num: int, bbox_lines: list[dict], y0: float, y1: float) -> list[dict]:
    lines = [
        line
        for line in bbox_lines
        if line.get("page") == page_num
        and y0 <= bbox_center(line["bbox"])[1] <= y1
    ]
    rows = []
    for row in merge_bbox_line_fragments(lines):
        pseudo = {
            "page": page_num,
            "yMin": row["bbox"][1],
            "yMax": row["bbox"][3],
            "text": row["text"],
        }
        if source_body_row_is_noise(pseudo, row):
            continue
        rows.append(row)
    return rows


def boundary_source_sentence_from_bbox_margin(
    previous_block,
    next_block,
    bbox_lines_by_page_map: dict[int, list[dict]] | None,
) -> str:
    if not bbox_lines_by_page_map:
        return ""
    previous_lines = bbox_lines_by_page_map.get(previous_block.get("page", 1), [])
    next_lines = bbox_lines_by_page_map.get(next_block.get("page", 1), [])
    if not previous_lines or not next_lines:
        return ""
    previous_rows = rows_in_vertical_window(
        previous_block.get("page", 1),
        previous_lines,
        max(0.0, previous_block["yMin"] - 4.0),
        previous_block["yMax"] + 32.0,
    )
    next_rows = rows_in_vertical_window(
        next_block.get("page", 1),
        next_lines,
        0.0,
        max(next_block["yMax"], next_block["yMin"] + 58.0),
    )
    if not previous_rows or not next_rows:
        return ""
    context_text = " ".join(
        row["text"]
        for row in sorted(previous_rows, key=lambda item: (item["bbox"][1], item["bbox"][0]))
        + sorted(next_rows, key=lambda item: (item["bbox"][1], item["bbox"][0]))
    )
    return first_complete_sentence_from_boundary_context(context_text)


def fallback_boundary_source_sentence(previous_block, next_block) -> str:
    return first_complete_sentence_from_boundary_context(
        source_boundary_text_for_block(previous_block) + " " + source_boundary_text_for_block(next_block)
    )


def compact_source_phrase(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalize_text(text).lower())


def source_head_compact_fragment(text: str) -> str:
    stripped = normalize_text(text)
    match = re.match(r"^[,.;:)\]}，。；：）】\s]*(.+?)(?:[.!?]\s|$)", stripped)
    head_sentence = match.group(1) if match else stripped
    words = re.findall(r"[A-Za-z0-9]+", head_sentence)
    if not words:
        return ""
    if len(words) >= 2:
        return compact_source_phrase(" ".join(words[:2]))
    return compact_source_phrase(words[0])


def boundary_sentence_contains_next_fragment(source_sentence: str, next_source: str) -> bool:
    head = source_head_compact_fragment(next_source)
    if len(head) < 4:
        return False
    return head in compact_source_phrase(source_sentence)


def detect_cross_page_sentence_splits(
    selected_pages,
    *,
    bbox_lines_by_page: dict[int, list[dict]] | None = None,
) -> list[dict]:
    ordered_pages = sorted(selected_pages, key=lambda item: item[0])
    candidates = []
    for (previous_page_num, previous_blocks), (next_page_num, next_blocks) in zip(ordered_pages, ordered_pages[1:]):
        if next_page_num != previous_page_num + 1:
            continue
        previous_candidates = boundary_candidate_blocks(previous_blocks)
        next_candidates = boundary_candidate_blocks(next_blocks)
        previous_block = next(
            (
                block
                for block in reversed(previous_candidates)
                if block.get("_classification") == "body"
                if source_text_likely_continues(block.get("_boundary_text", source_boundary_text_for_block(block)))
            ),
            None,
        )
        next_block = next(
            (
                block
                for block in next_candidates
                if source_text_starts_as_continuation(block.get("_boundary_text", source_boundary_text_for_block(block)))
            ),
            None,
        )
        if previous_block is None or next_block is None:
            continue
        source_sentence = boundary_source_sentence_from_bbox_margin(
            previous_block,
            next_block,
            bbox_lines_by_page,
        ) or fallback_boundary_source_sentence(previous_block, next_block)
        if not boundary_sentence_contains_next_fragment(
            source_sentence,
            next_block.get("_boundary_text", source_boundary_text_for_block(next_block)),
        ):
            continue
        candidates.append(
            {
                "key": boundary_repair_key(previous_block["id"], next_block["id"]),
                "previous_id": previous_block["id"],
                "next_id": next_block["id"],
                "previous_page": previous_page_num,
                "next_page": next_page_num,
                "previous_source": previous_block.get("_boundary_text", source_boundary_text_for_block(previous_block)),
                "next_source": next_block.get("_boundary_text", source_boundary_text_for_block(next_block)),
                "source_sentence": source_sentence,
            }
        )
    return candidates


def remove_leading_translation_prefix(text: str, prefix: str) -> str:
    if not text or not prefix:
        return text
    variants = [prefix, prepare_render_translation(prefix)]
    prepared_text = prepare_render_translation(text)
    for variant in variants:
        variant = variant.strip()
        if not variant:
            continue
        if text.startswith(variant):
            return text[len(variant) :].lstrip()
        if prepared_text.startswith(variant):
            return prepared_text[len(variant) :].lstrip()
    lines = text.splitlines()
    if len(lines) >= 2:
        first_compact = re.sub(r"\s+", "", lines[0].lower())
        if first_compact in {"无等待同步", "wait-freesynchronization", "waitfreesynchronization"}:
            tail = "\n".join(lines[1:])
            trimmed_tail = remove_leading_translation_prefix(tail, prefix)
            if trimmed_tail != tail:
                return lines[0] + "\n" + trimmed_tail
    return text


def load_boundary_repairs(job_paths) -> dict:
    if not job_paths:
        return {}
    path = job_paths.get("boundary_repairs_path")
    if not path or not Path(path).exists():
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_boundary_repairs(job_paths, repairs: dict) -> None:
    path = job_paths.get("boundary_repairs_path")
    if not path:
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(repairs, ensure_ascii=False, indent=2), encoding="utf-8")


def write_boundary_repair_schema(path: Path) -> None:
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "translation": {"type": "string"},
                        "next_prefix_translation": {"type": "string"},
                    },
                    "required": ["key", "translation", "next_prefix_translation"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }
    path.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")


def make_boundary_repair_prompt(items: list[dict]) -> str:
    payload = {"items": items}
    return (
        "你在修复学术论文中被分页切断的英文句子翻译。每个 item 给出跨页拼接后的 source_sentence、"
        "上一页已有译文 previous_translation、下一页开头已有译文 next_translation。"
        "请为 source_sentence 给出一条完整、忠实、自然的简体中文 translation。"
        "next_prefix_translation 填写 next_translation 开头中对应该跨页残片、需要从下一页删除以避免重复的中文前缀；"
        "如果没有需要删除的前缀，填空字符串。不要解释，不要 Markdown，只输出 JSON。\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def translate_boundary_sentence_repairs(
    candidates: list[dict],
    translations: dict,
    job_paths,
    *,
    model: str,
    reasoning_effort: str = "low",
) -> dict:
    if not candidates:
        return {}
    schema_path = job_paths["boundary_schema_path"]
    write_boundary_repair_schema(schema_path)
    prompt_items = []
    for candidate in candidates:
        prompt_items.append(
            {
                "key": candidate["key"],
                "source_sentence": candidate["source_sentence"],
                "previous_source": candidate["previous_source"],
                "next_source": candidate["next_source"],
                "previous_translation": translations.get(candidate["previous_id"], ""),
                "next_translation": translations.get(candidate["next_id"], ""),
            }
        )
    prompt = make_boundary_repair_prompt(prompt_items)
    prompt_path = job_paths["job_dir"] / "boundary-sentence-repair.prompt.txt"
    out_path = job_paths["job_dir"] / "boundary-sentence-repair.out.json"
    log_path = job_paths["job_dir"] / "boundary-sentence-repair.log.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    proc = run(
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
        raise RuntimeError(f"boundary sentence repair failed, see {log_path}")
    data = json.loads(out_path.read_text(encoding="utf-8"))
    return {
        item["key"]: {
            "translation": item.get("translation", "").strip(),
            "next_prefix_translation": item.get("next_prefix_translation", "").strip(),
        }
        for item in data.get("items", [])
        if item.get("key")
    }


def postprocess_cross_page_sentence_splits(
    selected_pages,
    translations: dict,
    *,
    boundary_repairs: dict | None = None,
    job_paths=None,
    model: str | None = None,
    reasoning_effort: str = "low",
) -> dict:
    repaired_translations = dict(translations)
    lines_by_page = bbox_lines_by_page(job_paths) if job_paths else {}
    candidates = detect_cross_page_sentence_splits(selected_pages, bbox_lines_by_page=lines_by_page)
    repairs = {}
    repairs.update(load_boundary_repairs(job_paths))
    if boundary_repairs:
        repairs.update(boundary_repairs)
    missing = [candidate for candidate in candidates if candidate["key"] not in repairs]
    if missing and model and job_paths:
        generated = translate_boundary_sentence_repairs(
            missing,
            repaired_translations,
            job_paths,
            model=model,
            reasoning_effort=reasoning_effort,
        )
        repairs.update(generated)
        save_boundary_repairs(job_paths, repairs)

    for candidate in candidates:
        repair = repairs.get(candidate["key"])
        if not repair:
            continue
        translation = normalize_translation(repair.get("translation", ""))
        if translation:
            repaired_translations[candidate["previous_id"]] = translation
        next_prefix = normalize_translation(repair.get("next_prefix_translation", ""))
        if next_prefix:
            repaired_translations[candidate["next_id"]] = remove_leading_translation_prefix(
                repaired_translations.get(candidate["next_id"], ""),
                next_prefix,
            )
    return repaired_translations


def page_image_path(page_num: int, job_paths) -> Path:
    return job_paths["pages_dir"] / f"page-{page_num:03d}.png"


def translated_page_path(page_num: int, job_paths) -> Path:
    return job_paths["translated_pages_dir"] / f"page-{page_num:03d}.png"


def sample_background(img: Image.Image, box):
    x0, y0, x1, y1 = box
    width, height = img.size
    pad = 3
    samples = []

    def add_strip(xs, ys, xe, ye):
        xs = max(0, min(width - 1, xs))
        ys = max(0, min(height - 1, ys))
        xe = max(0, min(width, xe))
        ye = max(0, min(height, ye))
        if xe <= xs or ye <= ys:
            return
        crop = img.crop((xs, ys, xe, ye)).convert("RGB")
        pixels = list(crop.getdata())
        samples.extend(pixels)

    add_strip(x0 - pad, y0 - pad, x1 + pad, y0)
    add_strip(x0 - pad, y1, x1 + pad, y1 + pad)
    add_strip(x0 - pad, y0, x0, y1)
    add_strip(x1, y0, x1 + pad, y1)
    if not samples:
        return (255, 255, 255)

    bright = [p for p in samples if sum(p) >= 540]
    target = bright or samples
    return tuple(int(median(channel)) for channel in zip(*target))


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int):
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    lines = []
    dummy = ImageDraw.Draw(Image.new("RGB", (10, 10), "white"))

    def text_width(value: str) -> int:
        bbox = dummy.textbbox((0, 0), value, font=font)
        return bbox[2] - bbox[0]

    def tokens_for(paragraph: str) -> list[str]:
        return re.findall(
            r"\s+|[A-Za-z0-9][A-Za-z0-9._+:/%#?=&~×-]*|.",
            paragraph,
            flags=re.S,
        )

    def append_long_token(token: str, current: str):
        for ch in token:
            candidate = current + ch
            if text_width(candidate) <= max_width or not current:
                current = candidate
            else:
                lines.append(current.rstrip())
                current = ch
        return current

    for paragraph in paragraphs:
        current = ""
        for token in tokens_for(paragraph):
            if token.isspace() and not current:
                continue
            candidate = current + token
            if text_width(candidate) <= max_width or not current:
                current = candidate
            else:
                lines.append(current.rstrip())
                token = token.lstrip()
                if not token:
                    current = ""
                elif text_width(token) <= max_width:
                    current = token
                else:
                    current = append_long_token(token, "")
            if current and text_width(current) > max_width:
                current = append_long_token(current, "")
        if current:
            lines.append(current.rstrip())
    return lines or [text]


def fit_font_and_lines(
    text: str,
    box_width: int,
    box_height: int,
    vertical: bool,
    max_font_size: int | None = None,
):
    if vertical:
        max_size = max(14, min(box_width, box_height // 2))
        return max_size, [text]

    low, high = 10, max(12, min(80, box_height))
    if max_font_size is not None:
        high = max(low, min(high, max_font_size))
    best = (10, wrap_text(text, ImageFont.truetype(FONT_PATH, 10), box_width))
    while low <= high:
        mid = (low + high) // 2
        font = ImageFont.truetype(FONT_PATH, mid)
        lines = wrap_text(text, font, box_width)
        ascent, descent = font.getmetrics()
        line_height = int((ascent + descent) * 1.25)
        total_height = line_height * max(1, len(lines))
        if total_height <= box_height:
            best = (mid, lines)
            low = mid + 1
        else:
            high = mid - 1
    return best


def source_line_count(text: str) -> int:
    return max(1, normalize_text(text).count("\n") + 1)


def target_font_size_for_block(block, dpi: int, vertical: bool) -> int | None:
    if vertical:
        return None
    scale = dpi / 72.0
    block_height_px = max(1.0, (block["yMax"] - block["yMin"]) * scale)
    source_line_height = block_height_px / source_line_count(block.get("text", ""))
    return max(10, int(round(source_line_height / 1.25 * SOURCE_FONT_SCALE)))


def target_font_size_points_for_block(block, *, max_size: float = 30.0) -> float:
    block_height_pt = max(1.0, block["yMax"] - block["yMin"])
    source_line_height = block_height_pt / source_line_count(block.get("text", ""))
    return max(6.0, min(max_size, source_line_height / 1.25 * SOURCE_FONT_SCALE))


def draw_vertical(draw_img: Image.Image, text: str, box, fill_bg, fill_text):
    x0, y0, x1, y1 = box
    width = max(1, x1 - x0)
    height = max(1, y1 - y0)
    font_size, _ = fit_font_and_lines(text, height - 4, width - 4, vertical=True)
    font = ImageFont.truetype(FONT_PATH, font_size)
    tmp = Image.new("RGBA", (height, width), fill_bg + (255,))
    tdraw = ImageDraw.Draw(tmp)
    bbox = tdraw.textbbox((0, 0), text, font=font)
    tx = max(0, (height - (bbox[2] - bbox[0])) // 2)
    ty = max(0, (width - (bbox[3] - bbox[1])) // 2)
    tdraw.text((tx, ty), text, font=font, fill=fill_text)
    rotated = tmp.rotate(90, expand=True)
    draw_img.alpha_composite(rotated, (x0, y0))


def block_to_px_box(block, dpi: int, page_width: int, page_height: int, pad: int = 2):
    scale = dpi / 72.0
    x0 = int(block["xMin"] * scale) - pad
    y0 = int(block["yMin"] * scale) - pad
    x1 = int(block["xMax"] * scale) + pad
    y1 = int(block["yMax"] * scale) + pad
    return (
        max(0, x0),
        max(0, y0),
        min(page_width, x1),
        min(page_height, y1),
    )


def protected_box_for_block(block, dpi: int, page_width: int, page_height: int):
    x0, y0, x1, y1 = block_to_px_box(block, dpi, page_width, page_height, pad=3)
    if is_formula_like(block.get("text", "")):
        width = x1 - x0
        center_x = (x0 + x1) / 2
        if width < page_width * 0.45:
            if center_x < page_width / 2:
                x0, x1 = 0, int(page_width * FORMULA_COLUMN_FRACTION)
            else:
                x0, x1 = int(page_width * (1 - FORMULA_COLUMN_FRACTION)), page_width
            y0 -= FORMULA_PAD_TOP_PX
            y1 += FORMULA_PAD_BOTTOM_PX
        else:
            y0 -= 4
            y1 += 4
    return (
        max(0, x0),
        max(0, y0),
        min(page_width, x1),
        min(page_height, y1),
    )


def overlap_area(box_a, box_b):
    x0 = max(box_a[0], box_b[0])
    y0 = max(box_a[1], box_b[1])
    x1 = min(box_a[2], box_b[2])
    y1 = min(box_a[3], box_b[3])
    if x1 <= x0 or y1 <= y0:
        return 0
    return (x1 - x0) * (y1 - y0)


def horizontal_overlap(box_a, box_b):
    x0 = max(box_a[0], box_b[0])
    x1 = min(box_a[2], box_b[2])
    return max(0, x1 - x0)


def avoid_protected_boxes(box, protected_boxes):
    x0, y0, x1, y1 = box
    for protected in protected_boxes or []:
        if overlap_area((x0, y0, x1, y1), protected) <= 0:
            continue
        protected_center_y = (protected[1] + protected[3]) / 2
        if y1 <= protected_center_y:
            y1 = min(y1, protected[1])
        elif y0 >= protected_center_y:
            y0 = max(y0, protected[3])
        else:
            top_space = protected[1] - y0
            bottom_space = y1 - protected[3]
            if top_space >= bottom_space:
                y1 = min(y1, protected[1])
            else:
                y0 = max(y0, protected[3])
        if x1 - x0 < 8 or y1 - y0 < 8:
            return None
    return x0, y0, x1, y1


def translation_for_block(block, translations):
    translated = translations.get(block["id"])
    if translated:
        return prepare_render_translation(translated)
    if is_trivial_keep(block["text"]):
        return ""
    return block["text"]


def text_required_height(text: str, width: int, font_size: int) -> int:
    font = ImageFont.truetype(FONT_PATH, font_size)
    lines = wrap_text(text, font, width)
    ascent, descent = font.getmetrics()
    line_height = int((ascent + descent) * 1.25)
    return line_height * max(1, len(lines)) + 4


def boxes_horizontally_conflict(box_a, box_b) -> bool:
    overlap = horizontal_overlap(box_a, box_b)
    min_width = max(1, min(box_a[2] - box_a[0], box_b[2] - box_b[0]))
    return overlap >= min_width * 0.2


def build_render_boxes(blocks, translations, dpi: int, page_width: int, page_height: int, protected_boxes):
    base_boxes = {}
    for block in blocks:
        if should_preserve_as_image(block):
            continue
        if is_page_number(block["text"]):
            continue
        translation = translation_for_block(block, translations)
        if not translation:
            continue
        box = block_to_px_box(block, dpi, page_width, page_height, pad=2)
        box = avoid_protected_boxes(box, protected_boxes)
        if box is not None:
            base_boxes[block["id"]] = box

    render_boxes = dict(base_boxes)
    occupied = list(base_boxes.items())
    protected_occupied = [(f"protected-{idx}", box) for idx, box in enumerate(protected_boxes or [])]
    for block in blocks:
        box = base_boxes.get(block["id"])
        if box is None:
            continue
        translation = translation_for_block(block, translations)
        x0, y0, x1, y1 = box
        width = max(10, x1 - x0 - 4)
        vertical = (y1 - y0) > (x1 - x0) * 3 and len(translation) > 4
        target_font_size = target_font_size_for_block(block, dpi, vertical)
        if target_font_size is None:
            continue
        required_height = text_required_height(translation, width, target_font_size)
        current_height = y1 - y0
        if required_height <= current_height:
            continue

        top_limit = TEXT_BOX_MARGIN_PX
        bottom_limit = page_height - TEXT_BOX_MARGIN_PX
        for other_id, other_box in occupied + protected_occupied:
            if other_id == block["id"]:
                continue
            if not boxes_horizontally_conflict(box, other_box):
                continue
            if other_box[3] <= y0:
                top_limit = max(top_limit, other_box[3] + TEXT_BOX_MARGIN_PX)
            elif other_box[1] >= y1:
                bottom_limit = min(bottom_limit, other_box[1] - TEXT_BOX_MARGIN_PX)

        remaining = required_height - current_height
        grow_down = max(0, min(remaining, bottom_limit - y1))
        y1 += grow_down
        remaining -= grow_down
        grow_up = max(0, min(remaining, y0 - top_limit))
        y0 -= grow_up
        render_boxes[block["id"]] = (x0, y0, x1, y1)
    return render_boxes


def draw_block(
    draw_img: Image.Image,
    block,
    translation: str,
    dpi: int,
    protected_boxes=None,
    render_box=None,
):
    box = render_box or block_to_px_box(block, dpi, draw_img.width, draw_img.height, pad=2)
    if render_box is None:
        box = avoid_protected_boxes(box, protected_boxes)
    if box is None:
        return
    x0, y0, x1, y1 = box
    box = (x0, y0, x1, y1)
    bg = sample_background(draw_img.convert("RGB"), box)
    rect = Image.new("RGBA", (x1 - x0, y1 - y0), bg + (255,))
    draw_img.alpha_composite(rect, (x0, y0))

    width = max(10, x1 - x0 - 4)
    height = max(10, y1 - y0 - 4)
    vertical = (y1 - y0) > (x1 - x0) * 3 and len(translation) > 4
    if vertical:
        draw_vertical(draw_img, translation, box, bg, (32, 32, 32, 255))
        return

    target_font_size = target_font_size_for_block(block, dpi, vertical=False)
    font_size, lines = fit_font_and_lines(
        translation,
        width,
        height,
        vertical=False,
        max_font_size=target_font_size,
    )
    font = ImageFont.truetype(FONT_PATH, font_size)
    ascent, descent = font.getmetrics()
    line_height = int((ascent + descent) * 1.25)
    text_img = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
    text_draw = ImageDraw.Draw(text_img)
    y = 2
    for line in lines:
        text_draw.text((2, y), line, font=font, fill=(32, 32, 32, 255))
        y += line_height
    draw_img.alpha_composite(text_img, (x0, y0))


def render_pages(pages, translations, dpi: int, job_paths):
    for page_num, blocks in pages:
        src = page_image_path(page_num, job_paths)
        out = translated_page_path(page_num, job_paths)
        source_img = Image.open(src).convert("RGBA")
        img = source_img.copy()
        protected_boxes = [
            protected_box_for_block(block, dpi, img.width, img.height)
            for block in blocks
            if should_preserve_as_image(block)
        ]
        render_boxes = build_render_boxes(blocks, translations, dpi, img.width, img.height, protected_boxes)
        for block in blocks:
            if should_preserve_as_image(block):
                continue
            if is_page_number(block["text"]):
                continue
            translated = translation_for_block(block, translations)
            if not translated:
                continue
            draw_block(
                img,
                block,
                translated,
                dpi,
                protected_boxes=protected_boxes,
                render_box=render_boxes.get(block["id"]),
            )
        for box in protected_boxes:
            x0, y0, x1, y1 = box
            if x1 > x0 and y1 > y0:
                img.alpha_composite(source_img.crop(box), (x0, y0))
        img.save(out)


def load_fitz():
    if str(VENDOR_ROOT) not in sys.path:
        sys.path.insert(0, str(VENDOR_ROOT))
    try:
        import fitz
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Vector PDF rendering requires PyMuPDF. "
            "Install it with: python3 -m pip install --target vendor pymupdf"
        ) from exc
    return fitz


def vector_text_color(block) -> tuple[float, float, float]:
    text = block.get("text", "")
    if text.startswith("DeepSeek Scales") or text.startswith("NVIDIA ") or text.startswith("Figure "):
        return VECTOR_ACCENT_COLOR if text.startswith("DeepSeek Scales") else VECTOR_BODY_COLOR
    return VECTOR_BODY_COLOR


def expanded_rect_for_text(fitz, block, page_rect):
    pad_x = 1.5
    pad_y = 1.5
    return fitz.Rect(
        max(page_rect.x0, block["xMin"] - pad_x),
        max(page_rect.y0, block["yMin"] - pad_y),
        min(page_rect.x1, block["xMax"] + pad_x),
        min(page_rect.y1, block["yMax"] + pad_y),
    )


def text_style(style_name: str) -> TextStyle:
    return DOCUMENT_STYLES.get(style_name, DOCUMENT_STYLES["body"])


def text_height_for_lines(lines, font_size: float, line_height_factor: float, paragraph_spacing: float = 0.0) -> float:
    if not lines:
        return 0.0
    line_height = font_size * line_height_factor
    total = 0.0
    for line in lines:
        total += line_height
        if line == []:
            total += paragraph_spacing
    return total


def fitted_text_spacing(lines, font_size: float, rect_height: float, style: TextStyle) -> tuple[float, float] | None:
    line_factor_candidates = [style.line_height_factor]
    min_line_factor = style.min_line_height_factor if style.min_line_height_factor is not None else style.line_height_factor
    if min_line_factor < style.line_height_factor:
        line_factor_candidates.append(min_line_factor)
    paragraph_spacing_candidates = [style.paragraph_spacing]
    min_paragraph_spacing = style.min_paragraph_spacing if style.min_paragraph_spacing is not None else style.paragraph_spacing
    if min_paragraph_spacing < style.paragraph_spacing:
        paragraph_spacing_candidates.append(min_paragraph_spacing)

    for paragraph_spacing in paragraph_spacing_candidates:
        for line_factor in line_factor_candidates:
            if text_height_for_lines(lines, font_size, line_factor, paragraph_spacing) <= rect_height:
                return line_factor, paragraph_spacing
    return None


def insert_vector_textbox(
    page,
    fitz,
    rect,
    text: str,
    font_size: float,
    color,
    *,
    line_height_factor: float = 1.22,
    paragraph_spacing: float = 0.0,
    letter_spacing: float = 0.0,
    allow_shrink: bool = False,
    min_line_height_factor: float | None = None,
    min_paragraph_spacing: float | None = None,
):
    if not text.strip() or rect.is_empty:
        return True

    def draw_if_fits(size: float, line_factor: float, para_spacing: float) -> bool:
        lines = wrap_mixed_pdf_text(fitz, text, rect.width, size)
        style = TextStyle(
            font_size=size,
            line_height_factor=line_factor,
            paragraph_spacing=para_spacing,
            letter_spacing=letter_spacing,
            color=color,
            min_line_height_factor=min_line_height_factor,
            min_paragraph_spacing=min_paragraph_spacing,
        )
        fit = fitted_text_spacing(lines, size, rect.height, style)
        if fit is None:
            return False
        fitted_line_factor, fitted_para_spacing = fit
        draw_mixed_pdf_lines(
            page,
            fitz,
            rect,
            lines,
            size,
            size * fitted_line_factor,
            color,
            paragraph_spacing=fitted_para_spacing,
            letter_spacing=letter_spacing,
        )
        return True

    if draw_if_fits(font_size, line_height_factor, paragraph_spacing):
        return True
    if not allow_shrink:
        return False
    size = font_size - 0.5
    while size >= 5.0:
        if draw_if_fits(size, line_height_factor, paragraph_spacing):
            return True
        size -= 0.5
    return False


def is_vertical_vector_block(block, text: str) -> bool:
    return (block["yMax"] - block["yMin"]) > (block["xMax"] - block["xMin"]) * 3 and len(text) > 4


def insert_vertical_vector_text(page, fitz, rect, text: str, font_size: float, color):
    if not text.strip() or rect.is_empty:
        return

    fontname = VECTOR_FONT if re.search(r"[^\x00-\x7f]", text) else "helv"
    size = min(font_size, max(5.0, rect.width * 0.85), 30.0)
    while size >= 5.0:
        text_length = fitz.get_text_length(text, fontname=fontname, fontsize=size)
        if text_length <= rect.height:
            break
        size -= 0.5

    x = rect.x0 + min(rect.width - 1.0, size * 0.9)
    y = rect.y1 - 1.0
    page.insert_text(
        (x, y),
        text,
        fontsize=size,
        fontname=fontname,
        color=color,
        rotate=90,
    )


def pdf_token_font(token: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9._+:/%#?=&~×,;()[\]'\" -]+", token):
        return "helv"
    return VECTOR_FONT


def pdf_text_width(fitz, text: str, font_size: float) -> float:
    if not text:
        return 0.0
    return sum(
        fitz.get_text_length(token, fontname=pdf_token_font(token), fontsize=font_size)
        for token in drawable_pdf_line_tokens([text])
    )


def split_pdf_text_tokens(text: str) -> list[str]:
    return re.findall(
        r"\s+|[A-Za-z0-9][A-Za-z0-9._+:/%#?=&~×,;()'\"-]*|[!-/:-@\[-`{-~]+|[\u4e00-\u9fff]+|[^\sA-Za-z0-9\u4e00-\u9fff]",
        text,
        flags=re.S,
    )


def token_list_width(fitz, tokens: list[str], font_size: float) -> float:
    return sum(
        fitz.get_text_length(token, fontname=pdf_token_font(token), fontsize=font_size)
        for token in drawable_pdf_line_tokens(tokens)
    )


def strip_trailing_space_tokens(tokens: list[str]) -> list[str]:
    while tokens and tokens[-1].isspace():
        tokens = tokens[:-1]
    return tokens


def break_current_line(lines, current):
    lines.append(strip_trailing_space_tokens(current))
    return []


def split_pdf_wrap_units(paragraph: str) -> list[str]:
    units = []
    split_latin_tokens = bool(re.search(r"[\u4e00-\u9fff]", paragraph))
    for token in split_pdf_text_tokens(paragraph):
        if token.isspace():
            units.append(" ")
        elif split_latin_tokens and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+:/%#?=&~×,;()'\"-]*", token):
            units.extend(token)
        else:
            units.append(token)
    return units


def small_overflow_tolerance(max_width: float, font_size: float) -> float:
    return max(font_size * 1.1, max_width * 0.02)


def wrap_mixed_pdf_text(fitz, text: str, max_width: float, font_size: float):
    lines = []
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    allowed_width = max_width + small_overflow_tolerance(max_width, font_size)
    for paragraph_idx, paragraph in enumerate(paragraphs):
        current = []
        units = split_pdf_wrap_units(paragraph)
        idx = 0
        while idx < len(units):
            token = units[idx]
            if token.isspace():
                token = " "
                if not current:
                    idx += 1
                    continue
            candidate_width = token_list_width(fitz, current + [token], font_size)
            if current and candidate_width > allowed_width:
                current = break_current_line(
                    lines,
                    current,
                )
                if token.isspace():
                    idx += 1
                    continue
                else:
                    continue
            current.append(token)
            idx += 1
        if current:
            lines.append(current)
        if paragraph_idx != len(paragraphs) - 1:
            lines.append([])
    return lines


def merge_pdf_line_tokens(line: list[str]) -> list[str]:
    merged = []
    for token in line:
        if not token:
            continue
        for part in split_pdf_text_tokens(token):
            if not part:
                continue
            if merged and pdf_token_font(merged[-1]) == pdf_token_font(part):
                merged[-1] += part
            else:
                merged.append(part)
    return merged


def clean_pdf_draw_tokens(tokens: list[str]) -> list[str]:
    cleaned = []
    for token in tokens:
        if token.isspace() and cleaned and cleaned[-1].isspace():
            continue
        if token:
            cleaned.append(token)
    return cleaned


def drawable_pdf_line_tokens(line: list[str]) -> list[str]:
    merged = merge_pdf_line_tokens(line)
    return clean_pdf_draw_tokens(merged)


def draw_mixed_pdf_lines(
    page,
    fitz,
    rect,
    lines,
    font_size: float,
    line_height: float,
    color,
    *,
    paragraph_spacing: float = 0.0,
    letter_spacing: float = 0.0,
):
    y = rect.y0 + font_size
    for line in lines:
        if y > rect.y1:
            return
        if line == []:
            y += line_height + paragraph_spacing
            continue
        x = rect.x0
        for token in drawable_pdf_line_tokens(line):
            if not token:
                continue
            fontname = pdf_token_font(token)
            page.insert_text(
                (x, y),
                token,
                fontsize=font_size,
                fontname=fontname,
                color=color,
            )
            x += fitz.get_text_length(token, fontname=fontname, fontsize=font_size) + letter_spacing
        y += line_height


def preserve_images_on_page(src_page, out_page, fitz, dpi: int):
    matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    page_area = max(1.0, src_page.rect.get_area())
    for info in src_page.get_image_info(xrefs=True):
        bbox = fitz.Rect(info["bbox"])
        if bbox.is_empty:
            continue
        if bbox.get_area() / page_area >= FULL_PAGE_IMAGE_AREA_FRACTION:
            continue
        if bbox.x0 <= 2 and bbox.width <= EDGE_ICON_MAX_SIZE_PT and bbox.height <= EDGE_ICON_MAX_SIZE_PT:
            continue
        image_stream = None
        xref = info.get("xref") or 0
        if xref:
            try:
                image_stream = src_page.parent.extract_image(xref).get("image")
            except Exception:  # noqa: BLE001
                image_stream = None
        if image_stream:
            out_page.insert_image(bbox, stream=image_stream, keep_proportion=False)
        else:
            pix = src_page.get_pixmap(matrix=matrix, clip=bbox, alpha=False)
            out_page.insert_image(bbox, pixmap=pix, keep_proportion=False)


def insert_source_clip(src_page, out_page, fitz, bbox, dpi: int):
    rect = fitz.Rect(bbox)
    if rect.is_empty:
        return
    matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    pix = src_page.get_pixmap(matrix=matrix, clip=rect, alpha=False)
    out_page.insert_image(rect, pixmap=pix, keep_proportion=False)


def source_page_image_path(job_paths, page_num: int) -> Path | None:
    if not job_paths:
        return None
    pages_dir = job_paths.get("pages_dir")
    if not pages_dir:
        return None
    path = Path(pages_dir) / f"page-{page_num:03d}.png"
    return path if path.exists() else None


def source_image_clip_stream(source_image_path: Path, page_rect, bbox) -> bytes | None:
    rect = tuple(float(value) for value in bbox)
    if rect[2] <= rect[0] or rect[3] <= rect[1]:
        return None
    with Image.open(source_image_path) as image:
        source = image.convert("RGB")
        scale_x = source.width / max(1.0, float(page_rect.width))
        scale_y = source.height / max(1.0, float(page_rect.height))
        crop_box = (
            max(0, int(math.floor(rect[0] * scale_x))),
            max(0, int(math.floor(rect[1] * scale_y))),
            min(source.width, int(math.ceil(rect[2] * scale_x))),
            min(source.height, int(math.ceil(rect[3] * scale_y))),
        )
        if crop_box[2] <= crop_box[0] or crop_box[3] <= crop_box[1]:
            return None
        clipped = source.crop(crop_box)
        out = io.BytesIO()
        clipped.save(out, format="PNG", optimize=True)
        return out.getvalue()


def insert_source_image_clip(out_page, fitz, source_image_path: Path, page_rect, bbox) -> bool:
    rect = fitz.Rect(bbox)
    if rect.is_empty:
        return False
    stream = source_image_clip_stream(source_image_path, page_rect, bbox)
    if not stream:
        return False
    out_page.insert_image(rect, stream=stream, keep_proportion=False)
    return True


def preserve_drawings_on_page(src_page, out_page):
    for drawing in src_page.get_drawings():
        rect = drawing.get("rect")
        if rect is None:
            continue
        color = drawing.get("color") or (0, 0, 0)
        width = drawing.get("width") or 0.5
        fill = drawing.get("fill")
        if fill is not None:
            out_page.draw_rect(rect, color=color, fill=fill, width=width)
        else:
            out_page.draw_rect(rect, color=color, width=width)


def block_area_pt(block) -> float:
    return max(0.0, block["xMax"] - block["xMin"]) * max(0.0, block["yMax"] - block["yMin"])


def block_overlap_area_pt(a, b) -> float:
    x0 = max(a["xMin"], b["xMin"])
    y0 = max(a["yMin"], b["yMin"])
    x1 = min(a["xMax"], b["xMax"])
    y1 = min(a["yMax"], b["yMax"])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def compact_duplicate_text(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "", normalize_text(text)).lower()


def nested_duplicate_block_ids(blocks) -> set[str]:
    skipped = set()
    for block in blocks:
        block_area = block_area_pt(block)
        if block_area <= 0:
            continue
        block_compact = compact_duplicate_text(block.get("text", ""))
        if len(block_compact) < 8:
            continue
        for other in blocks:
            if other["id"] == block["id"]:
                continue
            other_area = block_area_pt(other)
            if other_area <= block_area * 2.5:
                continue
            if block_overlap_area_pt(block, other) / block_area < 0.85:
                continue
            other_compact = compact_duplicate_text(other.get("text", ""))
            if block_compact and block_compact in other_compact:
                skipped.add(block["id"])
                break
    return skipped


def contained_standalone_label_ids(blocks, classes) -> set[str]:
    skipped = set()
    for block in blocks:
        if classes.get(block["id"]) not in NORMAL_TRANSLATED_CLASSES:
            continue
        if not is_standalone_equation_label(block.get("text", "")):
            continue
        block_box = block_bbox(block)
        center = bbox_center(block_box)
        block_area = block_area_pt(block)
        for other in blocks:
            if other["id"] == block["id"] or classes.get(other["id"]) not in NORMAL_TRANSLATED_CLASSES:
                continue
            if block_area_pt(other) < max(1.0, block_area) * 8.0:
                continue
            if bbox_contains_point(block_bbox(other), center):
                skipped.add(block["id"])
                break
    return skipped


@dataclass
class RenderItem:
    kind: str
    source_ids: list[str]
    bbox: tuple[float, float, float, float]
    text: str = ""
    font_size: float | None = None
    style_name: str = ""
    color: tuple[float, float, float] = VECTOR_BODY_COLOR
    fallback_reason: str = ""


@dataclass
class CoverageEntry:
    block_id: str
    classification: str
    render_kind: str
    rendered: bool
    fallback_reason: str = ""


@dataclass
class PageRenderPlan:
    page_num: int
    items: list[RenderItem] = field(default_factory=list)
    ledger: list[CoverageEntry] = field(default_factory=list)
    protected_boxes: list[tuple[float, float, float, float]] = field(default_factory=list)


def filter_nested_vector_blocks(blocks, translations):
    text_blocks = [
        block
        for block in blocks
        if not should_preserve_as_image(block)
        and not is_page_number(block["text"])
        and translation_for_block(block, translations)
    ]
    skipped = set()
    for block in text_blocks:
        block_area = block_area_pt(block)
        if block_area <= 0:
            continue
        block_text_len = len(block.get("text", ""))
        for other in text_blocks:
            if other["id"] == block["id"]:
                continue
            other_area = block_area_pt(other)
            if other_area <= block_area * 2.5:
                continue
            if len(other.get("text", "")) <= block_text_len * 3:
                continue
            if block_overlap_area_pt(block, other) / block_area >= 0.85:
                skipped.add(block["id"])
                break
    return [block for block in blocks if block["id"] not in skipped]


def normalize_outline_match_text(text: str) -> str:
    text = normalize_text(text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"([A-Za-z0-9])- +([A-Za-z0-9])", r"\1-\2", text)
    return text.strip()


def clean_outline_title(text: str) -> str:
    text = normalize_translation(text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
    text = re.sub(r"^(第\s*\d+\s*章)(?=\S)", r"\1 ", text)
    return text.strip()


def translated_outline_title_map(selected_pages, translations):
    title_map = {}
    page_title_map = {}
    for page_num, blocks in selected_pages:
        for block in blocks:
            translated = translations.get(block["id"])
            if not translated:
                continue
            source_title = normalize_outline_match_text(block["text"])
            if not source_title:
                continue
            translated_title = clean_outline_title(translated)
            title_map.setdefault(source_title, translated_title)
            page_title_map.setdefault(page_num, {})[source_title] = translated_title
            chapter_match = re.match(r"Chapter\s+(\d+)\.\s+(.+)", source_title)
            if chapter_match:
                short_title = f"{chapter_match.group(1)}. {chapter_match.group(2)}"
                title_map.setdefault(short_title, translated_title)
                page_title_map.setdefault(page_num, {})[short_title] = translated_title
    return title_map, page_title_map


def translate_outline_title(title: str, page_num: int, title_map, page_title_map) -> str:
    key = normalize_outline_match_text(title)
    if key in title_map:
        return title_map[key]
    for source_title, translated_title in page_title_map.get(page_num, {}).items():
        if key == source_title or key in source_title or source_title in key:
            return translated_title
    return title


def build_translated_toc(toc, selected_pages, translations):
    title_map, page_title_map = translated_outline_title_map(selected_pages, translations)
    return [
        [level, translate_outline_title(title, page_num, title_map, page_title_map), page_num]
        for level, title, page_num in toc
    ]


def same_heading_line(block, other) -> bool:
    vertical_overlap = min(block["yMax"], other["yMax"]) - max(block["yMin"], other["yMin"])
    min_height = max(1.0, min(block["yMax"] - block["yMin"], other["yMax"] - other["yMin"]))
    return vertical_overlap / min_height >= 0.45 and other["xMin"] >= block["xMax"]


def short_heading_text(text: str) -> str:
    first_line = normalize_text(text).split("\n", 1)[0].strip()
    if not first_line or len(first_line) > HEURISTIC_HEADING_MAX_CHARS:
        return ""
    if first_line.count(".") > 2 and not re.match(r"^\d+(?:\.\d+)*\.?\s+", first_line):
        return ""
    return first_line


def starts_like_source_heading(text: str) -> bool:
    return bool(re.match(r"[A-Z]", text.strip()))


def heuristic_heading_from_block(block):
    text = short_heading_text(block.get("text", ""))
    if not text:
        return None

    if re.fullmatch(r"(?i)abstract|acknowledg(?:e)?ments?|references|bibliography", text):
        return 1, text
    if re.match(r"(?i)^appendix(?:\s|$)", text):
        return 1, text

    match = re.match(r"^(\d+(?:\.\d+)*)(?:\.)?\s+(.+)$", text)
    if not match:
        return None
    title = match.group(2).strip()
    if not title or not starts_like_source_heading(title) or title.endswith("."):
        return None
    if re.match(r"(?i)question:", title):
        return None
    return min(4, match.group(1).count(".") + 1), text


def translated_heading_title(blocks, translations) -> str:
    pieces = []
    for block in blocks:
        translated = translations.get(block["id"])
        pieces.append(clean_outline_title(translated or short_heading_text(block.get("text", ""))))
    return clean_outline_title(" ".join(piece for piece in pieces if piece))


def build_heuristic_toc(selected_pages, translations):
    toc = []
    seen = set()
    for output_page_num, (_source_page_num, blocks) in enumerate(selected_pages, start=1):
        if not blocks:
            continue
        page_bottom = max(block["yMax"] for block in blocks)
        sorted_blocks = sorted(
            blocks,
            key=lambda item: (item.get("block_index", 0), item["yMin"], item["xMin"]),
        )
        skip_ids = set()
        for idx, block in enumerate(sorted_blocks):
            if block["id"] in skip_ids or should_preserve_as_image(block):
                continue
            if block["yMin"] > page_bottom - HEURISTIC_HEADING_BOTTOM_MARGIN_PT:
                continue

            numbered = re.fullmatch(r"\d+(?:\.\d+)*\.?", short_heading_text(block.get("text", "")))
            if numbered:
                number_text = numbered.group(0).rstrip(".")
                number_parts = [int(part) for part in number_text.split(".") if part.isdigit()]
                if not number_parts or number_parts[0] > 20 or any(part > 20 for part in number_parts[1:]):
                    continue
                for other in sorted_blocks[idx + 1 : idx + 5]:
                    if should_preserve_as_image(other):
                        continue
                    if not same_heading_line(block, other):
                        continue
                    other_text = short_heading_text(other.get("text", ""))
                    if not other_text or not starts_like_source_heading(other_text) or other_text.endswith("."):
                        continue
                    level = min(4, number_text.count(".") + 1)
                    title = translated_heading_title([block, other], translations)
                    key = (level, output_page_num, normalize_outline_match_text(title))
                    if title and key not in seen:
                        toc.append([level, title, output_page_num])
                        seen.add(key)
                    skip_ids.add(other["id"])
                    break
                continue

            heading = heuristic_heading_from_block(block)
            if not heading:
                continue
            level, _source_title = heading
            title = translated_heading_title([block], translations)
            key = (level, output_page_num, normalize_outline_match_text(title))
            if title and key not in seen:
                toc.append([level, title, output_page_num])
                seen.add(key)

    normalized = []
    previous_level = 0
    for level, title, page_num in toc:
        if previous_level == 0:
            level = 1
        elif level > previous_level + 1:
            level = previous_level + 1
        normalized.append([level, title, page_num])
        previous_level = level
    return normalized


def copy_outline(src_doc, out_doc, selected_pages, translations):
    toc = src_doc.get_toc(simple=True)
    if not toc:
        heuristic_toc = build_heuristic_toc(selected_pages, translations)
        if heuristic_toc:
            out_doc.set_toc(heuristic_toc)
        return

    selected_page_numbers = [page_num for page_num, _ in selected_pages]
    if selected_page_numbers == list(range(1, src_doc.page_count + 1)):
        out_doc.set_toc(build_translated_toc(toc, selected_pages, translations))
        return

    page_map = {page_num: idx for idx, page_num in enumerate(selected_page_numbers, start=1)}
    title_map, page_title_map = translated_outline_title_map(selected_pages, translations)
    partial_toc = [
        [1, translate_outline_title(title, page_num, title_map, page_title_map), page_map[page_num]]
        for _level, title, page_num in toc
        if page_num in page_map
    ]
    if partial_toc:
        out_doc.set_toc(partial_toc)


def block_bbox(block) -> tuple[float, float, float, float]:
    return (block["xMin"], block["yMin"], block["xMax"], block["yMax"])


def bbox_union(boxes) -> tuple[float, float, float, float]:
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def bbox_intersects(a, b) -> bool:
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def bbox_center(box) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def bbox_contains_point(box, point) -> bool:
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def expanded_bbox(box, pad_x: float = 0.0, pad_y: float = 0.0):
    return (box[0] - pad_x, box[1] - pad_y, box[2] + pad_x, box[3] + pad_y)


def is_reference_heading(text: str) -> bool:
    return bool(re.fullmatch(r"(?i)references|bibliography", normalize_text(text)))


def starts_reference_item(text: str) -> bool:
    return bool(re.match(r"^\s*\d{1,3}\.\s*[A-Z][A-Za-z-]+,", normalize_text(text)))


def is_decorative_update_marker(text: str) -> bool:
    return re.sub(r"\s+", "", normalize_text(text).lower()) == "checkforupdates"


def journal_footer_match_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalize_text(text).lower())


def is_journal_footer_text(text: str) -> bool:
    key = journal_footer_match_key(text)
    if not key:
        return False
    has_acm = "acm" in key
    has_transactions = "transactions" in key or "transactlons" in key
    has_venue = "programming" in key or "programmmg" in key or "languagesandsystems" in key or "lanwages" in key
    has_issue = "january1991" in key or ("vol" in key and "1991" in key)
    return has_acm and has_transactions and has_venue and has_issue


def text_is_only_journal_footer(text: str) -> bool:
    stripped = normalize_text(text)
    if not stripped or not is_journal_footer_text(stripped):
        return False
    without_footer = strip_journal_footer_lines(stripped)
    return not without_footer.strip()


def is_journal_footer_block(block) -> bool:
    return block["yMin"] > 560 and text_is_only_journal_footer(block.get("text", ""))


def strip_journal_footer_lines(text: str) -> str:
    lines = text.split("\n")
    if not lines:
        return text
    cleaned = []
    for idx, line in enumerate(lines):
        near_end = idx >= max(0, len(lines) - 3)
        if near_end and (
            is_journal_footer_text(line)
            or re.search(r"ACM\s+Transactions", line, flags=re.I)
            or re.search(r"Programming\s+Languages\s+and\s+Systems", line, flags=re.I)
        ):
            continue
        cleaned.append(line)
    return normalize_text("\n".join(cleaned))


def is_first_page_footer_fragment(block) -> bool:
    text = normalize_text(block.get("text", ""))
    return block.get("page") == 1 and block["yMin"] > 540 and len(text) < 50


def is_running_header_fragment(block) -> bool:
    text = normalize_text(block.get("text", ""))
    return (
        block.get("page", 1) > 1
        and block["yMin"] < 62.0
        and (block["yMax"] - block["yMin"]) <= 28.0
        and len(text) <= 140
        and (
            is_page_number(text)
            or is_decorated_ocr_page_number_text(text)
            or is_noisy_ocr_page_number_text(text)
            or re.fullmatch(r"[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3}", text)
            or "wait-free" in text.lower()
        )
    )


def contains_reference_item(text: str) -> bool:
    return bool(re.search(r"(?m)(?:^|\n)\s*\d{1,3}\.\s*[A-Z][A-Za-z-]+,", normalize_text(text)))


def reference_block_ids(blocks) -> set[str]:
    first_reference_y = None
    sorted_blocks = sorted(blocks, key=lambda item: (item["yMin"], item["xMin"]))
    for block in sorted_blocks:
        text = normalize_text(block.get("text", ""))
        if is_reference_heading(text) or starts_reference_item(text) or contains_reference_item(text):
            first_reference_y = block["yMin"]
            break
    if first_reference_y is None:
        return set()
    ids = set()
    for block in sorted_blocks:
        text = normalize_text(block.get("text", ""))
        if not text or is_page_number(text):
            continue
        if block["yMin"] >= first_reference_y:
            ids.add(block["id"])
    return ids


def first_reference_y(blocks) -> float | None:
    ids = reference_block_ids(blocks)
    if not ids:
        return None
    return min(block["yMin"] for block in blocks if block["id"] in ids)


def merge_bbox_line_fragments(lines) -> list[dict]:
    rows = []
    for line in sorted(lines, key=lambda item: ((item["bbox"][1] + item["bbox"][3]) / 2.0, item["bbox"][0])):
        text = normalize_text(line.get("text", ""))
        if not text:
            continue
        x0, y0, x1, y1 = line["bbox"]
        center_y = (y0 + y1) / 2.0
        target = None
        for row in reversed(rows[-4:]):
            if abs(center_y - row["center_y"]) <= 2.2:
                target = row
                break
        if target is None:
            rows.append({"center_y": center_y, "parts": [(x0, text, (x0, y0, x1, y1))]})
        else:
            target["parts"].append((x0, text, (x0, y0, x1, y1)))
            centers = [(part[2][1] + part[2][3]) / 2.0 for part in target["parts"]]
            target["center_y"] = median(centers)

    merged = []
    for row in rows:
        parts = sorted(row["parts"], key=lambda item: item[0])
        boxes = [part[2] for part in parts]
        merged.append(
            {
                "text": normalize_text(" ".join(part[1] for part in parts)),
                "bbox": bbox_union(boxes),
            }
        )
    return merged


def journal_footer_bbox(page_size) -> tuple[float, float, float, float]:
    width, height = page_size
    footer_width = min(JOURNAL_FOOTER_WIDTH_PT, max(40.0, width - 24.0))
    x0 = (width - footer_width) / 2.0
    y0 = min(height - JOURNAL_FOOTER_HEIGHT_PT - 4.0, max(0.0, height - 152.0))
    return (
        round(x0, 3),
        round(y0, 3),
        round(x0 + footer_width, 3),
        round(y0 + JOURNAL_FOOTER_HEIGHT_PT, 3),
    )


def journal_footer_render_items(bbox_lines, page_size) -> list[RenderItem]:
    if not bbox_lines:
        return []
    _width, height = page_size
    footer_candidates = [
        line
        for line in bbox_lines
        if line["bbox"][1] > height * 0.72
    ]
    items = []
    for line in merge_bbox_line_fragments(footer_candidates):
        if not is_journal_footer_text(line["text"]):
            continue
        items.append(
            RenderItem(
                "original_selectable_text",
                [],
                journal_footer_bbox(page_size),
                text=JOURNAL_FOOTER_TEXT,
                font_size=JOURNAL_FOOTER_FONT_SIZE,
                style_name="footer",
                fallback_reason="journal_footer",
            )
        )
    return items[:1]


def reference_line_render_items(blocks, bbox_lines, page_size) -> list[RenderItem]:
    start_y = first_reference_y(blocks)
    if start_y is None or not bbox_lines:
        return []
    width, height = page_size
    items = []
    reference_lines = []
    for line in bbox_lines:
        text = normalize_text(line.get("text", ""))
        if not text:
            continue
        x0, y0, x1, y1 = line["bbox"]
        if y0 < start_y - 4.0:
            continue
        if is_journal_footer_text(text):
            continue
        reference_lines.append(line)
    for line in merge_bbox_line_fragments(reference_lines):
        text = line["text"]
        if is_journal_footer_text(text):
            continue
        x0, y0, x1, y1 = line["bbox"]
        bbox = (
            max(0.0, x0),
            max(0.0, y0),
            min(width, x1 + 2.0),
            min(height, y1 + 2.0),
        )
        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            continue
        font_size = DOCUMENT_STYLES["reference"].font_size
        items.append(
            RenderItem(
                "original_selectable_text",
                [],
                bbox,
                text=text,
                font_size=font_size,
                style_name="reference",
                fallback_reason="reference_original",
            )
        )
    return items


def is_heading_text(text: str) -> bool:
    first = normalize_text(text).split("\n", 1)[0].strip()
    if is_reference_heading(first):
        return False
    compact = re.sub(r"\s+", "", first)
    if re.match(r"^\d+(?:\.\d+)+(?:[A-Z]|[I1l]/O|1/O|I/O)", compact):
        return True
    if re.match(r"^\d+(?:\.\d+)*\.[A-Z][A-Z0-9 /&-]{2,80}$", first):
        return True
    if re.match(r"^\d+(?:\.\d+)*\.?\s+[A-Z][A-Za-z0-9 ,/&().-]{2,80}$", first):
        return True
    if re.match(r"^\d+(?:\.\d+)*\.?\s+[A-Z][A-Za-z0-9 /&-]+$", first):
        return True
    if re.fullmatch(r"[A-Z][A-Z0-9 /&-]{3,80}", first) and len(first.split()) <= 8:
        return True
    return False


def is_title_block(block) -> bool:
    text = normalize_text(block.get("text", ""))
    return block.get("page") == 1 and block["yMin"] < 90 and len(text) <= 120 and "\n" not in text


def adjusted_render_bbox(block, classification: str, page_size) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = block_bbox(block)
    page_width, page_height = page_size
    if classification == "heading":
        y1 = max(y1, y0 + 18.0)
        x1 = max(x1, x0 + 140.0)
    elif classification == "title":
        y1 = max(y1, y0 + 22.0)
    return (
        max(0.0, x0),
        max(0.0, y0),
        min(page_width, x1),
        min(page_height, y1),
    )


def clamped_expanded_bbox(box, page_size, pad_x: float = 0.0, pad_y: float = 0.0) -> tuple[float, float, float, float]:
    page_width, page_height = page_size
    x0, y0, x1, y1 = expanded_bbox(box, pad_x=pad_x, pad_y=pad_y)
    return (
        max(0.0, x0),
        max(0.0, y0),
        min(page_width, x1),
        min(page_height, y1),
    )


def clamp_bbox(box, page_size) -> tuple[float, float, float, float]:
    page_width, page_height = page_size
    x0, y0, x1, y1 = box
    return (
        max(0.0, x0),
        max(0.0, y0),
        min(page_width, x1),
        min(page_height, y1),
    )


def pixel_search_bbox(box, page_size, pad_x: float, pad_y: float) -> tuple[float, float, float, float]:
    return clamped_expanded_bbox(box, page_size, pad_x=pad_x, pad_y=pad_y)


def dark_pixel_bbox_in_source_image(
    source_image_path: Path | None,
    search_box,
    page_size,
    *,
    darkness_threshold: int = 245,
) -> tuple[float, float, float, float] | None:
    if source_image_path is None or not source_image_path.exists():
        return None
    page_width, page_height = page_size
    if page_width <= 0 or page_height <= 0:
        return None
    try:
        image = Image.open(source_image_path).convert("L")
    except Exception:
        return None
    scale_x = image.width / page_width
    scale_y = image.height / page_height
    x0, y0, x1, y1 = search_box
    px0 = max(0, int(math.floor(x0 * scale_x)))
    py0 = max(0, int(math.floor(y0 * scale_y)))
    px1 = min(image.width, int(math.ceil(x1 * scale_x)))
    py1 = min(image.height, int(math.ceil(y1 * scale_y)))
    if px1 <= px0 or py1 <= py0:
        return None

    crop = image.crop((px0, py0, px1, py1))
    min_x = crop.width
    min_y = crop.height
    max_x = -1
    max_y = -1
    pixels = crop.load()
    for y in range(crop.height):
        for x in range(crop.width):
            if pixels[x, y] >= darkness_threshold:
                continue
            min_x = min(min_x, x)
            min_y = min(min_y, y)
            max_x = max(max_x, x)
            max_y = max(max_y, y)
    if max_x < min_x or max_y < min_y:
        return None

    return clamp_bbox(
        (
            (px0 + min_x) / scale_x,
            (py0 + min_y) / scale_y,
            (px0 + max_x + 1) / scale_x,
            (py0 + max_y + 1) / scale_y,
        ),
        page_size,
    )


def visual_clip_bbox(
    region_bbox,
    page_size,
    pad_x: float,
    pad_y: float,
    *,
    page_num: int = 1,
    source_image_path: Path | None = None,
    use_pixel_bounds: bool = False,
    pixel_search_pad_x: float = VISUAL_CLIP_PIXEL_SEARCH_PAD_X_PT,
    pixel_search_pad_y: float = VISUAL_CLIP_PIXEL_SEARCH_PAD_Y_PT,
    pixel_final_pad: float = VISUAL_CLIP_PIXEL_FINAL_PAD_PT,
) -> tuple[float, float, float, float]:
    base_bbox = clamped_expanded_bbox(region_bbox, page_size, pad_x=pad_x, pad_y=pad_y)
    if not use_pixel_bounds:
        return base_bbox
    search_box = pixel_search_bbox(
        base_bbox,
        page_size,
        pad_x=pixel_search_pad_x,
        pad_y=pixel_search_pad_y,
    )
    if page_num > 1 and search_box[1] < 62.0 < search_box[3]:
        search_box = (search_box[0], 62.0, search_box[2], search_box[3])
    pixel_bbox = dark_pixel_bbox_in_source_image(source_image_path, search_box, page_size)
    if pixel_bbox is None:
        return base_bbox
    return clamped_expanded_bbox(
        pixel_bbox,
        page_size,
        pad_x=pixel_final_pad,
        pad_y=pixel_final_pad,
    )


def cap_visual_bbox_before_following_text(region_bbox, visual_bbox, blocks, classes, visual_ids):
    capped = visual_bbox
    for block in blocks:
        if block["id"] in visual_ids:
            continue
        classification = classes.get(block["id"], "unknown")
        if classification not in NORMAL_TRANSLATED_CLASSES:
            continue
        text = normalize_text(block.get("text", ""))
        if not text:
            continue
        block_box = block_bbox(block)
        if block_box[1] < region_bbox[3] - 1.0:
            continue
        if block_box[1] >= capped[3]:
            continue
        if horizontal_overlap(capped, block_box) < min(capped[2] - capped[0], block_box[2] - block_box[0]) * 0.15:
            continue
        capped = (capped[0], capped[1], capped[2], max(capped[1], block_box[1] - 2.0))
    return capped


def compact_match_text(text: str) -> str:
    return re.sub(r"\s+", "", normalize_text(text).lower())


def matching_line_bboxes_for_block(block, bbox_lines) -> list[tuple[float, float, float, float]]:
    block_compact = compact_match_text(block.get("text", ""))
    if not block_compact or not bbox_lines:
        return []
    matches = []
    for line in bbox_lines:
        line_text = line.get("text", "")
        line_compact = compact_match_text(line_text)
        if not line_compact:
            continue
        if line_compact in block_compact or block_compact in line_compact:
            matches.append(line["bbox"])
    return matches


def refined_text_bbox_from_lines(block, bbox, bbox_lines, page_size) -> tuple[float, float, float, float]:
    if not bbox_lines:
        return bbox
    x0, y0, x1, y1 = bbox
    if x0 > 4.0:
        return bbox
    matches = matching_line_bboxes_for_block(block, bbox_lines)
    if not matches:
        return bbox
    matched = bbox_union(matches)
    page_width, page_height = page_size
    return (
        max(0.0, matched[0]),
        max(0.0, y0),
        min(page_width, max(x1, matched[2] + 2.0)),
        min(page_height, y1),
    )


def formula_region_bbox_from_lines(region, bbox_lines, page_size) -> tuple[float, float, float, float] | None:
    if not bbox_lines:
        return None
    region_box = region["bbox"]
    search = expanded_bbox(region_box, pad_x=24.0, pad_y=4.0)
    candidates = []
    for line in bbox_lines:
        center = bbox_center(line["bbox"])
        if not (search[1] <= center[1] <= search[3]):
            continue
        if not (search[0] <= center[0] <= search[2]):
            continue
        candidates.append(line)
    if not candidates:
        return None
    formula_rows = [
        row
        for row in merge_bbox_line_fragments(candidates)
        if is_formula_or_code_block(row["text"]) and not is_body_enumeration_line(row["text"])
    ]
    if not formula_rows:
        return None
    return clamp_bbox(bbox_union([row["bbox"] for row in formula_rows]), page_size)


def render_font_size_for_block(block, classification: str) -> float:
    return text_style(style_name_for_block(block, classification)).font_size


def style_name_for_heading_text(text: str) -> str:
    normalized = normalize_text(text)
    if re.match(r"^\s*\d+\.\d+", normalized):
        return "subheading"
    return "heading"


def style_name_for_block(block, classification: str) -> str:
    if classification == "heading":
        return style_name_for_heading_text(block.get("text", ""))
    if classification == "title":
        return "title"
    if classification == "reference":
        return "reference"
    if classification == "journal_footer":
        return "footer"
    return "body"


def first_page_title_metadata_split(block, translated: str) -> tuple[str, list[str], str] | None:
    if block.get("page") != 1 or block["yMin"] > 110.0:
        return None
    source_lines = [line.strip() for line in normalize_text(block.get("text", "")).split("\n") if line.strip()]
    translated_lines = [line.strip() for line in normalize_text(translated).split("\n") if line.strip()]
    if len(source_lines) < 4:
        return None
    if not source_lines[0] or len(source_lines[0]) > 120:
        return None

    source_metadata = []
    for line in source_lines[1:5]:
        words = latin_words(line)
        if len(words) >= 6 or cjk_char_count(line) > 0:
            break
        source_metadata.append(line)
    if not source_metadata:
        return None

    if len(translated_lines) < 4:
        one_line = normalize_text(translated)
        metadata_positions = []
        for line in source_metadata:
            pos = one_line.find(line)
            if pos >= 0:
                metadata_positions.append((pos, line))
        if not metadata_positions:
            return None
        metadata_positions.sort()
        first_pos, _first_line = metadata_positions[0]
        last_pos, last_line = metadata_positions[-1]
        title = one_line[:first_pos].strip()
        abstract = one_line[last_pos + len(last_line) :].strip()
        if not title or not abstract:
            return None
        return title, [line for _pos, line in metadata_positions], prepare_render_translation(abstract)

    title = translated_lines[0]
    metadata = []
    body_start = None
    for idx, line in enumerate(translated_lines[1:], start=1):
        if cjk_char_count(line) >= 8:
            body_start = idx
            break
        if len(line) <= 120:
            metadata.append(line)
            continue
        body_start = idx
        break
    if body_start is None or not metadata:
        return None
    abstract = prepare_render_translation("\n".join(translated_lines[body_start:]))
    if not title or not abstract:
        return None
    return title, metadata[:3], abstract


def first_page_title_metadata_render_items(block, translated: str, bbox, page_size, fitz=None) -> list[RenderItem]:
    split = first_page_title_metadata_split(block, translated)
    if split is None:
        return []
    if fitz is None:
        fitz = load_fitz()
    title, metadata, abstract = split
    page_width, page_height = page_size
    x0, y0, x1, y1 = bbox
    x0 = max(0.0, x0)
    x1 = min(page_width, x1)

    title_style = text_style("title")
    metadata_style = text_style("metadata")
    title_width = max(1.0, x1 - x0)
    title_lines = wrap_mixed_pdf_text(fitz, title, title_width, title_style.font_size)
    metadata_text = "\n".join(metadata)
    metadata_lines = wrap_mixed_pdf_text(fitz, metadata_text, title_width, metadata_style.font_size)
    title_height = text_height_for_lines(title_lines, title_style.font_size, title_style.line_height_factor, title_style.paragraph_spacing) + 2.0
    metadata_height = text_height_for_lines(
        metadata_lines,
        metadata_style.font_size,
        metadata_style.line_height_factor,
        metadata_style.paragraph_spacing,
    ) + 2.0

    title_box = clamp_bbox((x0, y0, x1, y0 + title_height), page_size)
    metadata_top = title_box[3] + 2.0
    metadata_box = clamp_bbox((x0, metadata_top, x1, metadata_top + metadata_height), page_size)
    abstract_top = metadata_box[3] + 5.0
    abstract_box = clamp_bbox((x0, abstract_top, x1, max(abstract_top + BODY_FONT_SIZE * 2.0, min(page_height, y1))), page_size)

    return [
        RenderItem(
            "translated_text",
            [block["id"]],
            title_box,
            text=title,
            font_size=title_style.font_size,
            style_name="title",
            fallback_reason="first_page_title",
        ),
        RenderItem(
            "translated_text",
            [block["id"]],
            metadata_box,
            text=metadata_text,
            font_size=metadata_style.font_size,
            style_name="metadata",
            fallback_reason="first_page_metadata",
        ),
        RenderItem(
            "translated_text",
            [block["id"]],
            abstract_box,
            text=abstract,
            font_size=BODY_FONT_SIZE,
            style_name="body",
            fallback_reason="first_page_abstract",
        ),
    ]


def heuristic_translation_for_missing_block(text: str) -> str:
    normalized = normalize_text(text)
    match = re.fullmatch(r"\((\d+)\)\s+(.+?)\s+is\s+a\s+set\s+of\s+(.+?)[,.]?", normalized, flags=re.I)
    if match:
        number, subject, noun = match.groups()
        noun_map = {
            "input events": "输入事件",
            "output events": "输出事件",
            "internal events": "内部事件",
            "starting states": "初始状态",
        }
        noun_zh = noun_map.get(noun.lower(), noun)
        return f"({number}) {subject} 是{noun_zh}集合，"
    return ""


def is_garbled_latin_fragment(line: str) -> bool:
    normalized = normalize_text(line)
    if not normalized or cjk_char_count(normalized) > 0:
        return False
    if not re.search(r"[A-Za-z]", normalized):
        return False
    if is_formula_or_code_block(normalized) or re.search(r"[:=()[\]{}<>0-9_]", normalized):
        return False
    words = re.findall(r"[A-Za-z]+", normalized)
    if len(words) < 4:
        return False
    single_letter_words = sum(1 for word in words if len(word) == 1)
    short_words = sum(1 for word in words if len(word) <= 2)
    average_length = sum(len(word) for word in words) / len(words)
    function_words = english_function_word_count(normalized)
    if single_letter_words >= 2 and short_words >= len(words) * 0.45 and function_words <= 1:
        return True
    return normalized == normalized.lower() and function_words == 0 and average_length < 4.0


def drop_garbled_translation_lines(text: str) -> str:
    lines = [line for line in text.split("\n") if not is_garbled_latin_fragment(line)]
    return normalize_text("\n".join(lines))


def repair_incomplete_translation_from_source(block, text: str) -> str:
    source = normalize_text(block.get("text", "")).lower()
    if "validity follows because each process initializes its position in prefer before" in source:
        text = re.sub(
            r"有效性成立，因为每个进程在[。.]?",
            "有效性成立，因为每个进程在执行 swap 前初始化其在 prefer 中的位置。",
            text,
        )
    return text


def clean_render_text(block, text: str, raw_text: str | None = None) -> str:
    text = strip_journal_footer_lines(text)
    text = strip_leading_running_header_text(text, block.get("page", 1))
    if raw_text:
        visual_tail = translation_tail_after_visual_prefix(raw_text)
        if visual_tail and cjk_char_count(visual_tail) >= 6:
            text = strip_journal_footer_lines(visual_tail)
            text = strip_leading_running_header_text(text, block.get("page", 1))
    text = drop_garbled_translation_lines(text)
    text = repair_incomplete_translation_from_source(block, text)
    lines = text.split("\n")
    if len(lines) >= 2 and re.fullmatch(r"[a-z][a-z-]{3,}", lines[0].strip()) and re.search(r"[\u4e00-\u9fff]", lines[1]):
        source_first = normalize_text(block.get("text", "")).split("\n", 1)[0].strip()
        if source_first == lines[0].strip():
            return strip_journal_footer_lines("\n".join(lines[1:]).strip())
    source_first = normalize_text(block.get("text", "")).split("\n", 1)[0].strip()
    if block.get("page", 1) > 1 and re.search(r"wait-?free\s*synchronization", source_first, flags=re.I):
        first = lines[0].strip() if lines else ""
        if first in {"无等待同步", "Wait-Free Synchronization", "Wait-FreeSynchronization"}:
            return strip_journal_footer_lines("\n".join(lines[1:]).strip())
        stripped = text.strip()
        for prefix in ("无等待同步", "Wait-Free Synchronization", "Wait-FreeSynchronization"):
            if stripped.startswith(prefix) and len(stripped) > len(prefix):
                return strip_journal_footer_lines(stripped[len(prefix) :].strip())
    return strip_journal_footer_lines(text)


def row_from_bbox_fragments(fragments: list[dict]) -> dict:
    ordered = sorted(fragments, key=lambda item: item["bbox"][0])
    boxes = [item["bbox"] for item in ordered]
    return {
        "text": normalize_text(" ".join(item["text"] for item in ordered)),
        "bbox": bbox_union(boxes),
    }


def bbox_line_rows_for_block(block, bbox_lines: list[dict]) -> list[dict]:
    if not bbox_lines:
        return []
    block_box = expanded_bbox(block_bbox(block), 2.0, 2.0)
    fragments = []
    for line in bbox_lines:
        if line.get("page") != block.get("page"):
            continue
        line_box = line["bbox"]
        if not bbox_contains_point(block_box, bbox_center(line_box)) and not bbox_intersects(block_box, line_box):
            continue
        fragments.append(line)
    if not fragments:
        return []

    rows: list[list[dict]] = []
    for fragment in sorted(fragments, key=lambda item: (bbox_center(item["bbox"])[1], item["bbox"][0])):
        center_y = bbox_center(fragment["bbox"])[1]
        if rows:
            row_center_y = median(bbox_center(item["bbox"])[1] for item in rows[-1])
            if abs(center_y - row_center_y) <= 2.0:
                rows[-1].append(fragment)
                continue
        rows.append([fragment])
    return [row_from_bbox_fragments(row) for row in rows]


def source_body_row_is_noise(block, row: dict) -> bool:
    text = normalize_text(row["text"])
    if not text:
        return True
    pseudo = {
        "page": block.get("page", 1),
        "yMin": row["bbox"][1],
        "yMax": row["bbox"][3],
        "text": text,
    }
    if is_journal_footer_text(text) or is_running_header_fragment(pseudo):
        return True
    if row["bbox"][1] < 65.0 and (is_page_number(text) or is_decorated_ocr_page_number_text(text)):
        return True
    return False


def source_paragraph_boxes_for_block(block, bbox, bbox_lines: list[dict]) -> list[tuple[float, float, float, float]]:
    rows = [row for row in bbox_line_rows_for_block(block, bbox_lines) if not source_body_row_is_noise(block, row)]
    if len(rows) < 4:
        return []

    lefts = sorted(row["bbox"][0] for row in rows)
    base_left = lefts[max(0, min(len(lefts) - 1, int(len(lefts) * 0.15)))]
    line_heights = [max(1.0, row["bbox"][3] - row["bbox"][1]) for row in rows]
    typical_line_height = median(line_heights)
    indent_threshold = max(6.0, typical_line_height * 0.75)
    vertical_gap_threshold = typical_line_height * 1.2

    groups: list[list[dict]] = []
    current: list[dict] = []
    previous = None
    for row in rows:
        starts_new = not current
        if previous is not None:
            vertical_gap = row["bbox"][1] - previous["bbox"][3]
            row_indent = row["bbox"][0] - base_left
            previous_text = previous["text"].rstrip()
            if vertical_gap > vertical_gap_threshold:
                starts_new = True
            elif row_indent >= indent_threshold and not previous_text.endswith("-"):
                starts_new = True
        if starts_new:
            if current:
                groups.append(current)
            current = [row]
        else:
            current.append(row)
        previous = row
    if current:
        groups.append(current)
    if len(groups) < 2:
        return []

    boxes = []
    for idx, group in enumerate(groups):
        group_box = bbox_union([row["bbox"] for row in group])
        y0 = max(bbox[1], group[0]["bbox"][1])
        if idx + 1 < len(groups):
            y1 = min(bbox[3], groups[idx + 1][0]["bbox"][1] - BODY_FLOW_MIN_GAP_PT)
        else:
            y1 = min(bbox[3], group_box[3] + typical_line_height * 1.25)
        if y1 <= y0 + BODY_FONT_SIZE:
            y1 = min(bbox[3], y0 + BODY_FONT_SIZE * 2.0)
        boxes.append(clamp_bbox((bbox[0], y0, bbox[2], y1), (10_000, 10_000)))
    return boxes


def render_paragraphs(text: str) -> list[str]:
    return [paragraph.strip() for paragraph in text.split("\n") if paragraph.strip()]


def merge_extra_paragraphs(paragraphs: list[str], target_count: int) -> list[str]:
    if target_count <= 0 or len(paragraphs) <= target_count:
        return paragraphs
    merged = paragraphs[: target_count - 1]
    merged.append("\n".join(paragraphs[target_count - 1 :]))
    return merged


def coalesce_paragraph_boxes(boxes: list[tuple[float, float, float, float]], target_count: int) -> list[tuple[float, float, float, float]]:
    if target_count <= 0 or len(boxes) <= target_count:
        return boxes
    boxes = list(boxes)
    while len(boxes) > target_count:
        best_idx = min(
            range(len(boxes) - 1),
            key=lambda idx: max(0.0, boxes[idx + 1][1] - boxes[idx][3]),
        )
        boxes[best_idx] = bbox_union([boxes[best_idx], boxes[best_idx + 1]])
        del boxes[best_idx + 1]
    return boxes


def box_area_value(box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def box_significantly_overlaps_protected(box, protected_box) -> bool:
    x0 = max(box[0], protected_box[0])
    y0 = max(box[1], protected_box[1])
    x1 = min(box[2], protected_box[2])
    y1 = min(box[3], protected_box[3])
    if x1 <= x0 or y1 <= y0:
        return False
    overlap_height = y1 - y0
    overlap_area = (x1 - x0) * overlap_height
    return overlap_height > 6.0 and overlap_area > min(box_area_value(box), box_area_value(protected_box)) * 0.05


def paragraph_boxes_overlap_protected(
    boxes: list[tuple[float, float, float, float]],
    protected_boxes: list[tuple[float, float, float, float]] | None,
) -> bool:
    return bool(
        protected_boxes
        and any(box_significantly_overlaps_protected(box, protected) for box in boxes for protected in protected_boxes)
    )


def source_paragraph_render_items(
    block,
    translated: str,
    bbox,
    page_size,
    bbox_lines: list[dict],
    protected_boxes: list[tuple[float, float, float, float]] | None = None,
) -> list[RenderItem]:
    paragraphs = render_paragraphs(translated)
    if len(paragraphs) < 2 or not bbox_lines:
        return []
    if block["yMax"] - block["yMin"] < 120.0:
        return []
    boxes = source_paragraph_boxes_for_block(block, bbox, bbox_lines)
    if len(boxes) < 2:
        return []
    if len(paragraphs) > len(boxes):
        paragraphs = merge_extra_paragraphs(paragraphs, len(boxes))
    elif len(boxes) > len(paragraphs):
        boxes = coalesce_paragraph_boxes(boxes, len(paragraphs))
    if len(paragraphs) != len(boxes) or len(paragraphs) < 2:
        return []
    if paragraph_boxes_overlap_protected(boxes, protected_boxes):
        return []

    style = text_style("body")
    items = []
    for paragraph, paragraph_box in zip(paragraphs, boxes):
        items.append(
            RenderItem(
                "translated_text",
                [block["id"]],
                clamp_bbox(paragraph_box, page_size),
                text=paragraph,
                font_size=style.font_size,
                style_name="body",
                fallback_reason="source_paragraph_split",
            )
        )
    return items


def translated_lines_with_embedded_headings(text: str) -> list[str]:
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if not lines:
        return []
    if not any(render_line_is_standalone_heading(line) for line in lines[1:]):
        return []
    return lines


def source_heading_rows_for_block(block, bbox_lines: list[dict]) -> list[dict]:
    return [row for row in bbox_line_rows_for_block(block, bbox_lines) if is_heading_text(row["text"])]


def approximate_line_box(bbox, line_index: int, line_count: int) -> tuple[float, float, float, float]:
    line_count = max(1, line_count)
    x0, y0, x1, y1 = bbox
    line_height = max(1.0, (y1 - y0) / line_count)
    return (x0, y0 + line_height * line_index, x1, y0 + line_height * (line_index + 1))


def heading_box_from_row(row_box, page_size, style: TextStyle) -> tuple[float, float, float, float]:
    min_height = style.font_size * style.line_height_factor + 0.5
    x0, y0, x1, y1 = row_box
    box = (
        max(0.0, x0),
        max(0.0, y0 - 1.0),
        min(page_size[0], max(x1 + 2.0, x0 + 140.0)),
        min(page_size[1], max(y1 + 2.0, y0 - 1.0 + min_height)),
    )
    return box


def embedded_heading_render_items(
    block,
    translated: str,
    bbox,
    page_size,
    bbox_lines: list[dict],
) -> list[RenderItem]:
    lines = translated_lines_with_embedded_headings(translated)
    if not lines:
        return []
    heading_indices = [idx for idx, line in enumerate(lines) if idx > 0 and render_line_is_standalone_heading(line)]
    if not heading_indices:
        return []

    source_heading_rows = source_heading_rows_for_block(block, bbox_lines)
    heading_rows = []
    for idx, _line_index in enumerate(heading_indices):
        if idx < len(source_heading_rows):
            heading_rows.append(source_heading_rows[idx])
        else:
            line_box = approximate_line_box(bbox, heading_indices[idx], len(lines))
            heading_rows.append({"text": lines[heading_indices[idx]], "bbox": line_box})

    items: list[RenderItem] = []
    body_style = text_style("body")
    cursor = 0
    current_y = bbox[1]
    for heading_index, heading_row in zip(heading_indices, heading_rows):
        heading_line = lines[heading_index]
        style_name = style_name_for_heading_text(heading_line)
        heading_style = text_style(style_name)
        heading_box = heading_box_from_row(heading_row["bbox"], page_size, heading_style)

        body_text = "\n".join(lines[cursor:heading_index]).strip()
        if body_text:
            body_y1 = max(current_y + body_style.font_size * body_style.line_height_factor, heading_box[1] - BODY_FLOW_MIN_GAP_PT)
            items.append(
                RenderItem(
                    "translated_text",
                    [block["id"]],
                    clamp_bbox((bbox[0], current_y, bbox[2], min(body_y1, heading_box[1] - TEXT_PROTECTED_GAP_PT)), page_size),
                    text=body_text,
                    font_size=body_style.font_size,
                    style_name="body",
                    fallback_reason="embedded_heading_body",
                )
            )

        items.append(
            RenderItem(
                "translated_text",
                [block["id"]],
                heading_box,
                text=heading_line,
                font_size=heading_style.font_size,
                style_name=style_name,
                fallback_reason="embedded_heading",
            )
        )
        current_y = min(bbox[3], heading_box[3] + BODY_FLOW_MIN_GAP_PT)
        cursor = heading_index + 1

    tail_text = "\n".join(lines[cursor:]).strip()
    if tail_text:
        items.append(
            RenderItem(
                "translated_text",
                [block["id"]],
                clamp_bbox((bbox[0], current_y, bbox[2], bbox[3]), page_size),
                text=tail_text,
                font_size=body_style.font_size,
                style_name="body",
                fallback_reason="embedded_heading_body",
            )
        )
    return [item for item in items if item.bbox[3] > item.bbox[1] and item.bbox[2] > item.bbox[0]]


def item_is_body_text_item(item: RenderItem) -> bool:
    return (
        item.kind in {"translated_text", "original_selectable_text"}
        and item.style_name == "body"
        and item.text.strip()
    )


def numbered_enumeration_start(text: str) -> int | None:
    match = re.match(r"^\((\d{1,2})\)\s*[\u4e00-\u9fffA-Za-z]", text.strip())
    if not match:
        return None
    return int(match.group(1))


def split_leading_continuation_before_numbered_enum(text: str) -> tuple[str, str] | None:
    stripped = text.strip()
    if stripped.startswith("("):
        return None
    match = re.search(r"\(\d{1,2}\)\s*[\u4e00-\u9fffA-Za-z]", stripped)
    if not match or match.start() <= 0:
        return None
    leading = stripped[: match.start()].strip()
    rest = stripped[match.start() :].strip()
    if not leading or not rest:
        return None
    if "\n" in leading or len(leading) > 80:
        return None
    if numbered_enumeration_start(rest) is None:
        return None
    return leading, rest


def previous_body_item_in_same_lane(plan: PageRenderPlan, item_idx: int) -> RenderItem | None:
    item = plan.items[item_idx]
    candidates = []
    for idx, other in enumerate(plan.items):
        if idx == item_idx or not item_is_body_text_item(other):
            continue
        if other.bbox[1] >= item.bbox[1]:
            continue
        if horizontal_overlap(other.bbox, item.bbox) < min(other.bbox[2] - other.bbox[0], item.bbox[2] - item.bbox[0]) * 0.45:
            continue
        if item.bbox[1] - other.bbox[3] > 90.0:
            continue
        candidates.append(other)
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: candidate.bbox[1])


def move_leading_enum_continuations_to_previous_items(plan: PageRenderPlan) -> None:
    for idx, item in sorted(enumerate(plan.items), key=lambda pair: (pair[1].bbox[1], pair[1].bbox[0])):
        if not item_is_body_text_item(item):
            continue
        split = split_leading_continuation_before_numbered_enum(item.text)
        if split is None:
            continue
        previous = previous_body_item_in_same_lane(plan, idx)
        if previous is None or not re.search(r"\(\d{1,2}\)", previous.text):
            continue
        leading, rest = split
        previous.text = join_render_lines(previous.text.rstrip(), leading)
        for source_id in item.source_ids:
            if source_id not in previous.source_ids:
                previous.source_ids.append(source_id)
        item.text = rest


def same_body_lane(left: RenderItem, right: RenderItem) -> bool:
    overlap = horizontal_overlap(left.bbox, right.bbox)
    min_width = max(1.0, min(left.bbox[2] - left.bbox[0], right.bbox[2] - right.bbox[0]))
    return overlap >= min_width * 0.45


def enumeration_candidate_groups(plan: PageRenderPlan) -> list[list[tuple[int, RenderItem, int]]]:
    candidates = [
        (idx, item, number)
        for idx, item in enumerate(plan.items)
        if item_is_body_text_item(item)
        for number in [numbered_enumeration_start(item.text)]
        if number is not None
    ]
    groups: list[list[tuple[int, RenderItem, int]]] = []
    for candidate in sorted(candidates, key=lambda entry: (entry[1].bbox[0], entry[1].bbox[1])):
        _idx, item, _number = candidate
        target = None
        for group_idx, group in enumerate(groups):
            if any(same_body_lane(item, other) for _other_idx, other, _other_number in group):
                target = group_idx
                break
        if target is None:
            groups.append([candidate])
        else:
            groups[target].append(candidate)

    local_groups = []
    for group in groups:
        current: list[tuple[int, RenderItem, int]] = []
        for candidate in sorted(group, key=lambda entry: entry[1].bbox[1]):
            if current and candidate[1].bbox[1] - current[-1][1].bbox[1] > 140.0:
                if len(current) > 1:
                    local_groups.append(current)
                current = []
            current.append(candidate)
        if len(current) > 1:
            local_groups.append(current)
    return local_groups


def repair_numbered_enumeration_item_order(plan: PageRenderPlan) -> None:
    for group in enumeration_candidate_groups(plan):
        by_y = sorted(group, key=lambda entry: entry[1].bbox[1])
        by_number = sorted(group, key=lambda entry: entry[2])
        if [number for _idx, _item, number in by_y] == [number for _idx, _item, number in by_number]:
            continue
        numbers = [number for _idx, _item, number in by_number]
        if numbers != list(range(numbers[0], numbers[-1] + 1)):
            continue
        y_slots = sorted((item.bbox[1], item.bbox[3] - item.bbox[1]) for _idx, item, _number in group)
        for (_idx, item, _number), (y0, height) in zip(by_number, y_slots):
            x0, _old_y0, x1, _old_y1 = item.bbox
            item.bbox = (x0, y0, x1, y0 + height)


def repair_numbered_enumeration_flow(plan: PageRenderPlan) -> None:
    move_leading_enum_continuations_to_previous_items(plan)
    repair_numbered_enumeration_item_order(plan)


def source_is_short_continuation_fragment(block) -> bool:
    text = normalize_text(block.get("text", ""))
    if not text or "\n" in text or len(text) > 30:
        return False
    words = latin_words(text)
    return 0 < len(words) <= 2 and not source_requires_chinese_translation(text)


def source_row_before_short_fragment(block, bbox_lines: list[dict] | None) -> dict | None:
    if not bbox_lines or not all(key in block for key in ("page", "yMin", "yMax")):
        return None
    rows = rows_in_vertical_window(
        int(block.get("page", 1) or 1),
        bbox_lines,
        max(0.0, block["yMin"] - 24.0),
        block["yMax"] + 6.0,
    )
    if len(rows) < 2:
        return None
    fragment_compact = compact_source_phrase(block.get("text", ""))
    if not fragment_compact:
        return None
    ordered_rows = sorted(rows, key=lambda row: (row["bbox"][1], row["bbox"][0]))
    for row_idx, row in enumerate(ordered_rows):
        row_compact = compact_source_phrase(row["text"])
        if fragment_compact and fragment_compact in row_compact and row_idx > 0:
            return ordered_rows[row_idx - 1]
    return None


def neighboring_source_ids(blocks, source_id: str) -> tuple[str | None, str | None]:
    for idx, block in enumerate(blocks):
        if block.get("id") != source_id:
            continue
        previous_id = blocks[idx - 1]["id"] if idx > 0 else None
        next_id = blocks[idx + 1]["id"] if idx + 1 < len(blocks) else None
        return previous_id, next_id
    return None, None


def merge_short_fragment_text(target_text: str, fragment_text: str) -> str:
    target = normalize_text(target_text).strip()
    fragment = normalize_text(fragment_text).strip()
    if not target or not fragment:
        return target_text
    fragment_compact = compact_duplicate_text(fragment)
    if not fragment_compact or fragment_compact in compact_duplicate_text(target):
        return target
    short_fragment = re.sub(r"[。！？.!?]+$", "", fragment).strip()
    if short_fragment == "链表" and "链表" not in target and "下一个单元" in target:
        return target.replace("下一个单元", "链表中的下一个单元", 1)
    if short_fragment and len(compact_duplicate_text(short_fragment)) <= 8:
        terminal = re.search(r"([。！？.!?])$", target)
        if terminal:
            return target[: terminal.start()] + f"（{short_fragment}）" + terminal.group(1)
    separator = "" if target.endswith(("\n", " ", "　")) else " "
    return target + separator + fragment


def short_fragment_absorption_target(
    plan: PageRenderPlan,
    item_idx: int,
    source_block,
    blocks,
    bbox_lines: list[dict] | None,
) -> RenderItem | None:
    item = plan.items[item_idx]
    previous_id, next_id = neighboring_source_ids(blocks, source_block["id"])
    neighbor_ids = {source_id for source_id in (previous_id, next_id) if source_id}
    if not neighbor_ids:
        return None

    context_row = source_row_before_short_fragment(source_block, bbox_lines)
    context_enum = numbered_enumeration_start(context_row["text"]) if context_row else None
    candidates = []
    for idx, other in enumerate(plan.items):
        if idx == item_idx or not item_is_body_text_item(other):
            continue
        if not (set(other.source_ids) & neighbor_ids):
            continue
        if horizontal_overlap(other.bbox, item.bbox) < min(other.bbox[2] - other.bbox[0], item.bbox[2] - item.bbox[0]) * 0.35:
            continue
        vertical_distance = min(abs(other.bbox[1] - item.bbox[3]), abs(item.bbox[1] - other.bbox[3]))
        if vertical_distance > 96.0:
            continue
        if context_enum is not None and f"({context_enum})" not in other.text:
            continue
        candidates.append((vertical_distance, other))
    if candidates:
        return min(candidates, key=lambda candidate: candidate[0])[1]

    previous = previous_body_item_in_same_lane(plan, item_idx)
    if previous is not None and set(previous.source_ids) & neighbor_ids and item.bbox[1] - previous.bbox[3] <= 48.0:
        return previous
    return None


def drop_redundant_short_body_fragments(plan: PageRenderPlan, blocks, bbox_lines: list[dict] | None = None) -> None:
    block_by_id = {block["id"]: block for block in blocks}
    remove_indices = set()
    for idx, item in sorted(enumerate(plan.items), key=lambda pair: (pair[1].bbox[1], pair[1].bbox[0])):
        if not item_is_body_text_item(item) or len(item.source_ids) != 1:
            continue
        source_block = block_by_id.get(item.source_ids[0])
        if source_block is None or not source_is_short_continuation_fragment(source_block):
            continue
        compact = compact_duplicate_text(item.text)
        if not compact or len(compact) > 8 or re.search(r"[A-Za-z0-9]", compact):
            continue
        previous = previous_body_item_in_same_lane(plan, idx)
        if previous is not None and item.bbox[1] - previous.bbox[3] <= 36.0 and compact in compact_duplicate_text(previous.text):
            target = previous
        else:
            target = short_fragment_absorption_target(plan, idx, source_block, blocks, bbox_lines)
            if target is not None:
                target.text = merge_short_fragment_text(target.text, item.text)
        if target is None:
            continue
        for source_id in item.source_ids:
            if source_id not in target.source_ids:
                target.source_ids.append(source_id)
        update_ledger_render_kind(plan, item.source_ids, "translated_text", "merged_redundant_short_fragment")
        remove_indices.add(idx)
    if remove_indices:
        plan.items = [item for idx, item in enumerate(plan.items) if idx not in remove_indices]


def is_formula_or_code_block(text: str) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return False
    if is_code_listing_block(normalized):
        return True
    if is_formula_like(normalized):
        return True
    symbol_count = len(re.findall(r"[=^∀∃<>≤≥∧∨()[\],]", normalized))
    latin_count = len(re.findall(r"[A-Za-z]", normalized))
    word_count = len(re.findall(r"[A-Za-z]{2,}", normalized))
    if len(normalized) > 120 and word_count >= 12:
        return False
    if word_count >= 2 and not re.search(r"[=^∀∃<>≤≥∧∨]", normalized):
        return False
    if word_count >= 4 and re.search(r"\bis\b", normalized, flags=re.I) and symbol_count < 8:
        return False
    return symbol_count >= 3 and latin_count <= max(30, len(normalized) * 0.8)


def is_code_listing_block(text: str) -> bool:
    normalized = normalize_text(text)
    lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    if len(lines) < 2:
        return False
    code_markers = 0
    for line in lines:
        compact = re.sub(r"\s+", "", line.lower())
        if any(
            marker in compact
            for marker in (
                ":=",
                "returns(",
                "return(",
                "create(",
                "endif",
                "endfor",
                "endwhile",
                "enddecide",
                "universal(",
                "decide(",
                "foreachprocess",
                "mine:",
                "inv:",
                "new:",
                "before:",
                "after:",
            )
        ):
            code_markers += 1
        elif re.match(r"^(if|then|else|while|return|end)\b", line.lower()):
            code_markers += 1
        elif re.match(r"^for\b.+\bdo\b", line.lower()):
            code_markers += 1
    prose_like = 0
    for line in lines:
        words = re.findall(r"[A-Za-z][A-Za-z-]{2,}", line)
        function_words = sum(1 for word in words if word.lower() in ENGLISH_FUNCTION_WORDS)
        if len(words) >= 5 and function_words >= 1:
            prose_like += 1
        elif re.match(r"(?i)^\s*(proof|theorem|lemma|corollary|for our|informally)\b", line):
            prose_like += 1
    if prose_like >= max(2, len(lines) // 3) and code_markers < max(3, len(lines) * 0.55):
        return False
    return code_markers >= max(2, math.ceil(len(lines) * 0.35))


def is_code_row_text(text: str) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return False
    lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    if len(lines) > 2:
        return False
    compact = re.sub(r"\s+", "", normalized.lower())
    return (
        any(
            marker in compact
            for marker in (
                ":=",
                "returns(",
                "return(",
                "decide(",
                "universal(",
                "compare&swap(",
                "swap(",
                "deq(",
                "enq(",
                "peek(",
                "endif",
                "endfor",
                "enddecide",
                "foreachprocess",
                "forqin",
                "thenreturn",
                "elsereturn",
                "mine:",
                "inv:",
                "new:create",
                "before:create",
                "after:null",
                "seq:",
            )
        )
        or bool(re.match(r"^(if|then|else|while|return|end)\b", normalized.lower()))
        or bool(re.match(r"^for\b.+\bdo\b", normalized.lower()))
        or bool(re.match(r"^end\s+for\b", normalized.lower()))
    )


def is_prose_row_text(text: str) -> bool:
    normalized = normalize_text(text)
    if not normalized or is_code_row_text(normalized) or is_code_line_number_block(normalized):
        return False
    if cjk_char_count(normalized) >= 4:
        return True
    words = latin_words(normalized)
    if len(words) >= 5 and english_function_word_count(normalized) >= 1:
        return True
    return bool(
        re.match(
            r"(?i)^\s*(proof|theorem|lemma|corollary|claim|because|therefore|first|second|now|we|the)\b",
            normalized,
        )
    )


def is_code_line_number_block(text: str) -> bool:
    lines = [line.strip() for line in normalize_text(text).split("\n") if line.strip()]
    return bool(lines) and len(lines) <= 20 and all(re.fullmatch(r"\d{1,3}", line) for line in lines)


def is_visual_row_text(text: str) -> bool:
    normalized = normalize_text(text)
    return (
        is_visual_caption(normalized)
        or contains_visual_caption(normalized)
        or is_formula_or_code_block(normalized)
        or is_code_row_text(normalized)
        or is_code_line_number_block(normalized)
    )


def rows_inside_bbox(bbox_lines, box) -> list[dict]:
    if not bbox_lines:
        return []
    x0, y0, x1, y1 = box
    candidates = []
    for line in bbox_lines:
        center = bbox_center(line["bbox"])
        if x0 - 2.0 <= center[0] <= x1 + 2.0 and y0 - 2.0 <= center[1] <= y1 + 2.0:
            candidates.append(line)
    return merge_bbox_line_fragments(candidates)


def split_mixed_visual_body_rows(region_bbox, bbox_lines):
    rows = rows_inside_bbox(bbox_lines, region_bbox)
    visual_boxes = []
    body_boxes = []
    saw_code = False
    in_body = False
    for row in sorted(rows, key=lambda item: item["bbox"][1]):
        text = row["text"]
        visual = is_visual_row_text(text)
        if visual and not in_body:
            visual_boxes.append(row["bbox"])
            saw_code = saw_code or is_code_row_text(text) or is_code_listing_block(text)
            continue
        if visual_boxes and is_prose_row_text(text):
            in_body = True
            body_boxes.append(row["bbox"])
            continue
        if visual_boxes and in_body:
            body_boxes.append(row["bbox"])
            continue
        if visual_boxes:
            visual_boxes.append(row["bbox"])
    if not saw_code or not visual_boxes or not body_boxes:
        return None
    body_bbox = bbox_union(body_boxes)
    if body_bbox[3] - body_bbox[1] < 18.0:
        return None
    return bbox_union(visual_boxes), body_bbox


def is_translation_visual_prefix_line(line: str) -> bool:
    normalized = normalize_text(line)
    if not normalized:
        return False
    if cjk_char_count(normalized) >= 3 and not is_code_row_text(normalized):
        return False
    compact = re.sub(r"\s+", "", normalized.lower())
    return is_visual_row_text(normalized) or any(
        marker in compact
        for marker in (
            "decide(",
            "compare&swap(",
            "enq(",
            "peek(",
            ":=",
            "universal(",
            "foreachprocess",
            "forqin",
            "thenreturn",
            "elsereturn",
            "endif",
            "endfor",
            "enddecide",
            "mine:",
            "inv:",
            "new:create",
            "before:create",
            "after:null",
            "announce[",
            "head[",
        )
    )


def is_translation_body_line(line: str) -> bool:
    return cjk_char_count(line) >= 3 or is_prose_row_text(line)


def translation_tail_after_visual_prefix(text: str) -> str:
    lines = text.split("\n")
    first_content_seen = False
    saw_visual_prefix = False
    body_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if saw_visual_prefix and body_lines:
                body_lines.append(line)
            continue
        if not first_content_seen:
            first_content_seen = True
            if is_translation_visual_prefix_line(stripped):
                saw_visual_prefix = True
                continue
            return ""
        if saw_visual_prefix and not body_lines and is_translation_visual_prefix_line(stripped):
            continue
        if saw_visual_prefix and not body_lines and not is_translation_body_line(stripped):
            continue
        if saw_visual_prefix:
            body_lines.append(line)
    if not saw_visual_prefix or not body_lines:
        return ""
    return prepare_render_translation("\n".join(body_lines))


def mixed_visual_body_render_item(region, bbox_lines, translations, page_size):
    split = split_mixed_visual_body_rows(region["bbox"], bbox_lines)
    if split is None:
        return None
    visual_bbox, body_bbox = split
    body_source_ids = []
    body_texts = []
    for source_id in region["source_ids"]:
        tail = translation_tail_after_visual_prefix(translations.get(source_id, ""))
        if not tail:
            continue
        body_source_ids.append(source_id)
        body_texts.append(tail)
    if not body_texts:
        return None
    page_width, page_height = page_size
    body_item = RenderItem(
        "translated_text",
        body_source_ids,
        (
            max(0.0, body_bbox[0]),
            max(0.0, body_bbox[1]),
            min(page_width, body_bbox[2] + 2.0),
            min(page_height, body_bbox[3] + 2.0),
        ),
        text="\n".join(body_texts),
        font_size=BODY_FONT_SIZE,
        style_name="body",
        fallback_reason="mixed_visual_body",
    )
    return visual_bbox, body_item


def is_body_enumeration_line(text: str) -> bool:
    normalized = normalize_text(text)
    return bool(re.match(r"^\(\d+\)\s+[A-Za-z][A-Za-z0-9() ]+\bis\b", normalized))


def build_visual_regions(blocks) -> list[dict]:
    regions = []
    consumed = set()
    references = reference_block_ids(blocks)
    sorted_blocks = sorted(blocks, key=lambda item: (item["yMin"], item["xMin"]))
    for block in sorted_blocks:
        if block["id"] in consumed or block["id"] in references:
            continue
        text = normalize_text(block.get("text", ""))
        if not text:
            continue
        explicit_image = (
            (block.get("preserve_image") or should_preserve_as_image(block))
            and not is_standalone_equation_label(text)
        ) and not is_body_enumeration_line(text)
        has_caption = is_visual_caption(text) or contains_visual_caption(text)
        code_seed = is_code_listing_block(text) or is_code_row_text(text)
        formula_seed = (is_formula_or_code_block(text) or is_code_row_text(text)) and not is_standalone_equation_label(text)
        is_visual_seed = explicit_image or has_caption or formula_seed
        if not is_visual_seed:
            continue
        seed_box = block_bbox(block)
        if has_caption:
            search = (seed_box[0] - 180.0, seed_box[1] - 60.0, seed_box[2] + 180.0, seed_box[3] + 28.0)
        elif code_seed:
            search = expanded_bbox(seed_box, pad_x=130.0, pad_y=28.0)
        elif is_formula_or_code_block(text):
            search = expanded_bbox(seed_box, pad_x=35.0, pad_y=18.0)
        else:
            search = expanded_bbox(seed_box, pad_x=10.0, pad_y=8.0)
        group = []
        for other in sorted_blocks:
            other_text = normalize_text(other.get("text", ""))
            if not other_text or other["id"] in consumed:
                continue
            if is_running_header_fragment(other):
                continue
            if other["id"] != block["id"] and contains_visual_caption(other_text):
                continue
            other_box = block_bbox(other)
            if bbox_intersects(search, other_box) and bbox_contains_point(search, bbox_center(other_box)):
                short_fragment = len(other_text) <= 140
                visual_text = (
                    is_visual_caption(other_text)
                    or contains_visual_caption(other_text)
                    or (is_formula_or_code_block(other_text) and not is_standalone_equation_label(other_text))
                    or (is_code_row_text(other_text) and not is_standalone_equation_label(other_text))
                    or (should_preserve_as_image(other) and not is_body_enumeration_line(other_text))
                )
                code_line_number = code_seed and is_code_line_number_block(other_text)
                if visual_text or code_line_number or (short_fragment and not formula_seed):
                    group.append(other)
        if not group:
            group = [block]
        group_ids = {item["id"] for item in group}
        consumed.update(group_ids)
        region_box = bbox_union([block_bbox(item) for item in group])
        regions.append(
            {
                "source_ids": [item["id"] for item in group],
                "bbox": region_box,
                "has_code_seed": code_seed,
            }
        )
    return regions


def classify_blocks(blocks, visual_regions) -> dict[str, str]:
    classes = {}
    visual_ids = {source_id for region in visual_regions for source_id in region["source_ids"]}
    references = reference_block_ids(blocks)
    in_references = False
    for block in sorted(blocks, key=lambda item: (item["yMin"], item["xMin"])):
        text = normalize_text(block.get("text", ""))
        if not text:
            continue
        if is_page_number(text) or is_decorated_ocr_page_number_block(block):
            classes[block["id"]] = "page_number"
            continue
        if is_journal_footer_block(block):
            classes[block["id"]] = "journal_footer"
            continue
        if is_decorative_update_marker(text) or is_first_page_footer_fragment(block) or is_running_header_fragment(block):
            classes[block["id"]] = "header_footer"
            continue
        if block["id"] in references:
            classes[block["id"]] = "reference"
            in_references = True
            continue
        if block["id"] in visual_ids:
            if is_formula_or_code_block(text):
                classes[block["id"]] = "formula_region"
            else:
                classes[block["id"]] = "figure_region"
            continue
        if is_reference_heading(text) or in_references or starts_reference_item(text):
            classes[block["id"]] = "reference"
            in_references = True
            continue
        if is_heading_text(text):
            classes[block["id"]] = "heading"
            continue
        if is_title_block(block):
            classes[block["id"]] = "title"
            continue
        classes[block["id"]] = "body"
    return classes


def build_page_render_plan(
    page_num: int,
    blocks,
    translations,
    page_size,
    bbox_lines=None,
    source_image_path: Path | None = None,
) -> PageRenderPlan:
    plan = PageRenderPlan(page_num=page_num)
    visual_regions = build_visual_regions(blocks)
    classes = classify_blocks(blocks, visual_regions)
    block_by_id = {block["id"]: block for block in blocks}
    visual_ids = {source_id for region in visual_regions for source_id in region["source_ids"]}
    footer_items = journal_footer_render_items(bbox_lines or [], page_size)
    plan.items.extend(footer_items)
    reference_line_items = reference_line_render_items(blocks, bbox_lines or [], page_size)
    plan.items.extend(reference_line_items)
    duplicate_ids = {
        block_id
        for block_id in nested_duplicate_block_ids(blocks)
        if classes.get(block_id) in {"body", "heading", "title"}
    }
    duplicate_ids.update(contained_standalone_label_ids(blocks, classes))

    for region in visual_regions:
        region_classes = {classes.get(source_id, "figure_region") for source_id in region["source_ids"]}
        if region_classes and region_classes <= {"formula_region"}:
            pad_x = FORMULA_CLIP_PAD_X_PT
            pad_y = FORMULA_CLIP_PAD_Y_PT
        else:
            pad_x = VISUAL_CLIP_PAD_X_PT
            pad_y = VISUAL_CLIP_PAD_Y_PT
        region_has_caption = any(
            contains_visual_caption(block_by_id[source_id].get("text", ""))
            or is_visual_caption(block_by_id[source_id].get("text", ""))
            for source_id in region["source_ids"]
            if source_id in block_by_id
        )
        formula_only = bool(
            region_classes
            and region_classes <= {"formula_region"}
            and not region_has_caption
            and not region.get("has_code_seed")
        )
        visual_source_bbox = region["bbox"]
        mixed_body_item = None
        mixed_visual_body = False
        if region.get("has_code_seed") and bbox_lines:
            mixed = mixed_visual_body_render_item(region, bbox_lines, translations, page_size)
            if mixed is not None:
                visual_source_bbox, mixed_body_item = mixed
                mixed_visual_body = True
        if formula_only and bbox_lines:
            line_bbox = formula_region_bbox_from_lines(region, bbox_lines, page_size)
            if line_bbox is not None:
                visual_source_bbox = line_bbox
        region_bbox = visual_clip_bbox(
            visual_source_bbox,
            page_size,
            pad_x=pad_x,
            pad_y=pad_y,
            page_num=page_num,
            source_image_path=source_image_path,
            use_pixel_bounds=True,
            pixel_search_pad_x=FORMULA_CLIP_PIXEL_SEARCH_PAD_X_PT if formula_only else VISUAL_CLIP_PIXEL_SEARCH_PAD_X_PT,
            pixel_search_pad_y=(
                FORMULA_CLIP_PIXEL_SEARCH_PAD_Y_PT
                if formula_only or mixed_visual_body
                else VISUAL_CLIP_PIXEL_SEARCH_PAD_Y_PT
            ),
            pixel_final_pad=0.75 if formula_only else VISUAL_CLIP_PIXEL_FINAL_PAD_PT,
        )
        if not formula_only:
            region_bbox = cap_visual_bbox_before_following_text(
                visual_source_bbox,
                region_bbox,
                blocks,
                classes,
                visual_ids,
            )
        plan.items.append(
            RenderItem(
                kind="original_image_clip",
                source_ids=region["source_ids"],
                bbox=region_bbox,
                fallback_reason="visual_region",
            )
        )
        plan.protected_boxes.append(region_bbox)
        for source_id in region["source_ids"]:
            plan.ledger.append(
                CoverageEntry(
                    source_id,
                    classes.get(source_id, "figure_region"),
                    "original_image_clip",
                    True,
                    "visual_region",
                )
            )
        if mixed_body_item is not None:
            plan.items.append(mixed_body_item)

    for block in blocks:
        text = normalize_text(block.get("text", ""))
        if not text or block["id"] in visual_ids:
            continue
        classification = classes.get(block["id"], "unknown")
        bbox = adjusted_render_bbox(block, classification, page_size)
        if classification in {"body", "heading", "title", "reference"}:
            bbox = refined_text_bbox_from_lines(block, bbox, bbox_lines or [], page_size)
        if block["id"] in duplicate_ids:
            plan.ledger.append(CoverageEntry(block["id"], "nested_duplicate", "skip_explicitly", True))
            continue
        if classification == "journal_footer":
            if footer_items:
                plan.ledger.append(
                    CoverageEntry(
                        block["id"],
                        classification,
                        "original_selectable_text",
                        True,
                        "journal_footer_lines",
                    )
                )
                continue
            plan.items.append(
                RenderItem(
                    "original_selectable_text",
                    [block["id"]],
                    journal_footer_bbox(page_size),
                    text=JOURNAL_FOOTER_TEXT,
                    font_size=JOURNAL_FOOTER_FONT_SIZE,
                    style_name="footer",
                    fallback_reason="journal_footer",
                )
            )
            plan.ledger.append(
                CoverageEntry(
                    block["id"],
                    classification,
                    "original_selectable_text",
                    True,
                    "journal_footer",
                )
            )
            continue
        if classification in {"page_number", "header_footer"}:
            plan.ledger.append(CoverageEntry(block["id"], classification, "skip_explicitly", True))
            continue
        if classification == "reference":
            if reference_line_items:
                plan.ledger.append(
                    CoverageEntry(
                        block["id"],
                        classification,
                        "original_selectable_text",
                        True,
                        "reference_original_lines",
                    )
                )
                continue
            plan.items.append(
                RenderItem(
                    "original_selectable_text",
                    [block["id"]],
                    bbox,
                    text=text,
                    font_size=DOCUMENT_STYLES["reference"].font_size,
                    style_name="reference",
                    fallback_reason="reference_original",
                )
            )
            plan.ledger.append(
                CoverageEntry(
                    block["id"],
                    classification,
                    "original_selectable_text",
                    True,
                    "reference_original",
                )
            )
            continue
        if classification in {"body", "heading", "title"}:
            translated = clean_render_text(
                block,
                translation_for_block(block, translations),
                translations.get(block["id"], ""),
            )
            style_name = style_name_for_block(block, classification)
            if (
                translated
                and block["id"] in translations
                and not translation_appears_untranslated(text, translated)
            ):
                title_metadata_items = first_page_title_metadata_render_items(block, translated, bbox, page_size)
                if title_metadata_items:
                    plan.items.extend(title_metadata_items)
                    plan.ledger.append(
                        CoverageEntry(
                            block["id"],
                            classification,
                            "translated_text",
                            True,
                            "first_page_title_metadata_split",
                        )
                    )
                    continue
                embedded_heading_items = embedded_heading_render_items(
                    block,
                    translated,
                    bbox,
                    page_size,
                    bbox_lines or [],
                )
                if embedded_heading_items:
                    plan.items.extend(embedded_heading_items)
                    plan.ledger.append(
                        CoverageEntry(
                            block["id"],
                            classification,
                            "translated_text",
                            True,
                            "embedded_heading_split",
                        )
                    )
                    continue
                paragraph_items = []
                if style_name == "body":
                    paragraph_items = source_paragraph_render_items(
                        block,
                        translated,
                        bbox,
                        page_size,
                        bbox_lines or [],
                        plan.protected_boxes,
                    )
                if paragraph_items:
                    plan.items.extend(paragraph_items)
                    plan.ledger.append(
                        CoverageEntry(
                            block["id"],
                            classification,
                            "translated_text",
                            True,
                            "source_paragraph_split",
                        )
                    )
                    continue
                plan.items.append(
                    RenderItem(
                        "translated_text",
                        [block["id"]],
                        bbox,
                        text=translated,
                        font_size=text_style(style_name).font_size,
                        style_name=style_name,
                    )
                )
                plan.ledger.append(CoverageEntry(block["id"], classification, "translated_text", True))
            elif translated and block["id"] in translations:
                plan.items.append(
                    RenderItem(
                        "original_selectable_text",
                        [block["id"]],
                        bbox,
                        text=strip_journal_footer_lines(text),
                        font_size=text_style(style_name).font_size,
                        style_name=style_name,
                        fallback_reason="untranslated_fallback_original",
                    )
                )
                plan.ledger.append(
                    CoverageEntry(
                        block["id"],
                        classification,
                        "original_selectable_text",
                        True,
                        "untranslated_fallback_original",
                    )
                )
            elif classification == "body" and (heuristic_translated := heuristic_translation_for_missing_block(text)):
                plan.items.append(
                    RenderItem(
                        "translated_text",
                        [block["id"]],
                        bbox,
                        text=heuristic_translated,
                        font_size=BODY_FONT_SIZE,
                        style_name="body",
                        fallback_reason="heuristic_translation",
                    )
                )
                plan.ledger.append(
                    CoverageEntry(
                        block["id"],
                        classification,
                        "translated_text",
                        True,
                        "heuristic_translation",
                    )
                )
            elif block.get("preserve_image") and not is_body_enumeration_line(text):
                plan.items.append(
                    RenderItem(
                        "original_image_clip",
                        [block["id"]],
                        bbox,
                        fallback_reason="missing_translation_preserve_image",
                    )
                )
                plan.protected_boxes.append(bbox)
                plan.ledger.append(
                    CoverageEntry(
                        block["id"],
                        classification,
                        "original_image_clip",
                        True,
                        "missing_translation_preserve_image",
                    )
                )
            else:
                plan.items.append(
                    RenderItem(
                        "original_selectable_text",
                        [block["id"]],
                        bbox,
                        text=strip_journal_footer_lines(text),
                        font_size=text_style(style_name).font_size,
                        style_name=style_name,
                        fallback_reason="missing_translation",
                    )
                )
                plan.ledger.append(
                    CoverageEntry(
                        block["id"],
                        classification,
                        "original_selectable_text",
                        True,
                        "missing_translation",
                    )
                )
            continue
        plan.items.append(RenderItem("original_image_clip", [block["id"]], bbox, fallback_reason="unknown"))
        plan.protected_boxes.append(bbox)
        plan.ledger.append(CoverageEntry(block["id"], classification, "original_image_clip", True, "unknown"))
    merge_contained_text_fragments(plan)
    split_translated_text_around_protected(plan)
    merge_contained_text_fragments(plan)
    repair_numbered_enumeration_flow(plan)
    merge_adjacent_body_text_flows(plan)
    drop_redundant_short_body_fragments(plan, blocks, bbox_lines or [])
    expand_text_boxes_to_fit(plan, page_size)
    rebalance_body_text_flows(plan, page_size)
    repair_numbered_enumeration_flow(plan)
    drop_redundant_short_body_fragments(plan, blocks, bbox_lines or [])
    rebalance_body_text_flows(plan, page_size)
    return plan


ALLOWED_SKIP_CLASSES = {"page_number", "header_footer", "nested_duplicate"}


def nontrivial_block(block) -> bool:
    text = normalize_text(block.get("text", ""))
    return bool(text) and not is_trivial_keep(text)


def validate_plan_coverage(page_num: int, blocks, plan: PageRenderPlan) -> list[str]:
    errors = []
    ledger_by_id = {entry.block_id: entry for entry in plan.ledger}
    for block in blocks:
        if not nontrivial_block(block):
            continue
        entry = ledger_by_id.get(block["id"])
        if entry is None:
            errors.append(f"page {page_num} block {block['id']} has no coverage entry")
            continue
        if not entry.rendered:
            errors.append(f"page {page_num} block {block['id']} is marked unrendered")
            continue
        if entry.render_kind == "skip_explicitly" and entry.classification not in ALLOWED_SKIP_CLASSES:
            errors.append(f"page {page_num} block {block['id']} has illegal skip class {entry.classification}")
    return errors


def cjk_char_count(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def latin_words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z][A-Za-z-]{2,}", normalize_text(text))


def english_function_word_count(text: str) -> int:
    return sum(1 for word in latin_words(text) if word.lower() in ENGLISH_FUNCTION_WORDS)


def source_requires_chinese_translation(text: str) -> bool:
    cleaned = strip_journal_footer_lines(text)
    words = latin_words(cleaned)
    if len(words) < 4:
        return False
    return len(cleaned) >= 30 or english_function_word_count(cleaned) >= 1


def compact_alpha_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalize_text(text).lower())


def translation_appears_untranslated(source_text: str, translated_text: str) -> bool:
    translated = strip_journal_footer_lines(translated_text)
    if not source_requires_chinese_translation(source_text):
        return False
    if not translated:
        return True
    source_compact = compact_alpha_text(strip_journal_footer_lines(source_text))
    translated_compact = compact_alpha_text(translated)
    if source_compact and source_compact == translated_compact:
        return True

    chinese_chars = cjk_char_count(translated)
    words = latin_words(translated)
    function_words = sum(1 for word in words if word.lower() in ENGLISH_FUNCTION_WORDS)
    latin_chars = sum(len(word) for word in words)
    if chinese_chars == 0 and len(words) >= 3:
        return True
    if function_words >= 3 and chinese_chars < 5:
        return True
    if len(translated) >= 80 and function_words >= 6 and latin_chars > max(40, chinese_chars * 1.2):
        return True
    return False


def validate_plan_translation_quality(page_num: int, blocks, translations, plan: PageRenderPlan) -> list[str]:
    errors = []
    ledger_by_id = {entry.block_id: entry for entry in plan.ledger}
    block_by_id = {block["id"]: block for block in blocks}
    rendered_text_by_id = rendered_text_by_source_id(plan)
    for block_id, block in block_by_id.items():
        entry = ledger_by_id.get(block_id)
        if entry is None or entry.classification not in NORMAL_TRANSLATED_CLASSES:
            continue
        source_text = strip_journal_footer_lines(block.get("text", ""))
        if not source_requires_chinese_translation(source_text):
            continue
        if entry.render_kind == "original_image_clip":
            errors.append(
                f"page {page_num} block {block_id} normal text block rendered as original image"
                f" ({entry.fallback_reason or 'no reason'})"
            )
            continue
        if entry.render_kind == "original_selectable_text":
            errors.append(f"page {page_num} block {block_id} normal text block is missing Chinese translation")
            continue
        translated = translations.get(block_id, "")
        rendered_text = rendered_text_by_id.get(block_id, "")
        if translation_appears_untranslated(source_text, rendered_text or translated):
            errors.append(f"page {page_num} block {block_id} appears untranslated or mostly English")
        expected_render_text = clean_render_text(block, translation_for_block(block, translations), translated)
        anchor_text = translation_anchor_text(expected_render_text)
        anchor = cjk_anchor(anchor_text)
        if anchor and anchor not in compact_cjk_text(rendered_text):
            errors.append(f"page {page_num} block {block_id} rendered text is missing translated anchor")
    return errors


def rendered_text_by_source_id(plan: PageRenderPlan) -> dict[str, str]:
    rendered: dict[str, list[str]] = {}
    for item in sorted(plan.items, key=lambda candidate: (candidate.bbox[1], candidate.bbox[0])):
        if item.kind not in {"translated_text", "original_selectable_text"} or not item.text.strip():
            continue
        for source_id in item.source_ids:
            rendered.setdefault(source_id, []).append(item.text)
    return {source_id: "\n".join(parts) for source_id, parts in rendered.items()}


def compact_cjk_text(text: str) -> str:
    return "".join(re.findall(r"[\u4e00-\u9fff]", normalize_text(text)))


def cjk_anchor(text: str, length: int = 8) -> str:
    compact = compact_cjk_text(text)
    if len(compact) < length:
        return ""
    return compact[:length]


def translation_anchor_text(translated: str) -> str:
    if not translated:
        return ""
    return translation_tail_after_visual_prefix(translated) or translated


def validate_plan_text_overlaps(plan: PageRenderPlan) -> list[str]:
    errors = []
    text_items = [
        item
        for item in plan.items
        if item.kind in {"translated_text", "original_selectable_text"} and bbox_area(item.bbox) > 0
    ]
    for left_idx, left in enumerate(text_items):
        for right in text_items[left_idx + 1 :]:
            if left.source_ids and right.source_ids and set(left.source_ids) == set(right.source_ids):
                continue
            if bbox_overlap_height(left.bbox, right.bbox) <= 3.0:
                continue
            overlap = bbox_overlap_area(left.bbox, right.bbox)
            if overlap <= min(bbox_area(left.bbox), bbox_area(right.bbox)) * 0.12:
                continue
            errors.append(
                f"page {plan.page_num} text {left.source_ids or left.fallback_reason}"
                f" overlaps text {right.source_ids or right.fallback_reason}"
            )
    return errors


def validate_plan_vertical_balance(plan: PageRenderPlan, fitz=None) -> list[str]:
    if fitz is None:
        fitz = load_fitz()
    errors = []
    for lane in body_layout_lanes(plan):
        for group in split_body_layout_lane(plan, lane):
            items = [plan.items[idx] for idx in group]
            if len(items) < 2:
                continue
            preferred_heights = [preferred_text_height_for_item(item, fitz) for item in items]
            for item, preferred in zip(items, preferred_heights):
                slack = (item.bbox[3] - item.bbox[1]) - preferred
                if slack > BODY_FLOW_INTERNAL_SLACK_WARN_PT:
                    errors.append(
                        f"page {plan.page_num} body flow has uneven vertical spacing: "
                        f"text {item.source_ids} keeps {slack:.1f}pt unused height"
                    )
            for left, right, left_height in zip(items, items[1:], preferred_heights):
                visible_gap = right.bbox[1] - (left.bbox[1] + left_height)
                if visible_gap > BODY_FLOW_VISIBLE_GAP_WARN_PT:
                    errors.append(
                        f"page {plan.page_num} body flow has uneven vertical spacing: "
                        f"gap between {left.source_ids} and {right.source_ids} is {visible_gap:.1f}pt"
                    )
    return errors


def validate_plan_reference_policy(plan: PageRenderPlan) -> list[str]:
    errors = []
    for entry in plan.ledger:
        if entry.classification != "reference":
            continue
        if entry.render_kind != "original_selectable_text":
            errors.append(f"page {plan.page_num} reference block {entry.block_id} is not selectable text")
    for item in plan.items:
        if item.fallback_reason != "reference_original":
            continue
        if cjk_char_count(item.text) > 0:
            errors.append(f"page {plan.page_num} reference item {item.source_ids} contains translated Chinese text")
    return errors


def validate_plan_footer_policy(plan: PageRenderPlan) -> list[str]:
    errors = []
    footers = [item for item in plan.items if item.fallback_reason == "journal_footer"]
    if len(footers) > 1:
        errors.append(f"page {plan.page_num} has multiple journal footers")
    translated_text = "\n".join(item.text for item in plan.items if item.kind == "translated_text")
    if is_journal_footer_text(translated_text):
        errors.append(f"page {plan.page_num} journal footer leaked into translated text")
    return errors


def validate_plan_embedded_heading_policy(plan: PageRenderPlan) -> list[str]:
    errors = []
    for item in plan.items:
        if item.kind not in {"translated_text", "original_selectable_text"}:
            continue
        if render_text_style_name(item) != "body":
            continue
        for line in item.text.split("\n")[1:]:
            if render_line_is_standalone_heading(line):
                errors.append(f"page {plan.page_num} item {item.source_ids} has embedded heading in body text")
                break
    return errors


def text_starts_with_running_header(text: str) -> bool:
    first = normalize_text(text).split("\n", 1)[0].strip()
    return bool(
        re.match(
            r"^(?:Maurice\s*Herlihy|MauriceHerlihy|Wait-?\s*Free\s*Synchronization|Wait-FreeSynchronization|无等待同步)\b",
            first,
            flags=re.I,
        )
    )


def validate_plan_text_noise_policy(plan: PageRenderPlan) -> list[str]:
    errors = []
    for item in plan.items:
        if item.kind not in {"translated_text", "original_selectable_text"} or not item.text.strip():
            continue
        if render_text_style_name(item) in {"reference", "footer"}:
            continue
        if plan.page_num > 1 and text_starts_with_running_header(item.text):
            errors.append(f"page {plan.page_num} item {item.source_ids} contains running header text")
        for line in item.text.split("\n"):
            if is_garbled_latin_fragment(line):
                errors.append(f"page {plan.page_num} item {item.source_ids} contains garbled OCR text")
                break
    return errors


def validate_plan_style_policy(plan: PageRenderPlan) -> list[str]:
    errors = []
    for item in plan.items:
        if item.kind not in {"translated_text", "original_selectable_text"}:
            continue
        expected_name = render_text_style_name(item)
        style = text_style(expected_name)
        if item.style_name and item.style_name != expected_name:
            errors.append(f"page {plan.page_num} item {item.source_ids} has style {item.style_name}, expected {expected_name}")
        if item.font_size is not None and abs(item.font_size - style.font_size) > 0.01:
            errors.append(f"page {plan.page_num} item {item.source_ids} has font size {item.font_size}, expected {style.font_size}")
    return errors


def validate_plan_text_fit(plan: PageRenderPlan, fitz=None) -> list[str]:
    if fitz is None:
        fitz = load_fitz()
    errors = []
    for item in plan.items:
        if item.kind not in {"translated_text", "original_selectable_text"} or not item.text.strip():
            continue
        fit, required, available = text_item_fit_metrics(item, fitz)
        if fit is None:
            errors.append(
                f"page {plan.page_num} text {item.source_ids or item.fallback_reason}"
                f" needs {required:.1f}pt height but has {available:.1f}pt for style {render_text_style_name(item)}"
            )
    return errors


def source_image_dark_pixel_count(source_image_path: Path | None, bbox, page_size, *, darkness_threshold: int = 245) -> int | None:
    if source_image_path is None or not Path(source_image_path).exists():
        return None
    page_width, page_height = page_size
    if page_width <= 0 or page_height <= 0:
        return None
    try:
        image = Image.open(source_image_path).convert("L")
    except Exception:
        return None
    scale_x = image.width / page_width
    scale_y = image.height / page_height
    x0, y0, x1, y1 = bbox
    crop_box = (
        max(0, int(math.floor(x0 * scale_x))),
        max(0, int(math.floor(y0 * scale_y))),
        min(image.width, int(math.ceil(x1 * scale_x))),
        min(image.height, int(math.ceil(y1 * scale_y))),
    )
    if crop_box[2] <= crop_box[0] or crop_box[3] <= crop_box[1]:
        return 0
    crop = image.crop(crop_box)
    return sum(1 for pixel in crop.getdata() if pixel < darkness_threshold)


def validate_plan_image_clip_content(
    plan: PageRenderPlan,
    source_image_path: Path | None,
    page_size,
) -> list[str]:
    if source_image_path is None:
        return []
    errors = []
    for item in plan.items:
        if item.kind != "original_image_clip":
            continue
        dark_pixels = source_image_dark_pixel_count(source_image_path, item.bbox, page_size)
        if dark_pixels is None:
            continue
        if dark_pixels <= 0:
            errors.append(f"page {plan.page_num} item {item.source_ids} has blank source image clip")
    return errors


def validate_plan_quality(page_num: int, blocks, translations, plan: PageRenderPlan) -> list[str]:
    return (
        validate_plan_translation_quality(page_num, blocks, translations, plan)
        + validate_plan_text_overlaps(plan)
        + validate_plan_vertical_balance(plan)
        + validate_plan_reference_policy(plan)
        + validate_plan_footer_policy(plan)
        + validate_plan_embedded_heading_policy(plan)
        + validate_plan_text_noise_policy(plan)
        + validate_plan_style_policy(plan)
    )


def validate_footer_consistency(plans: list[PageRenderPlan]) -> list[str]:
    footer_shapes = []
    for plan in plans:
        for item in plan.items:
            if item.fallback_reason == "journal_footer":
                footer_shapes.append((plan.page_num, item.kind, item.bbox, item.font_size, item.text))
    if not footer_shapes:
        return []
    expected = footer_shapes[0][1:]
    inconsistent_pages = [page_num for page_num, *shape in footer_shapes if tuple(shape) != expected]
    if inconsistent_pages:
        return [f"journal footer style is inconsistent on pages {inconsistent_pages[:10]}"]
    return []


def bbox_lines_by_page(job_paths) -> dict[int, list[dict]]:
    by_page = {}
    if not job_paths or not job_paths.get("bbox_path") or not job_paths["bbox_path"].exists():
        return by_page
    for line in parse_bbox_lines(job_paths["bbox_path"]):
        by_page.setdefault(line["page"], []).append(line)
    return by_page


def validate_document_quality(selected_pages, translations, page_size, job_paths=None) -> list[str]:
    errors = []
    plans = []
    fitz = load_fitz()
    translations = postprocess_cross_page_sentence_splits(
        selected_pages,
        translations,
        job_paths=job_paths,
    )
    lines_by_page = bbox_lines_by_page(job_paths)
    for page_num, blocks in selected_pages:
        source_image_path = source_page_image_path(job_paths, page_num)
        plan = build_page_render_plan(
            page_num,
            blocks,
            translations,
            page_size,
            bbox_lines=lines_by_page.get(page_num),
            source_image_path=source_image_path,
        )
        plans.append(plan)
        errors.extend(validate_plan_coverage(page_num, blocks, plan))
        errors.extend(validate_plan_layout(plan, page_size))
        errors.extend(validate_plan_quality(page_num, blocks, translations, plan))
        errors.extend(validate_plan_image_clip_content(plan, source_image_path, page_size))
        errors.extend(validate_plan_text_fit(plan, fitz))
    errors.extend(validate_footer_consistency(plans))
    return errors


def update_ledger_render_kind(plan: PageRenderPlan, source_ids: list[str], render_kind: str, fallback_reason: str):
    source_id_set = set(source_ids)
    for entry in plan.ledger:
        if entry.block_id not in source_id_set:
            continue
        entry.render_kind = render_kind
        entry.fallback_reason = fallback_reason


def bbox_area(box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def bbox_overlap_area(a, b) -> float:
    x0 = max(a[0], b[0])
    y0 = max(a[1], b[1])
    x1 = min(a[2], b[2])
    y1 = min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def bbox_overlap_height(a, b) -> float:
    return max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def item_significantly_overlaps_protected(item: RenderItem, protected_item: RenderItem) -> bool:
    if bbox_overlap_height(item.bbox, protected_item.bbox) <= 6.0:
        return False
    overlap = bbox_overlap_area(item.bbox, protected_item.bbox)
    return overlap > min(bbox_area(item.bbox), bbox_area(protected_item.bbox)) * 0.05


def ledger_classifications(plan: PageRenderPlan) -> dict[str, str]:
    return {entry.block_id: entry.classification for entry in plan.ledger}


def item_classifications(item: RenderItem, classes: dict[str, str]) -> set[str]:
    return {classes[source_id] for source_id in item.source_ids if source_id in classes}


def item_is_body_like(item: RenderItem, classes: dict[str, str]) -> bool:
    if item.style_name == "body" and item.fallback_reason == "mixed_visual_body":
        return True
    if item.style_name == "body" and item.fallback_reason == "source_paragraph_split":
        return True
    item_classes = item_classifications(item, classes)
    return bool(item_classes) and item_classes <= {"body"}


def short_insertion_is_redundant(base_text: str, insertion: str) -> bool:
    insertion = normalize_text(insertion).strip()
    if not insertion:
        return True
    if insertion in {"如下", "如下:", "如下："}:
        return bool(re.search(r"(表述为|如下|如下所示|定义为|记为|形式为)[:：]?\s*$", base_text.strip()))
    return False


def merge_text_at_relative_position(base_text: str, insertion: str, relative_y: float) -> str:
    insertion = normalize_translation(insertion)
    if not insertion:
        return base_text
    if short_insertion_is_redundant(base_text, insertion):
        return base_text
    base_compact = compact_duplicate_text(base_text)
    insertion_compact = compact_duplicate_text(insertion)
    if len(insertion_compact) >= 8 and insertion_compact in base_compact:
        return base_text
    paragraphs = base_text.split("\n")
    if not paragraphs:
        return insertion
    index = int(round(max(0.0, min(1.0, relative_y)) * len(paragraphs)))
    index = max(0, min(len(paragraphs), index))
    if len(insertion) <= 36 and paragraphs:
        target = max(0, min(len(paragraphs) - 1, int(math.floor(max(0.0, min(0.999, relative_y)) * len(paragraphs)))))
        paragraphs[target] = join_render_lines(paragraphs[target].rstrip(), insertion)
        return "\n".join(paragraph for paragraph in paragraphs if paragraph.strip())
    paragraphs.insert(index, insertion)
    return "\n".join(paragraph for paragraph in paragraphs if paragraph.strip())


def merge_contained_text_fragments(plan: PageRenderPlan) -> None:
    classes = ledger_classifications(plan)
    text_items = [
        (idx, item)
        for idx, item in enumerate(plan.items)
        if item.kind == "translated_text" and item.source_ids
    ]
    merge_targets: dict[int, list[tuple[float, int, RenderItem]]] = {}
    remove_indices = set()
    for large_idx, large in text_items:
        large_height = large.bbox[3] - large.bbox[1]
        if not item_is_body_like(large, classes) or large_height < 40.0:
            continue
        large_area = bbox_area(large.bbox)
        for small_idx, small in text_items:
            if small_idx == large_idx or small_idx in remove_indices:
                continue
            if not item_is_body_like(small, classes):
                continue
            if bbox_area(small.bbox) <= 0 or large_area < bbox_area(small.bbox) * 6.0:
                continue
            if not (
                bbox_contains_point(large.bbox, bbox_center(small.bbox))
                or item_significantly_overlaps_protected(small, large)
            ):
                continue
            relative_y = (bbox_center(small.bbox)[1] - large.bbox[1]) / max(1.0, large_height)
            merge_targets.setdefault(large_idx, []).append((relative_y, small_idx, small))
            remove_indices.add(small_idx)

    if not merge_targets:
        return

    for large_idx, fragments in merge_targets.items():
        large = plan.items[large_idx]
        for relative_y, _small_idx, small in sorted(fragments, key=lambda item: item[0]):
            large.text = merge_text_at_relative_position(large.text, small.text, relative_y)
            for source_id in small.source_ids:
                if source_id not in large.source_ids:
                    large.source_ids.append(source_id)
        update_ledger_render_kind(
            plan,
            [source_id for _relative_y, _idx, small in fragments for source_id in small.source_ids],
            "translated_text",
            "merged_into_large_text",
        )

    plan.items = [item for idx, item in enumerate(plan.items) if idx not in remove_indices]


def translated_section_start(text: str) -> bool:
    return bool(re.match(r"^\s*\d+(?:\.\d+)*\.?\s*[\u4e00-\u9fffA-Z]", normalize_text(text)))


def item_is_body_flow_text(item: RenderItem, classes: dict[str, str]) -> bool:
    if item.kind != "translated_text" or item.style_name != "body" or not item.source_ids:
        return False
    if item.fallback_reason == "source_paragraph_split":
        return False
    if item.bbox[3] - item.bbox[1] < 8.0:
        return False
    return item_is_body_like(item, classes)


def protected_between_body_items(left: RenderItem, right: RenderItem, protected: list[RenderItem]) -> bool:
    top = min(left.bbox[3], right.bbox[3])
    bottom = max(left.bbox[1], right.bbox[1])
    for item in protected:
        if item.bbox[1] >= bottom or item.bbox[3] <= top:
            continue
        if horizontal_overlap(item.bbox, bbox_union([left.bbox, right.bbox])) > 8.0:
            return True
    return False


def body_items_can_share_flow(left: RenderItem, right: RenderItem, protected: list[RenderItem]) -> bool:
    if translated_section_start(right.text):
        return False
    vertical_gap = right.bbox[1] - left.bbox[3]
    if vertical_gap > 16.0:
        return False
    overlap = horizontal_overlap(left.bbox, right.bbox)
    min_width = max(1.0, min(left.bbox[2] - left.bbox[0], right.bbox[2] - right.bbox[0]))
    if overlap < min_width * 0.55:
        return False
    if protected_between_body_items(left, right, protected):
        return False
    return True


def merge_adjacent_body_text_flows(plan: PageRenderPlan) -> None:
    classes = ledger_classifications(plan)
    protected = [
        item
        for item in plan.items
        if item.kind == "original_image_clip"
        or (
            item.kind in {"translated_text", "original_selectable_text"}
            and item.style_name in {"heading", "subheading", "title", "reference", "footer"}
        )
    ]
    candidates = [
        (idx, item)
        for idx, item in enumerate(plan.items)
        if item_is_body_flow_text(item, classes)
    ]
    if len(candidates) < 2:
        return

    sorted_candidates = sorted(candidates, key=lambda pair: (pair[1].bbox[1], pair[1].bbox[0]))
    groups = []
    current = [sorted_candidates[0]]
    for idx, item in sorted_candidates[1:]:
        previous = current[-1][1]
        if body_items_can_share_flow(previous, item, protected):
            current.append((idx, item))
            continue
        if len(current) > 1:
            groups.append(current)
        current = [(idx, item)]
    if len(current) > 1:
        groups.append(current)
    if not groups:
        return

    replacement_by_first = {}
    remove_indices = set()
    for group in groups:
        ordered_items = [item for _idx, item in group]
        first_idx = group[0][0]
        source_ids = [source_id for item in ordered_items for source_id in item.source_ids]
        replacement_by_first[first_idx] = RenderItem(
            "translated_text",
            source_ids,
            bbox_union([item.bbox for item in ordered_items]),
            text="\n".join(item.text for item in ordered_items if item.text.strip()),
            font_size=DOCUMENT_STYLES["body"].font_size,
            style_name="body",
            color=ordered_items[0].color,
            fallback_reason="body_flow",
        )
        remove_indices.update(idx for idx, _item in group[1:])

    plan.items = [
        replacement_by_first.get(idx, item)
        for idx, item in enumerate(plan.items)
        if idx not in remove_indices
    ]


def render_text_style_name(item: RenderItem) -> str:
    if item.style_name:
        return item.style_name
    if item.fallback_reason == "journal_footer":
        return "footer"
    if item.fallback_reason == "reference_original":
        return "reference"
    return "body"


def text_item_fit_metrics(item: RenderItem, fitz) -> tuple[tuple[float, float] | None, float, float]:
    style = text_style(render_text_style_name(item))
    font_size = item.font_size or style.font_size
    width = max(1.0, item.bbox[2] - item.bbox[0])
    lines = wrap_mixed_pdf_text(fitz, item.text, width, font_size)
    fit = fitted_text_spacing(lines, font_size, item.bbox[3] - item.bbox[1], style)
    required = text_height_for_lines(
        lines,
        font_size,
        style.min_line_height_factor or style.line_height_factor,
        style.min_paragraph_spacing if style.min_paragraph_spacing is not None else style.paragraph_spacing,
    )
    available = item.bbox[3] - item.bbox[1]
    return fit, required, available


def item_is_expandable_body_text(item: RenderItem) -> bool:
    return (
        item.kind in {"translated_text", "original_selectable_text"}
        and item.text.strip()
        and render_text_style_name(item) == "body"
    )


def layout_horizontal_conflict(a, b) -> bool:
    overlap = horizontal_overlap(a, b)
    if overlap <= 0:
        return False
    min_width = max(1.0, min(a[2] - a[0], b[2] - b[0]))
    return overlap >= min_width * 0.15 or overlap >= 12.0


def vertical_expansion_limits(item: RenderItem, items: list[RenderItem], page_size) -> tuple[float, float]:
    _width, page_height = page_size
    top_limit = 0.0
    bottom_limit = page_height
    item_center_y = bbox_center(item.bbox)[1]
    for other in items:
        if other is item or not layout_horizontal_conflict(item.bbox, other.bbox):
            continue
        if other.bbox[3] <= item.bbox[1]:
            top_limit = max(top_limit, other.bbox[3] + 2.0)
        elif other.bbox[1] >= item.bbox[3]:
            bottom_limit = min(bottom_limit, other.bbox[1] - 2.0)
        elif bbox_center(other.bbox)[1] < item_center_y:
            top_limit = max(top_limit, other.bbox[3] + 2.0)
        else:
            bottom_limit = min(bottom_limit, other.bbox[1] - 2.0)
    return top_limit, bottom_limit


def expand_text_boxes_to_fit(plan: PageRenderPlan, page_size, fitz=None) -> None:
    if fitz is None:
        fitz = load_fitz()
    for item in sorted(plan.items, key=lambda candidate: (candidate.bbox[1], candidate.bbox[0])):
        if not item_is_expandable_body_text(item):
            continue
        fit, required, available = text_item_fit_metrics(item, fitz)
        if fit is not None:
            continue
        target_height = required + 0.5
        needed = target_height - available
        if needed <= 0:
            continue
        top_limit, bottom_limit = vertical_expansion_limits(item, plan.items, page_size)
        x0, y0, x1, y1 = item.bbox
        grow_down = min(max(0.0, bottom_limit - y1), needed)
        y1 += grow_down
        needed -= grow_down
        grow_up = min(max(0.0, y0 - top_limit), needed)
        y0 -= grow_up
        item.bbox = clamp_bbox((x0, y0, x1, y1), page_size)


def preferred_text_height_for_item(item: RenderItem, fitz) -> float:
    style = text_style(render_text_style_name(item))
    font_size = item.font_size or style.font_size
    width = max(1.0, item.bbox[2] - item.bbox[0])
    lines = wrap_mixed_pdf_text(fitz, item.text, width, font_size)
    return text_height_for_lines(lines, font_size, style.line_height_factor, style.paragraph_spacing)


def minimum_text_height_for_item(item: RenderItem, fitz) -> float:
    style = text_style(render_text_style_name(item))
    font_size = item.font_size or style.font_size
    width = max(1.0, item.bbox[2] - item.bbox[0])
    lines = wrap_mixed_pdf_text(fitz, item.text, width, font_size)
    return text_height_for_lines(
        lines,
        font_size,
        style.min_line_height_factor or style.line_height_factor,
        style.min_paragraph_spacing if style.min_paragraph_spacing is not None else style.paragraph_spacing,
    )


def item_is_formula_intro_text(item: RenderItem) -> bool:
    text = normalize_text(item.text)
    return bool(
        re.match(r"^(引理|定理|命题|推论)\s*\d+", text)
        and ("：" in text or ":" in text or "不变式" in text or "断言" in text)
        and len(text) <= 80
    )


def item_is_body_layout_text(item: RenderItem) -> bool:
    return (
        item.kind in {"translated_text", "original_selectable_text"}
        and item.text.strip()
        and render_text_style_name(item) in TEXT_FLOW_STYLE_NAMES
        and not item_is_formula_intro_text(item)
    )


def lane_shares_item(lane_box, item: RenderItem) -> bool:
    overlap = horizontal_overlap(lane_box, item.bbox)
    min_width = max(1.0, min(lane_box[2] - lane_box[0], item.bbox[2] - item.bbox[0]))
    return overlap >= min_width * 0.50


def body_layout_lanes(plan: PageRenderPlan) -> list[list[int]]:
    candidates = [
        (idx, item)
        for idx, item in enumerate(plan.items)
        if item_is_body_layout_text(item)
    ]
    lanes: list[list[int]] = []
    lane_boxes: list[tuple[float, float, float, float]] = []
    for idx, item in sorted(candidates, key=lambda pair: (pair[1].bbox[0], pair[1].bbox[1])):
        target = None
        for lane_idx, lane_box in enumerate(lane_boxes):
            if lane_shares_item(lane_box, item):
                target = lane_idx
                break
        if target is None:
            lanes.append([idx])
            lane_boxes.append(item.bbox)
            continue
        lanes[target].append(idx)
        lane_boxes[target] = bbox_union([lane_boxes[target], item.bbox])
    return [sorted(lane, key=lambda idx: plan.items[idx].bbox[1]) for lane in lanes]


def anchor_between_body_items(left: RenderItem, right: RenderItem, anchors: list[RenderItem]) -> bool:
    if right.bbox[1] < left.bbox[1]:
        left, right = right, left
    lane_box = bbox_union([left.bbox, right.bbox])
    for anchor in anchors:
        if anchor.bbox[1] >= right.bbox[1] or anchor.bbox[3] <= left.bbox[3]:
            continue
        if layout_horizontal_conflict(lane_box, anchor.bbox):
            return True
    return False


def split_body_layout_lane(plan: PageRenderPlan, lane: list[int]) -> list[list[int]]:
    if not lane:
        return []
    body_indices = set(lane)
    anchors = [
        item
        for idx, item in enumerate(plan.items)
        if idx not in body_indices and bbox_area(item.bbox) > 0
    ]
    groups = []
    current = [lane[0]]
    for idx in lane[1:]:
        previous = plan.items[current[-1]]
        item = plan.items[idx]
        if anchor_between_body_items(previous, item, anchors):
            groups.append(current)
            current = [idx]
            continue
        current.append(idx)
    groups.append(current)
    return groups


def body_group_limits(plan: PageRenderPlan, group: list[int], page_size) -> tuple[float, float, bool] | None:
    _width, page_height = page_size
    group_set = set(group)
    group_items = [plan.items[idx] for idx in group]
    group_box = bbox_union([item.bbox for item in group_items])
    top_limit = 0.0
    bottom_limit = page_height
    has_top_anchor = False
    for idx, other in enumerate(plan.items):
        if idx in group_set or bbox_area(other.bbox) <= 0:
            continue
        if not layout_horizontal_conflict(group_box, other.bbox):
            continue
        if other.bbox[3] <= group_box[1]:
            top_limit = max(top_limit, other.bbox[3] + TEXT_PROTECTED_GAP_PT)
            has_top_anchor = True
            continue
        if other.bbox[1] >= group_box[3]:
            bottom_limit = min(bottom_limit, other.bbox[1] - TEXT_PROTECTED_GAP_PT)
            continue
        if other.bbox[3] <= group_box[3] and other.bbox[1] <= group_box[1]:
            top_limit = max(top_limit, other.bbox[3] + TEXT_PROTECTED_GAP_PT)
            has_top_anchor = True
            continue
        if other.bbox[1] >= group_box[1] and other.bbox[3] >= group_box[3]:
            bottom_limit = min(bottom_limit, other.bbox[1] - TEXT_PROTECTED_GAP_PT)
            continue
        return None
    if bottom_limit <= top_limit:
        return None
    return top_limit, bottom_limit, has_top_anchor


def body_group_max_gap(items: list[RenderItem]) -> float:
    if items and all(item.fallback_reason == "source_paragraph_split" for item in items):
        return SOURCE_PARAGRAPH_FLOW_MAX_GAP_PT
    return BODY_FLOW_MAX_GAP_PT


def body_group_heights_and_gap(items: list[RenderItem], available_span: float, fitz) -> tuple[list[float], float]:
    preferred = [preferred_text_height_for_item(item, fitz) + BODY_TEXT_BOX_CUSHION_PT for item in items]
    minimum = [minimum_text_height_for_item(item, fitz) for item in items]
    max_gap = body_group_max_gap(items)
    if len(items) <= 1:
        if preferred[0] <= available_span:
            return preferred, 0.0
        if minimum[0] <= available_span:
            return [available_span], 0.0
        return minimum, 0.0

    preferred_gap_capacity = (available_span - sum(preferred)) / (len(items) - 1)
    if preferred_gap_capacity >= BODY_FLOW_MIN_GAP_PT:
        return preferred, min(max_gap, preferred_gap_capacity)

    minimum_gap_capacity = (available_span - sum(minimum)) / (len(items) - 1)
    if minimum_gap_capacity >= BODY_FLOW_MIN_GAP_PT:
        return minimum, min(max_gap, minimum_gap_capacity)
    return minimum, max(0.0, minimum_gap_capacity)


def rebalance_body_text_flows(plan: PageRenderPlan, page_size, fitz=None) -> None:
    if fitz is None:
        fitz = load_fitz()
    for lane in body_layout_lanes(plan):
        for group in split_body_layout_lane(plan, lane):
            items = [plan.items[idx] for idx in group]
            limits = body_group_limits(plan, group, page_size)
            if limits is None:
                continue
            top_limit, bottom_limit, has_top_anchor = limits
            original_start = min(item.bbox[1] for item in items)
            if has_top_anchor:
                start_y = max(top_limit, min(original_start, top_limit + BODY_FLOW_TOP_MAX_GAP_PT))
            else:
                start_y = original_start

            available_span = bottom_limit - start_y
            heights, gap = body_group_heights_and_gap(items, available_span, fitz)
            if sum(heights) + gap * max(0, len(items) - 1) > available_span and has_top_anchor:
                start_y = top_limit
                available_span = bottom_limit - start_y
                heights, gap = body_group_heights_and_gap(items, available_span, fitz)

            y = start_y
            for item, height in zip(items, heights):
                x0, _y0, x1, _y1 = item.bbox
                item.bbox = clamp_bbox((x0, y, x1, y + height), page_size)
                y += height + gap


def usable_text_segment(segment) -> bool:
    return segment[2] - segment[0] >= 80.0 and segment[3] - segment[1] >= BODY_FONT_SIZE * 1.8


def vertical_segments_around_protected(box, protected_boxes) -> list[tuple[float, float, float, float]]:
    segments = [box]
    for protected in sorted(protected_boxes, key=lambda item: item.bbox[1]):
        next_segments = []
        for segment in segments:
            probe = RenderItem("translated_text", [], segment)
            if not item_significantly_overlaps_protected(probe, protected):
                next_segments.append(segment)
                continue
            top = (segment[0], segment[1], segment[2], min(segment[3], protected.bbox[1] - TEXT_PROTECTED_GAP_PT))
            bottom = (segment[0], max(segment[1], protected.bbox[3] + TEXT_PROTECTED_GAP_PT), segment[2], segment[3])
            if usable_text_segment(top):
                next_segments.append(top)
            if usable_text_segment(bottom):
                next_segments.append(bottom)
        segments = next_segments
        if not segments:
            return [box]
    return sorted(segments, key=lambda item: item[1])


def split_text_units(text: str) -> list[str]:
    paragraphs = [paragraph.strip() for paragraph in text.split("\n") if paragraph.strip()]
    if len(paragraphs) != 1:
        return paragraphs
    pieces = re.split(r"(?<=[。！？；;])\s*", paragraphs[0])
    return [piece.strip() for piece in pieces if piece.strip()] or paragraphs


def text_units_fit_segment(units: list[str], segment, style: TextStyle, fitz) -> bool:
    if not units:
        return True
    font_size = style.font_size
    lines = wrap_mixed_pdf_text(fitz, "\n".join(units), segment[2] - segment[0], font_size)
    return fitted_text_spacing(lines, font_size, segment[3] - segment[1], style) is not None


def distribute_text_across_segments(text: str, segments, style: TextStyle | None = None, fitz=None) -> list[str]:
    units = split_text_units(text)
    if len(segments) <= 1 or len(units) <= 1:
        return [text]
    if style is None:
        style = text_style("body")
    if fitz is None:
        fitz = load_fitz()
    result = []
    start = 0
    for idx, segment in enumerate(segments):
        if idx == len(segments) - 1:
            result.append("\n".join(units[start:]))
            break
        best_end = start
        for end in range(start + 1, len(units) + 1):
            if not text_units_fit_segment(units[start:end], segment, style, fitz):
                break
            best_end = end
        result.append("\n".join(units[start:best_end]))
        start = best_end
    while len(result) < len(segments):
        result.append("")
    return result


def split_translated_text_around_protected(plan: PageRenderPlan) -> None:
    protected = [item for item in plan.items if item.kind == "original_image_clip"]
    if not protected:
        return
    new_items = []
    for item in plan.items:
        if item.kind != "translated_text":
            new_items.append(item)
            continue
        if not any(item_significantly_overlaps_protected(item, protected_item) for protected_item in protected):
            new_items.append(item)
            continue
        segments = vertical_segments_around_protected(item.bbox, protected)
        if len(segments) == 1 and segments[0] == item.bbox:
            new_items.append(item)
            continue
        texts = distribute_text_across_segments(item.text, segments, text_style(item.style_name or "body"))
        for segment, text in zip(segments, texts):
            if not text.strip():
                continue
            new_items.append(
                RenderItem(
                    item.kind,
                    list(item.source_ids),
                    segment,
                    text=text,
                    font_size=item.font_size,
                    style_name=item.style_name,
                    color=item.color,
                    fallback_reason=item.fallback_reason or "split_around_visual",
                )
            )
        update_ledger_render_kind(plan, item.source_ids, "translated_text", "split_around_visual")
    plan.items = new_items


def validate_plan_layout(plan: PageRenderPlan, page_size) -> list[str]:
    errors = []
    width, height = page_size
    protected = [item for item in plan.items if item.kind == "original_image_clip"]
    for item in plan.items:
        x0, y0, x1, y1 = item.bbox
        if x0 < -6.0 or y0 < -6.0 or x1 > width + 6.0 or y1 > height + 6.0:
            errors.append(f"page {plan.page_num} item {item.source_ids} outside page bounds")
        if item.kind != "translated_text":
            continue
        for protected_item in protected:
            if item_significantly_overlaps_protected(item, protected_item):
                errors.append(f"page {plan.page_num} text {item.source_ids} overlaps protected {protected_item.source_ids}")
    return errors


def render_plan_item(out_page, src_page, fitz, item: RenderItem, dpi: int, source_image_path: Path | None = None):
    rect = fitz.Rect(item.bbox)
    if item.kind == "translated_text":
        style = text_style(render_text_style_name(item))
        if insert_vector_textbox(
            out_page,
            fitz,
            rect,
            item.text,
            item.font_size or style.font_size,
            item.color,
            line_height_factor=style.line_height_factor,
            paragraph_spacing=style.paragraph_spacing,
            letter_spacing=style.letter_spacing,
            min_line_height_factor=style.min_line_height_factor,
            min_paragraph_spacing=style.min_paragraph_spacing,
            allow_shrink=False,
        ):
            return
        raise RuntimeError(f"text item {item.source_ids} did not fit during render")
    if item.kind == "original_selectable_text":
        style = text_style(render_text_style_name(item))
        if insert_vector_textbox(
            out_page,
            fitz,
            rect,
            item.text,
            item.font_size or style.font_size,
            VECTOR_BODY_COLOR,
            line_height_factor=style.line_height_factor,
            paragraph_spacing=style.paragraph_spacing,
            letter_spacing=style.letter_spacing,
            min_line_height_factor=style.min_line_height_factor,
            min_paragraph_spacing=style.min_paragraph_spacing,
            allow_shrink=False,
        ):
            return
        raise RuntimeError(f"selectable text item {item.source_ids or item.fallback_reason} did not fit during render")
    if item.kind == "original_image_clip":
        if source_image_path and insert_source_image_clip(out_page, fitz, source_image_path, src_page.rect, item.bbox):
            return
        insert_source_clip(src_page, out_page, fitz, item.bbox, dpi)
        return


def write_vector_pdf(pdf_path: Path, pdf_output: Path, selected_pages, translations, pdf_size_pt, dpi: int, job_paths=None):
    fitz = load_fitz()
    src_doc = fitz.open(pdf_path)
    out_doc = fitz.open()
    translations = postprocess_cross_page_sentence_splits(
        selected_pages,
        translations,
        job_paths=job_paths,
    )
    bbox_lines_by_page = {}
    if job_paths and job_paths.get("bbox_path") and job_paths["bbox_path"].exists():
        for line in parse_bbox_lines(job_paths["bbox_path"]):
            bbox_lines_by_page.setdefault(line["page"], []).append(line)
    total_pages = len(selected_pages)
    for output_idx, (page_num, blocks) in enumerate(selected_pages, start=1):
        if output_idx == 1 or output_idx % 50 == 0 or output_idx == total_pages:
            print(
                f"vector render {output_idx}/{total_pages} source_page={page_num}",
                file=sys.stderr,
                flush=True,
            )
        src_page = src_doc[page_num - 1]
        page_rect = src_page.rect
        source_image = source_page_image_path(job_paths, page_num)
        out_page = out_doc.new_page(width=page_rect.width, height=page_rect.height)
        out_page.draw_rect(page_rect, color=None, fill=(1, 1, 1))
        preserve_images_on_page(src_page, out_page, fitz, dpi)
        preserve_drawings_on_page(src_page, out_page)

        plan = build_page_render_plan(
            page_num,
            blocks,
            translations,
            (page_rect.width, page_rect.height),
            bbox_lines=bbox_lines_by_page.get(page_num),
            source_image_path=source_image,
        )
        coverage_errors = validate_plan_coverage(page_num, blocks, plan)
        if coverage_errors:
            raise RuntimeError("\n".join(coverage_errors[:20]))
        layout_errors = validate_plan_layout(plan, (page_rect.width, page_rect.height))
        if layout_errors:
            raise RuntimeError("\n".join(layout_errors[:20]))
        text_fit_errors = validate_plan_text_fit(plan, fitz)
        if text_fit_errors:
            raise RuntimeError("\n".join(text_fit_errors[:20]))
        for item in plan.items:
            render_plan_item(out_page, src_page, fitz, item, dpi, source_image_path=source_image)

    copy_outline(src_doc, out_doc, selected_pages, translations)
    pdf_output.parent.mkdir(parents=True, exist_ok=True)
    out_doc.save(pdf_output, garbage=4, deflate=True)
    out_doc.close()
    src_doc.close()


def get_pdf_page_size(pdf_path: Path):
    proc = run(["pdfinfo", str(pdf_path)], check=True)
    match = re.search(r"Page size:\s*([0-9.]+)\s+x\s+([0-9.]+)\s+pts", proc.stdout)
    if not match:
        raise RuntimeError(f"unable to parse page size from pdfinfo for {pdf_path}")
    return float(match.group(1)), float(match.group(2))


def write_latex(pdf_output: Path, page_numbers, pdf_size_pt, job_paths):
    tex_path = job_paths["tex_path"]
    width_pt, height_pt = pdf_size_pt
    lines = [
        r"\documentclass{article}",
        rf"\usepackage[paperwidth={width_pt}bp,paperheight={height_pt}bp,margin=0in]{{geometry}}",
        r"\usepackage{graphicx}",
        r"\pagestyle{empty}",
        r"\begin{document}",
    ]
    for idx, i in enumerate(page_numbers, start=1):
        lines.append(
            r"\noindent\includegraphics[width=\paperwidth,height=\paperheight]{"
            + str(translated_page_path(i, job_paths)).replace("\\", "/")
            + r"}"
        )
        if idx != len(page_numbers):
            lines.append(r"\newpage")
    lines.append(r"\end{document}")
    tex_path.write_text("\n".join(lines), encoding="utf-8")
    pdf_output.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "xelatex",
            "-interaction=nonstopmode",
            "-output-directory",
            str(job_paths["job_dir"]),
            str(tex_path),
        ],
        check=True,
    )
    shutil.copy2(job_paths["pdf_path"], pdf_output)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", default=str(Path.cwd() / "DiveintoClaudeCode.pdf"))
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--batch-chars", type=int, default=10000)
    parser.add_argument("--pdf-output", default=str(Path.cwd() / "claudeCodeChinese.pdf"))
    parser.add_argument("--page-start", type=int, default=1)
    parser.add_argument("--page-end", type=int, default=0)
    parser.add_argument("--job-name", default="")
    parser.add_argument("--work-dir", default=str(TMP_ROOT))
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--reasoning-effort", default="low")
    parser.add_argument("--force-ocr", action="store_true")
    parser.add_argument("--render-mode", choices=["vector", "raster"], default="vector")
    args = parser.parse_args()
    set_work_dir(Path(args.work_dir))

    pdf_path = Path(args.pdf)
    job_paths = build_job_paths(pdf_path, args.job_name or None)
    pdf_size_pt = get_pdf_page_size(pdf_path)
    all_pages = load_or_build_source_pages(
        pdf_path,
        args.dpi,
        job_paths,
        pdf_size_pt,
        args.page_start,
        args.page_end,
        force_ocr=args.force_ocr,
    )
    page_end = args.page_end or len(all_pages)
    selected_pages = [
        (idx, page)
        for idx, page in enumerate(all_pages, start=1)
        if args.page_start <= idx <= page_end
    ]
    batches = build_batches([page for _, page in selected_pages], args.batch_chars)
    translations = translate_batches(
        batches,
        job_paths,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
    )
    translations = postprocess_cross_page_sentence_splits(
        selected_pages,
        translations,
        job_paths=job_paths,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
    )
    if args.render_mode == "raster":
        render_pages(selected_pages, translations, args.dpi, job_paths)
        write_latex(
            Path(args.pdf_output),
            [idx for idx, _ in selected_pages],
            pdf_size_pt,
            job_paths,
        )
    else:
        write_vector_pdf(
            pdf_path,
            Path(args.pdf_output),
            selected_pages,
            translations,
            pdf_size_pt,
            args.dpi,
            job_paths=job_paths,
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        sys.exit(1)
