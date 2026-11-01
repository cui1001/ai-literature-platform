# 命题对照矩阵

> 覆盖六大命题模块与三条基本要求，逐条给出：实现位置、真实接口、演示路径、验证方式。
> 状态标记：✅ 已实现并可演示；◐ 部分实现（注明边界）。所有命令均可在**无网络**环境执行。

## 0. 基本要求

| 要求 | 实现 | 演示 | 验证 |
| --- | --- | --- | --- |
| 输入素材可审查 | PyMuPDF 逐块抽取，解析结果落盘 `data/parsed/{id}.json`；每页渲染 150 DPI PNG；`parse_quality` 九项检查写入 `papers.parse_quality` | 文献库 →「阅读」→ 左栏解析质量徽章；`GET /api/papers/{id}/parsed` 查看原始 JSON | `python -m eval.validate_corpus` 输出逐篇 blocks/tables/refs/quality |
| 输出成果可验证 | 问答与对比的每条引用含 `paper_id/page/bbox/quote`；图表返回 SVG + `caption` + matplotlib 脚本；论述标注 provider/degraded | 点击 `[E1]` 角标跳转阅读器对应页与 bbox；图表下方展示生成脚本 | `POST /api/qa/ask` 响应中 `citations[].anchor`；`python -m unittest tests.test_indexing_qa` |
| 系统运行闭环 | FastAPI 单进程同时托管 REST 与 SPA；`scripts/run_server.py --demo` 完成建库→导入 15 个 PDF→服务启动 | 浏览器完成 导入→解析→问答→阅读→图谱→综述→写作→可视化 | `python scripts/run_server.py --demo` 后 `/api/health` 200、`/` 200 |

## 1. 模块一：PDF 文件管理与细粒度解析

| 子要求 | 状态 | 实现 | 接口 | 验证 |
| --- | --- | --- | --- | --- |
| 单篇 / 批量上传 | ✅ | raw body 上传（规避 python-multipart）逐文件解析入库；批量走 base64 JSON，最多 30 个 | `POST /api/papers/upload?filename=`、`POST /api/papers/batch` | 上传后 `pages>0`；`backend/tests` 无多部件依赖 |
| 语义去重（v1 vs v7） | ✅ | sha256 精确判重 + arXiv 版本族 + 标题相似度 + 正文 SimHash；返回 `duplicate` 与 `comparison`，建议保留最新 | `POST /api/papers/upload?force=false`（在导入流程内） | 先传 v1 再传 v7：`status=duplicate`、`verdict.kind=version`、`action=replace-existing`；`python -m eval.run_eval` 的 dedupe 段 |
| 扫描件 OCR | ✅ | 文本层字符数 < `OCR_TEXT_THRESHOLD=40` 触发 OCR：Windows.Media.Ocr（`winrt`）→ Tesseract → 文本层回退，记录 `ocr_engine` | 解析流水线（无独立接口） | `fixtures/papers/scanned/udl_scan.pdf` 解析后 `ocr_used=true`、chars≈16.3k、quality 0.66 |
| 长篇 / 书籍章节 | ◐ | 多页章节（5 页）按页切块、跨页标题续接、页眉页脚剔除；超长书未做目录页专门识别 | 同解析流水线 | `python -m eval.validate_corpus --only udl_scan` |
| 标题 / 作者 / 机构 / 摘要 / 关键词 | ✅ | 版面前区启发式 + 字号统计 + 机构正则；作者重建覆盖相邻行被拆块的情况 | `GET /api/papers/{id}`、`GET /api/papers/{id}/parsed` | 14 篇语料作者/机构问答 28/28 命中 |
| 一 / 二 / 三级标题树 | ✅ | 字号 ≥ 正文 0.90×、编号深度、粗体、附录规则；排除数据行与句子式伪标题 | `GET /api/papers/{id}/outline` | 标题树层级 ∈ {1,2,3}；transformer_v7 26 节 |
| 表格 | ✅ | `find_tables` + 表题向下 260pt 文本带聚类切格（无边框表也可抽） | `GET /api/papers/{id}/document` 的 `tables` | 13/14 篇抽到表；transformer Table 1 表头 `Model/EN-DE/EN-FR` |
| 图 | ◐ | 图像块面积过滤 + 矢量图簇聚合 + 邻接图注关联，裁剪存 `data/figures/` | `document.figures`、`GET /api/papers/{id}/pages/{n}.png` | 语料每篇 1–2 张；AlexNet 矢量图簇漏 1 张 |
| LaTeX 公式 | ✅ | 居中孤行 + Unicode 数学符号启发式 → `to_latex()` 生成 `\tag{n}` | `document.equations` | 公式 `plain` 与页面块一致；transformer β1/β2 题命中 |
| 参考文献 | ✅ | References 状态机 + `[n]` 切分 + 标题/年份/arXiv/DOI 抽取；修复参考文献标题被图注块吞并 | `GET /api/papers/{id}/citations`、`document.references` | 13/14 篇抽到 9–18 条；BERT 12 条 |
| 附录 | ✅ | 一级标题后退出 References 状态；附录进入标题树与索引 | `GET /api/papers/{id}/outline`（`appendix=true`） | transformer_v7 有 `B Appendix: Additional Ablations` |

