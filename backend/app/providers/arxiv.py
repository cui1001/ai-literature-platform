"""arXiv 检索 Provider：在线 Atom API + 离线镜像，接口形态对齐 Sickle。

在线通道直接请求 ``export.arxiv.org/api/query``，不依赖第三方包；
离线通道读取 ``fixtures/arxiv/mirror.json``（由脚本生成，内容为人工整理的元数据快照），
在无网络环境中仍可完整演示「检索 → 引用信号 → 入库 → 追踪」闭环。
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..core.config import Settings, get_settings
from ..core.hashing import short_id
from ..core.logging import get_logger
from ..core.textutil import ARXIV_ID_RE, GITHUB_RE, jaccard, normalize_ws, tokens, truncate

log = get_logger(__name__)

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"
API_URL = "https://export.arxiv.org/api/query"

_CACHE_LOCK = threading.Lock()
_CACHE: dict[tuple[Any, ...], Any] = {}


@dataclass
class ArxivEntry:
    arxiv_id: str
    title: str
    authors: list[str] = field(default_factory=list)
    abstract: str = ""
    comments: str = ""
    journal_ref: str = ""
    categories: list[str] = field(default_factory=list)
    published: str = ""
    updated: str = ""
    url: str = ""
    pdf_url: str = ""
    github: str = ""
    primary_category: str = ""
    source: str = "offline-mirror"

    @property
    def year(self) -> int:
        match = re.match(r"(\d{4})", self.published or "")
        return int(match.group(1)) if match else 0

    @property
    def version(self) -> int:
        match = re.search(r"v(\d+)$", self.arxiv_id)
        return int(match.group(1)) if match else 1

    @property
    def bare_id(self) -> str:
        return re.sub(r"v\d+$", "", self.arxiv_id)

    @property
    def has_code(self) -> bool:
        return bool(self.github)

    def to_dict(self) -> dict[str, Any]:
        return {
            "arxiv_id": self.arxiv_id,
            "title": self.title,
            "authors": list(self.authors),
            "abstract": self.abstract,
            "comments": self.comments,
            "journal_ref": self.journal_ref,
            "categories": list(self.categories),
            "primary_category": self.primary_category,
            "published": self.published,
            "updated": self.updated,
            "year": self.year,
            "url": self.url or f"https://arxiv.org/abs/{self.arxiv_id}",
            "pdf_url": self.pdf_url or f"https://arxiv.org/pdf/{self.arxiv_id}.pdf",
            "github": self.github,
            "has_code": self.has_code,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ArxivEntry":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        payload = {k: v for k, v in data.items() if k in known}
        entry = cls(**payload)
        if not entry.github:
            entry.github = _detect_github(entry.abstract, entry.comments)
        return entry


def _detect_github(*texts: str) -> str:
    for text in texts:
        match = GITHUB_RE.search(text or "")
        if match:
            return match.group(0).rstrip(".,;)")
    return ""


def parse_atom(xml_text: str) -> list[ArxivEntry]:
    """解析 Atom 响应；单个 entry 解析失败不影响整体。"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"invalid atom xml: {exc}") from exc
    entries: list[ArxivEntry] = []
    for node in root.findall(f"{ATOM}entry"):
        try:
            raw_id = (node.findtext(f"{ATOM}id") or "").strip()
            match = ARXIV_ID_RE.search(raw_id)
            arxiv_id = match.group(0) if match else raw_id.rsplit("/", 1)[-1]
            title = normalize_ws(node.findtext(f"{ATOM}title") or "")
            abstract = normalize_ws(node.findtext(f"{ATOM}summary") or "")
            authors = [
                normalize_ws(a.findtext(f"{ATOM}name") or "")
                for a in node.findall(f"{ATOM}author")
                if normalize_ws(a.findtext(f"{ATOM}name") or "")
            ]
            categories = [c.get("term", "") for c in node.findall(f"{ATOM}category") if c.get("term")]
            links = {link.get("rel", ""): link.get("href", "") for link in node.findall(f"{ATOM}link")}
            comments = normalize_ws(node.findtext(f"{ARXIV_NS}comment") or "")
            journal_ref = normalize_ws(node.findtext(f"{ARXIV_NS}journal_ref") or "")
            primary = node.find(f"{ARXIV_NS}primary_category")
            entries.append(
                ArxivEntry(
                    arxiv_id=arxiv_id,
                    title=title,
                    authors=authors,
                    abstract=abstract,
                    comments=comments,
                    journal_ref=journal_ref,
                    categories=categories,
                    primary_category=(primary.get("term", "") if primary is not None else ""),
                    published=(node.findtext(f"{ATOM}published") or "").strip(),
                    updated=(node.findtext(f"{ATOM}updated") or "").strip(),
                    url=links.get("alternate", f"https://arxiv.org/abs/{arxiv_id}"),
                    pdf_url=links.get("related", "") or f"https://arxiv.org/pdf/{arxiv_id}.pdf",
                    github=_detect_github(abstract, comments),
                    source="arxiv-api",
                )
            )
        except Exception as exc:  # pragma: no cover - 单条容错
            log.debug("skip malformed atom entry: %s", exc)
    return entries


