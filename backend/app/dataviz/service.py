"""学术数据可视化 Copilot 的编排层：图表构建、Caption 生成与持久化。"""

from __future__ import annotations

import re
from typing import Any

from ..core.errors import NotFoundError, ValidationError
from ..core.hashing import short_id, stable_json
from ..db import repo
from ..providers import llm as llm_provider
from .charts import (
    CHART_KINDS,
    _render_script,
    matplotlib_script,
    prepare_chart,
    render_svg,
    svg_from_spec,
)
from .dataset import get_dataset as _get_dataset

__all__ = [
    "CHART_KINDS",
    "build_chart",
    "get_chart",
    "list_charts",
    "svg_from_spec",
    "matplotlib_script",
]

_TREND_WORDS = (
    "increase",
    "increasing",
    "decrease",
    "decreasing",
    "rise",
    "rising",
    "fall",
    "falling",
    "grow",
    "growing",
    "declin",
    "peak",
    "maximum",
    "minimum",
    "cross",
    "trend",
    "上升",
    "下降",
    "增长",
    "减少",
    "峰值",
    "趋势",
)


def _has_digit(text: str) -> bool:
    return any(character.isdigit() for character in text)


def _has_trend_word(text: str) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in _TREND_WORDS)


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def _statistics(facts: dict[str, Any]) -> dict[str, Any]:
    """把内部 facts 展平为 caption 统计量（离线引擎约定的字段名）。"""
    best = facts.get("max") or {}
    worst = facts.get("min") or {}
    return {
        "trend": facts.get("trend"),
        "first": facts.get("first"),
        "last": facts.get("last"),
        "max_series": best.get("series"),
        "max_value": best.get("value"),
        "max_x": best.get("x"),
        "min_series": worst.get("series"),
        "min_value": worst.get("value"),
        "min_x": worst.get("x"),
        "crossover": facts.get("crossover"),
        "points": facts.get("points"),
        "rows": facts.get("rows"),
        "column_count": facts.get("column_count"),
        "series": list(facts.get("series") or []),
    }


def _fallback_caption(facts: dict[str, Any]) -> str:
    """确定性的学术 Caption 兜底：保证趋势结论 + 真实数字。"""
    stats = _statistics(facts)
    y_label = facts.get("y_label") or "value"
    x_label = facts.get("x_label") or "x"
    parts: list[str] = []

    trend = stats.get("trend")
    if trend is not None and stats.get("first") is not None and stats.get("last") is not None:
        direction = "increasing" if trend > 0 else ("decreasing" if trend < 0 else "flat")
        parts.append(
            f"The {y_label} shows an overall {direction} trend with {x_label}, "
            f"changing from {_fmt(stats['first'])} to {_fmt(stats['last'])} "
            f"(OLS slope {_fmt(trend)})."
        )
    elif stats.get("max_value") is not None:
        parts.append(
            f"The maximum {stats.get('max_series') or y_label} reaches "
            f"{_fmt(stats['max_value'])} at {stats.get('max_x')}, while the minimum is "
            f"{_fmt(stats.get('min_value'))} at {stats.get('min_x')}."
        )
    else:
        parts.append(
            f"The table summarises {stats.get('rows') or 0} rows and "
            f"{stats.get('column_count') or 0} columns; no numeric trend is available."
        )

    if stats.get("crossover"):
        parts.append(
            f"The two leading series cross near {x_label}={stats['crossover']}, "
            "which marks a change in their relative ordering."
        )
    if stats.get("max_value") is not None and trend is not None:
        parts.append(
            f"The peak value is {_fmt(stats['max_value'])} for {stats.get('max_series')} "
            f"at {stats.get('max_x')}."
        )
    return " ".join(parts)


def _caption_payload(plan: dict[str, Any]) -> dict[str, Any]:
    facts = plan["facts"]
    spec = plan["spec"]
    stats = _statistics(facts)
    payload = {
        "kind": spec["kind"],
        "title": spec["title"],
        "x_label": facts.get("x_label") or spec.get("x") or "x",
        "y_label": facts.get("y_label") or "value",
        "series": list(facts.get("series") or []),
        "stats": stats,
        "row_count": facts.get("rows"),
        "column_count": facts.get("column_count"),
        "note": spec.get("note") or "",
    }
    if stats.get("max_value") is not None:
        payload["max"] = {
            "series": stats.get("max_series"),
            "value": stats.get("max_value"),
            "x": stats.get("max_x"),
        }
    return payload


