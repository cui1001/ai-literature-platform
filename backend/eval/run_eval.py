"""端到端评测：用合成语料逐项验证六大命题模块。

运行：``cd backend && python -m eval.run_eval``
产物：``docs/EVALUATION.md``（人可读报告）+ ``backend/eval/report.json``（机器可读明细）。

设计原则：
* 全部走真实模块调用（解析 → 入库 → 索引 → 检索 → 问答 → 对比 → 图谱 → 综述 → 写作 → 图表），
  不 mock、不联网、不依赖外部二进制；
* 每项指标都可追溯到具体论文 / 问题 / 页码，报告里保留失败样例，便于人工复核；
* 固定数据目录、固定参数，重复运行结果一致（确定性）。
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.stdout.reconfigure(encoding="utf-8")

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
FIXTURES = REPO / "fixtures"
CORPUS_JSON = FIXTURES / "corpus" / "papers.json"
PAPERS_DIR = FIXTURES / "papers"
REPORT_JSON = BACKEND / "eval" / "report.json"
REPORT_MD = REPO / "docs" / "EVALUATION.md"

from app.core.config import reset_settings  # noqa: E402
from app.db import repo  # noqa: E402
from app.db.database import close_all, get_connection, init_schema, reset_database  # noqa: E402
from app.parsing import ParsedPaper, detect_duplicate, load_parsed  # noqa: E402
from app.parsing.dedupe import compare_parsed  # noqa: E402
from app.parsing.pipeline import parse_pdf  # noqa: E402

STOP = {"the", "a", "an", "of", "and", "to", "in", "on", "for", "with", "by", "is", "are", "was", "were", "as", "at", "be",
        "this", "that", "it", "its", "from", "how", "many", "much", "what", "which", "who", "does", "do", "did", "used",
        "use", "paper", "model", "models", "using", "than", "then", "we", "our"}
NUM_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
             "nine": 9, "ten": 10, "twelve": 12, "sixteen": 16, "twenty": 20, "thirty": 30, "sixty": 60}


def norm_tokens(text: str) -> list[str]:
    text = (text or "").lower()
    text = text.replace("%", " percent ").replace("×", " x ").replace("−", "-").replace("–", "-")
    text = re.sub(r"(\d),(\d{3})", r"\1\2", text)
    tokens = re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", text)
    out: list[str] = []
    for token in tokens:
        if token in STOP:
            continue
        if token in NUM_WORDS:
            token = str(NUM_WORDS[token])
        out.append(token)
    return out


def gold_hit(answer: str, gold: str) -> float:
    """金标准 token 在答案中的召回率（0–1）。"""
    gold_tokens = norm_tokens(gold)
    if not gold_tokens:
        return 0.0
    answer_tokens = set(norm_tokens(answer))
    hit = sum(1 for token in set(gold_tokens) if token in answer_tokens)
    return round(hit / len(set(gold_tokens)), 3)


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def main() -> int:
    started = time.time()
    data_dir = Path(tempfile.gettempdir()) / "agentlit_eval"
    if data_dir.exists():
        import shutil

        shutil.rmtree(data_dir, ignore_errors=True)
    settings = reset_settings(
        data_dir=str(data_dir),
        llm_mode="offline",
        embed_mode="hashing",
        arxiv_mode="offline",
    )
    settings.ensure_dirs()
    reset_database(settings)
    init_schema(get_connection(settings))

    corpus = json.loads(CORPUS_JSON.read_text(encoding="utf-8"))
    papers_meta: list[dict[str, Any]] = corpus["papers"] if isinstance(corpus, dict) else corpus
    by_key = {item["key"]: item for item in papers_meta}

    report: dict[str, Any] = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "modules": {}}
    key_to_id: dict[str, str] = {}

    # ---------------------------------------------------------------- 模块一：导入 / 解析 / OCR / 去重
    from app.papers import service as papers_service

    print("[1/7] 导入语料并解析 …")
    ingest_results: list[dict[str, Any]] = []
    order = ["transformer_v1", "transformer_v7"] + [k for k in by_key if k not in {"transformer_v1", "transformer_v7", "udl_scan"}]
    # 扫描件先入库，确保 OCR 路径被真实执行（可检索版随后作为近似重复被识别）
    order = ["udl_scan"] + order
    for key in order:
        meta = by_key[key]
        path = PAPERS_DIR / ("scanned" if meta.get("scanned") else "") / f"{key}.pdf"
        if not path.exists():
            path = PAPERS_DIR / f"{key}.pdf"
        result = papers_service.ingest_path(
            path,
            settings=settings,
            source="fixture",
            index=True,
            force=key == "transformer_v7",
        )
        pid = str((result.get("paper") or {}).get("id") or result.get("paper_id") or "")
        if pid and result.get("status") != "duplicate":
            key_to_id[key] = pid
        ingest_results.append(
            {
                "key": key,
                "status": result.get("status"),
                "paper_id": pid,
                "duplicate_kind": (result.get("verdict") or {}).get("kind"),
                "index": result.get("index", {}).get("chunks"),
                "elapsed_ms": result.get("elapsed_ms"),
            }
        )

    # 若 v7 被 force 入库但返回了 duplicate，重新按标题定位
    if "transformer_v7" not in key_to_id:
        for row in repo.all_papers():
            if "v7" in (row.get("version_label") or "") or "V7" in (row.get("version_label") or ""):
                key_to_id["transformer_v7"] = row["id"]
    if "transformer_v1" not in key_to_id:
        for row in repo.all_papers():
            if row.get("arxiv_id", "").startswith("1706.03762") and row["id"] != key_to_id.get("transformer_v7"):
                if (row.get("version_label") or "").endswith("1"):
                    key_to_id["transformer_v1"] = row["id"]

    parse_rows: list[dict[str, Any]] = []
    for key, pid in key_to_id.items():
        raw = load_parsed(pid, settings)
        if not raw:
            continue
        paper = ParsedPaper.from_dict(raw)
        meta = by_key.get(key, {})
        facts = meta.get("facts") or {}
        full = paper.full_text(limit=200000)
        fact_hits = {}
        for name in ("gpu", "params", "training_time"):
            value = facts.get(name)
            if not value:
                continue
            fact_hits[name] = gold_hit(full, value) >= 0.6
        parse_rows.append(
            {
                "key": key,
                "paper_id": pid,
                "title": paper.title,
                "year": paper.year,
                "layout": paper.layout,
                "ocr": bool(paper.ocr_used),
                "ocr_engine": paper.ocr_engine,
                "pages": len(paper.pages),
                "sections": len(paper.sections),
                "tables": len(paper.tables),
                "figures": len(paper.figures),
                "equations": len(paper.equations),
                "references": len(paper.references),
                "chars": len(full),
                "quality": round(float(paper.quality.score), 3),
                "fact_hits": fact_hits,
            }
        )

    ocr_row = next((row for row in parse_rows if row["key"] == "udl_scan"), {})
    reference_ok = sum(1 for row in parse_rows if row["references"] >= 5)
    table_ok = sum(1 for row in parse_rows if row["tables"] >= 1)
    quality_avg = round(sum(row["quality"] for row in parse_rows) / max(1, len(parse_rows)), 3)
    report["modules"]["ingest_parse"] = {
        "ingested": len(key_to_id),
        "expected": len(by_key),
        "parse_rows": parse_rows,
        "ingest_results": ingest_results,
        "metrics": {
            "parse_success_rate": round(len(parse_rows) / len(by_key), 3),
            "avg_quality": quality_avg,
            "papers_with_tables": table_ok,
            "papers_with_references": reference_ok,
            "ocr_engine": ocr_row.get("ocr_engine"),
            "ocr_chars": ocr_row.get("chars"),
            "ocr_quality": ocr_row.get("quality"),
            "fact_coverage": round(
                sum(1 for row in parse_rows for ok in row["fact_hits"].values() if ok)
                / max(1, sum(len(row["fact_hits"]) for row in parse_rows)),
                3,
            ),
        },
    }

    # 语义去重：v7 对比库内 v1
    dedupe: dict[str, Any] = {}
    v1_id, v7_id = key_to_id.get("transformer_v1"), key_to_id.get("transformer_v7")
    if v1_id and v7_id:
        v1 = ParsedPaper.from_dict(load_parsed(v1_id, settings))
        v7 = ParsedPaper.from_dict(load_parsed(v7_id, settings))
        comparison = compare_parsed(v1, v7)
        v1_record = repo.get_paper(v1_id)
        verdict = detect_duplicate(
            v7,
            [v1_record],
            text_sample=v7.full_text(limit=4000),
        )
        dedupe = {
            "kind": verdict.kind,
            "action": verdict.action,
            "similarity": round(float(verdict.similarity or 0), 4),
            "reason": verdict.reason,
            "text_similarity": comparison.get("text_similarity"),
            "sections_added": comparison.get("sections_added"),
            "verdict": comparison.get("verdict"),
            "chars_v1": comparison.get("chars_a"),
            "chars_v7": comparison.get("chars_b"),
        }
    report["modules"]["dedupe"] = dedupe

    # ---------------------------------------------------------------- 模块二：抗幻觉问答 + 跨文献对比
    print("[2/7] 抗幻觉问答评测 …")
    from app.qa import answer_question, compare_papers

    qa_rows: list[dict[str, Any]] = []
    for key, meta in by_key.items():
        pid = key_to_id.get(key)
        if not pid:
            continue
        for item in meta.get("qa") or []:
            result = answer_question(item["question"], [pid], settings=settings, save=False)
            answer = result.answer or ""
            citations = [c for c in result.citations if c.cited] or result.citations
            anchored = [
                c for c in citations
                if c.page >= 1 and c.bbox and len(c.bbox) == 4
            ]
            hit = gold_hit(answer, item.get("gold", ""))
            qa_rows.append(
                {
                    "key": key,
                    "question": item["question"],
                    "gold": item.get("gold"),
                    "kind": item.get("kind"),
                    "hit": hit,
                    "matched": hit >= 0.6,
                    "refused": bool(result.refused),
                    "verified": bool(result.verification.verified),
                    "confidence": round(float(result.verification.confidence), 3),
                    "citations": len(citations),
                    "anchored": len(anchored),
                    "pages": sorted({c.page for c in citations}),
                }
            )

    total = len(qa_rows)
    matched = sum(1 for row in qa_rows if row["matched"])
    refused = sum(1 for row in qa_rows if row["refused"])
    verified = sum(1 for row in qa_rows if row["verified"])
    anchored = sum(1 for row in qa_rows if row["anchored"] >= 1)
    by_kind: dict[str, dict[str, int]] = {}
    for row in qa_rows:
        bucket = by_kind.setdefault(row["kind"] or "other", {"total": 0, "matched": 0})
        bucket["total"] += 1
        bucket["matched"] += 1 if row["matched"] else 0

    compare: dict[str, Any] = {}
    gpt_ids = [key_to_id[k] for k in ("gpt1", "gpt2", "gpt3") if k in key_to_id]
    if len(gpt_ids) == 3:
        result = compare_papers(gpt_ids, keys=["params", "layers", "context", "hardware"], settings=settings)
        markdown = result.markdown or ""
        lowered = markdown.lower().replace(",", "")
        expectations = {
            "117M": "117 million" in lowered,
            "1.5B": "1.5 billion" in lowered,
            "175B": "175 billion" in lowered,
            "1024": "1024" in markdown,
            "2048": "2048" in markdown,
        }
        anchored_cells = sum(
            1 for dim in result.dimensions for cell in dim.get("rows", []) if cell.get("citations")
        )
        compare = {
            "papers": [repo.get_paper(i)["title"] for i in gpt_ids],
            "coverage": result.coverage,
            "verified": result.verified,
            "cells_anchored": anchored_cells,
            "cells_total": len(result.dimensions) * len(gpt_ids),
            "expectations": expectations,
            "markdown": markdown,
        }

    report["modules"]["qa"] = {
        "total": total,
        "matched": matched,
        "accuracy": round(matched / total, 3) if total else 0.0,
        "answer_rate": round((total - refused) / total, 3) if total else 0.0,
        "verified_rate": round(verified / total, 3) if total else 0.0,
        "anchor_rate": round(anchored / total, 3) if total else 0.0,
        "by_kind": by_kind,
        "rows": qa_rows,
    }
    report["modules"]["compare"] = compare

    # ---------------------------------------------------------------- 模块三：版式阅读 / 引用跳转
    print("[3/7] 阅读器结构评测 …")
    from app.reading import document, link_map

    reader: dict[str, Any] = {}
    sample_id = key_to_id.get("transformer_v7")
    if sample_id:
        doc = document(sample_id, include_translations=False)
        links = link_map(ParsedPaper.from_dict(load_parsed(sample_id, settings)))
        page_images = [page.get("image") for page in doc.get("pages", [])]
        reader = {
            "paper_id": sample_id,
            "pages": len(doc.get("pages", [])),
            "blocks": sum(len(page.get("blocks", [])) for page in doc.get("pages", [])),
            "sections": len(doc.get("sections", [])),
            "reference_links": links["stats"]["linked"],
            "unresolved": len(links["unresolved_citations"]),
            "uncited": len(links["uncited_references"]),
            "page_images": len([p for p in page_images if p]),
            "sample_anchors": [block.get("citations") for page in doc.get("pages", []) for block in page.get("blocks", []) if block.get("citations")][:5],
        }
    report["modules"]["reader"] = reader

    # ---------------------------------------------------------------- 模块四：检索 / 追踪 / 推荐
    print("[4/7] arXiv 检索与追踪评测 …")
    from app import discovery

    found = discovery.search("chain of thought", max_results=5, settings=settings)
    recommend = discovery.recommend(limit=5, settings=settings)
    discovery_metrics = {
        "search_items": len(found.get("items", [])),
        "top_hit": (found.get("items") or [{}])[0].get("title"),
        "top_has_reason": bool((found.get("items") or [{}])[0].get("reason")),
        "recommend_items": len(recommend.get("items", [])),
        "degraded": bool(found.get("degraded")),
        "sources": sorted({item.get("source") for item in found.get("items", []) if item.get("source")}),
    }
    report["modules"]["discovery"] = discovery_metrics

    # ---------------------------------------------------------------- 模块五：图谱 / 综述
    print("[5/7] 引用图谱与综述评测 …")
    from app.graph import build_graph, survey

    all_ids = [row["id"] for row in repo.all_papers()]
    graph = build_graph(all_ids)
    summary = survey(all_ids, title="大模型与注意力机制的技术演进", graph_id=graph.get("graph_id"))
    directions = summary.get("future_directions") or []
    node_titles = {node.get("id"): node.get("title") or node.get("id") for node in graph.get("nodes", [])}
    report["modules"]["graph"] = {
        "nodes": graph["metrics"].get("nodes"),
        "edges": graph["metrics"].get("edges"),
        "density": graph["metrics"].get("density"),
        "components": graph["metrics"].get("components"),
        "foundation": [node_titles.get(pid, pid) for pid in graph.get("foundation", [])],
        "bridge": [node_titles.get(pid, pid) for pid in graph.get("bridge", [])],
        "peripheral_count": len(graph.get("peripheral", [])),
        "insights": graph.get("insights"),
        "edges_list": [
            {"source": edge.get("source_title") or edge.get("source"), "target": edge.get("target_title") or edge.get("target"),
             "kind": edge.get("kind"), "evidence": (edge.get("evidence") or "")[:120]}
            for edge in graph.get("links", [])
        ],
        "survey": {
            "sections": len([key for key in (summary.get("sections") or {}) if not key.startswith("_")]),
            "chars": len(summary.get("markdown") or ""),
            "future_directions": len(directions),
            "open_directions": len([d for d in directions if d.get("status") != "solved"]),
            "solved_directions": len([d for d in directions if d.get("status") == "solved"]),
            "citations": summary.get("citation_count") or len(summary.get("citations") or []),
            "provider": summary.get("provider"),
            "degraded": bool(summary.get("degraded")),
        },
    }

    # ---------------------------------------------------------------- 模块六：写作 / 图表
    print("[6/7] 写作 Copilot 与数据可视化评测 …")
    from app import dataviz
    from app.writing import draft_outline

    ids_for_outline = [key_to_id[k] for k in ("transformer_v1", "bert", "gpt3", "resnet", "rag") if k in key_to_id]
    outline = draft_outline(
        "把 LoRA 注入视觉 Transformer 的注意力层，在少样本图像分类上降低显存并保持精度",
        paper_ids=ids_for_outline,
        title="Low-Rank Adaptation for Vision Transformers",
        settings=settings,
        language="en",
    )
    reference_ids = {ref.get("paper_id") for ref in outline.get("references", [])}
    checks = outline.get("checks", {})
    checks = checks if isinstance(checks, dict) else {str(item.get("name")): item.get("passed") for item in checks}

    csv_bytes = (
        "epoch,train_loss,valid_loss,accuracy\n"
        "1,2.31,2.44,0.512\n2,1.87,2.01,0.611\n3,1.55,1.79,0.678\n"
        "4,1.32,1.71,0.715\n5,1.18,1.74,0.742\n6,1.07,1.79,0.756\n"
    ).encode("utf-8")
    dataset = dataviz.parse_csv_bytes(csv_bytes, "training.csv")
    chart_a = dataviz.build_chart(dataset["id"], {"kind": "line", "x": "epoch", "y": "accuracy", "title": "Accuracy over epochs"})
    chart_b = dataviz.build_chart(dataset["id"], {"kind": "line", "x": "epoch", "y": "accuracy", "title": "Accuracy over epochs"})
    radar = dataviz.build_chart(dataset["id"], {"kind": "radar3d", "title": "Training dynamics"})
    report["modules"]["writing"] = {
        "sections": len(outline.get("sections", [])),
        "references": len(outline.get("references", [])),
        "references_all_local": reference_ids.issubset(set(all_ids)),
        "checks": checks,
        "checks_passed": all(bool(value) for value in checks.values()) if checks else False,
        "provider": outline.get("provider"),
        "degraded": bool(outline.get("degraded")),
    }
    report["modules"]["dataviz"] = {
        "dataset_columns": [col["name"] for col in dataset["columns"]],
        "dataset_rows": dataset["row_count"],
        "chart_kind": chart_a.get("kind"),
        "svg_bytes": len(chart_a.get("svg") or ""),
        "deterministic_svg": (chart_a.get("svg") == chart_b.get("svg")),
        "caption": chart_a.get("caption"),
        "caption_has_numbers": bool(re.search(r"\d", chart_a.get("caption") or "")),
        "matplotlib_script_bytes": len(chart_a.get("script") or ""),
        "radar_svg_bytes": len(radar.get("svg") or ""),
        "chart_id": chart_a.get("id"),
    }

    # ---------------------------------------------------------------- 汇总
    metrics = {
        "parse_success_rate": report["modules"]["ingest_parse"]["metrics"]["parse_success_rate"],
        "avg_quality": report["modules"]["ingest_parse"]["metrics"]["avg_quality"],
        "qa_accuracy": report["modules"]["qa"]["accuracy"],
        "qa_anchor_rate": report["modules"]["qa"]["anchor_rate"],
        "qa_verified_rate": report["modules"]["qa"]["verified_rate"],
        "graph_edges": report["modules"]["graph"]["edges"],
        "foundation_count": len(report["modules"]["graph"]["foundation"]),
        "outline_checks_passed": report["modules"]["writing"]["checks_passed"],
        "chart_deterministic": report["modules"]["dataviz"]["deterministic_svg"],
        "elapsed_s": None,
    }
    report["metrics"] = metrics
    report["elapsed_s"] = round(time.time() - started, 1)
    metrics["elapsed_s"] = report["elapsed_s"]

    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(report)
    close_all()

    print(f"[7/7] 报告已生成：{REPORT_MD}（{report['elapsed_s']}s）")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0


def write_markdown(report: dict[str, Any]) -> None:
    modules = report["modules"]
    ingest = modules["ingest_parse"]
    qa = modules["qa"]
    graph = modules["graph"]
    writing = modules["writing"]
    dataviz = modules["dataviz"]
    reader = modules.get("reader") or {}
    compare = modules.get("compare") or {}
    discovery = modules.get("discovery") or {}
    dedupe = modules.get("dedupe") or {}

    lines: list[str] = []
    lines.append("# 评测报告（自动生成）")
    lines.append("")
    lines.append(f"> 生成时间：{report['generated_at']} ｜ 运行耗时：{report['elapsed_s']}s ｜ 运行方式：`cd backend && python -m eval.run_eval`")
    lines.append("> 语料：`fixtures/corpus/papers.json` 14 篇合成论文（含扫描件），全部在离线模式运行，无网络、无外部模型。")
    lines.append("")
    lines.append("## 总体指标")
    lines.append("")
    lines.append("| 指标 | 结果 |")
    lines.append("| --- | --- |")
    metrics = report["metrics"]
    lines.append(f"| 解析成功率 | {pct(metrics['parse_success_rate'])} |")
    lines.append(f"| 平均解析质量 | {metrics['avg_quality']:.3f} |")
    lines.append(f"| 问答金标准命中率 | {pct(metrics['qa_accuracy'])}（{qa['matched']}/{qa['total']}） |")
    lines.append(f"| 答案附原文锚点比例 | {pct(metrics['qa_anchor_rate'])} |")
    lines.append(f"| 答案通过校验比例 | {pct(metrics['qa_verified_rate'])} |")
    lines.append(f"| 引用图谱边数 | {metrics['graph_edges']} |")
    lines.append(f"| 基石论文数 | {metrics['foundation_count']} |")
    lines.append(f"| 写作引用校验通过 | {'是' if metrics['outline_checks_passed'] else '否'} |")
    lines.append(f"| 图表 SVG 确定性 | {'是' if metrics['chart_deterministic'] else '否'} |")
    lines.append("")
    lines.append("## 模块一：PDF 管理与细粒度解析")
    lines.append("")
    lines.append(f"- 入库 {ingest['ingested']} / {ingest['expected']} 篇；平均质量 {ingest['metrics']['avg_quality']:.3f}")
    lines.append(f"- 表格：{ingest['metrics']['papers_with_tables']} 篇至少 1 张表；参考文献：{ingest['metrics']['papers_with_references']} 篇 ≥5 条")
    lines.append(f"- 扫描件 OCR：引擎 `{ingest['metrics']['ocr_engine']}`，识别 {ingest['metrics']['ocr_chars']} 字符，质量 {ingest['metrics']['ocr_quality']}")
    lines.append(f"- 关键事实覆盖率（GPU/参数量/训练时长）：{pct(ingest['metrics']['fact_coverage'])}")
    lines.append("")
    lines.append("| 论文 | 页 | 章节 | 表 | 图 | 公式 | 参考文献 | 质量 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for row in ingest["parse_rows"]:
        lines.append(
            f"| {row['key']} | {row['pages']} | {row['sections']} | {row['tables']} | {row['figures']} | "
            f"{row['equations']} | {row['references']} | {row['quality']:.3f} |"
        )
    lines.append("")
    if dedupe:
        lines.append("**语义去重（1706.03762 v1 → v7）**")
        lines.append("")
        lines.append(f"- 判定：`{dedupe.get('kind')}` / `{dedupe.get('action')}`，相似度 {dedupe.get('similarity')}")
        lines.append(f"- 文本相似度 {dedupe.get('text_similarity')}；新增章节 {dedupe.get('sections_added')}")
        lines.append(f"- 理由：{dedupe.get('reason')}")
        lines.append("")
    lines.append("## 模块二：抗幻觉问答与多文对比")
    lines.append("")
    lines.append(f"- 金标准命中 {qa['matched']}/{qa['total']}（{pct(qa['accuracy'])}），拒答 {qa['total'] - int(qa['answer_rate'] * qa['total'])} 题")
    lines.append(f"- 每条答案平均引用锚点 {pct(qa['anchor_rate'])} 覆盖；校验通过率 {pct(qa['verified_rate'])}")
    lines.append("")
    lines.append("| 问题类型 | 命中 / 总数 |")
    lines.append("| --- | --- |")
    for kind, bucket in sorted(qa["by_kind"].items()):
        lines.append(f"| {kind} | {bucket['matched']} / {bucket['total']} |")
    lines.append("")
    failures = [row for row in qa["rows"] if not row["matched"]][:8]
    if failures:
        lines.append("<details><summary>未命中样例（保留以便复核）</summary>")
        lines.append("")
        for row in failures:
            lines.append(f"- `{row['key']}` {row['question']} → gold: {row['gold']}（hit={row['hit']}）")
        lines.append("")
        lines.append("</details>")
        lines.append("")
    if compare:
        lines.append("**跨文献对比（GPT-1/2/3）**")
        lines.append("")
        lines.append(f"- 维度覆盖 {compare.get('coverage')}；关键数值命中：{compare.get('expectations')}")
        lines.append("")
        lines.append(compare.get("markdown", "")[:2400])
        lines.append("")
    lines.append("## 模块三：版式阅读与双语对照")
    lines.append("")
    lines.append(f"- 样例论文渲染 {reader.get('pages')} 页 / {reader.get('blocks')} 个文本块 / {reader.get('sections')} 个章节")
    lines.append(f"- `[n]` → References 双向链接 {reader.get('reference_links')} 条，未解析 {reader.get('unresolved')}，未被引用条目 {reader.get('uncited')}")
    lines.append(f"- 页面图存在 {reader.get('page_images')} / {reader.get('pages')}")
    lines.append("")
    lines.append("## 模块四：论文检索、追踪与推荐")
    lines.append("")
    lines.append(f"- 离线镜像检索返回 {discovery.get('search_items')} 条，Top1：{discovery.get('top_hit')}")
    lines.append(f"- 推荐 {discovery.get('recommend_items')} 条；检索来源：{discovery.get('sources')}；降级：{discovery.get('degraded')}")
    lines.append("")
    lines.append("## 模块五：引用图谱与综述")
    lines.append("")
    lines.append(f"- 图谱 {graph['nodes']} 节点 / {graph['edges']} 边，密度 {graph['density']}，连通分量 {graph['components']}")
    lines.append(f"- 基石论文：{graph['foundation']}")
    lines.append(f"- 综述 {graph['survey']['sections']} 节 / {graph['survey']['chars']} 字符；Future Work 方向 {graph['survey']['future_directions']}（待探索 {graph['survey']['open_directions']} / 已解决 {graph['survey']['solved_directions']}）")
    lines.append("")
    lines.append("## 模块六：写作 Copilot 与数据可视化")
    lines.append("")
    lines.append(f"- 框架 {writing['sections']} 节，References {writing['references']} 条，全部来自本地库：{writing['references_all_local']}")
    lines.append(f"- 引用校验：{writing['checks']}")
    lines.append(f"- 图表：{dataviz['chart_kind']}，SVG {dataviz['svg_bytes']} 字节，确定性 {dataviz['deterministic_svg']}，Caption 含数字 {dataviz['caption_has_numbers']}")
    lines.append(f"- Figure Caption：{dataviz['caption']}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("本报告由 `backend/eval/run_eval.py` 生成，所有数字均可在 `backend/eval/report.json` 中追溯到具体论文与问题。")
    REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