## 2. 模块二：抗幻觉知识问答（RAG & 多文推理）

| 子要求 | 状态 | 实现 | 接口 | 验证 |
| --- | --- | --- | --- | --- |
| 单篇精准问答 | ✅ | BM25 ⊕ 确定性向量 → RRF 融合 → 意图加权（meta/reference/公式/表）→ 抽取式作答 | `POST /api/qa/ask`（`paper_ids` 限定单篇） | 115 题金标准 **90.4%**；作者/机构/超参/硬件/指标全对 |
| 原文出处 / 位置锚点 | ✅ | 每条引用 = `{paper_id, page, bbox, quote, section, anchor}`；答案内 `[E1]` 与证据一一对应 | 同上响应 `citations[]` | `qa_anchor_rate = 1.0`；前端点击角标跳页并高亮 |
| 多文跨文献对比 | ✅ | 9 个预定义维度，每维每篇独立检索 → 逐单元格选证 → 回挂锚点 → 缺失写「未提及」 | `POST /api/qa/compare`、`GET /api/qa/dimensions` | GPT-1/2/3：117M / 1.5B / 175B、context 1024/2048 全部命中；12/12 单元格有锚点 |
| 抗幻觉校验 | ✅ | 逐句 token 支撑率 ≥0.28 + 数值必须出现在被引原文 + 至少 1 条被引用证据；返回 `verified/confidence/checks` | 内嵌于 `/api/qa/ask` 响应 | 响应 `verification.checks`；`backend/tests/test_indexing_qa.py` |
| 无证据拒答 | ✅ | 检索无命中直接拒绝，不调用模型；`refused=true` | 同上 | 问库外问题返回「未找到直接证据」 |

## 3. 模块三：版式感知阅读与双向联动翻译