def _generate_caption(plan: dict[str, Any]) -> tuple[str, str, str, bool, str]:
    """返回 (caption, provider, model, degraded, notes)。"""
    payload = _caption_payload(plan)
    result_text = ""
    provider = "offline"
    model = "deterministic-v1"
    degraded = True
    notes = ""

    try:
        result = llm_provider.run_task("caption", payload)
        result_text = (result.text or "").strip()
        provider = result.provider or "offline"
        model = result.model or model
        degraded = bool(result.degraded) or provider == "offline"
        notes = result.notes or ""
    except Exception as exc:  # 生成失败不应阻断图表交付
        notes = f"caption fallback: {type(exc).__name__}: {exc}"
        degraded = True

    caption = " ".join(result_text.split())
    if not caption or not _has_digit(caption) or not _has_trend_word(caption):
        fallback = _fallback_caption(plan["facts"])
        caption = f"{caption} {fallback}".strip() if caption else fallback
    return caption, provider, model, degraded, notes


def _chart_title(chart: dict[str, Any]) -> str:
    spec = chart.get("spec") or {}
    return str(spec.get("title") or f"{chart.get('kind') or 'chart'} chart")


def build_chart(dataset_id: str, spec: dict) -> dict:
    """根据数据集与图表 spec 生成 SVG / Caption / matplotlib 脚本并入库。"""
    dataset = _get_dataset(dataset_id)
    plan = prepare_chart(dataset, spec)
    normalized = plan["spec"]
    svg = render_svg(plan)
    script = _render_script(plan)
    caption, provider, model, degraded, notes = _generate_caption(plan)
    stats = _statistics(plan["facts"])

    chart_id = short_id(
        f"chart|{dataset_id}|{normalized['kind']}|{stable_json(normalized)}", 12
    )
    repo.save_chart(
        {
            "id": chart_id,
            "dataset_id": dataset_id,
            "kind": normalized["kind"],
            "spec": normalized,
            "svg": svg,
            "caption": caption,
            "script": script,
        }
    )
    stored = repo.get_chart(chart_id) or {}
    return {
        "id": chart_id,
        "dataset_id": dataset_id,
        "kind": normalized["kind"],
        "title": normalized["title"],
        "svg": svg,
        "caption": caption,
        "script": script,
        "spec": normalized,
        "stats": stats,
        "provider": provider,
        "degraded": degraded,
        "model": model,
        "notes": notes,
        "created_at": stored.get("created_at") or "",
    }


def get_chart(chart_id: str) -> dict:
    """读取已保存的图表；缺失时抛 NotFoundError，并重算最新统计量。"""
    row = repo.get_chart(chart_id)
    if not row:
        raise NotFoundError(f"图表不存在: {chart_id}")
    spec = row.get("spec") or {}
    stats: dict[str, Any] = {}
    dataset = repo.get_dataset(row.get("dataset_id") or "")
    if dataset:
        try:
            stats = _statistics(prepare_chart(dataset, spec)["facts"])
        except ValidationError:
            stats = {}
    return {
        "id": row["id"],
        "dataset_id": row.get("dataset_id") or "",
        "kind": row.get("kind") or "line",
        "title": _chart_title(row),
        "svg": row.get("svg") or "",
        "caption": row.get("caption") or "",
        "script": row.get("script") or "",
        "spec": spec,
        "stats": stats,
        "created_at": row.get("created_at") or "",
    }


def list_charts(dataset_id: str | None = None, limit: int = 20) -> list[dict]:
    """列出图表（按创建时间倒序、同一时间按 id 稳定排序）。"""
    try:
        size = int(limit)
    except (TypeError, ValueError) as exc:
        raise ValidationError("limit 必须是整数") from exc
    if size <= 0:
        return []
    rows = repo.list_charts(10000)
    if dataset_id:
        rows = [row for row in rows if row.get("dataset_id") == dataset_id]
    rows = sorted(
        rows,
        key=lambda row: (row.get("created_at") or "", row.get("id") or ""),
        reverse=True,
    )[:size]
    out: list[dict] = []
    for row in rows:
        item = dict(row)
        item["title"] = _chart_title(repo.get_chart(row["id"]) or row)
        out.append(item)
    return out
