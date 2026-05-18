import math
import re


NORMAL_TRANSLATED_CLASSES = {"body", "heading", "subheading", "title"}
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
VISUAL_CAPTION_NUMBER_PATTERN = r"\d+[0-9il]*(?:[-‐‑‒–—.]\d+[0-9il]*)?"


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


def is_table_caption_line(text: str) -> bool:
    normalized = re.sub(r"\s+", "", normalize_text(text).strip().lower())
    return bool(re.match(rf"^table{VISUAL_CAPTION_NUMBER_PATTERN}[:.|]", normalized))


def is_visual_caption_line(text: str) -> bool:
    normalized = re.sub(r"\s+", "", normalize_text(text).strip().lower())
    return bool(re.match(rf"^(fig\.?|figure|table){VISUAL_CAPTION_NUMBER_PATTERN}[:.|]", normalized))


def is_table_caption(text: str) -> bool:
    first_line = normalize_text(text).split("\n", 1)[0].strip()
    return is_table_caption_line(first_line)


def is_visual_caption(text: str) -> bool:
    first_line = normalize_text(text).split("\n", 1)[0].strip()
    return is_visual_caption_line(first_line)


def contains_visual_caption(text: str) -> bool:
    normalized = normalize_text(text).lower()
    lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    for idx, line in enumerate(lines):
        if not is_visual_caption_line(line):
            continue
        if idx == 0:
            return True
        prefix = " ".join(lines[:idx])
        if len(re.findall(r"[A-Za-z]{3,}", prefix)) >= 8:
            continue
        if re.search(r"[.!?][\"')\]）】”’]*", prefix):
            continue
        return True
    return False


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


def cjk_char_count(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def latin_words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z][A-Za-z-]{2,}", normalize_text(text))


def english_function_word_count(text: str) -> int:
    return sum(1 for word in latin_words(text) if word.lower() in ENGLISH_FUNCTION_WORDS)


def is_numeric_metric_cell(text: str) -> bool:
    normalized = normalize_text(text)
    if not normalized or cjk_char_count(normalized) > 0:
        return False
    if len(normalized) > 120 or not re.search(r"\d", normalized):
        return False
    words = latin_words(normalized)
    if len(words) > 8 or english_function_word_count(normalized) >= 2:
        return False
    if is_prose_row_text(normalized):
        return False
    if re.search(r"[.!?][\"')\]）】”’]*\s+[A-Z][a-z]", normalized):
        return False
    unit_pattern = r"(?i)\b\d+(?:\.\d+)?\s*(?:[KMGTPE]?i?B|GB/s|MB/s|KB/s|TB/s|ms|ns|us|µs|s|%)\b"
    return bool(
        "%"
        in normalized
        or normalized.strip().startswith("~")
        or re.search(unit_pattern, normalized)
        or re.search(r"\d+(?:\.\d+)?\s*[x×]", normalized, flags=re.I)
    )


def is_fragmented_narrow_table_cell(block) -> bool:
    text = normalize_text(block.get("text", ""))
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if len(lines) < 3 or cjk_char_count(text) > 0:
        return False
    width = block.get("xMax", 0) - block.get("xMin", 0)
    height = block.get("yMax", 0) - block.get("yMin", 0)
    if width > 95.0 or height < 42.0:
        return False
    if any(len(line) > 18 for line in lines):
        return False
    if sum(1 for line in lines if " " in line) > 1:
        return False
    joined = " ".join(lines)
    if re.search(r"[.!?。！？]", joined):
        return False
    return bool(re.search(r"[A-Za-z0-9]", joined))


def should_preserve_as_image(block) -> bool:
    if block.get("preserve_image"):
        return True
    text = block.get("text", "")
    return (
        is_visual_caption(text)
        or is_formula_like(text)
        or is_numeric_metric_cell(text)
        or is_fragmented_narrow_table_cell(block)
    )


def is_reference_heading(text: str) -> bool:
    return bool(re.fullmatch(r"(?i)references|bibliography", normalize_text(text)))


def starts_reference_item(text: str) -> bool:
    return bool(
        re.match(
            r"^\s*(?:\[\d{1,3}\]|\d{1,3}\.)\s*[A-Z][A-Za-z-]+,",
            normalize_text(text),
        )
    )


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


