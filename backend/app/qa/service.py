"""问答服务：检索 → 组织证据 → 生成 → 引用回链 → 校验 → 落库。

这一层是「抗幻觉」的最后一公里：
- 送给模型的上下文与前端展示的证据是同一份（编号一致）；
- 生成结果中的 [En] 必须能在证据表中找到，否则记为「幻觉引用」；
- 数值与逐句支撑度由 :mod:`app.qa.verifier` 复核，结果随答案一起返回并入库。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..core.config import Settings, get_settings
from ..core.errors import ProviderUnavailable, ValidationError
from ..core.logging import get_logger
from ..db import repo
from ..indexing.retriever import expand_query, get_retriever
from ..providers.base import LLMResult
from ..providers.llm import get_llm
from ..providers.offline import run_offline
from .citations import Citation, build_citations, evidence_payload, link_citations, parse_markers, strip_markers, unique_sources
from .verifier import VerificationResult, verify_answer

logger = get_logger(__name__)

REFUSAL_MARKERS = ("未找到直接证据", "不作回答", "未能在所选的", "证据不足", "无法回答")


@dataclass
class QAResult:
    question: str
    answer: str
    citations: list[Citation] = field(default_factory=list)
    evidence: list[Citation] = field(default_factory=list)
    verification: VerificationResult | None = None
    refused: bool = False
    provider: str = ""
    model: str = ""
    degraded: bool = False
    latency_ms: float = 0.0
    retrieval: dict[str, Any] = field(default_factory=dict)
    qa_id: str = ""
    marker_warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.qa_id,
            "question": self.question,
            "answer": self.answer,
            "answer_plain": strip_markers(self.answer),
            "citations": [c.to_dict() for c in self.citations],
            "evidence": [c.to_dict() for c in self.evidence],
            "sources": unique_sources(self.evidence),
            "verification": self.verification.to_dict() if self.verification else None,
            "verified": bool(self.verification.verified) if self.verification else False,
            "confidence": self.verification.confidence if self.verification else 0.0,
            "refused": self.refused,
            "provider": self.provider,
            "model": self.model,
            "degraded": self.degraded,
            "latency_ms": round(self.latency_ms, 1),
            "retrieval": self.retrieval,
            "marker_warnings": self.marker_warnings,
            "notes": self.notes,
        }


def run_task_with(
    task: str,
    payload: dict[str, Any],
    settings: Settings | None = None,
    *,
    expect_json: bool = False,
) -> LLMResult:
    """与 providers.llm.run_task 相同，但允许传入自定义 Settings（测试/多库）。"""
    settings = settings or get_settings()
    llm = get_llm(settings)
    started = time.perf_counter()
    try:
        return llm.run(task, payload, expect_json=expect_json)
    except ProviderUnavailable as exc:
        result = run_offline(task, payload, expect_json=expect_json)
        result.degraded = True
        result.notes = (result.notes + " | " if result.notes else "") + f"在线失败降级: {exc}"
        result.latency_ms = (time.perf_counter() - started) * 1000
        logger.warning("llm degraded to offline: %s", exc)
        return result


def answer_question(
    question: str,
    paper_ids: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    top_k: int | None = None,
    mode: str = "hybrid",
    user_id: str = "local",
    save: bool = True,
) -> QAResult:
    settings = settings or get_settings()
    question = (question or "").strip()
    if not question:
        raise ValidationError("问题不能为空")

    started = time.perf_counter()
    retriever = get_retriever(settings)
    retrieval = retriever.search(question, paper_ids=paper_ids, top_k=top_k or max(settings.top_k, 8), mode=mode)
    chunks = retrieval.chunks

    if not chunks:
        refusal = QAResult(
            question=question,
            answer="未在所选文献中检索到与问题相关的内容。为避免编造，本次不作回答。请尝试换用更具体的术语，或先上传相关论文。",
            refused=True,
            provider="retriever",
            retrieval=retrieval.stats,
            notes=["检索无命中"],
        )
        refusal.verification = VerificationResult(
            verified=True,
            confidence=0.0,
            cited_count=0,
            checks=[{"name": "no-evidence", "ok": True, "detail": "检索无命中，明确拒答"}],
            notes=["无证据时不产生答案"],
        )
        refusal.latency_ms = (time.perf_counter() - started) * 1000
        if save:
            refusal.qa_id = _save(refusal, settings, user_id)
        return refusal

    citations = build_citations(chunks)
    _, expansions = expand_query(question)
    payload = {
        "question": question,
        "query_expansion": expansions,
        "evidence": evidence_payload(citations),
        "requirement": "每个结论后标注 [En]，未在证据中出现的信息不要写。",
    }
    result = run_task_with("answer_question", payload, settings)
    answer = (result.text or "").strip()
    linked = link_citations(answer, citations)
    refused = _is_refusal(answer, result)

    full_texts = {chunk.chunk_id: chunk.text for chunk in chunks}
    verification = verify_answer(answer, citations, full_texts=full_texts, refused=refused)
    marker_warnings: list[str] = []
    known = {c.evidence_id for c in citations}
    for marker in parse_markers(answer):
        if marker not in known:
            marker_warnings.append(f"答案引用了不存在的证据编号 {marker}")

    notes: list[str] = []
    if result.notes:
        notes.append(result.notes)
    if not refused and not linked:
        notes.append("答案没有任何可定位的引用，已标记为不可验证")
        verification.verified = False
        verification.confidence = min(verification.confidence, 0.2)
    if result.degraded:
        notes.append("在线模型不可用，本次由确定性离线引擎生成")

    qa = QAResult(
        question=question,
        answer=answer,
        citations=linked,
        evidence=citations,
        verification=verification,
        refused=refused,
        provider=result.provider,
        model=result.model,
        degraded=result.degraded,
        retrieval=retrieval.stats,
        marker_warnings=marker_warnings,
        notes=[note for note in notes if note],
    )
    qa.latency_ms = (time.perf_counter() - started) * 1000
    if save:
        qa.qa_id = _save(qa, settings, user_id)
    return qa


def _is_refusal(answer: str, result: LLMResult) -> bool:
    if "no-evidence" in (result.notes or ""):
        return True
    if not answer:
        return True
    head = answer[:120]
    return any(marker in head for marker in REFUSAL_MARKERS) and len(answer) < 400


def _save(qa: QAResult, settings: Settings, user_id: str) -> str:
    try:
        return repo.add_qa(
            {
                "user_id": user_id,
                "question": qa.question,
                "paper_ids": sorted({c.paper_id for c in qa.evidence}),
                "answer": qa.answer,
                "citations": [c.to_dict() for c in qa.citations],
                "verified": qa.verification.verified if qa.verification else False,
                "confidence": qa.verification.confidence if qa.verification else 0.0,
                "provider": qa.provider,
                "mode": qa.retrieval.get("mode", ""),
                "trace": {
                    "retrieval": qa.retrieval,
                    "verification": qa.verification.to_dict() if qa.verification else {},
                    "notes": qa.notes,
                },
            }
        )
    except Exception:  # noqa: BLE001 - 记录失败不应影响答案返回
        logger.exception("保存问答记录失败")
        return ""


def history(limit: int = 20) -> list[dict[str, Any]]:
    return repo.list_qa(limit=limit)
