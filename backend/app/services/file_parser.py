"""文件解析服务：把上传的文件统一解析为纯文本。

支持格式：.txt / .md / .pdf / .docx
新增格式只需在此扩展，upload 接口无需改动（开闭原则）。
"""
import io
import logging

from app.exceptions import ServiceError

logger = logging.getLogger(__name__)

# 支持的扩展名（供前端/接口提示）
SUPPORTED_EXTENSIONS = ("txt", "md", "pdf", "docx")


def _get_extension(filename: str) -> str:
    """取出文件扩展名（小写，不含点）。"""
    if "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower()


def _parse_pdf(content: bytes) -> str:
    """解析 PDF（复用 pypdf）。"""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(content))
    pages = [page.extract_text() or "" for page in reader.pages]
    text = "\n".join(pages).strip()
    if not text:
        raise ServiceError("PDF 中未提取到文本（可能是扫描版图片 PDF，暂不支持）")
    return text


def _parse_docx(content: bytes) -> str:
    """解析 Word 文档（复用 python-docx）。"""
    from docx import Document

    doc = Document(io.BytesIO(content))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    text = "\n".join(paragraphs).strip()
    if not text:
        raise ServiceError("Word 文档中未提取到文本")
    return text


def parse_file(filename: str, content: bytes) -> str:
    """按扩展名解析文件为纯文本。

    Args:
        filename: 原始文件名（用于判断类型）
        content: 文件二进制内容
    Returns:
        解析出的纯文本
    Raises:
        ServiceError: 格式不支持或解析失败时
    """
    ext = _get_extension(filename)

    if ext in ("txt", "md"):
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            raise ServiceError("文本文件编码不是 UTF-8，请转码后重试")
    elif ext == "pdf":
        text = _parse_pdf(content)
    elif ext == "docx":
        text = _parse_docx(content)
    else:
        raise ServiceError(
            f"不支持的文件类型 .{ext}（支持 {', '.join(SUPPORTED_EXTENSIONS)}）"
        )

    if not text.strip():
        raise ServiceError("文件中没有可提取的文本内容")

    logger.info("解析文件 %s 成功，提取 %d 字", filename, len(text))
    return text