def is_publication_header_fragment(block) -> bool:
    text = normalize_text(block.get("text", ""))
    if block.get("page") != 1 or block["yMin"] > 72.0 or not text or len(text) > 180:
        return False
    lower = text.lower()
    if re.fullmatch(r"\(?20\d{2}\)?\s+\d{1,4}:\d{1,5}", text):
        return True
    if re.match(r"(?i)^journal\s+of\b", text) or re.match(r"(?i)^proceedings\s+of\b", text):
        return True
    if re.match(r"(?i)^journal\s+of\s+l\s*a\s*tex\s+class\s+files\b", text):
        return True
    if any(marker in lower for marker in ("journal of", "transactions on", "proceedings of", "vol.", "issn")):
        return bool(re.search(r"\b20\d{2}\b|\bvol\.|\bno\.", lower))
    return False


def is_conference_footer_fragment(block) -> bool:
    text = normalize_text(block.get("text", ""))
    return (
        block.get("page", 1) > 1
        and block["yMin"] > 700.0
        and len(text) <= 40
        and bool(re.fullmatch(r"[A-Z][A-Z0-9&./-]{1,16}\s+20\d{2}", text))
    )


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
    return bool(
        re.search(
            r"(?m)(?:^|\n)\s*(?:\[\d{1,3}\]|\d{1,3}\.)\s*[A-Z][A-Za-z-]+,",
            normalize_text(text),
        )
    )


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


def is_first_page_author_block(block) -> bool:
    text = normalize_text(block.get("text", ""))
    if block.get("page") != 1 or not text:
        return False
    if not (80.0 <= block["yMin"] <= 380.0):
        return False
    if "@" in text:
        return len(text) <= 220 and bool(re.search(r"[A-Za-z]", text))
    if not (140.0 <= block["yMin"] <= 340.0):
        return False
    if len(text) > 220:
        return False
    if text.lower() in {"abstract", "introduction", "references"}:
        return False
    if re.search(r"\d|[:;!?]", text):
        return False
    words = re.findall(r"[A-Za-z][A-Za-z.'-]*", text)
    if not (1 <= len(words) <= 5):
        return False
    if english_function_word_count(text) > 0:
        return False
    return True


def is_first_page_arxiv_side_metadata_block(block) -> bool:
    text = normalize_text(block.get("text", ""))
    return (
        block.get("page") == 1
        and block["xMax"] <= 70.0
        and block["yMin"] < 600.0
        and "arXiv:" in text
    )


def should_preserve_first_page_metadata_as_image(block) -> bool:
    return is_first_page_author_block(block) or is_first_page_arxiv_side_metadata_block(block)


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


def is_code_like_line(line: str) -> bool:
    line = line.strip()
    if not line:
        return False
    compact = re.sub(r"\s+", "", line.lower())
    if re.match(r"^import\s+[A-Za-z_][\w.]*(?:\s+as\s+[A-Za-z_]\w*)?$", line):
        return True
    if re.match(r"^from\s+[A-Za-z_][\w.]*\s+import\s+.+", line):
        return True
    if re.match(r"^[A-Za-z_][\w.-]*(?:\s+--?[A-Za-z0-9][\w-]*(?:[=\s]\S+)*)+\s*\\?$", line):
        return True
    if re.match(r"^--?[A-Za-z0-9][\w-]*(?:[=\s]\S+)*\s*\\?$", line):
        return True
    if re.search(r"\b(?:python|numactl|ncu|nsys|pip|conda|torchrun)\b", line) and "--" in line:
        return True
    if re.search(r"\b(?:reinterpret_cast|static_cast|sizeof|cuda::|std::|__global__|__launch_bounds__)\b", line):
        return True
    if re.search(r"[A-Za-z_][\w:<>]*\s*\([^)]*$", line):
        return True
    if line.endswith((",", ";", "\\")) and re.search(r"[()<>*_:]", line):
        return True
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
        return True
    if line.startswith("//"):
        return True
    if re.search(r"(;|::|->|<<|>>|#include|\{\}|\{|\})", line):
        return True
    if re.search(r"[A-Za-z_][\w.>\-]*\s*=\s*[^=]", line):
        return True
    if re.match(r"^(if|then|else|while|return|end|do)\b", line.lower()):
        return True
    if re.match(r"^for\b.+\bdo\b", line.lower()) or re.search(r"\b(if|while|for|switch)\s*\(", line):
        return True
    if re.match(r"^(auto|const|static|std::|nixl_[A-Za-z_]+)\b", line):
        return True
    return bool(re.fullmatch(r"[A-Za-z_][\w.>\-]*(?:\([^)]*\))?;?", line) and re.search(r"[._:]", line))


