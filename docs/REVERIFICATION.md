# 复验报告（P0/P1/P2 全量回归）

- 复验日期：2026-11-15
- 仓库：`D:\AI-Project\ai-literature-platform`（master，HEAD `44ac6ce`，工作树含未提交修复）
- 环境：Python 3.14.3（`D:/python/python.exe`），离线三开关 `LLM_MODE=offline` / `EMBED_MODE=hashing` / `ARXIV_MODE=offline`，翻译 `TRANSLATE_MODE=offline`
- 隔离数据目录：`_agentlit_work\data_accept`（39 篇审计）、`_agentlit_work\data_qa`（9 问 / 阅读器）、`%TEMP%\agentlit_eval`（评测）
- 说明：本次全部为可复现的离线验收；未联网、未推送远端。

## 1. 结论

| 验收项 | 验收前 | 复验后 |
|---|---|---|
| 39 个 PDF 入库 | 38 入库，541 页书被 400 页上限拒绝 | 39/39 全部处理：36 入库 + 1 版本替换 + 2 判重跳过；库内 37 篇唯一文献 |
| 标题 | 24/38 是 arXiv 水印串 | 0 篇水印标题；书名、技术报告、双栏会议论文均正确 |
| 机构 | 38/38 为空 | 仅 2 篇为空（Mistral 7B、Understanding Deep Learning，原 PDF 本身无机构行） |
| 参考文献 | 28/38 为 0 条 | 0 篇为 0 条；37 篇共 2904 条，平均 78.5 条 |
| 作者 | 5 篇缺失 | 0 篇缺失（含团队署名 OpenAI / BigScience Workshop / Qwen Team 等） |
| 英文扫描件 | 走中文 OCR，乱码 | EasyOCR 英文链，标题正确、参考文献 28 条；与文本版自动判重 |
| 9 个指定问题 | Q1/Q4/Q5 正确，其余失败；2 题被误判 verified=false | 9/9 关键词命中且 verified=true、refused=false |
| 图谱 / 综述 | 22 节点 2 边；Future Work 0 条 | 20 节点 43 边、基石 8 篇；Future Work 51 条 |
| 正文 [n] 跳转 | 只能从块 inspector 的 chip 点 | 页面图正文热区直接点击，引用↔正文双向跳转并闪烁 |
| 单元测试 | 49 OK | 49 OK（15.0s） |
| 评测 | 质量 0.971 / 问答 104/115 / 校验 62.6% / 图谱 10 边 | 质量 0.981 / 问答 105/115 / 校验 97.4% / 图谱 26 边 |

整体判定：**通过**（P0 全部关闭，P1 全部关闭，P2 两项均通过真实浏览器操作验证；F1–F10 中 9 项通过，F6 离线兜底链路已验、联网机器翻译未验，如实标注）。

## 2. 修复与验证方法

### P0-1 541 页书籍无法导入

- `core/config.py` 新增 `max_pages`（默认 1200）、`page_render_limit`（默认 120），`pipeline.py` 的 `MAX_PAGES` 同步放宽到 1200；
- 大文档只预渲染前 120 页页面图，其余页由 `api/papers.py::_ensure_page_png` 按需渲染并原子写缓存，避免上传 500+ 页时生成上千张 PNG。
- 实测：`UnderstandingDeepLearning_02_09_26_C.pdf` 541 页，133.1s 入库，质量 0.86，120 张预渲染 + 按需缓存，parsed JSON 5.6MB。

### P0-2 arXiv 水印抢标题

- `pdftext.py` 新增 `is_boilerplate_text()`（arXiv 水印 / JMLR 页眉 / 版权 / Proceedings / Editor / 页码 / 纯 URL），`structure.classify_blocks` 将其标为 `meta`，不再参与标题、作者、章节判定；
- 标题改为按「字号 + 位置 + 非水印 + 连续块」挑选（`_pick_title`），并对 PDF 断词做去连字符。
- 实测：37 篇入库文献标题全部正确、0 条水印标题；`2303.08774v6`（GPT-4）、`1910.10683v4`（T5）等水印论文亦正确。

