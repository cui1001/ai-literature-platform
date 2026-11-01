"""版式感知阅读与双语对照。

* :mod:`app.reading.layout`    —— 版式 JSON（页码 / 包围盒 / 标题树）
* :mod:`app.reading.links`     —— 正文引用 ↔ References 双向跳转索引
* :mod:`app.reading.translate` —— 块级翻译、缓存、降级
* :mod:`app.reading.export`    —— Markdown / HTML / 双语对照 / 原位 PDF
* :mod:`app.reading.service`   —— 供 API 调用的门面
"""

from .layout import block_json, document_json, heading_tree, pages_json, reference_json
from .links import backlinks, citation_occurrences, link_map
from .service import citations, document, load_paper, outline, page_image_path, translate
from .service import export as export_paper
from .translate import SUPPORTED_LANGS, TranslationPiece, section_translations, translate_paper, translate_text

__all__ = [
    "SUPPORTED_LANGS",
    "TranslationPiece",
    "backlinks",
    "block_json",
    "citations",
    "citation_occurrences",
    "document",
    "document_json",
    "export_paper",
    "heading_tree",
    "link_map",
    "load_paper",
    "outline",
    "page_image_path",
    "pages_json",
    "reference_json",
    "section_translations",
    "translate",
    "translate_paper",
    "translate_text",
]
