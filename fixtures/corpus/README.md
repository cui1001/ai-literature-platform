# 合成论文语料（fixtures/corpus）

本目录是「科研助手 Agent」竞赛项目的**确定性合成论文语料真值**，与 `fixtures/papers/` 下的 PDF 一一对应。
语料完全离线生成，不依赖网络与第三方数据；同一份 `papers.json` 在任何机器上都会生成字节级一致的 PDF，
用于解析、版面分析、RAG 检索、引用图谱与评测的稳定输入。

## 文件清单

| 路径 | 说明 |
| --- | --- |
| `fixtures/corpus/papers.json` | 语料真值：14 篇论文的结构化定义（标题、作者、章节正文、公式、表格、图注、参考文献、附录、facts、qa） |
| `fixtures/corpus/README.md` | 本文件 |
| `fixtures/papers/*.pdf` | 14 篇可检索 PDF（含 `udl_scan.pdf`；`scanned/` 另计 1 个） |
| `fixtures/papers/scanned/udl_scan.pdf` | 纯图像扫描版（200 DPI 栅格化，无文本层），用于 OCR 演示 |
| `fixtures/arxiv/mirror.json` | 本地 arXiv 镜像元数据（9 条，覆盖有 arXiv 编号的 9 个版本） |
| `scripts/make_corpus.py` | 生成脚本：读取 `papers.json` 渲染 PDF，并派生 `mirror.json` |

`papers.json` 顶层结构：`{"corpus_version": 1, "layout_seed": 20260101, "papers": [...]}`。
每篇论文包含 `key/title/authors/year/venue/arxiv_id/versions/abstract/keywords/layout/scanned/pages_target/
sections/equations/tables/figures/references/appendix/facts/qa` 等字段；`facts` 与 `qa` 中的数字与正文严格一致，
可直接作为抽取与问答评测的金标准。

## 语料内容

14 篇论文覆盖 2012–2024 年、单栏/双栏两种版式：

- Transformer 系：`transformer_v1`（2017，arXiv 1706.03762v1）、`transformer_v7`（同题 v7，补充训练细节、修正 BLEU 表并新增附录 B，用于语义去重演示）、`bert`、`gpt1`、`gpt2`、`gpt3`
- 视觉系：`alexnet`、`resnet`、`vit`
- 适配与检索：`lora`、`rag`、`chain_of_thought`
- 综述与书籍章节：`survey`（含独立 “Future Work” 章节与附录）、`udl_scan`（书籍章节，扫描件用于 OCR）

引用关系构成小型图谱：`survey` 引用其余 12 篇；`vit/bert/gpt1/gpt2/gpt3` 引用 `transformer_v1`；
`lora` 引用 `gpt3`；`rag` 引用 `bert`；`resnet` 引用 `alexnet`；`chain_of_thought` 引用 `gpt3`。
`references[].target_key` 指向本语料内的论文 key，可直接构图。

## 再生成

在仓库根目录执行（Windows 下 `python` 即系统 Python，需已安装 PyMuPDF）：

```bash
python scripts/make_corpus.py
```

默认输出：

- PDF → `fixtures/papers/`（扫描版自动写入 `fixtures/papers/scanned/`）
- 镜像 → `fixtures/arxiv/mirror.json`

常用参数：

```bash
python scripts/make_corpus.py --out "$TEMP/corpus_check"          # 输出到其它目录
python scripts/make_corpus.py --only transformer_v1,rag        # 只生成指定论文
python scripts/make_corpus.py --corpus fixtures/corpus/papers.json \
    --out fixtures/papers --mirror fixtures/arxiv/mirror.json  # 显式指定全部路径
```

脚本无随机数、无时间戳，重复运行结果一致（`fitz` 保存时使用固定 metadata 与 `no_new_id=True`）。
若只修改 `papers.json` 的正文，重新执行脚本即可同步 PDF；`--only` 适合局部调试。

## 版式约定（解析器可依赖）

- A4 595×842pt，页边距 56pt；奇数页顶部 7.5pt 灰色 running head，页底居中页码。
- 标题 18pt Helvetica-Bold 居中；作者 10pt；机构 9pt Oblique；日期/arXiv 号 8.5pt。
- 一级标题 12pt、二级 10.5pt、三级 10pt BoldOblique；正文 9.5pt / 行距 13pt / 段距 5pt。
- 双栏栏宽 226pt、栏距 30pt；表格 caption 在上、图 caption 在下，均为 8.5pt；表体 8pt。
- 公式单独居中、编号右对齐；References 条目 8pt 悬挂缩进；附录正文 9pt。
- 文本仅使用 Latin-1 可编码字符（内置 Helvetica 限制），`udl_scan` 标题中的破折号以 ASCII `-` 表示。

## 校验

```bash
python -c "import json;d=json.load(open('fixtures/corpus/papers.json',encoding='utf-8'));print(len(d['papers']))"   # 14
python scripts/make_corpus.py --out "$TEMP/corpus_check"                                                              # 15 个 PDF
```

扫描版应满足：每页 `page.get_text().strip() == ""` 且 `page.get_images()` 非空；
`transformer_v1` 与 `transformer_v7` 的正文相似度约 0.95（`difflib.SequenceMatcher`），
既保证同源又体现 v7 的实质修订。
