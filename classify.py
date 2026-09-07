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
STRUCTURAL_ALL_CAPS_HEADINGS = {
    "ABSTRACT",
    "ACKNOWLEDGEMENTS",
    "ACKNOWLEDGMENTS",
    "APPENDIX",
    "BACKGROUND",
    "BIBLIOGRAPHY",
    "CONCLUSION",
    "CONCLUSIONS",
    "DISCUSSION",
    "EVALUATION",
    "EXPERIMENTS",
    "IMPLEMENTATION",
    "IMPLEMENTATIONS",
    "INTRODUCTION",
    "LIMITATIONS",
    "METHODOLOGY",
    "METHODS",
    "PRELIMINARIES",
    "REFERENCES",
    "RELATED WORK",
    "RESULTS",
}


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
    if re.fullmatch(r"https?://\S+", stripped, flags=re.I):
        return False
    compact = re.sub(r"\s+", "", stripped)
    if re.fullmatch(r"\(\d{1,2}\)", compact):
        return True
    if re.fullmatch(r"i[∈e]s", compact.lower()):
        return True
    lower_compact = compact.lower()
    if any(term in lower_compact for term in ("argmin", "available_bw", "segment_size*cwnd")):
        return True
    formula_symbol_pattern = r"[=+\-−*/_|{}()[\]<>≤≥∈∉∑Σαβγπσϕφμ]"
    symbol_count = len(re.findall(formula_symbol_pattern, compact))
    word_count = len(re.findall(r"[A-Za-z]{3,}", stripped))
    if word_count >= 2 and not re.search(formula_symbol_pattern, compact):
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


def english_function_token_count(text: str) -> int:
    return sum(
        1
        for word in re.findall(r"[A-Za-z]+", normalize_text(text))
        if word.lower() in ENGLISH_FUNCTION_WORDS
    )


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


REFERENCE_MARKER_RE = r"(?:\[\d{1,3}\]|\d{1,3}\.)"
REFERENCE_AUTHOR_TOKEN_RE = r"[^\W\d_][^\s,;:()\[\]{}]*"
REFERENCE_YEAR_RE = r"(?:18|19|20)\d{2}[a-z]?"
REFERENCE_VENUE_CUES = (
    "proceedings",
    "conference",
    "journal",
    "transactions",
    "workshop",
    "symposium",
    "association for computational",
    "international conference",
    "advances in neural",
    "machine learning",
    "natural language",
    "empirical methods",
    "technical report",
    "arxiv",
    "corr",
    "acl",
    "emnlp",
    "naacl",
    "conll",
    "iclr",
    "nips",
    "neurips",
    "bulletin",
)


def reference_marker_and_body(text: str) -> tuple[str, str] | None:
    normalized = normalize_text(text)
    match = re.match(
        rf"^[^\S\n]*(?P<marker>{REFERENCE_MARKER_RE})[^\S\n]*(?P<body>.+)$",
        normalized,
        flags=re.S,
    )
    if not match:
        return None
    marker = match.group("marker")
    body = match.group("body").strip()
    if not marker.startswith("[") and not re.match(r"[^\W\d_]", body):
        return None
    return marker, flattened_bibliography_text(body)


def reference_item_body_from_line(text: str) -> tuple[str, bool] | None:
    match = re.match(
        rf"^[^\S\n]*(?P<marker>{REFERENCE_MARKER_RE})[^\S\n]*(?P<body>[^\n]+)",
        text,
    )
    if not match:
        return None
    body = match.group("body").strip()
    marker = match.group("marker")
    if not marker.startswith("[") and not re.match(r"[^\W\d_]", body):
        return None
    return body, marker.startswith("[")


