"""论文检索、追踪与智能推荐（竞赛模块四）。

设计要点
--------
* 检索/导入统一走 :mod:`app.providers.arxiv`：在线 Atom API，失败回落
  ``fixtures/arxiv/mirror.json``；本模块不新增任何网络依赖。
* 离线导入在 ``fixtures/papers/`` 中按 arXiv id / 标题相似度寻找本地 PDF，
  再交给 :func:`app.papers.service.ingest_bytes` 完成解析、入库与索引。
* 推荐不使用大模型：本地库主题词 + discovery 候选 + feedback 表即可离线复现；
  所有排序都稳定（分数降序 + arxiv_id 升序），便于单元测试断言。
* 追踪器用 ``track_state`` 记录上次运行时间，在
  ``settings.tracker_interval_minutes`` 内幂等跳过（除非 ``force=True``）。
"""

from __future__ import annotations

import json
import math
import re
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

from ..core.clock import parse_iso, utc_now, utc_now_iso
from ..core.config import Settings, get_settings
from ..core.errors import ValidationError
from ..core.hashing import short_id
from ..core.logging import get_logger
from ..core.textutil import (
    ARXIV_ID_RE,
    jaccard,
    normalize_title,
    normalize_ws,
    text_similarity,
    tokens,
    truncate,
)
from ..db import repo
from ..providers.arxiv import ArxivEntry, get_arxiv, relevance_score

log = get_logger("discovery")

#: ``prefer_code=True`` 时给附带开源仓库的论文额外加权
CODE_BONUS = 0.08
#: track_state 中记录追踪器上次运行时间的 key
TRACKER_STATE_KEY = "discovery:tracker:last_run"
FEEDBACK_ACTIONS = ("like", "dislike", "save", "hide")

_MAX_LOCAL_PAPERS = 200
_MAX_DISCOVERY = 1000


# --------------------------------------------------------------------------- 工具


def _settings(settings: Settings | None) -> Settings:
    return settings or get_settings()


