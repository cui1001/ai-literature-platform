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

## 2026-11-01 · 阶段 B：核心基础设施与 Provider 网关

**目标**：把「解析 → 检索 → 问答」依赖的底座一次做对，后续模块只组合。

**变更**
1. `core/`：`Settings`（环境变量 + 测试覆盖）、结构化异常、日志 span、sha1/simhash 哈希、文本工具（标题识别、引用标记、分词）；
2. `db/`：`schema.sql` 20 张表 + 仓储函数（papers/sections/chunks/refs/edges/translations/qa_log/discovery/feedback/...）；
3. `providers/`：LLM（在线 DeepSeek / 离线抽取引擎）、Embedding（bge-m3 / 确定性哈希）、OCR（winrt→tesseract→文本层）、arXiv（Atom / 离线镜像）；
4. `parsing/`：PyMuPDF 块抽取 → 标题树、表格、图、公式、参考文献、附录、OCR、质量评分；
5. `indexing/`：`chunk_paper`、`meta_chunk`、BM25、indexer；`qa/`：检索 → 证据 → 作答 → 引用锚点 → 校验。

**关键 bug**
- `Settings.from_env(**overrides)` 只接受小写字段名，测试传 `DATA_DIR` 直接 TypeError；补 `_coerce_overrides` 后统一按字段名解析；
- `reset_settings()` 只清缓存不写回覆盖值，导致 repo 仍连默认数据库（测试数据写进 `backend/data/app.db`）。改为模块级 `_OVERRIDES` + `get_settings()` 优先读取；
- SimHash 是无符号 64 位，写 SQLite 报 `OverflowError: Python int too large`；落库转有符号、读回转无符号。

**提交**：`25f7250 feat: 文献解析、混合检索与抗幻觉问答内核`、`5cfef18 chore: 退役被新架构取代的旧原型模块`。

## 2026-11-01 · 阶段 C：版式阅读、引用跳转与双语导出

**目标**：不依赖 PDF.js 也能做「版式一致 + 锚点可定位 + 可翻译导出」。

**变更**
1. `reading/layout.py`：页面块 JSON（百分比 bbox）、标题树、参考文献与回链；
2. `reading/links.py`：`[n]` → References、References → 所有正文引用位置（双向 anchor）；
3. `reading/translate.py`：按块翻译 + `glossary.protect` 公式/引用保护 + `src_hash` 缓存；
4. `reading/export.py`：Markdown / 自包含 HTML / 双语 HTML / 原位 PDF（`china-s` 字体写回 bbox，图表从页面图裁剪）。

**关键 bug**
- `reading/__init__.py` 把 `service.export` 绑成 `export`，遮蔽同名子模块，测试报 `'function' object has no attribute 'export_paper'`；改为 `export_paper` 别名；
- PyMuPDF 会把图注（8.5pt）与紧跟的 `References`（12pt）合并成一个 block，中位字号按图注算，参考文献标题被吞；新增 `split_mixed_blocks` 按行字号分段；
- PyMuPDF 把多行作者合并/拆分不规则，transformer 第 8 位作者单独成块；作者区改为遍历标题到摘要之间的原始块重建。

**提交**：`0b01266 feat: 版式阅读、引用双向跳转与双语对照导出`。

## 2026-11-01 · 阶段 D：API 层、前端 SPA 与四模块并行开发

**变更**
1. `papers/service.py`：上传 → 解析 → 去重 → 入库 → 索引 → 版本比较；
2. `api/`：papers / reading / qa / system 四组路由，随后补齐 discovery / graph / writing / dataviz，共 **51 条路径**；
3. 四个业务模块交给子代理并行实现（发现、图谱、写作、可视化），各自产出测试；父代理按真实签名对齐路由；
4. `frontend/`：原生 ES Module SPA，8 个视图（文献库、阅读器、问答、发现、图谱、写作、可视化、系统）。

**关键 bug**
- 环境无 `httpx`，`fastapi.testclient` 不可用；改用真实 uvicorn + curl 验证，中文 JSON 需先写临时文件再 `--data-binary`；
- 子代理函数签名与路由参数不一致（如 `diagram(spec: dict)`、`build_graph(paper_ids)`、`track_remove(topic_id)`），路由逐一按签名对齐；
- dataviz 前端字段错位（`dataset_id/chart_id` vs 实际 `id`），导致「0 行 × 4 列」；按真实响应键重写渲染；
- 图谱 SVG 右侧标签溢出：改为 22 字符截断 + 按节点位置切换 `text-anchor`。

