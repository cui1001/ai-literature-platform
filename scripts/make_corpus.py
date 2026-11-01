"""生成确定性合成论文语料：PDF + arXiv 离线镜像。

用法：
    python scripts/make_corpus.py
    python scripts/make_corpus.py --out fixtures/papers
    python scripts/make_corpus.py --only transformer_v1,bert
    python scripts/make_corpus.py --mirror fixtures/arxiv/mirror.json

约定：
    * 真值来源是 ``fixtures/corpus/papers.json``，本脚本只负责排版与派生，不发明内容；
    * 输出 PDF 与镜像均无随机性：固定元数据、固定 ``/ID``、按稳定顺序写入，
      同一份 papers.json 重复运行得到的文件字节一致；
    * 所有文字使用 PDF 内置 Helvetica 字体（helv/hebo/heit/hebi），
      因此正文仅使用 Latin-1 可编码字符，排版前会做一次确定性替换。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import zlib
from pathlib import Path
from typing import Any, Iterable, Sequence

import fitz

# --------------------------------------------------------------------------- 版面常量

PAGE_W, PAGE_H = 595.0, 842.0
MARGIN = 56.0
GUTTER = 30.0
COL_W = 226.0
BODY_W_SINGLE = PAGE_W - 2 * MARGIN  # 483.0
BODY_SIZE = 9.5
LEADING = 13.0
PARA_GAP = 5.0
BODY_TOP = MARGIN
BODY_BOTTOM = PAGE_H - MARGIN
HEADER_BASELINE = 40.0
FOOTER_BASELINE = 812.0

INK = (0.0, 0.0, 0.0)
GRAY = (0.45, 0.45, 0.45)
LIGHT = (0.78, 0.78, 0.78)
FRAME = (0.72, 0.72, 0.72)

HEADING_SIZE = {1: 12.0, 2: 10.5, 3: 10.0}
HEADING_FONT = {1: "hebo", 2: "hebo", 3: "hebi"}

FIXED_DATE = "D:20260101000000Z"
PRODUCER = "agentlit-corpus/1"
CREATOR = "agentlit-make-corpus"

# 内置 Helvetica 只支持 Latin-1；把常见排版字符确定性降级为等义 ASCII。
_CHAR_MAP = {
    "\u2014": "-",
    "\u2013": "-",
    "\u2212": "-",
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2026": "...",
    "\u00a0": " ",
    "\u2022": "-",
    "\u2248": "~",
    "\u2264": "<=",
    "\u2265": ">=",
    "\u2192": "->",
    "\u2190": "<-",
    "\u03b1": "alpha",
    "\u03b2": "beta",
    "\u03b5": "epsilon",
    "\u03a3": "sum",
    "\u03bc": "mu",
    "\u03bb": "lambda",
    "\u03b8": "theta",
    "\u03c0": "pi",
}


def sanitize(text: Any) -> str:
    """把任意文本压成内置 Helvetica 可编码的字符串（确定性、无异常）。"""
    raw = "" if text is None else str(text)
    out = []
    for ch in raw:
        if ch in _CHAR_MAP:
            out.append(_CHAR_MAP[ch])
        elif ord(ch) < 256:
            out.append(ch)
        else:
            out.append("?")
    return "".join(out).replace("\r\n", "\n").replace("\r", "\n")


def text_width(text: str, font: str, size: float) -> float:
    return fitz.get_text_length(text, fontname=font, fontsize=size)


def wrap_text(text: str, font: str, size: float, width: float) -> list[str]:
    """按像素宽度折行；超长单词按字符硬切，保证不会溢出栏宽。"""
    if width <= 0:
        return [sanitize(text)]
    lines: list[str] = []
    for block in sanitize(text).split("\n"):
        words = block.split()
        if not words:
            continue
        current = ""
        for word in words:
            candidate = word if not current else current + " " + word
            if text_width(candidate, font, size) <= width:
                current = candidate
                continue
            if current:
                lines.append(current)
                current = ""
            while text_width(word, font, size) > width and len(word) > 1:
                lo, hi = 1, len(word)
                while lo < hi:
                    mid = (lo + hi + 1) // 2
                    if text_width(word[:mid], font, size) <= width:
                        lo = mid
                    else:
                        hi = mid - 1
                lines.append(word[:lo])
                word = word[lo:]
            current = word
        if current:
            lines.append(current)
    return lines or [""]


# --------------------------------------------------------------------------- 页面流


class Flow:
    """在固定栏宽上顺序排版，自动跨栏 / 跨页，绝不越过下边距。"""

    def __init__(self, doc: "fitz.Document", layout: str, short_title: str) -> None:
        self.doc = doc
        self.layout = layout
        self.short_title = sanitize(short_title)
        self.page: "fitz.Page" = None  # type: ignore[assignment]
        self.page_index = -1
        self.regions: list[tuple[float, float, float]] = []
        self.ri = 0
        self.y = 0.0
        self.at_top = True
        self.new_page()

    # -- 页面与栏 ---------------------------------------------------------
    def _regions(self, top: float) -> list[tuple[float, float, float]]:
        if self.layout == "double":
            return [
                (MARGIN, COL_W, top),
                (MARGIN + COL_W + GUTTER, COL_W, top),
            ]
        return [(MARGIN, BODY_W_SINGLE, top)]

    def new_page(self) -> None:
        self.page = self.doc.new_page(width=PAGE_W, height=PAGE_H)
        self.page_index += 1
        self.regions = self._regions(BODY_TOP)
        self.ri = 0
        self.y = BODY_TOP + BODY_SIZE
        self.at_top = True

    def finalize(self) -> None:
        """页眉页脚最后绘制，保证正文文本先出现（便于抽取与解析）。"""
        for index in range(self.doc.page_count):
            page = self.doc.load_page(index)
            number = index + 1
            if number % 2 == 1:
                page.insert_text(
                    (MARGIN, HEADER_BASELINE),
                    self.short_title,
                    fontname="helv",
                    fontsize=7.5,
                    color=GRAY,
                )
            label = str(number)
            width = text_width(label, "helv", 8.5)
            page.insert_text(
                ((PAGE_W - width) / 2.0, FOOTER_BASELINE),
                label,
                fontname="helv",
                fontsize=8.5,
                color=(0.3, 0.3, 0.3),
            )

    def _advance(self) -> None:
        if self.ri + 1 < len(self.regions):
            self.ri += 1
            self.y = self.regions[self.ri][2] + BODY_SIZE
        else:
            self.new_page()
        self.at_top = True

    def _ensure(self, height: float) -> None:
        if self.y + height > BODY_BOTTOM:
            self._advance()

    def set_full_width(self, top: float) -> None:
        self.regions = [(MARGIN, BODY_W_SINGLE, top)]
        self.ri = 0
        self.y = top + BODY_SIZE
        self.at_top = True

    def begin_body(self, top: float) -> None:
        if top > BODY_BOTTOM - 96.0:
            self.new_page()
            return
        self.regions = self._regions(top)
        self.ri = 0
        self.y = top + BODY_SIZE
        self.at_top = True

    # -- 基础绘制 ---------------------------------------------------------
    def _line(self, x: float, y: float, text: str, font: str, size: float, color: tuple = INK) -> None:
        self.page.insert_text((x, y), text, fontname=font, fontsize=size, color=color)

    def heading(self, number: str, title: str, level: int) -> None:
        size = HEADING_SIZE[level]
        font = HEADING_FONT[level]
        label = sanitize(f"{number} {title}".strip() if number else title)
        x, width, _ = self.regions[self.ri]
        lines = wrap_text(label, font, size, width)
        lead = size * 1.34
        before = {1: 9.0, 2: 6.5, 3: 4.5}[level]
        after = {1: 3.0, 2: 2.0, 3: 1.5}[level]
        self._ensure(before + lead * len(lines) + after + LEADING * 1.6)
        if not self.at_top:
            self.y += before
        x, width, _ = self.regions[self.ri]
        for line in lines:
            self._line(x, self.y, line, font, size)
            self.y += lead
        self.y += after
        self.at_top = False

    def paragraph(
        self,
        text: str,
        *,
        size: float = BODY_SIZE,
        leading: float = LEADING,
        font: str = "helv",
        gap: float = PARA_GAP,
        indent: float = 0.0,
        color: tuple = INK,
    ) -> None:
        x, width, _ = self.regions[self.ri]
        lines = wrap_text(text, font, size, width - indent)
        for index, line in enumerate(lines):
            if self.y + size * 0.24 > BODY_BOTTOM:
                self._advance()
            x, width, _ = self.regions[self.ri]
            self._line(x + (indent if index == 0 else 0.0), self.y, line, font, size, color)
            self.y += leading
            self.at_top = False
        self.y += gap

    def equation(self, plain: str, number: str) -> None:
        plain = sanitize(plain)
        label = sanitize(f"({number})")
        x, width, _ = self.regions[self.ri]
        size = BODY_SIZE
        while size > 7.0 and text_width(plain, "helv", size) > width - 48:
            size -= 0.5
        eq_w = text_width(plain, "helv", size)
        num_w = text_width(label, "helv", BODY_SIZE)
        self._ensure(5.0 + size * 1.2 + 6.0)
        x, width, _ = self.regions[self.ri]
        self.y += 5.0
        self._line(x + (width - eq_w) / 2.0, self.y, plain, "helv", size)
        self._line(x + width - num_w, self.y, label, "helv", BODY_SIZE)
        self.y += size * 1.2 + 6.0
        self.at_top = False

    def table(self, item: dict) -> None:
        caption = sanitize(f"Table {item['number']}: {item['caption']}")
        header = [sanitize(cell) for cell in item["header"]]
        rows = [[sanitize(cell) for cell in row] for row in item["rows"]]
        ncol = len(header)
        x, width, _ = self.regions[self.ri]
        cap_lines = wrap_text(caption, "helv", 8.5, width)
        natural: list[float] = []
        for col in range(ncol):
            widest = text_width(header[col], "hebo", 8.0)
            for row in rows:
                if col < len(row):
                    widest = max(widest, text_width(row[col], "helv", 8.0))
            natural.append(widest + 10.0)
        available = width - 2.0
        total = sum(natural) or 1.0
        if total > available:
            widths = [max(20.0, value * available / total) for value in natural]
            scale = available / sum(widths)
            widths = [value * scale for value in widths]
        else:
            extra = (available - total) / ncol
            widths = [value + extra for value in natural]

        def cell_lines(value: str, font: str, col: int) -> list[str]:
            return wrap_text(value, font, 8.0, widths[col] - 4.0)

        head_cells = [cell_lines(header[c], "hebo", c) for c in range(ncol)]
        body_cells = [[cell_lines(row[c] if c < len(row) else "", "helv", c) for c in range(ncol)] for row in rows]

        def row_height(cells: list[list[str]]) -> float:
            return max(1, max(len(cell) for cell in cells)) * 9.6 + 4.0

        head_h = row_height(head_cells)
        body_hs = [row_height(cells) for cells in body_cells]
        cap_h = len(cap_lines) * 10.4
        total_h = 4.0 + cap_h + 3.0 + head_h + sum(body_hs) + 3.0
        self._ensure(total_h)
        x, width, _ = self.regions[self.ri]
        y = self.y + 2.0
        for line in cap_lines:
            self._line(x, y + 8.5, line, "helv", 8.5)
            y += 10.4
        y += 3.0
        top = y

        def draw_cells(cells: list[list[str]], top_y: float, bold: bool) -> None:
            offset = 0.0
            for col, lines in enumerate(cells):
                for index, line in enumerate(lines):
                    self._line(x + offset + 2.0, top_y + 8.5 + index * 9.6, line, "hebo" if bold else "helv", 8.0)
                offset += widths[col]

        self.page.draw_line(fitz.Point(x, top), fitz.Point(x + width, top), color=INK, width=0.7)
        draw_cells(head_cells, top, True)
        cursor = top + head_h
        self.page.draw_line(fitz.Point(x, cursor), fitz.Point(x + width, cursor), color=INK, width=0.5)
        for cells, height in zip(body_cells, body_hs):
            draw_cells(cells, cursor, False)
            cursor += height
        self.page.draw_line(fitz.Point(x, cursor), fitz.Point(x + width, cursor), color=INK, width=0.7)
        self.y = cursor + 6.0
        self.at_top = False

    def figure(self, item: dict, seed: int) -> None:
        caption = sanitize(f"Figure {item['number']}: {item['caption']}")
        x, width, _ = self.regions[self.ri]
        cap_lines = wrap_text(caption, "helv", 8.5, width)
        box_h = 126.0 if self.layout == "double" else 150.0
        total = 6.0 + box_h + 4.0 + len(cap_lines) * 10.4 + 4.0
        self._ensure(total)
        x, width, _ = self.regions[self.ri]
        top = self.y + 4.0
        rect = fitz.Rect(x, top, x + width, top + box_h)
        self.page.draw_rect(rect, color=FRAME, width=0.6)
        draw_figure_body(self.page, rect, seed, int(item["number"]), str(item.get("caption", "")))
        y = rect.y1 + 4.0
        for line in cap_lines:
            self._line(x, y + 8.5, line, "helv", 8.5)
            y += 10.4
        self.y = y + 4.0
        self.at_top = False

    def reference(self, idx: int, raw: str) -> None:
        text = sanitize(f"[{idx}] {raw}")
        x, width, _ = self.regions[self.ri]
        lines = wrap_text(text, "helv", 8.0, width - 14.0)
        for index, line in enumerate(lines):
            if self.y + 2.0 > BODY_BOTTOM:
                self._advance()
            x, width, _ = self.regions[self.ri]
            self._line(x + (14.0 if index > 0 else 0.0), self.y, line, "helv", 8.0)
            self.y += 9.6
            self.at_top = False
        self.y += 1.5


# --------------------------------------------------------------------------- 图形示意


def draw_figure_body(
    page: "fitz.Page", rect: "fitz.Rect", seed: int, number: int, caption: str = ""
) -> None:
    """极简示意图形：矩形 / 箭头 / 文字，完全由 seed 与 caption 决定，无随机数。"""
    text = caption.lower()
    if "neuron" in text:
        kind = 4
    elif "subfield" in text or "taxonomy" in text:
        kind = 5
    elif "feed-forward" in text or "fully connected" in text:
        kind = 6
    elif "architecture" in text or "encoder" in text or "decoder" in text:
        kind = 0
    else:
        kind = (seed + number) % 4
    pad = 10.0
    box = fitz.Rect(rect.x0 + pad, rect.y0 + pad, rect.x1 - pad, rect.y1 - pad)
    label_size = 6.6

    def label(x: float, y: float, text: str, size: float = label_size, font: str = "helv") -> None:
        page.insert_text((x, y), sanitize(text), fontname=font, fontsize=size, color=INK)

    if kind == 0:
        # 编码器 / 解码器式模块图
        n = 4
        gap = 6.0
        h = (box.height - gap * (n - 1)) / n
        names = ["Input tokens", "Encoder stack", "Attention", "Output logits"]
        for i in range(n):
            cell = fitz.Rect(box.x0 + 8, box.y0 + i * (h + gap), box.x1 - 8, box.y0 + i * (h + gap) + h)
            page.draw_rect(cell, color=INK, width=0.6, fill=(0.94, 0.94, 0.94) if i % 2 == 0 else None)
            text_w = text_width(names[i], "helv", label_size)
            label(cell.x0 + (cell.width - text_w) / 2.0, cell.y0 + cell.height / 2.0 + 2.4, names[i])
            if i < n - 1:
                cx = cell.x0 + cell.width / 2.0
                page.draw_line(fitz.Point(cx, cell.y1), fitz.Point(cx, cell.y1 + gap), color=INK, width=0.6)
                page.draw_line(fitz.Point(cx, cell.y1 + gap), fitz.Point(cx - 2.0, cell.y1 + gap - 2.4), color=INK, width=0.6)
                page.draw_line(fitz.Point(cx, cell.y1 + gap), fitz.Point(cx + 2.0, cell.y1 + gap - 2.4), color=INK, width=0.6)
    elif kind == 1:
        # 柱状图
        axis_y = box.y1 - 12
        values = [35 + (seed * 17 + i * 29) % 55 for i in range(5)]
        names = ["A", "B", "C", "D", "E"]
        bw = box.width / (len(values) * 1.6)
        for i, value in enumerate(values):
            x0 = box.x0 + 10 + i * (bw * 1.6)
            height = (axis_y - box.y0 - 12) * value / 100.0
            cell = fitz.Rect(x0, axis_y - height, x0 + bw, axis_y)
            page.draw_rect(cell, color=INK, width=0.5, fill=(0.82, 0.82, 0.82))
            label(x0 + bw / 2.0 - 2.0, axis_y - height - 2.5, str(value), 6.0)
            label(x0 + bw / 2.0 - 2.0, axis_y + 7.0, names[i], 6.0)
        page.draw_line(fitz.Point(box.x0 + 6, axis_y), fitz.Point(box.x1 - 6, axis_y), color=INK, width=0.6)
        page.draw_line(fitz.Point(box.x0 + 6, axis_y), fitz.Point(box.x0 + 6, box.y0 + 6), color=INK, width=0.6)
    elif kind == 2:
        # 注意力热力网格
        grid = 6
        side = min(box.width, box.height) - 8
        cell_side = side / grid
        ox = box.x0 + (box.width - side) / 2.0
        oy = box.y0 + (box.height - side) / 2.0
        for row in range(grid):
            for col in range(grid):
                shade = 0.96 - 0.11 * ((seed + row * 7 + col * 13) % 8)
                page.draw_rect(
                    fitz.Rect(ox + col * cell_side, oy + row * cell_side, ox + (col + 1) * cell_side, oy + (row + 1) * cell_side),
                    color=(0.85, 0.85, 0.85),
                    width=0.3,
                    fill=(shade, shade, shade),
                )
        label(ox, oy + side + 8.0, "query", 6.0)
        label(ox + side - 14.0, oy + side + 8.0, "key", 6.0)
    elif kind == 4:
        # 单个神经元：输入 -> 加权求和 -> 非线性 -> 输出
        cy = (box.y0 + box.y1) / 2.0
        r = 7.0
        in_x = box.x0 + 18.0
        for i, name in enumerate(["x1", "x2", "x3"]):
            py = cy + (i - 1) * 26.0
            page.draw_circle(fitz.Point(in_x, py), r, color=INK, width=0.6)
            label(in_x - 5.0, py + 2.2, name, 5.8)
        mid = fitz.Rect(box.x0 + box.width * 0.38, cy - 26.0, box.x0 + box.width * 0.72, cy + 26.0)
        page.draw_rect(mid, color=INK, width=0.6, fill=(0.93, 0.93, 0.93))
        label(mid.x0 + 4.0, mid.y0 + 13.0, "weighted sum", 6.2)
        label(mid.x0 + 4.0, mid.y0 + 25.0, "+ nonlinearity", 6.2)
        out_x = box.x1 - 20.0
        page.draw_circle(fitz.Point(out_x, cy), r, color=INK, width=0.6)
        label(out_x - 8.0, cy + 2.2, "y", 6.0)
        for i in range(3):
            py = cy + (i - 1) * 26.0
            page.draw_line(fitz.Point(in_x + r, py), fitz.Point(mid.x0, py), color=INK, width=0.5)
            page.draw_line(fitz.Point(mid.x0, py), fitz.Point(mid.x0 - 2.4, py - 1.6), color=INK, width=0.5)
            page.draw_line(fitz.Point(mid.x0, py), fitz.Point(mid.x0 - 2.4, py + 1.6), color=INK, width=0.5)
        page.draw_line(fitz.Point(mid.x1, cy), fitz.Point(out_x - r, cy), color=INK, width=0.5)
        page.draw_line(fitz.Point(out_x - r, cy), fitz.Point(out_x - r - 2.4, cy - 1.6), color=INK, width=0.5)
        page.draw_line(fitz.Point(out_x - r, cy), fitz.Point(out_x - r - 2.4, cy + 1.6), color=INK, width=0.5)
    elif kind == 5:
        # 嵌套集合：AI > ML > DL
        levels = [("Artificial intelligence", 0.0), ("Machine learning", 13.0), ("Deep learning", 26.0)]
        for name, inset in levels:
            cell = fitz.Rect(box.x0 + inset, box.y0 + inset, box.x1 - inset, box.y1 - inset)
            page.draw_rect(cell, color=INK, width=0.6, fill=(0.96 - inset / 200.0,) * 3)
            label(cell.x0 + 5.0, cell.y0 + 9.0, name, 6.2)
    elif kind == 6:
        # 全连接网络：四层节点与连线
        layers = [2, 4, 4, 2]
        xs = [box.x0 + 18.0 + i * (box.width - 36.0) / (len(layers) - 1) for i in range(len(layers))]
        pos = []
        for li, count in enumerate(layers):
            span = box.height - 34.0
            step = span / max(count - 1, 1)
            ys = [box.y0 + 17.0 + j * step for j in range(count)]
            pos.append([fitz.Point(xs[li], y) for y in ys])
        for li in range(len(layers) - 1):
            for a in pos[li]:
                for b in pos[li + 1]:
                    page.draw_line(a, b, color=(0.62, 0.62, 0.62), width=0.3)
        for nodes in pos:
            for point in nodes:
                page.draw_circle(point, 3.6, color=INK, width=0.5, fill=(1, 1, 1))
        label(xs[0] - 8.0, box.y1 - 6.0, "input", 6.0)
        label(xs[-1] - 10.0, box.y1 - 6.0, "output", 6.0)
    else:
        # 折线图
        points = []
        count = 6
        for i in range(count):
            value = 20 + (seed * 13 + i * 23) % 70
            px = box.x0 + 8 + i * (box.width - 16) / (count - 1)
            py = box.y1 - 14 - (box.height - 26) * value / 100.0
            points.append(fitz.Point(px, py))
        for i in range(count - 1):
            page.draw_line(points[i], points[i + 1], color=INK, width=0.8)
        for point in points:
            page.draw_circle(point, 1.5, color=INK, fill=(0.2, 0.2, 0.2))
        page.draw_line(fitz.Point(box.x0 + 6, box.y1 - 14), fitz.Point(box.x1 - 6, box.y1 - 14), color=INK, width=0.6)
        page.draw_line(fitz.Point(box.x0 + 6, box.y1 - 14), fitz.Point(box.x0 + 6, box.y0 + 6), color=INK, width=0.6)
        label(box.x0 + 6, box.y1 - 4.0, "steps", 6.0)
        label(box.x0 + 6, box.y0 + 4.0, "score", 6.0)


# --------------------------------------------------------------------------- 论文排版


def version_label(paper: dict) -> str:
    versions = paper.get("versions") or []
    if versions:
        label = str(versions[0].get("label", "v1"))
        return label if label.startswith("v") else f"v{label}"
    return "v1"


def meta_line(paper: dict, label: str) -> str:
    arxiv_id = str(paper.get("arxiv_id") or "").strip()
    venue = sanitize(paper["venue"])
    year = paper["year"]
    if arxiv_id:
        return f"arXiv:{arxiv_id}{label}  |  {venue} {year}"
    return f"{venue} {year}"


def draw_front_matter(flow: Flow, paper: dict) -> float:
    page = flow.page
    cx = PAGE_W / 2.0
    width = BODY_W_SINGLE
    y = 96.0

    for line in wrap_text(paper["title"], "hebo", 18.0, width):
        line_w = text_width(line, "hebo", 18.0)
        page.insert_text((cx - line_w / 2.0, y), line, fontname="hebo", fontsize=18.0)
        y += 22.0

    y += 8.0
    authors = ", ".join(str(author) for author in paper["authors"])
    for line in wrap_text(authors, "helv", 10.0, width):
        line_w = text_width(line, "helv", 10.0)
        page.insert_text((cx - line_w / 2.0, y), line, fontname="helv", fontsize=10.0)
        y += 12.5

    y += 2.0
    affiliations = "; ".join(str(item) for item in paper["affiliations"])
    for line in wrap_text(affiliations, "heit", 9.0, width):
        line_w = text_width(line, "heit", 9.0)
        page.insert_text((cx - line_w / 2.0, y), line, fontname="heit", fontsize=9.0)
        y += 11.2

    y += 3.0
    meta = meta_line(paper, version_label(paper))
    meta_w = text_width(meta, "helv", 8.5)
    page.insert_text((cx - meta_w / 2.0, y), meta, fontname="helv", fontsize=8.5, color=(0.25, 0.25, 0.25))
    return y + 14.0


def _mentions(text: str, kind: str, number: str) -> bool:
    if kind == "equation":
        return bool(re.search(rf"(?:Equation|Eq\.)\s*\({re.escape(str(number))}\)", text))
    if kind == "table":
        return bool(re.search(rf"Table\s+{re.escape(str(number))}(?!\d)", text))
    return bool(re.search(rf"Figure\s+{re.escape(str(number))}(?!\d)", text))


def _flush(flow: Flow, paper: dict, equations: list[dict], tables: list[dict], figures: list[dict]) -> None:
    for item in equations:
        flow.equation(item["plain"], item["number"])
    for item in tables:
        flow.table(item)
    for item in figures:
        flow.figure(item, zlib.crc32(f"{paper['key']}|{item['number']}".encode("utf-8")))


def render_paper(paper: dict, out_dir: Path) -> dict:
    doc = fitz.open()
    doc.set_metadata(
        {
            "title": sanitize(paper["title"]),
            "author": sanitize(", ".join(paper["authors"])),
            "subject": "Synthetic research corpus (deterministic)",
            "keywords": sanitize(", ".join(paper["keywords"])),
            "creator": CREATOR,
            "producer": PRODUCER,
            "creationDate": FIXED_DATE,
            "modDate": FIXED_DATE,
        }
    )

    flow = Flow(doc, paper["layout"], paper["short_title"])
    y = draw_front_matter(flow, paper)
    flow.set_full_width(y)
    flow.heading("", "Abstract", 1)
    flow.paragraph(paper["abstract"])
    flow.paragraph(
        "Keywords: " + ", ".join(str(keyword) for keyword in paper["keywords"]),
        size=8.8,
        leading=11.5,
        font="heit",
        gap=9.0,
    )
    flow.begin_body(flow.y + 2.0)

    equations = list(paper.get("equations") or [])
    tables = list(paper.get("tables") or [])
    figures = list(paper.get("figures") or [])

    for section in paper["sections"]:
        flow.heading(section["number"], section["title"], int(section["level"]))
        for paragraph in section["paragraphs"]:
            flow.paragraph(paragraph)
        joined = " ".join(section["paragraphs"])
        used_eq = [item for item in equations if _mentions(joined, "equation", item["number"])]
        used_tb = [item for item in tables if _mentions(joined, "table", item["number"])]
        used_fg = [item for item in figures if _mentions(joined, "figure", item["number"])]
        if used_eq or used_tb or used_fg:
            _flush(flow, paper, used_eq, used_tb, used_fg)
            equations = [item for item in equations if item not in used_eq]
            tables = [item for item in tables if item not in used_tb]
            figures = [item for item in figures if item not in used_fg]

    if equations or tables or figures:
        _flush(flow, paper, equations, tables, figures)

    flow.heading("", "References", 1)
    for ref in paper["references"]:
        flow.reference(int(ref["idx"]), ref["raw"])

    for appendix in paper.get("appendix") or []:
        flow.heading(appendix["number"], appendix["title"], 1)
        for paragraph in appendix["paragraphs"]:
            flow.paragraph(paragraph, size=9.0, leading=12.2, gap=5.0)

    flow.finalize()

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{paper['key']}.pdf"
    if path.exists():
        path.unlink()
    doc.save(str(path), garbage=4, deflate=True, no_new_id=True)
    pages = doc.page_count
    chars = sum(len(page.get_text()) for page in doc)
    doc.close()

    scanned_path = None
    if paper.get("scanned"):
        scanned_dir = out_dir / "scanned"
        scanned_dir.mkdir(parents=True, exist_ok=True)
        scanned_path = scanned_dir / f"{paper['key']}.pdf"
        rasterize_scan(path, scanned_path, paper)

    return {
        "key": paper["key"],
        "path": path,
        "pages": pages,
        "chars": chars,
        "target": int(paper.get("pages_target") or 0),
        "scanned": scanned_path,
    }


def rasterize_scan(src: Path, dst: Path, paper: dict, dpi: int = 200) -> None:
    """把可检索 PDF 栅格化成纯图像 PDF（无文本层），用于 OCR 演示。"""
    source = fitz.open(str(src))
    out = fitz.open()
    out.set_metadata(
        {
            "title": sanitize(f"{paper['title']} (scanned)"),
            "author": sanitize(", ".join(paper["authors"])),
            "subject": "Scanned raster sample for OCR (200 DPI, no text layer)",
            "creator": CREATOR,
            "producer": PRODUCER,
            "creationDate": FIXED_DATE,
            "modDate": FIXED_DATE,
        }
    )
    for page in source:
        pixmap = page.get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False)
        png = pixmap.tobytes("png")
        target = out.new_page(width=page.rect.width, height=page.rect.height)
        target.insert_image(fitz.Rect(0, 0, page.rect.width, page.rect.height), stream=png)
    source.close()
    if dst.exists():
        dst.unlink()
    out.save(str(dst), garbage=4, deflate=True, no_new_id=True)
    out.close()


# --------------------------------------------------------------------------- 镜像派生


def _categories_for(paper: dict) -> list[str]:
    keywords = " ".join(str(item).lower() for item in paper["keywords"])
    if any(word in keywords for word in ("image", "vision", "convolution", "patch")):
        primary = "cs.CV"
    elif any(word in keywords for word in ("language model", "transformer", "retrieval", "reasoning")):
        primary = "cs.CL"
    else:
        primary = "cs.CL"
    secondary = "cs.LG" if primary == "cs.CL" else "cs.LG"
    return [primary, secondary]


def build_mirror(papers: Sequence[dict], layout_seed: int) -> dict:
    entries: list[dict] = []
    for paper in papers:
        arxiv_id = str(paper.get("arxiv_id") or "").strip()
        if not arxiv_id:
            continue
        categories = _categories_for(paper)
        for version in paper.get("versions") or [{"label": "v1"}]:
            label = str(version.get("label", "v1"))
            bare = str(version.get("arxiv_id") or arxiv_id).strip()
            full_id = bare if bare.endswith(label) else f"{bare}{label}"
            entries.append(
                {
                    "arxiv_id": full_id,
                    "title": paper["title"],
                    "authors": list(paper["authors"]),
                    "abstract": paper["abstract"],
                    "comments": f"Synthetic offline mirror entry for {paper['venue']} {paper['year']}.",
                    "journal_ref": "",
                    "categories": categories,
                    "published": f"{paper['year']}-06-01T00:00:00Z",
                    "updated": f"{paper['year']}-12-01T00:00:00Z",
                    "url": f"https://arxiv.org/abs/{full_id}",
                    "pdf_url": f"https://arxiv.org/pdf/{full_id}.pdf",
                    "github": "",
                    "primary_category": categories[0],
                    "source": "offline-mirror",
                }
            )
    return {
        "corpus_version": 1,
        "generated_by": "scripts/make_corpus.py",
        "layout_seed": int(layout_seed),
        "papers": entries,
    }


# --------------------------------------------------------------------------- CLI


_REQUIRED_PAPER_FIELDS = (
    "key",
    "title",
    "short_title",
    "authors",
    "affiliations",
    "year",
    "venue",
    "arxiv_id",
    "versions",
    "abstract",
    "keywords",
    "layout",
    "scanned",
    "pages_target",
    "sections",
    "equations",
    "tables",
    "figures",
    "references",
    "appendix",
    "facts",
    "qa",
)


def _native_path(raw: str) -> Path:
    """把 Git-Bash 风格的 /tmp 或 /c/... 转成 Windows 原生路径。"""
    text = raw.strip().strip('"')
    if os.name == "nt" and text.startswith("/"):
        drive = re.match(r"^/([A-Za-z])/(.*)$", text)
        if drive:
            return Path(f"{drive.group(1).upper()}:/{drive.group(2)}")
        if text == "/tmp":
            return Path(tempfile.gettempdir())
        if text.startswith("/tmp/"):
            return Path(tempfile.gettempdir()) / text[len("/tmp/") :]
    return Path(os.path.expanduser(text))


def load_corpus(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("papers"), list):
        raise SystemExit(f"invalid corpus file: {path}")
    seen: set[str] = set()
    for paper in payload["papers"]:
        missing = [field for field in _REQUIRED_PAPER_FIELDS if field not in paper]
        if missing:
            raise SystemExit(f"paper {paper.get('key')!r} missing fields: {', '.join(missing)}")
        if paper["key"] in seen:
            raise SystemExit(f"duplicate paper key: {paper['key']}")
        seen.add(paper["key"])
        if paper["layout"] not in {"single", "double"}:
            raise SystemExit(f"paper {paper['key']!r} has invalid layout: {paper['layout']}")
        for table in paper["tables"]:
            for row in table["rows"]:
                if len(row) != len(table["header"]):
                    raise SystemExit(f"paper {paper['key']!r} table {table['number']} row/header width mismatch")
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Generate the deterministic synthetic paper corpus.")
    parser.add_argument("--corpus", default=str(root / "fixtures" / "corpus" / "papers.json"), help="papers.json path")
    parser.add_argument("--out", default=str(root / "fixtures" / "papers"), help="PDF output directory")
    parser.add_argument("--only", default="", help="comma separated paper keys")
    parser.add_argument("--mirror", default=str(root / "fixtures" / "arxiv" / "mirror.json"), help="mirror.json output path")
    args = parser.parse_args(argv)

    corpus_path = _native_path(args.corpus)
    out_dir = _native_path(args.out)
    mirror_path = _native_path(args.mirror)
    payload = load_corpus(corpus_path)
    papers = list(payload["papers"])

    if args.only.strip():
        wanted = [item.strip() for item in re.split(r"[,\s]+", args.only.strip()) if item.strip()]
        known = {paper["key"] for paper in papers}
        unknown = [item for item in wanted if item not in known]
        if unknown:
            raise SystemExit(f"unknown --only keys: {', '.join(unknown)}")
        papers = [paper for paper in papers if paper["key"] in set(wanted)]

    results = []
    for paper in papers:
        stats = render_paper(paper, out_dir)
        results.append(stats)
        flag = ""
        if stats["target"] and stats["pages"] != stats["target"]:
            flag = f"  [warn] target {stats['target']}"
        print(f"[ok] {stats['key']:<16} {stats['pages']} pages  {stats['chars']:>6} chars  {stats['path']}{flag}")
        if stats["scanned"]:
            doc = fitz.open(str(stats["scanned"]))
            text_chars = sum(len(page.get_text()) for page in doc)
            images = sum(len(page.get_images()) for page in doc)
            doc.close()
            print(f"[ok] {stats['key']:<16} {stats['pages']} pages  scanned copy  text_chars={text_chars} images={images}  {stats['scanned']}")

    mirror = build_mirror(payload["papers"], int(payload.get("layout_seed", 0)))
    mirror_text = json.dumps(mirror, ensure_ascii=False, indent=2) + "\n"
    try:
        mirror_path.parent.mkdir(parents=True, exist_ok=True)
        mirror_path.write_text(mirror_text, encoding="utf-8")
    except OSError:
        # Read-only checkout (or sandboxed run): keep the generated PDFs usable by
        # dropping the mirror next to them instead of failing the whole run.
        mirror_path = out_dir / "mirror.json"
        mirror_path.parent.mkdir(parents=True, exist_ok=True)
        mirror_path.write_text(mirror_text, encoding="utf-8")
        print(f"[warn] mirror default path not writable; wrote {mirror_path}")
    print(f"[ok] mirror          {len(mirror['papers'])} entries  {mirror_path}")

    total_pages = sum(item["pages"] for item in results)
    print(f"[done] papers={len(results)} pdfs={len(results) + sum(1 for item in results if item['scanned'])} pages={total_pages}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
