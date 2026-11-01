"""参考文献匹配：把论文 references 对齐到本地论文库。

匹配优先级（高 → 低）：

1. **arXiv ID**：去掉 ``arXiv:`` 前缀与 ``vN`` 版本号后精确匹配，置信度 1.0；
2. **DOI**：去掉 ``https://doi.org/`` / ``doi:`` 前缀并小写化后精确匹配，置信度 1.0；
3. **标题**：``normalize_title`` 后完全一致记 1.0；否则用 ``text_similarity`` 取最高分，
   仅当相似度 **大于 0.86** 时采用，避免把同领域不同论文硬连；
4. **原文兜底**：ref 缺标题时，若本地论文标题完整出现在 ``raw`` 引用串中，按 0.90 采用。

本模块只做纯计算，不读写数据库；写回 ``refs`` / ``edges`` 由 ``builder`` 负责。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from ..core.textutil import normalize_title, text_similarity, truncate

TITLE_THRESHOLD = 0.86
EXACT_TITLE_SCORE = 1.0
RAW_CONTAINMENT_SCORE = 0.90
MIN_TITLE_LEN = 12
MAX_RAW_TITLE_LEN = 400

_ARXIV_PREFIX_RE = re.compile(r"^\s*(?:arxiv\s*:\s*|https?://arxiv\.org/(?:abs|pdf)/)?", re.I)
_ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})(?:v\d+)?", re.I)
_ARXIV_VERSION_RE = re.compile(r"v\d+$", re.I)
_DOI_PREFIX_RE = re.compile(r"^\s*(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)", re.I)
_DOI_TRAILING_RE = re.compile(r"[.,;]+$")


def normalize_arxiv_id(value: str | None) -> str:
    """arXiv 规范化：``arXiv:1905.00001v2`` → ``1905.00001``（保留旧式编号原文）。"""
    text = (value or "").strip()
    if not text:
        return ""
    text = _ARXIV_PREFIX_RE.sub("", text)
    match = _ARXIV_ID_RE.search(text)
    if match:
        return match.group(1).lower()
    return _ARXIV_VERSION_RE.sub("", text).strip().lower()


def normalize_doi(value: str | None) -> str:
    """DOI 规范化：去协议/``doi:`` 前缀、小写化、去掉句末标点。"""
    text = (value or "").strip().lower()
    if not text:
        return ""
    text = _DOI_PREFIX_RE.sub("", text)
    return _DOI_TRAILING_RE.sub("", text.strip())


@dataclass(frozen=True)
class RefMatch:
    """一条参考文献匹配到本地论文的结果。"""

    paper_id: str
    score: float
    method: str  # arxiv | doi | title | raw
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "score": self.score,
            "method": self.method,
            "evidence": self.evidence,
        }


class LibraryIndex:
    """本地论文库的匹配索引（arXiv / DOI / 标题三级索引）。"""

    def __init__(self, records: Iterable[Mapping[str, Any]]):
        self._records: list[dict[str, Any]] = []
        self._by_arxiv: dict[str, str] = {}
        self._by_doi: dict[str, str] = {}
        self._by_title: dict[str, str] = {}
        for record in records:
            paper_id = str(record.get("id") or "").strip()
            if not paper_id:
                continue
            title = str(record.get("title") or "").strip()
            title_norm = str(record.get("title_norm") or "").strip() or normalize_title(title)
            arxiv_id = normalize_arxiv_id(record.get("arxiv_id"))
            doi = normalize_doi(record.get("doi"))
            item = {
                "id": paper_id,
                "title": title,
                "title_norm": title_norm,
                "arxiv_id": arxiv_id,
                "doi": doi,
                "year": int(record.get("year") or 0),
            }
            self._records.append(item)
            if arxiv_id:
                self._by_arxiv.setdefault(arxiv_id, paper_id)
            if doi:
                self._by_doi.setdefault(doi, paper_id)
            if title_norm:
                self._by_title.setdefault(title_norm, paper_id)
        # 标题完全一致时优先使用最新年份的记录（同名预印本/正式版场景）。
        self._record_by_id = {item["id"]: item for item in self._records}

    @property
    def size(self) -> int:
        return len(self._records)

    def match(self, ref: Mapping[str, Any], *, exclude: Iterable[str] = ()) -> RefMatch | None:
        """按 arXiv → DOI → 标题 → 原文 的顺序匹配一条参考文献。"""
        excluded = {str(pid) for pid in exclude if pid}
        ref_arxiv = normalize_arxiv_id(ref.get("arxiv_id"))
        if ref_arxiv and ref_arxiv in self._by_arxiv:
            paper_id = self._by_arxiv[ref_arxiv]
            if paper_id not in excluded:
                return RefMatch(paper_id, 1.0, "arxiv", f"arXiv:{ref_arxiv} 精确匹配")

        ref_doi = normalize_doi(ref.get("doi"))
        if ref_doi and ref_doi in self._by_doi:
            paper_id = self._by_doi[ref_doi]
            if paper_id not in excluded:
                return RefMatch(paper_id, 1.0, "doi", f"DOI:{ref_doi} 精确匹配")

        ref_title = str(ref.get("title") or "").strip()
        title_norm = normalize_title(ref_title)
        if title_norm:
            paper_id = self._by_title.get(title_norm)
            if paper_id and paper_id not in excluded:
                return RefMatch(paper_id, EXACT_TITLE_SCORE, "title", f"标题完全一致：{truncate(ref_title, 80)}")
            best = self._best_title_match(title_norm, ref_title, excluded)
            if best is not None:
                return best

        # 只有标题缺失时才用原文兜底，避免作者列表/年份误命中。
        if not title_norm:
            return self._raw_containment(ref, excluded)
        return None

    def _best_title_match(self, title_norm: str, ref_title: str, excluded: set[str]) -> RefMatch | None:
        best_id = ""
        best_score = 0.0
        for item in self._records:
            if item["id"] in excluded or not item["title_norm"]:
                continue
            score = text_similarity(title_norm, item["title_norm"])
            if score > best_score:
                best_id, best_score = item["id"], score
        if best_id and best_score > TITLE_THRESHOLD:
            return RefMatch(
                best_id,
                round(best_score, 4),
                "title",
                f"标题相似度 {best_score:.3f}：{truncate(ref_title, 80)}",
            )
        return None

    def _raw_containment(self, ref: Mapping[str, Any], excluded: set[str]) -> RefMatch | None:
        raw_norm = normalize_title(ref.get("raw") or "")
        if len(raw_norm) < MIN_TITLE_LEN:
            return None
        raw_norm = raw_norm[:MAX_RAW_TITLE_LEN]
        best_id = ""
        best_len = 0
        for item in self._records:
            candidate = item["title_norm"]
            if item["id"] in excluded or len(candidate) < MIN_TITLE_LEN:
                continue
            if candidate in raw_norm and len(candidate) > best_len:
                best_id, best_len = item["id"], len(candidate)
        if not best_id:
            return None
        return RefMatch(
            best_id,
            RAW_CONTAINMENT_SCORE,
            "raw",
            f"参考文献原文包含本地标题：{truncate(self._record_by_id[best_id]['title'], 80)}",
        )
