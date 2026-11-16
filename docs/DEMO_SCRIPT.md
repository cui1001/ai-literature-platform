# AgentLit 模块功能演示视频 · 制作脚本

> 成片：`docs/demo/AgentLit_Demo_1080p.mp4`（1920×1080、30 fps、H.264、时长 3:32、中文字幕烧录、无配音）
> 本文件同时是分镜脚本、录制说明与可复现的生成步骤。

---

## 1. 视频规格

| 项目 | 规格 |
| --- | --- |
| 分辨率 | 1920 × 1080 |
| 帧率 | 30 fps |
| 编码 | H.264（High profile，yuv420p） |
| 容器 | MP4（faststart） |
| 时长 | 3 分 32 秒（212 秒） |
| 字幕 | 中文，烧录在画面底部字幕条 |
| 配音 | 无（字幕即旁白，可后期按本文件第 6 节录制配音） |
| 文件大小 | 约 7.4 MB |
| 成片路径 | `docs/demo/AgentLit_Demo_1080p.mp4` |
| 元数据 | `docs/demo/video_meta.json` |

**画面来源**：全部来自本地真实运行的验收实例（`DATA_DIR=D:/AI-Project/_agentlit_work/data_accept`，离线三开关：`LLM_MODE=offline`、`EMBED_MODE=hashing`、`ARXIV_MODE=offline`），不是设计稿或合成示意图。

---

## 2. 演示模块与时长分配

视频覆盖 `docs/PROJECT_INTRO.md` 第 1.3 节的六个模块；「版式阅读」模块用两个连续镜头分别展示阅读与引用跳转，「写作与可视化」模块用两个镜头分别展示框架生成与图表。

| 模块 | 镜头 | 时间码 | 时长 |
| --- | --- | --- | --- |
| 总览 | 片头 | 0:00 – 0:06 | 6 s |
| 01 文献库与解析 | 文库 + 解析指标 | 0:06 – 0:34 | 28 s |
| 02 版式阅读 | 阅读器 + 标题树 | 0:34 – 1:00 | 26 s |
| 02 版式阅读 | 正文引用跳转 | 1:00 – 1:18 | 18 s |
| 03 抗幻觉问答 | 数值题问答与证据 | 1:18 – 1:48 | 30 s |
| 04 arXiv 发现与追踪 | 检索、推荐、入库 | 1:48 – 2:12 | 24 s |
| 05 引用图谱与综述 | 20 篇引用拓扑 | 2:12 – 2:38 | 26 s |
| 06 写作与可视化 | 框架 + 真实 References | 2:38 – 3:04 | 26 s |
| 06 写作与可视化 | CSV → 图表与复现脚本 | 3:04 – 3:24 | 20 s |
| 总结 | 复验数据 | 3:24 – 3:32 | 8 s |

---

## 3. 分镜脚本

### 镜头 1 · 片头（0:00 – 0:06）

- **画面**：深色标题页，`AgentLit` 大字 + 一句话定位；下方四枚指标卡：37 篇验收文献、1,941 页、10,332 个文本块、2,819 条参考文献。
- **操作**：无（片头）。
- **字幕 / 旁白**：AgentLit 科研小助手，六大模块演示。
- **预期反馈**：标题与指标在 0.45 s 内淡入。

### 镜头 2 · 文献库与解析（0:06 – 0:34）

- **画面**：文库页实拍。顶部统计条 `37 篇 / 1,941 页 / 10,332 块 / 2,813 章节 / 2,819 参考文献 / 1 篇 OCR`；卡片展示 Understanding Deep Learning（541 页、质量 86%、双栏）、BERT（16 页、质量 100%）、GPT-1、LLaMA、DeepSeek-R1、DeepSeek-V3；页面右侧是上传区与 `sha256 + arXiv 版本号 + 正文 SimHash` 去重说明。
- **操作步骤**：
  1. 打开 `#library`；
  2. 展示上传区（拖拽或「上传 PDF」）；
  3. 指针扫过卡片上的质量分、版式标记与「阅读 / 版本 / 删除」按钮。
- **字幕 / 旁白**：上传 PDF 后，系统完成解析、去重、入库与索引，并在卡片上给出质量分与版式。
- **预期反馈**：统计数字与卡片即时可见；「版本」按钮进入版本族对比。

### 镜头 3 · 版式阅读（0:34 – 1:00）

- **画面**：阅读器实拍。左侧 26 节目录（Abstract → 1 Introduction → 2 Background → 3 Model Architecture → 3.1–3.5 → 4 Why Self-Attention → 5 Training）；中间页面图保留单栏版式、作者与机构、Abstract；右上角 `15 blocks`；顶部三种阅读模式（原文 / 中文对照 / 双语并排）与导出菜单。
- **操作步骤**：
  1. 打开 `#reader/<paper_id>`；
  2. 点击目录中的 `3 Model Architecture`，页面滚动到对应页；
  3. 悬停页面块，展示块级定位框；
  4. 切换「中文对照」标签展示翻译模式（不改变版式）。
