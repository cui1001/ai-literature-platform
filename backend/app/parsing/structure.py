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
from .pdftext import PageText, RawBlock

_ABSTRACT_RE = re.compile(r"^\s*(?:abstract|摘\s*要)\b[.:：—-]?\s*", re.I)
_KEYWORDS_RE = re.compile(r"^\s*(?:key\s*words?|index\s*terms?|关键词)\b[.:：—-]?\s*", re.I)
_AFFIL_RE = re.compile(
    r"universit|institut|college|laborator|department|school of|academy|google|microsoft|openai|meta\b|deepmind|"
    r"baidu|tencent|alibaba|bytedance|research|inc\.|ltd|gmbh|@[\w.\-]+\.\w+",
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
    full_width = page.width * 0.72
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
            else:
                kind = "paragraph"
            level = 0
            number = ""
            heading_hint, hint_level, hint_number = looks_like_heading(text)
            if kind == "paragraph":
                big = raw_block.size >= max(body_size * 1.06, body_size + 0.4)
                short = len(text) <= 140 and _heading_text_ok(text)
                size_ok = _heading_size_ok(raw_block, body_size)
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


def extract_metadata(blocks: list[TextBlock], pages: Sequence[PageText], body_size: float) -> dict[str, Any]:
    """标题、作者、机构、摘要、关键词、arXiv 编号。"""
    page1 = [b for b in blocks if b.page == 1 and b.kind not in {"header", "footer"}]
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

    # arXiv 编号与年份（页眉/首页任意位置）
    joined = " ".join(b.text for b in page1[:8])
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
    doi = DOI_RE.search(" ".join(b.text for b in page1))
    if doi:
        meta["doi"] = doi.group(0)

    # 摘要起点之前的区域视为「标题区」
    abstract_index = next(
        (i for i, b in enumerate(page1) if b.kind == "heading" and b.text.strip().lower().startswith(("abstract", "摘要"))),
        None,
    )
    head_zone = page1[: abstract_index if abstract_index is not None else min(6, len(page1))]
    head_zone = [b for b in head_zone if b.kind in {"paragraph", "heading"}]
    if head_zone:
        max_size = max(b.font_size for b in head_zone)
        title_blocks = [
            b for b in head_zone if b.font_size >= max(body_size * 1.5, max_size - 0.6) and b.bbox.y0 < pages[0].height * 0.5
        ]
        if not title_blocks:
            title_blocks = [head_zone[0]]
        title_blocks.sort(key=lambda b: b.bbox.y0)
        parts: list[str] = []
        for block in title_blocks:
            if parts and _ends_sentence(parts[-1]):
                break
            parts.append(block.text)
        title = " ".join(parts).strip()
        meta["title"] = normalize_ws(title)
        title_ids = {b.id for b in title_blocks}
        rest = [b for b in head_zone if b.id not in title_ids]
        authors: list[str] = []
        affiliations: list[str] = []
        # 原始 PDF 的作者行可能被 PyMuPDF 拆成多个紧邻文本块（姓名单行折行），
        # 这里直接用标题下方到 Abstract 之间的原始块重建作者/机构，避免丢名字。
        title_bottom = min(b.bbox.y1 for b in title_blocks)
        abstract_top = page1[abstract_index].bbox.y0 if abstract_index is not None else None
        raw_zone = [
            b
            for b in pages[0].blocks
            if b.text.strip()
            and b.bbox.y0 >= title_bottom - 1
            and (abstract_top is None or b.bbox.y1 <= abstract_top - 1)
        ]
        raw_authors: list[str] = []
        raw_affiliations: list[str] = []
        for raw in sorted(raw_zone, key=lambda b: b.bbox.y0):
            text = normalize_ws(raw.text)
            if not text or text.lower().startswith(("arxiv:", "doi")):
                continue
            if ";" in text and _AFFIL_RE.search(text):
                raw_affiliations.extend(p.strip() for p in text.split(";") if p.strip())
                continue
            if _AFFIL_RE.search(text) and "," not in text and len(text) < 160 and not _ends_sentence(text):
                raw_affiliations.append(text)
                continue
            if "," in text or " and " in text:
                raw_authors.extend(_split_authors(text))
            elif not raw_affiliations and not raw_authors and len(text) < 120 and not _ends_sentence(text):
                raw_authors.extend(_split_authors(text))
        if raw_authors:
            authors = list(dict.fromkeys(raw_authors))
        if raw_affiliations:
            affiliations = list(dict.fromkeys(raw_affiliations))
        if not authors:
            for block in rest:
                text = block.text.strip()
                if not text:
                    continue
                if _AFFIL_RE.search(text) and not authors:
                    affiliations.append(text)
                elif "," in text or " and " in text or "·" in text:
                    authors.extend(_split_authors(text))
                elif len(authors) == 0 and len(text) < 120 and not _ends_sentence(text):
                    authors.extend(_split_authors(text))
        meta["authors"] = authors[:20]
        meta["affiliations"] = [truncate(a, 300) for a in affiliations[:8]]

    if abstract_index is not None:
        parts = []
        for block in page1[abstract_index + 1 :]:
            if block.kind == "heading":
                break
            if block.kind in {"paragraph", "equation", "caption"}:
                parts.append(block.text)
            if sum(len(p) for p in parts) > 2600:
                break
        meta["abstract"] = normalize_ws(_ABSTRACT_RE.sub("", " ".join(parts)))
    else:
        for block in page1:
            if _ABSTRACT_RE.match(block.text):
                meta["abstract"] = normalize_ws(_ABSTRACT_RE.sub("", block.text))[:2600]
                break

    for block in page1:
        if _KEYWORDS_RE.match(block.text):
            raw = _KEYWORDS_RE.sub("", block.text)
            meta["keywords"] = [k.strip(" .;") for k in re.split(r"[,;，；]", raw) if k.strip()]
            break
    return meta


def _ends_sentence(text: str) -> bool:
    return bool(re.search(r"[.。!?！？]$", text.strip()))


def _split_authors(text: str) -> list[str]:
    cleaned = re.sub(r"[\d∗†‡§¶]+", "", text)
    cleaned = re.sub(r"\s+and\s+", ",", cleaned)
    parts = [p.strip(" ,.;*") for p in re.split(r",|、|;", cleaned)]
    return [p for p in parts if p and len(p) < 80]


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


def parse_references(reference_blocks: Iterable[TextBlock]) -> list[RefEntry]:
    """把 References 区块解析成条目（支持跨块、跨页合并）。"""
    blocks = list(reference_blocks)
    if not blocks:
        return []
    joined = ""
    offsets: list[tuple[int, TextBlock]] = []
    for block in blocks:
        offsets.append((len(joined), block))
        joined += " " + block.text
    entries: list[RefEntry] = []
    for chunk in _REF_SPLIT_RE.split(joined):
        chunk = normalize_ws(chunk)
        if not chunk:
            continue
        match = re.match(r"^\[(\d{1,3})\]\s*(.*)$", chunk)
        if not match:
            if entries and len(chunk) < 300:
                entries[-1].raw = normalize_ws(f"{entries[-1].raw} {chunk}")
            continue
        idx = int(match.group(1))
        raw = match.group(2).strip()
        if len(raw) < 8:
            continue
        entry = RefEntry(idx=idx, raw=raw, page=_page_for_offset(joined, raw, offsets))
        entry.block_id = _block_for_offset(joined, raw, offsets)
        _fill_reference(entry, raw)
        entries.append(entry)
    entries.sort(key=lambda e: e.idx)
    return entries


def _page_for_offset(joined: str, raw: str, offsets: list[tuple[int, TextBlock]]) -> int:
    position = joined.find(raw)
    page = 0
    for offset, block in offsets:
        if offset <= position:
            page = block.page
        else:
            break
    return page


def _block_for_offset(joined: str, raw: str, offsets: list[tuple[int, TextBlock]]) -> str:
    position = joined.find(raw)
    block_id = ""
    for offset, block in offsets:
        if offset <= position:
            block_id = block.id
        else:
            break
    return block_id


def _fill_reference(entry: RefEntry, raw: str) -> None:
    arxiv = ARXIV_ID_RE.search(raw)
    if arxiv:
        entry.arxiv_id = arxiv.group(0)
    doi = DOI_RE.search(raw)
    if doi:
        entry.doi = doi.group(0)
        entry.venue = entry.venue or "DOI"
    url = re.search(r"https?://\S+", raw)
    if url:
        entry.url = url.group(0).rstrip(".,);")
    years = [int(y.group(0)) for y in YEAR_RE.finditer(raw)]
    if years:
        entry.year = max(years)
    # 标题：优先引号内内容，否则取第一个「大写开头且长度足够」的句子
    quoted = re.findall(r"[\"“”]([^\"“”]{8,200})[\"“”]", raw)
    if quoted:
        entry.title = normalize_ws(quoted[0])
    else:
        pieces = [p.strip() for p in re.split(r"\.\s+", raw) if p.strip()]
        candidate = next((p for p in pieces if len(p) > 12 and not re.match(r"^[\d\W]", p)), pieces[0] if pieces else raw)
        entry.title = normalize_ws(candidate)[:240]
    # 作者：标题之前的片段，截断到前 6 人
    head = raw.split(entry.title)[0] if entry.title and entry.title in raw else raw[:160]
    head = re.sub(r"(?:et al\.?|and)\s*$", "", head).strip(" .,;")
    if head and not re.match(r"^[\d\W]", head):
        authors = [normalize_ws(a) for a in re.split(r",|;|、| and ", head) if normalize_ws(a) and len(a) > 2]
        entry.authors = authors[:6]
    venue_match = re.search(r"(?:In\s+)?(Proceedings|NeurIPS|ICLR|ICML|ACL|EMNLP|CVPR|ICCV|AAAI|IJCAI|Nature|Science|Journal)[^.]{0,80}", raw)
    if venue_match:
        entry.venue = normalize_ws(venue_match.group(0))


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
