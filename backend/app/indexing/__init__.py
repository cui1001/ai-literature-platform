"""检索与索引层。

对外接口：
- :func:`chunk_paper` / :class:`Chunk`：结构化文档 → 可检索块；
- :func:`index_paper` / :func:`reindex_library`：写入 chunk 与向量；
- :func:`search` / :class:`Retriever`：BM25 + 向量 + RRF 混合检索；
- :func:`context_block`：把检索结果拼成带编号的 LLM 上下文。
"""

from .bm25 import BM25Index, build_bm25
from .chunker import Chunk, chunk_paper, chunk_stats
from .indexer import (
    embedding_text,
    ensure_index,
    index_paper,
    index_paper_by_id,
    index_status,
    reindex_library,
)
from .retriever import (
    RetrievalResult,
    RetrievedChunk,
    Retriever,
    context_block,
    get_retriever,
    reset_retriever,
    search,
)

__all__ = [
    "BM25Index",
    "Chunk",
    "RetrievalResult",
    "RetrievedChunk",
    "Retriever",
    "build_bm25",
    "chunk_paper",
    "chunk_stats",
    "context_block",
    "embedding_text",
    "ensure_index",
    "get_retriever",
    "index_paper",
    "index_paper_by_id",
    "index_status",
    "reindex_library",
    "reset_retriever",
    "search",
]
