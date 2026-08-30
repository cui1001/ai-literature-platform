"""混合检索测试 v5：用"生造型号词"刁难向量模型，逼 BM25 立功。

向量模型训练时没见过"XK-42"这种生造词，语义理解会失效；
BM25 靠字面精确匹配，能命中含"XK-42"的文档。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import vector_store
from app.services import embedding_service

ORIGINAL_COUNT = vector_store._collection.count()

# 生造型号词：向量模型没见过的组合
DOCS = [
    # === 目标文档（含生造型号，应该被检索到） ===
    "XK-42型工业设备在校准时需要按照操作规程进行三次复核。",
    # === 干扰文档（语义相近，讲设备校准，但不含 XK-42） ===
    "工业设备校准流程包括零点校准、量程校准和线性度验证。",
    "设备维护人员需要定期对测量仪器进行精度校验。",
    "校准实验室的环境温度和湿度必须控制在标准范围内。",
    "计量器具的检定证书有效期通常为一年，到期需要重新检定。",
    "工厂车间的自动化设备需要每季度进行一次全面检查。",
    "精密仪器的误差分析需要考虑系统误差和随机误差。",
    "设备调试过程中发现异常需要及时记录并上报处理。",
    "生产线的传感器需要定期更换以保证测量准确性。",
]


def test_hybrid_search():
    print("=" * 60)
    print("混合检索测试 v5（生造型号词刁难）")
    print("=" * 60)

    # 1. 添加全部文档
    print(f"\n[1] 添加 {len(DOCS)} 条文档...")
    embeddings = [embedding_service.get_embedding(d) for d in DOCS]
    vector_store.add_documents(DOCS, embeddings)
    print(f"    知识库现有 {vector_store._collection.count()} 条")

    # 2. 用生造型号查询
    q = "XK-42"
    print(f"\n[2] 查询: '{q}'（生造型号，向量模型没见过）")
    qv = embedding_service.get_embedding(q)

    # 纯向量 top1
    vector_only = vector_store._collection.query(
        query_embeddings=[qv],
        n_results=1,
    )["documents"][0]

    # 混合 top1
    hybrid = vector_store.search(q, qv, top_k=1)

    print(f"\n    纯向量 top1: {vector_only[0][:45]}")
    print(f"    混合   top1: {hybrid[0][:45]}")

    # 3. 判断
    print("\n[3] 结论")
    vector_ok = "XK-42" in vector_only[0]
    hybrid_ok = "XK-42" in hybrid[0]
    print(f"    纯向量 命中 XK-42: {vector_ok}")
    print(f"    混合   命中 XK-42: {hybrid_ok}")
    if hybrid_ok and not vector_ok:
        print("    🎯 完美！向量被干扰文档带偏，BM25 靠'XK-42'字面救回目标")
    elif hybrid_ok and vector_ok:
        print("    ⚠️ 两种都命中——向量模型对生造词也强")
    else:
        print("    ❌ 都没命中——检查分词/BM25")

    # 4. 清理
    print(f"\n[4] 清理测试数据（原 {ORIGINAL_COUNT} 条）...")
    current = vector_store._collection.get()
    for i, doc in enumerate(current["documents"]):
        if doc in DOCS:
            try:
                vector_store._collection.delete(ids=[current["ids"][i]])
            except Exception:
                pass
    vector_store._rebuild_bm25()
    print(f"    清理后 {vector_store._collection.count()} 条")

    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)


if __name__ == "__main__":
    test_hybrid_search()
