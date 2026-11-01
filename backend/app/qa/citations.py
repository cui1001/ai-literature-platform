"""引用锚点：把答案里的 [E3] 标记映射回原文的页码、包围盒与章节。

每一处引用都包含足够的信息让前端「点一下跳到 PDF 对应位置」：
``paper_id + page + bbox``，不依赖任何运行时状态，因此可被截图/评测脚本复现。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from ..indexing.retriever import RetrievedChunk

MARKER_RE = re.compile(r"\[(E\d{1,3})\]")


@dataclass
class Citation:
    evidence_id: str
    chunk_id: str
    paper_id: str
    paper_title: str
    page: int
    bbox: list[float]
    section: str
    section_path: str
    kind: str
    quote: str
    text: str = ""
    score: float = 0.0
    cited: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "chunk_id": self.chunk_id,
            "paper_id": self.paper_id,
            "paper_title": self.paper_title,
            "page": self.page,
            "bbox": self.bbox,
            "section": self.section,
            "section_path": self.section_path,
            "kind": self.kind,
            "quote": self.quote,
            "text": self.text,
            "score": round(self.score, 5),
            "cited": self.cited,
            "anchor": {"paper_id": self.paper_id, "page": self.page, "bbox": self.bbox},
        }

    def label(self) -> str:
        title = self.paper_title or self.paper_id
        section = f" · {self.section}" if self.section else ""
        return f"{title}{section} · p{self.page}"


def build_citations(chunks: Sequence[RetrievedChunk], *, quote_chars: int = 240) -> list[Citation]:
    citations: list[Citation] = []
    for index, chunk in enumerate(chunks, start=1):
        text = chunk.text or ""
        quote = text if len(text) <= quote_chars else text[:quote_chars].rstrip() + "…"
        citations.append(
            Citation(
                evidence_id=f"E{index}",
                chunk_id=chunk.chunk_id,
                paper_id=chunk.paper_id,
                paper_title=chunk.paper_title,
                page=chunk.page,
                bbox=list(chunk.bbox),
                section=chunk.section,
                section_path=chunk.section_path,
                kind=chunk.kind,
                quote=quote,
                text=text,
                score=float(chunk.scores.get("fused", 0.0)),
            )
        )
    return citations


def evidence_payload(citations: Sequence[Citation]) -> list[dict[str, Any]]:
    """供 LLM/离线引擎使用的证据结构（字段名与 providers.offline 对齐）。"""
    return [
        {
            "id": citation.evidence_id,
            "chunk_id": citation.chunk_id,
            "text": citation.text or citation.quote,
            "page": citation.page,
            "paper_id": citation.paper_id,
            "paper_title": citation.paper_title,
            "section": citation.section,
            "kind": citation.kind,
        }
        for citation in citations
    ]


def parse_markers(text: str) -> list[str]:
    return list(dict.fromkeys(MARKER_RE.findall(text or "")))


def link_citations(text: str, citations: Sequence[Citation]) -> list[Citation]:
    """标记答案中被真正引用的证据，并保持原文顺序。"""
    wanted = set(parse_markers(text))
    linked: list[Citation] = []
    for citation in citations:
        if citation.evidence_id in wanted:
            citation.cited = True
            linked.append(citation)
    return linked


def cite_enough(citations: Sequence[Citation], minimum: int = 1) -> bool:
    return sum(1 for citation in citations if citation.cited) >= minimum


def strip_markers(text: str) -> str:
    return MARKER_RE.sub("", text or "").strip()


def unique_sources(citations: Iterable[Citation]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for citation in citations:
        seen.setdefault(
            citation.paper_id,
            {
                "paper_id": citation.paper_id,
                "title": citation.paper_title,
                "pages": [],
            },
        )
        pages = seen[citation.paper_id]["pages"]
        if citation.page not in pages:
            pages.append(citation.page)
    return list(seen.values())
