"""文献管理 API：上传 / 批量 / 列表 / 详情 / 版本对比 / 页面图 / 删除。"""

from __future__ import annotations

import base64
import binascii
from typing import Any

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from ..core.config import get_settings
from ..core.errors import NotFoundError, ValidationError
from ..papers import service

router = APIRouter(prefix="/api/papers", tags=["papers"])


@router.post("/upload")
async def upload(request: Request, filename: str = Query(..., min_length=1), force: bool = Query(False)) -> dict[str, Any]:
    data = await request.body()
    if not data:
        raise ValidationError("请求体为空：请以 application/pdf 原始字节上传")
    return service.ingest_bytes(data, filename, settings=get_settings(), force=force)


@router.post("/batch")
async def upload_batch(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    files = payload.get("files") or []
    if not isinstance(files, list) or not files:
        raise ValidationError("files 不能为空")
    decoded: list[tuple[str, bytes]] = []
    for item in files[:30]:
        name = str(item.get("filename") or "paper.pdf")
        try:
            data = base64.b64decode(item.get("content") or "", validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValidationError(f"{name}: base64 解码失败 ({exc})") from exc
        decoded.append((name, data))
    return service.ingest_many(decoded, settings=get_settings())


@router.get("")
async def list_papers(
    q: str | None = None,
    sort: str = Query("recent", pattern="^(recent|title|year|pages)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    latest_only: bool = False,
) -> dict[str, Any]:
    return service.list_library(q=q, sort=sort, limit=limit, offset=offset, latest_only=latest_only)


@router.get("/search")
async def search_titles(q: str = Query(..., min_length=1)) -> dict[str, Any]:
    return {"items": service.search_titles(q)}


@router.post("/reindex")
async def reindex(paper_ids: list[str] | None = Body(None, embed=True)) -> dict[str, Any]:
    from ..indexing.indexer import reindex_library

    return reindex_library(get_settings(), paper_ids=paper_ids)


@router.get("/{paper_id}")
async def detail(paper_id: str) -> dict[str, Any]:
    return service.get_detail(paper_id, settings=get_settings())


@router.delete("/{paper_id}")
async def remove(paper_id: str) -> dict[str, Any]:
    return service.delete(paper_id, settings=get_settings())


@router.get("/{paper_id}/versions")
async def versions(paper_id: str) -> dict[str, Any]:
    from ..db import repo

    row = repo.get_paper(paper_id)
    if not row:
        raise NotFoundError(f"文献不存在: {paper_id}")
    group = str(row.get("version_group") or "")
    items = [service.summary(item) for item in repo.version_members(group)]
    return {"version_group": group, "items": items}


@router.get("/{paper_id}/compare")
async def compare(paper_id: str, other: str = Query(..., min_length=1)) -> dict[str, Any]:
    return service.compare(paper_id, other, settings=get_settings())


@router.get("/{paper_id}/pages/{page}.png")
async def page_image(paper_id: str, page: int) -> FileResponse:
    path = get_settings().pages_dir / paper_id / f"p{page:03d}.png"
    if not path.exists():
        raise NotFoundError(f"页面图不存在: {paper_id} p{page}（请先完成解析）")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/{paper_id}/file")
async def pdf_file(paper_id: str) -> FileResponse:
    path = get_settings().papers_dir / f"{paper_id}.pdf"
    if not path.exists():
        raise NotFoundError(f"原始 PDF 不存在: {paper_id}")
    return FileResponse(path, media_type="application/pdf", filename=f"{paper_id}.pdf")


@router.get("/{paper_id}/parsed")
async def parsed(paper_id: str) -> JSONResponse:
    from ..parsing import load_parsed

    payload = load_parsed(paper_id, get_settings())
    if not payload:
        raise NotFoundError(f"尚未完成解析: {paper_id}")
    return JSONResponse(payload)
