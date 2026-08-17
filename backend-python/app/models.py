"""所有请求/响应模型集中管理。"""
from pydantic import BaseModel

class RagRequest(BaseModel):
    question: str

class KnowledgeAddRequest(BaseModel):
    texts: list[str]

class ChatRequest(BaseModel):
    messages: list[dict]