- **字幕 / 旁白**：阅读器保留原始版式，左侧标题树可跳转，正文块带有精确坐标。
- **预期反馈**：标题树高亮当前章节；页面滚动与块定位同步；模式切换不破坏页面排版。

### 镜头 4 · 正文引用跳转（1:00 – 1:18）

- **画面**：阅读器第 2 页，正文中的 `[11]` 被点开，弹出「引用 [11]」弹窗：Kaiming He, Xiangyu Zhang, Shaoqing Ren, and Jian Sun. *Deep residual learning for image recognition.* In Proceedings of the IEEE Conference on Computer Vision and Pattern Recognition, pages 770–778, 2016.
- **操作步骤**：
  1. 点击正文热区 `[11]`（`.cite-hot`）；
  2. 弹窗展示完整条目；
  3. 关闭弹窗，点击多编号热区 `[10, 39, 42]`，弹窗列出三条编号；
  4. 选择 `[10]`，References 面板滚动到第 10 条并高亮。
- **字幕 / 旁白**：正文里的引用编号就是热区，点击即可看到条目原文，也能从条目回到正文位置。
- **预期反馈**：单编号直接定位；多编号弹出选择；条目与正文双向回跳。

### 镜头 5 · 抗幻觉问答（1:18 – 1:48）

- **画面**：问答页实拍。文献范围只勾选 Attention Is All You Need；问题「训练 Transformer Base 模型使用了多少块 GPU？具体是什么型号？」；回答卡徽标「证据已校验」，答案含 `Training took 3.5 days on 8 P100 GPUs.`；底部指标：置信度 95%、引用 4 处、数值校验 6/6、模型 offline、2730 ms；下方「原文证据（点击定位）」。
- **操作步骤**：
  1. 打开 `#qa`，取消全选，只勾选目标论文；
  2. 输入问题并发送；
  3. 指针对准答案中的 `8 P100` 与底部的 `数值校验 6/6`；
  4. 点击一条原文证据，页面跳到对应块并高亮。
- **字幕 / 旁白**：问答返回结论、证据与校验结果，数值逐项对照原文，避免无依据生成。
- **预期反馈**：答案、证据、校验徽标同时出现；点击证据可定位原文。

### 镜头 6 · arXiv 发现与追踪（1:48 – 2:12）

- **画面**：发现页实拍。搜索框 `attention is all you need`；结果 2 条（1706.03762v1、v7，相关性 72%，均标注「已在本地文献库中」）；右侧「为你推荐」：GPT-3（相关性 49%）、ViT（46%）、BERT，附「入库 / 赞 / 踩」。
- **操作步骤**：
  1. 打开 `#discovery`，输入关键词并检索；
  2. 展示「已在本地文献库中」标记与相关性；
  3. 指针扫过推荐卡的入库与反馈按钮；
  4. 点击「入库」演示导入入口（视频中停在确认前，避免重复导入）。
- **字幕 / 旁白**：检索结果与推荐都来自本地语料镜像，入库后立即参与问答与图谱。
- **预期反馈**：结果标注去重状态；推荐按相关性排序；入库按钮进入解析流水线。

### 镜头 7 · 引用图谱与综述（2:12 – 2:38）

- **画面**：图谱页实拍。标题 `20 节点 · 43 边 · 密度 0.1132 · 基石 8`；节点按角色着色（基石 / 桥接 / 边缘 / 常规）；下方「图论洞察」：覆盖 20 篇、43 条引用边、密度 0.113、平均聚类系数 0.405、时间跨度 2018–2026、基石 BERT 等。
- **操作步骤**：
  1. 打开 `#graph`，勾选 20 篇论文；
  2. 点击「构建引用图谱」，等待画布渲染；
  3. 悬停节点查看标题与角色；
  4. 展示「生成综述 + Future Work」入口与导出 Mermaid / DOT。
- **字幕 / 旁白**：图谱把引用关系变成结构指标，自动综述再从全文中抽取未来研究方向。
- **预期反馈**：图在数秒内布局完成；悬停显示节点信息；导出按钮可用。

### 镜头 8 · 写作 Copilot（2:38 – 3:04）

- **画面**：写作页实拍。idea「高效长文本建模的注意力机制研究：从 Transformer 到 BERT 的架构演进」，勾选 6 篇文献，点击「生成框架」；右侧生成标题、摘要、引言、相关工作（References 6 条，全部来自本地库，`[1]`–`[6]` 与正文一一对应）；下方还有「架构拓扑图脚本」与「学术翻译」。
- **操作步骤**：
  1. 打开 `#writing`，输入 idea；
  2. 勾选目标文献；
  3. 点击「生成框架」；
  4. 指针对准 `[1]`–`[6]` 条目与「References 6 条」；
  5. 展示「生成脚本」与「翻译」入口。
