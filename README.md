# AgentLit · 科研助手 Agent 平台

> 面向「真实学术研究 + 论文写作」场景的全栈式科研助手：从 PDF 导入、结构化解析、抗幻觉问答、
> 版式感知阅读与双语对照，到文献追踪推荐、局域引用图谱、自动综述与写作 / 数据可视化 Copilot。

本仓库前身为一个 Streamlit + Chroma 的 RAG 原型（见 `legacy/`），在保留完整提交历史的前提下，
按竞赛六大命题完成全栈重构。

## 快速开始

```bash
# 1) 依赖（仅需 Python 3.11+）
python -m pip install -r backend/requirements.txt

# 2) 生成本地演示语料（12 篇合成论文：双栏 / 扫描件 / 公式 / 表格 / 引用网络）
python scripts/make_corpus.py --out fixtures/corpus

# 3) 一键启动（自动建库、灌入语料并打开浏览器）
python scripts/run_server.py --demo
```

打开 <http://127.0.0.1:8000> 即可体验完整闭环。

无网络环境下系统自动进入**离线确定性模式**：检索、抽取式问答、图谱、综述、图表全部由本地算法完成；
配置 `DEEPSEEK_API_KEY` 等环境变量后自动切换在线大模型增强。

## 六大命题模块

| 模块 | 能力 | 代码位置 |
| --- | --- | --- |
| 一 · 文献管理 | 上传 / 批量 / 版本语义去重 / 细粒度解析 / OCR | `backend/app/parsing/`、`backend/app/api/library.py` |
| 二 · 抗幻觉问答 | 块级 RAG、引用锚点、多文对比、claim 校验 | `backend/app/indexing/`、`backend/app/qa/` |
| 三 · 版式阅读 | 双栏还原、标题树、引用双向跳转、中英对照 | `backend/app/reading/`、`frontend/src/views/reader.js` |
| 四 · 发现追踪 | arXiv 检索 / 引用含金量筛选 / 推荐与反馈 | `backend/app/discovery/` |
| 五 · 图谱综述 | 引用拓扑、基石/边缘节点、Future Work 提炼 | `backend/app/graph/` |
| 六 · 写作 Copilot | 无幻觉大纲、TikZ/Graphviz/Mermaid/Matplotlib、CSV 可视化 | `backend/app/writing/`、`backend/app/dataviz/` |

详细设计见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)，命题逐条对照见
[docs/COMPETITION_MATRIX.md](docs/COMPETITION_MATRIX.md)，开发历程见 [docs/DEVLOG.md](docs/DEVLOG.md)。

## 目录结构

```
backend/app/          # FastAPI 应用（core / db / providers / parsing / indexing / qa / reading / ...）
backend/tests/        # 标准库 unittest 测试
backend/eval/         # 可复现评测脚本（解析质量、检索指标）
frontend/             # 零依赖原生 ES Module SPA（无构建步骤）
fixtures/corpus/      # 合成论文语料与 arXiv 离线镜像
scripts/              # 语料生成、服务启动、演示流水线
docs/                 # 架构、命题映射、开发日志
legacy/               # 重构前的原型代码（仅作历史留存）
```

## 复现性承诺

- 所有实体 ID 由内容哈希派生，重复导入结果稳定；
- 解析产物落盘为 JSON，可逐字段核对；
- 检索 / 图谱布局 / 图表几何使用固定种子，两次运行哈希一致；
- `python -m unittest discover -s backend/tests` 与 `python backend/eval/run_eval.py` 可离线执行。
