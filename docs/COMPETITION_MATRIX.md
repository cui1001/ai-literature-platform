# 命题对照矩阵

> 覆盖六大命题模块与三条基本要求。每条要求给出：实现位置、可调用的接口、演示路径、验证方式。
> 「验证」列中的命令均可在无网络环境执行。

## 0. 基本要求

| 要求 | 实现 | 演示 | 验证 |
| --- | --- | --- | --- |
| 输入素材可审查 | PyMuPDF 逐块抽取，解析产物落盘 JSON；页级渲染 PNG 保留原版式；解析质量分与 OCR 标记写入 `papers.parse_quality` | 文献详情页「解析产物」抽屉可直接下钻 JSON 原文与页面截图比对 | `GET /api/papers/{id}/artifact`；`backend/tests/test_parsing.py` |
| 输出成果可验证 | 问答/综述/大纲全部返回 `citations[]`（paper_id + page + bbox + 原文片段）；图表返回 SVG 与生成脚本 | 点击任意引用角标跳转到页面高亮区域 | `POST /api/qa/verify` 复核 claim；前端高亮框与 PDF 原页对照 |
| 系统运行闭环 | FastAPI 单进程同时托管 REST 与 SPA；一条 `scripts/run_server.py --demo` 完成建库→灌语料→前端可用 | 浏览器完成 导入→解析→问答→阅读→图谱→综述→写作→可视化 | `python scripts/run_server.py --demo` 后访问 `http://127.0.0.1:8000` |

## 1. 模块一：PDF 管理与细粒度解析

| 子要求 | 实现 | 接口 | 演示 | 验证 |
| --- | --- | --- | --- | --- |
| 单篇/批量上传 | raw body 上传，异步解析任务 | `POST /api/papers/upload?filename=` | 文献库页拖拽多个 PDF | 上传后列表出现，`pages>0` |
| 基于内容的语义去重 | `sha256` 精确去重 + 标题规范化 + 正文 SimHash 近似去重；`version_group` 归并版本族 | `POST /api/papers/dedup` | 导入 `1706.03762v1` 与 `v7`，提示「同族版本，建议保留 v7」 | 返回 `duplicates[]` 含相似度与建议；`tests/test_dedup.py` |
| 建议保留最新版 | 版本比较：arXiv 版本号 > 发布日期 > 页数 | 同上 | 弹窗中一键「保留最新并归档旧版」 | `is_latest` 唯一性断言 |
| 扫描件 OCR | 文本密度低于阈值触发 OCR：PyMuPDF 内嵌 OCR → Tesseract → 文本层回退，并记录 `ocr_engine` | 解析流水线 | `fixtures/corpus/scan_*.pdf` | `ocr_used=true` 且抽取文本非空；`tests/test_ocr.py` |
| 长篇书籍 | 目录页识别 + 跨页标题续接 + 段落去页眉页脚 | 同上 | `fixtures/corpus/book_*.pdf`（多页章节） | 标题树层级 ≥3、页数 > 20 |
| 标题/作者/机构/摘要 | 版面前区启发式 + 字体统计 + 正则 | `GET /api/papers/{id}` | 详情页元数据卡 | 与解析 JSON 字段一致 |
| 一/二/三级标题树 | 字号 + 加粗 + 编号模式（`1.` `1.1` `1.1.1`）+ 目录对齐 | `GET /api/papers/{id}/outline` | 阅读器左栏树状导航 | `len(sections)>0` 且 level∈{1,2,3} |
| 表格 | `page.find_tables()` + 表题关联 + 单元格二维数组 | `GET /api/papers/{id}/tables` | 阅读器表格浮层 | 单元格数与页内表格一致 |
| 图 | 图注正则 + 邻接图像块裁剪 | `GET /api/papers/{id}/figures` | 图片墙 + 点击定位 | 图注编号连续 |
| LaTeX 公式 | 行内/行间公式启发式（$$、Unicode 数学符号、居中孤行） + LaTeX 化 | `GET /api/papers/{id}/formulas` | 公式列表与原文对照 | 公式块 bbox 落在页面内 |
| 参考文献 | 末尾 References 区段切分 + 编号/悬挂缩进解析 + 字段抽取 | `GET /api/papers/{id}/references` | 参考文献面板 | 条目数与原文编号一致 |
| 附录 | `Appendix`/`A.` 检测，单列于标题树末枝 | 同上 | 标题树展示附录 | `sections[].appendix=true` |

