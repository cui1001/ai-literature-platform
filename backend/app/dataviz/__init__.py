"""学术数据可视化 Copilot。

对外公开 CSV 数据集解析、确定性 SVG 图表、学术 Figure Caption 与
matplotlib 复现脚本。API 路由只依赖本包导出的函数。
"""

from __future__ import annotations

from .charts import (
    CHART_KINDS,
    matplotlib_script,
    normalize_spec,
    prepare_chart,
    render_svg,
    svg_from_spec,
)
from .dataset import (
    TYPE_DATE,
    TYPE_NUMBER,
    TYPE_TEXT,
    get_dataset,
    list_datasets,
    parse_csv_bytes,
)
from .service import build_chart, get_chart, list_charts

__all__ = [
    "CHART_KINDS",
    "TYPE_DATE",
    "TYPE_NUMBER",
    "TYPE_TEXT",
    "build_chart",
    "get_chart",
    "get_dataset",
    "list_charts",
    "list_datasets",
    "matplotlib_script",
    "normalize_spec",
    "parse_csv_bytes",
    "prepare_chart",
    "render_svg",
    "svg_from_spec",
]
