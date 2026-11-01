"""问答 API：单篇/多篇抗幻觉问答、跨文献对比、历史与模型状态。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Query

from ..core.config import get_settings
from ..qa.compare import DEFAULT_KEYS, DIMENSIONS, compare_papers
from ..qa.service import answer_question, history

router = APIRouter(prefix="/api/qa", tags=["qa"])


@router.post("/ask")
async def ask(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    question = str(payload.get("question") or "").strip()
    paper_ids = payload.get("paper_ids") or None
    if paper_ids is not None:
        paper_ids = [str(item) for item in paper_ids]
    result = answer_question(
        question,
        paper_ids,
        settings=get_settings(),
        top_k=int(payload.get("top_k") or 6),
        mode=str(payload.get("mode") or "hybrid"),
        user_id=str(payload.get("user_id") or "local"),
        save=bool(payload.get("save", True)),
    )
    return result.to_dict()


@router.post("/compare")
async def compare(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    paper_ids = [str(item) for item in (payload.get("paper_ids") or [])]
    result = compare_papers(
        paper_ids,
        keys=payload.get("dimensions") or DEFAULT_KEYS,
        settings=get_settings(),
    )
    return result.to_dict() if hasattr(result, "to_dict") else result


@router.get("/history")
async def qa_history(limit: int = Query(20, ge=1, le=200)) -> dict[str, Any]:
    return {"items": history(limit)}


@router.get("/dimensions")
async def dimensions() -> dict[str, Any]:
    return {"items": DIMENSIONS, "default": DEFAULT_KEYS}


@router.get("/status")
async def status() -> dict[str, Any]:
    from ..providers.llm import llm_status

    return llm_status(get_settings())