| 子要求 | 状态 | 实现 | 接口 | 验证 |
| --- | --- | --- | --- | --- |
| 双栏 / 公式 / 表格原生渲染 | ✅ | 服务端 150 DPI 页面图 + 结构化 bbox 覆盖层；双栏按通栏块分带还原阅读顺序 | `GET /api/papers/{id}/document`、`GET /api/papers/{id}/pages/{n}.png` | 覆盖框坐标 = `bbox/page_size` 百分比；阅读器显示「单栏/双栏」 |
| 标题树导航 | ✅ | 层级栈构建树，点击滚动到章节所在页 | `GET /api/papers/{id}/outline` | 阅读器左栏目录；26 节 transformer_v7 |
| `[n]` ↔ References 双向跳转 | ✅ | 正文引用标记 ↔ 参考文献条目互相回链，未解析/未引用分别统计 | `GET /api/papers/{id}/citations`（`link_map`） | `tests.test_reading` 断言 2 条引用、2 条参考文献、双向 anchor |
| 划词 / 悬停翻译 | ◐ | 块级翻译 + 块点击查看译文；未实现任意字符选区翻译 | `POST /api/papers/{id}/translate`（`block_ids` 可选） | 阅读器「翻译全文」后每块显示译文 |
| 整篇翻译（公式/代码保留英文） | ✅ | 按块翻译，先 `glossary.protect` 保护公式、`[n]`、变量、数字，译文按 `src_hash` 缓存 | 同上 | 译文块中 `$O(n^2)$`、`[1]` 原样保留 |
| 中英对比 / 双语并排 | ✅ | 左原文右译文两列，比例滚动同步；导出双栏 HTML | `frontend/assets/reader.js`（`bindScrollSync`） | 拖动任一侧另一侧跟随 |
| 导出 Markdown / HTML / 原位 PDF | ✅ | 原位 PDF 用 `china-s` 字体把译文写回原 bbox，图表从页面图裁剪嵌入 | `GET /api/papers/{id}/export/{fmt}`（`md/html/bilingual/pdf`） | 导出文件存在且 PDF 含中文；`tests.test_reading` |

## 4. 模块四：论文自动检索、追踪与智能推荐

| 子要求 | 状态 | 实现 | 接口 | 验证 |
| --- | --- | --- | --- | --- |
| 按名称 / 参考文献抓取入库 | ✅ | arXiv Atom API（在线）或 `fixtures/arxiv/mirror.json`（离线 9 条）；参考文献逐条匹配本地库再抓取 | `POST /api/discovery/search`、`POST /api/discovery/import`、`POST /api/discovery/references/{paper_id}` | 搜 "chain of thought" → 命中 CoT；导入后进入文献库并可解析 |
| 引用含金量筛选 | ◐ | `refs` 表预留 `value_score/value_label/value_reason`，当前按「是否被正文引用 + 引用次数」排序；上下文情感打分未实现 | `GET /api/papers/{id}/citations` | 引用统计 `cited_by/cited_count` |
| Sickle 风格定期追踪 | ✅ | 主题表 + `track_state` 时间戳节流（默认 360 分钟），`force` 立即刷新；进程内调度，未起独立 Sickle 进程 | `POST /api/discovery/track`（action=add/run/remove）、`GET /api/discovery/status` | `run` 返回 `fetched/new_items`；状态含 `last_run` |
| 本地库相关性推荐 | ✅ | `0.72*检索相关 + 0.28*本地库 token 重合 + 0.08*有代码`，再按反馈加权 | `GET /api/discovery/recommend?user_id=` | 推荐卡片显示相关性百分比与 reason |
| GitHub 代码优先 | ✅ | 元数据/摘要中识别 GitHub 链接，`has_code` 加权并显示徽标 | 同上 | 卡片「有代码 / 无代码」徽标 |
| 推荐理由 | ✅ | 模板化理由：命中的主题词、是否已在本地库、代码可得性 | 同上 `reason` | 理由中的词均来自标题/摘要 |
| 点赞 / 踩 / 多用户 | ✅ | `feedback` 按 `(user_id,target)` 存储；like +0.30、dislike −0.35、save +0.08、hide 过滤 | `POST /api/discovery/feedback` | `backend/tests/test_discovery.py`（14 tests） |

## 5. 模块五：局域引用图谱挖掘与学术综述生成

