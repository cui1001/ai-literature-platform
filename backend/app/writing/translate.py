"""学术风格翻译：保护公式 / 引用 / 数字，翻译后原样还原。

流程
----
1. :func:`app.providers.glossary.protect` 把 ``$...$`` / ``\\(...\\)`` /
   ``[n]`` / 变量名 / 数字 / URL 替换成占位符，避免模型改写公式与引用编号；
2. 调用 ``academic_translate``（中→英）或 ``translate``（英→中）任务，
   在线失败自动回落离线术语引擎；
3. ``restore`` 还原占位符，并逐项确认每个受保护片段都还在译文里；在线译文
   丢失片段时改用离线译文并标记 ``degraded=True``；
4. 返回术语对照表、术语覆盖率与说明。
"""

from __future__ import annotations

import re
from typing import Any

from ..core.config import get_settings
from ..core.errors import ValidationError
from ..providers import glossary
from ..providers.offline import run_offline
from .runtime import run_task_with

#: 方向映射：source -> target
SUPPORTED_SOURCES: dict[str, str] = {"zh": "en", "en": "zh"}
_TASKS = {"zh": "academic_translate", "en": "translate"}

_PLACEHOLDER_RE = re.compile("\x01(\\d+)\x01")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_EN_WORD_RE = re.compile(r"[A-Za-z][A-Za-z\-]*")


def _missing_placeholders(raw: str, count: int) -> list[int]:
    present = {int(match.group(1)) for match in _PLACEHOLDER_RE.finditer(raw or "")}
    return [index for index in range(count) if index not in present]


def _collect_terms(
    original: str,
    used: list[str] | None = None,
) -> list[dict[str, str]]:
    """从原文与离线引擎命中的术语中整理 ``[{zh, en}]`` 对照表。"""
    pairs: dict[str, str] = {}
    lowered = original.lower()
    for zh, en in glossary.ZH_TERMS.items():
        if len(zh) >= 2 and zh in original:
            pairs.setdefault(zh, en)
    for en, zh in glossary.TERMS.items():
        if re.search(rf"(?<![a-z]){re.escape(en)}(?![a-z])", lowered):
            pairs.setdefault(zh, en)
    for term in used or []:
        text = str(term or "").strip()
        if not text:
            continue
        if text in glossary.ZH_TERMS:
            pairs.setdefault(text, glossary.ZH_TERMS[text])
        elif text in glossary.TERMS:
            pairs.setdefault(glossary.TERMS[text], text)
    ordered = sorted(pairs.items(), key=lambda item: (-len(item[0]), item[0], item[1]))
    return [{"zh": zh, "en": en} for zh, en in ordered]


def _estimate_coverage(text: str, source: str) -> float:
    try:
        if source == "zh":
            _translated, coverage, _used = glossary.translate_zh_to_en(text)
        else:
            _translated, coverage, _used = glossary.translate_en_to_zh(text)
        return round(max(0.0, min(1.0, float(coverage))), 3)
    except (TypeError, ValueError):
        return 0.0


def _coverage_of(result: Any, original: str, source: str) -> float:
    data = result.data if isinstance(result.data, dict) else {}
    raw = data.get("coverage")
    if raw is not None:
        try:
            return round(max(0.0, min(1.0, float(raw))), 3)
        except (TypeError, ValueError):
            pass
    return _estimate_coverage(original, source)


def academic_translate(
    text: str,
    *,
    source: str = "zh",
    settings: Any = None,
    style: str = "paper",
) -> dict[str, Any]:
    """学术风格中英互译；公式、引用编号与数字在译文中原样保留。"""
    original = str(text or "")
    if not original.strip():
        raise ValidationError("待翻译文本不能为空")
    direction = str(source or "").strip().lower()
    if direction not in SUPPORTED_SOURCES:
        raise ValidationError(
            f"不支持的翻译方向: {source!r}；可选 {sorted(SUPPORTED_SOURCES)}"
        )
    target = SUPPORTED_SOURCES[direction]
    resolved = settings or get_settings()
    style_text = str(style or "paper").strip() or "paper"

    protected, pieces = glossary.protect(original)
    payload: dict[str, Any] = {
        "text": protected,
        "source": direction,
        "target": target,
        "style": style_text,
        "hint": "学术论文风格；占位符 \\x01N\\x01 必须原样保留，不要翻译或删除。",
    }
    task = _TASKS[direction]
    result = run_task_with(task, payload, resolved)
    raw = result.text or ""

    missing = _missing_placeholders(raw, len(pieces))
    if missing and not result.degraded:
        fallback = run_offline(task, payload)
        fallback.degraded = True
        fallback.notes = (
            (fallback.notes + " | " if fallback.notes else "")
            + f"在线译文丢失 {len(missing)} 处受保护的公式/引用/数字，已降级离线词典翻译"
        )
        result = fallback
        raw = result.text or ""

    restored = glossary.restore(raw, pieces)
    notes: list[str] = []
    if result.notes:
        notes.append(str(result.notes))
    if pieces:
        notes.append(f"{len(pieces)} 处公式/引用/数字已保护并原样还原")
    if missing and result.degraded:
        notes.append(f"在线译文缺失占位符 {missing}，已采用离线译文")
    notes.append(f"翻译方向 {direction}->{target}；风格 {style_text}")
    if not restored.strip():
        restored = original
        notes.append("翻译结果为空，已回退为原文")
        result.degraded = True

    return {
        "text": restored,
        "provider": result.provider,
        "degraded": bool(result.degraded),
        "terms": _collect_terms(original, list(result.data.get("terms") or [])
                                if isinstance(result.data, dict) else None),
        "coverage": _coverage_of(result, original, direction),
        "notes": notes,
    }
