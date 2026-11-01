"""外部能力 Provider 层。

统一封装「在线优先、离线降级」的四类能力：

* :mod:`app.providers.llm` —— 大模型（DeepSeek 等 OpenAI 兼容接口）
* :mod:`app.providers.embedding` —— 向量化（bge-m3 / 确定性哈希）
* :mod:`app.providers.ocr` —— 扫描件 OCR（Windows 内置 OCR / Tesseract / PyMuPDF）
* :mod:`app.providers.arxiv` —— 论文检索（arXiv Atom API / 本地镜像）

所有 Provider 都暴露 ``status()``，前端「系统状态」页据此展示当前实际生效的实现，
便于评审确认「同一份输入在不同环境下仍然给出可复现的输出」。
"""
