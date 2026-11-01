"""论文框架生成：六段式结构 + 只使用本地文献的编号引用。

生成流程
--------
1. 从 ``papers`` 表选出候选文献（显式 ``paper_ids`` 或全库相关性检索）；
2. 组装 payload 调用 ``outline`` 任务（在线优先，失败自动回落离线引擎）；
3. 章节统一为 Abstract / Introduction / Related Work / Method / Experiments /
   Conclusion（模型给出时附加 Limitations），缺失章节由离线模板补齐；
4. 正文 ``[n]`` 与参考文献编号双向校验：没有真实条目支撑的编号一律删除，
   未被引用的真实条目补一条 Related Work 引用（而不是删掉文献）；
5. ``repo.save_outline`` 落库并返回完整结果。
"""

from __future__ import annotations

import re
from typing import Any

from ..core.config import get_settings
from ..core.errors import ValidationError
from ..core.logging import get_logger
from ..core.textutil import normalize_ws, truncate
from ..db import repo
from ..providers.offline import run_offline
from .references import (
    build_references,
    check_library,
    check_references,
    cited_numbers,
    filter_markers,
    format_inline,
    format_reference,
    select_papers,
)
from .runtime import run_task_with

log = get_logger(__name__)

#: 必需章节：(key, 英文标题)
REQUIRED_SECTIONS: tuple[tuple[str, str], ...] = (
    ("abstract", "Abstract"),
    ("introduction", "Introduction"),
    ("related_work", "Related Work"),
    ("method", "Method"),
    ("experiments", "Experiments"),
    ("conclusion", "Conclusion"),
)
OPTIONAL_SECTIONS: tuple[tuple[str, str], ...] = (("limitations", "Limitations"),)
SECTION_ORDER = REQUIRED_SECTIONS + OPTIONAL_SECTIONS

HEADINGS: dict[str, dict[str, str]] = {
    "en": {
        "abstract": "Abstract",
        "introduction": "Introduction",
        "related_work": "Related Work",
        "method": "Method",
        "experiments": "Experiments",
        "conclusion": "Conclusion",
        "limitations": "Limitations",
    },
    "zh": {
        "abstract": "摘要",
        "introduction": "引言",
        "related_work": "相关工作",
        "method": "方法",
        "experiments": "实验",
        "conclusion": "结论",
        "limitations": "局限性",
    },
}

_SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "abstract": ("abstract", "摘要"),
    "introduction": ("introduction", "introduction and motivation", "引言", "绪论"),
    "related_work": (
        "related work",
        "related works",
        "background and related work",
        "literature review",
        "相关工作",
        "文献综述",
    ),
    "method": (
        "method",
        "methods",
        "methodology",
        "method sketch",
        "proposed method",
        "approach",
        "方法",
        "模型与方法",
    ),
    "experiments": (
        "experiments",
        "experiment",
        "experimental setup",
        "evaluation",
        "results",
        "实验",
        "实验结果",
    ),
    "conclusion": ("conclusion", "conclusions", "conclusion and future work", "结论", "总结"),
    "limitations": ("limitations", "discussion and limitations", "局限", "局限性"),
}
_ALIAS_LOOKUP = {
    alias: key for key, aliases in _SECTION_ALIASES.items() for alias in aliases
}

#: TASK_SPECS 中 outline 任务允许的顶层字段 -> 规范章节 key
_TOP_LEVEL_KEYS: dict[str, str] = {
    "abstract": "abstract",
    "introduction": "introduction",
    "related_work": "related_work",
    "method": "method",
    "method_sketch": "method",
    "methodology": "method",
    "experiments": "experiments",
    "conclusion": "conclusion",
    "limitations": "limitations",
}

_HEADING_PREFIX_RE = re.compile(r"^\d+[.)]?\s*")


def _alias_key(name: str) -> str | None:
    text = normalize_ws(str(name or "")).strip().lower()
    text = text.rstrip(":：").strip()
    text = _HEADING_PREFIX_RE.sub("", text).strip()
    return _ALIAS_LOOKUP.get(text)