def _clamp(value: int | float, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = low
    return max(low, min(high, number))


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def _bare(arxiv_id: str | None) -> str:
    """去掉版本号：``2401.00001v2 -> 2401.00001``。"""
    return re.sub(r"v\d+$", "", (arxiv_id or "").strip(), flags=re.I)


def _normalize_arxiv_identifier(value: str) -> str:
    """把 ``arXiv:2401.00001v2`` / arXiv URL / 裸 id 统一成裸 id（保留版本号）。"""
    text = normalize_ws(str(value or ""))
    text = re.sub(r"^https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/", "", text, flags=re.I)
    text = re.sub(r"\.pdf$", "", text, flags=re.I)
    text = re.sub(r"^arxiv\s*:\s*", "", text, flags=re.I)
    match = ARXIV_ID_RE.fullmatch(text)
    return match.group(0) if match else ""


def _add_note(notes: list[str], text: str) -> None:
    if text and text not in notes:
        notes.append(text)


def _year_from_text(value: str | None) -> int:
    match = re.match(r"(\d{4})", str(value or ""))
    return int(match.group(1)) if match else 0


def _category_matches(categories: Sequence[str], primary: str, category: str | None) -> bool:
    if not category:
        return True
    wanted = category.strip().lower()
    if not wanted:
        return True
    values = {str(c or "").strip().lower() for c in (*categories, primary) if str(c or "").strip()}
    if not values:
        # 元数据缺失时不误杀，交给上层展示
        return True
    return any(value == wanted or value.startswith(wanted + ".") for value in values)


# --------------------------------------------------------------------------- 本地库画像


def _library_context() -> dict[str, Any]:
    """一次性读取本地文献库，构建 token → 文献集合的轻量索引。"""
    papers = repo.list_papers(limit=_MAX_LOCAL_PAPERS) or []
    token_docs: dict[str, set[str]] = {}
    df: Counter[str] = Counter()
    keywords: Counter[str] = Counter()
    arxiv_map: dict[str, str] = {}
    for index, paper in enumerate(papers):
        paper_id = str(paper.get("id") or f"p{index}")
        raw_keywords = paper.get("keywords") or []
        if isinstance(raw_keywords, str):
            raw_keywords = [raw_keywords]
        keyword_text = " ".join(str(k) for k in raw_keywords if str(k).strip())
        text = " ".join(
            [
                str(paper.get("title") or ""),
                str(paper.get("abstract") or ""),
                keyword_text,
            ]
        )
        terms = set(tokens(text))
        for term in terms:
            token_docs.setdefault(term, set()).add(paper_id)
        df.update(terms)
        for keyword in raw_keywords:
            cleaned = normalize_ws(str(keyword or "")).lower()
            if cleaned:
                keywords[cleaned] += 1
        arxiv_id = str(paper.get("arxiv_id") or "").strip()
        if arxiv_id:
            arxiv_map[arxiv_id.lower()] = paper_id
            arxiv_map[_bare(arxiv_id).lower()] = paper_id
    return {
        "papers": papers,
        "token_docs": token_docs,
        "df": df,
        "keywords": keywords,
        "arxiv": arxiv_map,
        "terms": set(token_docs),
    }


def _is_profile_term(term: str) -> bool:
    if any("\u4e00" <= ch <= "\u9fff" for ch in term):
        return len(term) >= 2
    return len(term) >= 3


def _library_profile(library: dict[str, Any]) -> dict[str, list[str]]:
    terms = sorted(library["terms"], key=lambda term: (-library["df"].get(term, 0), term))
    terms = [term for term in terms if _is_profile_term(term)][:16]
    topics = [
        keyword
        for keyword, _ in sorted(library["keywords"].items(), key=lambda item: (-item[1], item[0]))
    ][:6]
    for term in terms:
        if len(topics) >= 6:
            break
        if term not in topics:
            topics.append(term)
    return {"topics": topics[:6], "terms": terms}


def _entry_terms(entry: ArxivEntry) -> set[str]:
    return set(tokens(f"{entry.title} {entry.abstract}"))


def _local_match(entry: ArxivEntry, library: dict[str, Any]) -> tuple[float, list[str], int]:
    """返回 (重叠比例, 命中词, 本地相关文献数)。"""
    terms = _entry_terms(entry)
    matched = [term for term in terms if term in library["terms"]]
    if not matched:
        return 0.0, [], 0
    matched.sort(key=lambda term: (-len(library["token_docs"].get(term, ())), term))
    docs: set[str] = set()
    for term in matched:
        docs |= library["token_docs"].get(term, set())
    ratio = min(1.0, len(matched) / max(1, min(len(terms), 12)))
    return round(ratio, 4), matched[:6], len(docs)


# --------------------------------------------------------------------------- 反馈


def _feedback_context(user_id: str) -> dict[str, dict[str, Any]]:
    """把 feedback 行折叠成每个 target 的最新状态。"""
    rows = repo.get_feedback(user_id) or []
    state: dict[str, dict[str, Any]] = {}
    for row in rows:
        target = str(row.get("target") or "").strip()
        action = str(row.get("action") or "").strip().lower()
        if not target or action not in FEEDBACK_ACTIONS:
            continue
        ts = str(row.get("ts") or "")
        try:
            weight = float(row.get("weight") or 1.0)
        except (TypeError, ValueError):
            weight = 1.0
        info = state.setdefault(
            target,
            {
                "latest": "",
                "latest_ts": "",
                "sentiment": "",
                "sentiment_ts": "",
                "sentiment_weight": 1.0,
                "saved": False,
                "hidden": False,
            },
        )
        if ts >= info["latest_ts"]:
            info["latest"] = action
            info["latest_ts"] = ts
        if action in {"like", "dislike"} and ts >= info["sentiment_ts"]:
            info["sentiment"] = action
            info["sentiment_ts"] = ts
            info["sentiment_weight"] = weight
        if action == "save":
            info["saved"] = True
    for info in state.values():
        info["hidden"] = info["latest"] == "hide"
    return state


def _feedback_for(state: dict[str, dict[str, Any]], *targets: str) -> dict[str, Any] | None:
    if not state:
        return None
    wanted: set[str] = set()
    for target in targets:
        cleaned = normalize_ws(str(target or "")).lower()
        if cleaned:
            wanted.add(cleaned)
            wanted.add(_bare(cleaned))
    if not wanted:
        return None
    for key, info in state.items():
        lowered = key.lower()
        if lowered in wanted or _bare(lowered) in wanted:
            return info
    return None


# --------------------------------------------------------------------------- 检索


def _entry_reason(
    entry: ArxivEntry,
    query: str,
    library: dict[str, Any],
    *,
    imported: bool,
    liked: bool,
    disliked: bool,
    score: float,
    offline: bool,
) -> str:
    """基于可核实的证据生成中文推荐理由，不编造引用量等未提供的事实。"""
    parts: list[str] = []
    if imported:
        parts.append("已在本地文献库中")
    ratio, matched, doc_count = _local_match(entry, library)
    if matched:
        joined = "、".join(matched[:4])
        if doc_count:
            parts.append(f"本地库中 {doc_count} 篇文献涉及相同主题（{joined}）")
        else:  # pragma: no cover - 有命中词必然有相关文献
            parts.append(f"与本地库共享关键词：{joined}")
    if entry.has_code:
        parts.append("附开源代码仓库，可复现验证")
    current_year = utc_now().year
    if entry.year and entry.year >= current_year - 2:
        parts.append(f"{entry.year} 年发表，时效性较好")
    title_hits = sorted(set(tokens(query)) & set(tokens(entry.title)))
    if title_hits and len(parts) < 4:
        parts.append(f"标题命中检索词：{'、'.join(title_hits[:4])}")
    if entry.primary_category and len(parts) < 4:
        parts.append(f"arXiv 分类 {entry.primary_category}")
    if liked:
        parts.append("你已点赞过该文献")
    elif disliked:
        parts.append("你标记过不感兴趣")
    if not parts:
        channel = "离线镜像" if offline else "arXiv"
        parts.append(f"{channel}检索相关性 {score:.2f}，按查询相似度返回")
    return "；".join(parts) + "。"


def search(
    query: str,
    *,
    max_results: int = 19,
    category: str | None = None,
    prefer_code: bool = True,
    user_id: str = "local",
    settings: Settings | None = None,
) -> dict[str, Any]:
    """arXiv 检索 + 本地库相关性 + 代码可得性加权。"""
    s = _settings(settings)
    cleaned = normalize_ws(str(query or ""))
    notes: list[str] = []
    client = get_arxiv(s)
    if not cleaned:
        return {
            "items": [],
            "query": cleaned,
            "provider": "offline-mirror" if s.arxiv_mode == "offline" else "arxiv-api",
            "degraded": True,
            "notes": ["查询为空，未执行检索。"],
        }

    limit = _clamp(max_results, 1, 100)
    try:
        entries = client.search(
            cleaned,
            max_results=limit,
            category=category or "",
            prefer_code=False,
            title_only=False,
        )
    except Exception as exc:  # noqa: BLE001 - 检索失败降级为空结果
        log.warning("arxiv search failed: %s", exc)
        entries = []
        _add_note(notes, f"arXiv 检索失败：{exc}")

    if category:
        before = len(entries)
        entries = [
            entry for entry in entries if _category_matches(entry.categories, entry.primary_category, category)
        ]
        if before != len(entries):
            _add_note(notes, f"已按分类 {category} 过滤 {before - len(entries)} 条结果。")
        if not entries and before:
            _add_note(notes, f"分类 {category} 下没有匹配结果。")

    library = _library_context()
    feedback = _feedback_context(user_id)
    discovery_map: dict[str, dict[str, Any]] = {}
    for row in repo.list_discovery(limit=_MAX_DISCOVERY) or []:
        discovery_map[str(row.get("arxiv_id") or "")] = row

    offline = s.arxiv_mode == "offline" or client.degraded
    items: list[dict[str, Any]] = []
    for entry in entries:
        base = relevance_score(entry, cleaned)
        ratio, matched, _docs = _local_match(entry, library)
        score = 0.72 * base + 0.28 * ratio
        if prefer_code and entry.has_code:
            score += CODE_BONUS
        score = round(max(0.0, min(1.0, score)), 4)

        row = discovery_map.get(entry.arxiv_id) or discovery_map.get(entry.bare_id) or {}
        paper_id = (
            str(row.get("imported_paper") or "")
            or library["arxiv"].get(entry.arxiv_id.lower(), "")
            or library["arxiv"].get(entry.bare_id.lower(), "")
        )
        imported = bool(paper_id)
        info = _feedback_for(feedback, entry.arxiv_id, entry.bare_id, paper_id)
        liked = bool(info and info["sentiment"] == "like")
        disliked = bool(info and info["sentiment"] == "dislike")
        items.append(
            {
                "arxiv_id": entry.arxiv_id,
                "title": entry.title,
                "abstract": entry.abstract,
                "authors": list(entry.authors),
                "year": entry.year,
                "url": entry.url or f"https://arxiv.org/abs/{entry.arxiv_id}",
                "pdf_url": entry.pdf_url or f"https://arxiv.org/pdf/{entry.arxiv_id}.pdf",
                "github": entry.github,
                "has_code": entry.has_code,
                "relevance": score,
                "reason": _entry_reason(
                    entry,
                    cleaned,
                    library,
                    imported=imported,
                    liked=liked,
                    disliked=disliked,
                    score=score,
                    offline=offline,
                ),
                "imported": imported,
                "liked": liked,
                "disliked": disliked,
            }
        )
    items.sort(key=lambda item: (-item["relevance"], item["arxiv_id"]))

    if entries:
        try:
            repo.upsert_discovery([entry.to_dict() for entry in entries])
        except Exception as exc:  # noqa: BLE001 - 写库失败不应让检索整体失败
            log.warning("persist discovery failed: %s", exc)
            _add_note(notes, f"检索结果写库失败：{exc}")

    if s.arxiv_mode == "offline":
        if client.mirror:
            _add_note(notes, f"离线镜像模式：使用 {len(client.mirror)} 条本地元数据。")
        else:
            mirror_path = Path(s.repo_root) / "fixtures" / "arxiv" / "mirror.json"
            _add_note(notes, f"离线镜像缺失（{mirror_path}），检索结果为空。")
    elif client.degraded:
        _add_note(notes, f"在线通道失败，已回落离线镜像：{client.last_error or '未知错误'}")
    if prefer_code:
        _add_note(notes, f"已对附带开源代码的论文加权 +{CODE_BONUS:.2f}。")
    if not items:
        _add_note(notes, "未检索到匹配的论文，可尝试更换关键词或放宽分类。")

    return {
        "items": items,
        "query": cleaned,
        "provider": "offline-mirror" if offline else "arxiv-api",
        "degraded": bool(offline),
        "notes": notes,
    }


# --------------------------------------------------------------------------- 导入


def _best_title_match(entries: Sequence[ArxivEntry], title: str) -> tuple[ArxivEntry | None, float]:
    wanted = normalize_title(title)
    best: ArxivEntry | None = None
    best_score = 0.0
    for entry in entries:
        candidate = normalize_title(entry.title)
        if not candidate:
            continue
        score = 1.0 if wanted == candidate else text_similarity(wanted, candidate)
        if score > best_score:
            best, best_score = entry, score
    return best, round(best_score, 4)


def _discovery_by_id(arxiv_id: str) -> dict[str, Any] | None:
    wanted = _bare(arxiv_id).lower()
    if not wanted:
        return None
    for row in repo.list_discovery(limit=_MAX_DISCOVERY) or []:
        row_id = str(row.get("arxiv_id") or "")
        if _bare(row_id).lower() == wanted:
            return row
    return None


def _discovery_by_title(title: str) -> dict[str, Any] | None:
    wanted = normalize_title(title)
    if not wanted:
        return None
    best: dict[str, Any] | None = None
    best_score = 0.0
    for row in repo.list_discovery(limit=_MAX_DISCOVERY) or []:
        score = text_similarity(wanted, normalize_title(str(row.get("title") or "")))
        if score > best_score:
            best, best_score = row, score
    return best if best_score >= 0.72 else None


def _resolve_entry(identifier: str, settings: Settings) -> tuple[ArxivEntry | None, str]:
    """把 arXiv id 或标题解析为元数据条目；失败时返回 (None, 原因)。"""
    client = get_arxiv(settings)
    arxiv_id = _normalize_arxiv_identifier(identifier)
    errors: list[str] = []
    if arxiv_id:
        bare = _bare(arxiv_id)
        entries: list[ArxivEntry] = []
        try:
            entries = client.search(bare, max_results=8)
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
        for entry in entries:
            if entry.arxiv_id.lower() == arxiv_id.lower() or entry.bare_id.lower() == bare.lower():
                return entry, ""
        row = _discovery_by_id(bare)
        if row:
            return ArxivEntry.from_dict(row), ""
        reason = f"检索通道未找到 arXiv:{arxiv_id}"
        if errors:
            reason += f"（{errors[0]}）"
        return None, reason

    for title_only in (True, False):
        entries = []
        try:
            entries = client.search(identifier, max_results=8, title_only=title_only)
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
        best, score = _best_title_match(entries, identifier)
        if best is not None and score >= 0.55:
            return best, ""
    row = _discovery_by_title(identifier)
    if row:
        return ArxivEntry.from_dict(row), ""
    reason = f"未能解析标题对应的 arXiv 条目：{truncate(identifier, 60)}"
    if errors:
        reason += f"（{errors[0]}）"
    return None, reason


def _corpus_papers(settings: Settings) -> list[dict[str, Any]]:
    path = Path(settings.repo_root) / "fixtures" / "corpus" / "papers.json"
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    papers = payload.get("papers") if isinstance(payload, dict) else payload
    if not isinstance(papers, list):
        return []
    return [paper for paper in papers if isinstance(paper, dict) and paper.get("key")]


def _fixture_score(stem: str, arxiv_id: str, title: str, identifier: str) -> tuple[float, str]:
    """给 ``fixtures/papers/<stem>.pdf`` 打分。"""
    lowered = normalize_ws(stem).lower()
    if arxiv_id:
        full = arxiv_id.lower()
        bare = _bare(full)
        if lowered == full or full in lowered:
            return 1.0, "arXiv id 精确匹配"
        if bare and bare in lowered:
            return 0.95, "arXiv id 匹配"
    for text, label in ((title, "标题"), (identifier, "标识文本")):
        if not text:
            continue
        wanted = normalize_title(text)
        current = normalize_title(stem)
        if wanted and wanted == current:
            return 0.9, f"{label}精确匹配"
        similarity = text_similarity(wanted, current)
        if similarity >= 0.45:
            return similarity, f"{label}相似度 {similarity:.2f}"
    return 0.0, ""


def _find_fixture_pdf(
    settings: Settings, *, entry: ArxivEntry | None, identifier: str
) -> tuple[Path | None, str]:
    """离线本地 PDF 兜底：先查语料元数据，再按文件名与标题相似度扫描。"""
    root = Path(settings.repo_root) / "fixtures" / "papers"
    if not root.is_dir():
        return None, f"本地 fixtures/papers 目录不存在：{root}"
    arxiv_id = entry.arxiv_id if entry is not None else _normalize_arxiv_identifier(identifier)
    title = entry.title if entry is not None else ("" if _normalize_arxiv_identifier(identifier) else identifier)
    candidates: list[tuple[float, Path, str]] = []

    for paper in _corpus_papers(settings):
        key = str(paper.get("key") or "").strip()
        path = root / f"{key}.pdf"
        if not key or not path.is_file():
            continue
        meta_id = str(paper.get("arxiv_id") or "")
        meta_title = str(paper.get("title") or "")
        score = 0.0
        why = ""
        if arxiv_id and meta_id and _bare(meta_id).lower() == _bare(arxiv_id).lower():
            score, why = 1.0, "语料元数据 arXiv id 匹配"
        elif title and meta_title:
            similarity = 1.0 if normalize_title(title) == normalize_title(meta_title) else text_similarity(
                normalize_title(title), normalize_title(meta_title)
            )
            if similarity >= 0.72:
                score, why = 0.98 * similarity, f"语料元数据标题相似度 {similarity:.2f}"
        if score > 0:
            candidates.append((score, path, why))

    for path in sorted(root.glob("*.pdf")):
        score, why = _fixture_score(path.stem, arxiv_id, title, identifier)
        if score > 0:
            candidates.append((score, path, why))

    if not candidates:
        return None, "fixtures/papers 中未找到匹配的本地 PDF"
    candidates.sort(key=lambda item: (-item[0], item[1].name))
    score, path, why = candidates[0]
    if score < 0.45:
        return None, "fixtures/papers 中未找到足够相似的本地 PDF"
    return path, f"本地 PDF 兜底：{path.name}（{why}）"


def _ingest_entry(
    data: bytes,
    *,
    filename: str,
    entry: ArxivEntry | None,
    source_label: str,
    settings: Settings,
    user_id: str,
) -> dict[str, Any]:
    from ..papers import service as paper_service

    result = paper_service.ingest_bytes(
        data,
        filename,
        settings=settings,
        source="arxiv",
        index=True,
        user_id=user_id,
    )
    paper = result.get("paper") or {}
    status = str(result.get("status") or ("imported" if paper else "failed"))
    if entry is None and paper:
        parsed_arxiv_id = str(paper.get("arxiv_id") or "").strip()
        entry = ArxivEntry(
            arxiv_id=parsed_arxiv_id,
            title=str(paper.get("title") or "Untitled"),
            authors=list(paper.get("authors") or []),
            abstract=str(paper.get("abstract") or ""),
            year=int(paper.get("year") or 0),
            url=f"https://arxiv.org/abs/{parsed_arxiv_id}" if parsed_arxiv_id else "",
            pdf_url=f"https://arxiv.org/pdf/{parsed_arxiv_id}.pdf" if parsed_arxiv_id else "",
        )
    if entry is not None and entry.arxiv_id:
        try:
            repo.upsert_discovery([entry.to_dict()])
            if paper.get("id"):
                repo.set_imported_paper(entry.arxiv_id, str(paper["id"]))
        except Exception as exc:  # noqa: BLE001 - 导入元数据写库失败不阻断入库结果
            log.warning("record imported paper failed: %s", exc)
    reason = {
        "arxiv": "已下载 arXiv PDF 并入库。",
        "fixtures": "在线不可用，已从本地 fixtures/papers 导入（离线降级）。",
    }.get(source_label, "已入库。")
    if status == "duplicate":
        reason = "文献已在本地库中，复用现有条目（未重复解析）。"
    return {
        "status": status,
        "paper": paper or None,
        "entry": entry.to_dict() if entry is not None else None,
        "reason": reason,
        "source": source_label,
        "indexed": bool(result.get("indexed")),
    }


def import_paper(
    identifier: str, *, settings: Settings | None = None, user_id: str = "local"
) -> dict[str, Any]:
    """按 arXiv id 或标题导入论文；在线失败/离线时使用本地 fixtures PDF。"""
    s = _settings(settings)
    started = time.perf_counter()
    cleaned = normalize_ws(str(identifier or ""))
    if not cleaned:
        return {
            "status": "failed",
            "paper": None,
            "entry": None,
            "reason": "未提供 arXiv id 或论文标题。",
            "source": "none",
            "elapsed_ms": _elapsed_ms(started),
        }

    errors: list[str] = []
    entry: ArxivEntry | None = None
    try:
        entry, resolve_reason = _resolve_entry(cleaned, s)
        if resolve_reason:
            errors.append(resolve_reason)
    except Exception as exc:  # noqa: BLE001 - 解析失败继续尝试本地 PDF
        errors.append(f"解析条目前失败：{exc}")

    if entry is not None and s.arxiv_mode != "offline":
        try:
            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / f"{entry.arxiv_id or 'paper'}.pdf"
                get_arxiv(s).download_pdf(entry, target)
                data = target.read_bytes()
            result = _ingest_entry(
                data,
                filename=f"{entry.arxiv_id or 'paper'}.pdf",
                entry=entry,
                source_label="arxiv",
                settings=s,
                user_id=user_id,
            )
            result["elapsed_ms"] = _elapsed_ms(started)
            return result
        except Exception as exc:  # noqa: BLE001 - 下载/入库失败后回落本地 fixtures
            errors.append(f"在线下载或入库失败：{exc}")

    fixture, fixture_reason = _find_fixture_pdf(s, entry=entry, identifier=cleaned)
    if fixture is not None:
        try:
            result = _ingest_entry(
                fixture.read_bytes(),
                filename=fixture.name,
                entry=entry,
                source_label="fixtures",
                settings=s,
                user_id=user_id,
            )
            result["elapsed_ms"] = _elapsed_ms(started)
            return result
        except Exception as exc:  # noqa: BLE001 - 本地 PDF 损坏时给出可读失败原因
            errors.append(f"本地 PDF 入库失败（{fixture.name}）：{exc}")
    elif fixture_reason:
        errors.append(fixture_reason)

    if entry is not None:
        errors.insert(0, f"已解析到条目《{truncate(entry.title, 60)}》，但没有可用的 PDF。")
    return {
        "status": "failed",
        "paper": None,
        "entry": entry.to_dict() if entry is not None else None,
        "reason": "；".join(dict.fromkeys(errors)) or "未找到可导入的 PDF。",
        "source": "none",
        "elapsed_ms": _elapsed_ms(started),
    }


def _match_local_reference(
    ref: dict[str, Any], index: Sequence[dict[str, Any]]
) -> tuple[dict[str, Any] | None, float]:
    ref_arxiv = str(ref.get("arxiv_id") or "").strip()
    if ref_arxiv:
        for paper in index:
            if str(paper.get("arxiv_id") or "") and _bare(str(paper.get("arxiv_id"))) == _bare(ref_arxiv):
                return paper, 1.0
    title = str(ref.get("title") or "").strip()
    if not title:
        return None, 0.0
    wanted = normalize_title(title)
    for paper in index:
        if normalize_title(str(paper.get("title") or "")) == wanted:
            return paper, 1.0
    best: dict[str, Any] | None = None
    best_score = 0.0
    for paper in index:
        score = text_similarity(wanted, normalize_title(str(paper.get("title") or "")))
        if score > best_score:
            best, best_score = paper, score
    return (best, round(best_score, 4)) if best_score >= 0.88 else (None, round(best_score, 4))


def import_references(
    paper_id: str, *, limit: int = 5, settings: Settings | None = None
) -> dict[str, Any]:
    """逐条处理本地论文的参考文献：先匹配本地库，再按标题/arXiv id 抓取。"""
    s = _settings(settings)
    paper = repo.get_paper(paper_id)
    if not paper:
        return {
            "attempted": 0,
            "imported": 0,
            "skipped": 0,
            "items": [],
            "reason": f"本地文献不存在：{paper_id}",
        }
    refs = repo.get_refs(paper_id)[: _clamp(limit, 1, 50)]
    if not refs:
        return {
            "attempted": 0,
            "imported": 0,
            "skipped": 0,
            "items": [],
            "reason": "该文献没有解析出参考文献。",
        }

    index = repo.paper_index()
    items: list[dict[str, Any]] = []
    imported = 0
    skipped = 0
    for ref in refs:
        ref_idx = int(ref.get("ref_idx") or len(items) + 1)
        title = str(ref.get("title") or "").strip()
        raw = str(ref.get("raw") or "").strip()
        existing = str(ref.get("matched_paper") or "").strip()
        if existing and repo.get_paper(existing):
            skipped += 1
            items.append(
                {
                    "ref_idx": ref_idx,
                    "title": title,
                    "status": "matched",
                    "paper_id": existing,
                    "reason": "已有匹配记录，跳过重复导入。",
                }
            )
            continue
        matched, score = _match_local_reference(ref, index)
        if matched is not None:
            matched_id = str(matched.get("id") or "")
            try:
                repo.update_ref(
                    paper_id,
                    ref_idx,
                    matched_paper=matched_id,
                    match_score=round(score, 3),
                    arxiv_id=matched.get("arxiv_id") or ref.get("arxiv_id") or "",
                )
            except Exception as exc:  # noqa: BLE001 - 回写失败不阻断返回
                log.warning("update ref failed: %s", exc)
            skipped += 1
            items.append(
                {
                    "ref_idx": ref_idx,
                    "title": title,
                    "status": "matched",
                    "paper_id": matched_id,
                    "reason": f"本地库已有同标题文献（相似度 {score:.2f}），未重复导入。",
                }
            )
            continue

        identifier = str(ref.get("arxiv_id") or "").strip() or title or truncate(raw, 160)
        if not identifier:
            skipped += 1
            items.append(
                {
                    "ref_idx": ref_idx,
                    "title": title,
                    "status": "skipped",
                    "paper_id": "",
                    "reason": "缺少标题/arXiv id，无法检索。",
                }
            )
            continue
        try:
            result = import_paper(identifier, settings=s)
        except Exception as exc:  # noqa: BLE001 - 单条失败不影响其余条目
            items.append(
                {
                    "ref_idx": ref_idx,
                    "title": title,
                    "status": "failed",
                    "paper_id": "",
                    "reason": f"导入异常：{exc}",
                }
            )
            continue
        result_paper = result.get("paper") or {}
        result_id = str(result_paper.get("id") or "")
        status = str(result.get("status") or "failed")
        if result_id and status not in {"failed"}:
            if status in {"imported", "replaced"}:
                imported += 1
            else:
                skipped += 1
            try:
                repo.update_ref(
                    paper_id,
                    ref_idx,
                    matched_paper=result_id,
                    match_score=1.0,
                    arxiv_id=str((result.get("entry") or {}).get("arxiv_id") or ref.get("arxiv_id") or ""),
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("update imported ref failed: %s", exc)
            items.append(
                {
                    "ref_idx": ref_idx,
                    "title": title,
                    "status": "imported" if status in {"imported", "replaced"} else "duplicate",
                    "paper_id": result_id,
                    "reason": str(result.get("reason") or "已导入。"),
                }
            )
        else:
            items.append(
                {
                    "ref_idx": ref_idx,
                    "title": title,
                    "status": "failed",
                    "paper_id": "",
                    "reason": str(result.get("reason") or "未能匹配或下载该参考文献。"),
                }
            )
    return {
        "attempted": len(refs),
        "imported": imported,
        "skipped": skipped,
        "items": items,
        "reason": f"共处理 {len(refs)} 条参考文献：导入 {imported} 条，跳过 {skipped} 条。",
    }


# --------------------------------------------------------------------------- 反馈与推荐


def feedback(
    target: str,
    action: str,
    *,
    user_id: str = "local",
    weight: float = 1.0,
) -> dict[str, Any]:
    """记录点赞/踩/收藏/隐藏；推荐排序会读取同一张 feedback 表。"""
    normalized_action = str(action or "").strip().lower()
    if normalized_action not in FEEDBACK_ACTIONS:
        raise ValidationError(f"不支持的反馈动作：{action}（可选 {', '.join(FEEDBACK_ACTIONS)}）")
    normalized_target = normalize_ws(str(target or ""))
    if not normalized_target:
        raise ValidationError("反馈目标不能为空")
    try:
        normalized_weight = float(weight)
    except (TypeError, ValueError):
        normalized_weight = 1.0
    if not math.isfinite(normalized_weight) or normalized_weight <= 0:
        normalized_weight = 1.0
    normalized_weight = min(5.0, normalized_weight)

    repo.add_feedback(user_id, normalized_target, normalized_action, normalized_weight)
    counts = Counter(str(row.get("action") or "") for row in repo.get_feedback(user_id) or [])
    return {
        "status": "ok",
        "user_id": user_id,
        "target": normalized_target,
        "action": normalized_action,
        "weight": round(normalized_weight, 3),
        "counts": dict(sorted(counts.items())),
    }


def _candidate_terms(row: dict[str, Any]) -> set[str]:
    return set(tokens(f"{row.get('title') or ''} {row.get('abstract') or ''}"))


def recommend(
    *,
    user_id: str = "local",
    limit: int = 8,
    category: str | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """基于本地库主题画像 + discovery 候选 + feedback 的离线推荐。"""
    _settings(settings)  # 保持与全局配置一致的 settings 获取路径
    library = _library_context()
    profile = _library_profile(library)
    feedback_state = _feedback_context(user_id)
    candidates = repo.list_discovery(limit=_MAX_DISCOVERY, sort="published") or []
    current_year = utc_now().year
    profile_terms = set(profile["terms"])

    scored: list[dict[str, Any]] = []
    for row in candidates:
        if not _category_matches(row.get("categories") or [], "", category):
            continue
        arxiv_id = str(row.get("arxiv_id") or "")
        imported_paper = str(row.get("imported_paper") or "")
        info = _feedback_for(feedback_state, arxiv_id, _bare(arxiv_id), imported_paper)
        if info is not None and info["hidden"]:
            continue

        terms = _candidate_terms(row)
        matched = [term for term in terms if term in profile_terms]
        matched.sort(key=lambda term: (-library["df"].get(term, 0), term))
        local = min(1.0, len(matched) / max(1, min(len(terms), 12))) if matched else 0.0
        year = _year_from_text(str(row.get("published") or row.get("updated") or ""))
        recency = max(0.0, min(1.0, 1.0 - (current_year - year) / 8.0)) if year else 0.0
        has_code = bool(str(row.get("github") or "").strip())
        score = 0.50 * local + 0.15 * recency + (0.05 if has_code else 0.0)

        liked = disliked = saved = False
        if info is not None:
            if info["sentiment"] == "like":
                liked = True
                score += 0.30 * float(info["sentiment_weight"])
            elif info["sentiment"] == "dislike":
                disliked = True
                score -= 0.35 * float(info["sentiment_weight"])
            if info["saved"]:
                saved = True
                score += 0.08
        imported = bool(imported_paper) or _bare(arxiv_id).lower() in library["arxiv"]
        if imported:
            score -= 0.05
        score = round(max(0.0, min(1.0, score)), 4)

        reasons: list[str] = []
        if matched:
            reasons.append(f"与本地库主题重合：{'、'.join(matched[:4])}")
        if year and year >= current_year - 2:
            reasons.append(f"{year} 年近期预印本")
        if has_code:
            reasons.append("附开源代码仓库")
        if liked:
            reasons.append("你点赞过该文献")
        elif disliked:
            reasons.append("你标记过不感兴趣（已降权）")
        if saved:
            reasons.append("你收藏过该文献")
        if imported:
            reasons.append("已在本地文献库中（仅作参考）")
        if not reasons:
            if profile_terms:
                reasons.append("本地库暂无直接主题重合，按候选新近度排序")
            else:
                reasons.append("本地文献库暂无主题画像，按候选新近度排序")

        item = dict(row)
        item.update(
            {
                "year": year,
                "has_code": has_code,
                "score": score,
                "reason": "；".join(reasons) + "。",
                "imported": imported,
                "liked": liked,
                "disliked": disliked,
                "saved": saved,
                "matched_terms": matched[:6],
            }
        )
        scored.append(item)

    scored.sort(key=lambda item: (-item["score"], str(item.get("arxiv_id") or "")))
    items = scored[: _clamp(limit, 1, 50)]
    notes: list[str] = []
    if not profile["terms"]:
        notes.append("本地文献库为空或没有可用主题词，推荐退化为候选新近度排序。")
    if not candidates:
        notes.append("discovery 候选为空，请先执行 search 或 tracker_run。")
    return {
        "items": items,
        "profile": profile,
        "provider": "local-library" if profile["terms"] else "discovery-fallback",
        "degraded": not bool(profile["terms"]) or not bool(candidates),
        "notes": notes,
    }


# --------------------------------------------------------------------------- 追踪


def _pack_topic_query(topic: str, category: str | None, keywords: Sequence[str]) -> str:
    pieces = [normalize_ws(topic)]
    pieces.extend(normalize_ws(str(keyword)) for keyword in keywords if normalize_ws(str(keyword)))
    cleaned_keywords = [normalize_ws(str(keyword)).replace(",", " ") for keyword in keywords]
    cleaned_keywords = [keyword for keyword in cleaned_keywords if keyword]
    if cleaned_keywords:
        pieces.append("kw:" + ",".join(cleaned_keywords))
    if category:
        pieces.append(f"cat:{normalize_ws(category)}")
    return normalize_ws(" ".join(piece for piece in pieces if piece))


def _unpack_topic_query(raw: str) -> tuple[str, str | None, list[str]]:
    text = normalize_ws(str(raw or ""))
    category = None
    cat_match = re.search(r"(?:^|\s)cat:([A-Za-z][A-Za-z0-9_.\-]*)", text)
    if cat_match:
        category = cat_match.group(1)
        text = normalize_ws(text[: cat_match.start()] + " " + text[cat_match.end() :])
    keywords: list[str] = []
    kw_match = re.search(r"(?:^|\s)kw:([^\s]+)", text)
    if kw_match:
        keywords = [piece for piece in re.split(r"[,\s]+", kw_match.group(1)) if piece]
        text = normalize_ws(text[: kw_match.start()] + " " + text[kw_match.end() :])
    return normalize_ws(text), category, keywords


def _topic_payload(row: dict[str, Any]) -> dict[str, Any]:
    query, category, keywords = _unpack_topic_query(str(row.get("query") or ""))
    item = dict(row)
    item["enabled"] = bool(item.get("enabled"))
    item["hits"] = int(item.get("hits") or 0)
    item["category"] = category
    item["keywords"] = keywords
    item["topic"] = query
    return item


def track_add(
    topic: str,
    *,
    category: str | None = None,
    keywords: Sequence[str] | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """新增/更新一个 Sickle 风格追踪主题。"""
    _settings(settings)
    cleaned = normalize_ws(str(topic or ""))
    if not cleaned:
        raise ValidationError("追踪主题不能为空")
    if isinstance(keywords, str):
        keyword_list = [piece for piece in re.split(r"[,，\s]+", keywords) if piece]
    else:
        keyword_list = [normalize_ws(str(keyword)) for keyword in (keywords or []) if normalize_ws(str(keyword))]
    keyword_list = [keyword for keyword in keyword_list if keyword]
    normalized_category = normalize_ws(str(category or "")) or None
    query = _pack_topic_query(cleaned, normalized_category, keyword_list)
    topic_id = short_id(
        f"topic|{normalize_title(cleaned)}|{normalized_category or ''}|{','.join(keyword_list)}",
        12,
    )
    row = repo.upsert_track_topic(
        {
            "id": topic_id,
            "name": cleaned,
            "query": query,
            "enabled": True,
        }
    )
    return _topic_payload(row)


def track_list() -> list[dict[str, Any]]:
    return [_topic_payload(row) for row in repo.list_track_topics() or []]


def track_remove(topic_id: str) -> dict[str, Any]:
    cleaned = normalize_ws(str(topic_id or ""))
    if not cleaned:
        return {"status": "not_found", "topic_id": cleaned}
    exists = any(str(row.get("id") or "") == cleaned for row in repo.list_track_topics() or [])
    if not exists:
        return {"status": "not_found", "topic_id": cleaned}
    repo.delete_track_topic(cleaned)
    return {"status": "removed", "topic_id": cleaned}


def tracker_run(
    *, settings: Settings | None = None, force: bool = False
) -> dict[str, Any]:
    """轮询启用的追踪主题；时间窗内幂等跳过，``force=True`` 强制刷新。"""
    started = time.perf_counter()
    s = _settings(settings)
    interval_minutes = max(1, int(s.tracker_interval_minutes))
    now = utc_now()
    state_value = repo.get_state(TRACKER_STATE_KEY)
    last_run = parse_iso(state_value)
    if not force and last_run is not None:
        elapsed_seconds = (now - last_run).total_seconds()
        if elapsed_seconds < interval_minutes * 60:
            return {
                "topics": 0,
                "fetched": 0,
                "new_items": 0,
                "elapsed_ms": _elapsed_ms(started),
                "skipped": True,
                "reason": f"距上次追踪不足 {interval_minutes} 分钟，已跳过；可 force=true 强制执行。",
                "last_run": state_value,
            }

    topics = [row for row in repo.list_track_topics() or [] if bool(row.get("enabled"))]
    if not topics:
        return {
            "topics": 0,
            "fetched": 0,
            "new_items": 0,
            "elapsed_ms": _elapsed_ms(started),
            "skipped": False,
            "reason": "没有启用的追踪主题。",
            "last_run": state_value,
        }

    client = get_arxiv(s)
    total_fetched = 0
    new_items = 0
    succeeded = 0
    details: list[dict[str, Any]] = []
    for row in topics:
        topic_id = str(row.get("id") or "")
        query, category, _keywords = _unpack_topic_query(str(row.get("query") or ""))
        if not query:
            details.append({"id": topic_id, "fetched": 0, "new_items": 0, "status": "skipped", "reason": "主题为空"})
            continue
        try:
            entries = client.search(
                query,
                max_results=max(1, int(s.arxiv_max_results)),
                category=category or "",
                prefer_code=False,
            )
            if category:
                entries = [
                    entry
                    for entry in entries
                    if _category_matches(entry.categories, entry.primary_category, category)
                ]
        except Exception as exc:  # noqa: BLE001 - 单个主题失败不影响其余主题
            details.append(
                {
                    "id": topic_id,
                    "fetched": 0,
                    "new_items": 0,
                    "status": "failed",
                    "reason": f"检索失败：{exc}",
                }
            )
            continue

        fresh = 0
        payload = []
        for entry in entries:
            if repo.get_discovery(entry.arxiv_id) is None:
                fresh += 1
            payload.append(entry.to_dict())
        if payload:
            try:
                repo.upsert_discovery(payload)
            except Exception as exc:  # noqa: BLE001
                details.append(
                    {
                        "id": topic_id,
                        "fetched": 0,
                        "new_items": 0,
                        "status": "failed",
                        "reason": f"写库失败：{exc}",
                    }
                )
                continue
        total_fetched += len(entries)
        new_items += fresh
        succeeded += 1
        try:
            repo.mark_topic_run(topic_id, int(row.get("hits") or 0) + len(entries))
        except Exception as exc:  # noqa: BLE001
            log.warning("mark topic run failed: %s", exc)
        details.append(
            {
                "id": topic_id,
                "topic": query,
                "fetched": len(entries),
                "new_items": fresh,
                "status": "ok",
                "reason": f"抓取 {len(entries)} 条，其中 {fresh} 条为新增。",
            }
        )

    if succeeded:
        repo.set_state(TRACKER_STATE_KEY, utc_now_iso())
    return {
        "topics": len(topics),
        "fetched": total_fetched,
        "new_items": new_items,
        "elapsed_ms": _elapsed_ms(started),
        "skipped": False,
        "items": details,
        "last_run": repo.get_state(TRACKER_STATE_KEY),
    }


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
