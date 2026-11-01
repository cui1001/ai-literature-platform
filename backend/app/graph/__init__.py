"""局域引用图谱与学术综述生成（竞赛模块五）。

对外只暴露六个入口：

* :func:`build_graph` / :func:`get_graph` / :func:`list_graphs` —— 局域引用图谱；
* :func:`survey` / :func:`get_survey` / :func:`list_surveys` —— 领域学术综述。
"""

from __future__ import annotations

from .builder import (
    ROLE_LABELS_ZH,
    build_graph,
    get_graph,
    list_graphs,
)
from .refmatch import RefMatch, normalize_arxiv_id, normalize_doi
from .survey import get_survey, list_surveys, survey

__all__ = [
    "ROLE_LABELS_ZH",
    "RefMatch",
    "build_graph",
    "get_graph",
    "get_survey",
    "list_graphs",
    "list_surveys",
    "normalize_arxiv_id",
    "normalize_doi",
    "survey",
]
