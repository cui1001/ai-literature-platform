"""页面级抽取：文本块、样式、分栏判断、扫描件 OCR 回落。

只负责「拿到干净的原始块」，不做语义分类（见 :mod:`app.parsing.structure`）。
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Any

from ..core.config import Settings
from ..core.logging import get_logger
from ..core.textutil import normalize_ws
from ..providers.ocr import ocr_page
from .model import BBox

log = get_logger(__name__)

# span flags 位（PyMuPDF 约定）
FLAG_SUPERSCRIPT = 1
FLAG_ITALIC = 2
FLAG_SERIF = 4
FLAG_MONO = 8
FLAG_BOLD = 16


@dataclass
class RawLine:
    text: str
    bbox: BBox
    size: float = 0.0
    font: str = ""
    bold: bool = False
    italic: bool = False


@dataclass
class RawBlock:
    bbox: BBox
    lines: list[RawLine] = field(default_factory=list)
    kind: str = "text"  # text | image
    image_bytes: bytes | None = None
    ocr: bool = False

    @property
    def text(self) -> str:
        return join_lines([line.text for line in self.lines])

    @property
    def size(self) -> float:
        sizes = [line.size for line in self.lines if line.size]
        return statistics.median(sizes) if sizes else 0.0

    @property
    def bold(self) -> bool:
        flags = [line.bold for line in self.lines if line.text.strip()]
        return bool(flags) and sum(flags) >= max(1, len(flags) // 2)

    @property
    def italic(self) -> bool:
        flags = [line.italic for line in self.lines if line.text.strip()]
        return bool(flags) and sum(flags) >= max(1, len(flags) // 2)

    @property
    def font(self) -> str:
        fonts = [line.font for line in self.lines if line.font]
        return max(set(fonts), key=fonts.count) if fonts else ""


@dataclass
class PageText:
    number: int
    width: float
    height: float
    blocks: list[RawBlock]
    text_chars: int = 0
    ocr: bool = False
    ocr_engine: str = ""
    columns: int = 1
    rotation: int = 0


_HYPHEN_END = re.compile(r"(\w)-$")


def join_lines(lines: list[str]) -> str:
    """拼接同一块内的多行文本：修复行尾断词，去掉孤立的页码/分隔符。"""
    out = ""
    for raw in lines:
        line = normalize_ws(raw)
        if not line:
            continue
        if not out:
            out = line
            continue
        if _HYPHEN_END.search(out) and line[:1].islower():
            out = out[:-1] + line
        else:
            out = f"{out} {line}"
    return out.strip()


def _span_flags(flags: int) -> tuple[bool, bool]:
    return bool(flags & FLAG_BOLD), bool(flags & FLAG_ITALIC)


def _font_size_weighted(spans: list[dict[str, Any]]) -> float:
    total = sum(max(1, len(span.get("text") or "")) for span in spans)
    if not total:
        return 0.0
    return sum(float(span.get("size") or 0) * max(1, len(span.get("text") or "")) for span in spans) / total


def _block_from_dict(block: dict[str, Any], *, page_rect: tuple[float, float]) -> RawBlock | None:
    bbox = BBox.from_list(block.get("bbox") or [0, 0, 0, 0])
    if bbox.width <= 0 or bbox.height <= 0:
        return None
    lines: list[RawLine] = []
    for raw_line in block.get("lines") or []:
        spans = raw_line.get("spans") or []
        text = "".join(span.get("text") or "" for span in spans)
        if not text.strip():
            continue
        line_bbox = BBox.from_list(raw_line.get("bbox") or bbox.to_list())
        bold, italic = False, False
        if spans:
            bold, italic = _span_flags(int(spans[0].get("flags") or 0))
            font = str(spans[0].get("font") or "")
            # 字体名兜底：Bold/Black/Heavy 视为粗体
            lowered = font.lower()
            bold = bold or "bold" in lowered or "black" in lowered or "heavy" in lowered
            italic = italic or "italic" in lowered or "oblique" in lowered
        else:
            font = ""
        lines.append(
            RawLine(
                text=text,
                bbox=line_bbox,
                size=round(_font_size_weighted(spans), 2),
                font=font,
                bold=bold,
                italic=italic,
            )
        )
    if not lines:
        return None
    return RawBlock(bbox=bbox, lines=lines, kind="text")


def _block_from_image(block: dict[str, Any]) -> RawBlock:
    bbox = BBox.from_list(block.get("bbox") or [0, 0, 0, 0])
    raw = block.get("image")
    return RawBlock(bbox=bbox, kind="image", image_bytes=raw if isinstance(raw, (bytes, bytearray)) else None)


def split_mixed_blocks(blocks: list[RawBlock]) -> list[RawBlock]:
    """把「图注 + 后面的标题」这类被 PDF 结构合并的块按字号拆开。

    PyMuPDF 的 dict 模式经常把相邻但字号不同的行放进同一个 block，例如
    图注（8.5pt）后面紧跟 ``References``（12pt）。若整块按中位字号分类，
    就会丢掉 References / 附录标题，进而导致参考文献解析为空。
    这里按行字号做连续分段，字号差异不足 20% 的块保持原样。
    """
    out: list[RawBlock] = []
    for block in blocks:
        if block.kind != "text" or len(block.lines) < 2:
            out.append(block)
            continue
        sizes = [line.size for line in block.lines if line.size]
        if not sizes or max(sizes) < min(sizes) * 1.2:
            out.append(block)
            continue
        groups: list[list[RawLine]] = []
        for line in block.lines:
            if not groups:
                groups.append([line])
                continue
            base = groups[-1][0].size or line.size
            same = abs((line.size or base) - base) <= max(0.8, base * 0.08)
            if same:
                groups[-1].append(line)
            else:
                groups.append([line])
        for group in groups:
            bbox = group[0].bbox
            for line in group[1:]:
                bbox = bbox.union(line.bbox)
            out.append(RawBlock(bbox=bbox, lines=group, kind=block.kind, ocr=block.ocr))
    return out


def detect_columns(blocks: list[RawBlock], page_width: float, text_left: float, text_right: float) -> int:
    """依据块的水平分布判断单/双栏：双栏要求左右两簇都有足够多、且窄的块。"""
    candidates = [b for b in blocks if b.kind == "text" and b.bbox.width > 40 and b.text.strip()]
    if len(candidates) < 6:
        return 1
    usable_width = max(1.0, text_right - text_left)
    narrow = [b for b in candidates if b.bbox.width < usable_width * 0.62]
    if len(narrow) < max(4, len(candidates) // 3):
        return 1
    mid = (text_left + text_right) / 2
    left = [b for b in narrow if (b.bbox.x0 + b.bbox.x1) / 2 < mid - 6]
    right = [b for b in narrow if (b.bbox.x0 + b.bbox.x1) / 2 > mid + 6]
    if len(left) >= 3 and len(right) >= 3:
        # 两簇的纵向分布应大体覆盖页面，排除“仅表格/页脚在右侧”的误判
        coverage = (max(b.bbox.y1 for b in right) - min(b.bbox.y0 for b in right)) if right else 0
        if coverage > (text_right - text_left) * 0.25:
            return 2
    return 1


def extract_page(page: Any, settings: Settings, *, page_number: int | None = None, dpi: int | None = None) -> PageText:
    """抽取单页：优先文本层；文本过少时自动 OCR。"""
    number = page_number if page_number is not None else page.number + 1
    rect = page.rect
    width, height = float(rect.width), float(rect.height)
    margin = min(64.0, width * 0.12)
    text_left, text_right = margin, width - margin

    raw = page.get_text("dict")
    blocks: list[RawBlock] = []
    for block in raw.get("blocks") or []:
        if block.get("type") == 0:
            converted = _block_from_dict(block, page_rect=(width, height))
            if converted is not None:
                blocks.append(converted)
        elif block.get("type") == 1:
            converted_image = _block_from_image(block)
            if converted_image.bbox.area > 400:
                blocks.append(converted_image)

    blocks = split_mixed_blocks(blocks)
    text_chars = sum(len(b.text) for b in blocks if b.kind == "text")
    ocr_used = False
    ocr_engine = ""
    rotation = int(page.rotation or 0)

    if text_chars < settings.ocr_text_threshold:
        result = ocr_page(page, dpi=min(300, max(150, settings.page_render_dpi)), lang="")
        if result.text.strip():
            blocks = _ocr_blocks(result)
            text_chars = sum(len(b.text) for b in blocks)
            ocr_used = not result.degraded
            ocr_engine = result.engine
        elif result.notes:
            log.info("page %d OCR unavailable: %s", number, result.notes[:160])
            ocr_engine = "none"

    columns = detect_columns(blocks, width, text_left, text_right)
    return PageText(
        number=number,
        width=width,
        height=height,
        blocks=blocks,
        text_chars=text_chars,
        ocr=ocr_used,
        ocr_engine=ocr_engine,
        columns=columns,
        rotation=rotation,
    )


def _ocr_blocks(result: Any) -> list[RawBlock]:
    """按 OCR 行分组，再按垂直间距合并为段落块。"""
    lines: dict[str, list[Any]] = {}
    order: list[str] = []
    for word in result.words:
        key = word.line.strip() or word.text
        if key not in lines:
            lines[key] = []
            order.append(key)
        lines[key].append(word)
    raw_blocks: list[RawBlock] = []
    for key in order:
        words = sorted(lines[key], key=lambda w: w.bbox[0])
        text = " ".join(w.text for w in words).strip()
        if not text:
            continue
        x0 = min(w.bbox[0] for w in words)
        y0 = min(w.bbox[1] for w in words)
        x1 = max(w.bbox[2] for w in words)
        y1 = max(w.bbox[3] for w in words)
        height = max(1.0, y1 - y0)
        raw_blocks.append(
            RawBlock(
                bbox=BBox(x0, y0, x1, y1),
                lines=[RawLine(text=text, bbox=BBox(x0, y0, x1, y1), size=round(height * 0.9, 2), font="ocr")],
                kind="text",
                ocr=True,
            )
        )
    # 合并相邻行成段：同一栏、垂直间距 < 0.9 倍行高、且上一行未以句号式标点结束
    merged: list[RawBlock] = []
    for block in raw_blocks:
        if not merged:
            merged.append(block)
            continue
        prev = merged[-1]
        same_column = abs(prev.bbox.x0 - block.bbox.x0) < 24
        gap = block.bbox.y0 - prev.bbox.y1
        line_height = max(prev.bbox.height, block.bbox.height)
        continues = not re.search(r"[.。!?！？:：]$", prev.text.strip())
        heading_like = len(prev.text) < 60 and re.match(r"^\d+(\.\d+)*\s+[A-Z]", prev.text)
        if same_column and -2 <= gap <= line_height * 0.9 and continues and not heading_like:
            prev.lines.append(block.lines[0])
            prev.bbox = prev.bbox.union(block.bbox)
        else:
            merged.append(block)
    return merged


def render_page_png(page: Any, settings: Settings, dpi: int | None = None) -> bytes:
    zoom = (dpi or settings.page_render_dpi) / 72.0
    import fitz

    pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    return pixmap.tobytes("png")