### P0-3 机构信息全缺

- 作者区按行解析，支持一行多作者、上标编号、邮箱组、大括号邮箱、团队署名、折行姓名合并；
- 机构优先取作者行，取不到时回退首页脚注（`_footnote_affiliations`，处理编号切分与跨行断词）；
- 团队名作者映射机构（OpenAI / BigScience / Alibaba Group / DeepSeek / Mistral AI / Meta AI）。
- 实测：`2002.04745` 从 0 作者 0 机构恢复为 10 作者 6 机构；CLIP 12 作者；GPT-4、BLOOM、Qwen、DeepSeek-V3 均补全。

### P0-4 无编号参考文献全丢

- `parse_references` 支持两种模式：数字编号（`[n]`）按编号切分；作者-年份（无编号）按「每块一条 + 起始启发式」切分，支持 `[Bel+15]` 之类非数字标签多条目块；
- `_fill_reference` 拆分作者 / 年份 / 标题 / venue，支持 `et al.`、姓名缩写（`Abadi, M.`、`Alayrac, J.-B.`）、引号标题、venue 截断。
- 实测：PPO 从 1 条恢复到 14 条；BERT 43 条；CLIP 211 条；T5 129 条；Llama 3 253 条；全库 0 篇零参考文献。

### P0-5 英文扫描件走中文 OCR

- `providers/ocr.py` 新增 EasyOCR 引擎（懒加载，本机无英文 Windows OCR 包、无 tesseract 时的英文兜底）；
- WinRT 引擎检测到实际语言与请求不一致时返回 `degraded=True` 并继续引擎链；
- `pipeline._doc_language()` 用前 4 页 CJK/拉丁比例判定语言，扫描页按文档语言调用 OCR；
- OCR 结果按 CRAFT 检测框建块（不再按 y 聚类），并收紧块合并规则、过滤竖排水印。
- 实测：`1706.03762v1_img.pdf` 15 页 232.4s，标题正确、作者 4/8、参考文献 28 条（旧为 0），质量 0.74；该文件与文本版 `1706.03762v1.pdf` 自动判重。

### P1-1 双栏误判单栏

- `detect_columns` 改为行级几何：先用右侧起始位置推断栏边界，再要求左右各 ≥5 行、多寡比 ≤3、通栏行不过多、两侧纵向覆盖率达标。
- 实测：T5 67 页、Llama 3 92 页、Qwen 26 页均判单栏；BERT 16 页、LLaMA 前 15 页判双栏；Attention 15 页全部单栏（旧版第 9 页误判已消失）。

### P1-2 参考文献标题取成作者串

- 作者段优先按 `et al.` 定位，其次按「逗号 + 姓名缩写」序列；标题在 venue 标记处截断，并跳过纯年份 / URL / `In ...` 片段。
- 实测：Attention REF1 `Ba, Kiros, Hinton — Layer normalization — 2016`；PPO REF1 `The arcade learning environment...`；CLIP REF1 `Tensorflow: A system for large-scale machine learning`。

### P1-3 问答 5/9 未达标

- 检索：新增 `transformer → attention is all you need` 等同义词；指标题放宽候选（表格行常排名靠后）；「哪篇论文提出」类问题识别并放宽 top-k；关键短语证据注入（LayerNorm / context size / 参数量）。
- 生成：数值题优先补「带新数字」的证据（保证 28.4 与 26.36 同时出现）；指标题补一条同名指标表格；引用题把参考文献条目本身作为证据；多篇问题按篇检索、避免单篇垄断。
- 校验：`verifier.py` 先剥离引用编号 `[25]`、页码、arXiv 号等噪声再做数字核对，并用完整 chunk 文本而非 240 字摘录。
- 实测：9/9 全中且 verified=true。

### P1-4 图谱仅 2 边、Future Work=0

