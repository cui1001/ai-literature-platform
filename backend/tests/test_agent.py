"""Agent 测试：验证 ReAct 循环是否能自主检索。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent import agent
from app import vector_store
from app.services import embedding_service

# 确保知识库有数据（若已存在则跳过）
DOCS = [
    "TCP建立连接需要三次握手，断开连接需要四次挥手。",
    "二叉树的前序遍历顺序是根节点、左子树、右子树。",
    "快速排序的平均时间复杂度是O(n log n)。",
]


def main():
    print("=" * 60)
    print("Agent 测试（ReAct 自主检索）")
    print("=" * 60)

    # 1. 确保知识库有数据
    print("\n[1] 确认知识库数据...")
    if vector_store._collection.count() == 0:
        embeddings = [embedding_service.get_embedding(d) for d in DOCS]
        vector_store.add_documents(DOCS, embeddings)
        print("    已添加测试数据")
    else:
        print(f"    知识库已有 {vector_store._collection.count()} 条，直接测试")

    # 2. 调用 run_agent 测试
    questions = [
        "TCP建立连接需要几次握手？",   # 需要检索
        "二叉树前序遍历的顺序是什么？",  # 需要检索
        "1+1等于几？",   # 纯常识，不该检索
    ]

    for q in questions:
        print(f"\n[2] 提问: {q}")
        answer = agent.run_agent(q)
        print(f"    Agent 回答: {str(answer)}")

    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
