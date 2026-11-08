---
title: AgentLit 科研助手 Agent 平台
emoji: 🔬
colorFrom: indigo
colorTo: blue
sdk: docker
app_port: 8000
pinned: false
license: mit
short_description: PDF 解析 · 抗幻觉问答 · 引用图谱 · 写作与可视化
---

# AgentLit · 科研助手 Agent 平台（在线演示）

这是竞赛作品的 Hugging Face Spaces 演示镜像：启动时从 GitHub 拉取 `master` 源码，
以完全离线模式（`LLM_MODE=offline`、`EMBED_MODE=hashing`、`ARXIV_MODE=offline`）运行，
并自动导入 `fixtures/` 中的 14 篇合成论文 + 1 份扫描件。

- 源码仓库：<https://github.com/cui1001/ai-literature-platform>
- 技术文档：仓库 `docs/TECHNICAL_REPORT.pdf`
- 首次启动需要解析 15 个 PDF，约 60–90 秒；之后秒开
- 数据写在容器内 `/data`，**重建 Space 会重置**（演示数据会自动重新导入）
- 免费 CPU 档在 48 小时无访问后会休眠，首次访问需等待约 30–60 秒唤醒