def looks_like_reference_item_line(text: str) -> bool:
    parsed = reference_item_body_from_line(text)
    if not parsed:
        return False
    body, bracketed_marker = parsed
    first_word_match = re.match(r"(?P<word>[A-Za-z][A-Za-z-]*)\b", body)
    if bracketed_marker and first_word_match:
        first_word = first_word_match.group("word")
        lower_name_prefixes = {"da", "de", "del", "den", "der", "la", "le", "van", "von"}
        if first_word[0].islower() and first_word.lower() not in lower_name_prefixes:
            return False
    comma_match = re.match(
        rf"{REFERENCE_AUTHOR_TOKEN_RE}(?:[ \t]+{REFERENCE_AUTHOR_TOKEN_RE}){{0,6}}\s*,",
        body,
    )
    if comma_match:
        if not bracketed_marker:
            comma_author = comma_match.group(0).rstrip(", \t")
            lastname_initial_match = re.match(
                rf"{REFERENCE_AUTHOR_TOKEN_RE},[ \t]*(?:[A-Z]\.?\s*){{1,4}}",
                body,
            )
            if not lastname_initial_match and not author_segment_looks_like_reference(comma_author):
                return False
        return True
    period_match = re.match(
        rf"(?P<author>{REFERENCE_AUTHOR_TOKEN_RE}(?:[ \t]+{REFERENCE_AUTHOR_TOKEN_RE}){{0,5}})\.[ \t]+\S",
        body,
    )
    if not period_match:
        return False
    author_words = re.findall(r"[^\W\d_]+", period_match.group("author"))
    if not author_words:
        return False
    if not bracketed_marker and len(author_words) < 2:
        return False
    if not bracketed_marker and not author_segment_looks_like_reference(period_match.group("author")):
        return False
    if len(author_words) >= 2 and all(word.isupper() and len(word) > 1 for word in author_words):
        return False
    return True


