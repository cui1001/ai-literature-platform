"""参考文献的真实性与引用编号校验。

实现原则
--------
1. **只从 ``papers`` 表取材**：候选论文由 :func:`select_papers` 从本地文献库
   检索，标题 / 作者 / 年份 / 会议 / DOI / arXiv 号全部直接复制入库元数据，
   缺失字段留空串，绝不补写或猜测；
2. **编号唯一来源**：正文中的 ``[n]`` 与 :func:`build_references` 生成的
   ``idx`` 一一对应，模型产出的其它编号会被 :func:`filter_markers` 删除；
3. **双向校验**：正文出现的编号必须在参考文献中真实存在，参考文献条目也
   必须被正文引用（未覆盖条目由调用方补一条 Related Work 引用，而不是删掉
   一篇真实文献）。
"""

from __future__ import annotations

import re
from typing import Any

from ..core.textutil import extract_citations, jaccard, token_set, truncate
from ..db import repo

#: ``[n]`` 以及 ``[1,2]`` / ``[1-3]`` / ``[1–3]`` 形式的编号标记
MARKER_RE = re.compile(r"\[\s*\d{1,3}(?:\s*[,，\-–—]\s*\d{1,3})*\s*\]")
_RANGE_RE = re.compile(r"^(\d{1,3})\s*[\-–—]\s*(\d{1,3})$")
_NUMBER_RE = re.compile(r"\d{1,3}")

#: 句末 / 分隔符之前不留空格（中英文混排时清理删标记后的空白）
_BEFORE_PUNCT_RE = re.compile(r"[ \t]+([.,;:!?，。；：！？、)\]}）】」』])")

MAX_REFERENCES = 12


def _authors_of(paper: dict[str, Any]) -> list[str]:
    authors = paper.get("authors") or []
    if isinstance(authors, str):
        try:
            import json

            decoded = json.loads(authors)
            authors = decoded if isinstance(decoded, list) else [authors]
        except (TypeError, ValueError):
            authors = [authors]
    return [str(author).strip() for author in authors if str(author).strip()]


def _year_of(paper: dict[str, Any]) -> int | None:
    try:
        year = paper.get("year")
        return int(year) if year not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _paper_text(paper: dict[str, Any]) -> str:
    parts = [
        paper.get("title") or "",
        paper.get("abstract") or "",
        paper.get("venue") or "",
        " ".join(_authors_of(paper)),
    ]
    keywords = paper.get("keywords")
    if isinstance(keywords, list):
        parts.extend(str(item) for item in keywords)
    return " ".join(part for part in parts if part)


def select_papers(
    idea: str,
    paper_ids: list[str] | None = None,
    limit: int = MAX_REFERENCES,
) -> tuple[list[dict[str, Any]], list[str]]:
    """挑选候选文献。

    给了 ``paper_ids`` 时严格按给定顺序取本地库条目；未在库中的 id 会记录在
    第二个返回值里（由调用方写入 notes，而不是伪造一条文献）。没给
    ``paper_ids`` 时按 idea 与 title/abstract/venue/authors 的 Jaccard 相关度
    从全库检索。
    """
    if limit <= 0:
        raise ValueError("limit 必须为正整数")
    if paper_ids is not None:
        papers: list[dict[str, Any]] = []
        missing: list[str] = []
        seen: set[str] = set()
        for raw in paper_ids:
            pid = str(raw or "").strip()
            if not pid or pid in seen:
                continue
            seen.add(pid)
            paper = repo.get_paper(pid)
            if paper is None:
                missing.append(pid)
            else:
                papers.append(paper)
        return papers[:limit], missing

    idea_tokens = token_set(idea or "")
    scored: list[tuple[float, dict[str, Any]]] = []
    for paper in repo.all_papers():
        score = jaccard(idea_tokens, token_set(_paper_text(paper)))
        year = _year_of(paper) or 0
        title = str(paper.get("title") or "")
        scored.append((score, year, title, str(paper.get("id") or ""), paper))
    # 相关度优先，其次新近度、标题、id，保证结果与输入顺序无关且确定
    scored.sort(key=lambda item: (-item[0], -item[1], item[2], item[3]))
    return [item[4] for item in scored[:limit]], []