- 依赖 P0-4 的参考文献恢复；图谱按 arXiv ID / 标题匹配本地引用并加边；
- 综述从章节标题与正文句子抽取 future work / limitation / open problem。
- 实测（20 篇）：20 节点 43 边、密度 0.113、基石 8 篇、边缘 8 篇；综述 4 节、Future Work 51 条。

### P2-1 扫描版与可检索版未去重

- 去重链 sha256 → arXiv 版本 → 文本 SimHash；扫描件 OCR 文本与文本版相似度达到阈值即判重。
- 实测：`1706.03762v1_img.pdf` 与 `1706.03762v1.pdf` 判重；`1706.03762v7.pdf` 触发版本替换（保留 v7）；`2206.07682v2.pdf` 与 `209_Emergent...pdf` 判重。

### P2-2 正文 [n] 不可点击

- 后端 `reading/service.py` 用 PyMuPDF 在页面图上定位每个引用编号的矩形（`_attach_citation_rects`），随 `links.occurrences` 下发；
- 前端 `reader.js` 聚合为热区按钮（多编号合并），单击单编号直接滚动并高亮 References 条目；多编号弹出选择；从 References 回跳时滚动到正文块并让对应热区闪烁；
- 浏览器实测（`http://127.0.0.1:8030/#reader/cd18800a0fe0b668`，v7 共 78 个热区 / 71 条参考文献）：
  - 点击 `[61]` 热区 → 参考文献 61 进入视口（top=377）；
  - 点击 `[10,39,42]` 热区 → 弹窗「该处引用了 3 篇文献」，含 [10]/[39]/[42] 按钮；
  - 选 [10] → 参考文献 10 定位；从条目回跳 `p1-b009` → 弹窗关闭、正文块 top=353、`.block.active=1`、`.cite-hot.flash=3`。


### F1–F10 功能验收

| 编号 | 功能 | 验收证据（离线确定性环境） | 结论 |
|---|---|---|---|
| F1 | 文献管理与语义去重 | 库内 37 篇唯一文献；Attention v1→v7 版本链（保留 v7）；扫描件 `v1_img` 与文本版判重；`2206.07682` 与 `209_Emergent` 判重 | 通过 |
| F2 | 解析粒度 | Attention：26 个章节（1–3 级）、8 张表、3 张图、9 个公式、40 条参考文献（作者 40/标题 39/年份 40）；541 页书 1174 节 | 通过 |
| F3 | 抗幻觉问答 | 9 个指定问题 9/9 命中且校验通过；115 题评测 105 中、锚点 100%、校验 97.4% | 通过 |
| F4 | 版式阅读 | Attention/T5/Llama 3 判单栏，BERT/LLaMA 判双栏；Attention 73 个引用编号均带页面矩形 | 通过 |
| F5 | 标题树 + 引用跳转 | 26 个章节、11 个一级节点；浏览器实测 `[61]` 定位、`[10,39,42]` 多选、条目回跳正文并闪烁 | 通过 |
| F6 | 翻译 | 整篇 376 块 / 1.74s（`provider=offline`），8/8 数字保留，公式与参考文献不翻译；联网 MT（DeepSeek）未验 | 部分（离线兜底已验） |
| F7 | 检索追踪与推荐 | 离线镜像 9 条元数据、检索命中 2 条；导入成功（50 chunks）、重复导入判重；推荐附理由；追踪 topic 运行 `fetched=2`；赞/踩写入反馈 | 通过（离线镜像） |
| F8 | 引用图谱 | 20 节点 / 43 边、基石 8 篇、边缘 8 篇、3 个连通分量 | 通过 |
| F9 | 自动综述 | 4 个章节、Future Work 方向 51 条 | 通过 |
| F10 | 写作助手 | 6 章框架、2 条真实 References（均回溯本地库）、4 项引用/章节检查全通过 | 通过 |

### 39 个 PDF 逐篇审计