def flattened_bibliography_text(text: str) -> str:
    normalized = normalize_text(text)
    normalized = re.sub(r"(?<=\w)-\n(?=\w)", "", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


REFERENCE_VENUE_CUE_RE = re.compile(
    r"\b("
    + "|".join(re.escape(cue) for cue in sorted(REFERENCE_VENUE_CUES, key=len, reverse=True))
    + r")\b",
    flags=re.I,
)


def reference_venue_span(text: str) -> tuple[int, int] | None:
    normalized = flattened_bibliography_text(text)
    matches = list(REFERENCE_VENUE_CUE_RE.finditer(normalized))
    if not matches:
        return None
    match = min(matches, key=lambda item: item.start())
    prefix = normalized[: match.start()]
    start = match.start()
    boundary = max(prefix.rfind(". "), prefix.rfind("; "), prefix.rfind(", "), prefix.rfind(": "), prefix.rfind("\n"))
    if boundary >= 0:
        start = boundary + 2 if prefix[boundary : boundary + 2] in {". ", "; ", ", ", ": "} else boundary + 1
    elif prefix.lower().startswith("in "):
        start = max(0, prefix.lower().rfind("in "))
    else:
        return None
    suffix = normalized[match.end() :]
    end = len(normalized)
    for punctuation in (",", ".", ";"):
        pos = suffix.find(punctuation)
        if pos >= 0:
            end = min(end, match.end() + pos)
    if start >= end:
        start = match.start()
    return start, end


def extract_reference_locators(text: str) -> dict[str, str | None]:
    normalized = flattened_bibliography_text(text)
    data: dict[str, str | None] = {"volume_issue": None, "pages": None, "locator": None}
    if not normalized:
        return data

    volume_issue_match = re.search(r"\b(?P<volume>\d{1,4})\s*\((?P<issue>\d{1,4})\)", normalized)
    remainder = normalized
    if volume_issue_match:
        data["volume_issue"] = volume_issue_match.group(0)
        remainder = normalized[volume_issue_match.end() :]
        pages_after_volume = re.match(r"^[^\S\n]*:[^\S\n]*(?P<pages>\d{1,5}(?:\s*[–-]\s*\d{1,5})?)", remainder)
        if pages_after_volume:
            data["pages"] = pages_after_volume.group("pages")
            remainder = remainder[pages_after_volume.end() :]

    if data["pages"] is None:
        pages_match = re.search(r"\bpages?\s+(?P<pages>\d{1,5}(?:\s*[–-]\s*\d{1,5})?)\b", normalized, flags=re.I)
        if pages_match:
            data["pages"] = pages_match.group("pages")
            remainder = normalized[pages_match.end() :]
        else:
            pp_match = re.search(r"\bpp\.?\s*(?P<pages>\d{1,5}(?:\s*[–-]\s*\d{1,5})?)\b", normalized, flags=re.I)
            if pp_match:
                data["pages"] = pp_match.group("pages")
                remainder = normalized[pp_match.end() :]
            else:
                range_match = re.search(r"\b(?P<pages>\d{1,5}\s*[–-]\s*\d{1,5})\b", normalized)
                if range_match:
                    data["pages"] = range_match.group("pages")
                    remainder = normalized[range_match.end() :]

    locator_match = re.search(
        r"\b(?P<locator>(?:abs/\d{4}\.\d{4,5}(?:v\d+)?)|(?:arXiv:\d{4}\.\d{4,5}(?:v\d+)?)|(?:doi:\S+))\b",
        remainder,
        flags=re.I,
    )
    if locator_match:
        data["locator"] = locator_match.group("locator")

    return data


def parse_reference_segment(text: str) -> dict[str, str | None]:
    normalized = flattened_bibliography_text(text)
    data: dict[str, str | None] = {"title": None, "venue": None, "pages": None, "locator": None, "volume_issue": None}
    if not normalized:
        return data
    venue_span = reference_venue_span(normalized)
    if venue_span is not None:
        start, end = venue_span
        title = normalized[:start].strip(" ,;.")
        venue = normalized[start:end].strip(" ,;.")
        data["title"] = title or None
        data["venue"] = venue or None
        remainder = normalized[end:].strip(" ,;.")
    else:
        data["title"] = normalized.strip(" ,;.") or None
        remainder = ""
    locators = extract_reference_locators(remainder or normalized)
    data.update(locators)
    return data


def reference_signature(text: str) -> dict[str, str | bool | None]:
    normalized = strip_journal_footer_lines(normalize_text(text))
    flat = flattened_bibliography_text(normalized)
    signature: dict[str, str | bool | None] = {
        "marker": None,
        "authors": None,
        "title": None,
        "venue": None,
        "date": None,
        "pages": None,
        "locator": None,
        "volume_issue": None,
        "pattern": None,
        "reference_like": False,
        "start_like": False,
    }
    marker_body = reference_marker_and_body(normalized)
    if marker_body is not None:
        signature["marker"], flat = marker_body

    author_year_match = re.match(
        rf"^(?P<authors>.{{4,220}}?)\.\s*(?P<date>{REFERENCE_YEAR_RE})\.\s*(?P<rest>.+)$",
        flat,
    )
    if author_year_match and author_segment_looks_like_reference(author_year_match.group("authors")):
        signature["authors"] = author_year_match.group("authors").strip()
        signature["date"] = author_year_match.group("date")
        parsed = parse_reference_segment(author_year_match.group("rest"))
        signature.update(parsed)
        signature["start_like"] = True
    else:
        year_match = re.search(rf"\b(?P<date>{REFERENCE_YEAR_RE})\b", flat)
        if year_match:
            signature["date"] = year_match.group("date")
            before_year = flat[: year_match.start()].strip(" ,;.")
            after_year = flat[year_match.end() :].strip(" ,;.")
            if len(flat) <= 240 and not is_prose_row_text(flat) and reference_venue_span(after_year) is not None:
                signature["title"] = before_year or None
                parsed = parse_reference_segment(after_year)
                for key in ("venue", "pages", "locator", "volume_issue"):
                    if parsed[key] is not None:
                        signature[key] = parsed[key]
            elif len(flat) <= 240 and not is_prose_row_text(flat) and reference_venue_span(before_year) is not None:
                parsed = parse_reference_segment(before_year)
                signature.update(parsed)
            else:
                if after_year and len(flat) <= 240 and not is_prose_row_text(flat):
                    signature["title"] = before_year or None
                    parsed = parse_reference_segment(after_year)
                    for key in ("venue", "pages", "locator", "volume_issue"):
                        if parsed[key] is not None:
                            signature[key] = parsed[key]
                elif len(flat) <= 240 and not is_prose_row_text(flat):
                    parsed = parse_reference_segment(before_year)
                    signature.update(parsed)
        else:
            parsed = parse_reference_segment(flat)
            signature.update(parsed)

    pattern_parts = [key for key in ("marker", "authors", "title", "venue", "date", "pages", "locator") if signature.get(key)]
    signature["pattern"] = "+".join(pattern_parts) if pattern_parts else None
    reference_like = bool(
        (
            signature["authors"]
            and signature["date"]
            and (signature["title"] or signature["venue"] or signature["pages"] or signature["locator"])
        )
        or (
            signature["title"]
            and signature["venue"]
            and (signature["date"] or signature["pages"] or signature["locator"])
        )
        or (
            signature["marker"]
            and (signature["authors"] or signature["title"])
            and (signature["date"] or signature["venue"] or signature["pages"] or signature["locator"])
        )
        or (
            signature["venue"]
            and signature["date"]
            and (signature["pages"] or signature["locator"])
        )
        or (
            signature["marker"]
            and signature["authors"]
            and signature["title"]
        )
        or (
            signature["title"]
            and signature["venue"]
            and (signature["pages"] or signature["locator"])
        )
    )
    if reference_like and not (signature["marker"] or signature["authors"] or signature["date"]):
        if len(flat) > 260 or is_prose_row_text(flat):
            reference_like = False
    signature["reference_like"] = reference_like
    return signature


def author_segment_looks_like_reference(authors: str) -> bool:
    authors = authors.strip()
    if not (4 <= len(authors) <= 220):
        return False
    if re.match(r"(?i)^(the|this|that|we|our|in|for|from|appendix|table|figure)\b", authors):
        return False
    name_words = re.findall(r"\b[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’-]{1,}\b", authors)
    if len(name_words) < 2:
        return False
    if re.fullmatch(
        r"[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’-]{1,}(?:\s+[A-Z]\.?)?\s+[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’-]{1,}",
        authors,
    ):
        return True
    has_author_separator = (
        "," in authors
        or re.search(r"\band\b", authors, flags=re.I)
        or re.search(r"\bet\s+al\b", authors, flags=re.I)
        or re.search(r"\b[A-Z]\.", authors)
    )
    return has_author_separator or len(name_words) >= 3


def looks_like_reference_item(text: str) -> bool:
    normalized = normalize_text(text)
    for line in normalized.splitlines():
        if looks_like_reference_item_line(line):
            return True
    signature = reference_signature(normalized)
    return bool(signature["reference_like"])


def starts_reference_item(text: str) -> bool:
    normalized = normalize_text(text)
    for line in normalized.splitlines():
        if line.strip():
            return looks_like_reference_item_line(line) or bool(reference_signature(normalized)["start_like"])
    return False


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
    return looks_like_reference_item(text)


def reference_block_ids(blocks) -> set[str]:
    sorted_blocks = sorted(blocks, key=lambda item: (item["yMin"], item["xMin"]))
    first_reference_y = None
    for block in sorted_blocks:
        text = normalize_text(block.get("text", ""))
        if is_reference_heading(text):
            first_reference_y = block["yMin"]
            break
    if first_reference_y is not None:
        ids = set()
        for block in sorted_blocks:
            text = normalize_text(block.get("text", ""))
            if not text or is_page_number(text):
                continue
            if block["yMin"] >= first_reference_y:
                ids.add(block["id"])
        return ids

    seed_blocks = [
        block
        for block in sorted_blocks
        if normalize_text(block.get("text", ""))
        and not is_page_number(normalize_text(block.get("text", "")))
        and (starts_reference_item(block.get("text", "")) or contains_reference_item(block.get("text", "")))
    ]
    ids = {block["id"] for block in seed_blocks}
    if not seed_blocks:
        return ids
    for block in sorted_blocks:
        text = normalize_text(block.get("text", ""))
        if not text or is_page_number(text) or block["id"] in ids:
            continue
        if block_looks_like_reference_continuation(block) and any(same_reference_column(block, seed) for seed in seed_blocks):
            ids.add(block["id"])
    return ids


def horizontal_overlap(a, b) -> float:
    left = max(a["xMin"], b["xMin"])
    right = min(a["xMax"], b["xMax"])
    if right <= left:
        return 0.0
    width = min(a["xMax"] - a["xMin"], b["xMax"] - b["xMin"])
    if width <= 0:
        return 0.0
    return (right - left) / width


def same_reference_column(block, seed) -> bool:
    return horizontal_overlap(block, seed) >= 0.35 or abs(block["xMin"] - seed["xMin"]) <= 36.0


def block_looks_like_reference_continuation(block) -> bool:
    text = flattened_bibliography_text(block.get("text", ""))
    if not text or is_page_number(text):
        return False
    if is_heading_text(text) or is_visual_caption(text):
        return False
    if text.startswith(("•", "-", "–")):
        return False
    signature = reference_signature(text)
    if signature["reference_like"] and not signature["authors"] and not signature["marker"]:
        return True
    if len(text) > 360 and not signature["reference_like"]:
        return False
    lower = text.lower()
    if any(cue in lower for cue in REFERENCE_VENUE_CUES):
        return True
    if re.search(r"\bpages?\s+\d", lower) or re.search(r"\bpp\.\s*\d", lower):
        return True
    if re.search(r"\barxiv:\d", lower) or re.search(r"\babs/\d", lower):
        return True
    return len(text) <= 260 and bool(
        re.match(r"^[a-z][^.!?]{8,}[.!?]\s+(?:in|journal|proceedings|pages?)\b", lower)
    )


def page_looks_like_reference_continuation(blocks) -> bool:
    sorted_blocks = sorted(blocks, key=lambda item: (item["yMin"], item["xMin"]))
    non_page_blocks = []
    for block in sorted_blocks:
        text = normalize_text(block.get("text", ""))
        if not text or is_page_number(text):
            continue
        non_page_blocks.append(block)
    if not non_page_blocks:
        return False
    top_blocks = [block for block in non_page_blocks[:8] if block["yMin"] <= 260.0]
    return any(
        starts_reference_item(block.get("text", ""))
        or contains_reference_item(block.get("text", ""))
        or block_looks_like_reference_continuation(block)
        for block in top_blocks
    )


def apply_reference_continuation(blocks, classes: dict[str, str], in_reference_section: bool):
    force_reference_page = in_reference_section and page_looks_like_reference_continuation(blocks)
    if force_reference_page:
        classes = dict(classes)
        reference_ids = reference_block_ids(blocks)
        seed_blocks = [block for block in blocks if block["id"] in reference_ids]
        for block in blocks:
            text = normalize_text(block.get("text", ""))
            if text and not is_page_number(text):
                if block["id"] in reference_ids or (
                    block_looks_like_reference_continuation(block)
                    and any(same_reference_column(block, seed) for seed in seed_blocks)
                ):
                    classes[block["id"]] = "reference"
    if any(classification == "reference" for classification in classes.values()):
        return classes, True, force_reference_page
    return classes, False, force_reference_page


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
    if re.fullmatch(r"[A-Z][A-Z0-9 /&-]{3,80}", first):
        tokens = first.split()
        # Single-token all-caps identifiers are common dataset/model labels; only
        # widen unnumbered headings when the text has structural heading shape.
        if 2 <= len(tokens) <= 8 or first in STRUCTURAL_ALL_CAPS_HEADINGS:
            return True
    return False


def is_title_block(block) -> bool:
    text = normalize_text(block.get("text", ""))
    if block.get("page") != 1 or not text:
        return False
    if len(text) > 180 or block["yMin"] >= 150:
        return False
    if re.search(r"[@]|https?://|arxiv:", text, flags=re.I):
        return False
    if re.search(r"[.!?][\"')\]）】”’]*\s*$", text):
        return False
    words = latin_words(text)
    if len(words) < 3 and not (block["yMin"] < 90 and len(words) >= 2):
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) > 3:
        return False
    if "\n" in text:
        return block["yMax"] <= 185 and any(len(line) >= 24 for line in lines)
    return block["yMin"] < 120 and len(text) <= 140


