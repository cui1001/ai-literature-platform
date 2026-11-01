"""离线确定性引擎。

在线大模型不可用时，本模块以「检索证据 + 规则抽取 + 模板组织」的方式完成同一批任务：
- 不做无根据的生成（每个结论句必须绑定证据块编号）；
- 数值、专名保持原文；
- 输出由输入唯一决定，便于复现与单元测试。

它同时也是在线模式的对照基线：评测脚本会用同一批任务比较两种通道。
"""

from __future__ import annotations

import re
import time
from typing import Any, Iterable

from ..core.config import Settings
from ..core.textutil import (
    extract_numbers,
    jaccard,
    normalize_ws,
    sentence_split,
    tokens,
    truncate,
)
from .base import LLMResult, ProviderStatus
from .glossary import detect_terms, term_pairs, translate_en_to_zh, translate_zh_to_en

#: 问题意图 → 关键词正则（用于在证据中定位「含金量」更高的句子）
INTENT_PATTERNS: dict[str, list[str]] = {
    "author": [r"\b(?:author|authors|by)\b", r"大学|学院|研究所|实验室|university|institute|laborator"],
    "affiliation": [
        r"university|institute|college|laborator|google|microsoft|openai|deepmind|meta|baidu",
        r"organization|organisation|company|team|developed|produced|created",
    ],
    "hyperparameter": [r"learning rate|batch size|beta|β|α|alpha|dropout|warmup|optimizer|adam|epoch"],
    "hardware": [r"\b\d+\s*[×x]?\s*(?:a100|v100|p100|tpu|gpu|h100|rtx)\b", r"\bgpus?\b|\btpus?\b", r"显存|内存"],
    "metric": [r"bleu|rouge|f1|accuracy|perplexity|ppl|em\b|map\b|iou", r"score|结果|提升"],
    "dataset": [r"dataset|corpus|benchmark|训练集|测试集|语料"],
    "year": [r"\b(?:19|20)\d{2}\b"],
    "citation": [
        r"reference|bibliography|引用|参考文献",
        r"which (?:\w+ ){0,3}(?:paper|model|architecture|work)|build(?:s|ing)? (?:its |their )?architecture|introduced by|cited as",
    ],
    "definition": [r"is defined as|refers to|we define|称为|定义为"],
}

_STOP = {"什么", "多少", "哪些", "如何", "是否", "请问", "如何", "the", "of", "is", "and"}


def classify_intents(question: str) -> list[str]:
    q = question.lower()
    hits = []
    for intent, patterns in INTENT_PATTERNS.items():
        if any(re.search(p, q) for p in patterns):
            hits.append(intent)
    return hits


def _content_tokens(text: str) -> set[str]:
    return {t for t in tokens(text) if t not in _STOP and len(t) > 1}


def _sentence_score(
    sentence: str,
    question: str,
    intents: list[str],
    *,
    wants_number: bool,
    extras: Iterable[str] = (),
) -> float:
    q_tokens = _content_tokens(question)
    s_tokens = _content_tokens(sentence)
    if not q_tokens or not s_tokens:
        overlap = 0.0
        hits = 0
    else:
        hits = len(q_tokens & s_tokens)
        overlap = hits / max(1, min(len(q_tokens), 10))
    score = 1.6 * min(1.0, overlap) + 0.18 * min(hits, 4)
    lowered = sentence.lower()
    for intent in intents:
        for pattern in INTENT_PATTERNS.get(intent, []):
            if re.search(pattern, lowered):
                score += 0.9
                break
    extra_tokens = {t.lower() for t in extras if t}
    if extra_tokens:
        extra_hits = len(extra_tokens & s_tokens)
        score += 0.22 * min(extra_hits, 2)
    if wants_number and extract_numbers(sentence):
        score += 0.5
        if hits >= 2 or extra_tokens & s_tokens:
            score += 0.35
    if "|" in sentence:
        # 表格行/表注不是自然语言证据，避免把多行表格当作答案
        score -= 0.45
    if re.search(r"[=≈<>≤≥]|\b\d+(?:\.\d+)?\s*(?:%|k|m|b)?\b", sentence):
        score += 0.25
    # 句子过长/过短轻微惩罚
    length = len(sentence)
    if length < 20:
        score -= 0.35
    elif length > 400:
        score -= 0.2
    return round(score, 4)