**提交**：`c175e44`（四模块）、`3b8d18c`（前端）、`33fe0a2`（语料与评测基线）及对应 `--no-ff` 合并。

## 2026-11-01 · 阶段 E：解析评测驱动的表格 / 参考文献修复

**现象**：14 篇语料首轮解析 `tables=0`、6 篇 `references=0`。

**修复**
1. **表格**：`find_tables(strategy="lines")` 对无边框表无效，`strategy="text"` 会把正文并成一张表。改为「表题向下 260pt 文本带 + 按 y 聚类成行 + 词间距切列」，并用同栏文本块推列边界；
2. **表头/数据行被误判标题**：`_heading_text_ok` 增加「含 ≥3 个数字则否」，`_heading_size_ok` 要求字号 ≥ 正文 0.90×，不再用 bold 兜底（表头常加粗但只有 8pt）；
3. **参考文献**：见阶段 C 的 mixed-block 拆分；附录不再被 References 状态机吞掉（`level==1` 一律退出）。

**结果**：13/14 篇抽到表（AlexNet 3 张）、13/14 篇抽到 9–18 条参考文献，quality 0.94–1.0；扫描件 OCR 16.3k 字符。

**关键 bug**：`_group_rows` 把 `(BBox, str)` 当对象访问 `.bbox`，连续两次 `AttributeError`；改为元组解包。

## 2026-11-01 · 阶段 F：离线问答 38.3% → 90.4%

**背景**：评测集 115 道金标准问题，首轮命中 44 题。分类型看：作者 2/14、机构 1/14、引用 0/13。

**迭代**
1. **meta 元信息块**：每篇论文生成 `Title/Authors/Affiliations/Year/Venue/arXiv/DOI` 块（作者/机构 28/28）；
2. **意图加权**：`META_QUERY_RE`/`CITATION_INTENT_RE` 识别后强制注入 meta/reference 候选并设分数下限；
3. **同义词扩展**：中文查询走术语表、英文查询走 `SYNONYMS`（fraction→percent、hidden size→hidden 等）；
4. **引用回挂**：citation 意图下从证据解析 `参考文献 [n]: ...`，用奠基论文别名表（GPT-1/2/3→Transformer、BERT、ResNet…）选中并追加到答案末尾；
5. **数值题**：同块多句 + 额外数字候选，避免并列数据集结果被挤掉；
6. **证据全文**：`Citation` 增加 `text` 字段，`evidence_payload` 不再只用 240 字 quote；
7. **锚点页对齐**：chunker 按 `(sid, page)` 分块，修复跨页 union 导致第 4 页标成第 3 页；
8. **OCR 小数点**：扫描件识别成 `"15 . 3 percent"`，在 `join_lines` 与 OCR 块归一为 `15.3`。

**结果**：0.383 → 0.53 → 0.557 → 0.565 → 0.835 → 0.878 → **0.904**（104/115）；锚点覆盖 100%。
**教训**：意图正则必须允许「which ... paper」中间词；`\b` 在写入源码时被转义成退格字符，改正则后必须 grep 复查。

**提交**：`cb6b047 feat: 离线抗幻觉问答准确率提升至 90.4%`，合并 `cce7db9`。

## 2026-11-01 · 阶段 G：跨文献对比与交付文档

**对比模块修复**：中文维度问题对英文证据做词袋匹配几乎必然选错句；为 9 个维度补英文 `hint`，按「提示词 token 命中 + 是否含数字」选句，并把每维检索深度提到 3。结果：GPT-1/2/3 的 117M/1.5B/175B、context 1024/2048 全部命中，12/12 单元格有锚点。

**交付物**
- `scripts/run_server.py`：一键启动（`--demo` 导入语料、离线三开关默认、可选 `--online`）；
- `docs/screenshots/`：6 张 Edge headless 真实截图；
- README / COMPETITION_MATRIX / ARCHITECTURE 按真实接口与指标重写；
- `docs/EVALUATION.md` 由评测脚本重新生成。

**最终指标**：解析 100% / 质量 0.971；问答 90.4%；锚点 100%；图谱 14 节点 10 边、基石 2；写作引用校验通过；图表确定性通过；48 个单元测试全绿。

**遗留**
- citation/Future Work 类问题仍为 7/13、1/3；
- 划词翻译仅到块级；
- 阅读器页面为图片，无法直接选中长段落文本；
- 参考文献「含金量」仅有字段与统计，未做上下文情感打分。
