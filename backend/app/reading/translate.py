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

import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
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
from ..providers.translation import status as translation_status
from ..providers.translation import translate_online

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


def _cache_tag(settings: Settings, target: str) -> str:
    """缓存签名说明：用于判断旧译文是否需要重译（见 _cache_valid）。"""
    mode = (settings.translate_mode or "auto").lower()
    key = "llm" if settings.has_llm_credentials() else "nollm"
    return f"{mode}|{target}|{key}"


GLOSSARY_PROVIDERS = {"offline", "glossary"}
NUMBER_RE = re.compile(r"\d+(?:\.\d+)*")
IDENT_NUMBER_RE = re.compile(r"(?<=[A-Za-z])-?(\d+(?:\.\d+)*)")


def _numbers_kept(source: str, text: str, threshold: float = 0.6) -> bool:
    """译文是否保留原文的关键数字（防止 GPT-3 -> GPT-1、表 1 -> 表 0、arXiv 编号被截断）。

    三条规则：
    1. 小数（含版本号、BLEU、arXiv 编号）必须原样保留；
    2. 紧跟字母的编号（GPT-3 / BERT-1 / V100）必须原样保留；
    3. 其余普通整数按覆盖率判断（允许 175 billion -> 1750 亿 这类单位换算）。
    """
    src = source or ""
    out = text or ""
    src_dec = {n for n in NUMBER_RE.findall(src.replace(",", "")) if "." in n}
    out_dec = {n for n in NUMBER_RE.findall(out.replace(",", "")) if "." in n}
    if src_dec - out_dec:
        return False
    src_ident = set(IDENT_NUMBER_RE.findall(src))
    out_ident = set(IDENT_NUMBER_RE.findall(out))
    if src_ident - out_ident:
        return False
    wanted = Counter(NUMBER_RE.findall(src.replace(",", "")))
    if not wanted:
        return True
    got = Counter(NUMBER_RE.findall(out.replace(",", "")))
    kept = sum((wanted & got).values())
    return kept / max(sum(wanted.values()), 1) >= threshold


def _cache_valid(cached: dict[str, Any] | None, source: str, settings: Settings) -> bool:
    """缓存是否可用：原文未变，且通道不降级、关键数字未丢失。

    早期版本在离线模式写入的是术语级译文（provider=offline）。启用机器翻译后
    若继续命中，用户会看到「英文里夹几个中文词」，因此非 offline 模式下遇到
    术语级缓存一律视为过期，强制重译。
    """
    if not cached or not cached.get("text"):
        return False
    if cached.get("src_hash") != sha1_text(source):
        return False
    if not _numbers_kept(source, cached.get("text", "")):
        return False
    mode = (settings.translate_mode or "auto").lower()
    if mode == "offline":
        return True
    return (cached.get("provider") or "") not in GLOSSARY_PROVIDERS


def _translate_once(
    protected: str,
    settings: Settings,
    *,
    target: str,
    task: str,
) -> LLMResult:
    """翻译一段已做占位保护的文本：在线模型 → 免密钥机器翻译 → 术语辅助。"""
    mode = (settings.translate_mode or "auto").lower()
    payload = {"text": protected, "target_lang": "en" if task == "academic_translate" else target}
    reasons: list[str] = []

    if mode != "offline":
        # 1) 在线大模型（显式 online/auto 且配置了 key；offline 模式直接跳过）
        if settings.llm_mode != "offline" and settings.has_llm_credentials():
            try:
                result = get_llm(settings).run(task, payload)
                if (result.provider or "") not in GLOSSARY_PROVIDERS:
                    return result
                reasons.append(f"llm: 已降级为 {result.provider}")
            except Exception as exc:  # 网络/额度/超时都继续往下走
                reasons.append(f"llm: {exc}")
                logger.info("在线模型翻译失败，尝试机器翻译：%s", exc)
        # 2) 免密钥机器翻译（整句翻译，非术语替换）
        try:
            mt = translate_online(protected, target=payload["target_lang"], timeout=settings.translate_timeout)
            return LLMResult(
                text=mt.text,
                data={"coverage": 0.0, "engine": mt.provider},
                provider=mt.provider,
                model="free-mt",
                latency_ms=mt.latency_ms,
                degraded=False,
                notes=mt.notes,
            )
        except Exception as exc:
            reasons.append(f"mt: {exc}")
            logger.info("机器翻译通道不可用，回落术语辅助：%s", exc)

    # 3) 离线术语级兜底（明确标注，不伪装成完整译文）
    result = run_offline(task, payload)
    note = "offline-glossary: 术语级辅助翻译（非整句机器翻译）"
    if reasons:
        note += "；" + "; ".join(reasons[:2])
    return LLMResult(
        text=result.text,
        data=result.data,
        provider=result.provider,
        model=result.model,
        latency_ms=result.latency_ms,
        degraded=True,
        notes=note,
    )


