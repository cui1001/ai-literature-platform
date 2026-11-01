"""版式数据组装：把 ``ParsedPaper`` 转成前端阅读器可直接消费的 JSON。

阅读器的目标是「原文可信、定位可查」：

* 每个文本块都带页内包围盒（PDF 点坐标）与页码，前端把它换算成百分比覆盖层；
* 标题树来自解析阶段的章节层级，最多三级（竞赛要求一/二/三级标题树）；
* 引用跳转所需的双向索引见 :mod:`app.reading.links`。

这里不做任何渲染，只做纯函数式的数据整形，保证同一份 PDF 每次输出的 JSON 完全一致。
"""

from __future__ import annotations

from typing import Any, Iterable

from ..parsing.model import (
    EquationData,
    FigureData,
    ParsedPaper,
    RefEntry,
    Section,
    TableData,
    TextBlock,
)
from .links import citation_numbers

#: 阅读器需要渲染的块类型（image 由专门图层绘制）
VISIBLE_KINDS = {
    "heading",
    "paragraph",
    "caption",
    "reference",
    "equation",
    "table_text",
    "header",
    "footer",
    "image",
}


def bbox_list(block: Any) -> list[float]:
    box = getattr(block, "bbox", None)
    if box is None:
        return [0.0, 0.0, 0.0, 0.0]
    return [round(float(box.x0), 2), round(float(box.y0), 2), round(float(box.x1), 2), round(float(box.y1), 2)]


def block_json(block: TextBlock) -> dict[str, Any]:
    marks = citation_numbers(block.text) if block.kind in {"paragraph", "heading", "caption", "table_text"} else []
    return {
        "id": block.id,
        "page": block.page,
        "kind": block.kind,
        "text": block.text,
        "bbox": bbox_list(block),
        "order": block.order,
        "column": block.column,
        "level": block.level,
        "number": block.number,
        "sid": block.sid,
        "font_size": round(float(block.font_size or 0), 2),
        "bold": bool(block.bold),
        "italic": bool(block.italic),
        "centered": bool(block.centered),
        "ocr": bool(block.ocr),
        "citations": marks,
    }


def section_json(section: Section) -> dict[str, Any]:
    return {
        "sid": section.sid,
        "number": section.number,
        "title": section.title,
        "level": section.level,
        "path": section.path,
        "page": section.page,
        "end_page": section.end_page,
        "appendix": bool(section.appendix),
        "block_ids": list(section.block_ids),
        "anchor": f"sec-{section.sid}",
    }


def table_json(table: TableData) -> dict[str, Any]:
    return {
        "id": table.id,
        "page": table.page,
        "number": table.number,
        "caption": table.caption,
        "header": list(table.header),
        "rows": [list(row) for row in table.rows],
        "markdown": table.markdown,
        "bbox": bbox_list(table),
        "caption_block": table.caption_block,
        "anchor": f"table-{table.id}",
    }


def figure_json(figure: FigureData) -> dict[str, Any]:
    return {
        "id": figure.id,
        "page": figure.page,
        "number": figure.number,
        "caption": figure.caption,
        "kind": figure.kind,
        "image_path": figure.image_path,
        "bbox": bbox_list(figure),
        "caption_block": figure.caption_block,
        "width": figure.width,
        "height": figure.height,
        "anchor": f"figure-{figure.id}",
    }


def equation_json(equation: EquationData) -> dict[str, Any]:
    return {
        "id": equation.id,
        "page": equation.page,
        "number": equation.number,
        "plain": equation.plain,
        "latex": equation.latex,
        "section": equation.section,
        "bbox": bbox_list(equation),
        "anchor": f"eq-{equation.id}",
    }


