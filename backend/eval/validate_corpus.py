"""语料自检：对 fixtures/papers 中每篇 PDF 跑完整解析并输出结构化指标。

用途
----
1. 竞赛评审时可一条命令复现「解析 → 去重 → OCR」的证据；
2. 开发期快速发现版式回归（章节数、表格/公式/参考文献数量骤降）。

运行::

    cd backend
    python -m eval.validate_corpus            # 全部
    python -m eval.validate_corpus --only transformer_v1 transformer_v7
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from app.core.config import REPO_ROOT, get_settings, reset_settings
from app.core.hashing import simhash64
from app.parsing import compare_parsed, detect_duplicate, parse_pdf

FIXTURES = REPO_ROOT / "fixtures" / "papers"


def _settings(tmpdir: str):
    reset_settings(
        data_dir=Path(tmpdir),
        llm_mode="offline",
        embed_mode="hashing",
        arxiv_mode="offline",
    )
    return get_settings()


def parse_one(path: Path, settings) -> dict:
    parsed = parse_pdf(path.read_bytes(), settings=settings, filename=path.name, render_pages=False)
    return {
        "file": path.name,
        "paper_id": parsed.paper_id,
        "title": parsed.title,
        "authors": parsed.authors[:3],
        "layout": parsed.layout,
        "pages": len(parsed.pages),
        "blocks": len(parsed.blocks),
        "sections": len(parsed.sections),
        "headings": sum(1 for s in parsed.sections if s.level <= 3),
        "tables": len(parsed.tables),
        "figures": len(parsed.figures),
        "equations": len(parsed.equations),
        "references": len(parsed.references),
        "appendix": any(s.appendix for s in parsed.sections),
        "ocr_used": parsed.ocr_used,
        "ocr_engine": parsed.ocr_engine,
        "quality": round(parsed.quality.score, 3),
        "chars": parsed.quality.text_chars,
        "parsed": parsed,
    }


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="校验合成语料的解析质量")
    parser.add_argument("--only", nargs="*", default=None, help="只处理指定文件名（不含 .pdf）")
    parser.add_argument("--json", action="store_true", help="输出 JSON 而非表格")
    parser.add_argument("--out", default=None, help="把结果写入 JSON 文件")
    args = parser.parse_args(argv)

    files = sorted(FIXTURES.glob("*.pdf"))
    if args.only:
        wanted = {name.lower() for name in args.only}
        files = [path for path in files if path.stem.lower() in wanted or path.name.lower() in wanted]
    if not files:
        print("未找到 PDF，请先运行 python ../scripts/make_corpus.py")
        return 2

    with tempfile.TemporaryDirectory(prefix="agentlit_eval_") as tmpdir:
        settings = _settings(tmpdir)
        rows: list[dict] = []
        for path in files:
            try:
                row = parse_one(path, settings)
            except Exception as exc:  # noqa: BLE001 - 自检脚本需要继续处理其余文件
                rows.append({"file": path.name, "error": f"{type(exc).__name__}: {exc}"})
                continue
            rows.append(row)
            print(
                f"{row['file']:<22} q={row['quality']:<5} {row['layout']:<6} p={row['pages']:<3} "
                f"sec={row['sections']:<3} tbl={row['tables']:<2} fig={row['figures']:<2} "
                f"eq={row['equations']:<2} ref={row['references']:<3} chars={row['chars']:<6} "
                f"ocr={int(bool(row['ocr_used']))} | {row['title'][:60]}"
            )

        public = [{k: v for k, v in row.items() if k != "parsed"} for row in rows]
        by_name = {row["file"]: row for row in rows if "parsed" in row}

        # 版本去重：v1 vs v7
        dedupe_report = {}
        if "transformer_v1.pdf" in by_name and "transformer_v7.pdf" in by_name:
            old = by_name["transformer_v1.pdf"]["parsed"]
            new = by_name["transformer_v7.pdf"]["parsed"]
            record = {
                "paper_id": old.paper_id,
                "sha256": old.sha256,
                "arxiv_id": old.arxiv_id,
                "arxiv_version": old.arxiv_version,
                "title": old.title,
                "doc_simhash": simhash64(old.full_text()),
            }
            verdict = detect_duplicate(new, [record], text_sample=new.abstract)
            diff = compare_parsed(old, new)
            dedupe_report = {"verdict": verdict.to_dict(), "diff": diff}

        # 扫描件：必须走 OCR
        scan_report = {}
        scanned = FIXTURES / "scanned" / "udl_scan.pdf"
        if scanned.exists():
            parsed = parse_pdf(scanned.read_bytes(), settings=settings, filename=scanned.name)
            scan_report = {
                "ocr_used": parsed.ocr_used,
                "ocr_engine": parsed.ocr_engine,
                "chars": parsed.quality.text_chars,
                "blocks": len(parsed.blocks),
                "quality": round(parsed.quality.score, 3),
            }
            print(f"\n[scan] {scanned.name} -> {json.dumps(scan_report, ensure_ascii=False)}")

        payload = {"papers": public, "dedupe": dedupe_report, "scan": scan_report}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        if args.out:
            Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"\n写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
