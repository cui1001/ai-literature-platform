"""把 docs/TECHNICAL_REPORT.md 渲染为可打印 HTML，并用 Edge 无头模式导出 PDF。

用法：
    python scripts/build_report_pdf.py
    python scripts/build_report_pdf.py --md docs/TECHNICAL_REPORT.md --pdf docs/TECHNICAL_REPORT.pdf

只依赖标准库 + PyMuPDF（仅用于校验页数）；不引入 markdown 库。
"""

from __future__ import annotations

import argparse
import html
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MD = ROOT / "docs" / "TECHNICAL_REPORT.md"
DEFAULT_PDF = ROOT / "docs" / "TECHNICAL_REPORT.pdf"
EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "msedge",
]

COVER = """
<section class="cover">
  <div class="kicker">科研助手 Agent 应用系统 · 竞赛提交材料</div>
  <h1 class="title">AgentLit<br/>科研助手 Agent 平台</h1>
  <div class="subtitle">技术文档</div>
  <div class="rule"></div>
  <table class="meta">
    <tr><th>版本</th><td>v2.0.0</td></tr>
    <tr><th>代码基线</th><td>master 分支（完整分阶段提交与合并历史）</td></tr>
    <tr><th>运行形态</th><td>FastAPI + SQLite + 原生 ES Module 前端（单机全栈）</td></tr>
    <tr><th>文档日期</th><td>2026-11-08</td></tr>
    <tr><th>文档范围</th><td>项目概述 / 需求分析 / 系统架构设计 / AI 技术方案 / 核心功能实现 / 测试与部署 / 总结与展望 / 创新点说明</td></tr>
  </table>
  <div class="cover-note">
    本文档所有指标均来自仓库内 <code>backend/eval/run_eval.py</code> 与
    <code>backend/tests</code> 的真实运行结果；截图与评测数据可在 <code>docs/</code> 中复核。
  </div>
</section>
"""

CSS = """
@page { size: A4; margin: 17mm 15mm 16mm 15mm; }
* { box-sizing: border-box; }
body {
  margin: 0; color: #1f2937; background: #fff;
  font-family: "Microsoft YaHei", "Segoe UI", "Noto Sans CJK SC", sans-serif;
  font-size: 10.2pt; line-height: 1.62;
  -webkit-print-color-adjust: exact; print-color-adjust: exact;
}
.cover { page-break-after: always; padding-top: 46mm; }
.cover .kicker { color: #2563eb; font-size: 11pt; letter-spacing: 2px; }
.cover .title { font-size: 30pt; line-height: 1.28; margin: 14px 0 8px; color: #0f172a; }
.cover .subtitle { font-size: 16pt; color: #334155; letter-spacing: 6px; }
.cover .rule { height: 3px; width: 168mm; background: #2563eb; margin: 20px 0 22px; }
.cover table.meta { font-size: 9.4pt; border: 0; width: 168mm; }
.cover table.meta th { width: 26mm; background: transparent; color: #64748b; text-align: left; font-weight: 600; border: 0; padding: 3px 0; }
.cover table.meta td { border: 0; padding: 3px 0; }
.cover .cover-note { margin-top: 40mm; color: #64748b; font-size: 8.8pt; border-top: 1px solid #e2e8f0; padding-top: 8px; }
h2 {
  font-size: 15pt; color: #0f172a; margin: 20px 0 8px;
  border-bottom: 2px solid #2563eb; padding-bottom: 4px;
  page-break-after: avoid;
}
h3 { font-size: 12pt; color: #1d4ed8; margin: 15px 0 5px; page-break-after: avoid; }
h4 { font-size: 10.8pt; color: #334155; margin: 12px 0 4px; page-break-after: avoid; }
p { margin: 5px 0 8px; text-align: justify; }
ul, ol { margin: 5px 0 9px 22px; padding: 0; }
li { margin: 2px 0; }
table { width: 100%; border-collapse: collapse; margin: 7px 0 12px; font-size: 8.5pt; page-break-inside: avoid; }
th, td { border: 0.6pt solid #cbd5e1; padding: 3.6px 5px; vertical-align: top; text-align: left; }
th { background: #eef4ff; color: #1e3a8a; font-weight: 600; }
tr:nth-child(even) td { background: #fafcff; }
code { font-family: Consolas, "Cascadia Mono", monospace; background: #f1f5f9; padding: 0 3px; font-size: 8.6pt; border-radius: 2px; }
pre { background: #0f172a; color: #e2e8f0; padding: 8px 10px; border-radius: 4px; font-size: 8.1pt; line-height: 1.45; overflow: hidden; page-break-inside: avoid; }
pre code { background: transparent; color: inherit; padding: 0; font-size: 8.1pt; }
blockquote { margin: 8px 0; padding: 6px 10px; border-left: 3px solid #93c5fd; background: #f8fbff; color: #475569; font-size: 9.4pt; }
figure { margin: 8px 0 10px; page-break-inside: avoid; }
figure img { width: 100%; border: 0.6pt solid #e2e8f0; }
figcaption { text-align: center; color: #64748b; font-size: 8.6pt; margin-top: 3px; }
a { color: #2563eb; text-decoration: none; }
hr { border: 0; border-top: 1px solid #e2e8f0; margin: 14px 0; }
"""


def inline(text: str) -> str:
    """行内元素：转义 → code → 粗体 → 链接。"""
    parts = re.split(r"(`[^`]*`)", text)
    out = []
    for part in parts:
        if part.startswith("`") and part.endswith("`") and len(part) > 2:
            out.append(f"<code>{html.escape(part[1:-1])}</code>")
        else:
            seg = html.escape(part)
            seg = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", seg)
            seg = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', seg)
            out.append(seg)
    return "".join(out)


