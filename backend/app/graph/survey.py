"""领域学术综述生成。

输入是局域引用图谱（``build_graph`` 的产物）与每篇论文的解析结果：

1. **研究脉络**：按年份梳理工作及其引用承继关系；
2. **基石工作**：解释 pagerank 高且入度高的论文为何构成领域基石；
3. **方法演化**：按社区 / 年份归纳方法路线的演进；
4. **未来方向**：从各论文 Future Work / Limitations 段落抽取方向，按时间线判断是否已被
   后续论文（标题或摘要关键词命中）解决；已解决的方向从开放方向中排除并附证据论文。

正文优先走 ``run_task("survey_section", payload)``（离线模式由 ``OfflineLLM`` 的
``survey_section`` 处理器生成），在线通道失败时自动回落本地拼装并标记 ``degraded``。
每条论述都通过 ``citations`` 关联到具体 ``paper_id`` + 页码/章节。
"""

from __future__ import annotations

import re
import statistics
from typing import Any, Mapping, Sequence

from ..core.config import Settings, get_settings
from ..core.errors import NotFoundError, ValidationError
from ..core.logging import get_logger
from ..core.textutil import (
    CJK_RE,
    dedupe_keep_order,
    keyword_candidates,
    normalize_title,
    normalize_ws,
    sentence_split,
    tokens,
    truncate,
)
from ..db import repo
from ..parsing.pipeline import load_parsed
from ..providers.llm import run_task
from .builder import ROLE_LABELS_ZH, build_graph, get_graph

logger = get_logger(__name__)

SECTION_TITLES = ("研究脉络", "基石工作", "方法演化", "未来方向")

SECTION_FOCUS = {
    "研究脉络": "按年份与引用关系梳理该领域的研究脉络，说明各阶段代表工作之间的继承与转折",
    "基石工作": "解释 pagerank 高、被引次数高的论文为何构成领域基石，并说明其影响范围",
    "方法演化": "归纳方法/技术路线的演化：哪些路线被延续、哪些被替代或融合",
    "未来方向": "汇总各论文 Future Work / Limitations 中的开放方向，并说明哪些已被后续工作解决",
}

FUTURE_SECTION_RE = re.compile(
    r"future\s*work|future\s*direction|future\s*research|further\s*work|limitation|open\s*problem"
    r"|未来工作|未来方向|未来研究|后续工作|后续研究|展望|局限|不足",
    re.I,
)
FUTURE_HINT_RE = re.compile(
    r"future\s*work|future\s*direction|future\s*research|in\s+the\s+future|further\s+(?:work|study|research)"
    r"|we\s+(?:plan|intend|hope|leave|propose|expect)|left\s+(?:for|to|as)\s+future|is\s+left"
    r"|open\s+(?:problem|question)|未来工作|未来方向|未来研究|进一步(?:研究|探索|工作)"
    r"|后续(?:工作|研究)|有待|留待|展望",
    re.I,
)

#: 抽取「方向关键词」时忽略的泛化词，避免所有方向都因 future/work/study 命中。
GENERIC_DIRECTION_TERMS = frozenset(
    {
        "future", "work", "direction", "research", "study", "studie", "paper", "approach", "method",
        "plan", "propose", "extend", "extension", "explore", "investigate", "investigation",
        "further", "new", "novel", "also", "well", "would", "could", "should", "will", "may",
        "might", "can", "leave", "left", "remain", "remaining", "open", "question", "problem",
        "limitation", "limited", "aim", "focus", "address", "improve", "provide", "possible",
        "potential", "application", "real", "world", "setting", "setting.",
        # 中文
        "未来", "工作", "方向", "研究", "进一步", "后续", "探索", "展望", "局限", "不足",
        "问题", "我们", "本文", "方法", "可以", "将会", "计划", "扩展", "拓展", "尝试", "关注",
    }
)

MIN_SOLVED_MATCHES = 2
MIN_SOLVED_RATIO = 0.5
MAX_DIRECTIONS_PER_PAPER = 3
MAX_SOLUTION_CANDIDATES = 12


