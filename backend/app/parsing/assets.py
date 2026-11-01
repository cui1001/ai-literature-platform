"""图表、公式、图片资源的抽取与落盘。

- 表格：优先 PyMuPDF 的 ``find_tables``（无边框表也能识别），并保留 bbox 供前端高亮；
- 图片：位图块直接裁剪；矢量图按「图元簇」聚合，需配合 caption 过滤装饰性线条；
- 公式：由文本块的行内数学特征 + 编号 ``(n)`` 判定，输出可读 plain 与基础 LaTeX。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Sequence

from ..core.config import Settings
from ..core.hashing import sha1_text
from ..core.logging import get_logger
from .model import BBox, EquationData, FigureData, TableData, TextBlock

log = get_logger(__name__)

_CAPTION_RE = re.compile(r"^\s*(table|figure|fig\.|表|图)\s*(\d+)[:：.]?\s*", re.I)
_MIN_IMAGE_AREA = 4500.0
_MIN_FIGURE_HEIGHT = 45.0


def table_to_markdown(header: list[str], rows: list[list[str]]) -> str:
    def esc(cell: str) -> str:
        return (cell or "").replace("|", "\\|").replace("\n", " ").strip()

    lines = ["| " + " | ".join(esc(h) for h in header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    for row in rows:
        padded = list(row) + [""] * max(0, len(header) - len(row))
        lines.append("| " + " | ".join(esc(c) for c in padded[: len(header)]) + " |")
    return "\n".join(lines)


_TABLE_CAPTION_RE = re.compile(r"^\s*(?:table|表)\s*(\d{1,3})\s*[:：.]?\s*", re.I)


def extract_tables(page: Any, page_no: int, blocks: Sequence[TextBlock]) -> list[TableData]:
    """抽取表格。

    两路来源，互为补充：

    1. ``page.find_tables()`` —— 对真实论文中带边框/留白的表格有效；
    2. ``_tables_from_captions`` —— 面向「用文字排版的表格」（无边框、列对齐），
       先定位 ``Table N:`` 说明，再在说明下方的文本带中按列间距切分单元格。

    合成语料与大量 arXiv 论文的表格都属于第 2 类，仅靠 find_tables 会全部漏掉。
    """
    tables: list[TableData] = []
    page_area = max(1.0, float(page.rect.width) * float(page.rect.height))
    seen: list[BBox] = []

    try:
        finder = page.find_tables()
        raw_tables = list(getattr(finder, "tables", []) or [])
    except Exception as exc:  # pragma: no cover - 依赖具体 PDF
        log.debug("find_tables failed on page %s: %s", page_no, exc)
        raw_tables = []

    for index, table in enumerate(raw_tables, start=1):
        try:
            rows = table.extract() or []
        except Exception:
            continue
        rows = [[_clean_cell(cell) for cell in row] for row in rows if row]
        if len(rows) < 2:
            continue
        bbox = BBox.from_list(table.bbox)
        if bbox.area > page_area * 0.55:  # 整页误检（find_tables 对纯文本页的典型误判）
            continue
        width = max(len(row) for row in rows)
        rows = [row + [""] * (width - len(row)) for row in rows]
        caption, caption_block = _find_caption(blocks, bbox, page_no, "table")
        number_match = _CAPTION_RE.match(caption) if caption else None
        number = number_match.group(2) if number_match else str(index)
        tables.append(
            TableData(
                id=f"t{page_no}-{index}",
                page=page_no,
                bbox=bbox,
                number=number,
                caption=caption,
                header=rows[0],
                rows=rows[1:],
                markdown=table_to_markdown(rows[0], rows[1:]),
                caption_block=caption_block,
                source="lines",
            )
        )
        seen.append(bbox)

    for item in _tables_from_captions(page, page_no, blocks):
        if any(item.bbox.intersects(box) and item.bbox.union(box).area < item.bbox.area * 1.15 for box in seen):
            continue
        tables.append(item)
        seen.append(item.bbox)
    return tables


def _tables_from_captions(page: Any, page_no: int, blocks: Sequence[TextBlock]) -> list[TableData]:
    """按 ``Table N`` 说明 + 下方对齐文本行还原无边框表格。"""
    captions = [
        block
        for block in blocks
        if block.page == page_no and block.kind != "header" and _TABLE_CAPTION_RE.match(block.text.strip())
    ]
    if not captions:
        return []
    try:
        words = page.get_text("words")
    except Exception:  # pragma: no cover
        return []
    items = [
        (BBox(float(w[0]), float(w[1]), float(w[2]), float(w[3])), str(w[4]))
        for w in words
        if len(w) >= 5 and str(w[4]).strip()
    ]
    results: list[TableData] = []
    for caption in captions:
        left, right = _column_bounds(caption, blocks, page)
        lower = caption.bbox.y1 - 1.0
        limit = min(float(page.rect.height) - 36.0, lower + 260.0)
        for block in blocks:
            if block.page != page_no or block.id == caption.id or block.kind != "heading":
                continue
            if block.bbox.y0 > lower + 2 and block.bbox.y0 < limit and block.bbox.x0 < right and block.bbox.x1 > left:
                limit = min(limit, block.bbox.y0)
        band = [
            (bbox, text)
            for bbox, text in items
            if bbox.y0 >= lower and bbox.y1 <= limit + 2 and bbox.x0 >= left - 6 and bbox.x1 <= right + 6
        ]
        rows = _group_rows(band)
        rows = _trim_table_rows(rows)
        if len(rows) < 2 or max(len(row) for row in rows) < 2:
            continue
        header = rows[0]
        body = [(row + [""] * len(header))[: len(header)] for row in rows[1:]]
        bbox = BBox(min(r[0].x0 for r in band), lower, max(r[0].x1 for r in band), limit)
        match = _TABLE_CAPTION_RE.match(caption.text.strip())
        number = match.group(1) if match else ""
        results.append(
            TableData(
                id=f"t{page_no}-c{number or len(results) + 1}",
                page=page_no,
                bbox=bbox,
                number=number,
                caption=caption.text.strip(),
                header=header,
                rows=body,
                markdown=table_to_markdown(header, body),
                caption_block=caption.id,
                source="caption",
            )
        )
    return results


def _column_bounds(caption: TextBlock, blocks: Sequence[TextBlock], page: Any) -> tuple[float, float]:
    """依据同栏文本块的横向范围推断表格可用宽度。"""
    same = [
        block
        for block in blocks
        if block.page == caption.page
        and block.kind in {"paragraph", "caption", "heading", "reference", "table_text"}
        and block.bbox.width > 60
        and block.column == caption.column
    ]
    if not same:
        margin = min(64.0, float(page.rect.width) * 0.12)
        return margin, float(page.rect.width) - margin
    left = min(block.bbox.x0 for block in same)
    right = max(block.bbox.x1 for block in same)
    return max(0.0, left - 2.0), min(float(page.rect.width), right + 2.0)


def _group_rows(band: list[tuple[BBox, str]]) -> list[list[str]]:
    """按纵坐标聚成行，再按横向间距切成单元格。"""
    if not band:
        return []
    entries = sorted(band, key=lambda item: ((item[0].y0 + item[0].y1) / 2, item[0].x0))
    rows: list[list[tuple[BBox, str]]] = []
    for bbox, text in entries:
        if rows:
            last_center = sum((b.y0 + b.y1) / 2 for b, _text in rows[-1]) / len(rows[-1])
            center = (bbox.y0 + bbox.y1) / 2
            if abs(center - last_center) <= 3.0:
                rows[-1].append((bbox, text))
                continue
        rows.append([(bbox, text)])
    out: list[list[str]] = []
    for row in rows:
        row.sort(key=lambda item: item[0].x0)
        cells: list[str] = []
        buffer: list[str] = []
        last_x1 = None
        for bbox, text in row:
            if last_x1 is not None and bbox.x0 - last_x1 > 7.0:
                cells.append(" ".join(buffer).strip())
                buffer = []
            buffer.append(text)
            last_x1 = bbox.x1
        if buffer:
            cells.append(" ".join(buffer).strip())
        out.append([cell for cell in cells if cell])
    return out


def _trim_table_rows(rows: list[list[str]]) -> list[list[str]]:
    """去掉表头前/表尾后的普通段落行。"""
    start = 0
    while start < len(rows) and len(rows[start]) < 2:
        start += 1
    if start >= len(rows):
        return []
    trimmed: list[list[str]] = []
    empty_run = 0
    for row in rows[start:]:
        if len(row) < 2:
            empty_run += 1
            if empty_run >= 2:
                break
            continue
        empty_run = 0
        trimmed.append(row)
    return trimmed


def _clean_cell(cell: Any) -> str:
    if cell is None:
        return ""
    return re.sub(r"\s+", " ", str(cell)).strip()


def _find_caption(blocks: Sequence[TextBlock], bbox: BBox, page_no: int, kind: str) -> tuple[str, str]:
    prefix = re.compile(rf"^\s*({kind}|fig\.|figure|table|表|图)\s*\d+", re.I)
    candidates = [
        b
        for b in blocks
        if b.page == page_no and prefix.match(b.text) and b.id != "" and b.bbox.y1 <= bbox.y0 + 6 and bbox.y0 - b.bbox.y1 < 80
    ]
    if not candidates:
        candidates = [
            b
            for b in blocks
            if b.page == page_no and prefix.match(b.text) and abs(b.bbox.y0 - bbox.y1) < 60
        ]
    if not candidates:
        return "", ""
    best = min(candidates, key=lambda b: abs(b.bbox.y1 - bbox.y0))
    return best.text, best.id


def _nearest_text(blocks: Sequence[TextBlock], bbox: BBox, *, prefer: str = "above", max_gap: float = 40) -> TextBlock | None:
    pool = []
    for block in blocks:
        if block.kind not in {"paragraph", "caption"} or block.page != bbox_probe_page(bbox, blocks):
            continue
        if prefer == "above" and block.bbox.y1 <= bbox.y0 + 2:
            gap = bbox.y0 - block.bbox.y1
        elif prefer == "below" and block.bbox.y0 >= bbox.y1 - 2:
            gap = block.bbox.y0 - bbox.y1
        else:
            continue
        if gap <= max_gap:
            pool.append((gap, block))
    return min(pool, key=lambda item: item[0])[1] if pool else None


def bbox_probe_page(bbox: BBox, blocks: Sequence[TextBlock]) -> int:
    for block in blocks:
        if block.bbox.intersects(bbox):
            return block.page
    return 0


def extract_figures(
    page: Any,
    page_no: int,
    blocks: Sequence[TextBlock],
    settings: Settings,
    paper_id: str,
    *,
    table_boxes: Sequence[BBox] = (),
) -> list[FigureData]:
    figures: list[FigureData] = []
    page_area = max(1.0, page.rect.width * page.rect.height)
    candidates: list[tuple[BBox, str]] = []
    for block in blocks:
        if block.page != page_no or block.kind != "image":
            continue
        if block.bbox.area < _MIN_IMAGE_AREA or block.bbox.area > page_area * 0.92:
            continue
        if any(block.bbox.intersects(box) and block.bbox.union(box).area < box.area * 1.05 for box in table_boxes):
            continue
        candidates.append((block.bbox, "image"))
    for cluster in _vector_clusters(page, table_boxes):
        candidates.append((cluster, "vector"))
    if not candidates:
        return figures
    # 合并高度重叠的候选
    merged: list[tuple[BBox, str]] = []
    for bbox, kind in sorted(candidates, key=lambda item: item[0].area, reverse=True):
        duplicate = next((m for m in merged if m[0].intersects(bbox) and _overlap_ratio(m[0], bbox) > 0.55), None)
        if duplicate:
            if duplicate[1] != "image":
                merged[merged.index(duplicate)] = (duplicate[0].union(bbox), kind)
            continue
        merged.append((bbox, kind))
    figures_dir = settings.figures_dir
    figures_dir.mkdir(parents=True, exist_ok=True)
    for index, (bbox, kind) in enumerate(sorted(merged, key=lambda item: item[0].y0), start=1):
        caption, caption_block = _find_caption(blocks, bbox, page_no, "figure")
        if not caption:
            caption, caption_block = "", ""
        number_match = _CAPTION_RE.match(caption) if caption else None
        number = number_match.group(2) if number_match else str(index)
        image_path = ""
        try:
            zoom = 150 / 72.0
            import fitz

            pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=fitz.Rect(bbox.x0, bbox.y0, bbox.x1, bbox.y1), alpha=False)
            filename = f"{paper_id}-p{page_no}-f{index}.png"
            target = figures_dir / filename
            pixmap.save(str(target))
            image_path = f"figures/{filename}"
        except Exception as exc:  # pragma: no cover
            log.debug("figure crop failed: %s", exc)
        figures.append(
            FigureData(
                id=f"fig{page_no}-{index}",
                page=page_no,
                bbox=bbox,
                number=number,
                caption=caption,
                kind=kind,
                image_path=image_path,
                caption_block=caption_block,
                width=round(bbox.width, 1),
                height=round(bbox.height, 1),
            )
        )
    return figures


def _overlap_ratio(a: BBox, b: BBox) -> float:
    x0, y0 = max(a.x0, b.x0), max(a.y0, b.y0)
    x1, y1 = min(a.x1, b.x1), min(a.y1, b.y1)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    return inter / max(1.0, min(a.area, b.area))


def _vector_clusters(page: Any, table_boxes: Sequence[BBox]) -> list[BBox]:
    """把矢量图元按空间邻近聚合成图块，过滤表格线与装饰性线段。"""
    try:
        drawings = page.get_drawings()
    except Exception:
        return []
    boxes: list[BBox] = []
    for drawing in drawings:
        try:
            rect = drawing.get("rect")
            if rect is None:
                continue
            bbox = BBox(float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))
        except Exception:
            continue
        if bbox.width < 12 or bbox.height < 8:
            continue
        if bbox.area > page.rect.width * page.rect.height * 0.8:
            continue
        if any(bbox.intersects(box) for box in table_boxes):
            continue
        boxes.append(bbox)
    clusters: list[BBox] = []
    for bbox in sorted(boxes, key=lambda b: (b.y0, b.x0)):
        for index, cluster in enumerate(clusters):
            if cluster.expand(12).intersects(bbox):
                clusters[index] = cluster.union(bbox)
                break
        else:
            clusters.append(bbox)
    # 迭代合并一次，处理链式相邻
    merged: list[BBox] = []
    for bbox in sorted(clusters, key=lambda b: b.area, reverse=True):
        for index, existing in enumerate(merged):
            if existing.expand(6).intersects(bbox):
                merged[index] = existing.union(bbox)
                break
        else:
            merged.append(bbox)
    return [b for b in merged if b.width > 70 and b.height > _MIN_FIGURE_HEIGHT and b.area > 3500]


def extract_equations(blocks: Sequence[TextBlock]) -> list[EquationData]:
    equations: list[EquationData] = []
    for block in blocks:
        if block.kind != "equation":
            continue
        number_match = re.search(r"\((\d{1,3})\)\s*$", block.text)
        number = number_match.group(1) if number_match else ""
        plain = re.sub(r"\((\d{1,3})\)\s*$", "", block.text).strip()
        if len(plain) < 4:
            continue
        equations.append(
            EquationData(
                id=f"eq-{block.id}",
                page=block.page,
                bbox=block.bbox,
                plain=plain,
                latex=to_latex(plain, number),
                number=number,
                section=block.sid,
            )
        )
    return equations


_LATEX_MAP = {
    "α": r"\alpha", "β": r"\beta", "γ": r"\gamma", "δ": r"\delta", "ε": r"\epsilon", "θ": r"\theta",
    "λ": r"\lambda", "μ": r"\mu", "π": r"\pi", "ρ": r"\rho", "σ": r"\sigma", "τ": r"\tau", "φ": r"\phi",
    "ψ": r"\psi", "ω": r"\omega", "Δ": r"\Delta", "Σ": r"\Sigma", "Ω": r"\Omega",
    "∑": r"\sum", "∏": r"\prod", "∫": r"\int", "√": r"\sqrt", "∞": r"\infty",
    "≈": r"\approx", "≤": r"\leq", "≥": r"\geq", "≠": r"\neq", "∈": r"\in", "∂": r"\partial",
    "∇": r"\nabla", "→": r"\rightarrow", "←": r"\leftarrow", "↔": r"\leftrightarrow",
    "×": r"\times", "÷": r"\div", "±": r"\pm", "−": "-",
}


def to_latex(plain: str, number: str = "") -> str:
    """把 PDF 中的 unicode 数学转成基础 LaTeX（尽力而为，保留结构）。"""
    text = plain
    for char, latex in _LATEX_MAP.items():
        text = text.replace(char, latex)
    text = re.sub(r"(?<![\\\w])([A-Za-z])\s*_\s*\{?([A-Za-z0-9]+)\}?", r"\1_{\2}", text)
    text = re.sub(r"(?<![\\\w])([A-Za-z])\s*\^\s*\{?([A-Za-z0-9+\-]+)\}?", r"\1^{\2}", text)
    text = re.sub(r"\s+", " ", text).strip()
    suffix = f" \\tag{{{number}}}" if number else ""
    return f"${text}{suffix}$"


def mark_table_blocks(blocks: list[TextBlock], tables: Sequence[TableData]) -> int:
    """把落在表格区域内的文本块标记为 table_text，避免正文分块重复。"""
    marked = 0
    for block in blocks:
        if block.kind not in {"paragraph", "caption", "heading"}:
            continue
        for table in tables:
            if block.page != table.page:
                continue
            if table.bbox.contains(block.bbox) or _overlap_ratio(table.bbox, block.bbox) > 0.85:
                block.kind = "table_text"
                marked += 1
                break
    return marked


def asset_digest(figures: Sequence[FigureData]) -> str:
    return sha1_text("|".join(f"{f.id}:{f.image_path}:{f.bbox.to_list()}" for f in figures))
