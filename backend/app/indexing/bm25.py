"""纯 Python BM25（Okapi）检索，替代 rank_bm25 第三方依赖。

特点：
- 支持中英文混合分词（复用 :mod:`app.core.textutil` 的 tokenizer）；
- 结果与参数完全确定，可用于离线评测；
- 语料规模为单机文献库（十万级 chunk 以内），无需倒排持久化。
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from ..core.textutil import tokens


@dataclass
class BM25Index:
    k1: float = 1.5
    b: float = 0.75
    doc_ids: list[str] = field(default_factory=list)
    doc_tokens: list[list[str]] = field(default_factory=list)
    doc_len: list[int] = field(default_factory=list)
    df: Counter = field(default_factory=Counter)
    idf: dict[str, float] = field(default_factory=dict)
    avgdl: float = 0.0
    term_freqs: list[Counter] = field(default_factory=list)

    def build(self, docs: Iterable[tuple[str, str]]) -> "BM25Index":
        self.doc_ids = []
        self.doc_tokens = []
        self.doc_len = []
        self.term_freqs = []
        self.df = Counter()
        for doc_id, text in docs:
            toks = tokens(text)
            self.doc_ids.append(doc_id)
            self.doc_tokens.append(toks)
            self.doc_len.append(len(toks))
            counter = Counter(toks)
            self.term_freqs.append(counter)
            for term in counter:
                self.df[term] += 1
        total_docs = len(self.doc_ids) or 1
        self.avgdl = sum(self.doc_len) / total_docs if self.doc_len else 0.0
        self.idf = {
            term: math.log(1 + (total_docs - freq + 0.5) / (freq + 0.5)) for term, freq in self.df.items()
        }
        return self

    def score(self, query: str, *, doc_index: int) -> float:
        toks = tokens(query)
        if not toks or not self.doc_tokens:
            return 0.0
        freqs = self.term_freqs[doc_index]
        length = self.doc_len[doc_index] or 1
        avgdl = self.avgdl or 1.0
        total = 0.0
        for term in toks:
            tf = freqs.get(term, 0)
            if not tf:
                continue
            idf = self.idf.get(term, 0.0)
            denom = tf + self.k1 * (1 - self.b + self.b * length / avgdl)
            total += idf * (tf * (self.k1 + 1)) / (denom or 1)
        return total

    def search(self, query: str, *, top_k: int = 50, allowed: set[int] | None = None) -> list[tuple[int, float]]:
        scores: list[tuple[int, float]] = []
        for index in range(len(self.doc_ids)):
            if allowed is not None and index not in allowed:
                continue
            value = self.score(query, doc_index=index)
            if value > 0:
                scores.append((index, value))
        scores.sort(key=lambda item: (-item[1], self.doc_ids[item[0]]))
        return scores[:top_k]

    def explain(self, query: str, doc_index: int, *, limit: int = 6) -> list[dict[str, float]]:
        """返回命中词及其 idf，便于前端展示「为什么检索到该段」。"""
        toks = tokens(query)
        freqs = self.term_freqs[doc_index] if self.term_freqs else Counter()
        hits = []
        for term in dict.fromkeys(toks):
            if freqs.get(term):
                hits.append({"term": term, "tf": float(freqs[term]), "idf": round(self.idf.get(term, 0.0), 4)})
        hits.sort(key=lambda item: -item["idf"])
        return hits[:limit]


def build_bm25(docs: Sequence[tuple[str, str]]) -> BM25Index:
    return BM25Index().build(docs)
