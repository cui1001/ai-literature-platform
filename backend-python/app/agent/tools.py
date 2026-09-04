"""Agent 工具：定义并执行大模型可调用的函数。

Function Calling 分为两部分：
  1. TOOLS 定义（告诉大模型有哪些工具可用）
  2. execute_tool()（大模型决定调用后，真实执行）
"""
import json
from app.services import rag_service
from app.services import embedding_service
from app import config
from app import vector_store


# 1. 工具定义（JSON Schema，告诉大模型："我的 search_documents 长这样"）
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_documents",
            "description": "在知识库中检索与问题最相关的文档段落。当问题需要基于资料回答时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "要检索的关键词或问题"
                    }
                },
                "required": ["query"]
            }
        }
    }
]


# 2. 工具执行：大模型决定调用 search_documents 时，真正执行它
def search_documents(query: str) -> str:
    """在知识库中检索相关文档，返回文本结果。"""
    question_vector = embedding_service.get_embedding(query)
    top_texts = vector_store.search(query, question_vector, config.TOP_K)
    if not top_texts:
        return "知识库为空"
    # 返回检索结果（拼接成文本给大模型）
    return "\n\n".join(top_texts)


# 3. 工具分发：根据大模型告诉我们的"工具名和参数"，调用对应函数
def execute_tool(tool_name: str, tool_args: dict) -> str:
    """根据工具名执行对应函数。"""
    if tool_name == "search_documents":
        return search_documents(tool_args.get("query", ""))
    return f"未知工具: {tool_name}"