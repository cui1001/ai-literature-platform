# 评测报告（自动生成）

> 生成时间：2026-11-01T13:33:39Z ｜ 运行耗时：50.0s ｜ 运行方式：`cd backend && python -m eval.run_eval`
> 语料：`fixtures/corpus/papers.json` 14 篇合成论文（含扫描件），全部在离线模式运行，无网络、无外部模型。

## 总体指标

| 指标 | 结果 |
| --- | --- |
| 解析成功率 | 100.0% |
| 平均解析质量 | 0.971 |
| 问答金标准命中率 | 90.4%（104/115） |
| 答案附原文锚点比例 | 100.0% |
| 答案通过校验比例 | 62.6% |
| 引用图谱边数 | 10 |
| 基石论文数 | 2 |
| 写作引用校验通过 | 是 |
| 图表 SVG 确定性 | 是 |

## 模块一：PDF 管理与细粒度解析

- 入库 14 / 14 篇；平均质量 0.971
- 表格：13 篇至少 1 张表；参考文献：13 篇 ≥5 条
- 扫描件 OCR：引擎 `winrt`，识别 16342 字符，质量 0.66
- 关键事实覆盖率（GPU/参数量/训练时长）：61.9%

| 论文 | 页 | 章节 | 表 | 图 | 公式 | 参考文献 | 质量 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| udl_scan | 5 | 9 | 0 | 0 | 1 | 0 | 0.660 |
| transformer_v1 | 6 | 25 | 2 | 1 | 8 | 12 | 1.000 |
| transformer_v7 | 6 | 26 | 2 | 1 | 8 | 12 | 1.000 |
| bert | 4 | 17 | 2 | 2 | 2 | 12 | 1.000 |
| gpt1 | 4 | 20 | 2 | 1 | 3 | 12 | 1.000 |
| gpt2 | 4 | 17 | 1 | 1 | 1 | 10 | 1.000 |
| gpt3 | 3 | 18 | 2 | 1 | 2 | 10 | 1.000 |
| resnet | 4 | 16 | 3 | 2 | 2 | 10 | 1.000 |
| alexnet | 4 | 19 | 3 | 1 | 2 | 10 | 1.000 |
| vit | 4 | 17 | 2 | 2 | 2 | 10 | 1.000 |
| lora | 3 | 16 | 2 | 0 | 4 | 10 | 0.940 |
| rag | 3 | 16 | 2 | 1 | 2 | 10 | 1.000 |
| chain_of_thought | 4 | 18 | 2 | 1 | 1 | 9 | 1.000 |
| survey | 6 | 25 | 2 | 1 | 3 | 18 | 1.000 |

**语义去重（1706.03762 v1 → v7）**

- 判定：`version` / `replace-existing`，相似度 1.0
- 文本相似度 0.991；新增章节 ['B Appendix: Additional Ablations']
- 理由：arXiv 编号同为 1706.03762，检测到版本关系：库中已有 v1，本次为 v7。建议保留本次上传的最新版本。

## 模块二：抗幻觉问答与多文对比

- 金标准命中 104/115（90.4%），拒答 0 题
- 每条答案平均引用锚点 100.0% 覆盖；校验通过率 62.6%

| 问题类型 | 命中 / 总数 |
| --- | --- |
| affiliation | 14 / 14 |
| author | 14 / 14 |
| citation | 7 / 13 |
| comparison | 11 / 14 |
| future_work | 1 / 3 |
| hardware | 10 / 10 |
| hyperparameter | 29 / 29 |
| metric | 18 / 18 |

<details><summary>未命中样例（保留以便复核）</summary>

- `transformer_v1` How long does training the big model take compared with the base model? → gold: 12 hours for the base model and 3.5 days for the big model（hit=0.333）
- `transformer_v1` Which optimizer does the paper use, and which reference is cited for it? → gold: Adam, cited as Kingma and Ba (2015)（hit=0.2）
- `transformer_v7` How long does training the big model take compared with the base model? → gold: 12 hours for the base model and 3.5 days for the big model（hit=0.333）
- `transformer_v7` Which optimizer does the paper use, and which reference is cited for it? → gold: Adam, cited as Kingma and Ba (2015)（hit=0.2）
- `gpt3` Which Transformer architecture does GPT-3 build on? → gold: the Transformer, following Vaswani et al. (2017)（hit=0.5）
- `resnet` Which earlier deep convolutional network is cited as a motivation for depth? → gold: AlexNet (Krizhevsky et al., 2012)（hit=0.4）
- `lora` Which large model is used as the main testbed for LoRA? → gold: GPT-3 175B, introduced by Brown et al. (2020)（hit=0.125）
- `rag` What future direction do the authors suggest for RAG? → gold: Extending retrieval-augmented generation to multimodal memory and updating the index without retraining the generator（hit=0.273）

