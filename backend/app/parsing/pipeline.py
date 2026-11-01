"""解析流水线：PDF → 结构化文档（页面图 + 块 + 章节 + 图表 + 参考文献）。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterable

from ..core.config import Settings
from ..core.errors import ParseError, UnsupportedFileError
from ..core.hashing import paper_id_from_bytes, sha256_bytes, simhash64
from ..core.logging import get_logger
from ..core.textutil import normalize_ws
from .assets import extract_equations, extract_figures, extract_tables, mark_table_blocks
from .model import BBox, PageInfo, ParsedPaper, ParseQuality
from .pdftext import PageText, extract_page, render_page_png
from .structure import analyze

log = get_logger(__name__)

MIN_TEXT_CHARS_PER_PAGE = 25
MAX_PAGES = 400


def _open_pdf(source: bytes | str | Path) -> Any:
    import fitz

    try:
        if isinstance(source, (str, Path)):
            doc = fitz.open(str(source))
        else:
            doc = fitz.open(stream=source, filetype="pdf")
    except Exception as exc:
        raise UnsupportedFileError(f"无法打开 PDF：{exc}") from exc
    if doc.is_closed:
        raise ParseError("PDF 文档已关闭")
    if doc.needs_pass:
        doc.close()
        raise ParseError("PDF 已加密，暂不支持密码保护文档")
    if doc.page_count == 0:
        doc.close()
        raise ParseError("PDF 不含任何页面")
    if doc.page_count > MAX_PAGES:
        pages = doc.page_count
        doc.close()
        raise ParseError(f"页数过多（{pages} > {MAX_PAGES}），请拆分后再上传")
    return doc


def parse_pdf(
    source: bytes | str | Path,
    settings: Settings,
    *,
    paper_id: str | None = None,
    filename: str = "",
    render_pages: bool = True,
) -> ParsedPaper:
    """解析入口。``source`` 为 bytes 时直接内存打开，不落临时文件。"""
    started = time.perf_counter()
    raw_bytes = source if isinstance(source, bytes) else Path(source).read_bytes()
    digest = sha256_bytes(raw_bytes)
    pid = paper_id or paper_id_from_bytes(raw_bytes)
    doc = _open_pdf(raw_bytes)
    try:
        pages_text: list[PageText] = []
        page_infos: list[PageInfo] = []
        page_dir = settings.pages_dir / pid
        if render_pages:
            page_dir.mkdir(parents=True, exist_ok=True)
        for index in range(doc.page_count):
            page = doc[index]
            page_text = extract_page(page, settings, page_number=index + 1)
            pages_text.append(page_text)
            if render_pages:
                try:
                    png = render_page_png(page, settings)
                    (page_dir / f"p{index + 1:03d}.png").write_bytes(png)
                except Exception as exc:  # pragma: no cover - 渲染失败不阻断解析
                    log.warning("render page %d failed: %s", index + 1, exc)
            page_infos.append(
                PageInfo(
                    number=index + 1,
                    width=round(page_text.width, 2),
                    height=round(page_text.height, 2),
                    text_chars=page_text.text_chars,
                    blocks=len(page_text.blocks),
                    columns=page_text.columns,
                    ocr=page_text.ocr,
                    ocr_engine=page_text.ocr_engine,
                    rotation=page_text.rotation,
                    blank=page_text.text_chars < MIN_TEXT_CHARS_PER_PAGE,
                )
            )

        analysis = analyze(pages_text, settings)

        tables = []
        figures = []
        for page, page_info in zip(pages_text, page_infos):
            fitz_page = doc[page_info.number - 1]
            page_tables = extract_tables(fitz_page, page_info.number, analysis.blocks)
            table_boxes = [t.bbox for t in page_tables]
            page_figures = extract_figures(
                fitz_page,
                page_info.number,
                analysis.blocks,
                settings,
                pid,
                table_boxes=table_boxes,
            )
            tables.extend(page_tables)
            figures.extend(page_figures)
        mark_table_blocks(analysis.blocks, tables)
        equations = extract_equations(analysis.blocks)

        full_text = "\n".join(b.text for b in analysis.blocks if b.kind in {"paragraph", "heading", "abstract", "reference"})
        quality = _assess_quality(analysis, pages_text, page_infos, tables, figures, equations)
        scanned = bool(page_infos) and sum(1 for p in page_infos if p.ocr) >= max(1, len(page_infos) // 2)
        ocr_engine = next((p.ocr_engine for p in page_infos if p.ocr_engine and p.ocr_engine != "none"), "")
        paper = ParsedPaper(
            paper_id=pid,
            sha256=digest,
            filename=filename or (Path(source).name if not isinstance(source, bytes) else ""),
            title=analysis.title,
            authors=analysis.authors,
            affiliations=analysis.affiliations,
            abstract=analysis.abstract,
            keywords=analysis.keywords,
            arxiv_id=analysis.arxiv_id,
            arxiv_version=analysis.arxiv_version,
            venue=analysis.venue,
            year=analysis.year,
            doi=analysis.doi,
            pages=page_infos,
            blocks=analysis.blocks,
            sections=analysis.sections,
            tables=tables,
            figures=figures,
            equations=equations,
            references=analysis.references,
            layout=analysis.layout,
            scanned=scanned,
            ocr_used=any(p.ocr for p in page_infos),
            ocr_engine=ocr_engine,
            quality=quality,
        )
        paper.elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        _persist(paper, settings, full_text)
        return paper
    finally:
        doc.close()


def _assess_quality(
    analysis: Any,
    pages_text: list[PageText],
    page_infos: list[PageInfo],
    tables: list[Any],
    figures: list[Any],
    equations: list[Any],
) -> ParseQuality:
    text_chars = sum(len(b.text) for b in analysis.blocks)
    headings = sum(1 for b in analysis.blocks if b.kind == "heading")
    checks = {
        "has_title": bool(analysis.title),
        "has_authors": bool(analysis.authors),
        "has_abstract": len(analysis.abstract) > 80,
        "has_sections": len(analysis.sections) >= 3,
        "has_references": len(analysis.references) >= 3,
        "has_tables": bool(tables),
        "has_figures": bool(figures),
        "has_equations": bool(equations),
        "text_layer": any(p.text_chars > 200 for p in page_infos),
    }
    weights = {
        "has_title": 0.18,
        "has_authors": 0.08,
        "has_abstract": 0.14,
        "has_sections": 0.18,
        "has_references": 0.14,
        "has_tables": 0.06,
        "has_figures": 0.06,
        "has_equations": 0.04,
        "text_layer": 0.12,
    }
    score = sum(weights[key] for key, ok in checks.items() if ok)
    ocr_pages = sum(1 for p in page_infos if p.ocr)
    warnings = list(analysis.warnings)
    if ocr_pages:
        warnings.append(f"{ocr_pages} 页使用了 OCR，字符级定位精度低于文本层")
    if not checks["text_layer"]:
        warnings.append("未检测到文本层且 OCR 不可用：该文档仅能展示页面图像")
    return ParseQuality(
        score=round(score, 3),
        text_chars=text_chars,
        pages=len(page_infos),
        ocr_pages=ocr_pages,
        headings=headings,
        tables=len(tables),
        figures=len(figures),
        equations=len(equations),
        references=len(analysis.references),
        warnings=warnings,
        checks=checks,
    )


def _persist(paper: ParsedPaper, settings: Settings, full_text: str) -> None:
    settings.parsed_dir.mkdir(parents=True, exist_ok=True)
    payload = paper.to_dict()
    payload["doc_simhash"] = simhash64(full_text or paper.abstract or paper.title)
    payload["text_chars"] = len(full_text)
    target = settings.parsed_dir / f"{paper.paper_id}.json"
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(target)


def load_parsed(paper_id: str, settings: Settings) -> dict[str, Any] | None:
    path = settings.parsed_dir / f"{paper_id}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def page_image_path(paper_id: str, page: int, settings: Settings) -> Path:
    return settings.pages_dir / paper_id / f"p{page:03d}.png"


def version_hint(parsed: ParsedPaper) -> dict[str, Any]:
    """基于 arXiv 编号/标题给出「保留最新版本」的建议依据。"""
    return {
        "arxiv_base": normalize_ws(parsed.arxiv_id).split("v")[0] if parsed.arxiv_id else "",
        "arxiv_version": parsed.arxiv_version,
        "title": parsed.title,
        "title_norm": normalize_ws(parsed.title).lower(),
        "doc_simhash": simhash64(parsed.abstract or parsed.title),
        "pages": len(parsed.pages),
        "has_text_layer": any(p.text_chars > 200 for p in parsed.pages),
    }


def iter_paragraphs(paper: ParsedPaper) -> Iterable[tuple[str, str]]:
    """(sid, text) 迭代正文段落，供分块使用。"""
    for block in paper.blocks:
        if block.kind in {"paragraph", "abstract"} and len(block.text) >= 20:
            yield block.sid, block.text
