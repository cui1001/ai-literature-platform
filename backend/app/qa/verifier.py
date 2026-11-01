"""答案校验：把「抗幻觉」从口号变成可展示的检查项。

三类检查：
1. 引用完整性：答案必须至少引用一个证据块；
2. 数值可溯源：答案中出现的每个数字都要能在被引用的原文里找到（超参、BLEU、GPU 数量最容易被编造）；
3. 句子支撑度：逐句与证据计算词元重合，标出「没有原文支撑」的句子。

结果写入 ``qa_log.verified`` 与返回体，前端可以逐条展开，评审也能看到失败样例。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..core.textutil import sentence_split, token_set
from .citations import Citation, parse_markers, strip_markers

NUMBER_RE = re.compile(r"(?<![\w.\-])\d[\d,]*(?:\.\d+)?\s?%?(?![\w])")
SUPPORT_THRESHOLD = 0.28


def numbers_in(text: str) -> list[str]:
    out: list[str] = []
    for match in NUMBER_RE.findall(text or ""):
        value = match.replace(",", "").replace(" ", "")
        if value and value not in out:
            out.append(value)
    return out


@dataclass
class VerificationResult:
    verified: bool
    confidence: float
    cited_count: int
    numbers_total: int = 0
    numbers_supported: int = 0
    unsupported_numbers: list[str] = field(default_factory=list)
    unsupported_sentences: list[str] = field(default_factory=list)
    sentence_count: int = 0
    checks: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verified": self.verified,
            "confidence": round(self.confidence, 3),
            "cited_count": self.cited_count,
            "numbers_total": self.numbers_total,
            "numbers_supported": self.numbers_supported,
            "unsupported_numbers": self.unsupported_numbers,
            "unsupported_sentences": self.unsupported_sentences,
            "sentence_count": self.sentence_count,
            "checks": self.checks,
            "notes": self.notes,
        }


def _evidence_text(citations: Sequence[Citation], full_texts: dict[str, str] | None) -> str:
    parts: list[str] = []
    for citation in citations:
        parts.append(citation.quote)
        if full_texts and citation.chunk_id in full_texts:
            parts.append(full_texts[citation.chunk_id])
    return "\n".join(parts)


def verify_answer(
    answer: str,
    citations: Sequence[Citation],
    *,
    full_texts: dict[str, str] | None = None,
    min_citations: int = 1,
    refused: bool = False,
) -> VerificationResult:
    body = strip_markers(answer)
    cited = [c for c in citations if c.cited]
    checks: list[dict[str, Any]] = []

    if refused:
        return VerificationResult(
            verified=True,
            confidence=0.0,
            cited_count=0,
            checks=[{"name": "refusal", "ok": True, "detail": "证据不足时明确拒答，视为通过"}],
            notes=["模型在缺少证据时选择拒答"],
        )

    # 1) 引用完整性
    citation_ok = len(cited) >= min_citations
    checks.append(
        {
            "name": "citation",
            "ok": citation_ok,
            "detail": f"引用证据 {len(cited)} 条（要求 ≥{min_citations}）",
        }
    )

    # 2) 数值可溯源
    evidence_text = _evidence_text(cited, full_texts)
    evidence_numbers = set(numbers_in(evidence_text))
    answer_numbers = numbers_in(body)
    unsupported_numbers = [n for n in answer_numbers if n not in evidence_numbers]
    if unsupported_numbers:
        # 容忍「第 3 节」这类结构性数字：若该数字在任一被引用块所在页出现，也算支持
        unsupported_numbers = [
            n for n in unsupported_numbers if not re.search(rf"(?<!\d){re.escape(n)}(?!\d)", evidence_text)
        ]
    numbers_ok = not unsupported_numbers
    checks.append(
        {
            "name": "numbers",
            "ok": numbers_ok,
            "detail": (
                f"数值 {len(answer_numbers) - len(unsupported_numbers)}/{len(answer_numbers)} 可在原文定位"
                if answer_numbers
                else "答案不含数值断言"
            ),
            "unsupported": unsupported_numbers[:10],
        }
    )

    # 3) 句子支撑度
    evidence_tokens = token_set(evidence_text)
    sentences = [s for s in sentence_split(body) if len(s) >= 8]
    unsupported_sentences: list[str] = []
    sentence_scores: list[float] = []
    for sentence in sentences:
        tokens = token_set(sentence)
        if not tokens:
            continue
        overlap = len(tokens & evidence_tokens) / max(1, len(tokens))
        sentence_scores.append(overlap)
        if overlap < SUPPORT_THRESHOLD and not _is_disclaimer(sentence):
            unsupported_sentences.append(sentence)
    support_ratio = (
        sum(1 for value in sentence_scores if value >= SUPPORT_THRESHOLD) / len(sentence_scores)
        if sentence_scores
        else 0.0
    )
    sentences_ok = len(unsupported_sentences) <= max(1, len(sentences) // 3)
    checks.append(
        {
            "name": "sentences",
            "ok": sentences_ok,
            "detail": f"逐句支撑率 {support_ratio:.0%}，未支撑句 {len(unsupported_sentences)} 条",
            "unsupported": unsupported_sentences[:5],
        }
    )

    confidence = 0.0
    if citation_ok:
        confidence += 0.4
    if numbers_ok:
        confidence += 0.25 if answer_numbers else 0.2
    confidence += 0.3 * support_ratio
    if unsupported_numbers:
        confidence -= min(0.25, 0.03 * len(unsupported_numbers))
    confidence = round(max(0.0, min(0.98, confidence)), 3)
    verified = citation_ok and numbers_ok and sentences_ok

    notes: list[str] = []
    if unsupported_numbers:
        notes.append(f"以下数值未能在被引原文中定位：{', '.join(unsupported_numbers[:6])}")
    if unsupported_sentences:
        notes.append(f"有 {len(unsupported_sentences)} 句缺少原文直接支撑")
    if not note_ok(checks):
        notes.append("校验未全部通过，请以引用原文为准")
    return VerificationResult(
        verified=verified,
        confidence=confidence,
        cited_count=len(cited),
        numbers_total=len(answer_numbers),
        numbers_supported=len(answer_numbers) - len(unsupported_numbers),
        unsupported_numbers=unsupported_numbers[:10],
        unsupported_sentences=unsupported_sentences[:5],
        sentence_count=len(sentences),
        checks=checks,
        notes=notes,
    )


def note_ok(checks: Sequence[dict[str, Any]]) -> bool:
    return all(bool(check.get("ok")) for check in checks)


def _is_disclaimer(sentence: str) -> bool:
    markers = ("未能", "未提及", "无法", "没有直接", "不作回答", "证据不足", "不排除", "仅供参考")
    return any(marker in sentence for marker in markers)


def verify_citation_markers(answer: str, citations: Sequence[Citation]) -> list[str]:
    """返回答案中出现但不存在于证据列表的标记（幻觉引用）。"""
    known = {c.evidence_id for c in citations}
    return [marker for marker in parse_markers(answer) if marker not in known]
