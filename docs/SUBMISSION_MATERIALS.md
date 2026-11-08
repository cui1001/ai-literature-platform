# 竞赛提交材料清单（2.1 / 2.3 / 2.4）

> 版本：v2.0.0 ｜ 生成日期：2026-11-08
> 本清单逐条对应赛方「2.1 材料」「2.3 技术文档」「2.4 在线演示」的要求，标注**已满足 / 部分满足 / 待补充**及证据位置。
> 按要求，2.2 演示视频本次不提交；文末列出仍需参赛队补充的信息，未确认的内容一律不代填。

## 2.1 材料要求

| 要求项 | 赛方要求 | 状态 | 证据 / 位置 | 说明 |
| --- | --- | --- | --- | --- |
| 托管平台 | GitHub / Gitee，须设为 Public 或提供评审访问权限 | 已满足 | <https://github.com/cui1001/ai-literature-platform> | 2026-11-08 核实：仓库 `private=false`（Public）、默认分支 `master`；远端 `master` 与本地一致，历史完整（旧原型 14 次提交 + 分阶段重构与 `--no-ff` 合并），另有 11 个开发/修复分支 |
| 代码规范 | 代码结构清晰、包含有效注释、遵循主流代码规范 | 已满足 | `backend/app/`（core/db/providers/parsing/papers/indexing/qa/reading/discovery/graph/writing/dataviz/api 分层）、`frontend/assets/`；关键函数均有 docstring 与中文注释 | 后端为 Python 3.11+ 标准库 + FastAPI 风格；前端为原生 ES Module，无构建链 |
| README 文件 | 项目介绍、技术架构、安装运行指南、团队信息 | 部分满足 | [README.md](../README.md)：项目介绍、六大模块、架构一览、快速开始、评测、测试复现、提交材料 | 「团队信息」已填写单人独立开发与分工；队伍名称/单位/联系方式为 `(待补充：…)`，需参赛队补充 |
| Git 记录 | 保留完整 Git 提交历史，体现真实开发过程 | 已满足 | <https://github.com/cui1001/ai-literature-platform/commits/master>：旧原型 14 次提交 → 阶段 A 文档/重组 → 内核/阅读/接口/四模块/前端/语料/问答/交付文档等分支，均以 `--no-ff` 合并 | 推送为 fast-forward（`7cace1a..c27c40d`），未 squash、未重写；`docs/DEVLOG.md` 按阶段记录动机、bug 与验证 |
| 环境配置 | 环境变量模板 + 依赖清单 | 已满足 | [backend/.env.example](../backend/.env.example)、[backend/requirements.txt](../backend/requirements.txt) | 变量含 `DATA_DIR / LLM_MODE / DEEPSEEK_* / EMBED_MODE / SILICONFLOW_* / ARXIV_MODE / PAGE_RENDER_DPI / OCR_TEXT_THRESHOLD / TRACKER_INTERVAL_MINUTES`；依赖 8 个纯 Python 包，无 Node/npm |

### 2.1 补充说明

- **运行环境基线**：Windows 10/11 或 Linux；Python 3.11+（本机验证 3.14）；无需 GPU、无需外网。OCR 优先使用 Windows 内置 `Windows.Media.Ocr`（`winrt`），Linux 无 OCR 引擎时自动回退文本层并在接口中标注 `degraded`。
- **代码仓库**：<https://github.com/cui1001/ai-literature-platform>（GitHub Public，默认分支 `master`；2026-11-08 核实：历史完整，含旧原型 14 次提交与分阶段重构合并）。
- **安装与启动**（与 README 一致）：

  ```bash
  python -m pip install -r backend/requirements.txt
  python scripts/run_server.py --demo          # http://127.0.0.1:8000
  python scripts/run_server.py --online --demo # 配置 API Key 后启用在线增强
  ```

- **依赖清单摘要**：`fastapi`、`uvicorn`、`pydantic`、`python-dotenv`、`pymupdf`、`numpy`、`networkx`、`pillow`、`openai`（可选，仅在线模式）。不使用 `python-multipart`、`pytest`、`chromadb`、`pandas`、`matplotlib` 运行时。

## 2.3 技术文档

