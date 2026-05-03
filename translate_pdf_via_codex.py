#!/usr/bin/env python3

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from statistics import median

from PIL import Image, ImageDraw, ImageFont


TOOL_ROOT = Path(__file__).resolve().parent
TMP_ROOT = TOOL_ROOT / "work"
VENDOR_ROOT = TOOL_ROOT / "vendor"
FONT_PATH = "/usr/share/fonts/truetype/arphic/uming.ttc"
FORMULA_COLUMN_FRACTION = 0.52
FORMULA_PAD_TOP_PX = 8
FORMULA_PAD_BOTTOM_PX = 4


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
        "translations_path": job_dir / "translations.json",
        "tex_path": job_dir / "claudeCodeChinese.tex",
        "pdf_path": job_dir / "claudeCodeChinese.pdf",
    }


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


def ensure_assets(pdf_path: Path, dpi: int, job_paths) -> Path:
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    job_paths["job_dir"].mkdir(parents=True, exist_ok=True)
    job_paths["pages_dir"].mkdir(parents=True, exist_ok=True)
    job_paths["translated_pages_dir"].mkdir(parents=True, exist_ok=True)
    bbox_path = job_paths["bbox_path"]
    if not bbox_path.exists():
        proc = run(["pdftotext", "-bbox-layout", str(pdf_path), "-"], check=True)
        bbox_path.write_text(proc.stdout, encoding="utf-8")
    first_page = job_paths["pages_dir"] / "page-001.png"
    if not first_page.exists():
        prefix = str(job_paths["pages_dir"] / "page")
        run(["pdftocairo", "-png", "-r", str(dpi), str(pdf_path), prefix], check=True)
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
    return bool(re.fullmatch(r"\d{1,3}", text.strip()))


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
    return bool(re.match(r"^(fig\.?|figure|table)\d+[:.]", normalized))


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
    return len(compact) >= 8 and symbol_count >= 3 and word_count <= 3


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
    pages = []

    for page_idx, image_path in enumerate(sorted(job_paths["pages_dir"].glob("page-*.png")), start=1):
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
        pages.append(page_blocks)
    return pages


def load_or_build_source_pages(pdf_path: Path, dpi: int, job_paths, pdf_size_pt):
    source_pages_path = job_paths["source_pages_path"]
    if source_pages_path.exists():
        return load_source_pages(job_paths)

    bbox_path = ensure_assets(pdf_path, dpi, job_paths)
    pages = parse_bbox(bbox_path)
    if should_use_ocr(pages):
        pages = generate_ocr_pages(job_paths, pdf_size_pt)
    save_source_pages(pages, job_paths)
    return pages


def build_batches(pages, max_chars: int):
    blocks = []
    for page in pages:
        for block in page:
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
        5. 输出必须是 JSON，对象格式固定为 {"items":[{"id":"...","translation":"..."}]}。
        6. 不要输出解释，不要使用 Markdown 代码块。

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
    write_schema(schema_path)
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

    for paragraph in paragraphs:
        current = ""
        for ch in paragraph:
            candidate = current + ch
            bbox = dummy.textbbox((0, 0), candidate, font=font)
            if bbox[2] <= max_width or not current:
                current = candidate
            else:
                lines.append(current.rstrip())
                current = ch.lstrip() if ch == " " else ch
        if current:
            lines.append(current.rstrip())
    return lines or [text]


def fit_font_and_lines(text: str, box_width: int, box_height: int, vertical: bool):
    if vertical:
        max_size = max(14, min(box_width, box_height // 2))
        return max_size, [text]

    low, high = 10, max(12, min(80, box_height))
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


def draw_block(draw_img: Image.Image, block, translation: str, dpi: int, protected_boxes=None):
    box = block_to_px_box(block, dpi, draw_img.width, draw_img.height, pad=2)
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

    font_size, lines = fit_font_and_lines(translation, width, height, vertical=False)
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
        for block in blocks:
            if should_preserve_as_image(block):
                continue
            if is_page_number(block["text"]):
                continue
            translated = translations.get(block["id"])
            if not translated:
                if is_trivial_keep(block["text"]):
                    continue
                translated = block["text"]
            draw_block(img, block, translated, dpi)
        for box in protected_boxes:
            x0, y0, x1, y1 = box
            if x1 > x0 and y1 > y0:
                img.alpha_composite(source_img.crop(box), (x0, y0))
        img.save(out)


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
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--reasoning-effort", default="low")
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    job_paths = build_job_paths(pdf_path, args.job_name or None)
    pdf_size_pt = get_pdf_page_size(pdf_path)
    all_pages = load_or_build_source_pages(pdf_path, args.dpi, job_paths, pdf_size_pt)
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
    render_pages(selected_pages, translations, args.dpi, job_paths)
    write_latex(
        Path(args.pdf_output),
        [idx for idx, _ in selected_pages],
        pdf_size_pt,
        job_paths,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        sys.exit(1)