def is_profiler_output_block(text: str) -> bool:
    normalized = normalize_text(text)
    lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    if len(lines) < 5:
        return False
    lowered = [line.lower() for line in lines]
    has_header = any("samples" in line and "command" in line for line in lowered) or any(
        "shared object" in line for line in lowered
    )
    dotted_lines = sum(1 for line in lines if re.fullmatch(r"[#.\s]{8,}", line))
    percent_rows = sum(1 for line in lines if re.search(r"\d+(?:\.\d+)?%", line))
    path_or_library_rows = sum(1 for line in lines if "/" in line or re.search(r"\.so(?:\.\d+)?\b", line))
    return has_header and (dotted_lines >= 1 or percent_rows >= 2) and (percent_rows + path_or_library_rows) >= 3


def is_yaml_config_block(text: str) -> bool:
    normalized = normalize_text(text)
    lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    if len(lines) < 5:
        return False
    key_lines = sum(1 for line in lines if re.match(r"^[A-Za-z_][A-Za-z0-9_.-]*\s*:", line))
    value_lines = sum(
        1
        for line in lines
        if re.match(r"^[A-Za-z_][A-Za-z0-9_.-]*\s*:\s*(?:[A-Za-z0-9_.-]+|true|false|\.\.\.|\{.*)?$", line)
    )
    prose_lines = sum(1 for line in lines if len(latin_words(line)) >= 6 and english_function_word_count(line) >= 1)
    return key_lines >= max(4, math.ceil(len(lines) * 0.55)) and value_lines >= 3 and prose_lines <= 1


def is_code_listing_block(text: str) -> bool:
    normalized = normalize_text(text)
    if is_profiler_output_block(normalized) or is_yaml_config_block(normalized):
        return True
    lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    if len(lines) < 2:
        return False
    code_markers = 0
    for line in lines:
        if is_code_like_line(line):
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
    if lines and all(re.match(r"^(?:[A-Za-z_][\w.:>\-]*\s*)?=\s*[^=].*;?$", line) for line in lines):
        return True
    if lines and all(is_code_like_line(line) for line in lines):
        return True
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


def is_code_line_number_block(text: str) -> bool:
    lines = [line.strip() for line in normalize_text(text).split("\n") if line.strip()]
    return bool(lines) and len(lines) <= 20 and all(re.fullmatch(r"\d{1,3}", line) for line in lines)


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


def is_visual_row_text(text: str) -> bool:
    normalized = normalize_text(text)
    return (
        is_visual_caption(normalized)
        or contains_visual_caption(normalized)
        or is_formula_or_code_block(normalized)
        or is_numeric_metric_cell(normalized)
        or is_code_row_text(normalized)
        or is_code_line_number_block(normalized)
    )


def is_body_enumeration_line(text: str) -> bool:
    normalized = normalize_text(text)
    return bool(re.match(r"^\(\d+\)\s+[A-Za-z][A-Za-z0-9() ]+\bis\b", normalized))


def is_non_prose_identifier_text(text: str) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return True
    if re.fullmatch(r"https?://\S+", normalized, flags=re.I):
        return True
    if normalized.startswith(("©", "Copyright ")) or "all rights reserved" in normalized.lower():
        return True
    if is_formula_or_code_block(normalized) or is_code_row_text(normalized):
        return True
    words = latin_words(normalized)
    if not words:
        return False
    if len(normalized) <= 90 and english_function_word_count(normalized) == 0:
        if not re.search(r"[.!?]\s+[A-Z]", normalized):
            return True
    return False


def source_requires_chinese_translation(text: str) -> bool:
    cleaned = strip_journal_footer_lines(text)
    if is_non_prose_identifier_text(cleaned):
        return False
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
    if chinese_chars >= 20:
        return False
    if chinese_chars >= 8 and chinese_chars >= latin_chars * 0.35:
        return False
    if chinese_chars == 0 and len(words) >= 3:
        return True
    if function_words >= 3 and chinese_chars < 5:
        return True
    if len(translated) >= 80 and function_words >= 6 and latin_chars > max(40, chinese_chars * 1.2):
        return True
    return False


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
        if (
            is_decorative_update_marker(text)
            or is_first_page_footer_fragment(block)
            or is_publication_header_fragment(block)
            or is_conference_footer_fragment(block)
            or is_running_header_fragment(block)
        ):
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
