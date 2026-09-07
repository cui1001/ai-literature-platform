"""Multi-Agent：文献分析流水线（检索Agent → 分析Agent → 总结Agent）。

参考 MetaGPT（Hong et al. 2023）的角色分工思想：
每个 Agent 承担特定角色，前一个 Agent 的输出作为后一个的输入。
"""
import json
from openai import OpenAI

from app import config
from app.agent.tools import search_documents

_client = OpenAI(
    api_key=config.DEEPSEEK_API_KEY,
    base_url=config.DEEPSEEK_BASE_URL,
)


def _call_llm(system_prompt: str, user_content: str) -> str:
    """调用大模型（复用逻辑，各 Agent 共用）。"""
    response = _client.chat.completions.create(
        model=config.LLM_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    )
    return response.choices[0].message.content


def analyze_topic(topic: str, top_k: int = 3) -> str:
    """多 Agent 协作分析一个研究主题。

    Args:
        topic: 研究主题（如 "RAG在医疗领域的应用"）
        top_k: 每个检索返回多少段
    Returns:
        综合分析报告
    """
    # ===== Agent 1: 检索Agent =====
    retriever_prompt = (
        "你是一个文献检索专家。请根据给定的研究主题，"
        "列出需要检索的 2-3 个关键词/子问题，用逗号分隔，只输出关键词列表。"
    )
    queries_text = _call_llm(retriever_prompt, topic)
    queries = [q.strip() for q in queries_text.split(",") if q.strip()][:3]

    # 用检索工具（复用）逐词检索
    retrieved = []
    for q in queries:
        result = search_documents(q)
        if result and result != "知识库为空":
            retrieved.append(result)

    if not retrieved:
        return "知识库中没有找到与主题相关的资料，请先上传相关文献。"

    # ===== Agent 2: 分析Agent =====
    analyst_prompt = (
        "你是一个文献分析专家。请阅读下面的检索资料，提炼出核心要点，"
        "按主题归类，指出关键发现。只基于资料内容，不要编造。"
    )
    analysis = _call_llm(analyst_prompt, "\n\n".join(retrieved))

    # ===== Agent 3: 总结Agent =====
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
    report = _call_llm(summarizer_prompt, analysis)

    return report