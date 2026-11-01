"""多篇论文跨文献对比。

流程：
1. 预定义「可比维度」（参数量、层数、上下文窗口、LayerNorm 位置、训练硬件、评测指标……）；
2. 每个维度在每篇论文内独立检索证据，避免一篇论文的证据污染另一篇；
3. 结构化对比交给生成模型（在线）或确定性抽取（离线），缺失维度统一写「未提及」；
4. 每个非空单元格都回挂证据锚点，并向用户报告「有证据支撑的单元格比例」。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..core.config import Settings, get_settings
from ..core.errors import ValidationError
from ..core.logging import get_logger
from ..db import repo
from ..indexing.retriever import RetrievedChunk, get_retriever
from ..providers.offline import run_offline
from .citations import Citation
from .verifier import note_ok

logger = get_logger(__name__)

DIMENSIONS: list[dict[str, str]] = [
    {"key": "params", "label": "模型参数量", "question": "模型参数量是多少，base 与 large 分别多大？"},
    {"key": "layers", "label": "层数与结构", "question": "模型有多少层 encoder/decoder，隐藏维度与注意力头数是多少？"},
    {"key": "context", "label": "上下文窗口", "question": "最大上下文窗口（context window）是多少 token？"},
    {"key": "normalization", "label": "归一化位置", "question": "LayerNorm 放在残差连接前还是后，post-LN 还是 pre-LN？"},
    {"key": "pretraining", "label": "预训练目标", "question": "预训练目标函数是什么（语言建模/掩码语言模型）？"},
    {"key": "data", "label": "训练数据", "question": "使用了什么训练数据集，规模多大？"},
    {"key": "hardware", "label": "训练硬件", "question": "训练使用了哪些 GPU/TPU 型号与数量，训练多久？"},
    {"key": "benchmark", "label": "主要评测结果", "question": "在主要 benchmark 上的分数或 BLEU 是多少？"},
    {"key": "code", "label": "开源代码", "question": "是否开源代码，GitHub 仓库地址是什么？"},
]

DEFAULT_KEYS = ["params", "layers", "context", "normalization", "hardware", "benchmark"]


@dataclass
class ComparisonResult:
    papers: list[dict[str, Any]] = field(default_factory=list)
    dimensions: list[dict[str, Any]] = field(default_factory=list)
    markdown: str = ""
    citations: list[Citation] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    degraded: bool = False
    coverage: float = 0.0
    verified: bool = False
    notes: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    checks: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "papers": self.papers,
            "dimensions": self.dimensions,
            "markdown": self.markdown,
            "citations": [c.to_dict() for c in self.citations],
            "provider": self.provider,
            "model": self.model,
            "degraded": self.degraded,
            "coverage": round(self.coverage, 3),
            "verified": self.verified,
            "notes": self.notes,
            "checks": self.checks,
            "latency_ms": round(self.latency_ms, 1),
        }


def select_dimensions(keys: Sequence[str] | None = None) -> list[dict[str, str]]:
    if not keys:
        keys = DEFAULT_KEYS
    by_key = {dim["key"]: dim for dim in DIMENSIONS}
    unknown = [key for key in keys if key not in by_key]
    if unknown:
        raise ValidationError(f"未知对比维度: {', '.join(unknown)}", detail={"available": list(by_key)})
    return [by_key[key] for key in keys]


def compare_papers(
    paper_ids: Sequence[str],
    *,
    keys: Sequence[str] | None = None,
    settings: Settings | None = None,
    per_dim_top: int = 2,
) -> ComparisonResult:
    settings = settings or get_settings()
    ids = list(dict.fromkeys(paper_ids))
    if len(ids) < 2:
        raise ValidationError("跨文献对比至少需要两篇论文")
    papers = []
    for pid in ids:
        paper = repo.get_paper(pid)
        if not paper:
            raise ValidationError(f"论文不存在: {pid}")
        papers.append(
            {
                "id": pid,
                "title": paper.get("title") or paper.get("filename") or pid,
                "year": paper.get("year"),
                "venue": paper.get("venue") or "",
            }
        )
    dimensions = select_dimensions(keys)
    started = time.perf_counter()
    retriever = get_retriever(settings)

    evidence_map: dict[str, dict[str, list[dict[str, Any]]]] = {}
    citations: list[Citation] = []
    citation_by_id: dict[str, Citation] = {}
    for dim in dimensions:
        per_paper: dict[str, list[dict[str, Any]]] = {}
        for paper in papers:
            result = retriever.search(dim["question"], paper_ids=[paper["id"]], top_k=per_dim_top, expand=False)
            items: list[dict[str, Any]] = []
            for index, chunk in enumerate(result.chunks, start=1):
                evidence_id = f"{dim['key']}::{paper['id']}::{index}"
                citation = _citation(evidence_id, chunk)
                citations.append(citation)
                citation_by_id[evidence_id] = citation
                items.append(
                    {
                        "id": evidence_id,
                        "text": chunk.text[:600],
                        "page": chunk.page,
                        "paper_id": paper["id"],
                        "paper_title": paper["title"],
                        "section": chunk.section,
                        "chunk_id": chunk.chunk_id,
                    }
                )
            per_paper[paper["id"]] = items
        evidence_map[dim["key"]] = per_paper

    payload = {
        "papers": papers,
        "dimensions": dimensions,
        "evidence": evidence_map,
        "requirement": "每个维度的每个单元格只写证据中的事实；无证据写「未提及」。",
    }
    result = _run("compare_papers", payload, settings)
    rows = _normalise_rows(result.data, dimensions, papers)
    if not rows:
        fallback = run_offline("compare_papers", payload, expect_json=True)
        rows = _normalise_rows(fallback.data, dimensions, papers)
        result = fallback
        result.degraded = True

    checks: list[dict[str, Any]] = []
    empty_cells = 0
    unsupported_cells = 0
    total_cells = 0
    for row in rows:
        for cell in row["rows"]:
            total_cells += 1
            if cell["value"] in {"未提及", "", "无"}:
                empty_cells += 1
                cell["citations"] = []
                continue
            ids_found = _match_evidence(cell.pop("evidence", []), row.get("key", ""), cell["paper_id"], evidence_map)
            cell["citations"] = [citation_by_id[item].to_dict() for item in ids_found if item in citation_by_id]
            if not cell["citations"]:
                unsupported_cells += 1
    coverage = 1 - (empty_cells / total_cells) if total_cells else 0.0
    checks.append(
        {
            "name": "coverage",
            "ok": coverage >= 0.3,
            "detail": f"{total_cells - empty_cells}/{total_cells} 个单元格找到直接证据",
        }
    )
    checks.append(
        {
            "name": "anchor",
            "ok": unsupported_cells == 0,
            "detail": f"{unsupported_cells} 个非空单元格缺少引用锚点",
        }
    )
    verified = note_ok(checks)
    notes: list[str] = []
    if result.degraded:
        notes.append("在线模型不可用，对比表由确定性抽取生成")
    if unsupported_cells:
        notes.append(f"{unsupported_cells} 个单元格没有找到可定位原文，请人工核对")
    if empty_cells == total_cells:
        notes.append("所选维度在文献中都没有找到证据，可以尝试更具体的维度或补充文献")

    comparison = ComparisonResult(
        papers=papers,
        dimensions=rows,
        markdown=_markdown(papers, rows),
        citations=citations,
        provider=result.provider,
        model=result.model,
        degraded=result.degraded,
        coverage=coverage,
        verified=verified,
        notes=notes,
        checks=checks,
    )
    comparison.latency_ms = (time.perf_counter() - started) * 1000
    try:
        repo.add_trace(
            "qa",
            "compare",
            {"paper_ids": ids, "keys": [d["key"] for d in dimensions], "coverage": comparison.coverage},
        )
    except Exception:  # noqa: BLE001
        logger.debug("trace 写入失败", exc_info=True)
    return comparison


def _run(task: str, payload: dict[str, Any], settings: Settings) -> Any:
    from .service import run_task_with

    return run_task_with(task, payload, settings, expect_json=True)


def _citation(evidence_id: str, chunk: RetrievedChunk) -> Citation:
    return Citation(
        evidence_id=evidence_id,
        chunk_id=chunk.chunk_id,
        paper_id=chunk.paper_id,
        paper_title=chunk.paper_title,
        page=chunk.page,
        bbox=list(chunk.bbox),
        section=chunk.section,
        section_path=chunk.section_path,
        kind=chunk.kind,
        quote=chunk.text[:240],
        score=float(chunk.scores.get("fused", 0.0)),
        cited=True,
    )


def _normalise_rows(
    data: dict[str, Any] | None,
    dimensions: list[dict[str, str]],
    papers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """把在线 JSON / 离线结果统一成 {key,label,rows:[{paper_id,value,evidence}],conclusion}。"""
    if not data:
        return []
    raw_rows = data.get("dimensions") or data.get("rows") or []
    if not isinstance(raw_rows, list):
        return []
    by_label = {dim["label"]: dim for dim in dimensions}
    out: list[dict[str, Any]] = []
    for position, raw in enumerate(raw_rows):
        if not isinstance(raw, dict):
            continue
        label = str(raw.get("dimension") or raw.get("label") or "")
        dim = by_label.get(label)
        key = raw.get("key") or (dim["key"] if dim else dimensions[position]["key"])
        label = dim["label"] if dim else (label or key)
        cells_raw = raw.get("rows") or raw.get("cells") or []
        cells: list[dict[str, Any]] = []
        for paper in papers:
            match = None
            for cell in cells_raw:
                if isinstance(cell, dict) and str(cell.get("paper_id")) == paper["id"]:
                    match = cell
                    break
            if match is None and cells_raw and all(isinstance(c, dict) for c in cells_raw):
                index = [p["id"] for p in papers].index(paper["id"])
                match = cells_raw[index] if index < len(cells_raw) else None
            if match is None:
                cells.append({"paper_id": paper["id"], "value": "未提及", "evidence": []})
                continue
            value = str(match.get("value") or "").strip()
            cells.append(
                {
                    "paper_id": paper["id"],
                    "value": value or "未提及",
                    "evidence": match.get("evidence") or [],
                }
            )
        out.append(
            {
                "key": key,
                "dimension": label,
                "rows": cells,
                "conclusion": str(raw.get("conclusion") or ""),
            }
        )
    # 补齐缺失维度（在线模型偶尔漏项），用「未提及」占位，保持数据结构稳定
    present = {row["key"] for row in out}
    for dim in dimensions:
        if dim["key"] not in present:
            out.append(
                {
                    "key": dim["key"],
                    "dimension": dim["label"],
                    "rows": [{"paper_id": p["id"], "value": "未提及", "evidence": []} for p in papers],
                    "conclusion": "",
                }
            )
    order = {dim["key"]: index for index, dim in enumerate(dimensions)}
    out.sort(key=lambda row: order.get(row["key"], 99))
    return out


def _match_evidence(
    evidence: Any,
    key: str,
    paper_id: str,
    evidence_map: dict[str, dict[str, list[dict[str, Any]]]],
) -> list[str]:
    available = [item["id"] for item in evidence_map.get(key, {}).get(paper_id, [])]
    if not available:
        return []
    if isinstance(evidence, list):
        matched = [str(item) for item in evidence if str(item) in available]
        if matched:
            return matched
        # 在线模型常用序号（1、2）指向该论文的证据顺序
        indexes = []
        for item in evidence:
            try:
                value = int(item)
            except (TypeError, ValueError):
                continue
            if 1 <= value <= len(available):
                indexes.append(available[value - 1])
        if indexes:
            return indexes
    return []


def _markdown(papers: list[dict[str, Any]], rows: list[dict[str, Any]]) -> str:
    def esc(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ").strip()

    header = ["维度", *[esc(paper["title"]) for paper in papers]]
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    for row in rows:
        by_paper = {cell["paper_id"]: cell["value"] for cell in row["rows"]}
        lines.append("| " + " | ".join([esc(row["dimension"]), *[esc(by_paper.get(p["id"], "未提及")) for p in papers]]) + " |")
    conclusions = [row for row in rows if row.get("conclusion")]
    if conclusions:
        lines.append("")
        for row in conclusions:
            lines.append(f"- **{row['dimension']}**：{row['conclusion']}")
    return "\n".join(lines)
