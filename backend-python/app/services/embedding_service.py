"""向量化服务：封装文字转向量的调用。"""
from openai import OpenAI
from app import config

_embed_client = OpenAI(
    api_key=config.EMBED_API_KEY,
    base_url=config.EMBED_BASE_URL,
)

def get_embedding(text: str) -> list[float]:
    """把一段文字转成向量。"""
    resp = _embed_client.embeddings.create(
        model=config.EMBED_MODEL,
        input=text,
    )
    return resp.data[0].embedding