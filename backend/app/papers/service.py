"""文献导入服务：上传、语义去重、解析入库、索引与版本管理。

设计要点（可复现 / 可审查）：

* 主键 = PDF 字节 sha1 前 16 位，同一文件重复上传不会产生新条目；
* 解析结果写 ``data/parsed/<paper_id>.json``，页面图写 ``data/pages/<paper_id>/``，
  论文 PDF 本体写 ``data/papers/<paper_id>.pdf``，全部可离线复现；
* 去重判定不依赖文件名，而是 sha256 → arXiv 版本号 → 标题+正文 SimHash 三级证据；
* 判定为「库中已有更新版本」时不覆盖入库结果，而是把选择权交还前端。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..core.clock import utc_now_iso
from ..core.config import Settings, get_settings
from ..core.errors import NotFoundError, ParseError, UnsupportedFileError, ValidationError
from ..core.hashing import paper_id_from_bytes, sha256_bytes
from ..core.logging import get_logger
from ..core.textutil import normalize_title, text_similarity, truncate
from ..db import repo
from ..indexing.indexer import index_paper
from ..indexing.retriever import reset_retriever
from ..parsing import ParsedPaper, load_parsed, parse_pdf
from ..parsing.dedupe import arxiv_base, compare_parsed, detect_duplicate

log = get_logger("papers")

_MASK64 = (1 << 64) - 1


def _signed64(value: int) -> int:
    """SQLite INTEGER 只有 64 位有符号，SimHash 需压缩到有符号区间存储。"""
    value &= _MASK64
    return value - (1 << 64) if value >= (1 << 63) else value

_MAX_FILE_BYTES = 130 * 1024 * 1024
_PDF_MAGIC = b"%PDF"

SUMMARY_FIELDS = (
    "id",
    "sha256",
    "title",
    "authors",
    "year",
    "venue",
    "arxiv_id",
    "arxiv_version",
    "pages",
    "layout",
    "ocr_used",
    "ocr_engine",
    "parse_status",
    "parse_quality",
    "version_group",
    "version_label",
    "is_latest",
    "size_bytes",
    "source",
    "created_at",
)


def _settings(settings: Settings | None) -> Settings:
    return settings or get_settings()


def summary(record: dict[str, Any]) -> dict[str, Any]:
    """列表/上传响应共用的论文摘要。"""
    if not record:
        return {}
    out = {key: record.get(key) for key in SUMMARY_FIELDS}
    out["title"] = record.get("title") or record.get("filename") or "未命名文献"
    out["id"] = record.get("id") or ""
    authors = record.get("authors") or []
    out["authors"] = authors if isinstance(authors, list) else []
    quality = record.get("parse_quality") or {}
    if isinstance(quality, str):
        quality = {}
    out["quality_score"] = round(float(quality.get("score") or 0.0), 3)
    out["has_code"] = bool(record.get("has_code"))
    return out


def _dedupe_records(limit: int = 300) -> list[dict[str, Any]]:
    rows = repo.list_papers(limit=limit) or []
    records = []
    for row in rows:
        records.append(
            {
                "paper_id": row.get("id"),
                "sha256": row.get("sha256"),
                "arxiv_id": row.get("arxiv_id"),
                "arxiv_version": row.get("arxiv_version"),
                "title": row.get("title"),
                "doc_simhash": (int(row.get("doc_simhash") or 0) & _MASK64) or None,
            }
        )
    return records


def _store_pdf(data: bytes, paper_id: str, settings: Settings) -> Path:
    settings.ensure_dirs()
    target = settings.papers_dir / f"{paper_id}.pdf"
    tmp = target.with_suffix(".pdf.tmp")
    tmp.write_bytes(data)
    tmp.replace(target)
    return target


def _paper_row(parsed: ParsedPaper, *, filename: str, size_bytes: int, file_path: str, source: str) -> dict[str, Any]:
    text_sample = parsed.full_text(20000)
    from ..core.hashing import simhash64

    doc_simhash = simhash64(text_sample or parsed.abstract or parsed.title)
    arxiv_version = parsed.arxiv_version or ""
    if arxiv_version and not str(arxiv_version).startswith("v"):
        arxiv_version = f"v{arxiv_version}"
    version_group = arxiv_base(parsed.arxiv_id) or normalize_title(parsed.title)[:80] or parsed.paper_id
    return {
        "id": parsed.paper_id,
        "sha256": parsed.sha256,
        "filename": filename,
        "file_path": file_path,
        "size_bytes": size_bytes,
        "title": parsed.title,
        "authors": parsed.authors,
        "affiliations": parsed.affiliations,
        "abstract": parsed.abstract,
        "keywords": parsed.keywords,
        "year": parsed.year,
        "venue": parsed.venue,
        "doi": parsed.doi,
        "arxiv_id": parsed.arxiv_id,
        "arxiv_version": arxiv_version,
        "pages": len(parsed.pages),
        "ocr_used": 1 if parsed.ocr_used else 0,
        "ocr_engine": parsed.ocr_engine,
        "layout": parsed.layout,
        "parse_status": "done",
        "parse_quality": parsed.quality.to_dict() if hasattr(parsed.quality, "to_dict") else {},
        "version_group": version_group,
        "version_label": arxiv_version or "v1",
        "is_latest": 1,
        "doc_simhash": _signed64(doc_simhash),
        "source": source,
        "lang": "en",
    }


def _refs_rows(parsed: ParsedPaper) -> list[dict[str, Any]]:
    rows = []
    for index, ref in enumerate(parsed.references):
        rows.append(
            {
                "ref_idx": ref.idx or index + 1,
                "raw": ref.raw,
                "title": ref.title,
                "authors": ref.authors,
                "year": ref.year,
                "venue": ref.venue,
                "doi": ref.doi,
                "arxiv_id": ref.arxiv_id,
                "url": ref.url,
            }
        )
    return rows


def ingest_bytes(
    data: bytes,
    filename: str,
    *,
    settings: Settings | None = None,
    source: str = "upload",
    index: bool = True,
    force: bool = False,
    user_id: str = "local",
) -> dict[str, Any]:
    """上传一份 PDF 并完成去重判定 + 解析 + 入库 + 索引。"""
    started = time.perf_counter()
    s = _settings(settings)
    name = Path(filename or "paper.pdf").name
    if not data:
        raise ValidationError("上传内容为空")
    limit = int(min(_MAX_FILE_BYTES, s.max_upload_mb * 1024 * 1024))
    if len(data) > limit:
        raise ValidationError(f"文件超过 {s.max_upload_mb:.0f} MB 上限")
    if not data.lstrip()[:4].startswith(_PDF_MAGIC):
        raise UnsupportedFileError("仅支持 PDF 文件（文件头不是 %PDF）")
    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf"

    sha256 = sha256_bytes(data)
    existing = repo.get_paper_by_sha(sha256)
    if existing:
        return {
            "status": "duplicate",
            "imported": False,
            "paper": summary(existing),
            "verdict": {
                "is_duplicate": True,
                "kind": "same-file",
                "similarity": 1.0,
                "action": "keep-existing",
                "existing_paper_id": existing.get("id"),
                "existing_title": existing.get("title"),
                "reason": "文件内容哈希完全一致，已直接复用库中条目，未重复解析。",
                "evidence": {"sha256": sha256[:16]},
            },
            "indexed": False,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }

    paper_id = paper_id_from_bytes(data)
    parsed = parse_pdf(data, settings=s, paper_id=paper_id, filename=name)
    if not parsed.title:
        parsed.title = Path(name).stem
    text_sample = parsed.full_text(6000)
    verdict = detect_duplicate(parsed, _dedupe_records(), text_sample=text_sample)
    comparison: dict[str, Any] | None = None
    if verdict.existing_paper_id:
        try:
            old = load_parsed(verdict.existing_paper_id, s)
            if old:
                comparison = compare_parsed(ParsedPaper.from_dict(old), parsed)
        except Exception:  # noqa: BLE001 - 版本对比失败不应阻断导入
            comparison = None

    if verdict.is_duplicate and verdict.action == "keep-existing" and not force:
        log.info("duplicate skipped | %s -> %s (%s)", name, verdict.existing_paper_id, verdict.kind)
        return {
            "status": "duplicate",
            "imported": False,
            "paper": None,
            "candidate": {
                "title": parsed.title,
                "pages": len(parsed.pages),
                "arxiv_id": parsed.arxiv_id,
                "version": parsed.arxiv_version,
            },
            "verdict": verdict.to_dict(),
            "comparison": comparison,
            "indexed": False,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }

    is_latest = 1 if verdict.action != "keep-existing" else 0
    file_path = _store_pdf(data, paper_id, s)
    row = _paper_row(parsed, filename=name, size_bytes=len(data), file_path=str(file_path), source=source)
    row["is_latest"] = is_latest
    if verdict.is_duplicate and verdict.existing_paper_id:
        row["version_group"] = repo.get_paper(verdict.existing_paper_id)["version_group"]  # type: ignore[index]
    inserted = repo.upsert_paper(row)
    repo.replace_sections(paper_id, [section_to_row(section) for section in parsed.sections])
    repo.replace_refs(paper_id, _refs_rows(parsed))
    if is_latest:
        repo.set_latest(str(inserted.get("version_group") or row["version_group"]), paper_id)

    indexed = False
    index_info: dict[str, Any] = {}
    if index:
        try:
            index_info = index_paper(parsed, s)
            indexed = True
            reset_retriever()
        except Exception as exc:  # noqa: BLE001 - 索引失败可稍后重建
            log.warning("index failed | %s | %s", paper_id, exc)
            index_info = {"error": str(exc)}

    status = "replaced" if verdict.action == "replace-existing" else "imported"
    return {
        "status": status,
        "imported": True,
        "paper": summary(inserted),
        "verdict": verdict.to_dict(),
        "comparison": comparison,
        "indexed": indexed,
        "index": index_info,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }


def section_to_row(section: Any) -> dict[str, Any]:
    return {
        "sid": section.sid,
        "level": section.level,
        "number": section.number,
        "title": section.title,
        "page": section.page,
        "end_page": section.end_page,
        "path": section.path,
        "appendix": 1 if section.appendix else 0,
        "block_ids": section.block_ids,
    }


def ingest_path(path: str | Path, *, settings: Settings | None = None, **kwargs: Any) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise NotFoundError(f"文件不存在: {p}")
    return ingest_bytes(p.read_bytes(), p.name, settings=settings, **kwargs)


def ingest_many(
    files: Iterable[tuple[str, bytes]],
    *,
    settings: Settings | None = None,
    source: str = "upload",
    index: bool = True,
) -> dict[str, Any]:
    """批量上传：单篇失败不影响其余文件。"""
    results = []
    for name, data in files:
        try:
            results.append(ingest_bytes(data, name, settings=settings, source=source, index=index))
        except Exception as exc:  # noqa: BLE001
            results.append({"status": "failed", "filename": name, "error": str(exc)})
    imported = sum(1 for item in results if item.get("imported"))
    duplicates = sum(1 for item in results if item.get("status") == "duplicate")
    failed = sum(1 for item in results if item.get("status") == "failed")
    return {
        "total": len(results),
        "imported": imported,
        "duplicates": duplicates,
        "failed": failed,
        "items": results,
    }


def list_library(*, q: str | None = None, sort: str = "recent", limit: int = 50, offset: int = 0, latest_only: bool = False) -> dict[str, Any]:
    rows = repo.list_papers(q=q, sort=sort, limit=limit, offset=offset, latest_only=latest_only)
    return {
        "items": [summary(row) for row in rows],
        "stats": repo.paper_stats(),
        "facets": repo.library_facets(),
        "query": {"q": q, "sort": sort, "limit": limit, "offset": offset, "latest_only": latest_only},
    }


def get_detail(paper_id: str, *, settings: Settings | None = None, include_parsed: bool = True) -> dict[str, Any]:
    s = _settings(settings)
    row = repo.get_paper(paper_id)
    if not row:
        raise NotFoundError(f"文献不存在: {paper_id}")
    out: dict[str, Any] = {
        "paper": summary(row),
        "sections": repo.get_sections(paper_id),
        "references": repo.get_refs(paper_id),
        "chunks": len(repo.get_chunks(paper_id)),
        "versions": [summary(item) for item in repo.version_members(str(row.get("version_group") or ""))],
    }
    if include_parsed:
        parsed = load_parsed(paper_id, s)
        if parsed:
            out["parse"] = {
                "layout": parsed.get("layout"),
                "pages": len(parsed.get("pages") or []),
                "blocks": len(parsed.get("blocks") or []),
                "tables": len(parsed.get("tables") or []),
                "figures": len(parsed.get("figures") or []),
                "equations": len(parsed.get("equations") or []),
                "heading_tree": _tree(parsed.get("sections") or []),
                "quality": parsed.get("quality") or {},
                "ocr_used": parsed.get("ocr_used"),
                "ocr_engine": parsed.get("ocr_engine"),
            }
    return out


def _tree(sections: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    stack: list[dict[str, Any]] = []
    for section in sections:
        level = int(section.get("level") or 1)
        node = {
            "sid": section.get("sid"),
            "title": section.get("title"),
            "number": section.get("number"),
            "level": level,
            "page": section.get("page"),
            "children": [],
        }
        while stack and stack[-1]["level"] >= level:
            stack.pop()
        if stack:
            stack[-1]["children"].append(node)
        else:
            nodes.append(node)
        stack.append(node)
    return nodes


def compare(paper_a: str, paper_b: str, *, settings: Settings | None = None) -> dict[str, Any]:
    s = _settings(settings)
    a = load_parsed(paper_a, s)
    b = load_parsed(paper_b, s)
    if not a or not b:
        raise NotFoundError("需要两篇已完成解析的文献才能进行版本对比")
    diff = compare_parsed(ParsedPaper.from_dict(a), ParsedPaper.from_dict(b))
    return {
        "a": summary(repo.get_paper(paper_a) or {}),
        "b": summary(repo.get_paper(paper_b) or {}),
        "diff": diff,
        "text_preview": {
            "a": truncate(a.get("abstract") or "", 200),
            "b": truncate(b.get("abstract") or "", 200),
        },
    }


def delete(paper_id: str, *, settings: Settings | None = None) -> dict[str, Any]:
    s = _settings(settings)
    row = repo.get_paper(paper_id)
    if not row:
        raise NotFoundError(f"文献不存在: {paper_id}")
    repo.delete_paper(paper_id)
    removed: list[str] = []
    parsed_path = s.parsed_dir / f"{paper_id}.json"
    if parsed_path.exists():
        parsed_path.unlink()
        removed.append(str(parsed_path))
    pdf_path = s.papers_dir / f"{paper_id}.pdf"
    if pdf_path.exists():
        pdf_path.unlink()
        removed.append(str(pdf_path))
    pages_dir = s.pages_dir / paper_id
    if pages_dir.exists():
        for item in pages_dir.glob("*.png"):
            item.unlink()
        pages_dir.rmdir()
        removed.append(str(pages_dir))
    for figure in s.figures_dir.glob(f"{paper_id}-*.png"):
        figure.unlink()
        removed.append(str(figure))
    reset_retriever()
    return {"deleted": paper_id, "title": row.get("title"), "artifacts_removed": len(removed)}


def search_titles(query: str, *, limit: int = 10) -> list[dict[str, Any]]:
    """按标题近似度在本地库中检索（供「引用入库」提示用）。"""
    query_norm = normalize_title(query or "")
    scored = []
    for row in repo.list_papers(limit=500) or []:
        sim = text_similarity(query_norm, str(row.get("title_norm") or ""))
        if sim > 0.2:
            scored.append((sim, row))
    scored.sort(key=lambda item: -item[0])
    return [dict(summary(row), similarity=round(sim, 3)) for sim, row in scored[:limit]]


def ensure_parsed(paper_id: str, *, settings: Settings | None = None) -> ParsedPaper:
    s = _settings(settings)
    payload = load_parsed(paper_id, s)
    if not payload:
        raise ParseError(f"论文尚未完成解析: {paper_id}")
    return ParsedPaper.from_dict(payload)


def ingested_at(paper_id: str) -> str:
    row = repo.get_paper(paper_id)
    return str(row.get("created_at") or "") if row else ""
