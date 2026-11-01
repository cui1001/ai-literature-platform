"""引用图谱与领域综述接口。"""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from ..core.errors import NotFoundError, ValidationError

router = APIRouter(prefix="/api/graph", tags=["graph"])


class GraphRequest(BaseModel):
    paper_ids: list[str] | None = None


class SurveyRequest(BaseModel):
    paper_ids: list[str] | None = None
    title: str | None = Field(default=None, max_length=120)
    graph_id: str | None = Field(default=None, max_length=40)


def _paper_ids(paper_ids: list[str] | None) -> list[str]:
    """未指定论文时默认取本地库全部（限 40 篇，保证可视化可读）。"""
    if paper_ids:
        return [str(item) for item in paper_ids if str(item).strip()][:40]
    from ..db import repo

    rows = repo.all_papers()[:40]
    ids = [str(row.get("paper_id") or row.get("id") or "") for row in rows]
    ids = [item for item in ids if item]
    if not ids:
        raise ValidationError("文献库为空，请先导入论文", field="paper_ids")
    return ids


@router.post("/build")
def build(payload: GraphRequest) -> dict:
    from ..graph import build_graph

    return build_graph(_paper_ids(payload.paper_ids))


@router.post("/survey")
def survey(payload: SurveyRequest) -> dict:
    from ..graph import survey as _survey

    return _survey(
        _paper_ids(payload.paper_ids),
        title=payload.title,
        graph_id=payload.graph_id or None,
    )


@router.get("")
def list_graphs(limit: int = Query(default=20, ge=1, le=100)) -> list[dict]:
    from ..graph import list_graphs as _list

    return _list(limit=limit)


@router.get("/surveys")
def list_surveys(limit: int = Query(default=20, ge=1, le=100)) -> list[dict]:
    from ..graph import list_surveys as _list

    return _list(limit=limit)


@router.get("/surveys/{survey_id}")
def get_survey(survey_id: str) -> dict:
    from ..graph import get_survey as _get

    return _get(survey_id)


@router.get("/{graph_id}/export")
def export_graph(graph_id: str, fmt: str = Query(default="mermaid", pattern="^(json|mermaid|dot)$")) -> dict:
    """把已保存的图谱导出为 Mermaid / Graphviz DOT，便于直接贴进论文附录。"""
    from ..graph import get_graph as _get

    graph = _get(graph_id)
    nodes = graph.get("nodes") or []
    links = graph.get("links") or graph.get("edges") or []
    if fmt == "json":
        return {"format": "json", "graph": graph}
    if fmt == "dot":
        lines = ["digraph citations {", "  rankdir=LR;", '  node [shape=box, style="rounded"];']
        for node in nodes:
            label = str(node.get("title") or node.get("id") or "").replace('"', "'")[:48]
            lines.append(f'  "{node.get("id")}" [label="{label}"];')
        for edge in links:
            lines.append(f'  "{edge.get("source")}" -> "{edge.get("target")}";')
        lines.append("}")
        return {"format": "dot", "content": "\n".join(lines)}

    def _alias(node_id: str) -> str:
        return "n" + "".join(ch if ch.isalnum() else "_" for ch in str(node_id))

    lines = ["flowchart LR"]
    for node in nodes:
        label = str(node.get("title") or node.get("id") or "").replace('"', "'")[:48]
        lines.append(f'  {_alias(node.get("id", ""))}["{label}"]')
    for edge in links:
        lines.append(f"  {_alias(edge.get('source'))} --> {_alias(edge.get('target'))}")
    return {"format": "mermaid", "content": "\n".join(lines)}


@router.get("/{graph_id}")
def get_graph(graph_id: str) -> dict:
    from ..graph import get_graph as _get

    graph = _get(graph_id)
    if not graph:
        raise NotFoundError(f"图谱不存在: {graph_id}")
    return graph