</details>

**跨文献对比（GPT-1/2/3）**

- 维度覆盖 1.0；关键数值命中：{'117M': True, '1.5B': True, '175B': True, '1024': True, '2048': True}

| 维度 | Improving Language Understanding by Generative Pre-Training | Language Models are Unsupervised Multitask Learners | Language Models are Few-Shot Learners |
| --- | --- | --- | --- |
| 模型参数量 | Our model is a 12-layer Transformer decoder with 768 hidden units, 12 attention heads, a context window of 512 tokens, and 117 million parameters. | Accuracy increases monotonically with model size, from 83.1 percent for the 117 million parameter model to 87.65 percent for the 1.5 billion parameter model. | The largest model has 96 layers, a hidden size of 12288, 96 attention heads, and 175 billion parameters, and was trained on 10000 NVIDIA V100 GPUs with a batch size of 3.2 million tokens. |
| 层数与结构 | Our model is a 12-layer Transformer decoder with 768 hidden units, 12 attention heads, a context window of 512 tokens, and 117 million parameters. | Table 1: Model sizes and zero-shot results. | GPT-3 is an autoregressive Transformer language model with 175 billion parameters, 96 layers, a hidden size of 12288, and 96 attention heads. |
| 上下文窗口 | Our model is a 12-layer Transformer decoder with 768 hidden units, 12 attention heads, a context window of 512 tokens, and 117 million parameters. | The context window is 1024 tokens, and the tokenizer is a byte-level byte pair encoding with a vocabulary of 50,257 tokens. | alternating dense and locally banded sparse attention layers, and the context window is 2048 tokens. |
| 训练硬件 | Training takes 30 days on a single machine with 8 GPUs. | The largest model is trained with a batch size of 512 sequences, a learning rate that is warmed up o | The largest model has 96 layers, a hidden size of 12288, 96 attention heads, and 175 billion parameters, and was trained on 10000 NVIDIA V100 GPUs with a batch size of 3.2 million tokens. |

- **模型参数量**：模型参数量：Improving Language Understanding by Generative Pre-Training 为「Our model is a 12-layer Transformer decoder with 768 hidden…」；Language Models are Unsupervised Multitask Learners 为「Accuracy increases monotonically with model size, from 83.1…」；Language Models are Few-Shot Learners 为「The largest model has 96 layers, a hidden size of 12288, 96…」。
- **层数与结构**：层数与结构：Improving Language Understanding by Generative Pre-Training 为「Our model is a 12-layer Transformer decoder with 768 hidden…」；Language Models are Unsupervised Multitask Learners 为「Table 1: Model sizes and zero-shot results.」；La

## 模块三：版式阅读与双语对照

- 样例论文渲染 6 页 / 126 个文本块 / 26 个章节
- `[n]` → References 双向链接 0 条，未解析 0，未被引用条目 12
- 页面图存在 6 / 6

## 模块四：论文检索、追踪与推荐

- 离线镜像检索返回 1 条，Top1：Chain-of-Thought Prompting Elicits Reasoning in Large Language Models
- 推荐 1 条；检索来源：[]；降级：True

## 模块五：引用图谱与综述

- 图谱 14 节点 / 10 边，密度 0.0549，连通分量 6
- 基石论文：['Deep Residual Learning for Image Recognition', 'Language Models are Few-Shot Learners']
- 综述 4 节 / 18519 字符；Future Work 方向 21（待探索 19 / 已解决 2）

## 模块六：写作 Copilot 与数据可视化

- 框架 6 节，References 5 条，全部来自本地库：True
- 引用校验：{'sections': True, 'reference_check': True, 'references_from_library': True, 'citation_markers': True}
- 图表：line，SVG 4087 字节，确定性 True，Caption 含数字 True
- Figure Caption：Figure shows that accuracy increases with epoch (from 0.51 to 0.76). The peak value is 0.76 (accuracy) at epoch = 6.

---

本报告由 `backend/eval/run_eval.py` 生成，所有数字均可在 `backend/eval/report.json` 中追溯到具体论文与问题。
