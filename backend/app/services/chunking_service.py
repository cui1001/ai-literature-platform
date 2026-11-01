"""文本切块服务，把长文本切成适合向量化的小块"""
import logging

logger = logging.getLogger(__name__)

# 每块目标长度（字符数）
CHUNK_SIZE = 500
# 相邻块的重叠长度（避免截断语义）
CHUNK_OVERLAP = 50

def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """
    把一个长文本切成若干小块，相邻块重叠
    :param text: 原始文本
    :param chunk_size:
    :param overlap:
    :return: 切好的文本块列表
    """
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        if end >= len(text):
            break
        # 下一块从“末尾 - 重叠”开始，保证语义衔接
        start = end - overlap

    logger.info("文本 %d 字切为 %d 块", len(text), len(chunks))
    return chunks