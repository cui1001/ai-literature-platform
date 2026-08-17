"""LLM 服务：封装所有 DeepSeek 对话调用。"""
from openai import OpenAI
from app import config

# 全局唯一的客户端（只创建一次）
_client = OpenAI(
    api_key=config.DEEPSEEK_API_KEY,
    base_url=config.DEEPSEEK_BASE_URL,
)

def chat(messages: list) -> str:
    """发送对话，返回 AI 的回答文本。"""
    response = _client.chat.completions.create(
        model=config.LLM_MODEL,
        messages=messages,
    )
    return response.choices[0].message.content