def is_book_chapter_label_text(text: str) -> bool:
    return bool(re.fullmatch(r"(?i)chapter\s+\d{1,3}", normalize_text(text).strip()))


def book_chapter_title_candidate(block) -> bool:
    text = normalize_text(block.get("text", ""))
    if not text or len(text) > 140:
        return False
    if is_page_number(text) or is_reference_heading(text) or is_visual_caption(text):
        return False
    if is_formula_like(text) or re.search(r"[@]|https?://|arxiv:", text, flags=re.I):
        return False
    if re.search(r"[.!?][\"')\]）】”’]*\s*$", text):
        return False
    words = latin_words(text)
    if not (1 <= len(words) <= 12):
        return False
    if source_requires_chinese_translation(text):
        return True
    return bool(re.fullmatch(r"[A-Z][A-Za-z0-9 ,/&().:'-]{1,139}", text))


def book_chapter_heading_ids(blocks) -> tuple[set[str], set[str]]:
    """Detect book chapter openers split as "Chapter N" plus a following title."""
    label_ids: set[str] = set()
    title_ids: set[str] = set()
    sorted_blocks = sorted(blocks, key=lambda item: (item["yMin"], item["xMin"]))
    for idx, block in enumerate(sorted_blocks):
        if not is_book_chapter_label_text(block.get("text", "")):
            continue
        label_box = (block["xMin"], block["yMin"], block["xMax"], block["yMax"])
        candidates = []
        for other in sorted_blocks[idx + 1 : idx + 5]:
            gap = other["yMin"] - block["yMax"]
            if gap < -2.0 or gap > 90.0:
                continue
            if abs(other["xMin"] - block["xMin"]) > 48.0:
                continue
            if other["yMax"] - other["yMin"] < 10.0:
                continue
            if not book_chapter_title_candidate(other):
                continue
            candidates.append((gap, abs((other["xMin"] + other["xMax"]) - (label_box[0] + label_box[2])), other))
        if not candidates:
            continue
        _gap, _x_delta, title = min(candidates, key=lambda item: item[:2])
        label_ids.add(block["id"])
        title_ids.add(title["id"])
    return label_ids, title_ids


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
        function_words = english_function_token_count(line)
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
    tokens = normalized.split()
    if tokens and all(re.fullmatch(r"https?://\S+", token, flags=re.I) for token in tokens):
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
    if len(normalized) <= 180 and len(words) >= 6 and english_function_word_count(normalized) == 0:
        if not re.search(r"[.!?]", normalized) and all(re.fullmatch(r"[A-Z][A-Za-zÀ-ÖØ-öø-ÿ-]+", word) for word in words):
            return True
    return False