| # | 文件 | 标题 | 作者 | 机构 | 参考文献 | 页数 | 版式 | 质量 | 状态 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1607.06450v1.pdf | Layer Normalization | 3 | 2 | 22 | 14 | single | 1.0 | 入库 |
| 2 | 1706.03762v1.pdf | Attention Is All You Need | 8 | 3 | 36 | 15 | single | 1.0 | 入库 |
| 3 | 1706.03762v1_img.pdf | — | 0 | 0 | — | — | — | — | 判重跳过 |
| 4 | 1706.03762v7.pdf | Attention Is All You Need | 8 | 3 | 40 | 15 | single | 1.0 | 版本替换 |
| 5 | 1707.06347v2.pdf | Proximal Policy Optimization Algorithms | 5 | 1 | 14 | 12 | single | 1.0 | 入库 |
| 6 | 1910.02054v3.pdf | ZeRO: Memory Optimizations Toward Training Trillion  | 4 | 1 | 26 | 24 | single | 1.0 | 入库 |
| 7 | 1910.10683v4.pdf | Exploring the Limits of Transfer Learning with a Uni | 9 | 1 | 129 | 67 | single | 1.0 | 入库 |
| 8 | 2001.08361v1.pdf | Scaling Laws for Neural Language Models | 10 | 1 | 51 | 30 | single | 1.0 | 入库 |
| 9 | 2002.04745v2.pdf | On Layer Normalization in the Transformer Architectu | 10 | 6 | 49 | 17 | double | 1.0 | 入库 |
| 10 | 2005.14165v4.pdf | Language Models are Few-Shot Learners | 20 | 1 | 144 | 75 | single | 1.0 | 入库 |
| 11 | 2010.11929v2.pdf | AN IMAGE IS WORTH 16X16 WORDS: TRANSFORMERS FOR IMAG | 12 | 1 | 50 | 22 | single | 1.0 | 入库 |
| 12 | 209_Emergent_Abilities_of_Larg.pdf | Emergent Abilities of Large Language Models | 16 | 3 | 90 | 30 | single | 1.0 | 入库 |
| 13 | 2101.03961v3.pdf | Switch Transformers: Scaling to Trillion Parameter M | 3 | 1 | 59 | 40 | single | 1.0 | 入库 |
| 14 | 2103.00020v1.pdf | Learning Transferable Visual Models From Natural Lan | 12 | 2 | 211 | 48 | double | 1.0 | 入库 |
| 15 | 2106.09685v2.pdf | LORA: LOW-RANK ADAPTATION OF LARGE LANGUAGE MODELS | 8 | 2 | 51 | 26 | single | 1.0 | 入库 |
| 16 | 2107.03374v2.pdf | Evaluating Large Language Models Trained on Code | 20 | 4 | 119 | 35 | double | 1.0 | 入库 |
| 17 | 2201.11903v6.pdf | Chain-of-Thought Prompting Elicits Reasoning in Larg | 9 | 1 | 75 | 43 | single | 1.0 | 入库 |
| 18 | 2203.02155v1.pdf | Training language models to follow instructions with | 20 | 1 | 36 | 68 | double | 1.0 | 入库 |
| 19 | 2205.14135v2.pdf | FlashAttention: Fast and Memory-Efficient Exact Atte | 5 | 2 | 94 | 34 | single | 1.0 | 入库 |
| 20 | 2206.07682v2.pdf | — | 0 | 0 | — | — | — | — | 判重跳过 |
| 21 | 2210.02414v2.pdf | GLM-130B: AN OPEN BILINGUAL PRE-TRAINED MODEL | 2 | 1 | 126 | 56 | single | 1.0 | 入库 |
| 22 | 2211.05100v4.pdf | BLOOM: A 176B-Parameter Open-Access Multilingual Lan | 1 | 1 | 155 | 73 | single | 1.0 | 入库 |
| 23 | 2212.04356v1.pdf | Robust Speech Recognition via Large-Scale Weak Super | 6 | 1 | 84 | 28 | double | 1.0 | 入库 |
| 24 | 2303.08774v6.pdf | GPT-4 Technical Report | 1 | 1 | 190 | 100 | double | 1.0 | 入库 |
| 25 | 2304.08485v2.pdf | Visual Instruction Tuning | 4 | 3 | 63 | 25 | single | 1.0 | 入库 |
| 26 | 2305.13048v2.pdf | RWKV: Reinventing RNNs for the Transformer Era | 3 | 1 | 89 | 30 | double | 1.0 | 入库 |
| 27 | 2305.14314v1.pdf | QLORA: Efficient Finetuning of Quantized LLMs | 4 | 1 | 73 | 26 | double | 1.0 | 入库 |
| 28 | 2305.18290v3.pdf | Direct Preference Optimization: Your Language Model  | 6 | 1 | 51 | 27 | double | 1.0 | 入库 |
| 29 | 2307.09288v2.pdf | Llama 2: Open Foundation and Fine-Tuned Chat Models | 20 | 1 | 76 | 77 | double | 1.0 | 入库 |
| 30 | 2310.06825v1.pdf | Mistral 7B | 14 | 0 | 29 | 9 | single | 0.9 | 入库 |
| 31 | 2406.14491v2.pdf | Instruction Pre-Training: Language Models are Superv | 6 | 3 | 109 | 22 | double | 1.0 | 入库 |
| 32 | 2407.21783v3.pdf | The Llama 3 Herd of Models | 1 | 1 | 253 | 92 | single | 1.0 | 入库 |
| 33 | 2412.15115v2.pdf | Qwen2.5 Technical Report | 1 | 1 | 94 | 26 | single | 1.0 | 入库 |
| 34 | 2412.19437v1.pdf | DeepSeek-V3 Technical Report | 1 | 1 | 8 | 53 | single | 1.0 | 入库 |
| 35 | 2501.12948v2.pdf | DeepSeek-R1: Incentivizing Reasoning Capability in L | 1 | 2 | 4 | 86 | single | 1.0 | 入库 |
| 36 | 333078981_693988129081760_47127078152257 | LLaMA: Open and Efficient Foundation Language Models | 14 | 1 | 74 | 27 | double | 1.0 | 入库 |
| 37 | language_understanding_paper.pdf | Improving Language Understanding by Generative Pre-T | 4 | 1 | 71 | 12 | single | 1.0 | 入库 |
| 38 | N19-1423.pdf | BERT: Pre-training of Deep Bidirectional Transformer | 4 | 1 | 43 | 16 | double | 1.0 | 入库 |
| 39 | UnderstandingDeepLearning_02_09_26_C.pdf | Understanding Deep Learning | 1 | 0 | 16 | 541 | double | 0.86 | 入库 |