- **字幕 / 旁白**：写作模块先给框架，再注入真实参考文献；每条引用都来自本地库。
- **预期反馈**：数秒内生成六章框架；参考文献标题、作者、年份与本地库一致；引用编号与正文对应。

### 镜头 9 · 数据可视化（3:04 – 3:24）

- **画面**：可视化页实拍。数据集 `models.csv`（4 行 × 4 列：GPT-1 0.117/512/2018、GPT-2 1.5/1024/2019、GPT-3 175/2048/2020、BERT 0.34/512/2018）；柱状图 `context_tokens vs model`；Figure Caption：`The peak value is 2048.00 (context_tokens) at model = GPT-3.`；下方 matplotlib 复现脚本。
- **操作步骤**：
  1. 打开 `#dataviz`，选择数据集；
  2. 选择柱状图、X=`model`、Y=`context_tokens`；
  3. 点击「生成图表」；
  4. 展示图表、Figure Caption 与复现脚本。
- **字幕 / 旁白**：图表由确定性渲染器生成，并附带可复现的 matplotlib 脚本与学术图注。
- **预期反馈**：SVG 图表即时渲染；相同输入重复生成结果一致；脚本可离线复现。

### 镜头 10 · 总结（3:24 – 3:32）

- **画面**：复验数据页。要点：解析质量 0.981、问答 105/115（91.3%）、锚点 100%、校验 97.4%；39 篇 PDF、2,904 条参考文献、平均质量 0.994、9 个验收问题 9/9；图谱 20 节点 43 边、写作与图表确定性通过、49 个单测；文档、复验报告、演示脚本路径。
- **操作**：无（总结）。
- **字幕 / 旁白**：以上画面与数字均来自本地真实运行，与复验报告一致。
- **预期反馈**：指标卡与镜头 2–9 中出现的数字一一对应。

---

## 4. 录制与制作方式

### 4.1 运行环境

| 组件 | 版本 / 路径 |
| --- | --- |
| Python | 3.14（`D:/python/python.exe`） |
| Pillow | 12.2（幻灯片排版） |
| ffmpeg | imageio-ffmpeg 9.0.2，`D:/AI-Project/_agentlit_work/video_libs/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v9.0.2.exe` |
| 中文字体 | `C:/Windows/Fonts/msyh.ttc`、`msyhbd.ttc` |
| 验收实例 | `python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8030`，`DATA_DIR=D:/AI-Project/_agentlit_work/data_accept`，离线三开关 |
| 浏览器 | ego-browser，1256×808 视口截图 |

### 4.2 采集方式

1. **启动验收实例**（离线模式，避免外网依赖）：

   ```bash
   cd D:/AI-Project/ai-literature-platform
   DATA_DIR=D:/AI-Project/_agentlit_work/data_accept \
   LLM_MODE=offline EMBED_MODE=hashing ARXIV_MODE=offline TRANSLATE_MODE=offline \
   python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8030
   ```

2. **逐页截图**：通过 `#library`、`#reader/<paper_id>`、`#qa`、`#discovery`、`#graph`、`#writing`、`#dataviz` 切换视图（hash 路由不带斜杠，切换 hash 后需 `location.reload()`）。截图保存在 `_agentlit_work/demo_raw/`。
3. **交互取证**：问答使用真实问题；图谱勾选 20 篇并构建；写作勾选 6 篇生成框架；可视化上传真实 CSV 并生成柱状图。所有数字均来自接口返回或页面渲染，不做手工改数。

### 4.3 后期合成

`_agentlit_work/build_demo_video.py` 完成三件事：

1. 用 Pillow 以 1920×1080 排版 10 张镜头卡：左侧为真实截图（等比缩放、圆角、投影），右侧为模块标题、要点与指标卡，底部为字幕条与进度条；
2. 每个镜头用 ffmpeg 编码为 30 fps 片段（0.45 s 淡入 / 淡出）；
3. 用 concat 合并为成片，写入 `docs/demo/AgentLit_Demo_1080p.mp4`。

```bash
D:/python/python.exe _agentlit_work/build_demo_video.py
```

> 说明：本机 ffmpeg 进程无法直接在 `docs/demo/` 下创建文件（权限拒绝），脚本先写入 `_agentlit_work/demo_build/final.mp4`，再用 `shutil.copy2` 复制到目标目录。

### 4.4 素材清单

