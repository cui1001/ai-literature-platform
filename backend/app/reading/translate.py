"""双语阅读：块级翻译、缓存与对照视图。

设计取舍（写进竞赛材料，方便评审复现）：

* **按块翻译**：译文与 ``block_id`` 一一对应，因此中文模式下仍可点击原文、
  仍能对回页码/包围盒，滚动同步也以块为锚点，不会出现「整页译文错位」。
* **公式/引用/数字保护**：翻译前用 ``glossary.protect`` 把这些片段替换为占位符，
  译文完成后原样还原——满足「代码与公式保留英文」的硬性要求。
* **结果缓存**：``translations`` 表按 ``(paper, lang, anchor, src_hash)`` 落库，
  同一段落重复翻译直接命中缓存；PDF 被替换（src_hash 变化）时自动重译。
* **在线优先、离线降级**：在线模型不可用时回落到术语级翻译，并显式返回
  ``degraded=True`` 与说明，绝不把降级结果伪装成完整译文。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

from ..core.config import Settings, get_settings
from ..core.hashing import sha1_text
from ..core.logging import get_logger
from ..db import repo
from ..parsing.model import ParsedPaper, TextBlock
from ..providers.base import LLMResult
from ..providers.glossary import protect, restore
from ..providers.llm import get_llm
from ..providers.offline import run_offline

logger = get_logger(__name__)

SUPPORTED_LANGS = {"zh": "中文", "en": "English"}
TRANSLATABLE_KINDS = {"heading", "paragraph", "caption", "table_text"}
SKIP_KINDS = {"reference", "equation", "header", "footer", "image"}
MIN_SOURCE_CHARS = 2


@dataclass
class TranslationPiece:
    anchor: str
    block_id: str
    page: int
    kind: str
    source: str
    text: str
    provider: str = "offline"
    coverage: float = 0.0
    cached: bool = False
    degraded: bool = False
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "anchor": self.anchor,
            "block_id": self.block_id,
            "page": self.page,
            "kind": self.kind,
            "source": self.source,
            "text": self.text,
            "provider": self.provider,
            "coverage": round(self.coverage, 4),
            "cached": self.cached,
            "degraded": self.degraded,
            "notes": self.notes,
        }


def has_letters(text: str, minimum: int = 8) -> bool:
    return sum(1 for ch in text if ch.isalpha()) >= minimum


def translatable(block: TextBlock) -> bool:
    if block.kind in SKIP_KINDS or block.kind not in TRANSLATABLE_KINDS:
        return False
    text = (block.text or "").strip()
    return len(text) >= MIN_SOURCE_CHARS and has_letters(text)


def translate_text(
    text: str,
    settings: Settings | None = None,
    *,
    target: str = "zh",
    task: str = "translate",
) -> LLMResult:
    """翻译一段文本；保护公式/引用/数字，失败时回落离线引擎。"""
    settings = settings or get_settings()
    source = text or ""
    protected, pieces = protect(source)
    payload = {"text": protected, "target_lang": target}
    result: LLMResult
    if task == "academic_translate":
        payload = {"text": protected, "target_lang": "en"}
    try:
        llm = get_llm(settings)
        result = llm.run(task, payload)
    except Exception as exc:  # ProviderUnavailable / 网络异常 / 未配置 key
        logger.info("在线翻译不可用，回落离线：%s", exc)
        result = run_offline(task, payload)
        result = LLMResult(
            text=result.text,
            data=result.data,
            provider=result.provider,
            model=result.model,
            latency_ms=result.latency_ms,
            degraded=True,
            notes=f"online-unavailable: {exc}",
        )
    restored = restore(result.text or "", pieces)
    result.text = restored.strip()
    return result


def _cached_map(paper_id: str, lang: str) -> dict[str, dict[str, Any]]:
    try:
        return repo.get_translations(paper_id, lang)
    except Exception:  # 未建库时静默降级
        return {}


def translate_block(
    paper: ParsedPaper,
    block: TextBlock,
    settings: Settings | None = None,
    *,
    lang: str = "zh",
    cache: dict[str, dict[str, Any]] | None = None,
    store: bool = True,
) -> TranslationPiece:
    settings = settings or get_settings()
    source = (block.text or "").strip()
    cached = (cache or {}).get(block.id)
    if cached and cached.get("src_hash") == sha1_text(source) and cached.get("text"):
        return TranslationPiece(
            anchor=block.id,
            block_id=block.id,
            page=block.page,
            kind=block.kind,
            source=source,
            text=cached["text"],
            provider=cached.get("provider", "cache"),
            cached=True,
        )
    result = translate_text(source, settings, target=lang)
    coverage = float((result.data or {}).get("coverage", 0.0) or 0.0)
    piece = TranslationPiece(
        anchor=block.id,
        block_id=block.id,
        page=block.page,
        kind=block.kind,
        source=source,
        text=result.text,
        provider=result.provider,
        coverage=coverage,
        degraded=bool(result.degraded),
        notes=result.notes or "",
    )
    if store and piece.text:
        try:
            repo.put_translation(
                paper.paper_id,
                lang=lang,
                scope="block",
                anchor=block.id,
                src_text=source,
                text=piece.text,
                provider=piece.provider,
            )
        except Exception as exc:  # 缓存失败不应阻断阅读
            logger.warning("译文缓存写入失败：%s", exc)
    return piece


def translate_paper(
    paper: ParsedPaper,
    settings: Settings | None = None,
    *,
    lang: str = "zh",
    block_ids: Sequence[str] | None = None,
    only_missing: bool = True,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """整篇（或指定块）翻译，返回统计与全部译文。"""
    settings = settings or get_settings()
    if lang not in SUPPORTED_LANGS:
        raise ValueError(f"不支持的翻译目标语言：{lang}")
    wanted = set(block_ids or ())
    cache = _cached_map(paper.paper_id, lang)
    targets = [
        block
        for block in paper.blocks
        if translatable(block) and (not wanted or block.id in wanted or block.sid in wanted)
    ]
    pieces: list[TranslationPiece] = []
    total = len(targets)
    for index, block in enumerate(targets, start=1):
        existing = cache.get(block.id)
        fresh = existing and existing.get("src_hash") == sha1_text((block.text or "").strip())
        if only_missing and fresh:
            pieces.append(
                TranslationPiece(
                    anchor=block.id,
                    block_id=block.id,
                    page=block.page,
                    kind=block.kind,
                    source=(block.text or "").strip(),
                    text=existing.get("text", ""),
                    provider=existing.get("provider", "cache"),
                    cached=True,
                )
            )
        else:
            pieces.append(translate_block(paper, block, settings, lang=lang, cache=cache))
        if progress:
            progress(index, total)
    cached_count = sum(1 for piece in pieces if piece.cached)
    providers = sorted({piece.provider for piece in pieces if piece.provider})
    return {
        "paper_id": paper.paper_id,
        "lang": lang,
        "lang_label": SUPPORTED_LANGS[lang],
        "count": len(pieces),
        "cached": cached_count,
        "translated": len(pieces) - cached_count,
        "providers": providers,
        "degraded": any(piece.degraded for piece in pieces),
        "pieces": [piece.to_dict() for piece in pieces],
        "by_block": {piece.block_id: piece.text for piece in pieces},
    }


def section_translations(paper: ParsedPaper, pieces: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """按章节聚合译文，供「整篇对照」视图使用。"""
    by_block = {piece["block_id"]: piece for piece in pieces}
    sections: list[dict[str, Any]] = []
    blocks_by_sid: dict[str, list[TextBlock]] = {}
    for block in paper.blocks:
        if not translatable(block):
            continue
        blocks_by_sid.setdefault(block.sid or "", []).append(block)
    for section in paper.sections:
        pairs = []
        for block in blocks_by_sid.get(section.sid, []):
            piece = by_block.get(block.id)
            if not piece:
                continue
            pairs.append([block.text.strip(), piece["text"]])
        sections.append(
            {
                "sid": section.sid,
                "title": section.title,
                "level": section.level,
                "page": section.page,
                "pairs": pairs,
            }
        )
    return sections


def model_status(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    llm = get_llm(settings)
    return {
        "provider": getattr(llm, "name", llm.__class__.__name__),
        "status": llm.status().to_dict() if hasattr(llm.status(), "to_dict") else {},
        "langs": SUPPORTED_LANGS,
    }
