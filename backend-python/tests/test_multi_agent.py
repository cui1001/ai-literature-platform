"""Multi-Agent 测试：验证文献分析流水线（检索Agent → 分析Agent → 总结Agent）。

复用 vs 自己写的说明：
- 复用：向量化 embedding_service、Chroma 存储 vector_store
- 核心被测对象：multi_agent.analyze_topic（三个 Agent 的编排）
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.multi_agent import analyze_topic
from app import vector_store
from app.services import embedding_service

# 医疗 RAG 主题的测试资料（multi_agent 的核心场景）
DOCS = [
    "RAG在医疗领域的应用包括辅助诊断、病历摘要和临床决策支持。",
    "RAG通过检索医学文献减少大模型幻觉，提高回答可靠性。",
    "医疗RAG系统需要处理患者隐私数据，涉及合规和伦理问题。",
    "基于RAG的临床问答系统可以帮助医生快速查找诊疗指南。",
]


def main():
    print("=" * 60)
    print("Multi-Agent 测试（检索→分析→总结流水线）")
    print("=" * 60)

    # 1. 确保知识库有数据
    print("\n[1] 确认知识库数据...")
    if vector_store._collection.count() == 0:
        embeddings = [embedding_service.get_embedding(d) for d in DOCS]
        vector_store.add_documents(DOCS, embeddings)
        print("    已添加医疗RAG测试数据")
    else:
        print(f"    知识库已有 {vector_store._collection.count()} 条，直接测试")

    # 2. 跑 Multi-Agent
    topic = "RAG在医疗领域的应用"
    print(f"\n[2] 分析主题: {topic}")
    print("    流程: 检索Agent生成关键词 → 检索 → 分析Agent提炼 → 总结Agent成报告")
    print("\n" + "-" * 60)

    report = analyze_topic(topic)

    print(f"\n[3] 研究报告输出:\n")
    print(report)
    print("\n" + "-" * 60)
    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