## 2. 模块二：抗幻觉知识问答

| 子要求 | 实现 | 接口 | 演示 | 验证 |
| --- | --- | --- | --- | --- |
| 单篇精准问答 | 混合检索（BM25 ⊕ 向量 RRF）→ 证据块 → 抽取式作答 → 逐句锚定 | `POST /api/qa/ask` | 问「β1/β2 取值」「训练用了几块 GPU」 | 每个 citation 的 bbox 在对应页 |
| 原文出处/位置锚点 | citation = {paper, section, page, bbox, snippet, score} | 同上 | 点击角标 → 阅读器高亮 | bbox 与页面文本层命中 |
| 多文跨文献对比 | 维度化对比：抽取各文对应证据 → 生成对照表（不臆造） | `POST /api/qa/compare` | BERT vs Transformer Encoder / GPT-1·2·3 参数演化 | 每格含来源锚点，缺失格显式标注「未提及」 |
| 抗幻觉约束 | 无证据不生成；数值/专名必须逐字命中证据；`verify` 二次校验 | `POST /api/qa/verify` | 输入伪造 claim，返回 unsupported | `verified=false` 且给出冲突证据 |

## 3. 模块三：版式感知阅读与双向联动翻译

| 子要求 | 实现 | 接口 | 演示 | 验证 |
| --- | --- | --- | --- | --- |
| 双栏/公式/表格原生渲染 | 服务端 150DPI 页面 + 块级 bbox 覆盖层（双栏阅读顺序按 x 聚类） | `GET /api/papers/{id}/reader` | 打开双栏论文 | 覆盖框与文字对齐误差 < 3px |
| 标题树导航 | 左栏树 + 滚动联动 | 同上 | 点击 3.2 节跳页 | 滚动位置与章节页一致 |
| 引用双向跳转 | `[11]` 标记 → References 第 11 条；References 条目 → 所有引用它的正文位置 | `GET /api/papers/{id}/references` | 点击角标往返 | 往返后回到原位置（history 栈） |
| 划词/悬停翻译 | 选区 → 翻译 API（缓存按源哈希） | `POST /api/papers/{id}/translate` | 选中句子 → 浮层译文 | 同一选区二次请求命中缓存 |
| 整篇翻译 | 按块翻译，保留公式/代码原样（占位符保护） | 同上 | 阅读器切换「中文」 | 公式/代码与原文完全一致 |
| 中英对比同步滚动 | 双栏：原文页 ↔ 译文块，按块锚点双向同步 | 前端 `reader.js` | 拖动左栏右栏跟随 | 对应块顶部对齐误差 < 24px |

## 4. 模块四：论文自动检索、追踪与推荐

| 子要求 | 实现 | 接口 | 演示 | 验证 |
| --- | --- | --- | --- | --- |
| 按名称/参考文献抓取入库 | arXiv Atom API（在线）/ 离线镜像；参考文献逐条检索匹配 | `POST /api/discovery/search`、`POST /api/discovery/import` | 搜 "Attention Is All You Need" → 导入 | 导入后进入文献库并可解析 |
| 引用含金量筛选 | 评分 = 引用位置权重(方法/实验>相关工作) + 上下文情感 + 引用句是否具体描述 | `GET /api/discovery/references/{id}/value` | 参考文献列表显示「高价值/应付式」标签 | 阈值分组与人工标注样例行一致 |
| Sickle 式定期追踪 | `ArxivTracker` 定时刷新（后台线程，可手动触发），记录 last_run | `GET /api/discovery/track`、`POST /api/discovery/track/refresh` | 追踪页点「立即刷新」 | `track_state.last_run` 更新 |
| 本地库相关性推荐 | 用户库向量质心 × 候选向量 + 主题重合度 | `GET /api/discovery/recommend?user_id=` | 推荐卡片含匹配度 | 无用户库时退化为热门推荐并标注 |
| GitHub 代码优先 | 元数据 `comment`/摘要中含 github 链接则加分并展示徽标 | 同上 | 卡片右上角 `code` 徽标 | 链接可点击 |
| 推荐理由 | 模板化理由：与库中哪几篇相近、命中哪些关键词 | 同上 | 卡片下方理由行 | 理由中的论文 ID 真实存在 |
| 点赞/踩与多用户 | `feedback` 表按用户聚合，协同过滤式加权（用户相似度 × 共同喜好） | `POST /api/discovery/feedback` | 切换用户看推荐差异 | 反馈后推荐排序变化 |

