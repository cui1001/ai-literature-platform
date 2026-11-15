"""阅读器服务：加载解析结果、拼装版式 JSON、调用翻译与导出。

API 层只依赖本模块，避免把 PyMuPDF/LLM 细节散落到路由里。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Sequence

from ..core.config import Settings, get_settings
from ..core.errors import NotFoundError
from ..db import repo
from ..parsing.model import ParsedPaper
from ..parsing.pipeline import load_parsed
from . import export as exporter
from .layout import document_json, heading_tree
from .links import backlinks, link_map, citation_occurrences
from .translate import (
    annotate_translation_status,
    cached_translation_valid,
    section_translations,
    skip_reason,
    translate_paper,
    translatable_blocks,
    translatable_kind,
)

PAGE_IMAGE_NAME = "pages"


def load_paper(paper_id: str, settings: Settings | None = None) -> ParsedPaper:
    settings = settings or get_settings()
    data = load_parsed(paper_id, settings)
    if not data:
        raise NotFoundError(f"论文 {paper_id} 尚未解析或解析缓存不存在")
    return ParsedPaper.from_dict(data)


def page_image_path(paper_id: str, page: int, settings: Settings | None = None) -> Path:
    from ..parsing.pipeline import page_image_path as _path

    settings = settings or get_settings()
    path = _path(paper_id, int(page), settings)
    if not path.exists():
        raise NotFoundError(f"论文 {paper_id} 第 {page} 页的渲染图不存在")
    return path


def translations_map(paper_id: str, lang: str = "zh") -> dict[str, dict[str, Any]]:
    try:
        return repo.get_translations(paper_id, lang)
    except Exception:
        return {}


def document(
    paper_id: str,
    settings: Settings | None = None,
    *,
    lang: str | None = None,
    include_translations: bool = True,
) -> dict[str, Any]:
    """阅读器主接口：版式 + 标题树 + 引用双向索引（+ 可选双语）。"""
    settings = settings or get_settings()
    paper = load_paper(paper_id, settings)
    prefix = f"/api/papers/{paper_id}/pages/"
    translations = translations_map(paper_id, lang) if (lang and include_translations) else {}
    if translations:
        # P1：旧缓存（如 175 billion 被译成 175 亿）在读取时即判无效，不再展示错误数字
        blocks = {block.id: block for block in paper.blocks}
        translations = {
            anchor: item
            for anchor, item in translations.items()
            if anchor in blocks
            and cached_translation_valid(item, (blocks[anchor].text or "").strip(), settings)
        }
    doc = document_json(
        paper,
        image_prefix=prefix,
        translations=translations,
        backlinks=backlinks(paper),
    )
    annotate_translation_status(paper, doc)
    doc["links"] = link_map(paper)
    doc["translation"] = {
        "lang": lang or "",
        "count": len(translations),
        "providers": sorted({item.get("provider", "") for item in translations.values() if item.get("provider")}),
    }
    return doc


def translate(
    paper_id: str,
    settings: Settings | None = None,
    *,
    lang: str = "zh",
    block_ids: Sequence[str] | None = None,
    only_missing: bool = True,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    paper = load_paper(paper_id, settings)
    result = translate_paper(
        paper,
        settings,
        lang=lang,
        block_ids=block_ids,
        only_missing=only_missing,
        progress=progress,
    )
    result["sections"] = section_translations(paper, result["pieces"])
    return result


def export(
    paper_id: str,
    fmt: str,
    settings: Settings | None = None,
    *,
    lang: str = "zh",
    translate: bool = True,
    output: str | Path | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    paper = load_paper(paper_id, settings)
    blocks = {block.id: block for block in paper.blocks}
    raw_translations = translations_map(paper_id, lang)
    translations = {
        anchor: item.get("text", "")
        for anchor, item in raw_translations.items()
        if anchor in blocks
        and cached_translation_valid(item, (blocks[anchor].text or "").strip(), settings)
    }
    if translate and lang != "en" and not translations:
        # 导出前按需补翻译（有缓存时几乎不耗时；避免导出英文占位却不自知）
        try:
            translate_paper(paper, settings, lang=lang)
            raw_translations = translations_map(paper_id, lang)
            translations = {
                anchor: item.get("text", "")
                for anchor, item in raw_translations.items()
                if anchor in blocks
                and cached_translation_valid(item, (blocks[anchor].text or "").strip(), settings)
            }
        except Exception:  # 翻译失败仍导出原文，不让导出整体失败
            translations = {}
    targets = translatable_blocks(paper)
    translated_count = sum(1 for block in targets if translations.get(block.id))
    path = exporter.export_paper(
        paper,
        fmt,
        output=output,
        translations=translations,
        lang=lang,
        settings=settings,
    )
    return {
        "format": fmt,
        "path": str(path),
        "filename": path.name,
        "size": path.stat().st_size if path.exists() else 0,
        "lang": lang,
        "translated_blocks": translated_count,
        "translatable_blocks": len(targets),
        "partial": bool(targets) and translated_count < len(targets),
    }


def outline(paper_id: str, settings: Settings | None = None, *, max_level: int = 3) -> list[dict[str, Any]]:
    paper = load_paper(paper_id, settings)
    return heading_tree(paper, max_level=max_level)


def citations(paper_id: str, settings: Settings | None = None) -> dict[str, Any]:
    paper = load_paper(paper_id, settings)
    return {
        "links": link_map(paper),
        "occurrences": citation_occurrences(paper),
    }
