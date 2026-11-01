"""正文引用标记 ↔ References 条目的双向索引。

竞赛要求「正文 ``[11]`` 与 References 条目之间双向无损跳转」，这里的「无损」指：

1. 每个正文标记都能定位到**具体的块 + 页码 + 包围盒**，而不是只给一个编号；
2. 每条参考文献都能反查**所有**引用它的位置，点击可逐条跳回；
3. 编号在正文出现但 References 里缺失（或反之）时，显式标注 ``resolved=False``，
   交由前端展示为「未匹配」，而不是静默丢弃。

解析阶段已经把 References 拆成 ``RefEntry`` 列表，本模块只负责建立索引。
"""

from __future__ import annotations

from typing import Any, Iterator

from ..core.textutil import CITATION_MARK_RE, split_citation_numbers
from ..parsing.model import ParsedPaper, TextBlock

#: 可能携带引用标记的块类型（表格正文、图注也可能引用文献）
BODY_KINDS = {"paragraph", "heading", "caption", "table_text"}

CONTEXT_CHARS = 72


def citation_numbers(text: str) -> list[int]:
    """块内出现的引用编号（去重、升序）。"""
    from ..core.textutil import extract_citations

    return extract_citations(text)


def _context(text: str, start: int, end: int) -> str:
    left = max(0, start - CONTEXT_CHARS)
    right = min(len(text), end + CONTEXT_CHARS)
    snippet = text[left:right].replace("\n", " ").strip()
    prefix = "…" if left > 0 else ""
    suffix = "…" if right < len(text) else ""
    return f"{prefix}{snippet}{suffix}"


def citation_occurrences(paper: ParsedPaper) -> list[dict[str, Any]]:
    """遍历正文，返回每一次引用标记的出现位置（按阅读顺序）。"""
    known = {ref.idx for ref in paper.references}
    occurrences: list[dict[str, Any]] = []
    for block in paper.blocks:
        if block.kind not in BODY_KINDS or not block.text:
            continue
        for match in CITATION_MARK_RE.finditer(block.text):
            inner = match.group(1) or ""
            if not inner:
                # \cite{key} 形式暂不展开：References 尚未建立 key→编号映射
                continue
            for number in split_citation_numbers(inner):
                occurrences.append(
                    {
                        "number": number,
                        "marker": match.group(0),
                        "block_id": block.id,
                        "page": block.page,
                        "sid": block.sid,
                        "bbox": [round(float(v), 2) for v in (block.bbox.x0, block.bbox.y0, block.bbox.x1, block.bbox.y1)],
                        "quote": _context(block.text, match.start(), match.end()),
                        "resolved": number in known,
                        "anchor": f"ref-{number}",
                        "back_anchor": f"cite-{block.id}",
                    }
                )
    return occurrences


def backlinks(paper: ParsedPaper, occurrences: list[dict[str, Any]] | None = None) -> dict[int, list[dict[str, Any]]]:
    """参考文献编号 → 引用它的位置列表（去重同一块内的重复标记）。"""
    items = occurrences if occurrences is not None else citation_occurrences(paper)
    grouped: dict[int, list[dict[str, Any]]] = {}
    seen: set[tuple[int, str]] = set()
    for item in items:
        key = (item["number"], item["block_id"])
        if key in seen:
            continue
        seen.add(key)
        grouped.setdefault(int(item["number"]), []).append(
            {
                "block_id": item["block_id"],
                "page": item["page"],
                "sid": item["sid"],
                "bbox": item["bbox"],
                "quote": item["quote"],
                "marker": item["marker"],
                "anchor": item["back_anchor"],
            }
        )
    for value in grouped.values():
        value.sort(key=lambda entry: (entry["page"], entry["bbox"][1], entry["bbox"][0]))
    return grouped


def reference_lookup(paper: ParsedPaper) -> dict[int, dict[str, Any]]:
    return {
        ref.idx: {
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
        }
        for ref in paper.references
    }


def link_map(paper: ParsedPaper) -> dict[str, Any]:
    """前端一次拉取即可完成双向跳转的完整索引。"""
    occurrences = citation_occurrences(paper)
    grouped = backlinks(paper, occurrences)
    refs = reference_lookup(paper)
    for number, ref in refs.items():
        cites = grouped.get(number, [])
        ref["cited_by"] = cites
        ref["cited_count"] = len(cites)
    unresolved = sorted({item["number"] for item in occurrences if not item["resolved"]})
    uncited = sorted(number for number in refs if number not in grouped)
    return {
        "references": list(refs.values()),
        "occurrences": occurrences,
        "unresolved_citations": unresolved,
        "uncited_references": uncited,
        "stats": {
            "citations": len(occurrences),
            "references": len(refs),
            "linked": sum(1 for item in occurrences if item["resolved"]),
            "blocks": len({item["block_id"] for item in occurrences}),
        },
    }


def blocks_with_citations(paper: ParsedPaper) -> Iterator[TextBlock]:
    for block in paper.blocks:
        if block.kind in BODY_KINDS and citation_numbers(block.text):
            yield block
