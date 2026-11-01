"""解析结果的数据模型（全部为可 JSON 序列化的纯数据类）。

坐标统一为 PDF 点（左上原点），与 PyMuPDF 保持一致；
``block.id`` 在单篇论文内唯一，格式 ``p{page}-b{order}``，供问答引用与前端高亮定位。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any


@dataclass
class BBox:
    x0: float
    y0: float
    x1: float
    y1: float

    def to_list(self) -> list[float]:
        return [round(self.x0, 2), round(self.y0, 2), round(self.x1, 2), round(self.y1, 2)]

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(0.0, self.y1 - self.y0)

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2)

    def intersects(self, other: "BBox") -> bool:
        return not (self.x1 < other.x0 or other.x1 < self.x0 or self.y1 < other.y0 or other.y1 < self.y0)

    def union(self, other: "BBox") -> "BBox":
        return BBox(min(self.x0, other.x0), min(self.y0, other.y0), max(self.x1, other.x1), max(self.y1, other.y1))

    def expand(self, margin: float) -> "BBox":
        return BBox(self.x0 - margin, self.y0 - margin, self.x1 + margin, self.y1 + margin)

    def contains(self, other: "BBox") -> bool:
        return self.x0 <= other.x0 and self.y0 <= other.y0 and self.x1 >= other.x1 and self.y1 >= other.y1

    @classmethod
    def from_list(cls, value: list[float] | tuple[float, ...]) -> "BBox":
        return cls(float(value[0]), float(value[1]), float(value[2]), float(value[3]))


@dataclass
class TextBlock:
    """正文/标题/脚注等文本块。kind ∈ title|authors|affiliation|meta|heading|paragraph|caption|reference|equation|footer|header|abstract"""

    id: str
    page: int
    kind: str
    text: str
    bbox: BBox
    order: int = 0
    column: int = 0
    level: int = 0
    number: str = ""
    sid: str = ""
    font_size: float = 0.0
    font: str = ""
    bold: bool = False
    italic: bool = False
    centered: bool = False
    ocr: bool = False
    scores: dict[str, float] = field(default_factory=dict)


@dataclass
class TableData:
    id: str
    page: int
    bbox: BBox
    number: str = ""
    caption: str = ""
    header: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    markdown: str = ""
    caption_block: str = ""
    source: str = "pymupdf"


@dataclass
class FigureData:
    id: str
    page: int
    bbox: BBox
    number: str = ""
    caption: str = ""
    kind: str = "image"  # image | vector | mixed
    image_path: str = ""
    caption_block: str = ""
    width: float = 0.0
    height: float = 0.0


@dataclass
class EquationData:
    id: str
    page: int
    bbox: BBox
    plain: str = ""
    latex: str = ""
    number: str = ""
    section: str = ""


@dataclass
class RefEntry:
    idx: int
    raw: str
    title: str = ""
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    venue: str = ""
    doi: str = ""
    arxiv_id: str = ""
    url: str = ""
    block_id: str = ""
    page: int = 0


@dataclass
class Section:
    sid: str
    number: str
    title: str
    level: int
    path: str
    page: int
    end_page: int = 0
    appendix: bool = False
    block_ids: list[str] = field(default_factory=list)


@dataclass
class PageInfo:
    number: int
    width: float
    height: float
    text_chars: int = 0
    blocks: int = 0
    columns: int = 1
    ocr: bool = False
    ocr_engine: str = ""
    rotation: int = 0
    blank: bool = False


@dataclass
class ParseQuality:
    score: float = 0.0
    text_chars: int = 0
    pages: int = 0
    ocr_pages: int = 0
    headings: int = 0
    tables: int = 0
    figures: int = 0
    equations: int = 0
    references: int = 0
    warnings: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)
    version: str = "2.0"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ParsedPaper:
    paper_id: str
    sha256: str = ""
    filename: str = ""
    title: str = ""
    authors: list[str] = field(default_factory=list)
    affiliations: list[str] = field(default_factory=list)
    abstract: str = ""
    keywords: list[str] = field(default_factory=list)
    arxiv_id: str = ""
    arxiv_version: str = ""
    venue: str = ""
    year: int | None = None
    doi: str = ""
    pages: list[PageInfo] = field(default_factory=list)
    blocks: list[TextBlock] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    tables: list[TableData] = field(default_factory=list)
    figures: list[FigureData] = field(default_factory=list)
    equations: list[EquationData] = field(default_factory=list)
    references: list[RefEntry] = field(default_factory=list)
    layout: str = "single"
    scanned: bool = False
    ocr_used: bool = False
    ocr_engine: str = ""
    quality: ParseQuality = field(default_factory=ParseQuality)
    parser_version: str = "2.0"
    elapsed_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ParsedPaper":
        """从缓存 JSON 还原（索引重建、API 读取共享同一份结构）。"""

        def _bbox(value: Any) -> BBox:
            if isinstance(value, BBox):
                return value
            if isinstance(value, dict):
                return BBox(**value)
            if isinstance(value, (list, tuple)) and len(value) == 4:
                return BBox(*value)
            return BBox(0, 0, 0, 0)

        def _rows(key: str, factory: Any) -> list[Any]:
            out = []
            for item in data.get(key) or []:
                payload = dict(item)
                if "bbox" in payload:
                    payload["bbox"] = _bbox(payload["bbox"])
                out.append(factory(**payload))
            return out

        quality_raw = data.get("quality")
        quality = ParseQuality()
        if isinstance(quality_raw, dict):
            allowed = {f.name for f in fields(ParseQuality)}
            quality = ParseQuality(**{k: v for k, v in quality_raw.items() if k in allowed})
        elif isinstance(quality_raw, ParseQuality):
            quality = quality_raw
        return cls(
            paper_id=data.get("paper_id", ""),
            sha256=data.get("sha256", ""),
            filename=data.get("filename", ""),
            title=data.get("title", ""),
            authors=list(data.get("authors") or []),
            affiliations=list(data.get("affiliations") or []),
            abstract=data.get("abstract", ""),
            keywords=list(data.get("keywords") or []),
            arxiv_id=data.get("arxiv_id", ""),
            arxiv_version=data.get("arxiv_version", ""),
            venue=data.get("venue", ""),
            year=data.get("year"),
            doi=data.get("doi", ""),
            pages=_rows("pages", PageInfo),
            blocks=_rows("blocks", TextBlock),
            sections=_rows("sections", Section),
            tables=_rows("tables", TableData),
            figures=_rows("figures", FigureData),
            equations=_rows("equations", EquationData),
            references=_rows("references", RefEntry),
            layout=data.get("layout", "single"),
            scanned=bool(data.get("scanned")),
            ocr_used=bool(data.get("ocr_used")),
            ocr_engine=data.get("ocr_engine", ""),
            quality=quality,
            parser_version=data.get("parser_version", "2.0"),
            elapsed_ms=float(data.get("elapsed_ms") or 0.0),
        )

    # ------------------------------------------------------------ 便捷查询

    def block_map(self) -> dict[str, TextBlock]:
        return {b.id: b for b in self.blocks}

    def section_map(self) -> dict[str, Section]:
        return {s.sid: s for s in self.sections}

    def heading_tree(self) -> list[dict[str, Any]]:
        """按层级栈构建标题树（容忍缺级：level 1 → 3 时 3 挂在 1 下）。"""
        nodes: dict[str, dict[str, Any]] = {
            section.sid: {
                "sid": section.sid,
                "number": section.number,
                "title": section.title,
                "level": section.level,
                "page": section.page,
                "end_page": section.end_page,
                "appendix": section.appendix,
                "children": [],
            }
            for section in self.sections
        }
        by_sid = {s.sid: s for s in self.sections}
        roots: list[dict[str, Any]] = []
        stack: list[str] = []
        for section in self.sections:
            while stack and by_sid[stack[-1]].level >= section.level:
                stack.pop()
            node = nodes[section.sid]
            if stack:
                nodes[stack[-1]]["children"].append(node)
            else:
                roots.append(node)
            stack.append(section.sid)
        return roots

    def section_path_for_page(self, page: int) -> str:
        for section in reversed(self.sections):
            if section.page <= page <= (section.end_page or section.page):
                return section.path
        return ""

    def full_text(self, limit: int | None = None) -> str:
        parts: list[str] = []
        for block in self.blocks:
            if block.kind in {"paragraph", "heading", "abstract", "reference", "caption"}:
                parts.append(block.text)
        text = "\n\n".join(parts)
        return text[:limit] if limit else text


def _parent_sid(section: Section, sections: list[Section]) -> str:
    """按文档顺序寻找最近的更高级标题。"""
    try:
        index = sections.index(section)
    except ValueError:
        return ""
    for prev in reversed(sections[:index]):
        if prev.level < section.level:
            return prev.sid
    return ""
