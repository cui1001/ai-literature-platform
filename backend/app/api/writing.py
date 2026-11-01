"""学术写作 Copilot 接口：框架生成、拓扑图脚本、学术翻译、导出。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from ..core.errors import NotFoundError, ValidationError

router = APIRouter(prefix="/api/writing", tags=["writing"])


class OutlineRequest(BaseModel):
    idea: str = Field(min_length=4, max_length=4000)
    paper_ids: list[str] | None = None
    title: str | None = Field(default=None, max_length=200)
    language: str = Field(default="zh", pattern="^(zh|en)$")


class DiagramRequest(BaseModel):
    """拓扑图脚本请求。

    ``nodes``/``edges`` 与 ``spec.nodes/spec.edges`` 两种写法都接受，
    便于前端直接提交 ``{nodes:[...], edges:[...]}``。
    """

    kind: str = Field(default="mermaid", pattern="^(mermaid|graphviz|tikz|matplotlib)$")
    nodes: list[dict[str, Any]] | None = None
    edges: list[dict[str, Any]] | None = None
    spec: dict[str, Any] | None = None


class TranslateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    source: str = Field(default="zh", pattern="^(zh|en)$")
    style: str = Field(default="paper", pattern="^(paper|academic|concise|literal)$")


@router.post("/outline")
def draft(payload: OutlineRequest) -> dict:
    from ..writing import draft_outline

    return draft_outline(
        payload.idea,
        paper_ids=payload.paper_ids,
        title=payload.title,
        language=payload.language,
    )


@router.post("/diagram")
def diagram(payload: DiagramRequest) -> dict:
    from ..writing import diagram as _diagram

    if payload.spec is not None:
        spec = dict(payload.spec)
    else:
        spec = {"nodes": payload.nodes or [], "edges": payload.edges or []}
    if not spec.get("nodes"):
        raise ValidationError("拓扑图至少需要一个节点", field="nodes")
    return _diagram(spec, kind=payload.kind)


@router.post("/translate")
def translate(payload: TranslateRequest) -> dict:
    from ..writing import academic_translate

    return academic_translate(payload.text, source=payload.source, style=payload.style)


@router.get("/outlines")
def list_outlines() -> list[dict]:
    from ..writing import list_outlines

    return list_outlines()


@router.get("/outlines/{outline_id}")
def get_outline(outline_id: str) -> dict:
    from ..writing import get_outline

    outline = get_outline(outline_id)
    if not outline:
        raise NotFoundError(f"框架不存在: {outline_id}")
    return outline


@router.post("/outlines/{outline_id}/export")
def export_outline(outline_id: str, fmt: str = Query(default="md", pattern="^(md|txt)$")) -> dict:
    from ..writing import export_outline

    return export_outline(outline_id, fmt=fmt)
