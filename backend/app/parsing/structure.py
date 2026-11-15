"""结构理解：阅读顺序、块类型、标题树、作者/摘要、参考文献。"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from ..core.config import Settings
from ..core.textutil import (
    ARXIV_ID_RE,
    CITATION_MARK_RE,
    DOI_RE,
    YEAR_RE,
    extract_numbers,
    looks_like_heading,
    normalize_ws,
    slugify,
    truncate,
)
from .model import BBox, RefEntry, Section, TextBlock
from .pdftext import PageText, RawBlock, is_boilerplate_text

_ABSTRACT_RE = re.compile(r"^\s*(?:abstract|摘\s*要)\b[.:：—-]?\s*", re.I)
_KEYWORDS_RE = re.compile(r"^\s*(?:key\s*words?|index\s*terms?|关键词)\b[.:：—-]?\s*", re.I)
_AFFIL_RE = re.compile(
    r"universit|institut|college|laborator|department|school of|academy|google|microsoft|openai|meta\b|deepmind|"
    r"baidu|tencent|alibaba|bytedance|amazon|apple|nvidia|samsung|huawei|salesforce|ibm|intel|hugging\s*face|"
    r"research|inc\.|ltd|gmbh|@[\w.\-]+\.\w+",
    re.I,
)
_CAPTION_RE = re.compile(r"^\s*(?:table|figure|fig\.|表|图)\s*(\d+)[:：.]?\s*", re.I)
_EQUATION_NUMBER_RE = re.compile(r"\((\d{1,3})\)\s*$")
_REF_SPLIT_RE = re.compile(r"(?=\[\d{1,3}\])")
_MATH_CHARS = set("=+−-×÷∑∏∫√≈≤≥≠∂∇αβγδεθλμπρστφψωΔΣΩ∈∀∃→←↔∞")


@dataclass
class Analysis:
    blocks: list[TextBlock] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    references: list[RefEntry] = field(default_factory=list)
    title: str = ""
    authors: list[str] = field(default_factory=list)
    affiliations: list[str] = field(default_factory=list)
    abstract: str = ""
    keywords: list[str] = field(default_factory=list)
    arxiv_id: str = ""
    arxiv_version: str = ""
    year: int | None = None
    venue: str = ""
    doi: str = ""
    layout: str = "single"
    warnings: list[str] = field(default_factory=list)
    body_size: float = 9.5


def _is_mathy(text: str) -> bool:
    stripped = text.strip()
    if not stripped or len(stripped) > 220:
        return False
    letters = sum(ch.isalpha() for ch in stripped)
    ratio = letters / max(1, len(stripped))
    math_hits = sum(1 for ch in stripped if ch in _MATH_CHARS)
    has_equals = "=" in stripped
    return (has_equals and ratio < 0.72) or math_hits >= 3 and ratio < 0.5


_SENT_END_RE = re.compile(r"[.。;；!?！？,，]$")


def _heading_text_ok(text: str) -> bool:
    """过滤「1. Chunking: …回…」这类被编号误判成标题的正文句子。

    同时排除「1 conv 11x11x3, 96 4 55x55x96」这类以数字开头的表格数据行：
    真正的章节标题几乎不会在同一行堆叠三个以上数字。
    """
    stripped = text.strip()
    if not stripped:
        return False
    if _SENT_END_RE.search(stripped) and len(stripped) > 32:
        return False
    if len(extract_numbers(stripped)) >= 3:
        return False
    return True


def _heading_size_ok(raw_block: RawBlock, body_size: float) -> bool:
    """标题字号不应明显小于正文。

    表格数据行/表头常被字号规则误判成「标题」：AlexNet 的 ``Method Top-1 Top-5``
    只有 8pt（正文 9.5pt），靠 bold 不能区分，必须用字号下限拦住。
    """
    size = float(raw_block.size or 0.0)
    if size <= 0:
        return True
    return size >= body_size * 0.90


def order_page_blocks(page: PageText) -> list[RawBlock]:
    """按阅读顺序排序：双栏时以「通栏块」为分隔带，带内先左栏后右栏。"""
    blocks = [b for b in page.blocks if b.text.strip() or b.kind == "image"]
    if not blocks:
        return []
    if page.columns <= 1:
        return sorted(blocks, key=lambda b: (round(b.bbox.y0, 1), b.bbox.x0))
    # 0.72 会把「几乎通栏但略窄」的标题/作者块误判成某一栏的块，导致首页 Abstract
    # 标题排到最前（head_zone 变空）。按经验取 0.55 页宽：正文栏宽通常 ~0.42 页宽。
    full_width = page.width * 0.55
    spanning = [b for b in blocks if b.bbox.width >= full_width]
    narrow = [b for b in blocks if b.bbox.width < full_width]
    bands: list[tuple[float, float]] = []
    if not spanning:
        bands = [(0.0, page.height)]
    else:
        cursor = 0.0
        for block in sorted(spanning, key=lambda b: b.bbox.y0):
            if block.bbox.y0 > cursor:
                bands.append((cursor, block.bbox.y0))
            bands.append((block.bbox.y0, block.bbox.y1))
            cursor = max(cursor, block.bbox.y1)
        if cursor < page.height:
            bands.append((cursor, page.height))
    ordered: list[RawBlock] = []
    mid = page.width / 2
    for low, high in bands:
        in_band = [b for b in blocks if b.bbox.y0 >= low - 1 and b.bbox.y1 <= high + 1]
        spans_here = [b for b in in_band if b.bbox.width >= full_width]
        if spans_here:
            ordered.extend(sorted(spans_here, key=lambda b: b.bbox.y0))
        columns = [b for b in in_band if b.bbox.width < full_width]
        left = sorted([b for b in columns if (b.bbox.x0 + b.bbox.x1) / 2 < mid], key=lambda b: b.bbox.y0)
        right = sorted([b for b in columns if (b.bbox.x0 + b.bbox.x1) / 2 >= mid], key=lambda b: b.bbox.y0)
        ordered.extend(left)
        ordered.extend(right)
    # 去重（跨带边界可能重复收录）
    seen: set[int] = set()
    unique: list[RawBlock] = []
    for block in ordered:
        if id(block) in seen:
            continue
        seen.add(id(block))
        unique.append(block)
    return unique


def guess_body_size(pages: Sequence[PageText]) -> float:
    sizes: list[float] = []
    for page in pages:
        for block in page.blocks:
            text = block.text.strip()
            if len(text) < 60 or block.kind == "image":
                continue
            sizes.append(block.size)
    return round(statistics.median(sizes), 2) if sizes else 9.5


def _heading_level(text: str, number: str) -> int:
    if number:
        depth = number.strip(".").count(".") + 1
        return max(1, min(3, depth))
    stripped = text.strip().lower()
    if stripped in {"abstract", "references", "acknowledgments", "acknowledgements", "appendix", "future work", "conclusion", "introduction", "related work"}:
        return 1
    return 1


def classify_blocks(pages: Sequence[PageText], body_size: float) -> list[TextBlock]:
    """把原始块转为带类型的 TextBlock，并维护「是否已进入 References」状态。"""
    blocks: list[TextBlock] = []
    in_references = False
    counter = 0
    for page in pages:
        ordered = order_page_blocks(page)
        header_limit = 46.0
        footer_limit = page.height - 46.0
        for raw_block in ordered:
            counter += 1
            bbox = raw_block.bbox
            text = normalize_ws(raw_block.text)
            block_id = f"p{page.number}-b{counter:03d}"
            if raw_block.kind == "image":
                blocks.append(
                    TextBlock(
                        id=block_id,
                        page=page.number,
                        kind="image",
                        text="",
                        bbox=bbox,
                        column=1 if (bbox.x0 + bbox.x1) / 2 > page.width / 2 else 0,
                        ocr=raw_block.ocr,
                    )
                )
                continue
            if not text:
                continue
            if bbox.y1 <= header_limit:
                kind = "header"
            elif bbox.y0 >= footer_limit:
                kind = "footer"
            elif is_boilerplate_text(text):
                # 旋转水印 / 版权行 / 编辑器行 / 页码：既不是正文，也不能参与标题与作者识别
                kind = "meta"
            else:
                kind = "paragraph"
            level = 0
            number = ""
            heading_hint, hint_level, hint_number = looks_like_heading(text)
            if kind == "paragraph":
                big = raw_block.size >= max(body_size * 1.06, body_size + 0.4)
                short = len(text) <= 140 and _heading_text_ok(text)
                # OCR 行高只是字号近似值（同一段正文的行高会差 1–2pt），
                # 对 "Abstract/Introduction" 这类显式提示词放宽下限。
                size_ok = _heading_size_ok(raw_block, body_size) or (
                    raw_block.ocr and raw_block.size >= body_size * 0.78
                )
                if (heading_hint and short and size_ok) or (big and short and raw_block.bold):
                    kind = "heading"
                    level = _heading_level(text, hint_number or "")
                    number = hint_number or ""
                    if hint_level and not number:
                        level = min(level, hint_level) if hint_level else level
                if not number:
                    match = re.match(r"^(\d+(?:\.\d+){0,2})\.?\s+\S", text)
                    if match:
                        number = match.group(1)
                        level = max(1, min(3, number.count(".") + 1))
            if kind == "heading":
                lowered = text.lower().strip("：: .")
                if lowered in {"references", "reference", "bibliography", "参考文献"}:
                    in_references = True
                elif in_references and level == 1:
                    # References 之后的附录/致谢等一级标题应退出参考文献状态，
                    # 否则附录正文会被误标为 reference 而不进入检索索引。
                    in_references = False
            if kind == "paragraph" and in_references:
                kind = "reference"
            caption_match = _CAPTION_RE.match(text)
            if caption_match and kind == "paragraph":
                kind = "caption"
            if kind == "paragraph" and _is_mathy(text):
                kind = "equation"
            blocks.append(
                TextBlock(
                    id=block_id,
                    page=page.number,
                    kind=kind,
                    text=text,
                    bbox=bbox,
                    column=1 if (bbox.x0 + bbox.x1) / 2 > page.width / 2 and page.columns > 1 else 0,
                    level=level,
                    number=number,
                    font_size=raw_block.size,
                    font=raw_block.font,
                    bold=raw_block.bold,
                    italic=raw_block.italic,
                    centered=abs((bbox.x0 + bbox.x1) / 2 - page.width / 2) < page.width * 0.08,
                    ocr=raw_block.ocr,
                )
            )
    return blocks


_NAME_TOKEN_RE = re.compile(r"^[^\W\d_][\w'’.\-]*$", re.UNICODE)
_EMAIL_RE = re.compile(r"[\w.+%\-]+@[\w.\-]+\.\w+")
_EMAIL_DOMAIN_RE = re.compile(r"@([\w.\-]+\.\w+)")
_NAME_MARKS_RE = re.compile(r"[∗†‡§¶*⋆]+")
_NON_NAME_WORDS = {
    "team", "language", "brain", "research", "university", "institute", "institution", "department",
    "school", "college", "laboratory", "labs", "inc", "ltd", "corp", "corporation", "company", "google",
    "microsoft", "meta", "openai", "deepmind", "baidu", "tencent", "alibaba", "bytedance", "amazon",
    "apple", "nvidia", "samsung", "huawei", "salesforce", "abstract", "keywords", "editor", "reviewer",
    "figure", "table", "introduction", "conclusion", "references", "appendix", "preprint", "arxiv",
    "version", "draft", "corresponding", "equal", "contribution", "contributions", "github", "code",
    "email", "dataset", "abstract", "released", "updated", "license",
}
_ORG_BY_DOMAIN = {
    "google.com": "Google",
    "googlemail.com": "Google",
    "microsoft.com": "Microsoft",
    "openai.com": "OpenAI",
    "meta.com": "Meta",
    "fb.com": "Meta",
    "deepmind.com": "DeepMind",
    "baidu.com": "Baidu",
    "tencent.com": "Tencent",
    "alibaba-inc.com": "Alibaba",
    "bytedance.com": "ByteDance",
    "amazon.com": "Amazon",
    "apple.com": "Apple",
    "nvidia.com": "NVIDIA",
    "stanford.edu": "Stanford University",
    "berkeley.edu": "UC Berkeley",
    "mit.edu": "MIT",
    "cmu.edu": "Carnegie Mellon University",
    "toronto.edu": "University of Toronto",
    "nyu.edu": "New York University",
    "washington.edu": "University of Washington",
    "princeton.edu": "Princeton University",
    "ox.ac.uk": "University of Oxford",
    "cam.ac.uk": "University of Cambridge",
    "ethz.ch": "ETH Zürich",
}


def _ends_sentence(text: str) -> bool:
    return bool(re.search(r"[.。!?！？]$", text.strip()))


def _clean_name(value: str) -> str:
    text = _NAME_MARKS_RE.sub("", value or "")
    text = re.sub(r"(?<=[A-Za-z])\d+$", "", text.strip())
    text = re.sub(r"^[\d\s∗†‡§¶*⋆]+", "", text)
    return text.strip(" ,.;:()[]")


def _person_name_ok(value: str) -> bool:
    text = _clean_name(value)
    if not text or len(text) > 60:
        return False
    words = text.split()
    if not 1 <= len(words) <= 5:
        return False
    if any(ch.isdigit() for ch in text):
        return False
    if any(word.lower().strip(".,") in _NON_NAME_WORDS for word in words):
        return False
    if not all(_NAME_TOKEN_RE.match(word) for word in words):
        return False
    # 每个词都应以大写字母开头（含 Unicode 姓名首字母）；中间允许 de/van/von 等
    # 姓氏前缀（"Henrique Ponde de Oliveira Pinto"）。
    particles = {"de", "van", "von", "da", "del", "der", "la", "le", "den", "di", "dos", "du"}
    return all(
        word[0].isupper() or (index > 0 and word.lower() in particles)
        for index, word in enumerate(words)
    )


def _looks_like_affiliation(text: str) -> bool:
    value = (text or "").strip()
    if not value or len(value) < 3:
        return False
    return bool(_AFFIL_RE.search(value))


def _clean_affiliation(value: str) -> str:
    text = _EMAIL_RE.sub(" ", value or "")
    text = re.sub(r"\{[^}]*\}", " ", text)
    text = _NAME_MARKS_RE.sub("", text)
    text = re.sub(r"(?<=[A-Za-z])\d+\b", "", text)
    text = re.sub(r"^(?:and|,|;)\s*", "", text.strip())
    return normalize_ws(text).strip(" ,;:")


def _org_from_email(email: str) -> str:
    domain = email.rsplit("@", 1)[-1].lower()
    if domain in _ORG_BY_DOMAIN:
        return _ORG_BY_DOMAIN[domain]
    for suffix, org in _ORG_BY_DOMAIN.items():
        if domain.endswith("." + suffix):
            return org
    return ""


def _split_person_list(text: str) -> list[str]:
    """把一行作者文本拆成姓名列表。

    支持三类常见排版：
    - 逗号/分号/and 分隔："A, B and C"；
    - 上标编号当分隔符："Alec Radford * 1 Jong Wook Kim * 1 …"（CV/ML 论文常见）；
    - 无分隔的「名 姓 名 姓」行。
    """
    raw = _EMAIL_RE.sub(" ", text or "")
    raw = re.sub(r"\{[^}]*\}", " ", raw)
    marked = _NAME_MARKS_RE.sub(" ", raw)
    marked = re.sub(r"(?<=[A-Za-z])\d{1,2}(?=\s|$)", "", marked)
    # 用独立的上标编号切分；编号后必须跟空白或行尾，避免切开 GPT-3 之类的词
    chunks = [normalize_ws(p) for p in re.split(r"(?:(?<=\s)|^)\d{1,2}(?=\s|$)", marked)]
    chunks = [p.strip(" ,;") for p in chunks if p]
    if len(chunks) >= 2:
        valid = [p for p in chunks if _person_name_ok(p)]
        if len(valid) >= 2 and len(valid) >= len(chunks) - 1:
            return valid
    cleaned = _clean_name(raw)
    cleaned = normalize_ws(re.sub(r"\b\d{1,2}\b", " ", cleaned))
    if not cleaned:
        return []
    pieces = [p for p in re.split(r"\s*(?:,|;|·|\band\b|&)\s*", cleaned) if p.strip()]
    pieces = [p for p in (p.strip() for p in pieces) if p and p.lower() not in {"et al", "et al."}]
    if len(pieces) >= 2 and all(_person_name_ok(p) for p in pieces):
        return pieces
    tokens = cleaned.split()
    if 4 <= len(tokens) <= 40 and len(tokens) % 2 == 0 and all(_NAME_TOKEN_RE.match(t) for t in tokens):
        pairs = [" ".join(tokens[i : i + 2]) for i in range(0, len(tokens), 2)]
        if all(_person_name_ok(p) for p in pairs):
            return pairs
    single = _clean_name(cleaned)
    return [single] if _person_name_ok(single) else []


# 团队/机构署名（论文只写组织名而非个人）→ 机构；仅在文档未给出机构时用于补位。
_TEAM_AFFIL = {
    "openai": "OpenAI",
    "qwen team": "Alibaba Group",
    "deepseek-ai": "DeepSeek",
    "deepseek": "DeepSeek",
    "bigscience workshop": "BigScience",
    "mistral ai team": "Mistral AI",
    "meta ai": "Meta AI",
}

_CORPORATE_RE = re.compile(r"(?i)\b(team|workshop|labs?)\s*$|^(openai|deepseek(?:-ai)?|bigscience|qwen|mistral\s*ai|meta\s*ai|google\s*(?:brain|research)|microsoft\s*research)$")
_CONTRIBUTOR_RE = re.compile(
    r"(?i)^(?:major\s+|core\s+)?contributors?\b|^a list of contributors|^equal contribution|^correspondence\b"
)


def _corporate_author(text: str) -> str:
    """团队署名（"OpenAI∗"、"Qwen Team"、"BigScience Workshop"）当作机构作者。"""
    cleaned = _clean_name(text).strip(" ∗*†‡§¶")
    if not cleaned or _person_name_ok(cleaned):
        return ""
    if _CORPORATE_RE.search(cleaned):
        return cleaned
    return ""


def _consume_author_line(line: str, authors: list[str], affiliations: list[str]) -> None:
    text = normalize_ws(line)
    if not text or is_boilerplate_text(text):
        return
    # 大括号包裹的邮箱组（可能被拆成两块，只有右半段带 "}"）：{a,b,c}@google.com
    if ("{" in text or "}" in text) and "@" in text:
        domain = _EMAIL_DOMAIN_RE.search(text)
        if domain:
            org = _org_from_email("x@" + domain.group(1))
            if org:
                affiliations.append(org)
        return
    # 「组织作者 + 机构」复合行，例如 Llama 3 的 "Llama Team, AI @ Meta"
    if "," in text and "@" in text and not _person_name_ok(text):
        left, right = text.split(",", 1)
        if _person_name_ok(left) or (left and not _looks_like_affiliation(left)):
            if _EMAIL_DOMAIN_RE.search(right) or re.search(r"@\s*[A-Z]", right):
                authors.append(_clean_name(left))
                affiliations.append(_clean_affiliation(right))
                return
    cleaned = _clean_name(_EMAIL_RE.sub(" ", text))
    emails = _EMAIL_RE.findall(text)
    domain_only = re.fullmatch(r"[\s{},;]*@[\w.\-]+\.\w+[\s{},;]*", cleaned or "") or ("@" in cleaned and not cleaned.replace("@", "").replace(".", "").strip(" _-").isalnum())
    if domain_only or (emails and not cleaned):
        for email in emails:
            org = _org_from_email(email)
            if org:
                affiliations.append(org)
        if not emails:
            match = _EMAIL_DOMAIN_RE.search(text)
            if match:
                org = _org_from_email("x@" + match.group(1))
                if org:
                    affiliations.append(org)
        return
    # OCR 常把邮箱切成 "noamCgoogle _" / "usz@google" 这类残片，不能当机构
    contact_fragment = (
        "@" in cleaned
        or cleaned.endswith("_")
        or ("google" in cleaned.lower() and " " not in cleaned)
    )
    real_org = bool(
        re.search(r"\b(university|institute|department|laboratory|research|brain|ai language)\b", cleaned, re.I)
    )
    if contact_fragment and not real_org:
        return
    if not authors:
        corporate = _corporate_author(cleaned)
        if corporate:
            authors.append(corporate)
            mapped = _TEAM_AFFIL.get(corporate.casefold(), "")
            if mapped and not affiliations:
                affiliations.append(mapped)
            return
    if _looks_like_affiliation(cleaned) and not _person_name_ok(cleaned):
        affiliations.append(_clean_affiliation(cleaned))
        return
    names = _split_person_list(cleaned)
    if names:
        authors.extend(names)
        return
    if _looks_like_affiliation(text):
        affiliations.append(_clean_affiliation(text))
    elif (
        not authors
        and len(cleaned) <= 80
        and len(cleaned.split()) >= 2
        and not re.search(r"[@_0-9]", cleaned)
        and not _ends_sentence(cleaned)
    ):
        authors.append(cleaned)


def _pick_title(head_zone: Sequence[TextBlock], body_size: float, page_height: float) -> list[TextBlock]:
    candidates = [
        b
        for b in head_zone
        if b.kind in {"paragraph", "heading"}
        and b.text.strip()
        and b.font_size > 0
        and b.font_size >= max(body_size * 1.15, 8.0)
        and b.bbox.y0 < page_height * 0.55
        and len(re.findall(r"[A-Za-z]", b.text)) >= 4
        and not is_boilerplate_text(b.text)
    ]
    if not candidates:
        fallback = [
            b
            for b in head_zone
            if b.kind in {"paragraph", "heading"} and 10 <= len(b.text.strip()) <= 260 and b.bbox.y0 < page_height * 0.45
        ]
        return fallback[:1]
    max_size = max(b.font_size for b in candidates)
    chosen = [b for b in candidates if b.font_size >= max_size - 0.8]
    chosen.sort(key=lambda b: b.bbox.y0)
    merged = [chosen[0]]
    for block in chosen[1:]:
        gap = block.bbox.y0 - merged[-1].bbox.y1
        if gap <= max(24.0, max_size * 2.4):
            merged.append(block)
    return merged


def _page1_zone(pages: Sequence[PageText], title_bottom: float, abstract_top: float | None, body_size: float) -> list[RawBlock]:
    raw_blocks = [b for b in pages[0].blocks if b.kind == "text" and b.text.strip()]
    raw_blocks.sort(key=lambda b: (round(b.bbox.y0, 1), b.bbox.x0))
    zone: list[RawBlock] = []
    for raw in raw_blocks:
        # 标题块自身（或与其同高/更高者）不属于作者区：y1==title_bottom 时旧的
        # `y1 <= title_bottom - 0.5` 会把标题漏进来，最终被当成第一个作者。
        if raw.bbox.y0 <= title_bottom - 0.5:
            continue
        text = normalize_ws(raw.text)
        if abstract_top is not None:
            if raw.bbox.y0 >= abstract_top - 0.5:
                continue
        else:
            if len(text) > 220 or (raw.size <= body_size * 1.02 and len(text) > 120 and _ends_sentence(text)):
                break
            if len(zone) >= 8:
                break
        zone.append(raw)
    return zone


def _abstract_lines(pages: Sequence[PageText], heading: TextBlock | None, stop_y: float | None, body_size: float) -> str:
    if heading is None:
        return ""
    page = pages[0]
    items: list[tuple[float, float, float, str]] = []
    for raw in page.blocks:
        if raw.kind != "text":
            continue
        for line in raw.lines:
            text = normalize_ws(line.text)
            if not text or is_boilerplate_text(text):
                continue
            top, bottom = line.bbox.y0, line.bbox.y1
            if top <= heading.bbox.y1 + 1:
                continue
            if stop_y is not None and top >= stop_y - 1:
                continue
            if line.size and line.size > body_size * 1.35:
                continue
            items.append((line.bbox.x0, line.bbox.x1, top, text))
    if not items:
        return ""
    if page.columns >= 2:
        # 摘要只属于它所在的栏。双栏页若把两栏行混排，会把另一栏的
        # Introduction 拼进摘要（2002.04745 就是 Abstract 与 1. Introduction 交错）。
        # 但不少双栏论文的摘要本身是通栏排版（如合成 fixture 的 transformer_v1），
        # 这类行左右边界都越过页宽 15%/80%，必须保留，否则 abstract 直接变空。
        full_width = page.width * 0.8
        left_limit = page.width * 0.55
        if heading.bbox.x1 <= left_limit:
            items = [
                item
                for item in items
                if item[1] <= left_limit or (item[0] <= page.width * 0.15 and item[1] >= full_width)
            ]
            items.sort(key=lambda item: item[2])
        elif heading.bbox.x0 >= page.width * 0.45:
            items = [
                item
                for item in items
                if item[0] >= page.width * 0.45 or (item[0] <= page.width * 0.15 and item[1] >= full_width)
            ]
            items.sort(key=lambda item: item[2])
        else:
            left = [item for item in items if item[0] < page.width / 2]
            right = [item for item in items if item[0] >= page.width / 2]
            items = sorted(left, key=lambda item: item[2]) + sorted(right, key=lambda item: item[2])
    else:
        items.sort(key=lambda item: (round(item[2] / 3.0), item[0]))
    text = normalize_ws(" ".join(item[3] for item in items))
    return re.sub(r"(?<=[a-z])-\s+(?=[a-z])", "", text)


_AFFIL_SPLIT_RE = re.compile(
    r"\s(?=\d{1,2}(?:[A-Z]|CAS|Department|Key|College|School|Institute|University|Microsoft|Google|Meta|Center))"
)
_ORG_WORD_RE = re.compile(r"(?i)(universit|institut|laborator|academy|college|school|department|research|center|centre|openai|google|microsoft|meta ai|deepmind|anthropic|amazon|apple|nvidia|samsung|huawei|salesforce|ibm|intel)")


def _footnote_affiliations(page: PageText, since_y: float) -> list[str]:
    """从首页脚注区提取机构名。

    不少论文（如 2002.04745）只在首页脚注里写单位：``1CAS Key Laboratory … 2Microsoft
    Research Asia …``。作者区里拿不到机构时用这个兜底。
    """
    chunks: list[str] = []
    for raw in page.blocks:
        if raw.kind != "text" or raw.bbox.y0 < since_y:
            continue
        if is_boilerplate_text(raw.text):
            continue
        text = " ".join(normalize_ws(line.text) for line in raw.lines if normalize_ws(line.text))
        if not text:
            text = normalize_ws(raw.text)
        # 跨行断词："Sci-\nences" → "Sciences"
        text = re.sub(r"(?<=[A-Za-z])-\s+(?=[a-z])", "", text)
        text = re.sub(r"(?i)\b(correspondence|corresponding author)\b.*$", "", text)
        for piece in _AFFIL_SPLIT_RE.split(text):
            piece = _clean_affiliation(re.sub(r"^\s*\d{1,2}\s*", "", piece))
            if len(piece) < 8 or "@" in piece:
                continue
            if re.match(r"(?i)(equal|works?\b|contribut|email|footnote)", piece):
                continue
            if not _ORG_WORD_RE.search(piece):
                continue
            chunks.append(piece[:160].strip(" ,;."))
    return list(dict.fromkeys(chunks))[:6]


def _looks_like_name_list(text: str) -> bool:
    """贡献者列表（"Dataset Aaron Gokaslan, Adi Simhi, …"）不是摘要。

    以逗号切片后，若前 12 段里有 ≥6 段是 2–3 个首字母大写单词的人名，判为名单。
    """
    parts = [p.strip() for p in text.split(",")[:12]]
    hits = 0
    for part in parts:
        words = part.split()
        if 2 <= len(words) <= 3 and all(w[:1].isupper() for w in words if w):
            hits += 1
    return hits >= 6


def extract_metadata(blocks: list[TextBlock], pages: Sequence[PageText], body_size: float) -> dict[str, Any]:
    """标题、作者、机构、摘要、关键词、arXiv 编号。"""
    page1 = [b for b in blocks if b.page == 1 and b.kind not in {"header", "footer", "image", "meta"}]
    meta: dict[str, Any] = {
        "title": "",
        "authors": [],
        "affiliations": [],
        "abstract": "",
        "keywords": [],
        "arxiv_id": "",
        "arxiv_version": "",
        "year": None,
        "venue": "",
        "doi": "",
    }
    if not page1:
        return meta

    # arXiv 编号与年份（页眉/首页任意位置；水印块已标记为 meta，这里从全页文本中查找）
    joined = " ".join(b.text for b in blocks if b.page == 1)
    arxiv = ARXIV_ID_RE.search(joined)
    if arxiv:
        meta["arxiv_id"] = arxiv.group(0)
        version = re.search(r"v(\d+)$", arxiv.group(0))
        meta["arxiv_version"] = f"v{version.group(1)}" if version else ""
    # 年份优先从 arXiv 编号的 YYMM 推导（2005.14165 → 2020），避免把编号前缀误当出版年
    if arxiv and re.match(r"^(\d{2})(\d{2})\.\d{4,5}", arxiv.group(0)):
        yy, mm = int(arxiv.group(0)[:2]), int(arxiv.group(0)[2:4])
        if 1 <= mm <= 12:
            meta["year"] = 2000 + yy if yy < 90 else 1900 + yy
    if not meta["year"]:
        year = YEAR_RE.search(joined)
        if year:
            meta["year"] = int(year.group(0))
    doi = DOI_RE.search(joined)
    if doi:
        meta["doi"] = doi.group(0)

    # 摘要起点之前的区域视为「标题区」
    abstract_index = next(
        (i for i, b in enumerate(page1) if b.kind == "heading" and b.text.strip().lower().startswith(("abstract", "摘要"))),
        None,
    )
    head_zone = page1[: abstract_index if abstract_index is not None else min(8, len(page1))]
    title_blocks = _pick_title(head_zone, body_size, pages[0].height)
    if title_blocks:
        parts: list[str] = []
        for block in title_blocks:
            text = normalize_ws(block.text)
            if parts and _ends_sentence(parts[-1]) and len(parts[-1]) > 30:
                break
            if text and (not parts or text != parts[-1]):
                parts.append(text)
        meta["title"] = normalize_ws(" ".join(parts)).strip(" ∗†‡")
        # PDF 里被 "- " 拆开的标题（"LORA: ... LARGE LAN- GUAGE MODELS"）
        meta["title"] = normalize_ws(re.sub(r"(?<=[A-Za-z])-\s+(?=[A-Za-z])", "", meta["title"]))

    # 作者/机构：标题下方到摘要上方之间的原始块（含折行）
    authors: list[str] = []
    affiliations: list[str] = []
    title_bottom = min(b.bbox.y1 for b in title_blocks) if title_blocks else pages[0].height * 0.2
    abstract_top = page1[abstract_index].bbox.y0 if abstract_index is not None else None
    for raw in _page1_zone(pages, title_bottom, abstract_top, body_size):
        block_text = normalize_ws(raw.text)
        if is_boilerplate_text(block_text):
            continue
        lines = [normalize_ws(line.text) for line in raw.lines if normalize_ws(line.text)] or [block_text]
        for line in lines:
            # 作者列表折行时（"…, Lukasz" / "Kaiser, Illia Polosukhin"），行尾会留下
            # 一个孤立的单词。把它和下一行开头的词合并，否则会多出两个假作者。
            if authors and " " not in authors[-1] and len(authors[-1]) > 2:
                head = re.match(r"([A-Z][\w'’\-]+)(?=\s*[,;]|\s|$)", line)
                if head and head.group(1).casefold() not in _NON_NAME_WORDS:
                    pending = authors.pop()
                    line = f"{pending} {line}"
            _consume_author_line(line, authors, affiliations)
    # 机构常只出现在首页脚注（"1CAS Key Laboratory … 2Microsoft Research Asia"）。
    # 作者区里没拿到机构时，按编号从脚注补一次。
    if not affiliations:
        foot_from = abstract_top if abstract_top is not None else pages[0].height * 0.5
        affiliations.extend(_footnote_affiliations(pages[0], foot_from))
    meta["authors"] = [a for a in dict.fromkeys(_clean_name(a) for a in authors) if a][:20]
    cleaned_affils = [a for a in dict.fromkeys(_clean_affiliation(a) for a in affiliations) if a]
    # 去掉被更长机构名包含的短名（"Google" vs "Google AI Language"），避免审计里出现噪声
    meta["affiliations"] = [
        a
        for a in cleaned_affils
        if not any(a.lower() != other.lower() and a.lower() in other.lower() for other in cleaned_affils)
    ][:10]
    # 团队署名论文（"DeepSeek-AI"、"BigScience Workshop"）文档里没有机构行时，
    # 用团队→机构映射补位，保证作者单位字段不为空。
    if not meta["affiliations"] and len(meta["authors"]) == 1:
        mapped = _TEAM_AFFIL.get(meta["authors"][0].casefold(), "")
        if mapped:
            meta["affiliations"] = [mapped]

    # 摘要：从 Abstract 标题到下一个一级标题之间的所有行；没有标题时取首页首段长正文
    if abstract_index is not None:
        heading = page1[abstract_index]
        page_w = pages[0].width
        heading_is_left = heading.bbox.x0 < page_w / 2
        stop_y = next(
            (
                b.bbox.y0
                for b in page1[abstract_index + 1 :]
                if b.kind == "heading"
                and b.bbox.y0 > heading.bbox.y0 - 1
                and (
                    (b.bbox.x0 < page_w / 2) == heading_is_left
                    or (b.bbox.x0 <= page_w * 0.15 and b.bbox.x1 >= page_w * 0.8)
                )
            ),
            None,
        )
        stop_y = min(stop_y, heading.bbox.y1 + 360.0) if stop_y is not None else heading.bbox.y1 + 360.0
        meta["abstract"] = _abstract_lines(pages, heading, stop_y, body_size)[:2600]
    else:
        for block in page1:
            if block.kind == "paragraph" and len(block.text) > 220 and not is_boilerplate_text(block.text):
                if _CONTRIBUTOR_RE.match(block.text.strip()) or _looks_like_name_list(block.text):
                    continue
                if block.bbox.y0 < pages[0].height * 0.75:
                    meta["abstract"] = normalize_ws(block.text)[:2600]
                    break

    # 大模型技术报告（BLOOM 等）首页是贡献者名单，摘要排在后续页面；
    # 首页取不到时在前 5 页里找 "Abstract" 标题（注意：page.blocks 是原始块，
    # 分类后的 kind 在 blocks 参数里，必须按页码重新取）。
    if not meta["abstract"]:
        for page2 in pages[1:5]:
            page_blocks = [b for b in blocks if b.page == page2.number]
            idx2 = next(
                (
                    i
                    for i, b in enumerate(page_blocks)
                    if b.kind == "heading" and b.text.strip().lower().startswith(("abstract", "摘要"))
                ),
                None,
            )
            if idx2 is None:
                continue
            heading2 = page_blocks[idx2]
            page_w2 = page2.width
            heading2_is_left = heading2.bbox.x0 < page_w2 / 2
            stop2 = next(
                (
                    b.bbox.y0
                    for b in page_blocks[idx2 + 1 :]
                    if b.kind == "heading"
                    and b.bbox.y0 > heading2.bbox.y0 - 1
                    and (
                        (b.bbox.x0 < page_w2 / 2) == heading2_is_left
                        or (b.bbox.x0 <= page_w2 * 0.15 and b.bbox.x1 >= page_w2 * 0.8)
                    )
                ),
                None,
            )
            stop2 = min(stop2, heading2.bbox.y1 + 360.0) if stop2 is not None else heading2.bbox.y1 + 360.0
            meta["abstract"] = _abstract_lines([page2], heading2, stop2, body_size)[:2600]
            if meta["abstract"]:
                break

    for block in page1:
        if _KEYWORDS_RE.match(block.text):
            raw = _KEYWORDS_RE.sub("", block.text)
            meta["keywords"] = [k.strip(" .;") for k in re.split(r"[,;，；]", raw) if k.strip()]
            break

    # 期刊/会议信息（页眉页脚与首页文本）
    venue_text = " ".join(b.text for b in blocks if b.page == 1)
    if re.search(r"Journal of Machine Learning Research", venue_text, re.I):
        meta["venue"] = "JMLR"
    elif re.search(r"Proceedings of (?:the )?[^.]{0,100}(?:NAACL|ACL|EMNLP|NeurIPS|ICML|ICLR|CVPR|AAAI)", venue_text, re.I):
        match = re.search(r"Proceedings of (?:the )?[^.]{0,100}?(?:NAACL|ACL|EMNLP|NeurIPS|ICML|ICLR|CVPR|AAAI)[^.]{0,40}", venue_text, re.I)
        meta["venue"] = normalize_ws(match.group(0)) if match else "Conference"
    elif meta["arxiv_id"]:
        meta["venue"] = "arXiv"
    return meta


def build_sections(blocks: list[TextBlock]) -> list[Section]:
    """从标题块构建章节树，并把后续块归入对应章节。"""
    sections: list[Section] = []
    current: Section | None = None
    seen_refs = False
    appendix_mode = False
    for block in blocks:
        if block.kind == "heading":
            title = re.sub(r"^\d+(?:\.\d+){0,2}\.?\s+", "", block.text).strip()
            lowered = title.lower()
            if lowered.startswith("references") or lowered in {"bibliography", "参考文献"}:
                seen_refs = True
            if lowered.startswith("appendix") or re.match(r"^[A-Z]\s+[A-Z]", title):
                appendix_mode = True
            sid = f"s{len(sections) + 1:02d}"
            path = title
            if sections:
                parent = _nearest_parent(sections, block.level)
                if parent is not None:
                    path = f"{parent.path} > {title}"
            section = Section(
                sid=sid,
                number=block.number,
                title=title or block.text,
                level=max(1, min(3, block.level or 1)),
                path=path,
                page=block.page,
                appendix=appendix_mode or seen_refs and lowered.startswith(("appendix",)),
                block_ids=[block.id],
            )
            block.sid = sid
            block.kind = "heading"
            sections.append(section)
            current = section
            continue
        if current is not None:
            current.block_ids.append(block.id)
            block.sid = current.sid
    for index, section in enumerate(sections):
        if section.block_ids:
            last_id = section.block_ids[-1]
            last = next((b for b in blocks if b.id == last_id), None)
            section.end_page = last.page if last else section.page
        if index + 1 < len(sections):
            section.end_page = max(section.page, sections[index + 1].page)
        else:
            section.end_page = max([b.page for b in blocks] or [section.page])
    return sections


def _nearest_parent(sections: list[Section], level: int) -> Section | None:
    for section in reversed(sections):
        if section.level < level:
            return section
    return None


_REF_NUM_RE = re.compile(r"^\s*\[(\d{1,3})\]\s*(.*)$")
_REF_LABEL_RE = re.compile(r"\[[A-Za-z][\w+]{0,14}\]")
_REF_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})[a-z]?(?!\d)")
_REF_YEAR_PIECE_RE = re.compile(r"^(?:19|20)\d{2}[a-z]?\s*[.,;]?$")
_REF_VENUE_RE = re.compile(
    r"\s(?:In\s+(?:Proceedings|Advances|The\s+\w+|Proc\.)|Proceedings\b|arXiv\s+preprint|arXiv:|CoRR\b|"
    r"Journal\b|Transactions\b|Conference\b|Workshop\b|Technical\s+Report\b|URL\b|https?://)"
)
_REF_ORG_AUTHOR_RE = re.compile(r"^(?:AI@|OpenAI\b|Google\b|Microsoft\b|Meta\b|Anthropic\b|DeepMind\b|Baidu\b|Tencent\b|Alibaba\b)")
_ABBREVIATIONS = (("et al.", "et al<DOT>"), ("e.g.", "e<DOT>g<DOT>"), ("i.e.", "i<DOT>e<DOT>"), ("vs.", "vs<DOT>"), ("U.S.", "U<DOT>S<DOT>"))


def _split_ref_pieces(raw: str) -> list[str]:
    text = raw
    for short, placeholder in _ABBREVIATIONS:
        text = text.replace(short, placeholder)
    # 保护姓名首字母缩写（"Quoc V. Le"）：否则会在 "V. " 处把作者行切断，只剩下 "Quoc V"
    text = re.sub(r"\b([A-Z])\.(?=\s)", r"\1<DOT>", text)
    pieces = re.split(r"\.\s+(?=[A-Z0-9\"“(])", text)
    return [normalize_ws(piece.replace("<DOT>", ".")) for piece in pieces if piece.strip()]


def _looks_like_author_list(piece: str) -> bool:
    text = normalize_ws(piece)
    if not text or len(text) > 420:
        return False
    if _REF_ORG_AUTHOR_RE.match(text):
        return True
    if not re.match(r"^[A-ZÄÖÜÉŁŠŽĆ](?:\.\s+|\w)", text):
        return False
    names = [p.strip() for p in re.split(r",| and ", text) if p.strip()]
    if len(names) >= 2 and sum(1 for n in names if re.search(r"[A-Z]", n)) >= 2:
        # 标题句通常不会以「姓, 名」这样的列表开头，且作者行很少超过 20 个逗号
        return len(names) <= 25
    # 单人作者：形如 "Aristotle" / "J. Austin" / "AI@Meta"
    return bool(re.fullmatch(r"(?:[A-Z]\.\s*)?[A-ZÄÖÜÉŁŠŽĆ][\w'’\-]+(?:\s+(?:[A-Z]\.|[A-Z][\w'’\-]+)){0,3}", text))


def _looks_like_author_shorthand(piece: str) -> bool:
    """判断 "Cho"/"Vaswani"/"Bahdanau, Cho" 这类只给姓的短作者前缀。

    合成 fixture 与不少会议版参考文献把作者写成 ``Vaswani et al. Attention Is
    All You Need``，若按 et al. 前缀长度 ≥10 才剥离，会把 "Vaswani et al." 留在标题里，
    导致图谱按标题匹配全部失配。
    """
    particles = {"de", "van", "von", "da", "del", "der", "la", "le", "den", "di", "dos", "du"}
    text = normalize_ws(piece)
    if not text or len(text) > 100:
        return False
    tokens = [t for t in re.split(r"[\s,]+", text) if t]
    if not 1 <= len(tokens) <= 8:
        return False
    for token in tokens:
        if token.casefold() in particles:
            continue
        if not re.fullmatch(r"[A-ZÄÖÜÉŁŠŽĆ][\w'’\-\.]*", token):
            return False
    return True


def _split_author_names(piece: str) -> list[str]:
    cleaned = re.sub(r"\s*,?\s*et\s+al\.?", "", piece)
    parts = [p.strip(" ,;") for p in re.split(r",| and ", cleaned) if p.strip(" ,;")]
    merged: list[str] = []
    for part in parts:
        part = normalize_ws(part)
        if not part or len(part) > 60 or not re.search(r"[A-Za-zÀ-ž]", part):
            continue
        if _REF_YEAR_PIECE_RE.match(part):
            continue
        # "Abadi, M., Barham, P." → 逗号拆开后名缩写会单独成项，合并回上一个姓
        if merged and re.fullmatch(r"[A-Z]\.(?:\s?-?[A-Z]\.)*", part):
            merged[-1] = f"{merged[-1]}, {part}"
            continue
        merged.append(part)
    return merged[:20]


def _fill_reference(entry: RefEntry, raw: str) -> None:
    # 非数字标签（"[Bel+15]"/"[ABC20]"）只是条目锚点，不进入作者/标题
    raw = re.sub(r"^\[[A-Za-z][^\]]{0,14}\]\s*", "", raw or "")
    arxiv = ARXIV_ID_RE.search(raw)
    if arxiv:
        entry.arxiv_id = arxiv.group(0)
    doi = DOI_RE.search(raw)
    if doi:
        entry.doi = doi.group(0)
    url = re.search(r"https?://\S+", raw)
    if url:
        entry.url = url.group(0).rstrip(".,);")
    years = [int(y.group(1)) for y in _REF_YEAR_RE.finditer(raw)]
    if years:
        entry.year = max(years)

    pieces = _split_ref_pieces(raw)
    authors: list[str] = []
    index = 0
    # 作者段与标题段的分界常被姓名缩写干扰："Isard, M., et al. Tensorflow: …"。
    # 先按 "et al." 或最后一个「, I.」定位作者段结尾，再解析标题。
    author_prefix = ""
    body = raw
    etal = re.search(r"\bet\s+al\.?\s*", raw)
    if etal and 2 <= etal.start() <= 120 and (
        _looks_like_author_list(raw[: etal.start()]) or _looks_like_author_shorthand(raw[: etal.start()])
    ):
        author_prefix = raw[: etal.end()].strip()
        body = raw[etal.end() :]
    if author_prefix:
        authors = _split_author_names(author_prefix)
        pieces = _split_ref_pieces(body)
    elif pieces and _looks_like_author_list(pieces[0]) and not _REF_YEAR_PIECE_RE.match(pieces[0]):
        authors = _split_author_names(pieces[0])
        index = 1
    while index < len(pieces) and _REF_YEAR_PIECE_RE.match(pieces[index]):
        index += 1
    title = ""
    for piece in pieces[index:]:
        candidate = normalize_ws(piece)
        if len(candidate) < 4 or _REF_YEAR_PIECE_RE.match(candidate) or candidate.startswith(("In ", "URL", "http")):
            continue
        title = candidate
        break
    if not title:
        quoted = re.search(r"[\u201c\"]([^\u201d\"]{8,240})[\u201d\"]", raw)
        if quoted:
            title = quoted.group(1)
        elif pieces and not (authors and normalize_ws(pieces[-1]) == normalize_ws(pieces[0])):
            title = pieces[-1]
    venue_match = _REF_VENUE_RE.search(title)
    if venue_match and venue_match.start() >= 12:
        title = title[: venue_match.start()]
    entry.title = normalize_ws(title).strip().strip('“”"').strip(" .")[:240]
    entry.authors = authors
    if not entry.venue and venue_match:
        entry.venue = normalize_ws(venue_match.group(0)).strip(" ,.")
    venue = re.search(r"(?:In\s+)?(Proceedings|NeurIPS|ICLR|ICML|ACL|EMNLP|CVPR|ICCV|AAAI|IJCAI|Nature|Science|Journal)[^.]{0,80}", raw)
    if venue:
        entry.venue = normalize_ws(venue.group(0))


def _looks_like_entry_start(text: str, prev_raw: str = "") -> bool:
    stripped = normalize_ws(text)
    if len(stripped) < 8:
        return False
    if _REF_NUM_RE.match(stripped) or re.match(r"^\d{1,3}\.\s+[A-Z]", stripped):
        return True
    if _REF_LABEL_RE.match(stripped):
        return True
    if _REF_ORG_AUTHOR_RE.match(stripped):
        return True
    prefix = re.split(r"\.\s", stripped, maxsplit=1)[0] if ". " in stripped else stripped[:220]
    words = prefix.replace(",", " ").split()
    capitalized = sum(
        1 for word in words if word[:1].isupper() or re.fullmatch(r"[A-Z]\.?", word) or word in {"and", "&"}
    )
    ratio = capitalized / max(1, len(words))
    if "," in prefix and ratio >= 0.6 and len(prefix) < 320:
        return True
    if 2 <= len(words) <= 4 and ratio == 1.0:
        return True
    if prev_raw and _REF_YEAR_RE.search(prev_raw[-16:]):
        return False
    return False


def parse_references(reference_blocks: Iterable[TextBlock]) -> list[RefEntry]:
    """把 References 区块解析成条目（支持跨块、跨页合并，兼容 [n] 与 author-year 两种格式）。"""
    blocks = [b for b in reference_blocks if b.kind == "reference" and b.text.strip()]
    if not blocks:
        return []
    numeric = any(_REF_NUM_RE.match(b.text.strip()) for b in blocks)
    entries: list[RefEntry] = []
    if numeric:
        for block in blocks:
            text = normalize_ws(block.text)
            if not text or re.fullmatch(r"\d{1,4}", text):
                continue
            chunks = _REF_SPLIT_RE.split(text)
            for chunk in chunks:
                chunk = normalize_ws(chunk)
                if not chunk:
                    continue
                match = _REF_NUM_RE.match(chunk)
                if match:
                    raw = match.group(2).strip()
                    if len(raw) < 8:
                        continue
                    entry = RefEntry(idx=int(match.group(1)), raw=raw, page=block.page, block_id=block.id)
                    entries.append(entry)
                elif entries and len(chunk) < 400:
                    entries[-1].raw = normalize_ws(f"{entries[-1].raw} {chunk}")
        for entry in entries:
            _fill_reference(entry, entry.raw)
        entries.sort(key=lambda e: e.idx)
        return entries

    for block in blocks:
        text = normalize_ws(block.text)
        if not text or re.fullmatch(r"\d{1,4}", text):
            continue
        # 有些 PDF 把整张参考文献表塞进一个块，条目之间只用 "[Bel+15]" 这类标签分隔
        if len(_REF_LABEL_RE.findall(text)) >= 2:
            for chunk in re.split(r"(?=\[[A-Za-z][\w+]{0,14}\])", text):
                chunk = chunk.strip()
                if len(chunk) >= 12:
                    entries.append(RefEntry(idx=len(entries) + 1, raw=chunk, page=block.page, block_id=block.id))
            continue
        previous_raw = entries[-1].raw if entries else ""
        if not entries or _looks_like_entry_start(text, previous_raw):
            entries.append(RefEntry(idx=len(entries) + 1, raw=text, page=block.page, block_id=block.id))
        elif len(text) < 500:
            entries[-1].raw = normalize_ws(f"{entries[-1].raw} {text}")
    for entry in entries:
        _fill_reference(entry, entry.raw)
    return entries


def short_citation_anchors(blocks: Sequence[TextBlock]) -> dict[str, list[str]]:
    """正文引用标记 [n] → 所在块 id，用于后端建立引用跳转索引。"""
    anchors: dict[str, list[str]] = {}
    for block in blocks:
        for match in CITATION_MARK_RE.finditer(block.text):
            for number in re.findall(r"\d+", match.group(0)):
                anchors.setdefault(number, []).append(block.id)
    return anchors


def analyze(pages: Sequence[PageText], settings: Settings) -> Analysis:
    body_size = guess_body_size(pages)
    blocks = classify_blocks(pages, body_size)
    sections = build_sections(blocks)
    meta = extract_metadata(blocks, pages, body_size)
    reference_blocks = [b for b in blocks if b.kind == "reference"]
    references = parse_references(reference_blocks)
    layout = "double" if any(p.columns >= 2 for p in pages) else "single"
    analysis = Analysis(
        blocks=blocks,
        sections=sections,
        references=references,
        title=meta["title"],
        authors=meta["authors"],
        affiliations=meta["affiliations"],
        abstract=meta["abstract"],
        keywords=meta["keywords"],
        arxiv_id=meta["arxiv_id"],
        arxiv_version=meta["arxiv_version"],
        year=meta["year"],
        venue=meta["venue"],
        doi=meta["doi"],
        layout=layout,
        body_size=body_size,
    )
    if not analysis.title:
        analysis.warnings.append("未能从首页识别标题")
    if not analysis.sections:
        analysis.warnings.append("未识别出章节标题")
    if not analysis.references:
        analysis.warnings.append("未解析出参考文献条目")
    return analysis