def reference_json(ref: RefEntry, backlinks: dict[int, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    items = (backlinks or {}).get(ref.idx, [])
    return {
        "idx": ref.idx,
        "raw": ref.raw,
        "title": ref.title,
        "authors": list(ref.authors),
        "year": ref.year,
        "venue": ref.venue,
        "doi": ref.doi,
        "arxiv_id": ref.arxiv_id,
        "url": ref.url,
        "page": ref.page,
        "block_id": ref.block_id,
        "anchor": f"ref-{ref.idx}",
        "cited_by": items,
        "cited_count": len(items),
    }


def heading_tree(paper: ParsedPaper, *, max_level: int = 3, include_appendix: bool = True) -> list[dict[str, Any]]:
    """一/二/三级标题树（层级栈构建，容忍跳级）。"""
    roots: list[dict[str, Any]] = []
    stack: list[tuple[int, dict[str, Any]]] = []
    for section in paper.sections:
        if section.level > max_level:
            continue
        if section.appendix and not include_appendix:
            continue
        node = {
            "sid": section.sid,
            "title": section.title,
            "number": section.number,
            "level": section.level,
            "page": section.page,
            "anchor": f"sec-{section.sid}",
            "children": [],
        }
        while stack and stack[-1][0] >= section.level:
            stack.pop()
        if stack:
            stack[-1][1]["children"].append(node)
        else:
            roots.append(node)
        stack.append((section.level, node))
    return roots


def pages_json(paper: ParsedPaper, *, image_prefix: str = "") -> list[dict[str, Any]]:
    by_page: dict[int, list[TextBlock]] = {}
    for block in paper.blocks:
        by_page.setdefault(block.page, []).append(block)
    pages: list[dict[str, Any]] = []
    for info in paper.pages:
        blocks = [block_json(b) for b in sorted(by_page.get(info.number, []), key=lambda b: (b.order, b.bbox.y0))]
        pages.append(
            {
                "number": info.number,
                "width": round(float(info.width), 2),
                "height": round(float(info.height), 2),
                "text_chars": info.text_chars,
                "columns": info.columns,
                "rotation": info.rotation,
                "ocr": bool(info.ocr),
                "ocr_engine": info.ocr_engine,
                "blank": bool(info.blank),
                "image": f"{image_prefix}{info.number}.png" if image_prefix else "",
                "blocks": blocks,
                "tables": [table_json(t) for t in paper.tables if t.page == info.number],
                "figures": [figure_json(f) for f in paper.figures if f.page == info.number],
                "equations": [equation_json(e) for e in paper.equations if e.page == info.number],
            }
        )
    return pages


def document_json(
    paper: ParsedPaper,
    *,
    image_prefix: str = "",
    translations: dict[str, dict[str, Any]] | None = None,
    backlinks: dict[int, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """完整的阅读器文档结构；``translations`` 以 block id 为键附加译文。"""
    maps = translations or {}
    pages = pages_json(paper, image_prefix=image_prefix)
    if maps:
        for page in pages:
            for block in page["blocks"]:
                entry = maps.get(block["id"])
                if entry:
                    block["translation"] = entry.get("text", "")
                    block["translation_provider"] = entry.get("provider", "")
    return {
        "paper": {
            "id": paper.paper_id,
            "sha256": paper.sha256,
            "filename": paper.filename,
            "title": paper.title,
            "authors": list(paper.authors),
            "affiliations": list(paper.affiliations),
            "abstract": paper.abstract,
            "keywords": list(paper.keywords),
            "arxiv_id": paper.arxiv_id,
            "arxiv_version": paper.arxiv_version,
            "venue": paper.venue,
            "year": paper.year,
            "doi": paper.doi,
            "pages": len(paper.pages),
            "layout": paper.layout,
            "scanned": bool(paper.scanned),
            "ocr_used": bool(paper.ocr_used),
            "ocr_engine": paper.ocr_engine,
            "quality": paper.quality.to_dict() if hasattr(paper.quality, "to_dict") else {},
            "parser_version": paper.parser_version,
        },
        "pages": pages,
        "sections": [section_json(s) for s in paper.sections],
        "heading_tree": heading_tree(paper),
        "references": [reference_json(r, backlinks) for r in paper.references],
        "tables": [table_json(t) for t in paper.tables],
        "figures": [figure_json(f) for f in paper.figures],
        "equations": [equation_json(e) for e in paper.equations],
        "stats": _stats(paper),
    }


def _stats(paper: ParsedPaper) -> dict[str, int]:
    kinds: dict[str, int] = {}
    for block in paper.blocks:
        kinds[block.kind] = kinds.get(block.kind, 0) + 1
    return {
        "blocks": len(paper.blocks),
        "sections": len(paper.sections),
        "tables": len(paper.tables),
        "figures": len(paper.figures),
        "equations": len(paper.equations),
        "references": len(paper.references),
        "ocr_pages": sum(1 for p in paper.pages if p.ocr),
        "kinds": sum(kinds.values()),
    }


def visible_blocks(paper: ParsedPaper, page: int | None = None) -> Iterable[TextBlock]:
    for block in paper.blocks:
        if block.kind not in VISIBLE_KINDS:
            continue
        if page is not None and block.page != page:
            continue
        yield block
