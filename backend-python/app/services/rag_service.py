"""RAG 编排服务：检索 + 生成的核心逻辑。"""
import logging
import math
from app.services import embedding_service
from app.services import llm_service
from app import config

logger = logging.getLogger(__name__)

# 知识库（内存版，后续会换成向量数据库）
knowledge_base = []

def add_documents(texts: list[str]) -> int:
    """把资料段落存进知识库。"""
    try:
        for text in texts:
            vector = embedding_service.get_embedding(text)
            knowledge_base.append({"text": text, "vector": vector})
        return len(knowledge_base)
    except Exception as e:
        logger.error("添加知识失败: %s", e)
        raise

def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """计算两个向量的余弦相似度。"""
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0

def answer(question: str) -> str:
    """RAG 问答：检索相关段落，让 AI 基于它们回答。"""
    if not knowledge_base:
        return "知识库为空，请先添加资料"

    # 1. 问题转向量
    question_vector = embedding_service.get_embedding(question)

    # 2. 检索最相关的 TOP_K 段
    scored = []
    for item in knowledge_base:
        score = _cosine_similarity(question_vector, item["vector"])
        scored.append((score, item["text"]))
    scored.sort(reverse=True)
    top_texts = [text for _, text in scored[:config.TOP_K]]

    # 3. 拼 prompt
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