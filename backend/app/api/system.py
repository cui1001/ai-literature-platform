"""系统状态与演示数据 API。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from ..core.config import get_settings
from ..core.logging import get_logger
from ..db import repo

log = get_logger("system")

router = APIRouter(prefix="/api", tags=["system"])

_STARTED_AT = time.time()


def _safe(fn: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - 状态探测失败不影响整体健康
        return {"error": str(exc)}


@router.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "uptime_s": round(time.time() - _STARTED_AT, 1), "version": get_settings().version}


@router.get("/system/status")
async def status() -> dict[str, Any]:
    s = get_settings()
    from ..indexing.indexer import index_status
    from ..providers.arxiv import arxiv_status
    from ..providers.embedding import embed_status
    from ..providers.llm import llm_status
    from ..providers.ocr import ocr_status

    return {
        "version": s.version,
        "modes": {
            "llm": s.llm_mode,
            "embed": s.embed_mode,
            "arxiv": s.arxiv_mode,
            "page_render_dpi": s.page_render_dpi,
            "ocr_text_threshold": s.ocr_text_threshold,
        },
        "paths": {"data": str(s.data_dir), "static": str(s.static_dir)},
        "library": _safe(repo.paper_stats),
        "index": _safe(index_status, s),
        "providers": {
            "llm": _safe(llm_status, s),
            "embedding": _safe(embed_status, s),
            "ocr": _safe(ocr_status, s),
            "arxiv": _safe(arxiv_status, s),
        },
        "counts": _safe(repo.table_counts),
    }


@router.post("/demo/seed")
async def seed_demo() -> dict[str, Any]:
    """导入仓库自带的合成语料（离线可复现），用于评委一键演示。"""
    s = get_settings()
    from ..papers import service

    # 扫描件优先：确保 demo 中保留一篇 OCR 解析的论文
    roots = [s.repo_root / "fixtures" / "papers" / "scanned", s.repo_root / "fixtures" / "papers"]
    files: list[Path] = []
    for root in roots:
        if root.exists():
            files.extend(sorted(root.glob("*.pdf")))
    results = []
    for path in files:
        try:
            result = service.ingest_bytes(path.read_bytes(), path.name, settings=s, source="fixture")
            results.append(
                {
                    "filename": path.name,
                    "status": result.get("status"),
                    "paper_id": (result.get("paper") or {}).get("id"),
                    "duplicate_kind": (result.get("verdict") or {}).get("kind"),
                }
            )
        except Exception as exc:  # noqa: BLE001
            results.append({"filename": path.name, "status": "failed", "error": str(exc)})
    return {
        "found": len(files),
        "imported": sum(1 for item in results if item.get("status") in {"imported", "replaced"}),
        "duplicates": sum(1 for item in results if item.get("status") == "duplicate"),
        "failed": sum(1 for item in results if item.get("status") == "failed"),
        "items": results,
    }


@router.post("/demo/reset")
async def reset_demo() -> dict[str, Any]:
    """清空文献库（仅删除本系统 artifacts，不动 fixtures 源文件）。"""
    s = get_settings()
    from ..papers import service

    removed = []
    for row in repo.list_papers(limit=1000) or []:
        try:
            service.delete(row["id"], settings=s)
            removed.append(row["id"])
        except Exception as exc:  # noqa: BLE001
            log.warning("reset delete failed | %s | %s", row.get("id"), exc)
    return {"removed": len(removed), "ids": removed}
