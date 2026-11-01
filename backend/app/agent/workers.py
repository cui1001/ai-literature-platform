"""Workers：Supervisor 手下的三个"员工"。

每个 Worker 是独立函数：收输入 → 返回输出。
Supervisor（supervisor.py）负责决定何时调用谁，并传递数据。
参考 MetaGPT 的角色分工：每个 Worker 只精于一件事。
"""
from app import config
from app.agent.tools import search_documents


# ===== LLM 调用（复用 openai client）=====
from openai import OpenAI

_client = OpenAI(
    api_key=config.DEEPSEEK_API_KEY,
    base_url=config.DEEPSEEK_BASE_URL,
)


def _call_llm(system_prompt: str, user_content: str) -> str:
    """调用大模型（复用逻辑，各 Worker 共用）。"""
    response = _client.chat.completions.create(
        model=config.LLM_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    )
    return response.choices[0].message.content


# ===== Worker 1: 检索 =====
def retrieve(query: str) -> str:
    """检索 Worker：去知识库找资料。

    Args:
        query: 要检索的问题/关键词
    Returns:
        检索到的资料文本
    """
    result = search_documents(query)
    if not result or result == "知识库为空":
        return "知识库中没有找到与查询相关的资料。"
    return result


# ===== Worker 2: 分析 =====
def analyze(content: str) -> str:
    """分析 Worker：从资料中提炼核心要点。

    Args:
        content: 检索到的原始资料
    Returns:
        提炼后的分析要点
    """
    analyst_prompt = (
        "你是一个文献分析专家。请阅读下面的检索资料，提炼出核心要点，"
        "按主题归类，指出关键发现。只基于资料内容，不要编造。"
    )
    return _call_llm(analyst_prompt, content)


# ===== Worker 3: 总结 =====
def summarize(content: str) -> str:
    """总结 Worker：把分析要点写成结构化报告。

    Args:
        content: 分析后的要点
    Returns:
        结构化研究报告
    """
    summarizer_prompt = (
        "你是一个学术总结专家。基于下面的分析要点，写一份结构化的研究报告，"
        "包含：1) 主题概述 2) 核心发现 3) 结论与建议。语言严谨专业。\n"
        "【溯源约束】\n"
        "1. 报告中的所有观点、结论和建议，必须严格基于给定的分析要点，"
        "不得添加分析要点中不存在的具体案例、数据、政策或事实细节。\n"
        "2. 分析要点只提及宏观表述时，报告同样只能停留在宏观层面，"
        "不得擅自编造具体的应用场景、数字或实施建议。\n"
        "3. 如果某个方面在分析要点中没有任何依据，请直接省略该方面，"
        "或用'（资料未提及）'明确标注，绝不虚构。\n"
        "4. 宁可报告简短、有所缺失，也不要通过润色补全让它显得完整。"
    )
    return _call_llm(summarizer_prompt, content)