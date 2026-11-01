"""文献导入与管理服务。"""

from .service import (
    compare,
    delete,
    ensure_parsed,
    get_detail,
    ingest_bytes,
    ingest_many,
    ingest_path,
    list_library,
    search_titles,
    summary,
)

__all__ = [
    "compare",
    "delete",
    "ensure_parsed",
    "get_detail",
    "ingest_bytes",
    "ingest_many",
    "ingest_path",
    "list_library",
    "search_titles",
    "summary",
]
