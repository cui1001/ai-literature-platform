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


def extract_tables(page: Any, page_no: int, blocks: Sequence[TextBlock]) -> list[TableData]:
    tables: list[TableData] = []
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
        width = max(len(row) for row in rows)
        rows = [row + [""] * (width - len(row)) for row in rows]
        bbox = BBox.from_list(table.bbox)
        caption, caption_block = _find_caption(blocks, bbox, page_no, "table")
        number_match = _CAPTION_RE.match(caption) if caption else None
        number = number_match.group(2) if number_match else str(index)
        if not caption:
            # 尝试用表头上方紧邻的文本行做说明
            above = _nearest_text(blocks, bbox, prefer="above", max_gap=28)
            if above and len(above.text) < 200:
                caption = above.text
        header = rows[0]
        body_rows = rows[1:]
        tables.append(
            TableData(
                id=f"t{page_no}-{index}",
                page=page_no,
                bbox=bbox,
                number=number,
                caption=caption,
                header=header,
                rows=body_rows,
                markdown=table_to_markdown(header, body_rows),
                caption_block=caption_block,
            )
        )
    return tables


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