# ============================================================ 基础工具
def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _load_context(paper_id: str, settings: Settings) -> dict[str, Any]:
    """论文 + 解析结果（标题/摘要/章节/文本块）。"""
    row = repo.get_paper(paper_id)
    if not row:
        raise NotFoundError(f"论文不存在：{paper_id}", detail={"paper_id": paper_id})
    parsed: dict[str, Any] = {}
    try:
        parsed = load_parsed(paper_id, settings) or {}
    except (OSError, ValueError) as exc:
        logger.warning("读取解析结果失败 paper_id=%s: %s", paper_id, exc)
    title = str(parsed.get("title") or row.get("title") or "").strip() or paper_id
    abstract = str(parsed.get("abstract") or row.get("abstract") or "").strip()
    return {
        "id": paper_id,
        "title": title,
        "abstract": abstract,
        "year": _as_int(row.get("year") or parsed.get("year"), 0),
        "arxiv_id": str(row.get("arxiv_id") or parsed.get("arxiv_id") or "").strip(),
        "parsed": parsed,
    }


def _keywords(text: str) -> list[str]:
    """方向关键词：词干化后过滤泛化词；中文保留二元组、英文保留长度 ≥3 的词。"""
    out: list[str] = []
    for token in tokens(text):
        if not token or token in GENERIC_DIRECTION_TERMS or token.isdigit():
            continue
        if CJK_RE.search(token):
            if len(token) < 2:
                continue
        elif len(token) < 3:
            continue
        if token not in out:
            out.append(token)
    return out


class _Citations:
    """生成 ``[C1]`` 形式的引用标记，并维护 ``paper_id + 页码/章节`` 的映射。"""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []
        self._index: dict[tuple[str, int, str], str] = {}

    def add(
        self,
        paper: Mapping[str, Any],
        *,
        page: int = 1,
        section: str = "Abstract",
        quote: str = "",
    ) -> str:
        paper_id = str(paper.get("id") or "").strip()
        if not paper_id:
            return ""
        page = _as_int(page, 1) or 1
        section = str(section or "Abstract").strip() or "Abstract"
        key = (paper_id, page, section)
        if key in self._index:
            return self._index[key]
        citation_id = f"C{len(self.items) + 1}"
        marker = f"[{citation_id}]"
        self.items.append(
            {
                "id": citation_id,
                "marker": marker,
                "paper_id": paper_id,
                "title": str(paper.get("title") or ""),
                "year": _as_int(paper.get("year"), 0),
                "page": page,
                "section": section,
                "quote": truncate(str(quote or paper.get("abstract") or paper.get("title") or ""), 200),
            }
        )
        self._index[key] = marker
        return marker

    def marker_for(self, paper: Mapping[str, Any]) -> str:
        return self.add(
            paper,
            page=1,
            section="Abstract",
            quote=str(paper.get("abstract") or paper.get("title") or ""),
        )