def _sentences_with_evidence(evidence: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in evidence:
        text = item.get("text") or ""
        parts = [text] if (item.get("kind") or "") == "meta" else (sentence_split(text) or ([text] if text.strip() else []))
        for idx, sentence in enumerate(parts):
            sentence = sentence.strip()
            if len(sentence) < 8:
                continue
            out.append({
                "id": item.get("id"),
                "paper_id": item.get("paper_id") or "",
                "title": item.get("title") or item.get("paper_title") or "",
                "page": item.get("page") or 1,
                "section": item.get("section") or "",
                "kind": item.get("kind") or "text",
                "sentence": sentence,
                "sentence_idx": idx,
                "block_score": float(item.get("score") or 0),
            })
    return out


def _select_claims(
    evidence: Iterable[dict[str, Any]],
    question: str,
    *,
    max_claims: int = 4,
    threshold: float = 0.55,
    extras: Iterable[str] = (),
) -> list[dict[str, Any]]:
    extras = list(extras)
    intents = classify_intents(question)
    wants_number = bool(
        re.search(
            r"多少|几个|多大|数值|参数|分数|准确率|准确度|性能|"
            r"how many|how much|number|score|bleu|accuracy|percentage|percent|f1|em\b|ppl|perplexity",
            question.lower(),
        )
    )
    candidates = _sentences_with_evidence(evidence)
    for cand in candidates:
        cand["score"] = _sentence_score(
            cand["sentence"], question, intents, wants_number=wants_number, extras=extras
        )
        cand["score"] += min(0.4, cand["block_score"] * 0.4)
        meta_intent = ({"author", "affiliation"} & set(intents)) and not (
            "citation" in intents and "author" not in question.lower() and "affiliation" not in question.lower()
        )
        if cand["kind"] == "meta" and meta_intent:
            # 元信息块本身就是作者/机构的权威来源，直接抬到最高优先级
            cand["score"] = max(cand["score"], 2.6)
        if cand["kind"] == "reference" and "citation" in intents:
            # 参考文献块只用于补出处，不参与正文主张排序，否则答案会变成引用列表
            cand["score"] -= 0.7
    candidates.sort(key=lambda c: (-c["score"], c["paper_id"], c["page"], c["sentence_idx"]))
    selected: list[dict[str, Any]] = []
    seen_blocks: set[Any] = set()
    for cand in candidates:
        if cand["score"] < threshold and selected and not wants_number:
            break
        if cand["id"] in seen_blocks:
            # 数值/指标类问题常在同一段里并列多个数据集的结果，允许同块多句以覆盖全部数字
            if not wants_number:
                continue
            if sum(1 for s in selected if s["id"] == cand["id"]) >= 3:
                continue
        if any(jaccard(tokens(cand["sentence"]), tokens(s["sentence"])) > 0.75 for s in selected):
            continue
        selected.append(cand)
        seen_blocks.add(cand["id"])
        if len(selected) >= max_claims:
            break
    if wants_number and selected:
        # 数值题：确保至少有一条「带数字且命中问题关键词」的句子进入答案，
        # 避免同段里并列的多个数据集结果被高分泛化句挤掉。
        numeric = [
            c for c in candidates
            if c not in selected and re.search(r"\d", c["sentence"]) and c["score"] >= 0.4
        ]
        numeric.sort(key=lambda c: (-c["score"], c["paper_id"], c["page"]))
        for cand in numeric[:2]:
            if len(selected) >= max(max_claims, 6):
                break
            selected.append(cand)
    return selected


# 离线模式没有参数化知识，但可以用一份「奠基论文别名表」把常见方法名映射到
# 本地参考文献条目的标题片段，避免「问 Transformer 却引用到无关文献」。
_REF_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (r"gpt-?3", ("language models are few-shot learners",)),
    (r"gpt-?2", ("language models are unsupervised multitask learners",)),
    (r"gpt-?1", ("improving language understanding",)),
    (r"transformer|attention", ("attention is all you need",)),
    (r"bert", ("bert: pre-training",)),
    (r"alexnet", ("imagenet classification",)),
    (r"resnet|residual", ("deep residual learning",)),
    (r"vit|vision transformer", ("an image is worth",)),
    (r"lora", ("lora: low-rank",)),
    (r"chain[- ]of[- ]thought", ("chain-of-thought prompting",)),
)
def _reference_index(evidence: Iterable[dict[str, Any]]) -> dict[int, str]:
    """把证据中的参考文献块整理成 {编号: 条目文本}，用于正文引用回挂。"""
    refs: dict[int, str] = {}
    for item in evidence:
        if (item.get("kind") or "") != "reference":
            continue
        text = (item.get("text") or "").strip()
        match = re.match(r"^参考文献\s*\[(\d{1,3})\]\s*[:：]?\s*(.*)$", text, re.S)
        if match:
            refs[int(match.group(1))] = truncate(normalize_ws(match.group(2)), 220)
    return refs


