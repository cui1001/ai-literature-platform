"""论文检索、追踪与智能推荐（竞赛模块四）。

统一出口：检索、导入、参考文献批量导入、反馈、推荐与 Sickle 风格追踪。
"""

from .service import (
    feedback,
    import_paper,
    import_references,
    recommend,
    search,
    track_add,
    track_list,
    track_remove,
    tracker_run,
)

__all__ = [
    "search",
    "import_paper",
    "import_references",
    "feedback",
    "recommend",
    "track_add",
    "track_list",
    "track_remove",
    "tracker_run",
]
