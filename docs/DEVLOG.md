# 开发日志

> 记录每个迭代阶段的目标、设计取舍、验证方式与遗留问题。
> 时间戳为实际开发时间；提交粒度对应可独立运行的最小闭环。

## 2026-11-01 · 阶段 A：竞赛命题拆解与工程基线

**目标**：在不重写历史的前提下，把 Streamlit 原型演进为可参赛的全栈系统骨架。

**背景盘点**
- 原仓库 14 次提交、34 个文件、约 1100 行：`backend-python` 内是 FastAPI + Chroma + BM25 的单轮 RAG，
  `backend-python/frontend` 是 79 行 Streamlit 页面，测试仅覆盖 agent 冒烟与 RAG 评测脚本。
- 环境为离线 Windows + Python 3.14：chromadb、pandas、matplotlib、pytest、python-multipart 均不可用，
  因此新架构不能延续旧依赖栈（见 ADR-003/004）。

**本阶段变更**
1. `git mv backend-python backend`，旧 Streamlit 前端归档到 `legacy/streamlit`，测试用文档归档到 `legacy/test_docs`，
   保留可追溯的演进痕迹；
2. 新增 `docs/ARCHITECTURE.md`（分层、数据模型、确定性设计、ADR）与 `docs/COMPETITION_MATRIX.md`（命题逐条映射）；
3. 重写 `.gitignore`：运行时数据 `data/`、解析产物、数据库文件不入库；
4. 依赖收敛为 8 个 Python 包，前端零依赖零构建。

**决策**
- 阅读器放弃 PDF.js：改为「服务端页面栅格 + 结构化 bbox 覆盖层」，使锚点、高亮、双语对照在任何环境都可复现；
- 上传不用 multipart：以 raw body + `filename` 查询参数实现，规避缺失依赖；
- 大模型与向量化全部做成可降级网关，离线时用确定性算法保证六大模块仍可完整演示。

**验证**：`git log --oneline` 可见历史完整；目录骨架与文档一致。

**下一步**：core / db 基础设施 → 合成语料生成器 → 解析流水线。