| 子要求 | 状态 | 实现 | 接口 | 验证 |
| --- | --- | --- | --- | --- |
| 引用关系解析 | ✅ | 对库内论文解析参考文献 → 标题/arXiv/DOI 匹配本地论文 → 建边并写 `edges`/`evidence` | `POST /api/graph/build` | 14 篇语料 10 条边；`evidence` 记录 ref 编号与匹配分 |
| 可视化图谱 | ✅ | networkx 指标 + 前端圆周布局 SVG（role 配色、PageRank 半径、悬停详情、Mermaid/DOT 导出） | `GET /api/graph`、`GET /api/graph/{id}`、`GET /api/graph/{id}/export?fmt=mermaid\|dot\|json` | 刷新后坐标不变（无随机） |
| 基石 / 边缘论文 | ✅ | PageRank(α=0.85) + 介数中心性 + 入度；role=foundation/bridge/peripheral/normal，附数值洞察 | `POST /api/graph/build` 响应 `foundation/bridge/peripheral/insights` | ResNet 被引 5 次、GPT-3 被引 3 次，识别 2 篇基石 |
| 自动综述 | ✅ | 按年份/社区组织，逐节生成「脉络—基石—演化」，每句带 `[paper_id]` 与页码引用；在线失败回落离线模板 | `POST /api/graph/survey`、`GET /api/graph/surveys/{id}` | 4 节 18.5k 字符、26 条引用；`backend/tests/test_graph.py` |
| Future Work 提炼 | ✅ | 抽取各文 Future Work → 按后续论文标题/摘要覆盖率判定 solved，排除已解决方向 | 同上响应 `future_directions`（`status=open\|solved`） | 21 个方向：19 open / 2 solved |

## 6. 模块六：学术写作辅助与数据可视化 Copilot

| 子要求 | 状态 | 实现 | 接口 | 验证 |
| --- | --- | --- | --- | --- |
| 无幻觉论文框架 | ✅ | 六节结构；References 只从本地 `papers` 表生成，正文 `[n]` allowlist 过滤 + 双向校验 | `POST /api/writing/outline`、`GET /api/writing/outlines` | `checks` 全 passed；`tests.test_writing`（8 tests） |
| 拓扑图脚本 | ✅ | TikZ / Graphviz / Mermaid / Matplotlib 四类确定性源码，不调用外部渲染器 | `POST /api/writing/diagram` | 同一输入两次输出字节一致 |
| CSV → 表格 / 折线 / 柱状 / 散点 / 三维雷达 | ✅ | `parse_csv_bytes` 类型推断 → 自研 SVG 720×420（雷达为分层多边形） | `POST /api/dataviz/datasets`、`POST /api/dataviz/charts`、`GET /api/dataviz/charts/{id}.svg` | 相同 spec 两次 SVG 字节一致；`tests.test_dataviz`（13 tests） |
| 学术 Figure Caption | ✅ | 趋势/极值/首末值/交叉点检测 → 规范句式，数字来自真实统计 | `charts` 响应 `caption` | `caption_has_numbers=true`；峰值句含 x 轴语义 |
| 学术翻译（中→英） | ✅ | `glossary.protect` 保护公式/引用/数字 → 在线 LLM 或离线术语+句式模板 → 标点归一；返回 coverage/notes | `POST /api/writing/translate` | 译文保留 `[1]`、`$O(n^2)$`；`tests.test_writing` |
| 导出大纲 | ✅ | Markdown / 纯文本导出 | `POST /api/writing/outlines/{id}/export?fmt=md\|txt` | 文件落盘且非空 |

## 7. 一键验证清单

```bash
cd backend
python -m unittest discover -s tests -v          # 48 tests OK
python -m eval.validate_corpus                   # 14 篇：tables/refs/quality
python -m eval.run_eval                          # 八段评测 → report.json + docs/EVALUATION.md

cd ..
python scripts/run_server.py --demo              # 建库 + 导入 15 PDF + 启动 8000 端口
curl -s localhost:8000/api/health                # {"status":"ok",...}
curl -s localhost:8000/api/system/status         # 能力矩阵与库统计
```

| 评测脚本输出 | 期望 |
| --- | --- |
| `parse_success_rate` | 1.0 |
| `avg_quality` | ≈0.97 |
| `qa_accuracy` | ≈0.90（115 题） |
| `qa_anchor_rate` | 1.0 |
| `graph_edges` / `foundation_count` | 10 / 2 |
| `outline_checks_passed` / `chart_deterministic` | true / true |