def build_references(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把本地库论文转成 1-based 编号的参考文献列表（字段原样复制）。"""
    references: list[dict[str, Any]] = []
    for index, paper in enumerate(papers, start=1):
        references.append(
            {
                "idx": index,
                "paper_id": str(paper.get("id") or ""),
                "title": str(paper.get("title") or ""),
                "authors": _authors_of(paper),
                "year": _year_of(paper),
                "venue": str(paper.get("venue") or ""),
                "doi": str(paper.get("doi") or ""),
                "arxiv_id": str(paper.get("arxiv_id") or ""),
            }
        )
    return references


def format_reference(ref: dict[str, Any]) -> str:
    """单条参考文献的纯文本格式（作者 / 标题 / 会议 / 年份 / DOI / arXiv）。"""
    authors = ref.get("authors") or []
    if len(authors) == 1:
        author_part = authors[0]
    elif len(authors) == 2:
        author_part = f"{authors[0]} and {authors[1]}"
    elif len(authors) > 2:
        author_part = f"{authors[0]} et al."
    else:
        author_part = ""

    title = str(ref.get("title") or "").strip() or "Untitled"
    title_part = title if title.endswith((".", "?", "!")) else f"{title}."
    parts = [part for part in (author_part, title_part) if part]

    venue = str(ref.get("venue") or "").strip()
    year = ref.get("year")
    tail = ", ".join(item for item in (venue, str(year) if year else "") if item)
    if tail:
        parts.append(tail)

    doi = str(ref.get("doi") or "").strip()
    if doi:
        parts.append(f"doi:{doi}")
    arxiv_id = str(ref.get("arxiv_id") or "").strip()
    if arxiv_id:
        parts.append(f"arXiv:{arxiv_id}")
    return " ".join(parts)


def format_inline(ref: dict[str, Any]) -> str:
    """正文中的简短引用描述：``Authors et al., Title (Year)``。"""
    authors = ref.get("authors") or []
    if len(authors) > 2:
        author_part = f"{authors[0]} et al."
    elif authors:
        author_part = " and ".join(authors)
    else:
        author_part = ""
    title = truncate(str(ref.get("title") or ""), 80)
    year = ref.get("year")
    suffix = f" ({year})" if year else ""
    return ", ".join(part for part in (author_part, title) if part) + suffix


def cited_numbers(text: str) -> list[int]:
    """正文中出现过的合法引用编号（去重、升序）。"""
    return extract_citations(text)


def _expand_marker(marker: str) -> list[int]:
    body = marker.strip("[]").strip()
    numbers: list[int] = []
    for chunk in re.split(r"\s*[,，]\s*", body):
        chunk = chunk.strip()
        if not chunk:
            continue
        match = _RANGE_RE.match(chunk)
        if match:
            start, end = int(match.group(1)), int(match.group(2))
            if start <= end and end - start <= 100:
                numbers.extend(range(start, end + 1))
                continue
        numbers.extend(int(item) for item in _NUMBER_RE.findall(chunk))
    return numbers


def filter_markers(text: str, valid: set[int]) -> tuple[str, list[int]]:
    """删除无法对应真实条目（或编号非法）的引用标记。

    返回清洗后的文本与被删除的编号（按出现顺序）。混合编号 ``[1, 9]`` 只
    保留有效编号，全部无效时整段标记删除。
    """
    if not text:
        return text, []
    dropped: list[int] = []

    def _replace(match: re.Match[str]) -> str:
        numbers = _expand_marker(match.group(0))
        kept = [number for number in numbers if number in valid]
        removed = [number for number in numbers if number not in valid]
        dropped.extend(removed)
        if not kept:
            return ""
        return "[" + ",".join(str(number) for number in dict.fromkeys(kept)) + "]"

    cleaned = MARKER_RE.sub(_replace, text)
    cleaned = _BEFORE_PUNCT_RE.sub(r"\1", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    return cleaned.strip(), dropped


def check_references(
    sections: list[dict[str, Any]],
    references: list[dict[str, Any]],
) -> dict[str, Any]:
    """双向校验：正文编号 ⊆ 参考文献编号，且每一条参考文献都被引用。"""
    cited: set[int] = set()
    for section in sections:
        cited.update(extract_citations(str(section.get("text") or "")))
        cited.update(int(number) for number in section.get("citations") or [])
    listed = {int(ref["idx"]) for ref in references}
    dangling = sorted(cited - listed)
    uncited = sorted(listed - cited)
    passed = not dangling and not uncited
    parts = [f"正文引用编号 {len(cited)} 个，参考文献条目 {len(listed)} 条"]
    if dangling:
        parts.append(f"无对应条目的正文编号: {dangling}")
    if uncited:
        parts.append(f"未被正文引用的条目: {uncited}")
    if passed:
        parts.append("双向一一对应")
    return {"name": "reference_check", "passed": passed, "detail": "；".join(parts)}


def _same_value(left: Any, right: Any) -> bool:
    if isinstance(left, list) or isinstance(right, list):
        return [str(item).strip() for item in left or []] == [
            str(item).strip() for item in right or []
        ]
    return str(left or "").strip() == str(right or "").strip()


def check_library(references: list[dict[str, Any]]) -> dict[str, Any]:
    """逐条回查本地库：每个编号都能在 ``papers`` 表找到同一篇真实文献。"""
    missing: list[int] = []
    mismatched: list[int] = []
    for ref in references:
        paper_id = str(ref.get("paper_id") or "")
        paper = repo.get_paper(paper_id) if paper_id else None
        if paper is None:
            missing.append(int(ref["idx"]))
            continue
        same_title = _same_value(ref.get("title"), paper.get("title"))
        same_authors = _same_value(ref.get("authors"), _authors_of(paper))
        same_year = _same_value(ref.get("year"), _year_of(paper))
        if not (same_title and same_authors and same_year):
            mismatched.append(int(ref["idx"]))
    passed = not missing and not mismatched
    detail = f"{len(references) - len(missing)}/{len(references)} 条参考文献可回溯到本地 papers 表"
    if missing:
        detail += f"；库中不存在: {missing}"
    if mismatched:
        detail += f"；元数据与库内记录不一致: {mismatched}"
    if passed:
        detail += "（标题 / 作者 / 年份逐条一致）"
    return {"name": "references_from_library", "passed": passed, "detail": detail}
