"""OCR Provider：为扫描件页提供文字与词级坐标。

引擎链（按可用性自动选择，结果中始终标注实际引擎）：
1. ``winrt``     —— Windows 10/11 内置 Windows.Media.Ocr（中英文取决于已装语言包）；
2. ``easyocr``   —— 纯 Python 深度学习 OCR（需 easyocr + torch；首次会下载英文模型）；
3. ``tesseract`` —— 若系统存在 tesseract 可执行文件；
4. ``pymupdf``   —— PyMuPDF 内置 OCR 入口（需 TESSDATA_PREFIX）；
5. ``none``      —— 均不可用，返回空文本并显式标记降级，绝不伪造内容。

引擎顺序按目标语言决定：英文文档优先 easyocr/tesseract（Windows 中文语言包
识别英文会产出 "ls/AII/GoogIe" 这类乱码）；中文文档优先 winrt。

所有引擎返回的词级包围盒先以「渲染图像像素」为单位，再由 :func:`ocr_page`
统一换算回 PDF 点坐标，保证高亮覆盖层与原文严格对齐。
"""

from __future__ import annotations

import re

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, Sequence

from ..core.config import Settings, get_settings
from ..core.logging import get_logger

log = get_logger(__name__)

_PS1 = Path(__file__).with_name("winocr.ps1")
_POWERSHELL = shutil.which("powershell.exe") or shutil.which("pwsh") or shutil.which("powershell")
_PROBE_LOCK = threading.Lock()
_PROBE_RESULT: dict[str, Any] | None = None


@dataclass
class OCRWord:
    text: str
    bbox: tuple[float, float, float, float]  # x0, y0, x1, y1（图像像素）
    conf: float | None = None
    line: str = ""


@dataclass
class OCRText:
    text: str
    engine: str
    words: list[OCRWord] = field(default_factory=list)
    lang: str = ""
    degraded: bool = False
    notes: str = ""
    image_width: float = 0.0
    image_height: float = 0.0
    scale: float = 1.0  # 图像像素 → PDF 点的缩放系数

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "lang": self.lang,
            "degraded": self.degraded,
            "notes": self.notes,
            "word_count": len(self.words),
            "text": self.text,
        }


class OCREngine(Protocol):
    name: str

    def available(self) -> bool: ...

    def recognize(self, png_bytes: bytes, lang: str) -> OCRText: ...


class WinRTEngine:
    """Windows 内置 OCR：通过 PowerShell 调用 WinRT，不安装任何东西。"""

    name = "winrt"

    def __init__(self, lang: str = "en-US", timeout: float = 60.0):
        self.lang = lang or "en-US"
        self.timeout = timeout

    def available(self) -> bool:
        return sys.platform == "win32" and bool(_POWERSHELL) and _PS1.is_file()

    def recognize(self, png_bytes: bytes, lang: str) -> OCRText:
        if not self.available():
            return OCRText("", self.name, degraded=True, notes="Windows.Media.Ocr 不可用")
        workdir = Path(tempfile.mkdtemp(prefix="ocr_"))
        image_path = workdir / "page.png"
        out_path = workdir / "ocr.json"
        image_path.write_bytes(png_bytes)
        cmd = [
            str(_POWERSHELL),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(_PS1),
            "-Image",
            str(image_path),
            "-Lang",
            lang or self.lang,
            "-OutFile",
            str(out_path),
        ]
        try:
            completed = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.timeout, encoding="utf-8", errors="replace"
            )
            if completed.returncode != 0 or not out_path.is_file():
                note = (completed.stderr or completed.stdout or "").strip()[:300]
                return OCRText("", self.name, degraded=True, notes=f"Windows OCR 调用失败：{note}")
            payload = json.loads(out_path.read_text(encoding="utf-8-sig"))
        except Exception as exc:  # pragma: no cover - 平台相关
            return OCRText("", self.name, degraded=True, notes=f"Windows OCR 异常：{exc}")
        finally:
            try:
                image_path.unlink(missing_ok=True)
                out_path.unlink(missing_ok=True)
                workdir.rmdir()
            except Exception:
                pass
        words = [
            OCRWord(
                text=str(item.get("text") or ""),
                bbox=(
                    float(item.get("x") or 0),
                    float(item.get("y") or 0),
                    float(item.get("x") or 0) + float(item.get("w") or 0),
                    float(item.get("y") or 0) + float(item.get("h") or 0),
                ),
                line=str(item.get("line") or ""),
            )
            for item in payload.get("words") or []
            if str(item.get("text") or "").strip()
        ]
        text = str(payload.get("text") or "").strip()
        if not text and words:
            text = " ".join(w.text for w in words)
        requested = (lang or self.lang or "en-US").lower()
        actual = str(payload.get("lang") or "").lower()
        if requested and actual and requested.split("-")[0] != actual.split("-")[0]:
            # 没有对应语言包时 WinRT 会静默回落到用户配置语言（本机是中文），
            # 英文扫描件会得到 "AII/GoogIe" 之类乱码，必须降级并交给下一个引擎。
            return OCRText(
                text=text,
                engine=self.name,
                words=words,
                lang=actual,
                degraded=True,
                notes=f"Windows OCR 缺少 {requested} 语言包，实际回落到 {actual}",
            )
        return OCRText(text=text, engine=self.name, words=words, lang=actual or requested)


