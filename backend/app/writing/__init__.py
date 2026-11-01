"""学术写作辅助 Copilot（竞赛模块六之一）。

模块划分
--------
* :mod:`app.writing.outline`   —— 论文框架生成、引用真实性校验、落库
* :mod:`app.writing.diagram`   —— Mermaid / Graphviz / TikZ / Matplotlib 脚本文本
* :mod:`app.writing.translate` —— 学术风格中英互译（公式 / 引用 / 数字保护）
* :mod:`app.writing.export`    —— Markdown / 纯文本导出到 ``artifacts/exports``

三条硬约束（与竞赛评审口径一致）：

1. **参考文献只能来自本地文献库**：编号引用由 ``papers`` 表真实元数据生成，
   正文引用编号与参考文献条目做双向校验，绝不编造条目；
2. **无外部渲染器依赖**：图形能力只生成脚本文本，不调用 graphviz / pdflatex /
   matplotlib，同一输入脚本字节级确定；
3. **在线优先、离线兜底**：LLM 不可用时自动回落到确定性离线引擎并显式标记
   ``degraded=True``，不把降级结果伪装成完整能力。
"""

from __future__ import annotations

from .diagram import SUPPORTED_KINDS, diagram
from .export import export_outline
from .outline import draft_outline, get_outline, list_outlines
from .translate import SUPPORTED_SOURCES, academic_translate

__all__ = [
    "SUPPORTED_KINDS",
    "SUPPORTED_SOURCES",
    "academic_translate",
    "diagram",
    "draft_outline",
    "export_outline",
    "get_outline",
    "list_outlines",
]
