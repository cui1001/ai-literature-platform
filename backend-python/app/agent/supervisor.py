"""Supervisor：自主编排的主管 Agent（参考 LangGraph supervisor-worker 模式）。

主管通过工具调用动态决定派哪个 Worker，直到它调用 finish 宣布完成。
它能根据任务复杂度自适应：简单问题直接 finish，复杂问题才逐级派发 Worker。
"""
import json

from openai import OpenAI

from app import config
from app.agent import workers

_client = OpenAI(
    api_key=config.DEEPSEEK_API_KEY,
    base_url=config.DEEPSEEK_BASE_URL,
)

# ===== 路由工具定义：主管能调用的"决策"工具 =====
SUPERVISOR_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "route_to_retriever",
            "description": "派检索Worker去知识库查找资料。当需要基于资料回答，且还没有检索过时使用。参数是检索问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "要检索的问题"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "route_to_analyst",
            "description": "派分析Worker，从已检索的资料中提炼核心要点。参数是待分析的资料内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "要分析的资料"}
                },
                "required": ["content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "route_to_summarizer",
            "description": "派总结Worker，把分析要点写成结构化报告。参数是分析要点。",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "要总结的分析要点"}
                },
                "required": ["content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "任务完成，输出最终答案给用户。",
            "parameters": {
                "type": "object",
                "properties": {
                    "answer": {"type": "string", "description": "最终回答"}
                },
                "required": ["answer"]
            }
        }
    },
]

# ===== 主管的 system 提示词：定义它的"决策逻辑" =====
SUPERVISOR_SYSTEM = (
    "你是一个任务主管，负责决定如何完成用户的任务。\n"
    "你有三个员工可派遣：\n"
    "  - 检索Worker：在知识库中查找资料（route_to_retriever）\n"
    "  - 分析Worker：从资料中提炼要点（route_to_analyst）\n"
    "  - 总结Worker：把要点写成报告（route_to_summarizer）\n"
    "决策规则：\n"
    "1. 【默认先检索】除非用户问的是纯粹的计算或通用定义（如数学题、"
    "'1+1'），否则回答任何问题前都必须先派检索Worker查知识库，"
    "看到检索结果后再决定下一步。\n"
    "2. 如果检索结果为空或与问题无关，说明知识库没有相关资料，"
    "此时可以基于你的通用知识简要回答，但要明确说明'知识库中未找到相关资料'。\n"
    "3. 如果检索到了相关资料，基于资料回答；需要提炼或总结时，"
    "再派分析Worker或总结Worker。\n"
    "4. 每一步只调用一个工具，观察结果后再决定。\n"
    "5. 只有当你认为任务已完成，才调用 finish。"
)


def run_supervisor(question: str, max_rounds: int = 10) -> str:
    """运行 Supervisor 循环：动态编排 Worker，直到主管宣布完成。

    Args:
        question: 用户问题
        max_rounds: 最大循环轮次（防死循环）
    Returns:
        最终回答
    """
    # 状态：记录各 Worker 的产出，供主管决策参考
    context = {
        "question": question,
        "retrieved": "",    # 检索结果
        "analysis": "",     # 分析结果
        "report": "",       # 总结结果
    }

    messages = [
        {"role": "system", "content": SUPERVISOR_SYSTEM},
        {"role": "user", "content": question},
    ]

    for _ in range(max_rounds):
        # 1. 问主管："下一步派谁？"
        response = _client.chat.completions.create(
            model=config.LLM_MODEL,
            messages=messages,
            tools=SUPERVISOR_TOOLS,
        )
        msg = response.choices[0].message

        # 2. 主管没调工具（异常）→ 直接返回它说的
        if not msg.tool_calls:
            return msg.content or "任务完成，但没有生成回答。"

        messages.append(msg)  # 记录主管的决策

        # 3. 主管调了一个路由工具 → 执行对应 Worker
        for tool_call in msg.tool_calls:
            tool_name = tool_call.function.name
            args = json.loads(tool_call.function.arguments)
            tool_result = ""

            if tool_name == "route_to_retriever":
                tool_result = workers.retrieve(args.get("query", ""))
                context["retrieved"] = tool_result
            elif tool_name == "route_to_analyst":
                # 分析内容：主管传的 content，优先用 context 里的检索结果
                content = args.get("content") or context["retrieved"]
                tool_result = workers.analyze(content)
                context["analysis"] = tool_result
            elif tool_name == "route_to_summarizer":
                content = args.get("content") or context["analysis"]
                tool_result = workers.summarize(content)
                context["report"] = tool_result
            elif tool_name == "finish":
                return args.get("answer", "任务完成")

            # 4. 把执行结果告诉主管，让它继续决策
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": tool_result,
            })

    return "抱歉，主管在多次调度后未能完成任务。"