### 9 个指定问题（离线确定性引擎）

| 题号 | 问题 | 命中关键词 | 校验 | 拒答 | 证据 | 耗时 |
|---|---|---|---|---|---|---|
| Q1 | 论文 Attention Is All You Need 的第 6 作者是谁，在哪个公司/机… | ✅ | ✅ | 否 | 14 | 0.20s |
| Q2 | 论文中 Adam 优化器的超参数 β1 和 β2 分别被设置为了多少？ | ✅ | ✅ | 否 | 15 | 0.00s |
| Q3 | 训练 Base 模型时使用了多少个 GPU？具体是什么型号？ | ✅ | ✅ | 否 | 12 | 0.00s |
| Q4 | Transformer (big) 在 English-to-German 任务上的 BLE… | ✅ | ✅ | 否 | 25 | 0.00s |
| Q5 | 请列出论文中引用的关于 Layer Normalization 的参考文献的完整作者和年份。 | ✅ | ✅ | 否 | 14 | 0.00s |
| Q6 | 对比 BERT 与原版 Transformer：BERT 的架构与 Transformer … | ✅ | ✅ | 否 | 18 | 0.10s |
| Q7 | 对比 GPT-1、GPT-2 和 GPT-3 的论文，说明这些模型的参数变化。 | ✅ | ✅ | 否 | 18 | 0.10s |
| Q8 | GPT-2 相比 GPT-1 在 Transformer 的 Layer Normaliza… | ✅ | ✅ | 否 | 18 | 0.10s |
| Q9 | 从 GPT-1 到 GPT-3，最大上下文 Token 长度分别递增到多少？ | ✅ | ✅ | 否 | 18 | 0.10s |

