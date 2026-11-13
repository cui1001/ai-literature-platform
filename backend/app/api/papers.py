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
async def page_image(
    paper_id: str,
    page: int,
    request: Request,
    fmt: str | None = Query(None, pattern="^(png|webp|jpeg|jpg)$"),
    w: int | None = Query(None, ge=240, le=2400, description="目标宽度（像素），仅对 webp/jpeg 生效"),
    q: int = Query(76, ge=40, le=95, description="webp/jpeg 质量"),
) -> FileResponse:
    """页面图。

    默认返回原始 150DPI PNG；当浏览器 Accept 含 ``image/webp``（现代浏览器都带）
    或显式传 ``fmt=webp`` 时，返回按需生成并缓存的 WebP 副本（体积通常只有 1/5~1/4），
    这对弱网/跨地域访问的阅读器体验影响很大。
    """
    settings = get_settings()
    src = settings.pages_dir / paper_id / f"p{page:03d}.png"
    if not src.exists():
        raise NotFoundError(f"页面图不存在: {paper_id} p{page}（请先完成解析）")

    accept = request.headers.get("accept", "")
    target = (fmt or ("webp" if "image/webp" in accept else "png")).lower()
    if target == "jpg":
        target = "jpeg"
    if target == "png":
        return FileResponse(src, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})

    width = w or 1100
    cache_dir = settings.pages_dir / paper_id / "_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    suffix = "webp" if target == "webp" else "jpg"
    out = cache_dir / f"p{page:03d}-w{width}-q{q}.{suffix}"
    if not out.exists():
        _render_derivative(src, out, target, width, q)
    return FileResponse(
        out,
        media_type="image/webp" if target == "webp" else "image/jpeg",
        headers={"Cache-Control": "public, max-age=604800", "Vary": "Accept"},
    )


def _render_derivative(src, out, target: str, width: int, quality: int) -> None:
    """把页面 PNG 缩放并转成 WebP/JPEG；临时文件 + 原子替换，避免并发读到半成品。

    WebP 分两种策略（大幅影响体积，弱网下差别明显）：

    * 近纯文字页：灰度二值化 + **无损 WebP**，446 KB 的 150DPI 页面约降到 45–60 KB，
      浏览器再按 CSS 尺寸缩放，肉眼几乎无差异；
    * 含彩色图表的页面：缩放后 **有损 WebP**，避免把图表的渐变/配色打坏。
    """
    import os

    from PIL import Image

    with Image.open(src) as raw:
        image = raw.convert("RGB")
        tmp = out.with_suffix(out.suffix + ".tmp")
        if target == "webp" and _is_text_page(image):
            import numpy as np

            gray = np.asarray(image.convert("L"))
            binary = ((gray > 200) * 255).astype("uint8")
            Image.fromarray(binary).save(tmp, "WEBP", lossless=True, method=4)
        else:
            if image.width > width:
                height = max(1, round(image.height * width / image.width))
                image = image.resize((width, height), Image.LANCZOS)
            if target == "webp":
                image.save(tmp, "WEBP", quality=quality, method=6)
            else:
                image.save(tmp, "JPEG", quality=quality, optimize=True, progressive=True)
    os.replace(tmp, out)


def _is_text_page(image) -> bool:
    """粗略判断页面是否「近纯文字」：几乎无彩色，且中间灰度像素占比低（无照片/渐变）。"""
    import numpy as np

    rgb = np.asarray(image.resize((200, max(1, round(image.height * 200 / image.width)))))
    saturation = (rgb.max(axis=2) - rgb.min(axis=2)) > 24
    if float(saturation.mean()) > 0.002:
        return False
    gray = np.asarray(image.convert("L"))
    midtone = ((gray > 60) & (gray < 200)).mean()
    return float(midtone) < 0.12


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
