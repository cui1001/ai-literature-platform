"""RAG 编排服务：检索 + 生成的核心逻辑。"""
import logging

from app import config
from app import vector_store
from app.services import embedding_service
from app.services import llm_service

logger = logging.getLogger(__name__)


def add_documents(texts: list[str]) -> int:
    """把资料段落存进知识库（持久化到 Chroma）。"""
    embeddings = [embedding_service.get_embedding(text) for text in texts]
    return vector_store.add_documents(texts, embeddings)


def answer(question: str) -> str:
    """RAG 问答：从知识库检索相关段落，让 AI 基于它们回答。"""
    # 1. 问题转向量
    question_vector = embedding_service.get_embedding(question)

    # 2. 从 Chroma 检索最相关的 TOP_K 段
    top_texts = vector_store.search(question_vector, config.TOP_K)

    if not top_texts:
        return "知识库为空，请先添加资料"

    # 3. 把相关段落拼进 prompt
    context = "\n\n".join(top_texts)
    prompt = f"""你是一个严谨的文献助手。请只根据下面提供的资料回答问题。
如果资料里没有答案，就明确说"资料中没有相关信息"，不要编造。

【资料】
{context}

【问题】
{question}
"""

    # 4. 生成回答
    return llm_service.chat([{"role": "user", "content": prompt}])