class TesseractEngine:
    """可选的 tesseract 通道：系统存在二进制与语言包时启用。"""

    name = "tesseract"

    def __init__(self, lang: str = "eng", timeout: float = 60.0):
        self.lang = lang or "eng"
        self.timeout = timeout

    def available(self) -> bool:
        if not shutil.which("tesseract"):
            return False
        try:
            import pytesseract  # noqa: F401
        except Exception:
            return False
        return True

    def recognize(self, png_bytes: bytes, lang: str) -> OCRText:
        if not self.available():
            return OCRText("", self.name, degraded=True, notes="tesseract 不可用")
        try:
            import io

            import pytesseract
            from PIL import Image

            image = Image.open(io.BytesIO(png_bytes))
            data = pytesseract.image_to_data(
                image, lang=lang or self.lang, output_type=pytesseract.Output.DICT, timeout=self.timeout
            )
        except Exception as exc:
            return OCRText("", self.name, degraded=True, notes=f"tesseract 识别失败：{exc}")
        words: list[OCRWord] = []
        for i, raw in enumerate(data.get("text") or []):
            word = (raw or "").strip()
            if not word:
                continue
            conf = data["conf"][i]
            try:
                conf_value = float(conf)
            except (TypeError, ValueError):
                conf_value = None
            if conf_value is not None and conf_value < 0:
                continue
            x, y = float(data["left"][i]), float(data["top"][i])
            w, h = float(data["width"][i]), float(data["height"][i])
            words.append(OCRWord(word, (x, y, x + w, y + h), conf_value, str(data.get("line_text", [""])[i] if "line_text" in data else "")))
        text = "\n".join(str(t) for t in (data.get("text") or []) if str(t).strip())
        return OCRText(text=text, engine=self.name, words=words, lang=lang or self.lang)


class PyMuPDFEngine:
    """PyMuPDF 自带的 OCR 入口（需要外置 Tesseract 与 tessdata）。"""

    name = "pymupdf"

    def available(self) -> bool:
        try:
            import fitz  # noqa: F401
        except Exception:
            return False
        return bool(os.environ.get("TESSDATA_PREFIX")) and bool(shutil.which("tesseract"))

    def recognize(self, png_bytes: bytes, lang: str) -> OCRText:  # pragma: no cover - 环境相关
        return OCRText("", self.name, degraded=True, notes="PyMuPDF OCR 需要先注册可用的 Tesseract 环境")



class EasyOCREngine:
    """EasyOCR：英文/中文都能识别，作为 Windows 只有中文语言包时的英文兜底。

    ``available()`` 只做轻量模块探测（真正 import torch 需要十几秒），识别时再
    懒加载 Reader 并缓存。首次使用需下载识别模型（约 15 MB）。
    """

    name = "easyocr"

    def __init__(self) -> None:
        self._readers: dict[tuple[str, ...], Any] = {}
        self._lock = threading.Lock()

    def available(self) -> bool:
        import importlib.util

        return bool(importlib.util.find_spec("easyocr")) and bool(importlib.util.find_spec("torch"))

    @staticmethod
    def _langs(lang: str) -> list[str]:
        primary = (lang or "en").lower().split("-")[0].split("_")[0]
        if primary in {"zh", "ch", "chi", "cn"}:
            return ["ch_sim", "en"]
        return ["en"]

    def _reader(self, langs: list[str]) -> Any:
        key = tuple(langs)
        with self._lock:
            if key not in self._readers:
                import easyocr

                self._readers[key] = easyocr.Reader(list(key), gpu=False, verbose=False)
            return self._readers[key]

    def recognize(self, png_bytes: bytes, lang: str) -> OCRText:
        if not self.available():
            return OCRText("", self.name, degraded=True, notes="easyocr 不可用")
        try:
            import io

            import numpy as np
            from PIL import Image

            image = Image.open(io.BytesIO(png_bytes)).convert("RGB")
            reader = self._reader(self._langs(lang))
            boxes = reader.readtext(np.array(image), detail=1, paragraph=False)
        except Exception as exc:  # noqa: BLE001
            return OCRText("", self.name, degraded=True, notes=f"easyocr 识别失败：{exc}")
        # CRAFT 检测框本身就是「行/短语」级，不再按 y 聚类（否则同一水平线上的
        # 多栏邮箱会被拼成一行、包围盒横跨整页，标题字号也随之失真）。
        raw_boxes: list[tuple[float, float, float, float, float, str]] = []
        for item in boxes:
            try:
                points, text, conf = item[0], str(item[1]), float(item[2])
            except Exception:
                continue
            text = text.strip()
            if not text:
                continue
            xs = [float(p[0]) for p in points]
            ys = [float(p[1]) for p in points]
            raw_boxes.append((min(ys), min(xs), min(xs), min(ys), max(xs), max(ys), conf, text))
        # 按阅读顺序编号：先上后下、同行内先左后右
        raw_boxes.sort(key=lambda item: (round(item[0] / 8.0), item[1]))
        words: list[OCRWord] = []
        text_lines: list[str] = []
        for index, item in enumerate(raw_boxes):
            y0, x0, _x0, _y0, x1, y1, conf, text = item
            word = OCRWord(text=text, bbox=(x0, y0, x1, y1), conf=conf, line=f"L{index}")
            words.append(word)
            text_lines.append(text)
        return OCRText(text="\n".join(text_lines), engine=self.name, words=words, lang=",".join(self._langs(lang)))

