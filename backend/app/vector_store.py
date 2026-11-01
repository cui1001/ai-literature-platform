""""向量存储：基于 Chroma + BM25 的混合检索知识库。

对标 privateGPT 的 vector_store 组件：封装"存/查"为简单接口。
混合检索 = Chroma 向量检索（语义）+ BM25 关键词检索（字面）。
"""
import logging
import re
import chromadb
from rank_bm25 import BM25Okapi

from app import config

logger = logging.getLogger(__name__)

_client = chromadb.PersistentClient(path=config.CHROMA_PATH)
_collection = _client.get_or_create_collection("knowledge_base")

# BM25 关键词索引（内存态，添加数据后重建）
_bm25 = None
_all_texts: list[str] = []


def _tokenize(text: str) -> list[str]:
    """分词：中文按单字，英文按单词。"""
    return re.findall(r'[\u4e00-\u9fff]|[a-zA-Z0-9]+', text)


def _rebuild_bm25() -> None:
    """从 Chroma 读取全部文本，重建 BM25 索引。"""
    global _bm25, _all_texts
    _all_texts = _collection.get()["documents"] or []
    tokenized = [_tokenize(t) for t in _all_texts]
    _bm25 = BM25Okapi(tokenized) if tokenized else None


def add_documents(texts: list[str], embeddings: list[list[float]]) -> int:
    """把文本和向量存入知识库，并重建 BM25 索引。"""
    ids = [str(i) for i in range(_collection.count(), _collection.count() + len(texts))]
    _collection.add(ids=ids, documents=texts, embeddings=embeddings)
    _rebuild_bm25()
    logger.info("已添加 %d 条文档，知识库现有 %d 条", len(texts), _collection.count())
    return _collection.count()


def search(query: str, query_embedding: list[float], top_k: int) -> list[str]:
    """混合检索：向量 + 关键词，合并去重后取最相关的 top_k 段。"""
    if _collection.count() == 0:
        return []

    # 1. 向量检索：取 top_k*2 个候选
    vector_hits: list[str] = []
    results = _collection.query(
        query_embeddings=[query_embedding],
        n_results=min(top_k * 2, _collection.count()),
    )
    if results["documents"] and results["documents"][0]:
        vector_hits = results["documents"][0]

    # 2. 关键词检索：BM25 打分，取 top_k*2 个候选
    keyword_hits: list[str] = []
    if _bm25 is not None:
        scores = _bm25.get_scores(_tokenize(query))
        ranked = sorted(zip(scores, _all_texts), key=lambda x: x[0], reverse=True)
        keyword_hits = [text for _, text in ranked[:top_k * 2]]

    # 3. 合并去重（向量结果在前），取 top_k
    merged: list[str] = []
    seen: set[str] = set()
    for text in vector_hits + keyword_hits:
        if text not in seen:
            seen.add(text)
            merged.append(text)

    return merged[:top_k]