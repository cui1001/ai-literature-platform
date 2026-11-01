"""索引构建：解析结果 → 分块 → 向量 → SQLite，供混合检索使用。

设计要点：
- 分块与向量都由确定性算法得出（chunk_id = 内容哈希），重复索引同一篇论文得到完全相同的 ID；
- 向量模型名写入 ``chunks.embeddings.model``，换模型时旧向量可整体识别并重建；
- 索引是幂等的：``index_paper`` 会先删除旧 chunk 与旧向量再写入。
"""

from __future__ import annotations

import time
from typing import Any, Callable, Sequence

from ..core.config import Settings, get_settings
from ..core.logging import get_logger
from ..db import repo
from ..parsing.model import ParsedPaper
from ..parsing.pipeline import load_parsed
from ..providers.embedding import get_embedder, to_blob
from .chunker import Chunk, chunk_paper, chunk_stats

logger = get_logger(__name__)

BATCH_SIZE = 32


def embedding_text(chunk: Chunk) -> str:
    """向量化文本：拼上章节路径，让「训练细节」这类查询更容易落在正确章节。"""
    prefix = chunk.section_path or chunk.section
    body = f"{prefix}：{chunk.text}" if prefix and prefix not in chunk.text else chunk.text
    return body[:2400]


def index_paper(
    paper: ParsedPaper,
    settings: Settings | None = None,
    embedder: Any | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    started = time.perf_counter()
    chunks = chunk_paper(paper)
    if not chunks:
        logger.warning("index_paper: 论文 %s 没有可分块的正文", paper.paper_id)
        repo.replace_chunks(paper.paper_id, [])
        repo.delete_embeddings(paper.paper_id)
        return {"paper_id": paper.paper_id, "chunks": 0, "embedded": 0, "model": "", "elapsed_ms": 0.0}

    repo.replace_chunks(paper.paper_id, [chunk.to_record() for chunk in chunks])
    repo.delete_embeddings(paper.paper_id)

    embedder = embedder or get_embedder(settings)
    model = str(getattr(embedder, "model", "hashing-v1"))
    texts = [embedding_text(chunk) for chunk in chunks]
    rows: list[tuple[str, str, int, bytes]] = []
    for start in range(0, len(texts), BATCH_SIZE):
        batch = texts[start : start + BATCH_SIZE]
        vectors = embedder.encode(batch)
        for offset, vector in enumerate(vectors):
            chunk = chunks[start + offset]
            rows.append((chunk.chunk_id, model, len(vector), to_blob(vector)))
    repo.save_embeddings(rows)

    elapsed = round((time.perf_counter() - started) * 1000, 1)
    stats = chunk_stats(chunks)
    logger.info(
        "index_paper: %s chunks=%s model=%s elapsed=%.1fms",
        paper.paper_id,
        stats["chunks"],
        model,
        elapsed,
    )
    return {
        "paper_id": paper.paper_id,
        "chunks": stats["chunks"],
        "avg_tokens": stats["avg_tokens"],
        "kinds": stats["kinds"],
        "embedded": len(rows),
        "model": model,
        "dim": len(rows[0][3]) // 4 if rows else 0,
        "elapsed_ms": elapsed,
    }


def index_paper_by_id(paper_id: str, settings: Settings | None = None) -> dict[str, Any] | None:
    settings = settings or get_settings()
    data = load_parsed(paper_id, settings)
    if not data:
        return None
    return index_paper(ParsedPaper.from_dict(data), settings)


def reindex_library(
    settings: Settings | None = None,
    paper_ids: Sequence[str] | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    """重建整库索引；返回逐篇统计，便于前端展示「索引完成 n/m」。"""
    settings = settings or get_settings()
    embedder = get_embedder(settings)
    papers = repo.list_papers(limit=10000)
    targets = [p for p in papers if not paper_ids or p["id"] in set(paper_ids)]
    results: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    for index, paper in enumerate(targets, start=1):
        try:
            data = load_parsed(paper["id"], settings)
            if not data:
                failed.append({"paper_id": paper["id"], "error": "缺少解析缓存"})
                continue
            stats = index_paper(ParsedPaper.from_dict(data), settings, embedder)
            results.append(stats)
        except Exception as exc:  # noqa: BLE001 - 单篇失败不应中断整库重建
            logger.exception("reindex_library failed for %s", paper["id"])
            failed.append({"paper_id": paper["id"], "error": str(exc)})
        if progress:
            progress(index, len(targets), paper.get("title") or paper["id"])
    return {
        "papers": len(results),
        "chunks": sum(item["chunks"] for item in results),
        "embedded": sum(item["embedded"] for item in results),
        "model": getattr(embedder, "model", ""),
        "failed": failed,
        "details": results,
    }


def index_status(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    embedder = get_embedder(settings)
    model = str(getattr(embedder, "model", ""))
    total_chunks = repo.count_chunks()
    vectors = repo.load_embeddings(model) if model else {}
    papers = repo.list_papers(limit=10000)
    embedded_papers = {row["paper_id"] for row in repo.query("SELECT DISTINCT paper_id FROM chunks")}
    return {
        "papers": len(papers),
        "papers_with_chunks": len(embedded_papers),
        "chunks": total_chunks,
        "vectors": len(vectors),
        "model": model,
        "coverage": round(len(vectors) / total_chunks, 4) if total_chunks else 0.0,
        "stale": total_chunks - len(vectors),
    }


def ensure_index(paper_id: str, settings: Settings | None = None) -> dict[str, Any] | None:
    """懒索引：上传后立即索引；若用户手动删了索引，访问时自动补建。"""
    settings = settings or get_settings()
    if repo.count_chunks() and repo.get_chunks(paper_id, chunk_ids=None):
        return None
    return index_paper_by_id(paper_id, settings)
