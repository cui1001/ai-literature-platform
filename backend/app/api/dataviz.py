"""CSV 数据集与图表生成接口（自研 SVG 渲染，附可复现 matplotlib 脚本）。

数据可视化模块的公开签名刻意不接收 ``settings``（内部统一读取全局配置），
因此路由层只做参数校验与延迟导入，不向模块传递配置对象。
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field

from ..core.errors import ValidationError

router = APIRouter(prefix="/api/dataviz", tags=["dataviz"])


class ChartRequest(BaseModel):
    dataset_id: str = Field(min_length=1, max_length=80)
    kind: str = Field(default="line", pattern="^(line|bar|scatter|radar3d|table)$")
    x: str | None = Field(default=None, max_length=120)
    y: str | list[str] | None = None
    series: str | None = Field(default=None, max_length=120)
    title: str | None = Field(default=None, max_length=200)
    caption: str | None = Field(default=None, max_length=2000)


@router.get("/datasets")
def list_datasets() -> list[dict]:
    from ..dataviz import list_datasets

    return list_datasets()


@router.post("/datasets")
async def upload_dataset(request: Request, filename: str = Query(default="data.csv")) -> dict:
    from ..dataviz import parse_csv_bytes

    body = await request.body()
    if not body:
        raise ValidationError("CSV 内容为空", field="file")
    return parse_csv_bytes(body, filename=filename)


@router.get("/datasets/{dataset_id}")
def get_dataset(dataset_id: str) -> dict:
    from ..dataviz import get_dataset

    return get_dataset(dataset_id)


@router.post("/charts")
def build_chart(payload: ChartRequest) -> dict:
    from ..dataviz import build_chart

    spec: dict = {"kind": payload.kind}
    for key in ("x", "y", "series", "title"):
        value = getattr(payload, key)
        if value not in (None, "", []):
            spec[key] = value
    return build_chart(payload.dataset_id, spec)


@router.get("/charts")
def list_charts(dataset_id: str | None = None) -> list[dict]:
    from ..dataviz import list_charts

    return list_charts(dataset_id=dataset_id)


@router.get("/charts/{chart_id}.svg")
def chart_svg(chart_id: str) -> Response:
    from ..dataviz import get_chart

    chart = get_chart(chart_id)
    svg = chart.get("svg") or ""
    if not svg.strip():
        raise ValidationError("该图表没有可用的 SVG 内容", field="chart_id")
    return Response(
        content=svg,
        media_type="image/svg+xml; charset=utf-8",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/charts/{chart_id}")
def get_chart(chart_id: str) -> dict:
    from ..dataviz import get_chart

    return get_chart(chart_id)