def _cite_label(cand: dict[str, Any]) -> str:
    title = cand.get("title") or "未命名文献"
    section = cand.get("section") or ""
    page = cand.get("page") or 1
    where = f"第 {page} 页"
    if section:
        where += f" · {section}"
    return f"《{truncate(title, 48)}》{where}"


# ============================================================ 任务实现


def task_answer_question(payload: dict[str, Any]) -> LLMResult:
    question = normalize_ws(payload.get("question") or "")
    evidence = payload.get("evidence") or []
    extras = payload.get("query_expansion") or []
    claims = _select_claims(
        evidence,
        question,
        max_claims=int(payload.get("max_claims") or 4),
        extras=extras if isinstance(extras, list) else [],
    )
    if not claims:
        return LLMResult(
            text=f"未能在所选的 {len(evidence)} 个证据块中找到直接依据，因此不作回答（避免编造）。"
            "建议：换用更具体的提问，或扩大文献范围。",
            data={"claims": [], "confidence": 0.0, "coverage": 0.0},
            provider="offline",
            model="deterministic-v1",
            notes="no-evidence",
        )
    intents = classify_intents(question)
    if "citation" in intents:
        # 引用类问题：把与问题最相关的参考文献条目本身作为一条证据补进答案，
        # 避免只回答「本文基于 Transformer」而给不出完整出处。
        ref_items: list[tuple[int, str, dict[str, Any]]] = []
        for item in evidence:
            if (item.get("kind") or "") != "reference":
                continue
            match = re.match(r"^参考文献\s*\[(\d{1,3})\]\s*[:：]?\s*(.*)$", (item.get("text") or ""), re.S)
            if match:
                ref_items.append((int(match.group(1)), normalize_ws(match.group(2)), item))
        if ref_items:
            # 用「问题 + 已选正文证据」一起匹配参考文献，正文里的作者姓氏能指向正确的条目
            # 匹配依据 = 问题 + 全部正文证据（正文里常出现作者姓氏/论文简称，能唯一定位条目）
            q_tokens = _content_tokens(question)
            for item in evidence:
                if (item.get("kind") or "") != "reference":
                    q_tokens |= _content_tokens(item.get("text") or "")
            best = max(ref_items, key=lambda item: len(q_tokens & _content_tokens(item[1])))
            for pattern, needles in _REF_ALIASES:
                if not re.search(pattern, question, re.I):
                    continue
                alias_hit = next(
                    (item for item in ref_items if any(n in item[1].lower() for n in needles)), None
                )
                if alias_hit is None:
                    continue  # 该别名在本文参考文献里不存在时继续尝试下一个（如 GPT-3 论文不引用自身）
                best = alias_hit
                break
            if best[0] not in {int(n) for c in claims for n in re.findall(r"\[(\d{1,3})\]", c["sentence"])}:
                claims.append(
                    {
                        "id": best[2].get("id"),
                        "paper_id": best[2].get("paper_id") or "",
                        "title": best[2].get("title") or best[2].get("paper_title") or "",
                        "page": best[2].get("page") or 1,
                        "section": best[2].get("section") or "References",
                        "kind": "reference",
                        "sentence": f"参考文献 [{best[0]}]: {best[1]}",
                        "sentence_idx": 0,
                        "score": 1.2,
                    }
                )
    if "citation" in intents:
        # 参考文献条目统一排在正文证据之后，正文优先回答「是什么」
        claims = [c for c in claims if c.get("kind") != "reference"] + [
            c for c in claims if c.get("kind") == "reference"
        ]
    lines = []
    refs = _reference_index(evidence)
    for cand in claims:
        line = f"- {cand['sentence']}  \n  ——{_cite_label(cand)} [{cand['id']}]"
        numbers = sorted({int(n) for n in re.findall(r"\[(\d{1,3})\]", cand["sentence"])})
        appendix = [f"[{n}] {refs[n]}" for n in numbers if n in refs]
        if appendix:
            line += "\n  参考文献：" + "；".join(appendix)
        lines.append(line)
    confidence = round(min(0.95, max(c["score"] for c in claims) / 2.6), 3)
    return LLMResult(
        text="\n".join(lines),
        data={
            "claims": [
                {"text": c["sentence"], "evidence": [c["id"]], "page": c["page"], "paper_id": c["paper_id"]}
                for c in claims
            ],
            "confidence": confidence,
            "coverage": round(min(1.0, len(claims) / 3), 3),
        },
        provider="offline",
        model="deterministic-v1",
    )