## 5. 模块五：局域引用图谱挖掘与学术综述生成

| 子要求 | 实现 | 接口 | 演示 | 验证 |
| --- | --- | --- | --- | --- |
| 引用关系解析 | 对库内 20 篇提取参考文献 → 标题/作者/年份/DOI/arXiv 匹配本地论文 | `POST /api/graph/build` | 选中 20 篇 → 生成图谱 | 边数与人工核对一致（容差 1） |
| 可视化图谱 | 服务端固定种子布局 + 前端 SVG 交互（拖拽/缩放/高亮邻域） | `GET /api/graph/{id}` | 图谱页 | 刷新后坐标不变（确定性） |
| 基石 / 边缘论文 | PageRank（基石）、度中心性、betweenness（桥接）、叶子/低入度（边缘衍生）；输出社区 | 同上 `metrics` | 节点大小 = PageRank，颜色 = 社区 | 指标可下载 CSV |
| 自动综述 | 按社区聚类 + 时间线 + 每篇贡献句（取自 Abstract 与结论）拼接为带引用的领域短综述 | `POST /api/graph/survey` | 综述页生成 Markdown | 每个结论句带 `[paper_id]` 锚点 |
| Future Work 提炼 | 抽取各文 Future Work 段落 → 语义聚类 → 标注「已解决/仍开放」（后续论文中是否出现对应主题） | `POST /api/graph/future-work` | 前沿探索面板 | 每条方向给出支持论文与被解决证据 |

## 6. 模块六：学术写作辅助与数据可视化 Copilot

| 子要求 | 实现 | 接口 | 演示 | 验证 |
| --- | --- | --- | --- | --- |
| 无幻觉框架 | 仅使用本地库检索到的文献生成 Abstract/Intro/Related Work，引用逐条可点 | `POST /api/writing/outline` | 输入 idea → 生成框架 | 所有 `\cite{}` 均能在库中找到 |
| 拓扑图脚本 | 依据引用图输出 TikZ / Graphviz / Mermaid / Matplotlib 四种源码 | `POST /api/writing/diagram` | 切换四种格式并预览 | 源码可独立编译/渲染（Mermaid 前端预览） |
| CSV 数据可视化 | 类型推断 + 聚合 + 自研 SVG 渲染（折线/柱状/表格/三维雷达） | `POST /api/dataviz/chart` | 上传 CSV → 选图型 | SVG 数值与 CSV 聚合一致 |
| Figure Caption | 趋势检测（斜率/极值/交叉点）→ 学术规范句式 | 同上 `caption` | 图下方 caption | caption 中的数值来自真实统计 |
| 学术翻译 | 中→英学术风格转换（在线 LLM / 离线术语表 + 句式模板），术语一致性校验 | `POST /api/qa/translate` 或 `/writing/translate` | 输入中文摘要 → 英文 | 术语表命中率报告 |

## 7. 评测与可复现性

| 项目 | 指标 | 脚本 |
| --- | --- | --- |
| 解析质量 | 字段召回率、标题树准确率、表格/公式/引用条数 | `backend/eval/run_eval.py` |
| 检索质量 | Recall@5、MRR（基于内置 QA 标注集） | 同上 |
| 问答可定位率 | citation bbox 命中率 | 同上 |
| 确定性 | 两次全流水线产物哈希一致 | 同上 `--determinism` |
