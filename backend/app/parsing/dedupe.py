"""语义级去重与版本比较。

竞赛要求：上传 ``1706.03762v1.pdf`` 与 ``1706.03762v7.pdf`` 时，系统能识别「同一篇论文的不同版本」，
提示用户并建议保留较新版本。这里不依赖文件名，而是组合三条证据：

1. 内容哈希（完全相同 → 直接判定重复）；
2. arXiv 编号（同一 base id、不同版本号 → 判定为版本关系，比较版本号）；
3. 语义相似度（标题 + 摘要 + 正文 simhash/文本相似度 → 判定近重复）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..core.hashing import hamming64, simhash64, simhash_similarity
from ..core.textutil import normalize_title, text_similarity, text_similarity as _ts
from .model import ParsedPaper
from .pipeline import load_parsed

_VERSION_RE = re.compile(r"v(\d+)$", re.I)


def version_number(label: str) -> int:
    match = _VERSION_RE.search((label or "").strip())
    if match:
        return int(match.group(1))
    match = re.search(r"v(\d+)", label or "")
    return int(match.group(1)) if match else 1


def arxiv_base(arxiv_id: str) -> str:
    return re.sub(r"v\d+$", "", (arxiv_id or "").strip(), flags=re.I)


@dataclass
class DuplicateVerdict:
    is_duplicate: bool = False
    kind: str = "none"  # same-file | version | near-duplicate | none
    similarity: float = 0.0
    action: str = "keep-both"  # keep-existing | replace-existing | keep-both
    existing_paper_id: str = ""
    existing_title: str = ""
    existing_version: str = ""
    new_version: str = ""
    reason: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_duplicate": self.is_duplicate,
            "kind": self.kind,
            "similarity": round(self.similarity, 4),
            "action": self.action,
            "existing_paper_id": self.existing_paper_id,
            "existing_title": self.existing_title,
            "existing_version": self.existing_version,
            "new_version": self.new_version,
            "reason": self.reason,
            "evidence": self.evidence,
        }


def detect_duplicate(
    parsed: ParsedPaper,
    existing: Sequence[dict[str, Any]],
    *,
    text_sample: str = "",
    threshold: float = 0.9,
) -> DuplicateVerdict:
    """在已有文献库中寻找重复/版本关系。"""
    new_version = parsed.arxiv_version or (f"v{version_number(parsed.arxiv_id)}" if parsed.arxiv_id else "")
    base = arxiv_base(parsed.arxiv_id)
    new_hash = simhash64(text_sample or parsed.abstract or parsed.title)

    best: DuplicateVerdict | None = None
    for record in existing:
        paper_id = str(record.get("paper_id") or "")
        if record.get("sha256") and parsed.sha256 and record["sha256"] == parsed.sha256:
            return DuplicateVerdict(
                is_duplicate=True,
                kind="same-file",
                similarity=1.0,
                action="keep-existing",
                existing_paper_id=paper_id,
                existing_title=record.get("title") or "",
                reason="文件内容哈希完全一致，属于同一文件的重复上传，已存在版本可直接复用。",
                evidence={"sha256": parsed.sha256[:16]},
            )
        record_base = arxiv_base(str(record.get("arxiv_id") or ""))
        record_version = str(record.get("arxiv_version") or "")
        if base and record_base and base == record_base:
            new_num = version_number(new_version or "v1")
            old_num = version_number(record_version or "v1")
            newer = new_num > old_num
            verdict = DuplicateVerdict(
                is_duplicate=True,
                kind="version",
                similarity=1.0,
                action="replace-existing" if newer else "keep-existing",
                existing_paper_id=paper_id,
                existing_title=record.get("title") or "",
                existing_version=record_version or f"v{old_num}",
                new_version=new_version or f"v{new_num}",
                reason=(
                    f"arXiv 编号同为 {base}，检测到版本关系：库中已有 v{old_num}，本次为 v{new_num}。"
                    + ("建议保留本次上传的最新版本。" if newer else "库中版本更新，建议保留库中版本，避免回退。")
                ),
                evidence={"arxiv_base": base, "existing_version": old_num, "new_version": new_num},
            )
            if best is None or (verdict.action == "replace-existing" and best.action != "replace-existing"):
                best = verdict
            continue

        title_sim = text_similarity(normalize_title(parsed.title), normalize_title(str(record.get("title") or "")))
        sim = title_sim
        abstract_sim = 0.0
        record_simhash = record.get("doc_simhash")
        if isinstance(record_simhash, int) and record_simhash:
            abstract_sim = simhash_similarity(new_hash, record_simhash)
            sim = max(sim, 0.55 * title_sim + 0.45 * abstract_sim)
        if sim >= threshold:
            verdict = DuplicateVerdict(
                is_duplicate=True,
                kind="near-duplicate",
                similarity=sim,
                action="keep-existing",
                existing_paper_id=paper_id,
                existing_title=record.get("title") or "",
                reason=(
                    f"标题与正文指纹高度相似（综合相似度 {sim:.3f}），疑似同一论文的另一份文件；"
                    "为避免重复入库，默认保留已有条目。"
                ),
                evidence={
                    "title_similarity": round(title_sim, 3),
                    "doc_simhash_similarity": round(abstract_sim, 3),
                    "hamming": hamming64(new_hash, record_simhash) if isinstance(record_simhash, int) else None,
                },
            )
            if best is None or verdict.similarity > best.similarity:
                best = verdict
    return best or DuplicateVerdict(
        is_duplicate=False,
        similarity=0.0,
        reason="未在文献库中发现重复或版本关系。",
        evidence={"checked": len(existing)},
    )


def compare_parsed(a: ParsedPaper, b: ParsedPaper) -> dict[str, Any]:
    """对比两个版本的正文差异（用于去重提示中的「改了什么」）。"""
    text_a = "\n".join(block.text for block in a.blocks if block.kind in {"paragraph", "abstract"})
    text_b = "\n".join(block.text for block in b.blocks if block.kind in {"paragraph", "abstract"})
    similarity = _ts(text_a, text_b)
    titles_a = {s.title for s in a.sections}
    titles_b = {s.title for s in b.sections}
    refs_a = {r.title.lower() for r in a.references if r.title}
    refs_b = {r.title.lower() for r in b.references if r.title}
    return {
        "text_similarity": round(similarity, 4),
        "pages": {"a": len(a.pages), "b": len(b.pages)},
        "abstract_similarity": round(_ts(a.abstract, b.abstract), 4),
        "sections_added": sorted(titles_b - titles_a),
        "sections_removed": sorted(titles_a - titles_b),
        "references_added": len(refs_b - refs_a),
        "references_removed": len(refs_a - refs_b),
        "chars": {"a": len(text_a), "b": len(text_b)},
        "verdict": "identical" if similarity > 0.98 else ("minor-revision" if similarity > 0.85 else "major-revision"),
    }


def compare_with_library(
    parsed: ParsedPaper,
    existing_paper_id: str,
    settings: Any,
) -> dict[str, Any] | None:
    """从磁盘读取已解析的旧版本并对比。"""
    payload = load_parsed(existing_paper_id, settings)
    if not payload:
        return None
    try:
        old = _parsed_from_dict(payload)
    except Exception:
        return None
    return compare_parsed(old, parsed)


def _parsed_from_dict(payload: dict[str, Any]) -> ParsedPaper:
    from .model import BBox, PageInfo, ParseQuality, RefEntry, Section, TextBlock

    def _bbox(value: Any) -> BBox:
        return BBox.from_list(value)

    blocks = [
        TextBlock(
            id=b["id"],
            page=int(b["page"]),
            kind=b["kind"],
            text=b["text"],
            bbox=_bbox(b["bbox"]),
            order=int(b.get("order") or 0),
            column=int(b.get("column") or 0),
            level=int(b.get("level") or 0),
            number=b.get("number") or "",
            sid=b.get("sid") or "",
            font_size=float(b.get("font_size") or 0),
            font=b.get("font") or "",
            bold=bool(b.get("bold")),
            italic=bool(b.get("italic")),
            centered=bool(b.get("centered")),
            ocr=bool(b.get("ocr")),
        )
        for b in payload.get("blocks") or []
    ]
    return ParsedPaper(
        paper_id=payload["paper_id"],
        sha256=payload.get("sha256") or "",
        title=payload.get("title") or "",
        abstract=payload.get("abstract") or "",
        arxiv_id=payload.get("arxiv_id") or "",
        arxiv_version=payload.get("arxiv_version") or "",
        pages=[PageInfo(**p) for p in payload.get("pages") or []],
        blocks=blocks,
        sections=[Section(**s) for s in payload.get("sections") or []],
        references=[RefEntry(**r) for r in payload.get("references") or []],
        quality=ParseQuality(**(payload.get("quality") or {})),
    )