| 素材 | 路径 | 说明 |
| --- | --- | --- |
| 成片 | `docs/demo/AgentLit_Demo_1080p.mp4` | 1920×1080，30 fps，3:32 |
| 元数据 | `docs/demo/video_meta.json` | 时长、帧率、镜头顺序 |
| 字幕文本 | `docs/demo/AgentLit_Demo.srt` | 10 条字幕，可重录配音 |
| 文库截图 | `_agentlit_work/demo_raw/library.png` | 37 篇统计与卡片 |
| 阅读截图 | `_agentlit_work/demo_raw/reader.png` | 26 节目录、15 页原文 |
| 引用截图 | `_agentlit_work/demo_raw/citation.png` | `[11]` 弹窗 |
| 问答截图 | `_agentlit_work/demo_raw/qa.png` | 8×P100 与 6/6 校验 |
| 发现截图 | `_agentlit_work/demo_raw/discovery.png` | 检索 2 条 + 推荐 3 条 |
| 图谱截图 | `_agentlit_work/demo_raw/graph.png` | 20 节点 43 边 |
| 写作截图 | `_agentlit_work/demo_raw/writing3.png` | 6 条真实 References |
| 图表截图 | `_agentlit_work/demo_raw/dataviz3.png` | 柱状图 + Caption + 脚本 |
| 合成脚本 | `_agentlit_work/build_demo_video.py` | 可重复生成同一成片 |

### 4.5 现场重录替代方案

若需要真人操作录屏，用 OBS 录制浏览器窗口，或使用 ffmpeg 抓屏：

```bash
ffmpeg -f gdigrab -framerate 30 -i desktop -c:v libx264 -crf 18 -pix_fmt yuv420p demo_live.mp4
```

按第 3 节镜头顺序操作，每段保留 1–2 秒静帧，后期按第 6 节字幕对齐；数据与画面必须与 `docs/REVERIFICATION.md` 一致。

---

## 5. 数据一致性核对

| 指标 | 画面值 | 数据来源 |
| --- | --- | --- |
| 解析成功率 | 100% | `docs/EVALUATION.md`、`backend/eval/report.json` |
| 解析质量（fixtures） | 0.981 | 同上 |
| 问答准确率 | 105/115（91.3%） | 同上 |
| 锚点定位 | 100% | 同上 |
| 答案校验 | 97.4% | 同上 |
| 39 篇验收平均质量 | 0.994 | `docs/REVERIFICATION.md` |
| 参考文献总数 | 2,904 条 | 同上 |
| 9 个验收问题 | 9/9 | `_agentlit_work/accept_qa.json` |
| 图谱规模 | 20 节点 / 43 边 / 基石 8 | `_agentlit_work/final_graph.json` |
| 单元测试 | 49 个 | `cd backend && python -m unittest discover -s tests` |

画面中出现的 37 篇、1,941 页、10,332 块、2,819 条参考文献、95% 置信度、4 处引用、6/6 数值校验、2,730 ms、43 条边、51 条 Future Work 等，均来自验收实例的接口返回与页面渲染；未使用虚构数据。

---

## 6. 字幕全文（可重录配音）

1. AgentLit 科研小助手，六大模块演示。
2. 上传 PDF 后，系统完成解析、去重、入库与索引，并在卡片上给出质量分与版式。
3. 阅读器保留原始版式，左侧标题树可跳转，正文块带有精确坐标。
4. 正文里的引用编号就是热区，点击即可看到条目原文，也能从条目回到正文位置。
5. 问答返回结论、证据与校验结果，数值逐项对照原文，避免无依据生成。
6. 检索结果与推荐都来自本地语料镜像，入库后立即参与问答与图谱。
7. 图谱把引用关系变成结构指标，自动综述再从全文中抽取未来研究方向。
8. 写作模块先给框架，再注入真实参考文献；每条引用都来自本地库。
9. 图表由确定性渲染器生成，并附带可复现的 matplotlib 脚本与学术图注。
10. 以上画面与数字均来自本地真实运行，与复验报告一致。

---

## 7. 验收自检

```bash
# 1) 时长 / 分辨率 / 编码
ffmpeg -hide_banner -i docs/demo/AgentLit_Demo_1080p.mp4 2>&1 | grep -E "Duration|Stream"

# 2) 抽帧检查（覆盖片头、模块、总结）
ffmpeg -y -ss 12  -i docs/demo/AgentLit_Demo_1080p.mp4 -frames:v 1 /tmp/f12.png
ffmpeg -y -ss 100 -i docs/demo/AgentLit_Demo_1080p.mp4 -frames:v 1 /tmp/f100.png
ffmpeg -y -ss 206 -i docs/demo/AgentLit_Demo_1080p.mp4 -frames:v 1 /tmp/f206.png
```

自检结果：`1920x1080, 30 fps, h264, 00:03:32`；抽帧与第 3 节画面一致；字幕无错字；未出现占位符。