| 要求项 | 赛方要求 | 状态 | 证据 / 位置 | 说明 |
| --- | --- | --- | --- | --- |
| 格式 | PDF，不超过 30 页 | 已满足 | [docs/TECHNICAL_REPORT.pdf](TECHNICAL_REPORT.pdf) | **14 页**；`scripts/build_report_pdf.py` 导出后自动校验页数与七章标题 |
| 内容 1 | 项目概述 | 已满足 | 技术文档第 1 章（1.1 背景与问题、1.2 建设目标、1.3 命题覆盖、1.4 术语） | — |
| 内容 2 | 需求分析 | 已满足 | 第 2 章（2.1 用户场景、2.2 功能需求 F1–F6、2.3 非功能需求、2.4 约束与假设） | 每条需求附验收要点 |
| 内容 3 | 系统架构设计 | 已满足 | 第 3 章（3.1 总体架构图、3.2 技术栈与选型理由、3.3 模块划分、3.4 数据模型 20 表、3.5 接口设计、3.6 运行流程） | 架构图源文件 [docs/assets/architecture.svg](assets/architecture.svg) |
| 内容 4 | AI 技术方案 | 已满足 | 第 4 章（4.1 总体思路、4.2 解析、4.3 检索增强、4.4 抗幻觉问答、4.5 多文对比、4.6 图谱与综述、4.7 写作、4.8 可视化、**4.9 选型理由与替代方案**、**4.10 优化策略与迭代记录**） | 同时覆盖「选型理由、集成方式、优化策略」三项要求 |
| 内容 5 | 核心功能实现 | 已满足 | 第 5 章（5.1–5.6 对应六大模块，5.7 前端交互） | 含真实接口路径与前端交互说明 |
| 内容 6 | 测试与部署 | 已满足 | 第 6 章（6.1 测试策略、6.2 测试结果、6.3 部署与运行、6.4 已知边界与风险） | 指标与 `docs/EVALUATION.md` 一致 |
| 内容 7 | 总结与展望 | 已满足 | 第 7 章（完成情况、不足与后续方向） | 展望仅写已识别的工程/算法方向，不虚构结论 |
| AI 技术说明 | 重点阐述选型理由、集成方式、优化策略 | 已满足 | 4.9 选型理由与替代方案、4.1 总体思路（集成架构）、4.10 优化策略与迭代记录（问答 38.3% → 90.4% 的六轮迭代） | 每项均给出取舍理由与量化结果 |
| 创新点说明 | 明确列出技术创新点与应用创新点 | 已满足 | 第 8 章：8.1 技术创新点（5 条）、8.2 应用创新点（5 条） | 每条对应到实现位置与可验证指标 |

### 技术文档章节结构

```
封面
摘要
1 项目概述        1.1 背景与问题 / 1.2 建设目标 / 1.3 命题覆盖与交付范围 / 1.4 术语约定
2 需求分析        2.1 用户与使用场景 / 2.2 功能需求 F1–F6 / 2.3 非功能需求 / 2.4 约束与假设
3 系统架构设计    3.1 总体架构 / 3.2 技术栈与选型理由 / 3.3 模块划分 / 3.4 数据模型 / 3.5 接口设计 / 3.6 运行流程
4 AI 技术方案     4.1 总体思路 / 4.2 解析 / 4.3 检索增强 / 4.4 抗幻觉问答 / 4.5 多文对比 / 4.6 图谱与综述 / 4.7 写作 / 4.8 可视化 / 4.9 选型理由与替代方案 / 4.10 优化策略与迭代记录
5 核心功能实现    5.1–5.6 六大模块 / 5.7 前端交互
6 测试与部署      6.1 测试策略 / 6.2 测试结果 / 6.3 部署与运行 / 6.4 已知边界与风险
7 总结与展望      完成情况、不足与后续方向（无二级标题）
8 创新点说明      8.1 技术创新点（5 条）/ 8.2 应用创新点（5 条）
附录 A 接口清单    附录 B 复现命令
```

## 2.4 在线演示

| 要求项 | 赛方要求 | 状态 | 证据 / 位置 | 说明 |
| --- | --- | --- | --- | --- |
| 部署要求 | 提供可公网访问的在线演示地址 | 待补充（部署套件已就绪） | [DEPLOYMENT.md](DEPLOYMENT.md)、[Dockerfile](../Dockerfile)、[docker-compose.yml](../docker-compose.yml)、[deploy/](../deploy/) | 三种可选方案已给出并可直接执行：云服务器 + Caddy（推荐、7×24）、Cloudflare Tunnel（最快，电脑需开机）、Render/Railway（容器托管）。**公网地址需实际部署后填入** |
| 可用性 | 评审期间保持服务可用 | 待补充 | `deploy/agentlit.service`（systemd 常驻 + 自动重启）、`deploy/Caddyfile`（HTTPS）、Docker `restart: unless-stopped` | 数据通过 `DATA_DIR` 持久化，重启不重新解析 |
| 体验账号 | 如需登录，提供评审专用测试账号 | 不适用（当前无登录） | — | 系统当前无鉴权，打开即可体验；若用 Caddy `basic_auth` 或 Cloudflare Access 加鉴权，需在此补充账号口令 |