def _clean_placeholders(text: str) -> tuple[str, bool]:
    """清除机器翻译残留的占位符（[[n]] / {n}），返回 (文本, 是否清理过)。"""
    cleaned, count = re.subn(r"\[\[\d+\]\]|\{\d{1,3}\}", "", text)
    return cleaned, bool(count)


def translate_text(
    text: str,
    settings: Settings | None = None,
    *,
    target: str = "zh",
    task: str = "translate",
) -> LLMResult:
    """翻译一段文本；保护公式/引用/数字，按通道依次降级。

    数字校验不通过时重试一次；仍不通过则回退术语级（术语级不改写数字），
    避免把「GPT-3 变成 GPT-1」这类错误译文当作可信结果返回。
    """
    settings = settings or get_settings()
    source = text or ""
    protected, pieces = protect(source)

    def attempt() -> LLMResult:
        res = _translate_once(protected, settings, target=target, task=task)
        out = restore(res.text or "", pieces).strip()
        out, cleaned = _clean_placeholders(out)
        if cleaned:
            res.degraded = True
            res.notes = ((res.notes or "") + "；placeholder-cleaned").strip("；")
        res.text = out
        return res

    result = attempt()
    if not _numbers_kept(source, result.text, threshold=0.9):
        retry = attempt()
        if _numbers_kept(source, retry.text, threshold=0.9):
            return retry
        fallback = run_offline(task, {"text": source, "target_lang": target})
        return LLMResult(
            text=(fallback.text or "").strip(),
            data=fallback.data,
            provider="offline",
            model=fallback.model,
            latency_ms=fallback.latency_ms,
            degraded=True,
            notes="offline-glossary: 在线译文未通过数字一致性校验，已回退术语级（不改写数字）",
        )
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
    if _cache_valid(cached, source, settings):
        cached = cached or {}
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
    # 标题的章节编号单独处理：翻译正文（去掉编号），再原样拼回，避免「1 Introduction → 简介」
    prefix = ""
    payload_text = source
    if block.kind == "heading" and block.number and source.startswith(block.number):
        prefix = block.number + " "
        payload_text = source[len(block.number):].strip()
    result = translate_text(payload_text, settings, target=lang)
    text = (result.text or "").strip()
    if prefix and not text.startswith(block.number):
        text = prefix + text
    coverage = float((result.data or {}).get("coverage", 0.0) or 0.0)
    piece = TranslationPiece(
        anchor=block.id,
        block_id=block.id,
        page=block.page,
        kind=block.kind,
        source=source,
        text=text,
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
    # 首页 Abstract 以上的标题/作者/机构属于元信息，翻译意义不大（人名也不该被译）
    abstract_top = next(
        (
            block.bbox.y0
            for block in paper.blocks
            if block.kind == "heading" and (block.text or "").strip().lower().startswith("abstract")
        ),
        None,
    )
    targets = [
        block
        for block in paper.blocks
        if translatable(block)
        and not (abstract_top is not None and block.page == 1 and block.bbox.y1 <= abstract_top)
        and (not wanted or block.id in wanted or block.sid in wanted)
    ]
    total = len(targets)

    def one(block: TextBlock) -> TranslationPiece:
        existing = cache.get(block.id)
        fresh = _cache_valid(existing, (block.text or "").strip(), settings)
        if only_missing and fresh:
            return TranslationPiece(
                anchor=block.id,
                block_id=block.id,
                page=block.page,
                kind=block.kind,
                source=(block.text or "").strip(),
                text=existing.get("text", ""),
                provider=existing.get("provider", "cache"),
                cached=True,
            )
        return translate_block(paper, block, settings, lang=lang, cache=cache)

    # 在线机器翻译是逐块 HTTP 请求，串行会把整篇翻译拖到几分钟；并发 3~4 路即可
    # 在几十秒内完成，同时对免费端点保持温和的请求速率。
    mode = (settings.translate_mode or "auto").lower()
    workers = 0 if mode == "offline" else (3 if total >= 18 else (2 if total >= 8 else 1))
    pieces: list[TranslationPiece] = []
    if workers > 1 and total > 12:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(one, block) for block in targets]
            for index, future in enumerate(futures, start=1):
                pieces.append(future.result())
                if progress:
                    progress(index, total)
    else:
        for index, block in enumerate(targets, start=1):
            pieces.append(one(block))
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
        "mode": (settings.translate_mode or "auto").lower(),
        "engines": translation_status()["engines"],
        "chain": ["online-llm", "free-mt(google/mymemory)", "offline-glossary"],
    }