def _as_text_lines(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [line.rstrip() for line in value.splitlines() if line.strip()]
    if isinstance(value, dict):
        return _as_text_lines(value.get("text") or value.get("content"))
    if isinstance(value, (list, tuple)):
        lines: list[str] = []
        for item in value:
            lines.extend(_as_text_lines(item))
        return lines
    return [str(value).strip()]


def _parse_markdown(markdown: str) -> tuple[dict[str, list[str]], str]:
    """从自由格式 Markdown 中抽取章节与标题。"""
    buckets: dict[str, list[str]] = {}
    title = ""
    current: str | None = None
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            heading = line[level:].strip().rstrip("#").strip()
            key = _alias_key(heading)
            if level == 1 and not title and key not in ("abstract",):
                title = heading
                current = None
                continue
            if key:
                current = key
                buckets.setdefault(current, [])
            else:
                current = None
            continue
        if current:
            buckets[current].append(line)
    return buckets, title


def _extract_sections(result: Any) -> tuple[dict[str, list[str]], str]:
    """把 LLMResult 的 data / text 归一化成 {章节 key: [行]}。"""
    data = result.data if isinstance(result.data, dict) else {}
    sections: dict[str, list[str]] = {}
    title = ""
    raw_title = data.get("title") or data.get("paper_title")
    if isinstance(raw_title, str):
        title = raw_title.strip()

    raw_sections = data.get("sections")
    if isinstance(raw_sections, dict):
        for name, value in raw_sections.items():
            key = _alias_key(str(name))
            if key:
                sections[key] = _as_text_lines(value)

    for name, key in _TOP_LEVEL_KEYS.items():
        if sections.get(key):
            continue
        value = data.get(name)
        if value not in (None, ""):
            lines = _as_text_lines(value)
            if lines:
                sections[key] = lines

    text = result.text or ""
    if text.strip():
        parsed, parsed_title = _parse_markdown(text)
        title = title or parsed_title
        for key, lines in parsed.items():
            if not sections.get(key):
                sections[key] = lines
    return sections, title


def _default_title(idea: str) -> str:
    return f"A Study of {truncate(idea, 90)}"


def _ensure_related_coverage(
    sections: list[dict[str, Any]],
    references: list[dict[str, Any]],
    language: str,
) -> int:
    """为未被正文引用的真实文献补一条 Related Work 引用，保证反向校验通过。"""
    cited: set[int] = set()
    for section in sections:
        cited.update(section["citations"])
    uncovered = [ref for ref in references if ref["idx"] not in cited]
    if not uncovered:
        return 0
    prefix = "- 相关工作：" if language == "zh" else "- Related work: "
    extra = [f"{prefix}{format_inline(ref)} [{ref['idx']}]" for ref in uncovered]

    target = next((sec for sec in sections if sec["key"] == "related_work"), None)
    if target is None:
        insert_at = next(
            (index + 1 for index, sec in enumerate(sections) if sec["key"] == "introduction"),
            1,
        )
        target = {
            "key": "related_work",
            "heading": HEADINGS[language]["related_work"],
            "text": "",
            "citations": [],
        }
        sections.insert(insert_at, target)
    target["text"] = "\n".join([target["text"], *extra]).strip()
    target["citations"] = cited_numbers(target["text"])
    return len(uncovered)


def _sections_check(sections: list[dict[str, Any]]) -> dict[str, Any]:
    present = {section["key"] for section in sections}
    missing = [key for key, _heading in REQUIRED_SECTIONS if key not in present]
    empty = [section["key"] for section in sections if not section["text"].strip()]
    passed = not missing and not empty
    detail = (
        f"必需章节 {len(REQUIRED_SECTIONS) - len(missing)}/{len(REQUIRED_SECTIONS)} 齐全"
    )
    if missing:
        detail += f"；缺少: {', '.join(missing)}"
    if empty:
        detail += f"；内容为空: {', '.join(empty)}"
    return {"name": "sections", "passed": passed, "detail": detail}


def _citation_markers_check(dropped: list[int]) -> dict[str, Any]:
    unique = sorted(set(dropped))
    if not unique:
        return {
            "name": "citation_markers",
            "passed": True,
            "detail": "正文中的引用标记全部对应本地文献条目",
        }
    return {
        "name": "citation_markers",
        "passed": False,
        "detail": f"已删除 {len(unique)} 个无法对应本地文献的引用标记: {unique}",
    }


def _render_markdown(
    title: str,
    sections: list[dict[str, Any]],
    references: list[dict[str, Any]],
) -> str:
    lines: list[str] = [f"# {title}", ""]
    for section in sections:
        lines.extend([f"## {section['heading']}", "", section["text"], ""])
    lines.extend(["## References", ""])
    if references:
        lines.extend(
            f"[{ref['idx']}] {format_reference(ref)}" for ref in references
        )
    else:
        lines.append("（本地文献库中暂无可用文献，未生成任何引用条目）")
    return "\n".join(lines).rstrip() + "\n"


def draft_outline(
    idea: str,
    *,
    paper_ids: list[str] | None = None,
    title: str | None = None,
    settings: Any = None,
    language: str = "en",
) -> dict[str, Any]:
    """生成论文框架，参考文献只来自本地文献库。"""
    resolved = settings or get_settings()
    idea_text = (idea or "").strip()
    if not idea_text:
        raise ValidationError("研究想法 (idea) 不能为空")
    language = "zh" if str(language or "").strip().lower().startswith("zh") else "en"

    papers, missing = select_papers(idea_text, paper_ids)
    references = build_references(papers)
    payload: dict[str, Any] = {
        "idea": idea_text,
        "language": language,
        "title": (title or "").strip(),
        "evidence": [
            {
                "ref": ref["idx"],
                "paper_id": ref["paper_id"],
                "title": ref["title"],
                "authors": ref["authors"],
                "year": ref["year"],
                "venue": ref["venue"],
                "doi": ref["doi"],
                "arxiv_id": ref["arxiv_id"],
                "abstract": str(paper.get("abstract") or ""),
            }
            for ref, paper in zip(references, papers)
        ],
        "requirement": (
            "每个论断用 [n] 标注本地文献编号；只允许引用给定 evidence；"
            "禁止虚构任何参考文献、作者、年份或 DOI。"
        ),
    }

    result = run_task_with("outline", payload, resolved, expect_json=True)
    template = run_offline("outline", payload)
    model_sections, model_title = _extract_sections(result)
    template_sections, _template_title = _extract_sections(template)

    valid = {ref["idx"] for ref in references}
    dropped: list[int] = []
    texts: dict[str, str] = {}
    for key, _heading in SECTION_ORDER:
        chosen = model_sections.get(key) or template_sections.get(key) or []
        cleaned, removed = filter_markers("\n".join(chosen).strip(), valid)
        dropped.extend(removed)
        if not cleaned.strip():
            fallback, removed_fallback = filter_markers(
                "\n".join(template_sections.get(key) or []).strip(), valid
            )
            dropped.extend(removed_fallback)
            cleaned = fallback
        texts[key] = cleaned.strip()

    headings = HEADINGS[language]
    sections: list[dict[str, Any]] = []
    for key, _heading in SECTION_ORDER:
        text = texts.get(key, "").strip()
        if not text:
            continue
        sections.append(
            {
                "key": key,
                "heading": headings[key],
                "text": text,
                "citations": cited_numbers(text),
            }
        )

    uncovered_count = _ensure_related_coverage(sections, references, language)
    final_title = (title or "").strip() or model_title or _default_title(idea_text)
    markdown = _render_markdown(final_title, sections, references)
    checks = [
        _sections_check(sections),
        check_references(sections, references),
        check_library(references),
        _citation_markers_check(dropped),
    ]

    outline_id = repo.save_outline(
        {
            "idea": idea_text,
            "paper_ids": [ref["paper_id"] for ref in references],
            "markdown": markdown,
            "sections": sections,
            "provider": result.provider,
        }
    )

    notes: list[str] = []
    if result.notes:
        notes.append(str(result.notes))
    if result.degraded:
        notes.append("在线模型不可用，本次论文框架由离线确定性引擎生成")
    if missing:
        notes.append(f"paper_ids 中以下文献不在本地库，已忽略: {', '.join(missing)}")
    if dropped:
        notes.append(
            f"已删除 {len(set(dropped))} 个无法对应本地文献的引用标记: {sorted(set(dropped))}"
        )
    if uncovered_count:
        notes.append(f"{uncovered_count} 条未被正文引用的真实文献已在 Related Work 中补充引用")
    if references:
        notes.append(f"参考文献 {len(references)} 条全部取自本地文献库（papers 表），未引入外部条目")
    else:
        notes.append("本地文献库没有可用文献：References 留空，未生成任何虚构引用")

    log.info(
        "outline %s: sections=%d refs=%d provider=%s degraded=%s",
        outline_id,
        len(sections),
        len(references),
        result.provider,
        result.degraded,
    )
    return {
        "outline_id": outline_id,
        "sections": sections,
        "references": references,
        "markdown": markdown,
        "checks": checks,
        "provider": result.provider,
        "degraded": bool(result.degraded),
        "notes": notes,
    }


def get_outline(outline_id: str) -> dict[str, Any] | None:
    """读取已落库的论文框架。"""
    pid = str(outline_id or "").strip()
    if not pid:
        raise ValidationError("outline_id 不能为空")
    return repo.get_outline(pid)


def list_outlines(limit: int = 20) -> list[dict[str, Any]]:
    """列出最近的论文框架（按创建时间倒序）。"""
    try:
        size = int(limit)
    except (TypeError, ValueError) as exc:
        raise ValidationError("limit 必须是整数") from exc
    if size <= 0:
        raise ValidationError("limit 必须为正整数")
    return repo.list_outlines(size)
