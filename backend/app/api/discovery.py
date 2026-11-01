"""arXiv 检索、入库、追踪与推荐接口。

服务实现位于 ``app.discovery``；路由层只做参数校验与响应整形，
保证模块未就绪时整个 API 仍可启动（延迟导入）。
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from ..core.errors import ValidationError

router = APIRouter(prefix="/api/discovery", tags=["discovery"])


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=400)
    max_results: int = Field(default=12, ge=1, le=50)
    category: str | None = Field(default=None, max_length=40)
    prefer_code: bool = True


class ImportRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=300)


class TrackRequest(BaseModel):
    action: str = Field(default="add", pattern="^(add|remove|run|list)$")
    query: str | None = Field(default=None, max_length=300)
    topic: str | None = Field(default=None, max_length=300)
    category: str | None = Field(default=None, max_length=40)
    keywords: list[str] | None = None
    topic_id: str | None = Field(default=None, max_length=80)
    force: bool = False

    @property
    def keyword(self) -> str:
        """同时兼容 ``query`` 与 ``topic`` 两种命名（前端使用 topic）。"""
        return (self.query or self.topic or "").strip()


class FeedbackRequest(BaseModel):
    target: str = Field(min_length=1, max_length=200)
    action: str = Field(pattern="^(like|dislike|save|hide)$")


@router.post("/search")
def search(payload: SearchRequest) -> dict:
    from ..discovery import search as _search

    return _search(
        payload.query,
        max_results=payload.max_results,
        category=payload.category,
        prefer_code=payload.prefer_code,
    )


@router.post("/import")
def import_paper(payload: ImportRequest) -> dict:
    from ..discovery import import_paper as _import

    return _import(payload.identifier)


@router.post("/references/{paper_id}")
def import_references(paper_id: str, limit: int = Query(default=5, ge=1, le=20)) -> dict:
    from ..discovery import import_references as _refs

    return _refs(paper_id, limit=limit)


@router.get("/recommend")
def recommend(user_id: str = Query(default="local"), limit: int = Query(default=8, ge=1, le=30)) -> dict:
    from ..discovery import recommend as _recommend

    return _recommend(user_id=user_id, limit=limit)


@router.post("/feedback")
def feedback(payload: FeedbackRequest) -> dict:
    from ..discovery import feedback as _feedback

    return _feedback(payload.target, payload.action)


@router.post("/track")
def track(payload: TrackRequest) -> dict:
    from ..discovery import track_add, track_list, track_remove, tracker_run

    if payload.action == "add":
        query = payload.keyword
        if not query:
            raise ValidationError("追踪主题不能为空", field="query")
        return track_add(query, category=payload.category, keywords=payload.keywords)
    if payload.action == "remove":
        topic_id = payload.topic_id or payload.keyword
        if not topic_id:
            raise ValidationError("需要 topic_id 或 query", field="topic_id")
        return track_remove(topic_id)
    if payload.action == "run":
        return tracker_run(force=payload.force)
    return {"topics": track_list()}


@router.get("/status")
def status() -> dict:
    from ..providers.arxiv import arxiv_status

    from ..core.config import get_settings

    return {"arxiv": arxiv_status(get_settings())}