### 本地等价演示（评审可复现）

```bash
python -m pip install -r backend/requirements.txt
python scripts/run_server.py --demo
# 浏览器打开 http://127.0.0.1:8000
```

首次启动会自动解析 15 个 PDF（14 篇论文 + 1 份扫描件）、建索引并写入 `data/`；重启不会重复解析。

## 2.2 演示视频（本次不提交）

| 要求项 | 赛方要求 | 状态 |
| --- | --- | --- |
| 时长 | 5–8 分钟 | 本次不拍摄 |
| 内容 | 项目介绍、核心功能演示、AI 技术亮点 | 本次不拍摄 |
| 格式 | MP4，分辨率 ≥1080p | 本次不拍摄 |
| 语言 | 普通话讲解，可配字幕 | 本次不拍摄 |

## 交付物目录

| 文件 | 说明 |
| --- | --- |
| <https://github.com/cui1001/ai-literature-platform> | 代码仓库（Public，`master` + 11 个开发/修复分支） |
| [TECHNICAL_REPORT.pdf](TECHNICAL_REPORT.pdf) | 技术文档（14 页，最终提交版） |
| [TECHNICAL_REPORT.md](TECHNICAL_REPORT.md) | 技术文档 Markdown 源文件 |
| [assets/architecture.svg](assets/architecture.svg) | 系统架构图源文件 |
| [EVALUATION.md](EVALUATION.md) | 可复现评测报告（脚本生成） |
| [COMPETITION_MATRIX.md](COMPETITION_MATRIX.md) | 六大命题逐条映射与验证命令 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 架构设计与 ADR |
| [DEVLOG.md](DEVLOG.md) | 分阶段开发日志与 bug 复盘 |
| [screenshots/](screenshots/) | 六个视图的真实渲染截图 |
| [DEPLOYMENT.md](DEPLOYMENT.md) | 在线演示部署指南（云服务器 / Cloudflare Tunnel / PaaS） |
| [../Dockerfile](../Dockerfile)、[../docker-compose.yml](../docker-compose.yml) | 容器镜像与编排（`DATA_DIR=/data` 持久化） |
| [../deploy/](../deploy/) | systemd 服务、Caddyfile（域名 / sslip.io 无域名 HTTPS）、cloudflared 配置示例 |
| [../scripts/run_server.py](../scripts/run_server.py) | 一键启动脚本（`--demo/--online/--port/--data-dir`） |
| [../scripts/build_report_pdf.py](../scripts/build_report_pdf.py) | 技术文档 PDF 构建脚本 |

## 待补充信息（需参赛队确认后替换）

1. **团队信息**：队伍名称、所属单位、联系方式——填入 README「团队信息」（分工已按单人独立开发填写）。
2. **在线演示地址、开放时段、评审账号**——部署套件已就绪（见 [DEPLOYMENT.md](DEPLOYMENT.md)）；实际域名需部署后填入 README「在线演示」与 2.4 表。
3. 若赛方要求封面含队伍名称/学校 Logo，可在 `scripts/build_report_pdf.py` 的 `COVER` 常量中补充后重新生成 PDF。

在以上信息确认前，本清单不对其做任何假设或代填。

## 仓库推送记录（2026-11-08）

| 步骤 | 命令 | 结果 |
| --- | --- | --- |
| 核对远端 | `git ls-remote --heads origin` | 远端仅 `master`，指向 `7cace1a`（旧原型 14 次提交） |
| 确认快进 | `git merge-base --is-ancestor origin/master master` | 通过（本地 `master` 是远端后代，可 fast-forward） |
| 推送主分支 | `git push origin master` | `7cace1a..c27c40d  master -> master`（26 个新提交，未重写历史） |
| 推送开发分支 | `git push origin --all` | 新增 10 个功能/修复分支 |
| 复核 | `git rev-list --count origin/master` | 推送后 42 次提交（含本次文档提交与合并）、12 个分支；GitHub API `private=false`、`default_branch=master` |