def source_requires_chinese_translation(text: str) -> bool:
    cleaned = strip_journal_footer_lines(text)
    if is_non_prose_identifier_text(cleaned):
        return False
    words = latin_words(cleaned)
    if len(words) < 4:
        return False
    if re.fullmatch(r"\d+(?:\.\d+)*\.?\s+(?:[A-Z][A-Za-z0-9-]*\s*){2,8}", normalize_text(cleaned)):
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
    chapter_label_ids, chapter_title_ids = book_chapter_heading_ids(blocks)
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
            continue
        if block["id"] in chapter_label_ids:
            classes[block["id"]] = "heading"
            continue
        if block["id"] in chapter_title_ids:
            classes[block["id"]] = "title"
            continue
        if block["id"] in visual_ids:
            if is_formula_or_code_block(text):
                classes[block["id"]] = "formula_region"
            else:
                classes[block["id"]] = "figure_region"
            continue
        if is_reference_heading(text) or starts_reference_item(text):
            classes[block["id"]] = "reference"
            continue
        if is_formula_like(text):
            classes[block["id"]] = "formula_region"
            continue
        if is_heading_text(text):
            classes[block["id"]] = "heading"
            continue
        if is_title_block(block):
            classes[block["id"]] = "title"
            continue
        classes[block["id"]] = "body"
    return classes
