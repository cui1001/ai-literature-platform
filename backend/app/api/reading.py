"""阅读 API：版式文档、标题树、引用索引、翻译与四种导出。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Query
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response

from ..core.config import get_settings
from ..core.errors import NotFoundError
from ..reading import service as reading

router = APIRouter(prefix="/api/papers/{paper_id}", tags=["reading"])


@router.get("/document")
async def document(paper_id: str, lang: str = "en", translations: bool = True) -> dict[str, Any]:
    return reading.document(paper_id, lang=lang, include_translations=translations, settings=get_settings())


@router.get("/outline")
async def outline(paper_id: str) -> dict[str, Any]:
    return reading.outline(paper_id, settings=get_settings())


@router.get("/citations")
async def citations(paper_id: str) -> dict[str, Any]:
    return reading.citations(paper_id, settings=get_settings())


@router.post("/translate")
async def translate(paper_id: str, payload: dict[str, Any] = Body(default=None)) -> dict[str, Any]:
    payload = payload or {}
    return reading.translate(
        paper_id,
        lang=str(payload.get("lang") or "zh"),
        block_ids=payload.get("block_ids"),
        only_missing=bool(payload.get("only_missing", False)),
        settings=get_settings(),
    )


@router.post("/export")
async def create_export(paper_id: str, payload: dict[str, Any] = Body(default=None)) -> dict[str, Any]:
    payload = payload or {}
    fmt = str(payload.get("format") or "md")
    return reading.export(
        paper_id,
        fmt=fmt,
        lang=str(payload.get("lang") or "zh"),
        translate=bool(payload.get("translate", True)),
        settings=get_settings(),
    )


@router.get("/export/{fmt}")
async def download_export(paper_id: str, fmt: str, lang: str = "zh", translate: bool = True) -> Response:
    info = reading.export(paper_id, fmt=fmt, lang=lang, translate=translate, settings=get_settings())
    path = info["path"]
    if not path:
        raise NotFoundError("导出失败：未生成文件")
    media = {
        "md": "text/markdown; charset=utf-8",
        "html": "text/html; charset=utf-8",
        "bilingual": "text/html; charset=utf-8",
        "pdf": "application/pdf",
    }.get(info["format"], "application/octet-stream")
    coverage = {
        "X-Translated-Blocks": str(info.get("translated_blocks", 0)),
        "X-Translatable-Blocks": str(info.get("translatable_blocks", 0)),
        "X-Translation-Partial": "1" if info.get("partial") else "0",
        "Access-Control-Expose-Headers": "X-Translated-Blocks, X-Translatable-Blocks, X-Translation-Partial, Content-Disposition",
    }
    if info["format"] == "pdf":
        return FileResponse(path, media_type=media, filename=info["filename"], headers=coverage)
    text = open(path, encoding="utf-8").read()
    headers = {"Content-Disposition": f'attachment; filename="{info["filename"]}"', **coverage}
    if info["format"] in {"html", "bilingual"}:
        return HTMLResponse(text, headers=headers)
    return PlainTextResponse(text, media_type=media, headers=headers)
