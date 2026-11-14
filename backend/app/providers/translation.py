"""免密钥在线机器翻译通道（整句翻译，非术语替换）。

为什么需要它：离线 ``glossary`` 只做术语对齐，读起来仍是英文，远达不到
「整篇双语阅读」的要求；在线大模型又依赖用户自己配置 API Key。这里提供
两个无需密钥的公开翻译端点作为中间档，按可用性自动选择：

1. ``google``   —— translate.googleapis.com 的 gtx 端点，整句质量最好；
2. ``mymemory`` —— api.mymemory.translated.net，匿名有每日额度，作为兜底。

设计约束（与竞赛「不虚构、可验证」一致）：

* **只翻译、不生成**：端点返回什么就写什么，不做二次改写；
* **失败可回退**：任一引擎失败即换下一个，全部失败抛 ``ProviderUnavailable``，
  由上层回落到术语级翻译并显式标注 ``degraded``；
* **负缓存**：某引擎失败后 10 分钟内不再尝试，避免整篇翻译被超时拖死；
* **公式/引用保护**：调用方（``reading.translate``）已把公式、``[n]``、数字
  替换成占位符，这里只处理自然语言。
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable

from ..core.errors import ProviderUnavailable
from ..core.logging import get_logger

logger = get_logger(__name__)

GOOGLE_URL = "https://translate.googleapis.com/translate_a/single"
MYMEMORY_URL = "https://api.mymemory.translated.net/get"
CHUNK_CHARS = 900
COOLDOWN_SECONDS = 90

_LANG = {"zh": ("auto", "zh-CN"), "en": ("auto", "en")}

#: 引擎失败时间戳（进程级负缓存）
_failed_at: dict[str, float] = {}
_last_call: dict[str, float] = {}
_throttle_lock = threading.Lock()
# MyMemory 免费端点速率限制较严（约 1 req/s），主动节流，否则批量翻译会整批失败
_MIN_INTERVAL = {"google": 0.0, "mymemory": 0.5}


@dataclass
class MTResult:
    text: str
    provider: str
    target: str
    chars: int
    latency_ms: int
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "provider": self.provider,
            "target": self.target,
            "chars": self.chars,
            "latency_ms": self.latency_ms,
            "notes": self.notes,
        }


def _chunks(text: str, limit: int = CHUNK_CHARS) -> list[str]:
    """按句子打包成不超过 ``limit`` 的片段，避免 GET URL 过长。"""
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    parts = re.split(r"(?<=[.!?。！？])\s+", text)
    chunks: list[str] = []
    buf = ""
    for part in parts:
        if not part:
            continue
        if len(buf) + len(part) + 1 <= limit:
            buf = f"{buf} {part}" if buf else part
        else:
            if buf:
                chunks.append(buf)
            buf = part[:limit]
    if buf:
        chunks.append(buf)
    return chunks


def _throttle(name: str) -> None:
    gap = _MIN_INTERVAL.get(name, 0.0)
    if gap <= 0:
        return
    with _throttle_lock:
        wait = _last_call.get(name, 0.0) + gap - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_call[name] = time.time()


def _http_json(url: str, timeout: float, headers: dict[str, str] | None = None) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - 固定 https 端点
        body = response.read()
    return json.loads(body.decode("utf-8", "replace"))


def _google(text: str, target: str, timeout: float) -> str:
    source, lang = _LANG.get(target, ("auto", target))
    pieces: list[str] = []
    for chunk in _chunks(text):
        query = urllib.parse.urlencode(
            {"client": "gtx", "sl": source, "tl": lang, "dt": "t", "q": chunk}
        )
        data = _http_json(f"{GOOGLE_URL}?{query}", timeout)
        if not isinstance(data, list) or not data or not isinstance(data[0], list):
            raise ProviderUnavailable("google: 返回结构异常")
        pieces.append("".join(seg[0] for seg in data[0] if isinstance(seg, list) and seg and seg[0]))
    translated = "".join(pieces).strip()
    if not translated:
        raise ProviderUnavailable("google: 空译文")
    return translated


def _mymemory(text: str, target: str, timeout: float) -> str:
    pair = "en|zh-CN" if target == "zh" else "zh-CN|en"
    pieces: list[str] = []
    for chunk in _chunks(text, 480):
        query = urllib.parse.urlencode({"q": chunk, "langpair": pair})
        data = _http_json(f"{MYMEMORY_URL}?{query}", timeout)
        if not isinstance(data, dict):
            raise ProviderUnavailable("mymemory: 返回结构异常")
        translated = str((data.get("responseData") or {}).get("translatedText") or "").strip()
        if not translated or translated.upper().startswith("MYMEMORY WARNING"):
            raise ProviderUnavailable(f"mymemory: 额度或内容受限（{translated[:40]}）")
        pieces.append(translated)
    return " ".join(pieces).strip()


ENGINES: tuple[tuple[str, Callable[[str, str, float], str]], ...] = (
    ("google", _google),
    ("mymemory", _mymemory),
)


def _is_cooling(name: str) -> bool:
    stamp = _failed_at.get(name, 0.0)
    return bool(stamp) and (time.time() - stamp) < COOLDOWN_SECONDS


def translate_online(text: str, target: str = "zh", *, timeout: float = 6.0) -> MTResult:
    """调用免密钥翻译端点；全部不可用时抛 ``ProviderUnavailable``。"""
    source = (text or "").strip()
    if not source:
        raise ProviderUnavailable("translation: 空文本")
    started = time.time()
    reasons: list[str] = []
    for name, engine in ENGINES:
        if _is_cooling(name):
            reasons.append(f"{name}: 冷却中")
            continue
        translated = ""
        for attempt in range(2):
            try:
                _throttle(name)
                translated = engine(source, target, timeout)
                break
            except Exception as exc:  # 网络/解析/额度问题都换下一个引擎
                msg = str(exc)
                limited = any(key in msg for key in ("429", "403", "额度", "受限", "过多"))
                logger.info("翻译引擎 %s 失败（%s）：%s", name, "限流" if limited else "错误", msg)
                if limited and attempt == 0:
                    time.sleep(2.0 + 1.5 * len(_chunks(source)))
                    continue
                # 限流只短冷却，普通故障冷却更久，避免整批翻译长时间不可用
                _failed_at[name] = time.time() - (COOLDOWN_SECONDS - 20 if limited else 0)
                reasons.append(f"{name}: {msg}")
                translated = ""
                break
        if not translated:
            continue
        _failed_at.pop(name, None)
        return MTResult(
            text=translated,
            provider=name,
            target=target,
            chars=len(source),
            latency_ms=int((time.time() - started) * 1000),
            notes="free-online-mt",
        )
    raise ProviderUnavailable("; ".join(reasons) or "translation: 无可用引擎")


def status() -> dict:
    now = time.time()
    return {
        "engines": [
            {"name": name, "available": not _is_cooling(name)}
            for name, _ in ENGINES
        ],
        "cooldown_seconds": COOLDOWN_SECONDS,
        "checked_at": now,
    }


def reset_engine_cache() -> None:
    _failed_at.clear()
