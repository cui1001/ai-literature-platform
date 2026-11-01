"""PDF 解析子系统。

对外接口：
- :func:`parse_pdf` —— 解析单个 PDF，返回 :class:`ParsedPaper` 并落盘；
- :func:`load_parsed` —— 读取已解析的 JSON；
- :func:`page_image_path` —— 页面渲染图路径（阅读器使用）；
- :func:`detect_duplicate` —— 语义级版本去重（内容相似度 + arXiv 版本号）。
"""

from .dedupe import DuplicateVerdict, compare_parsed, detect_duplicate
from .model import (
    BBox,
    EquationData,
    FigureData,
    ParseQuality,
    ParsedPaper,
    RefEntry,
    Section,
    TableData,
    TextBlock,
)
from .pipeline import iter_paragraphs, load_parsed, page_image_path, parse_pdf, version_hint

__all__ = [
    "BBox",
    "DuplicateVerdict",
    "EquationData",
    "FigureData",
    "ParseQuality",
    "ParsedPaper",
    "RefEntry",
    "Section",
    "TableData",
    "TextBlock",
    "compare_parsed",
    "detect_duplicate",
    "iter_paragraphs",
    "load_parsed",
    "page_image_path",
    "parse_pdf",
    "version_hint",
]