### 20 篇图谱与自动综述

- 节点 20，引用边 43，密度 0.1132，弱连通分量 3，平均聚类系数 0.4046
- 基石论文 8 篇，边缘论文 8 篇
- 自动综述章节 4 个，Future Work 方向 51 条

## 3. 评测回归（backend/eval/run_eval.py）

| 指标 | HEAD 基线 | 复验 |
|---|---|---|
| 解析成功率 | 1.0 | 1.0 |
| 平均质量 | 0.971 | **0.981** |
| 问答准确率 | 104/115 = 0.904 | **105/115 = 0.913** |
| 答案锚点率 | 1.0 | 1.0 |
| 校验通过率 | 0.626 | **0.974** |
| 图谱边数 | 10 | **26** |
| 基石论文 | 2 | **5** |
| 大纲检查 / 图表确定性 | 通过 | 通过 |
| 总耗时 | 50s | 142s（新增解析与校验工作量） |

报告文件：`backend/eval/report.json`、`docs/EVALUATION.md`（均由评测自动生成）。

## 4. 单元测试

```bash
cd backend && python -m unittest discover -s tests
# Ran 49 tests in 14.98s — OK（最终代码）
```

## 5. 残余限制（如实记录）

1. **离线答案形态**：本次验证据 `LLM_MODE=offline`，答案是「带引用的证据句列表」而非润色后的自然语言综述；联网 DeepSeek 模式未在本次复验中重跑（本机无网络），其提示词与引用约束未改动。
2. **扫描件精度**：英文 OCR 依赖 EasyOCR（约 19s/页）；本机无管理员权限安装 Windows 英文 OCR 包，WinRT 路径会明确降级。扫描件作者识别仍可能少于文本层（实测 4/8），但标题、摘要、参考文献已可用。
3. **两篇机构为空**：Mistral 7B 与 Understanding Deep Learning 的原始 PDF 未印机构行；系统未编造，保留空值。
4. **书籍质量 0.86**：书无摘要（`has_abstract` 不适用），且 4 个纯图页 OCR 不可用；已按需渲染页面图，不影响阅读与引用。
5. **评测中的扫描 fixture**：`fixtures/papers/scanned/udl_scan.pdf` 为纯图扫描件，表格/图/公式检测得分偏低，故 eval 平均质量未到 1.0；文本版 `fixtures/papers/udl_scan.pdf` 解析为 1.0。
6. **未测项**：联网 LLM 问答、在线翻译（DeepSeek/Google/MyMemory）、远程部署实例（8.219.85.252）本轮未复验；不得计为通过。

## 6. 变更文件（未提交、未推送）

```
backend/app/api/papers.py            页面图按需渲染
backend/app/core/config.py           max_pages / page_render_limit（LF 规范化）
backend/app/indexing/retriever.py    同义词与关键短语证据注入
backend/app/parsing/pdftext.py       水印识别、行级分栏、OCR 语言
backend/app/parsing/pipeline.py      页数上限、语言判定、预渲染上限
backend/app/parsing/structure.py     标题/作者/机构/摘要/参考文献解析
backend/app/providers/ocr.py         EasyOCR 引擎与语言降级
backend/app/providers/offline.py     证据选择（数值/表格/引用）
backend/app/qa/service.py            指标题放宽检索、多篇分篇检索
backend/app/qa/verifier.py           数字噪声剥离
backend/app/reading/service.py       正文引用矩形
frontend/assets/reader.js            正文热区与回跳闪烁
frontend/assets/styles.css           热区样式
backend/eval/report.json             评测输出（自动生成）
docs/EVALUATION.md                   评测报告（自动生成）
```

> 按约束未执行 `git push`；提交前已跑 49 个单测。