def build_query(query: str, *, title_only: bool = False, category: str = "", max_results: int = 20, sort: str = "relevance") -> str:
    """Sickle 风格：把自然语言/标题转成 arXiv API 查询串。"""
    cleaned = normalize_ws(query)
    if not cleaned:
        raise ValueError("empty query")
    if title_only:
        terms = f'ti:"{cleaned}"'
    elif re.fullmatch(r"[\w.\-]+(?:v\d+)?", cleaned):
        terms = f"id:{cleaned}"
    else:
        words = [w for w in re.findall(r"[\w\-]+", cleaned) if len(w) > 1][:8]
        terms = " AND ".join(f"all:{w}" for w in words)
    params = {
        "search_query": terms,
        "start": 0,
        "max_results": max(1, min(100, max_results)),
        "sortBy": "submittedDate" if sort in {"submitted", "date"} else "relevance",
        "sortOrder": "descending",
    }
    if category:
        params["search_query"] = f"({terms}) AND cat:{category}"
    return f"{API_URL}?{urllib.parse.urlencode(params)}"


class ArxivClient:
    """统一检索入口：在线失败自动回落离线镜像。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.mode = settings.arxiv_mode
        self.mirror = _load_mirror(settings)
        self.last_error = ""
        self.degraded = False

    # ---------------------------------------------------------------- 公共 API

    def search(
        self,
        query: str,
        *,
        max_results: int | None = None,
        sort: str = "relevance",
        category: str = "",
        title_only: bool = False,
        prefer_code: bool = False,
    ) -> list[ArxivEntry]:
        limit = max_results or self.settings.arxiv_max_results
        mode = self.mode
        if mode in {"auto", "online"}:
            try:
                entries = self._online_search(query, limit, sort=sort, category=category, title_only=title_only)
                self.degraded = False
                self.last_error = ""
            except Exception as exc:
                self.last_error = str(exc)
                self.degraded = True
                log.warning("arXiv 在线检索失败，使用离线镜像：%s", exc)
                if mode == "online":
                    raise
                entries = self._offline_search(query, limit, title_only=title_only)
        else:
            entries = self._offline_search(query, limit, title_only=title_only)
        if prefer_code:
            entries.sort(key=lambda e: (not e.has_code, -e.year, e.title.lower()))
        return entries[:limit]

    def by_ids(self, arxiv_ids: Sequence[str]) -> list[ArxivEntry]:
        ids = [i.strip() for i in arxiv_ids if i and i.strip()]
        if not ids:
            return []
        if self.mode in {"auto", "online"}:
            try:
                url = f"{API_URL}?{urllib.parse.urlencode({'id_list': ','.join(ids), 'max_results': len(ids)})}"
                return parse_atom(_http_get(url, timeout=8.0))
            except Exception as exc:
                self.last_error = str(exc)
                self.degraded = True
                if self.mode == "online":
                    raise
        by_id = {e.bare_id: e for e in self.mirror}
        return [by_id[i] for i in ids if i in by_id]

    def latest(self, category: str = "cs.CL", max_results: int = 20) -> list[ArxivEntry]:
        return self.search(f"cat:{category}", max_results=max_results, sort="submitted", category=category)

    def download_pdf(self, entry: ArxivEntry, dest: Path) -> Path:
        """在线下载 PDF；离线模式抛出明确错误（由调用方生成代理 PDF）。"""
        if self.mode == "offline":
            raise RuntimeError("offline arxiv mode")
        dest.parent.mkdir(parents=True, exist_ok=True)
        data = _http_get(entry.pdf_url or f"https://arxiv.org/pdf/{entry.arxiv_id}.pdf", timeout=30.0, binary=True)
        dest.write_bytes(data)
        return dest

    # ---------------------------------------------------------------- 内部实现

    def _online_search(
        self, query: str, limit: int, *, sort: str, category: str, title_only: bool
    ) -> list[ArxivEntry]:
        url = build_query(query, title_only=title_only, category=category, max_results=limit, sort=sort)
        started = time.perf_counter()
        entries = parse_atom(_http_get(url, timeout=8.0))
        log.info("arxiv online search %r -> %d in %.0fms", truncate(query, 40), len(entries), (time.perf_counter() - started) * 1000)
        return entries

    def _offline_search(self, query: str, limit: int, *, title_only: bool = False) -> list[ArxivEntry]:
        q_tokens = set(tokens(query))
        if not q_tokens:
            return []
        scored: list[tuple[float, ArxivEntry]] = []
        for entry in self.mirror:
            title_tokens = set(tokens(entry.title))
            abstract_tokens = set(tokens(entry.abstract))
            title_score = jaccard(q_tokens, title_tokens)
            if title_only:
                score = title_score
            else:
                score = 0.75 * title_score + 0.25 * jaccard(q_tokens, abstract_tokens)
                if normalize_ws(query).lower() in entry.title.lower():
                    score += 0.4
                if entry.bare_id in query or entry.arxiv_id == query:
                    score = 1.0
            if score > 0.02:
                scored.append((score, entry))
        scored.sort(key=lambda item: (-item[0], -item[1].year, item[1].title))
        return [entry for _, entry in scored[:limit]]


# -------------------------------------------------------------------- 工具函数


def _http_get(url: str, *, timeout: float = 10.0, binary: bool = False) -> Any:
    request = urllib.request.Request(
        url, headers={"User-Agent": "agentlit/2.0 (academic demo; contact: lab@example.edu)"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - 固定官方域名
        raw = response.read()
    if binary:
        return raw
    return raw.decode("utf-8", errors="replace")


def _mirror_path(settings: Settings) -> Path:
    return settings.repo_root / "fixtures" / "arxiv" / "mirror.json"


def _load_mirror(settings: Settings) -> list[ArxivEntry]:
    path = _mirror_path(settings)
    if not path.is_file():
        log.warning("arXiv 离线镜像不存在：%s", path)
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log.warning("arXiv 离线镜像解析失败：%s", exc)
        return []
    items = payload.get("papers") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return []
    entries: list[ArxivEntry] = []
    for item in items:
        if isinstance(item, dict) and item.get("title"):
            entries.append(ArxivEntry.from_dict(item))
    return entries


def get_arxiv(settings: Settings | None = None) -> ArxivClient:
    settings = settings or get_settings()
    key = (str(settings.repo_root), settings.arxiv_mode, settings.arxiv_max_results)
    with _CACHE_LOCK:
        if key not in _CACHE:
            _CACHE[key] = ArxivClient(settings)
        return _CACHE[key]


def reset_arxiv_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def arxiv_status(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    client = get_arxiv(settings)
    return {
        "mode": settings.arxiv_mode,
        "active": "online" if settings.arxiv_mode == "online" and not client.degraded else "mirror",
        "mirror_size": len(client.mirror),
        "mirror_path": str(_mirror_path(settings)),
        "last_error": client.last_error,
        "degraded": client.degraded,
        "max_results": settings.arxiv_max_results,
    }


def relevance_score(entry: ArxivEntry, query: str) -> float:
    """离线相关性：标题 0.6 + 摘要 0.25 + 代码可得 0.1 + 新鲜度 0.05。"""
    q = set(tokens(query))
    if not q:
        return 0.0
    base = 0.6 * jaccard(q, set(tokens(entry.title))) + 0.25 * jaccard(q, set(tokens(entry.abstract)))
    code_bonus = 0.1 if entry.has_code else 0.0
    freshness = max(0.0, min(0.05, (2026 - max(entry.year, 2000)) * -0.005 + 0.05))
    return round(base + code_bonus + freshness, 4)
