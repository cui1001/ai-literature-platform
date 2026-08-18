"""向量存储：基于 Chroma 的持久化知识库。

对标 privateGPT 的 vector_store 组件：把"存/查"封装成简单接口，
上层业务（rag_service）不关心底层是 Chroma 还是别的库。
"""
import logging
import chromadb

from app import config

logger = logging.getLogger(__name__)

# Chroma 客户端：数据持久化到本地文件夹
_client = chromadb.PersistentClient(path=config.CHROMA_PATH)

# 知识库集合（存文本 + 向量）
_collection = _client.get_or_create_collection("knowledge_base")


def add_documents(texts: list[str], embeddings: list[list[float]]) -> int:
    """把文本和对应的向量存入知识库。"""
    ids = [str(i) for i in range(_collection.count(), _collection.count() + len(texts))]
    _collection.add(
        ids=ids,
        documents=texts,
        embeddings=embeddings,
    )
    logger.info("已添加 %d 条文档，知识库现有 %d 条", len(texts), _collection.count())
    return _collection.count()


def search(query_embedding: list[float], top_k: int) -> list[str]:
    """按向量相似度检索最相关的文档。"""
    if _collection.count() == 0:
        return []
    results = _collection.query(
        query_embeddings=[query_embedding],
        n_results=min(top_k, _collection.count()),
    )
    if not results["documents"] or not results["documents"][0]:
        return []
    return results["documents"][0]