"""把已落库的论文框架导出为 Markdown / 纯文本文件。

导出落在 ``settings.artifacts_dir / "exports"``，文件名由 outline id 安全化
得到，返回真实路径与字节大小，便于前端提供下载与结果校验。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..core.config import get_settings
from ..core.errors import NotFoundError, ValidationError
from ..core.logging import get_logger
from ..db import repo

log = get_logger(__name__)

_FORMAT_ALIASES = {
    "md": "md",
    "markdown": "md",
    "txt": "txt",
    "text": "txt",
    "plain": "txt",
}
_EXTENSIONS = {"md": "md", "txt": "txt"}
_UNSAFE_ID_RE = re.compile(r"[^0-9A-Za-z_.-]+")


def _plain_text(markdown: str) -> str:
    text = re.sub(r"^#{1,6}\s*", "", markdown, flags=re.M)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def _render_from_record(outline: dict[str, Any]) -> str:
    """markdown 字段缺失时，从 sections 重建一份可导出的正文。"""
    sections = outline.get("sections") or []
    lines = [f"# {str(outline.get('idea') or 'Untitled').strip()}"]
    if isinstance(sections, dict):
        sections = [
            {"heading": heading, "text": "\n".join(value) if isinstance(value, list) else str(value)}
            for heading, value in sections.items()
        ]
    for section in sections:
        if not isinstance(section, dict):
            continue
        heading = str(section.get("heading") or section.get("key") or "").strip()
        body = str(section.get("text") or "").strip()
        if heading:
            lines.extend(["", f"## {heading}"])
        if body:
            lines.extend(["", body])
    return "\n".join(lines).strip() + "\n"


def export_outline(
    outline_id: str,
    fmt: str = "md",
    settings: Any = None,
) -> dict[str, Any]:
    """导出论文框架为 ``md`` 或 ``txt``，返回 ``{path, filename, size, format}``。"""
    normalized = _FORMAT_ALIASES.get(str(fmt or "").strip().lower())
    if normalized is None:
        raise ValidationError(f"不支持的导出格式: {fmt!r}；仅支持 md / txt")

    pid = str(outline_id or "").strip()
    outline = repo.get_outline(pid) if pid else None
    if outline is None:
        raise NotFoundError(f"论文框架不存在: {outline_id}")

    resolved = settings or get_settings()
    out_dir: Path = resolved.artifacts_dir / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)

    markdown = str(outline.get("markdown") or "").strip()
    if not markdown:
        markdown = _render_from_record(outline)
    content = markdown + "\n" if not markdown.endswith("\n") else markdown
    if normalized == "txt":
        content = _plain_text(content)

    safe_id = _UNSAFE_ID_RE.sub("-", pid).strip("-") or "outline"
    filename = f"outline-{safe_id}.{_EXTENSIONS[normalized]}"
    path = out_dir / filename
    path.write_text(content, encoding="utf-8")
    size = len(content.encode("utf-8"))
    log.info("outline %s exported to %s (%d bytes, %s)", pid, path, size, normalized)
    return {
        "path": str(path),
        "filename": filename,
        "size": size,
        "format": normalized,
    }