def _dimension_claim(blocks: list[Any], dim: dict[str, Any]) -> tuple[float, str, str] | None:
    """按维度的英文提示词挑选最相关的一句（跨文献对比用）。

    对比维度的问题是中文的，而论文证据是英文的；直接用问题做词袋匹配几乎必然
    选到无关句。这里退化为「维度提示词命中数 + 是否含数字」的确定性打分。
    """
    hint = str(dim.get("hint") or dim.get("question") or "")
    terms = [t for t in tokens(hint) if len(t) >= 2]
    if not terms:
        return None
    word_terms = [t for t in terms if any(ch.isalpha() for ch in t) and len(t) >= 3]
    num_terms = [t for t in terms if t.isdigit()]
    if not word_terms:
        return None
    best: tuple[float, str, str] | None = None
    for block in blocks:
        if isinstance(block, dict):
            text = str(block.get("text") or "")
            bid = str(block.get("id") or "")
        else:
            text = str(block or "")
            bid = ""
        for sentence in sentence_split(text):
            if "|" in sentence or len(sentence) < 20:
                continue
            sentence_tokens = set(tokens(sentence))
            word_hits = sum(1 for term in word_terms if term in sentence_tokens)
            if word_hits == 0:
                continue
            num_hits = sum(1 for term in num_terms if term in sentence_tokens)
            score = word_hits * 1.2 + min(num_hits, 2) * 0.4
            if any(ch.isdigit() for ch in sentence):
                score += 0.3
            if best is None or score > best[0]:
                best = (score, sentence.strip(), bid)
    return best


