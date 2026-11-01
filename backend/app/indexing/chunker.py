"""分块：把结构化文档切成可检索单元，并保留页码/包围盒/章节锚点。

分块策略（与解析结果共用同一份 block 坐标，保证「答案 → 原文位置」可回溯）：
- 正文按章节内连续段落累积到约 180 token，句末切分，带 1 句重叠；
- 表格整表一个块（保留 markdown 与 caption）；
- 图注、公式、参考文献各成一个块，便于「图 3 说明了什么」「[11] 是哪篇」这类问题命中。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core.hashing import chunk_id
from ..core.textutil import sentence_split, tokens
from ..parsing.model import BBox, ParsedPaper

TARGET_TOKENS = 180
MAX_TOKENS = 260
OVERLAP_SENTENCES = 1


@dataclass
class Chunk:
    chunk_id: str
    paper_id: str
    sid: str
    page: int
    bbox: BBox
    text: str
    order_idx: int
    token_len: int
    kind: str = "text"  # text | table | figure | equation | reference
    section: str = ""
    section_path: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "paper_id": self.paper_id,
            "sid": self.sid,
            "page": self.page,
            "x0": self.bbox.x0,
            "y0": self.bbox.y0,
            "x1": self.bbox.x1,
            "y1": self.bbox.y1,
            "text": self.text,
            "order_idx": self.order_idx,
            "token_len": self.token_len,
            "kind": self.kind,
        }

    def locate(self) -> dict[str, Any]:
        return {
            "page": self.page,
            "bbox": self.bbox.to_list(),
            "sid": self.sid,
            "section": self.section,
            "kind": self.kind,
        }


def _flush(buffer: list[Any], paper: ParsedPaper, section: Any, order: int) -> Chunk | None:
    if not buffer:
        return None
    text = " ".join(block.text for block in buffer).strip()
    if not text:
        return None
    bbox = buffer[0].bbox
    for block in buffer[1:]:
        bbox = bbox.union(block.bbox)
    sid = section.sid if section is not None else ""
    return Chunk(
        chunk_id=chunk_id(paper.paper_id, buffer[0].page, order, text),
        paper_id=paper.paper_id,
        sid=sid,
        page=buffer[0].page,
        bbox=bbox,
        text=text,
        order_idx=order,
        token_len=len(tokens(text)),
        kind="text",
        section=section.title if section is not None else "",
        section_path=section.path if section is not None else "",
        meta={"block_ids": [b.id for b in buffer]},
    )


def chunk_paper(paper: ParsedPaper) -> list[Chunk]:
    sections = {s.sid: s for s in paper.sections}
    chunks: list[Chunk] = []
    order = 0
    buffer: list[Any] = []
    current_sid = ""
    current_section = None

    def flush() -> None:
        nonlocal order, buffer
        chunk = _flush(buffer, paper, current_section, order)
        if chunk is not None:
            chunks.append(chunk)
            order += 1
        buffer = []

    for block in paper.blocks:
        if block.kind in {"header", "footer", "image", "table_text", "caption"}:
            continue
        if block.kind == "heading":
            continue
        if block.sid != current_sid:
            flush()
            current_sid = block.sid
            current_section = sections.get(block.sid)
        if block.kind in {"paragraph", "abstract"}:
            if not block.text.strip():
                continue
            buffer.append(block)
            if sum(len(tokens(b.text)) for b in buffer) >= TARGET_TOKENS:
                text_so_far = " ".join(b.text for b in buffer)
                if len(tokens(text_so_far)) >= MAX_TOKENS:
                    sentences = sentence_split(text_so_far)
                    if len(sentences) > 2 * OVERLAP_SENTENCES:
                        keep = sentences[-OVERLAP_SENTENCES:]
                        flush()
                        buffer = []
                        replay = _synthetic_blocks(" ".join(keep), buffer or [block], paper.paper_id)
                        buffer.extend(replay)
                    else:
                        flush()
                else:
                    flush()
            continue

    flush()

    # 表格 / 图注 / 公式 / 参考文献：各自独立成块
    for table in paper.tables:
        text = " ".join(part for part in [table.caption, table.markdown] if part).strip()
        if not text:
            continue
        chunks.append(
            Chunk(
                chunk_id=chunk_id(paper.paper_id, table.page, order, text),
                paper_id=paper.paper_id,
                sid=_sid_at_page(paper, table.page),
                page=table.page,
                bbox=table.bbox,
                text=text,
                order_idx=order,
                token_len=len(tokens(text)),
                kind="table",
                section=_section_title_at_page(paper, table.page),
                meta={"table_number": table.number, "caption": table.caption},
            )
        )
        order += 1
    for figure in paper.figures:
        text = figure.caption.strip()
        if len(text) < 8:
            continue
        chunks.append(
            Chunk(
                chunk_id=chunk_id(paper.paper_id, figure.page, order, text),
                paper_id=paper.paper_id,
                sid=_sid_at_page(paper, figure.page),
                page=figure.page,
                bbox=figure.bbox,
                text=text,
                order_idx=order,
                token_len=len(tokens(text)),
                kind="figure",
                section=_section_title_at_page(paper, figure.page),
                meta={"figure_number": figure.number, "image_path": figure.image_path},
            )
        )
        order += 1
    for equation in paper.equations:
        text = f"公式 ({equation.number}): {equation.plain}" if equation.number else f"公式: {equation.plain}"
        chunks.append(
            Chunk(
                chunk_id=chunk_id(paper.paper_id, equation.page, order, text),
                paper_id=paper.paper_id,
                sid=equation.section,
                page=equation.page,
                bbox=equation.bbox,
                text=text,
                order_idx=order,
                token_len=len(tokens(text)),
                kind="equation",
                section=_section_title_at_page(paper, equation.page),
                meta={"latex": equation.latex, "number": equation.number},
            )
        )
        order += 1
    for ref in paper.references:
        if len(ref.raw) < 8:
            continue
        text = f"参考文献 [{ref.idx}]: {ref.raw}"
        chunks.append(
            Chunk(
                chunk_id=chunk_id(paper.paper_id, ref.page or 1, order, text),
                paper_id=paper.paper_id,
                sid="",
                page=ref.page or 1,
                bbox=BBox(0, 0, 0, 0),
                text=text,
                order_idx=order,
                token_len=len(tokens(text)),
                kind="reference",
                section="References",
                meta={"ref_idx": ref.idx, "block_id": ref.block_id},
            )
        )
        order += 1
    return chunks


def _synthetic_blocks(text: str, fallback: list[Any], paper_id: str) -> list[Any]:
    from ..parsing.model import TextBlock

    source = fallback[-1] if fallback else None
    if source is None:
        return []
    return [
        TextBlock(
            id=f"{source.id}-ov",
            page=source.page,
            kind="paragraph",
            text=text,
            bbox=source.bbox,
            sid=source.sid,
            column=source.column,
        )
    ]


def _sid_at_page(paper: ParsedPaper, page: int) -> str:
    for section in reversed(paper.sections):
        if section.page <= page <= (section.end_page or section.page):
            return section.sid
    return ""


def _section_title_at_page(paper: ParsedPaper, page: int) -> str:
    for section in reversed(paper.sections):
        if section.page <= page <= (section.end_page or section.page):
            return section.title
    return ""


def chunk_stats(chunks: list[Chunk]) -> dict[str, Any]:
    kinds: dict[str, int] = {}
    for chunk in chunks:
        kinds[chunk.kind] = kinds.get(chunk.kind, 0) + 1
    total = len(chunks)
    avg = round(sum(c.token_len for c in chunks) / total, 1) if total else 0.0
    return {"chunks": total, "avg_tokens": avg, "kinds": kinds}