def parse_table(lines: list[str]) -> str:
    rows = []
    for line in lines:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows.append(cells)
    header = rows[0]
    body = [r for r in rows[2:]] if len(rows) > 2 else []
    out = ["<table>", "<thead><tr>"]
    out += [f"<th>{inline(c)}</th>" for c in header]
    out.append("</tr></thead><tbody>")
    for row in body:
        out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in row) + "</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def convert(md_text: str, md_dir: Path) -> str:
    lines = md_text.splitlines()
    out: list[str] = []
    i = 0
    in_code = False
    code_buf: list[str] = []

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):
            if in_code:
                out.append("<pre><code>" + html.escape("\n".join(code_buf)) + "</code></pre>")
                code_buf, in_code = [], False
            else:
                in_code = True
            i += 1
            continue
        if in_code:
            code_buf.append(line)
            i += 1
            continue

        if not stripped:
            i += 1
            continue

        # 表格：当前行以 | 开头，且下一行是分隔行
        if stripped.startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            block = [line]
            i += 1
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i])
                i += 1
            out.append(parse_table(block))
            continue

        m = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if m:
            level = len(m.group(1))
            text = inline(m.group(2))
            if level == 1:
                # 文档标题由封面承担，正文中忽略
                i += 1
                continue
            out.append(f"<h{level}>{text}</h{level}>")
            i += 1
            continue

        if stripped.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip())
                i += 1
            out.append("<blockquote>" + inline(" ".join(buf)) + "</blockquote>")
            continue

        if re.match(r"^!\[.*\]\(.*\)$", stripped):
            m = re.match(r"^!\[(.*?)\]\((.*?)\)$", stripped)
            alt, src = m.group(1), m.group(2)
            path = (md_dir / src).resolve() if not Path(src).is_absolute() else Path(src)
            out.append(
                f'<figure><img src="{path.as_uri()}" alt="{html.escape(alt)}"/>'
                f"<figcaption>{inline(alt)}</figcaption></figure>"
            )
            i += 1
            continue

        if re.match(r"^[-*]\s+", stripped):
            items = []
            while i < len(lines) and re.match(r"^[-*]\s+", lines[i].strip()):
                items.append(lines[i].strip()[2:].strip())
                i += 1
            out.append("<ul>" + "".join(f"<li>{inline(x)}</li>" for x in items) + "</ul>")
            continue

        if re.match(r"^\d+\.\s+", stripped):
            items = []
            while i < len(lines) and re.match(r"^\d+\.\s+", lines[i].strip()):
                items.append(re.sub(r"^\d+\.\s+", "", lines[i].strip()))
                i += 1
            out.append("<ol>" + "".join(f"<li>{inline(x)}</li>" for x in items) + "</ol>")
            continue

        if stripped in {"---", "***"}:
            out.append("<hr/>")
            i += 1
            continue

        # 段落：收集到空行或下一个块级元素
        buf = [stripped]
        i += 1
        while i < len(lines):
            nxt = lines[i].strip()
            if not nxt or nxt.startswith(("#", "|", ">", "```", "![")) or re.match(r"^[-*]\s+", nxt) or re.match(r"^\d+\.\s+", nxt):
                break
            buf.append(nxt)
            i += 1
        out.append("<p>" + inline(" ".join(buf)) + "</p>")

    if in_code:
        out.append("<pre><code>" + html.escape("\n".join(code_buf)) + "</code></pre>")
    return "\n".join(out)


def find_edge() -> str:
    for cand in EDGE_CANDIDATES:
        path = shutil.which(cand) if not Path(cand).exists() else cand
        if path and Path(path).exists():
            return str(path)
    raise SystemExit("未找到 Edge，可改用浏览器手动打印：请打开 HTML 后 Ctrl+P 存为 PDF")


def build(md_path: Path, pdf_path: Path) -> None:
    md_text = md_path.read_text(encoding="utf-8")
    body = convert(md_text, md_path.parent)
    html_doc = (
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'/>"
        "<title>AgentLit 科研助手 Agent 平台 — 技术文档</title>"
        f"<style>{CSS}</style></head><body>{COVER}{body}</body></html>"
    )

    build_dir = ROOT / "docs" / ".report_build"
    build_dir.mkdir(parents=True, exist_ok=True)
    html_path = build_dir / "technical_report.html"
    html_path.write_text(html_doc, encoding="utf-8")

    edge = find_edge()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        edge,
        "--headless=new",
        "--disable-gpu",
        "--no-pdf-header-footer",
        "--virtual-time-budget=10000",
        f"--print-to-pdf={pdf_path}",
        html_path.as_uri(),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        import fitz  # type: ignore

        doc = fitz.open(pdf_path)
        pages = doc.page_count
        text = "\n".join(page.get_text() for page in doc)
        doc.close()
        print(f"[report] HTML: {html_path}")
        print(f"[report] PDF : {pdf_path}  pages={pages}  chars={len(text)}")
        required = ["项目概述", "需求分析", "系统架构设计", "AI 技术方案", "核心功能实现", "测试与部署", "创新点说明"]
        missing = [k for k in required if k not in text]
        if missing:
            print("[report] 缺少章节:", missing)
            sys.exit(2)
        if pages > 30:
            print(f"[report] 超过 30 页限制：{pages}")
            sys.exit(3)
    except ImportError:
        print("[report] 未安装 PyMuPDF，跳过页数校验")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--md", default=str(DEFAULT_MD))
    parser.add_argument("--pdf", default=str(DEFAULT_PDF))
    args = parser.parse_args()
    build(Path(args.md), Path(args.pdf))


if __name__ == "__main__":
    main()
