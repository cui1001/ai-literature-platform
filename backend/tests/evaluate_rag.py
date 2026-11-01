"""RAG 评测体系：用数据对比"纯向量检索"和"混合检索"的准确率。

什么是评测？
  构造一个评测集（问题 + 标准答案 + 来源文档），
  让 RAG 检索每个问题的相关段落，看检索结果是否命中"标准答案来源"，
  统计命中率（准确率），用数据说明检索效果。

复试讲法：
  "我建了评测集，对比了纯向量和混合检索的准确率，得出...结论"
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import vector_store
from app.services import embedding_service


# ===== 评测集：每个条目 = 一个测试问题 + 它的标准答案来源文档 =====
# "source" 是正确答案所在的文档（用它来评估检索是否命中）
EVAL_SET = [
    {"question": "TCP建立连接需要几次握手？",
     "source": "TCP建立连接需要三次握手，断开需要四次挥手。"},
    {"question": "二叉树前序遍历的顺序是什么？",
     "source": "二叉树的前序遍历顺序是根节点、左子树、右子树。"},
    {"question": "快速排序的平均时间复杂度？",
     "source": "快速排序的平均时间复杂度是O(n log n)。"},
    {"question": "操作系统的PV操作解决什么问题？",
     "source": "PV操作是解决进程同步与互斥问题的信号量机制。"},
    {"question": "LRU页面置换算法淘汰什么页面？",
     "source": "LRU算法淘汰最久未被使用的页面。"},
    {"question": "IEEE754标准规定什么？",
     "source": "IEEE754标准规定了浮点数的表示方法。"},
    {"question": "HTTP和HTTPS的区别？",
     "source": "HTTPS相比HTTP增加了加密层，更安全。"},
    {"question": "什么是死锁？",
     "source": "死锁是指多个进程互相等待对方释放资源无法推进的状态。"},
]

# 知识库：把评测集的所有 source 文档 + 一些干扰文档放进去
KB_DOCS = [item["source"] for item in EVAL_SET] + [
    # 干扰文档（语义相关但不含正确答案关键词）
    "网络协议负责数据传输的可靠性保证。",
    "数据结构中树的遍历有多种方法。",
    "算法效率通常用时间复杂度和空间复杂度衡量。",
    "进程之间需要协调对共享资源的访问。",
    "内存管理涉及页表的设计和页面大小的选择。",
    "计算机中数值有多种表示方法。",
    "网络安全需要加密和认证机制。",
    "并发编程中需要处理资源共享和同步问题。",
]


def evaluate(mode: str) -> float:
    """评测给定检索模式的准确率。

    mode: "vector"（纯向量）或 "hybrid"（混合检索）
    返回：命中率（0~1）
    """
    hits = 0
    total = len(EVAL_SET)

    for item in EVAL_SET:
        q = item["question"]
        source = item["source"]
        qv = embedding_service.get_embedding(q)

        if mode == "vector":
            # 纯向量：取 top1
            results = vector_store._collection.query(
                query_embeddings=[qv], n_results=1,
            )["documents"][0]
            retrieved = results[0] if results else ""
        else:
            # 混合检索：取 top1
            search_results = vector_store.search(q, qv, top_k=1)
            retrieved = search_results[0] if search_results else ""

        # 判断：检索到的段落是否就是标准答案来源
        if retrieved == source:
            hits += 1

    return hits / total


def main():
    print("=" * 60)
    print("RAG 评测体系")
    print("=" * 60)

    # 1. 建知识库（评测 + 干扰文档）
    print(f"\n[1] 构建知识库：{len(KB_DOCS)} 条文档...")
    embeddings = [embedding_service.get_embedding(d) for d in KB_DOCS]
    vector_store.add_documents(KB_DOCS, embeddings)
    print(f"    知识库现有 {vector_store._collection.count()} 条")

    # 2. 对比评测
    print("\n[2] 分别评测两种检索模式")
    vector_acc = evaluate("vector")
    hybrid_acc = evaluate("hybrid")

    # 3. 输出报告
    print("\n" + "=" * 60)
    print("评测结果报告")
    print("=" * 60)
    print(f"  评测集规模: {len(EVAL_SET)} 个问题")
    print(f"  纯向量检索准确率: {vector_acc:.0%}")
    print(f"  混合检索准确率: {hybrid_acc:.0%}")
    diff = hybrid_acc - vector_acc
    if diff > 0:
        print(f"  → 混合检索比纯向量提升: {diff:.0%}")
    elif diff == 0:
        print(f"  → 两者持平（混合检索无明显优势，符合 bge-m3 模型较强的结论）")
    else:
        print(f"  → 混合检索反而下降: {diff:.0%}（需检查）")
    print("=" * 60)


if __name__ == "__main__":
    main()