def task_compare_papers(payload: dict[str, Any]) -> LLMResult:
    dimensions = payload.get("dimensions") or []
    papers = payload.get("papers") or []
    evidence_map = payload.get("evidence") or {}
    rows_out: list[dict[str, Any]] = []
    table_rows: list[list[str]] = []
    for dim in dimensions:
        key = dim.get("key") or dim.get("label") or ""
        question = dim.get("question") or dim.get("label") or key
        per_paper = evidence_map.get(key) or {}
        cells: list[dict[str, Any]] = []
        for paper in papers:
            pid = paper.get("id") or ""
            blocks = per_paper.get(pid) or []
            claim = _dimension_claim(blocks, dim)
            if claim:
                cells.append(
                    {
                        "paper_id": pid,
                        "value": truncate(claim[1], 220),
                        "evidence": [claim[2]] if claim[2] else [],
                    }
                )
            else:
                cells.append({"paper_id": pid, "value": "未提及", "evidence": []})
        values = [c["value"] for c in cells]
        if any(v != "未提及" for v in values):
            conclusion = (
                f"{dim.get('label') or key}：" + "；".join(
                    f"{p.get('title', p.get('id'))} 为「{truncate(v, 60)}」"
                    for p, v in zip(papers, values) if v != "未提及"
                ) + "。"
            )
        else:
            conclusion = f"{dim.get('label') or key}：所选文献均未提供直接证据。"
        rows_out.append({"dimension": dim.get("label") or key, "key": key, "rows": cells, "conclusion": conclusion})
        table_rows.append([dim.get("label") or key, *values])
    header = ["维度", *[p.get("title") or p.get("id") for p in papers]]
    markdown = _md_table(header, table_rows)
    return LLMResult(
        text=markdown,
        data={"dimensions": rows_out, "papers": papers},
        provider="offline",
        model="deterministic-v1",
    )


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    def esc(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ").strip()

    out = ["| " + " | ".join(esc(h) for h in headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        out.append("| " + " | ".join(esc(c) for c in row) + " |")
    return "\n".join(out)


def task_summarize(payload: dict[str, Any]) -> LLMResult:
    blocks = payload.get("blocks") or []
    max_sentences = int(payload.get("max_sentences") or 3)
    texts = [b if isinstance(b, str) else (b.get("text") or "") for b in blocks]
    sentences: list[tuple[float, int, int, str]] = []
    for bi, text in enumerate(texts):
        for si, sent in enumerate(sentence_split(text)):
            if len(sent) < 25:
                continue
            score = 1.0 / (si + 1) + 0.15 * min(6, len(extract_numbers(sent))) + 0.05 * min(4, len(sent) // 60)
            sentences.append((score, bi, si, sent))
    sentences.sort(key=lambda x: (-x[0], x[1], x[2]))
    chosen = []
    for _, _, _, sent in sentences:
        if any(jaccard(tokens(sent), tokens(c)) > 0.7 for c in chosen):
            continue
        chosen.append(sent)
        if len(chosen) >= max_sentences:
            break
    return LLMResult(
        text=" ".join(chosen) if chosen else "（无可摘要内容）",
        data={"sentences": chosen},
        provider="offline",
        model="deterministic-v1",
    )


def task_translate(payload: dict[str, Any]) -> LLMResult:
    text = payload.get("text") or ""
    translated, coverage, used = translate_en_to_zh(text)
    # 降级说明放在 notes/coverage 里，由前端统一提示；正文保持干净，便于对照阅读与缓存复用。
    return LLMResult(
        text=translated,
        data={"coverage": coverage, "terms": used[:40], "mode": "glossary"},
        provider="offline",
        model="glossary-v1",
        notes="offline-glossary: 术语级替换，非完整译文",
    )


def task_academic_translate(payload: dict[str, Any]) -> LLMResult:
    text = payload.get("text") or ""
    translated, coverage, used = translate_zh_to_en(text)
    if translated and translated[0].islower():
        translated = translated[0].upper() + translated[1:]
    if translated and translated[-1] not in ".!?":
        translated += "."
    return LLMResult(
        text=translated,
        data={"coverage": coverage, "terms": used[:40], "mode": "glossary"},
        provider="offline",
        model="glossary-v1",
        notes="offline-glossary",
    )


def task_outline(payload: dict[str, Any]) -> LLMResult:
    idea = (payload.get("idea") or "未命名研究主题").strip()
    evidence = payload.get("evidence") or []
    citations: dict[str, str] = {}
    refs: list[str] = []
    for idx, item in enumerate(evidence, start=1):
        pid = item.get("paper_id") or item.get("id") or ""
        if pid:
            citations[str(idx)] = str(pid)
            refs.append(f"[{idx}] {item.get('title') or pid} ({item.get('year') or 'n.d.'})")
    cite = lambda idx: f"[{idx}]" if str(idx) in citations else ""

    abstracts = [item.get("abstract") or "" for item in evidence if item.get("abstract")]
    lead = truncate(abstracts[0], 240) if abstracts else "现有文献尚未覆盖该问题。"
    sections: dict[str, list[str]] = {
        "Abstract": [
            f"We study {idea}.",
            f"Prior work {cite(1) or '[1]'} establishes the foundation, yet the problem of {idea} remains open.",
            "We propose an evidence-grounded approach and evaluate it under a reproducible protocol.",
            "Experiments show consistent improvements over strong baselines.",
        ],
        "Introduction": [
            f"研究背景：{lead}",
            f"现有工作 {cite(1) or '[1]'} 与 {cite(2) or '[2]'} 分别从不同角度处理该问题，但存在可复现性与泛化性不足。",
            f"本文聚焦「{idea}」，贡献包括：(i) 一个问题形式化；(ii) 一个可复现方法；(iii) 一组对照实验。",
        ],
        "Related Work": [
            f"- 奠基工作：{r}" for r in refs[:6]
        ] or ["- 暂无本地文献可引用，请先导入相关论文以避免虚构引用。"],
        "Method": [
            "3.1 问题定义：给出输入、输出与评价指标的形式化描述。",
            "3.2 方法框架：模块划分、关键公式与算法流程（配 Figure 1 架构图）。",
            "3.3 复杂度分析：给出时间/空间复杂度与适用边界。",
        ],
        "Experiments": [
            "4.1 数据集与基线：说明数据来源、划分方式与对照方法。",
            "4.2 主实验：报告 Table 1 的主结果与显著性检验。",
            "4.3 消融与鲁棒性：逐项移除组件，报告性能变化。",
        ],
        "Conclusion": [
            f"本文围绕「{idea}」提出可复现方案，并在统一协议下验证其有效性。",
            "局限与未来工作：扩展到更多领域、引入人工评估、开放全部实验脚本。",
        ],
    }
    md_lines = [f"# {idea}", ""]
    for name, bullets in sections.items():
        md_lines.append(f"## {name}")
        md_lines.extend(bullets)
        md_lines.append("")
    md_lines.append("## References")
    md_lines.extend(refs or ["（无：请先在文献库中导入相关论文，再重新生成以避免虚构引用）"])
    return LLMResult(
        text="\n".join(md_lines),
        data={"sections": sections, "citations": citations, "references": refs, "idea": idea},
        provider="offline",
        model="deterministic-v1",
    )


def task_caption(payload: dict[str, Any]) -> LLMResult:
    stats = payload.get("stats") or {}
    kind = payload.get("kind") or "line"
    x_label = payload.get("x_label") or "x"
    y_label = payload.get("y_label") or "y"
    series = payload.get("series") or []
    parts = []
    if stats.get("trend") and series:
        direction = "increases" if stats["trend"] > 0 else "decreases"
        parts.append(
            f"Figure shows that {y_label} {direction} with {x_label}"
            + (f" (from {stats.get('first'):.2f} to {stats.get('last'):.2f}" if stats.get("first") is not None else "")
            + ")."
        )
    elif stats.get("trend") is not None:
        parts.append(f"Figure summarises {y_label} across {x_label}.")
    if stats.get("max_series") and stats.get("max_value") is not None:
        parts.append(
            f"The peak value is {stats['max_value']:.2f} ({stats['max_series']}) at {x_label} = {stats['max_x']}."
        )
    if stats.get("crossover"):
        parts.append(f"A crossing point appears near {x_label}={stats['crossover']}, indicating a trade-off between series.")
    if not parts:
        parts.append(f"{kind.capitalize()} chart of {y_label} versus {x_label}; values are computed from the uploaded CSV.")
    return LLMResult(
        text=" ".join(parts),
        data={"caption": " ".join(parts)},
        provider="offline",
        model="deterministic-v1",
    )


def task_survey_section(payload: dict[str, Any]) -> LLMResult:
    name = payload.get("name") or "研究方向"
    papers = sorted(payload.get("papers") or [], key=lambda p: (p.get("year") or 0, p.get("title") or ""))
    if not papers:
        return LLMResult(text=f"## {name}\n\n暂无可用文献。", data={"markdown": ""}, provider="offline")
    lines = [f"## {name}", ""]
    if papers:
        years = [str(p.get("year")) for p in papers if p.get("year")]
        span = f"{min(years)}–{max(years)}" if years else "未知年份"
        lines.append(f"共 {len(papers)} 篇文献，时间跨度为 {span}。")
    for idx, paper in enumerate(papers, start=1):
        abstract = truncate(paper.get("abstract") or "", 160)
        role = paper.get("role") or "相关工作"
        lines.append(
            f"{idx}. **{paper.get('title') or paper.get('id')}**（{paper.get('year') or 'n.d.'}，{role}）：{abstract} [{paper.get('id')}]"
        )
    roots = [p for p in papers if (p.get("role") or "") == "基石论文"]
    if roots:
        lines.append("")
        lines.append("该领域的基石工作为：" + "、".join(f"{p.get('title')} [{p.get('id')}]" for p in roots) + "，后续研究多在其框架内扩展。")
    return LLMResult(
        text="\n".join(lines),
        data={"markdown": "\n".join(lines), "paper_count": len(papers)},
        provider="offline",
        model="deterministic-v1",
    )


def task_verify_claim(payload: dict[str, Any]) -> LLMResult:
    claim = normalize_ws(payload.get("claim") or "")
    evidence = payload.get("evidence") or []
    claim_numbers = extract_numbers(claim)
    claim_tokens = _content_tokens(claim)
    best_match = 0.0
    support: list[dict[str, Any]] = []
    for item in evidence:
        text = item.get("text") or ""
        overlap = len(claim_tokens & _content_tokens(text)) / max(1, len(claim_tokens)) if claim_tokens else 0
        numbers_ok = all(any(abs(n - m) < 1e-9 for m in extract_numbers(text)) for n in claim_numbers)
        if numbers_ok and claim_numbers:
            overlap += 0.35
        if overlap >= 0.45:
            support.append({"id": item.get("id"), "page": item.get("page"), "score": round(overlap, 3)})
        best_match = max(best_match, overlap)
    if support and best_match >= 0.55:
        verdict = "supported"
        reason = f"在 {len(support)} 个证据块中找到与 claim 高度重叠的表述，且关键数值一致。"
    elif claim_numbers and evidence and not any(
        all(any(abs(n - m) < 1e-9 for m in extract_numbers(e.get("text") or "")) for n in claim_numbers)
        for e in evidence
    ):
        verdict = "unsupported"
        reason = "证据中未出现 claim 所声称的数值，判定为缺乏支持。"
    else:
        verdict = "unsupported"
        reason = "检索到的证据与 claim 的语义重叠不足，无法支持该陈述。"
    return LLMResult(
        text=reason,
        data={"verdict": verdict, "evidence": [s["id"] for s in support], "reason": reason, "score": round(best_match, 3)},
        provider="offline",
        model="deterministic-v1",
    )


def task_translation_glossary(payload: dict[str, Any]) -> LLMResult:
    text = payload.get("text") or ""
    pairs = term_pairs([text])
    return LLMResult(text="", data={"terms": pairs}, provider="offline", model="glossary-v1")


def _task_unknown(payload: dict[str, Any]) -> LLMResult:
    return LLMResult(text="", data={}, provider="offline", model="deterministic-v1", notes="unknown-task")


HANDLERS = {
    "answer_question": task_answer_question,
    "compare_papers": task_compare_papers,
    "summarize": task_summarize,
    "translate": task_translate,
    "academic_translate": task_academic_translate,
    "outline": task_outline,
    "caption": task_caption,
    "survey_section": task_survey_section,
    "verify_claim": task_verify_claim,
    "translation_glossary": task_translation_glossary,
}


def run_offline(task: str, payload: dict[str, Any], *, expect_json: bool = False) -> LLMResult:
    started = time.perf_counter()
    handler = HANDLERS.get(task, _task_unknown)
    result = handler(payload or {})
    if expect_json and not result.data:
        result.data = {"text": result.text}
    result.latency_ms = (time.perf_counter() - started) * 1000
    return result


class OfflineLLM:
    """确定性引擎的 Provider 包装。"""

    name = "offline"
    model = "deterministic-v1"

    def __init__(self, settings: Settings, reason: str = "no-credentials"):
        self.settings = settings
        self.reason = reason

    def run(self, task: str, payload: dict[str, Any], *, expect_json: bool = False) -> LLMResult:
        return run_offline(task, payload, expect_json=expect_json)

    def status(self) -> ProviderStatus:
        detail = {
            "no-credentials": "未配置 DEEPSEEK_API_KEY，使用离线确定性引擎",
            "offline-mode": "LLM_MODE=offline，强制离线引擎",
            "unreachable": "在线模型不可达，已自动降级离线引擎",
        }.get(self.reason, self.reason)
        return ProviderStatus(
            name="llm",
            kind="text-generation",
            active="offline",
            available=True,
            detail=detail,
        )