# ============================================================ Future Work 抽取
def _section_blocks(section: Mapping[str, Any], blocks: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    by_id = {str(block.get("id")): block for block in blocks if block.get("id")}
    selected = [by_id[str(block_id)] for block_id in (section.get("block_ids") or []) if str(block_id) in by_id]
    if selected:
        return selected
    sid = str(section.get("sid") or "")
    return [block for block in blocks if str(block.get("sid") or "") == sid]


def _future_snippets(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    """收集 Future Work / Limitations 章节段落，以及正文中提到 future work 的段落。"""
    parsed = context.get("parsed") or {}
    blocks = [block for block in (parsed.get("blocks") or []) if isinstance(block, dict)]
    sections = [section for section in (parsed.get("sections") or []) if isinstance(section, dict)]
    skipped_kinds = {"reference", "footer", "header", "equation"}
    snippets: list[dict[str, Any]] = []
    seen: set[str] = set()

    future_sids: set[str] = set()
    for section in sections:
        title = str(section.get("title") or "").strip()
        if not FUTURE_SECTION_RE.search(title):
            continue
        future_sids.add(str(section.get("sid") or ""))
        for block in _section_blocks(section, blocks):
            if str(block.get("kind") or "") in skipped_kinds:
                continue
            text = normalize_ws(block.get("text") or "")
            if len(text) < 20:
                continue
            key = str(block.get("id") or "") or text[:100]
            if key in seen:
                continue
            seen.add(key)
            snippets.append(
                {
                    "text": text,
                    "page": _as_int(block.get("page") or section.get("page"), 1) or 1,
                    "section": title or "Future Work",
                    "from_section": True,
                }
            )

    for block in blocks:
        sid = str(block.get("sid") or "")
        if sid in future_sids:
            continue
        kind = str(block.get("kind") or "")
        if kind and kind != "paragraph":
            continue
        text = normalize_ws(block.get("text") or "")
        if len(text) < 40 or not FUTURE_HINT_RE.search(text):
            continue
        key = str(block.get("id") or "") or text[:100]
        if key in seen:
            continue
        seen.add(key)
        section_title = next(
            (str(section.get("title") or "") for section in sections if str(section.get("sid") or "") == sid),
            "",
        )
        snippets.append(
            {
                "text": text,
                "page": _as_int(block.get("page"), 1) or 1,
                "section": section_title or "Future Work",
                "from_section": False,
            }
        )
    return snippets


def _future_sentences(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    """把 Future Work 文本切成句子，每篇最多取 ``MAX_DIRECTIONS_PER_PAPER`` 条。"""
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for snippet in _future_snippets(context):
        for sentence in sentence_split(snippet["text"]):
            sentence = normalize_ws(sentence)
            if len(sentence) < 20:
                continue
            if not snippet["from_section"] and not FUTURE_HINT_RE.search(sentence):
                continue
            key = sentence.lower()
            if key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    "direction": truncate(sentence, 240),
                    "page": snippet["page"],
                    "section": snippet["section"],
                    "source_paper": context["id"],
                    "source_title": context["title"],
                    "year": context["year"],
                }
            )
            if len(results) >= MAX_DIRECTIONS_PER_PAPER:
                return results
    return results


def _find_solution(
    keywords: Sequence[str],
    source_id: str,
    source_year: int,
) -> tuple[dict[str, Any] | None, int]:
    """在时间线之后的论文中寻找同时命中 ≥2 个方向关键词的工作。"""
    keyword_set = set(keywords)
    if not keyword_set:
        return None, 0
    candidates: list[tuple[int, Mapping[str, Any]]] = []
    for record in repo.paper_index():
        paper_id = str(record.get("id") or "")
        if not paper_id or paper_id == source_id:
            continue
        if _as_int(record.get("year"), 0) <= source_year:
            continue
        title = str(record.get("title") or "")
        overlap = len(keyword_set & set(_keywords(title)))
        candidates.append((overlap, record))
    candidates.sort(
        key=lambda item: (-item[0], -_as_int(item[1].get("year"), 0), str(item[1].get("id") or ""))
    )
    checked = len(candidates)
    for _title_overlap, record in candidates[:MAX_SOLUTION_CANDIDATES]:
        paper_id = str(record.get("id") or "")
        row = repo.get_paper(paper_id) or {}
        corpus = f"{record.get('title') or ''} {row.get('abstract') or ''}"
        matched = sorted(keyword_set & set(_keywords(corpus)))
        ratio = len(matched) / max(1, len(keyword_set))
        if len(matched) >= MIN_SOLVED_MATCHES and ratio >= MIN_SOLVED_RATIO:
            year = _as_int(record.get("year"), 0)
            return (
                {
                    "paper_id": paper_id,
                    "title": str(record.get("title") or ""),
                    "year": year,
                    "score": round(ratio, 3),
                    "matched_terms": matched[:8],
                    "reason": (
                        f"后续论文《{record.get('title') or paper_id}》（{year}）的标题/摘要命中 "
                        f"{len(matched)} 个方向关键词（占比 {ratio:.0%}）：{'、'.join(matched[:6])}，"
                        "判定该方向已被解决。"
                    ),
                },
                checked,
            )
    return None, checked


def _extract_directions(
    contexts: Sequence[Mapping[str, Any]],
    citations: _Citations,
) -> list[dict[str, Any]]:
    directions: list[dict[str, Any]] = []
    seen_directions: set[str] = set()
    for context in contexts:
        for item in _future_sentences(context):
            dedupe_key = normalize_title(item["direction"])[:160]
            if not dedupe_key or dedupe_key in seen_directions:
                continue
            seen_directions.add(dedupe_key)
            keywords = _keywords(item["direction"])
            solution, checked = _find_solution(
                keywords, source_id=context["id"], source_year=_as_int(context["year"], 0)
            )
            item["citation"] = citations.add(
                context,
                page=item["page"],
                section=item["section"],
                quote=item["direction"],
            )
            if solution is not None:
                item["status"] = "solved"
                item["evidence"] = solution
                solution["citation"] = citations.add(
                    {
                        "id": solution["paper_id"],
                        "title": solution["title"],
                        "year": solution["year"],
                        "abstract": "",
                    },
                    page=1,
                    section="Abstract",
                    quote=solution["title"],
                )
            else:
                item["status"] = "open"
                item["evidence"] = {
                    "paper_id": "",
                    "title": "",
                    "year": None,
                    "score": 0.0,
                    "matched_terms": [],
                    "checked_papers": checked,
                    "reason": (
                        f"时间线之后共检索 {checked} 篇文献，均未同时命中至少 "
                        f"{MIN_SOLVED_MATCHES} 个方向关键词，判定仍属开放问题。"
                    ),
                }
            directions.append(item)

    directions.sort(
        key=lambda item: (
            _as_int(item.get("year"), 0),
            str(item.get("source_title") or ""),
            str(item.get("direction") or ""),
        )
    )
    return directions


# ============================================================ 本地降级正文
def _fallback_timeline(
    contexts: Sequence[Mapping[str, Any]],
    nodes_by_id: Mapping[str, Mapping[str, Any]],
    citations: _Citations,
) -> str:
    ordered = sorted(contexts, key=lambda item: (_as_int(item.get("year"), 0), str(item.get("title")).lower()))
    lines = []
    for context in ordered:
        node = nodes_by_id.get(context["id"]) or {}
        role = ROLE_LABELS_ZH.get(str(node.get("role") or "normal"), "常规工作")
        lines.append(
            f"- **{context['year'] or 'n.d.'}｜{context['title']}**（{role}）："
            f"{truncate(context['abstract'] or '（摘要缺失）', 180)} {citations.marker_for(context)}"
        )
    years = sorted({_as_int(context.get("year"), 0) for context in contexts if context.get("year")})
    head = (
        f"共 {len(contexts)} 篇文献，时间跨度 {years[0]}–{years[-1]} 年。"
        if years
        else f"共 {len(contexts)} 篇文献。"
    )
    return head + "\n\n" + "\n".join(lines)


def _fallback_foundation(
    contexts: Sequence[Mapping[str, Any]],
    nodes_by_id: Mapping[str, Mapping[str, Any]],
    citations: _Citations,
) -> str:
    foundation = [context for context in contexts if (nodes_by_id.get(context["id"]) or {}).get("role") == "foundation"]
    if not foundation:
        return "当前图谱未识别出满足「pagerank ≥ 中位数 × 1.3 且入度 ≥ 2」的基石论文。"
    pageranks = [float(node.get("pagerank") or 0.0) for node in nodes_by_id.values()]
    median = statistics.median(pageranks) if pageranks else 0.0
    lines = []
    for context in sorted(foundation, key=lambda item: -float((nodes_by_id.get(item["id"]) or {}).get("pagerank") or 0.0)):
        node = nodes_by_id[context["id"]]
        ratio = (float(node.get("pagerank") or 0.0) / median) if median > 0 else 0.0
        lines.append(
            f"- 《{context['title']}》（{context['year'] or 'n.d.'}）被引 {node.get('indegree', 0)} 次，"
            f"pagerank={float(node.get('pagerank') or 0.0):.3f}（全图中位数 {median:.3f} 的 {ratio:.2f} 倍）："
            f"{truncate(context['abstract'] or '（摘要缺失）', 160)} {citations.marker_for(context)}"
        )
    return "\n".join(lines)


def _fallback_methods(
    contexts: Sequence[Mapping[str, Any]],
    citations: _Citations,
) -> str:
    grouped: dict[int, list[Mapping[str, Any]]] = {}
    for context in contexts:
        grouped.setdefault(_as_int(context.get("year"), 0), []).append(context)
    lines = []
    for year in sorted(grouped):
        members = grouped[year]
        corpus = " ".join(f"{member['title']} {member['abstract']}" for member in members)
        themes = [word for word in keyword_candidates(corpus, limit=6) if len(word) >= 3][:3]
        representatives = "、".join(
            f"《{member['title']}》{citations.marker_for(member)}" for member in members
        )
        lines.append(
            f"- **{year or '年份未知'} 年**：{len(members)} 篇工作，主题集中在 "
            f"{'、'.join(themes) or '未标注主题'}；代表工作 {representatives}。"
        )
    return "\n".join(lines)


def _fallback_future(directions: Sequence[Mapping[str, Any]]) -> str:
    open_items = [item for item in directions if item.get("status") == "open"]
    solved_items = [item for item in directions if item.get("status") == "solved"]
    lines: list[str] = []
    if open_items:
        lines.append("**仍待解决的方向（open）**")
        for item in open_items:
            lines.append(
                f"- {truncate(str(item.get('direction') or ''), 200)}"
                f"（来源《{item.get('source_title')}》，{item.get('year') or 'n.d.'}）"
                f"{item.get('citation') or ''}"
            )
    if solved_items:
        lines.append("**已被后续工作解决的方向（solved，已排除）**")
        for item in solved_items:
            evidence = item.get("evidence") or {}
            lines.append(
                f"- {truncate(str(item.get('direction') or ''), 160)}{item.get('citation') or ''} → 证据："
                f"{evidence.get('citation') or ''}《{evidence.get('title')}》（{evidence.get('year')}）"
            )
    if not lines:
        lines.append("未从解析文本中提取到明确的 Future Work / Limitations 段落，建议补充解析结果的章节结构。")
    return "\n".join(lines)


def _directions_markdown(directions: Sequence[Mapping[str, Any]]) -> str:
    """结构化方向清单，追加在「未来方向」正文之后，保证 open/solved 都可追溯。"""
    open_items = [item for item in directions if item.get("status") == "open"]
    solved_items = [item for item in directions if item.get("status") == "solved"]
    lines: list[str] = []
    if open_items:
        lines.append("**开放方向（open）**")
        for item in open_items:
            lines.append(
                f"- {truncate(str(item.get('direction') or ''), 180)} —— 来源《{item.get('source_title')}》"
                f"（{item.get('year') or 'n.d.'}）{item.get('citation') or ''}"
            )
    if solved_items:
        lines.append("**已被后续工作解决（solved，已从开放方向排除）**")
        for item in solved_items:
            evidence = item.get("evidence") or {}
            lines.append(
                f"- {truncate(str(item.get('direction') or ''), 160)} —— 证据："
                f"{evidence.get('citation') or ''}《{evidence.get('title')}》（{evidence.get('year')}）"
                f"，命中关键词：{'、'.join(evidence.get('matched_terms') or []) or '无'}"
            )
    return "\n".join(lines)


# ============================================================ 正文生成
def _strip_section_heading(body: str, name: str) -> str:
    text = (body or "").strip()
    if not text:
        return ""
    lines = text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines:
        first = lines[0].strip()
        if first.startswith("#") and name in first:
            lines.pop(0)
            while lines and not lines[0].strip():
                lines.pop(0)
    return "\n".join(lines).strip()


def _link_markers(body: str, citations: _Citations) -> str:
    """把模型输出里的 ``[paper_id]`` 换成 ``[C#]`` 引用标记。"""
    text = body or ""
    for item in citations.items:
        text = text.replace(f"[{item['paper_id']}]", item["marker"])
    return text


def _generate_section(
    name: str,
    focus: str,
    payload_papers: Sequence[Mapping[str, Any]],
    graph_payload: Mapping[str, Any],
    fallback: str,
    citations: _Citations,
) -> tuple[str, str, bool]:
    payload = {
        "name": name,
        "focus": focus,
        "papers": list(payload_papers),
        "graph": dict(graph_payload),
    }
    body = ""
    provider = "offline"
    degraded = False
    try:
        result = run_task("survey_section", payload)
        provider = str(getattr(result, "provider", "") or "offline")
        degraded = bool(getattr(result, "degraded", False))
        data = getattr(result, "data", None)
        if isinstance(data, dict):
            body = str(data.get("markdown") or "")
        if not body:
            body = str(getattr(result, "text", "") or "")
    except Exception as exc:  # 在线通道/处理器异常时本地降级
        logger.warning("survey_section 生成失败（%s），使用本地降级正文: %s", name, exc)
        body = ""
        provider = "offline"
        degraded = True
    body = _strip_section_heading(body, name)
    body = _link_markers(body, citations)
    if not body.strip():
        body = fallback
        degraded = True
    return body.strip(), provider, degraded


def _default_title(contexts: Sequence[Mapping[str, Any]]) -> str:
    corpus = " ".join(f"{context['title']} {truncate(context['abstract'], 200)}" for context in contexts)
    keywords = [word for word in keyword_candidates(corpus, limit=10) if len(word) >= 3][:3]
    topic = "、".join(keywords) or "局域文献"
    years = sorted({_as_int(context.get("year"), 0) for context in contexts if context.get("year")})
    span = f"{years[0]}–{years[-1]}" if years else "年份未知"
    return f"{topic}领域研究综述（{span}）"


# ============================================================ 公开接口
def survey(
    paper_ids: Sequence[str],
    *,
    settings: Settings | None = None,
    title: str | None = None,
    graph_id: str | None = None,
) -> dict[str, Any]:
    """生成领域综述并持久化。

    Args:
        paper_ids: 参与综述的论文 id；若同时给 ``graph_id``，仅保留图谱内的论文。
        settings: 可选配置。
        title: 自定义综述标题；缺省按主题词与年份跨度生成。
        graph_id: 复用已构建的图谱（避免重复建图）。

    Returns:
        ``{survey_id, graph_id, title, markdown, sections, papers, future_directions,
        excluded_ideas, citations, provider, degraded}``。
    """
    settings = settings or get_settings()
    requested = dedupe_keep_order(
        [str(paper_id).strip() for paper_id in (paper_ids or []) if str(paper_id or "").strip()]
    )

    if graph_id:
        graph = get_graph(str(graph_id))
        graph_ids = [str(pid) for pid in (graph.get("paper_ids") or []) if str(pid or "").strip()]
        if requested:
            allowed = set(graph_ids)
            scope = [paper_id for paper_id in requested if paper_id in allowed]
            if not scope:
                raise ValidationError(
                    "paper_ids 与 graph_id 没有交集", detail={"graph_id": graph_id}
                )
        else:
            scope = graph_ids
        if not scope:
            raise ValidationError("图谱中没有可用于综述的论文", detail={"graph_id": graph_id})
    else:
        if not requested:
            raise ValidationError("paper_ids 不能为空")
        scope = requested
        graph = build_graph(scope, settings=settings)

    resolved_graph_id = str(graph.get("graph_id") or graph_id or "")
    nodes_by_id = {str(node.get("id")): node for node in (graph.get("nodes") or [])}
    contexts = sorted(
        (_load_context(paper_id, settings) for paper_id in scope),
        key=lambda item: (_as_int(item.get("year"), 0), str(item.get("title")).lower()),
    )

    citations = _Citations()
    for context in contexts:
        citations.marker_for(context)
    directions = _extract_directions(contexts, citations)
    excluded = [item for item in directions if item.get("status") == "solved"]

    payload_papers = [
        {
            "id": context["id"],
            "title": context["title"],
            "year": context["year"],
            "abstract": truncate(context["abstract"], 600),
            "role": ROLE_LABELS_ZH.get(
                str((nodes_by_id.get(context["id"]) or {}).get("role") or "normal"), "常规工作"
            ),
        }
        for context in contexts
    ]
    metrics = graph.get("metrics") or {}
    graph_payload = {
        "graph_id": resolved_graph_id,
        "nodes": metrics.get("nodes", len(contexts)),
        "edges": metrics.get("edges", 0),
        "foundation": [
            context["title"]
            for context in contexts
            if (nodes_by_id.get(context["id"]) or {}).get("role") == "foundation"
        ],
        "open_directions": [str(item.get("direction") or "") for item in directions if item.get("status") == "open"],
    }

    fallbacks = {
        "研究脉络": _fallback_timeline(contexts, nodes_by_id, citations),
        "基石工作": _fallback_foundation(contexts, nodes_by_id, citations),
        "方法演化": _fallback_methods(contexts, citations),
        "未来方向": _fallback_future(directions),
    }
    sections: dict[str, str] = {}
    providers: list[str] = []
    degraded = False
    for name in SECTION_TITLES:
        body, provider, section_degraded = _generate_section(
            name,
            SECTION_FOCUS[name],
            payload_papers,
            graph_payload,
            fallbacks[name],
            citations,
        )
        if provider and provider not in providers:
            providers.append(provider)
        degraded = degraded or section_degraded
        if name == "未来方向" and directions:
            appendix = _directions_markdown(directions)
            if appendix and appendix not in body:
                body = f"{body}\n\n{appendix}".strip()
        sections[name] = body

    provider = "、".join(providers) or "offline"
    survey_title = str(title or "").strip() or _default_title(contexts)
    header = (
        f"> 基于局部引用图谱 {resolved_graph_id}（{metrics.get('nodes', len(contexts))} 篇文献 / "
        f"{metrics.get('edges', 0)} 条引用边）生成；生成通道：{provider}；"
        f"降级：{'是' if degraded else '否'}。"
    )
    markdown_parts = [f"# {survey_title}", "", header]
    for name in SECTION_TITLES:
        markdown_parts.extend(["", f"## {name}", "", sections[name].strip()])
    markdown = "\n".join(markdown_parts).strip() + "\n"

    papers = []
    for context in contexts:
        node = nodes_by_id.get(context["id"]) or {}
        papers.append(
            {
                "id": context["id"],
                "title": context["title"],
                "year": context["year"],
                "role": str(node.get("role") or "normal"),
                "pagerank": float(node.get("pagerank") or 0.0),
                "indegree": _as_int(node.get("indegree"), 0),
                "outdegree": _as_int(node.get("outdegree"), 0),
            }
        )

    meta = {
        "future_directions": directions,
        "excluded_ideas": excluded,
        "citations": citations.items,
        "provider": provider,
        "degraded": degraded,
        "paper_titles": {context["id"]: context["title"] for context in contexts},
    }
    survey_id = repo.save_survey(
        {
            "graph_id": resolved_graph_id,
            "paper_ids": [context["id"] for context in contexts],
            "title": survey_title,
            "markdown": markdown,
            "sections": {**sections, "_meta": meta},
        }
    )
    return {
        "id": survey_id,
        "survey_id": survey_id,
        "graph_id": resolved_graph_id,
        "title": survey_title,
        "markdown": markdown,
        "sections": sections,
        "paper_ids": [context["id"] for context in contexts],
        "papers": papers,
        "future_directions": directions,
        "excluded_ideas": excluded,
        "citations": citations.items,
        "provider": provider,
        "degraded": degraded,
    }


def get_survey(survey_id: str) -> dict[str, Any]:
    """读取已保存的综述（含 future_directions / excluded_ideas / citations）。"""
    survey_id = str(survey_id or "").strip()
    if not survey_id:
        raise ValidationError("survey_id 不能为空")
    row = repo.get_survey(survey_id)
    if not row:
        raise NotFoundError(f"综述不存在：{survey_id}", detail={"survey_id": survey_id})
    sections = dict(row.get("sections") or {})
    meta = sections.pop("_meta", {}) or {}
    return {
        "id": row.get("id") or survey_id,
        "survey_id": row.get("id") or survey_id,
        "graph_id": str(row.get("graph_id") or ""),
        "title": str(row.get("title") or ""),
        "markdown": str(row.get("markdown") or ""),
        "sections": sections,
        "paper_ids": list(row.get("paper_ids") or []),
        "future_directions": meta.get("future_directions") or [],
        "excluded_ideas": meta.get("excluded_ideas") or [],
        "citations": meta.get("citations") or [],
        "provider": str(meta.get("provider") or ""),
        "degraded": bool(meta.get("degraded")),
        "created_at": row.get("created_at"),
    }


def list_surveys(limit: int = 20) -> list[dict[str, Any]]:
    """列出最近生成的综述摘要。"""
    limit = max(1, min(int(limit or 20), 200))
    out: list[dict[str, Any]] = []
    for row in repo.list_surveys(limit=limit):
        out.append(
            {
                "id": row.get("id"),
                "survey_id": row.get("id"),
                "title": row.get("title") or "",
                "graph_id": row.get("graph_id") or "",
                "paper_ids": list(row.get("paper_ids") or []),
                "created_at": row.get("created_at"),
            }
        )
    return out
