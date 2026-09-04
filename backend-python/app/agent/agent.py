"""Agent核心：ReAct循环（思考->行动->观察）

Agent在"推理"和"行动"之间交替，直到能回答用户问题或达到最大轮次
"""
import json

from openai import OpenAI

from app import config
from app.agent.tools import TOOLS, execute_tool

_client = OpenAI(
    api_key=config.DEEPSEEK_API_KEY,
    base_url=config.DEEPSEEK_BASE_URL,
)

def run_agent(question: str, max_rounds: int = 5) -> str:
    """运行Agent循环：让AI自主决定是否调用工具，最终给出回答
    Args:
        question:用户问题
        max_rounds:最多循环轮次（防止AI陷入死循环）
    Returns:
        AI的最终回答
    """
    # System 约束：区分"严格基于资料"和"通用知识"场景，防止幻觉
    system_prompt = (
        "你是一个严谨的文献分析助手。\n"
        "如果你决定检索，检索到相关资料后必须严格基于资料回答，"
        "不得编造资料中不存在的事实；\n"
        "如果检索结果不包含用户问题的答案，"
        "明确告知用户'资料中没有找到相关内容'，而不是虚构答案。"
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question},
    ]
    for round in range(max_rounds):
        # 1.思考：把“工具定义”发给我们的大模型，问它要不要用工具
        response = _client.chat.completions.create(
            model=config.LLM_MODEL,
            messages=messages,
            tools=TOOLS,   # 告诉模型有哪些工具可用
        )
        # 2.分析：看模型返回了什么
        msg = response.choices[0].message

        # 情况A：模型直接回答（没调用工具）
        if not msg.tool_calls:
            return msg.content
        # 情况B：模型调用工具，返回结果
        messages.append(msg) #记录模型的“思考结果”

        for tool_call in msg.tool_calls:
            #解析工具名和参数
            tool_name = tool_call.function.name
            tool_args = json.loads(tool_call.function.arguments)

            #执行工具
            result = execute_tool(tool_name, tool_args)

            #把工具结果加回对话，让模型继续思考
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result,
            })

    # 达到最大轮次还没回答
    return "抱歉，我在多轮尝试后未能给出答案。"