_ENGINES: list[OCREngine] = [WinRTEngine(), EasyOCREngine(), TesseractEngine(), PyMuPDFEngine()]


def probe_ocr(force: bool = False) -> dict[str, Any]:
    """探测可用引擎并缓存结果（不做真实识别，只检查依赖）。"""
    global _PROBE_RESULT
    with _PROBE_LOCK:
        if _PROBE_RESULT is not None and not force:
            return _PROBE_RESULT
        available = []
        for engine in _ENGINES:
            try:
                ok = engine.available()
            except Exception:
                ok = False
            available.append({"engine": engine.name, "available": ok})
        active = next((item["engine"] for item in available if item["available"]), "none")
        _PROBE_RESULT = {
            "active": active,
            "engines": available,
            "detail": {
                "winrt": "Windows 10/11 内置 OCR（离线可用，语言取决于已装语言包）",
                "easyocr": "EasyOCR（Python 深度学习，英文/中文均可）",
                "tesseract": "系统 Tesseract 5.x + 语言包",
                "pymupdf": "PyMuPDF OCR 入口 + TESSDATA_PREFIX",
                "none": "无可用 OCR 引擎，扫描件将标记为未识别（不会伪造文本）",
            }.get(active, active),
            "powershell": _POWERSHELL or "",
        }
        return _PROBE_RESULT


def recognize_image(png_bytes: bytes, lang: str = "", engine: str | None = None) -> OCRText:
    """依次尝试引擎，返回第一个非降级结果。"""
    forced = engine or ""
    primary = (lang or "en").lower().split("-")[0]
    if primary in {"zh", "ch", "chi", "cn"}:
        preferred = ["winrt", "easyocr", "tesseract", "pymupdf"]
    else:
        preferred = ["tesseract", "easyocr", "winrt", "pymupdf"]
    chain: Sequence[OCREngine]
    if forced:
        chain = [e for e in _ENGINES if e.name == forced] or []
    else:
        chain = sorted(_ENGINES, key=lambda e: preferred.index(e.name) if e.name in preferred else 99)
    notes: list[str] = []
    for candidate in chain:
        try:
            if not candidate.available():
                continue
        except Exception:
            continue
        result = candidate.recognize(png_bytes, lang)
        if not result.degraded and (result.text or result.words):
            return result
        if result.notes:
            notes.append(f"{candidate.name}: {result.notes}")
    return OCRText("", "none", degraded=True, notes="; ".join(notes) or "无可用 OCR 引擎")


def ocr_page(page: Any, *, dpi: int = 200, lang: str = "", engine: str | None = None) -> OCRText:
    """对 PyMuPDF Page 执行 OCR，坐标换算回 PDF 点。"""
    zoom = dpi / 72.0
    pixmap = page.get_pixmap(matrix=__import__("fitz").Matrix(zoom, zoom), alpha=False)
    png = pixmap.tobytes("png")
    result = recognize_image(png, lang=lang, engine=engine)
    # OCR 常把小写点识别成独立字符（"15 . 3 percent"），数值题会因此匹配失败
    result.text = re.sub(r"(\d)\s*\.\s*(\d)", r"\1.\2", result.text or "")
    result.image_width = float(pixmap.width)
    result.image_height = float(pixmap.height)
    result.scale = 1.0 / zoom
    inverse = result.scale
    result.words = [
        OCRWord(
            text=word.text,
            bbox=(word.bbox[0] * inverse, word.bbox[1] * inverse, word.bbox[2] * inverse, word.bbox[3] * inverse),
            conf=word.conf,
            line=word.line,
        )
        for word in result.words
    ]
    return result


def ocr_status(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    info = probe_ocr()
    return {
        "active": info["active"],
        "engines": info["engines"],
        "detail": info["detail"],
        "text_threshold": settings.ocr_text_threshold,
    }


def ocr_configured(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    return probe_ocr()["active"] != "none"
