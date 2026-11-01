"""成果导出：Markdown / HTML / 中英对照 HTML / 原位排版 PDF。

四种格式对应不同的使用场景：

* ``md``  —— 可直接进 Obsidian / GitHub 的纯文本，公式与引用保持原样；
* ``html``—— 自包含页面，引用标记是超链接，点击在 References 与正文间往返；
* ``bilingual`` —— 左右对照，逐段配对，滚动天然同步（同一滚动容器）；
* ``pdf`` —— 把译文写回每页原有的包围盒位置，图表按原页裁剪贴回，尽量保持版式。

所有导出都是确定性函数：同一份解析结果 + 同一份译文，字节级内容一致（PDF 元数据除外）。
"""

from __future__ import annotations

import html as html_lib
import io
import re
from pathlib import Path
from typing import Any, Iterable

from ..core.config import Settings, get_settings
from ..core.logging import get_logger
from ..core.textutil import CITATION_MARK_RE
from ..parsing.model import ParsedPaper, TextBlock
from ..parsing.pipeline import page_image_path

logger = get_logger(__name__)

_CITE_MARK_RE = CITATION_MARK_RE

_CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { margin: 0; padding: 0 0 80px; font-family: -apple-system, "Segoe UI", "Songti SC", "Noto Serif CJK SC", serif;
       line-height: 1.75; color: #1b1b1f; background: #fbfaf7; }
header.paper { padding: 48px 8vw 20px; border-bottom: 1px solid #e6e2d8; }
header.paper h1 { font-size: 30px; margin: 0 0 10px; line-height: 1.35; }
.meta { color: #66605a; font-size: 14px; margin: 4px 0; }
.tag { display: inline-block; padding: 1px 8px; margin-right: 6px; border: 1px solid #d8d2c4;
       border-radius: 999px; font-size: 12px; color: #7a5c2e; }
main { padding: 20px 8vw; max-width: 1100px; }
h2 { margin-top: 42px; padding-bottom: 6px; border-bottom: 1px solid #eee8dc; }
h3 { margin-top: 28px; } h4 { margin-top: 22px; }
p { margin: 0 0 12px; text-align: justify; }
a.cite { color: #8a5a00; text-decoration: none; border-bottom: 1px dotted #b98a2a; font-weight: 600; }
a.cite:hover { background: #f6e7c8; }
.abstract { background: #f5f2ea; border-left: 3px solid #c9a961; padding: 14px 18px; margin: 18px 0; }
.figure { margin: 22px 0; text-align: center; }
.figure img { max-width: 100%; border: 1px solid #e3ded1; background: #fff; }
.caption { color: #5d574e; font-size: 13.5px; }
table.data { border-collapse: collapse; width: 100%; margin: 18px 0; font-size: 13.5px; }
table.data th, table.data td { border: 1px solid #ddd6c8; padding: 6px 9px; }
table.data th { background: #f1ece1; }
pre.eq { background: #f4f1ea; padding: 10px 14px; overflow-x: auto; font-size: 13px; }
ol.refs { padding-left: 22px; font-size: 14px; }
ol.refs li { margin-bottom: 8px; }
ol.refs li:target, h2:target, h3:target { background: #fdf3d8; }
.cite-list { color: #8a5a00; font-size: 12px; margin-top: 4px; }
.bilingual { max-width: 1400px; margin: 0 auto; }
.pair { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; padding: 12px 0; border-bottom: 1px dashed #e8e2d6; }
.pair .src { color: #4b463f; }
.pair .dst { color: #1b1b1f; background: #fffdf6; padding-left: 16px; border-left: 2px solid #d9c58c; }
.pair .kind { font-size: 11px; color: #908878; text-transform: uppercase; letter-spacing: .08em; }
footer { padding: 30px 8vw; color: #8b857a; font-size: 12px; }
@media (prefers-color-scheme: dark) {
  body { background: #16151a; color: #e8e4dc; }
  header.paper, h2, table.data th, table.data td, .pair { border-color: #3a3730; }
  .abstract, pre.eq, table.data th { background: #24222a; }
  .pair .dst { background: #1e1c22; }
  a.cite { color: #e5b567; }
}
"""


def _escape(text: str) -> str:
    return html_lib.escape(text or "", quote=False)


def linkify(text: str, ref_numbers: set[int], *, html: bool = True) -> str:
    """把 ``[3]`` / ``[3,5-7]`` 变成可点击的锚点（区间展开为多个链接）。"""
    if not html:
        return text
    from ..core.textutil import extract_citations

    output: list[str] = []
    cursor = 0
    for match in _CITE_MARK_RE.finditer(text or ""):
        numbers = extract_citations(match.group(0))
        if not numbers:
            continue
        output.append(_escape(text[cursor : match.start()]))
        links: list[str] = []
        for number in numbers:
            if number in ref_numbers:
                links.append(f'<a class="cite" href="#ref-{number}" title="参考文献 [{number}]">[{number}]</a>')
            else:
                links.append(_escape(f"[{number}]"))
        output.append("".join(links))
        cursor = match.end()
    output.append(_escape(text[cursor:]))
    return "".join(output)


def _plain_marker(text: str) -> str:
    return text or ""


def _blocks_by_sid(paper: ParsedPaper) -> dict[str, list[TextBlock]]:
    grouped: dict[str, list[TextBlock]] = {}
    for block in paper.blocks:
        if block.kind in {"image"}:
            continue
        grouped.setdefault(block.sid or "", []).append(block)
    for blocks in grouped.values():
        blocks.sort(key=lambda block: (block.page, block.order, block.bbox.y0))
    return grouped


def _translation(translations: dict[str, str] | None, block: TextBlock) -> str:
    return (translations or {}).get(block.id, "")


def _title_line(paper: ParsedPaper) -> str:
    authors = ", ".join(paper.authors[:8]) if paper.authors else "（未识别作者）"
    venue = " · ".join(part for part in [paper.venue, str(paper.year or "")] if part)
    return f"{authors}  \n{venue}" if venue else authors


# ---------------------------------------------------------------- Markdown


def to_markdown(
    paper: ParsedPaper,
    translations: dict[str, str] | None = None,
    *,
    lang: str = "zh",
    include_references: bool = True,
) -> str:
    lines: list[str] = [f"# {paper.title or paper.filename}", ""]
    if paper.authors:
        lines.append(f"**Authors**: {', '.join(paper.authors)}")
    if paper.affiliations:
        lines.append(f"**Affiliations**: {'; '.join(paper.affiliations)}")
    meta = [part for part in [paper.venue, str(paper.year or ""), f"arXiv:{paper.arxiv_id}" if paper.arxiv_id else ""] if part]
    if meta:
        lines.append(f"**Venue**: {' · '.join(meta)}")
    if paper.doi:
        lines.append(f"**DOI**: {paper.doi}")
    lines.append("")
    abstract = paper.abstract.strip()
    if abstract:
        lines.extend(["## Abstract", "", abstract, ""])
    grouped = _blocks_by_sid(paper)
    if not paper.sections:
        # 无编号标题的文档（技术报告、笔记）退回按阅读顺序输出，避免导出为空
        for block in sorted(paper.blocks, key=lambda b: (b.page, b.order, b.bbox.y0)):
            if block.kind in {"header", "footer", "image", "table_text", "reference"}:
                continue
            text = _translation(translations, block) or block.text
            if not text.strip():
                continue
            if block.kind == "heading":
                lines.extend([f"### {text.strip()}", ""])
            elif block.kind == "caption":
                lines.extend([f"*{text.strip()}*", ""])
            elif block.kind == "equation":
                lines.extend(["```", text.strip(), "```", ""])
            else:
                lines.extend([text.strip(), ""])
    for section in paper.sections:
        level = min(6, max(2, section.level + 1))
        lines.append(f"{'#' * level} {section.title}")
        lines.append("")
        for block in grouped.get(section.sid, []):
            if block.kind in {"heading", "header", "footer"}:
                continue
            text = _translation(translations, block) or block.text
            if block.kind == "table_text":
                continue
            if block.kind == "caption":
                lines.append(f"*{text.strip()}*")
                lines.append("")
                continue
            if block.kind == "equation":
                lines.extend(["```", block.text.strip(), "```", ""])
                continue
            lines.extend([text.strip(), ""])
    if paper.tables:
        lines.extend(["## Tables", ""])
        for table in paper.tables:
            lines.append(f"**Table {table.number}** {table.caption}".strip())
            lines.append("")
            lines.append(table.markdown or "")
            lines.append("")
    if paper.figures:
        lines.extend(["## Figures", ""])
        for figure in paper.figures:
            lines.append(f"**Figure {figure.number}** {figure.caption}".strip())
            if figure.image_path:
                lines.append("")
                lines.append(f"![Figure {figure.number}]({figure.image_path})")
            lines.append("")
    if paper.equations:
        lines.extend(["## Equations", ""])
        for equation in paper.equations:
            lines.append(f"- ({equation.number}) `{equation.latex or equation.plain}`")
        lines.append("")
    if include_references and paper.references:
        lines.extend(["## References", ""])
        for ref in paper.references:
            lines.append(f"[{ref.idx}] {ref.raw}".strip())
            lines.append("")
    lines.extend(["---", f"导出器：AgentLit v2 · 目标语言：{lang} · 页数：{len(paper.pages)}"])
    return "\n".join(lines).strip() + "\n"


# ---------------------------------------------------------------- HTML


def _figure_html(paper: ParsedPaper, figure: Any, embed_images: bool) -> str:
    src = ""
    if figure.image_path:
        path = Path(figure.image_path)
        src = _data_uri(path, "image/png") if embed_images and path.exists() else figure.image_path
    img = f'<img src="{src}" alt="Figure {figure.number}">' if src else ""
    return (
        f'<figure class="figure" id="figure-{figure.id}">{img}'
        f'<figcaption class="caption"><b>Figure {html_lib.escape(str(figure.number))}</b> '
        f'{_escape(figure.caption)}</figcaption></figure>'
    )


def _data_uri(path: Path, mime: str) -> str:
    import base64

    try:
        payload = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{payload}"
    except OSError:
        return ""


def _table_html(table: Any) -> str:
    header = "".join(f"<th>{_escape(str(cell))}</th>" for cell in table.header)
    body = "".join(
        "<tr>" + "".join(f"<td>{_escape(str(cell))}</td>" for cell in row) + "</tr>" for row in table.rows
    )
    return (
        f'<table class="data" id="table-{table.id}"><caption class="caption"><b>Table {table.number}</b> '
        f"{_escape(table.caption)}</caption><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>"
    )


def to_html(
    paper: ParsedPaper,
    translations: dict[str, str] | None = None,
    *,
    lang: str = "zh",
    embed_images: bool = True,
    include_references: bool = True,
) -> str:
    ref_numbers = {ref.idx for ref in paper.references}
    grouped = _blocks_by_sid(paper)
    body: list[str] = []
    abstract = paper.abstract.strip()
    if abstract:
        body.append(f'<div class="abstract"><b>Abstract</b><p>{_escape(abstract)}</p></div>')
    if not paper.sections:
        for block in sorted(paper.blocks, key=lambda b: (b.page, b.order, b.bbox.y0)):
            if block.kind in {"header", "footer", "image", "table_text", "reference"}:
                continue
            text = _translation(translations, block) or block.text
            if not text.strip():
                continue
            if block.kind == "heading":
                body.append(f'<h3 id="{block.id}">{_escape(text)}</h3>')
            elif block.kind == "caption":
                body.append(f'<p class="caption">{_escape(text)}</p>')
            elif block.kind == "equation":
                body.append(f'<pre class="eq" id="eq-{block.id}">{_escape(text)}</pre>')
            else:
                body.append(f'<p id="{block.id}">{linkify(text, ref_numbers)}</p>')
    for section in paper.sections:
        level = min(5, max(2, section.level + 1))
        body.append(f'<h{level} id="sec-{section.sid}">{_escape(section.number + " " if section.number else "")}{_escape(section.title)}</h{level}>')
        for block in grouped.get(section.sid, []):
            translate = _translation(translations, block)
            if block.kind == "heading":
                continue
            if block.kind == "caption":
                body.append(f'<p class="caption">{_escape(translate or block.text)}</p>')
            elif block.kind == "equation":
                body.append(f'<pre class="eq" id="eq-{block.id}">{_escape(block.text)}</pre>')
            elif block.kind == "header" or block.kind == "footer":
                continue
            else:
                body.append(f'<p id="{block.id}">{linkify(translate or block.text, ref_numbers)}</p>')
    if paper.tables:
        body.append("<h2>Tables</h2>")
        body.extend(_table_html(table) for table in paper.tables)
    if paper.figures:
        body.append("<h2>Figures</h2>")
        body.extend(_figure_html(paper, figure, embed_images) for figure in paper.figures)
    if include_references and paper.references:
        body.append('<h2 id="references">References</h2><ol class="refs">')
        for ref in paper.references:
            body.append(f'<li id="ref-{ref.idx}" value="{ref.idx}">{_escape(ref.raw)}</li>')
        body.append("</ol>")
    header_meta = "".join(
        f'<span class="tag">{_escape(part)}</span>'
        for part in [paper.venue, str(paper.year or ""), f"arXiv:{paper.arxiv_id}" if paper.arxiv_id else ""]
        if part
    )
    return f"""<!doctype html>
<html lang="{html_lib.escape(lang)}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_escape(paper.title)}</title><style>{_CSS}</style></head><body>
<header class="paper"><h1>{_escape(paper.title or paper.filename)}</h1>
<p class="meta">{_escape(', '.join(paper.authors))}</p>
<p class="meta">{_escape('; '.join(paper.affiliations))}</p>
<p>{header_meta}</p></header>
<main>{''.join(body)}</main>
<footer>AgentLit v2 · 解析质量 {paper.quality.score:.2f} · 页数 {len(paper.pages)} · 导出语言 {html_lib.escape(lang)}</footer>
</body></html>
"""


def to_bilingual_html(
    paper: ParsedPaper,
    translations: dict[str, str],
    *,
    lang: str = "zh",
) -> str:
    """左右对照：逐段配成一行，同一滚动容器，天然同步。"""
    rows: list[str] = []
    grouped = _blocks_by_sid(paper)
    for section in paper.sections:
        rows.append(
            f'<h2 id="sec-{section.sid}" style="grid-column:1 / -1">{_escape(section.title)}</h2>'
        )
        for block in grouped.get(section.sid, []):
            if block.kind in {"header", "footer", "heading"}:
                continue
            translation = _translation(translations, block)
            if not translation and block.kind != "paragraph":
                continue
            source = block.text.strip()
            rows.append(
                f'<div class="pair" id="{block.id}">'
                f'<div class="src"><div class="kind">{block.kind} · p.{block.page}</div>{_escape(source)}</div>'
                f'<div class="dst"><div class="kind">{lang}</div>{_escape(translation or "（未翻译）")}</div></div>'
            )
    header_meta = "".join(
        f'<span class="tag">{_escape(part)}</span>'
        for part in [paper.venue, str(paper.year or ""), f"arXiv:{paper.arxiv_id}" if paper.arxiv_id else ""]
        if part
    )
    return f"""<!doctype html>
<html lang="{html_lib.escape(lang)}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_escape(paper.title)} · 中英对照</title><style>{_CSS}</style></head><body>
<header class="paper"><h1>{_escape(paper.title or paper.filename)}</h1>
<p class="meta">{_escape(', '.join(paper.authors))}</p><p>{header_meta}</p></header>
<main class="bilingual">{''.join(rows)}</main>
<footer>AgentLit v2 · 原文 {len(grouped)} 组块 · 译文由本地缓存或在线模型提供</footer>
</body></html>
"""


# ---------------------------------------------------------------- PDF


def _insert_fitted(page: Any, rect: Any, text: str, fontsize: float, align: int = 0) -> None:
    import fitz

    size = max(5.5, min(fontsize or 9.5, 14.0))
    box = fitz.Rect(rect)
    while size > 5.0:
        if page.insert_textbox(box, text, fontsize=size, fontname="china-s", align=align) >= 0:
            return
        size -= 0.5
    page.insert_textbox(box, text[:400], fontsize=5.0, fontname="china-s", align=align)


def _crop_page_image(paper: ParsedPaper, settings: Settings, page_number: int, bbox: list[float]) -> bytes | None:
    """从已渲染的整页 PNG 中裁出图表区域，保证译文 PDF 里图表仍是原图。"""
    path = page_image_path(paper.paper_id, page_number, settings)
    if not path.exists():
        return None
    try:
        from PIL import Image

        info = next((p for p in paper.pages if p.number == page_number), None)
        if info is None:
            return None
        zoom = settings.page_render_dpi / 72.0
        image = Image.open(path)
        box = (
            max(0, int(bbox[0] * zoom)),
            max(0, int(bbox[1] * zoom)),
            min(image.width, int(bbox[2] * zoom)),
            min(image.height, int(bbox[3] * zoom)),
        )
        if box[2] - box[0] < 4 or box[3] - box[1] < 4:
            return None
        buffer = io.BytesIO()
        image.crop(box).save(buffer, format="PNG")
        return buffer.getvalue()
    except Exception as exc:
        logger.warning("裁剪图表失败：%s", exc)
        return None


def to_pdf(
    paper: ParsedPaper,
    translations: dict[str, str] | None = None,
    *,
    output: str | Path,
    settings: Settings | None = None,
) -> Path:
    """按原页包围盒写回译文，保留标题层级、图表位置与页码。"""
    import fitz

    settings = settings or get_settings()
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    figure_map: dict[int, list[Any]] = {}
    for figure in paper.figures:
        figure_map.setdefault(figure.page, []).append(figure)
    table_pages: dict[int, list[Any]] = {}
    for table in paper.tables:
        table_pages.setdefault(table.page, []).append(table)
    blocks_by_page: dict[int, list[TextBlock]] = {}
    for block in paper.blocks:
        blocks_by_page.setdefault(block.page, []).append(block)
    for info in paper.pages:
        page = doc.new_page(width=info.width, height=info.height)
        for figure in figure_map.get(info.number, []):
            payload = _crop_page_image(paper, settings, info.number, [figure.bbox.x0, figure.bbox.y0, figure.bbox.x1, figure.bbox.y1])
            if payload:
                try:
                    page.insert_image(fitz.Rect(figure.bbox.x0, figure.bbox.y0, figure.bbox.x1, figure.bbox.y1), stream=payload)
                except Exception:
                    pass
        table_boxes = [table.bbox for table in table_pages.get(info.number, [])]
        for block in sorted(blocks_by_page.get(info.number, []), key=lambda b: (b.order, b.bbox.y0)):
            if block.kind in {"image"}:
                continue
            if any(block.bbox.intersects(box) for box in table_boxes) and block.kind == "table_text":
                continue
            translation = _translation(translations, block)
            text = (translation or block.text or "").strip()
            if not text:
                continue
            rect = fitz.Rect(block.bbox.x0, max(0, block.bbox.y0 - 1), block.bbox.x1, min(info.height, block.bbox.y1 + 3))
            if rect.height < 6 or rect.width < 12:
                continue
            if block.kind == "header":
                continue
            align = 1 if block.centered else 0
            fontsize = block.font_size or (11.0 if block.kind == "heading" else 9.5)
            _insert_fitted(page, rect, text, fontsize, align=align)
        if info.number != len(paper.pages):
            pass
    doc.set_metadata({"title": paper.title, "author": ", ".join(paper.authors), "producer": "AgentLit v2"})
    doc.save(str(target), deflate=True, garbage=3)
    doc.close()
    return target


def export_paper(
    paper: ParsedPaper,
    fmt: str,
    *,
    output: str | Path | None = None,
    translations: dict[str, str] | None = None,
    lang: str = "zh",
    settings: Settings | None = None,
) -> Path:
    fmt = (fmt or "markdown").lower()
    settings = settings or get_settings()
    out_dir = settings.artifacts_dir / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{paper.paper_id}-{lang}"
    if fmt in {"md", "markdown"}:
        path = Path(output) if output else out_dir / f"{stem}.md"
        path.write_text(to_markdown(paper, translations, lang=lang), encoding="utf-8")
        return path
    if fmt in {"html", "htm"}:
        path = Path(output) if output else out_dir / f"{stem}.html"
        path.write_text(to_html(paper, translations, lang=lang), encoding="utf-8")
        return path
    if fmt in {"bilingual", "bilingual-html", "dual"}:
        path = Path(output) if output else out_dir / f"{stem}-bilingual.html"
        path.write_text(to_bilingual_html(paper, translations or {}, lang=lang), encoding="utf-8")
        return path
    if fmt == "pdf":
        path = Path(output) if output else out_dir / f"{stem}.pdf"
        return to_pdf(paper, translations, output=path, settings=settings)
    raise ValueError(f"不支持的导出格式：{fmt}")
