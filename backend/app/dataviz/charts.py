"""确定性 SVG 图表渲染与 matplotlib 复现脚本。

约束
----
- 不依赖 matplotlib / numpy / pandas：SVG 由字符串直接拼装；
- 坐标统一保留 2 位小数，画布固定 720×420；
- 所有几何、颜色、刻度、图例都由输入唯一决定，同一输入得到同一字节。
"""

from __future__ import annotations

import html
import math
import pprint
import statistics
from typing import Any

from ..core.errors import ValidationError
from .dataset import TYPE_NUMBER, parse_number, dataset_view

CHART_KINDS = ("line", "bar", "scatter", "radar3d", "table")

WIDTH = 720
HEIGHT = 420
PLOT_LEFT = 64.0
PLOT_RIGHT = 696.0
PLOT_TOP = 64.0
PLOT_BOTTOM = 364.0
FONT = "Arial, Helvetica, sans-serif"

PALETTE = (
    "#2563eb",
    "#dc2626",
    "#059669",
    "#d97706",
    "#7c3aed",
    "#0891b2",
    "#db2777",
    "#4d7c0f",
)

KIND_LABELS = {
    "line": "折线图",
    "bar": "柱状图",
    "scatter": "散点图",
    "radar3d": "三维雷达图",
    "table": "数据表",
}


# ------------------------------------------------------------------ 数值 / 文本
def _f(value: float) -> str:
    number = float(value)
    if abs(number) < 0.005:
        number = 0.0
    return f"{number:.2f}"


def _round(value: float | None) -> float | None:
    if value is None:
        return None
    out = round(float(value), 6)
    return 0.0 if out == 0 else out


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    return parse_number(value)


def _label(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _num_text(value)
    return str(value)


def _num_text(value: Any) -> str:
    number = _number(value)
    if number is None:
        return _label(value) if not isinstance(value, (int, float)) else ""
    if abs(number - round(number)) < 1e-9:
        return str(int(round(number)))
    text = f"{number:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if not math.isfinite(number):
            return ""
        if abs(number - round(number)) < 1e-9:
            return str(int(round(number)))
        return f"{number:.4f}".rstrip("0").rstrip(".")
    return str(value)


def _esc(text: Any) -> str:
    return html.escape(str(text), quote=True)


def _truncate(text: Any, limit: int) -> str:
    value = str(text)
    return value if len(value) <= limit else value[: max(0, limit - 1)] + "…"


def _dedupe(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return out


# ------------------------------------------------------------------ 刻度 / 比例
def _nice_ticks(low: float, high: float, count: int = 5) -> list[float]:
    if not math.isfinite(low) or not math.isfinite(high):
        return [0.0]
    if high < low:
        low, high = high, low
    if high - low < 1e-12:
        return [round(low, 10)]
    raw = (high - low) / max(1, count - 1)
    magnitude = 10.0 ** math.floor(math.log10(raw))
    normal = raw / magnitude
    for candidate in (1.0, 2.0, 2.5, 5.0, 10.0):
        if normal <= candidate:
            step = candidate * magnitude
            break
    else:  # pragma: no cover - 上面的循环总会命中
        step = 10.0 * magnitude
    start = math.ceil(low / step - 1e-9) * step
    ticks: list[float] = []
    value = start
    while value <= high + step * 1e-6 and len(ticks) < 40:
        ticks.append(round(value, 10))
        value += step
    if len(ticks) < 2:
        ticks = [round(low, 10), round(high, 10)]
    return ticks


def _scale(low: float, high: float, pixel_low: float, pixel_high: float):
    if high - low < 1e-12:
        high = low + 1.0

    def convert(value: float) -> float:
        return pixel_low + (float(value) - low) / (high - low) * (pixel_high - pixel_low)

    return convert


def _value_range(values: list[float], *, pad: float = 0.08) -> tuple[float, float]:
    clean = [value for value in values if value is not None and math.isfinite(value)]
    if not clean:
        return 0.0, 1.0
    low, high = min(clean), max(clean)
    if high - low < 1e-12:
        gap = 1.0 if abs(high) < 1e-12 else abs(high) * 0.1
        return low - gap, high + gap
    margin = (high - low) * pad
    return low - margin, high + margin


def _bar_range(values: list[float]) -> tuple[float, float]:
    clean = [value for value in values if value is not None and math.isfinite(value)]
    if not clean:
        return 0.0, 1.0
    low, high = min(0.0, min(clean)), max(0.0, max(clean))
    if high - low < 1e-12:
        return 0.0, 1.0
    return low, high + (high - low) * 0.08


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


# ------------------------------------------------------------------ 规格解析
def _column_arg(raw: Any, by_name: dict[str, dict[str, Any]], field: str) -> str | None:
    if raw is None or raw == "":
        return None
    name = str(raw).strip()
    if name not in by_name:
        raise ValidationError(f"spec.{field} 引用了不存在的列: {name}")
    return name


def _series_arg(raw: Any, by_name: dict[str, dict[str, Any]]) -> list[str]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        items = [raw]
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        raise ValidationError("spec.series 必须是列名数组")
    names: list[str] = []
    for item in items:
        name = str(item).strip()
        if not name:
            continue
        if name not in by_name:
            raise ValidationError(f"spec.series 引用了不存在的列: {name}")
        if name not in names:
            names.append(name)
    return names


def _metric_score(name: str, by_name: dict[str, dict[str, Any]], rows: list[list[Any]]) -> float:
    index = int(by_name[name]["index"])
    values = [number for row in rows if index < len(row) for number in [_number(row[index])] if number is not None]
    if len(values) < 2:
        return 0.0
    span = max(values) - min(values)
    center = abs(sum(values) / len(values))
    return span / (center + 1e-9)


def _default_metric(
    numeric: list[str],
    by_name: dict[str, dict[str, Any]],
    rows: list[list[Any]],
    exclude: str | None = None,
) -> str | None:
    candidates = [name for name in numeric if name != exclude] or list(numeric)
    best: str | None = None
    best_score = -1.0
    for name in candidates:
        score = _metric_score(name, by_name, rows)
        if score > best_score + 1e-12:
            best, best_score = name, score
    return best


def normalize_spec(dataset: dict[str, Any], spec: dict[str, Any] | None) -> dict[str, Any]:
    """把用户 spec 补齐为可渲染的确定性规格。"""
    view = dataset_view(dataset)
    columns: list[dict[str, Any]] = view["columns"]
    rows: list[list[Any]] = view["rows"]
    by_name = {column["name"]: column for column in columns}
    names = [column["name"] for column in columns]
    numeric = [column["name"] for column in columns if column["type"] == TYPE_NUMBER]

    if spec is None:
        spec = {}
    if not isinstance(spec, dict):
        raise ValidationError("spec 必须是对象")

    kind = str(spec.get("kind") or "line").strip().lower()
    if kind not in CHART_KINDS:
        raise ValidationError(f"不支持的图表类型: {kind}（可选: {', '.join(CHART_KINDS)}）")

    x = _column_arg(spec.get("x"), by_name, "x")
    y = _column_arg(spec.get("y"), by_name, "y")
    requested_series = _series_arg(spec.get("series"), by_name)
    requested_axes = _series_arg(spec.get("radar_axes"), by_name)

    raw_limit = spec.get("limit")
    try:
        limit = 200 if raw_limit is None or raw_limit == "" else int(raw_limit)
    except (TypeError, ValueError) as exc:
        raise ValidationError("spec.limit 必须是正整数") from exc
    if limit <= 0:
        raise ValidationError("spec.limit 必须是正整数")
    raw_bins = spec.get("bins")
    try:
        bins = 0 if raw_bins is None or raw_bins == "" else int(raw_bins)
    except (TypeError, ValueError) as exc:
        raise ValidationError("spec.bins 必须是非负整数") from exc
    if bins < 0:
        raise ValidationError("spec.bins 必须是非负整数")

    series: list[str] = []
    radar_axes: list[str] = []
    y_out = y

    if kind == "radar3d":
        if requested_axes:
            axes = requested_axes
        elif len(numeric) >= 3:
            axes = numeric
        else:
            axes = requested_series
        if len(axes) < 3:
            raise ValidationError("radar3d 至少需要 3 个数值列作为雷达轴")
        for name in axes:
            if by_name[name]["type"] != TYPE_NUMBER:
                raise ValidationError(f"radar_axes 必须是数值列: {name}")
        radar_axes = axes
        series = list(axes)
        if x is None:
            leftovers = [name for name in numeric if name not in radar_axes]
            if leftovers:
                x = leftovers[0]
            else:
                textual = [name for name in names if by_name[name]["type"] != TYPE_NUMBER]
                x = textual[0] if textual else None
        y_out = None
    elif kind == "table":
        picked: list[str] = []
        if x:
            picked.append(x)
        if y and y not in picked:
            picked.append(y)
        for name in requested_series:
            if name not in picked:
                picked.append(name)
        if not picked:
            picked = names[:8]
        picked = picked[:8]
        series = [name for name in picked if by_name[name]["type"] == TYPE_NUMBER]
        y_out = y
    else:
        series_input = [name for name in requested_series]
        if y and y not in series_input:
            series_input = [y] + series_input
        if not series_input:
            metric = _default_metric(numeric, by_name, rows, exclude=x)
            series_input = [metric] if metric else []
        for name in series_input:
            if by_name[name]["type"] != TYPE_NUMBER:
                raise ValidationError(f"图表 y 轴/系列必须是数值列: {name}")
        series = series_input
        if x is None:
            if kind == "bar":
                textual = [name for name in names if by_name[name]["type"] != TYPE_NUMBER and name not in series]
                candidates = textual or [name for name in names if name not in series]
            elif kind == "scatter":
                candidates = [name for name in numeric if name not in series]
            else:
                candidates = [name for name in numeric if name not in series] or [
                    name for name in names if name not in series
                ]
            if not candidates and kind == "scatter":
                raise ValidationError("散点图需要数值型 x 列")
            x = candidates[0] if candidates else (names[0] if names else None)
        if kind == "scatter" and (x is None or by_name[x]["type"] != TYPE_NUMBER):
            raise ValidationError(f"散点图 x 轴必须是数值列: {x}")
        if not series:
            raise ValidationError("没有可用于绘图的数值列")
        if len(series) == 1:
            y_out = series[0]

    title = str(spec.get("title") or "").strip()
    if not title:
        if x and series and kind != "table":
            title = f"{', '.join(series[:3])} vs {x}"
        elif series:
            title = f"{', '.join(series[:3])} · {KIND_LABELS[kind]}"
        else:
            title = f"{view['name']} · {KIND_LABELS[kind]}"
    note = str(spec.get("note") or "").strip()

    return {
        "kind": kind,
        "title": title,
        "x": x,
        "y": y_out,
        "series": series,
        "bins": bins,
        "radar_axes": radar_axes,
        "limit": limit,
        "note": note,
    }


# ------------------------------------------------------------------ 数据准备
def _series_indexes(series: list[str], by_name: dict[str, dict[str, Any]]) -> dict[str, int]:
    return {name: int(by_name[name]["index"]) for name in series}


def _row_mean(rows: list[list[Any]], index: int) -> float | None:
    values = [number for row in rows for number in [_number(row[index] if index < len(row) else None)] if number is not None]
    return _round(_mean(values))


def _group_mean(rows: list[list[Any]], index: int) -> float | None:
    values: list[float] = []
    for row in rows:
        number = _number(row[index] if index < len(row) else None)
        if number is not None:
            values.append(number)
    return _round(_mean(values)) if values else None


def _slope(pairs: list[tuple[float, float]]) -> float | None:
    if len(pairs) < 2:
        return None
    count = float(len(pairs))
    mean_x = sum(pair[0] for pair in pairs) / count
    mean_y = sum(pair[1] for pair in pairs) / count
    denominator = sum((pair[0] - mean_x) ** 2 for pair in pairs)
    if denominator <= 1e-12:
        return 0.0
    numerator = sum((pair[0] - mean_x) * (pair[1] - mean_y) for pair in pairs)
    return _round(numerator / denominator)


def _series_facts(
    view: dict[str, Any],
    spec: dict[str, Any],
    x_labels: list[str],
    x_numeric: list[float] | None,
    series_values: dict[str, list[float | None]],
) -> dict[str, Any]:
    names = list(series_values)
    primary = names[0] if names else None
    points = len(x_labels)

    pairs: list[tuple[float, float]] = []
    if primary and x_numeric is not None:
        for index, value in enumerate(series_values[primary]):
            if value is None:
                continue
            pairs.append((float(x_numeric[index]), float(value)))
    trend = _slope(pairs) if len(pairs) >= 2 else None

    first: float | None = None
    last: float | None = None
    if primary:
        for value in series_values[primary]:
            if value is not None:
                first = float(value)
                break
        for value in reversed(series_values[primary]):
            if value is not None:
                last = float(value)
                break

    best: dict[str, Any] | None = None
    worst: dict[str, Any] | None = None
    for name in names:
        for index, value in enumerate(series_values[name]):
            if value is None:
                continue
            value = float(value)
            if best is None or value > best["value"]:
                best = {"series": name, "x": x_labels[index] if index < len(x_labels) else "", "value": value}
            if worst is None or value < worst["value"]:
                worst = {"series": name, "x": x_labels[index] if index < len(x_labels) else "", "value": value}

    crossover: str | None = None
    if len(names) >= 2:
        left, right = series_values[names[0]], series_values[names[1]]
        previous: float | None = None
        for index in range(min(len(left), len(right))):
            a, b = left[index], right[index]
            if a is None or b is None:
                continue
            difference = float(a) - float(b)
            if abs(difference) < 1e-12:
                crossover = x_labels[index]
                break
            if previous is not None and (previous < 0 < difference or difference < 0 < previous):
                crossover = x_labels[index]
                break
            previous = difference

    return {
        "kind": spec["kind"],
        "x_label": spec.get("x") or "index",
        "y_label": primary if len(names) <= 1 else "value",
        "series": names,
        "points": points,
        "rows": len(view["rows"]),
        "column_count": len(view["columns"]),
        "trend": trend,
        "first": _round(first),
        "last": _round(last),
        "max": None if best is None else {**best, "value": _round(best["value"])},
        "min": None if worst is None else {**worst, "value": _round(worst["value"])},
        "crossover": crossover,
    }


def _prepare_line(view: dict[str, Any], spec: dict[str, Any], *, scatter: bool = False) -> dict[str, Any]:
    columns = view["columns"]
    by_name = {column["name"]: column for column in columns}
    rows = view["rows"]
    x_column = by_name[spec["x"]]
    x_index = int(x_column["index"])
    series_index = _series_indexes(spec["series"], by_name)
    limit = int(spec["limit"])

    if scatter:
        points_raw: list[tuple[float, dict[str, float | None]]] = []
        for row in rows:
            x_value = _number(row[x_index] if x_index < len(row) else None)
            if x_value is None:
                continue
            values = {
                name: _number(row[index] if index < len(row) else None)
                for name, index in series_index.items()
            }
            points_raw.append((x_value, values))
        points_raw.sort(key=lambda item: item[0])
        points_raw = points_raw[:limit]
        x_values = [point[0] for point in points_raw]
        series_values = {
            name: [point[1][name] for point in points_raw]
            for name in spec["series"]
        }
        x_labels = [_num_text(value) for value in x_values]
        all_y = [value for values in series_values.values() for value in values if value is not None]
        facts = _series_facts(view, spec, x_labels, x_values, series_values)
        return {
            "x_name": spec["x"],
            "x_numeric": True,
            "x_values": x_values,
            "x_labels": x_labels,
            "series_values": series_values,
            "all_y": all_y,
            "facts": facts,
        }

    groups: dict[Any, dict[str, Any]] = {}
    order: list[Any] = []
    x_numeric = x_column["type"] == TYPE_NUMBER
    for row in rows:
        raw = row[x_index] if x_index < len(row) else None
        if raw is None or str(raw).strip() == "":
            continue
        if x_numeric:
            key = _number(raw)
            if key is None:
                continue
        else:
            key = str(raw)
        if key not in groups:
            groups[key] = {"key": key, "rows": []}
            order.append(key)
        groups[key]["rows"].append(row)

    keys = sorted(groups) if x_numeric else order
    keys = keys[:limit]
    x_values = [float(key) for key in keys] if x_numeric else None
    x_labels = [_num_text(key) if x_numeric else str(key) for key in keys]
    series_values = {
        name: [_group_mean(groups[key]["rows"], index) for key in keys]
        for name, index in series_index.items()
    }
    all_y = [value for values in series_values.values() for value in values if value is not None]
    facts = _series_facts(view, spec, x_labels, x_values, series_values)
    return {
        "x_name": spec["x"],
        "x_numeric": bool(x_numeric),
        "x_values": x_values,
        "x_labels": x_labels,
        "series_values": series_values,
        "all_y": all_y,
        "facts": facts,
    }


def _prepare_bar(view: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    columns = view["columns"]
    by_name = {column["name"]: column for column in columns}
    rows = view["rows"]
    x_column = by_name[spec["x"]]
    x_index = int(x_column["index"])
    series_index = _series_indexes(spec["series"], by_name)
    limit = int(spec["limit"])
    bins = int(spec["bins"])
    x_numeric = x_column["type"] == TYPE_NUMBER

    groups: dict[Any, dict[str, Any]] = {}
    order: list[Any] = []
    numeric_values: list[float | None] = []

    if x_numeric and bins > 0:
        raw_values = [
            _number(row[x_index] if x_index < len(row) else None) for row in rows
        ]
        clean = [value for value in raw_values if value is not None]
        if clean:
            low, high = min(clean), max(clean)
            span = high - low
            bin_count = max(1, bins)
            for row, number in zip(rows, raw_values):
                if number is None:
                    continue
                if span <= 1e-12:
                    position = 0
                else:
                    position = min(bin_count - 1, int((number - low) / span * bin_count))
                if position not in groups:
                    groups[position] = {"key": position, "rows": []}
                    order.append(position)
                groups[position]["rows"].append(row)
            ordered = sorted(groups)
            labels: list[str] = []
            centers: list[float] = []
            for position in ordered:
                start = low + span * position / bin_count
                end = low + span * (position + 1) / bin_count
                labels.append(f"{_num_text(start)}–{_num_text(end)}")
                centers.append((start + end) / 2)
            series_values = {
                name: [_group_mean(groups[position]["rows"], index) for position in ordered]
                for name, index in series_index.items()
            }
            all_y = [value for values in series_values.values() for value in values if value is not None]
            facts = _series_facts(view, spec, labels, centers, series_values)
            return {
                "x_name": spec["x"],
                "x_labels": labels,
                "series_values": series_values,
                "all_y": all_y,
                "facts": facts,
            }

    for row in rows:
        raw = row[x_index] if x_index < len(row) else None
        if raw is None or str(raw).strip() == "":
            continue
        if x_numeric:
            key = _number(raw)
            if key is None:
                continue
        else:
            key = str(raw)
        if key not in groups:
            groups[key] = {"key": key, "rows": []}
            order.append(key)
        groups[key]["rows"].append(row)

    keys = sorted(groups) if x_numeric else order
    keys = keys[:limit]
    x_labels = [_num_text(key) if x_numeric else str(key) for key in keys]
    centers = [float(key) for key in keys] if x_numeric else None
    series_values = {
        name: [_group_mean(groups[key]["rows"], index) for key in keys]
        for name, index in series_index.items()
    }
    all_y = [value for values in series_values.values() for value in values if value is not None]
    facts = _series_facts(view, spec, x_labels, centers, series_values)
    return {
        "x_name": spec["x"],
        "x_labels": x_labels,
        "series_values": series_values,
        "all_y": all_y,
        "facts": facts,
    }


def _prepare_radar(view: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    columns = view["columns"]
    by_name = {column["name"]: column for column in columns}
    rows = view["rows"]
    axes = list(spec["radar_axes"])
    x_index = int(by_name[spec["x"]]["index"]) if spec.get("x") else None
    limit = int(spec["limit"])

    ranges: dict[str, tuple[float, float]] = {}
    for name in axes:
        index = int(by_name[name]["index"])
        values = [
            number
            for row in rows
            for number in [_number(row[index] if index < len(row) else None)]
            if number is not None
        ]
        if values:
            ranges[name] = (min(values), max(values))
        else:
            ranges[name] = (0.0, 1.0)

    selected = rows[: max(1, min(limit, 8))]
    layer_labels: list[str] = []
    normalized: list[list[float]] = []
    for position, row in enumerate(selected):
        if x_index is not None:
            layer_labels.append(_label(row[x_index] if x_index < len(row) else ""))
        else:
            layer_labels.append(f"Row {position + 1}")
        values: list[float] = []
        for name in axes:
            index = int(by_name[name]["index"])
            raw = _number(row[index] if index < len(row) else None)
            low, high = ranges[name]
            if raw is None:
                values.append(0.0)
            elif high - low < 1e-12:
                values.append(0.5)
            else:
                values.append(min(1.0, max(0.0, (raw - low) / (high - low))))
        normalized.append(values)

    offsets: list[tuple[float, float]] = []
    count = len(selected)
    for position in range(count):
        shift = position - (count - 1) / 2.0
        offsets.append((_round(shift * 6.0) or 0.0, _round(-shift * 4.0) or 0.0))

    best: dict[str, Any] | None = None
    worst: dict[str, Any] | None = None
    for layer_index, values in enumerate(normalized):
        for axis_index, value in enumerate(values):
            if best is None or value > best["value"]:
                best = {"series": axes[axis_index], "x": layer_labels[layer_index], "value": value}
            if worst is None or value < worst["value"]:
                worst = {"series": axes[axis_index], "x": layer_labels[layer_index], "value": value}

    facts = {
        "kind": spec["kind"],
        "x_label": spec.get("x") or "layer",
        "y_label": "normalized score",
        "series": axes,
        "points": count,
        "rows": len(rows),
        "column_count": len(columns),
        "trend": None,
        "first": None,
        "last": None,
        "max": None if best is None else {**best, "value": _round(best["value"])},
        "min": None if worst is None else {**worst, "value": _round(worst["value"])},
        "crossover": None,
    }
    return {
        "axes": axes,
        "layers": layer_labels,
        "normalized": normalized,
        "offsets": offsets,
        "ranges": ranges,
        "series_values": {name: [normalized[i][j] for i in range(count)] for j, name in enumerate(axes)},
        "all_y": [0.0, 1.0],
        "facts": facts,
    }


def _prepare_table(view: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    by_name = {column["name"]: column for column in view["columns"]}
    names = [column["name"] for column in view["columns"]]
    rows = view["rows"]
    picked: list[str] = []
    if spec.get("x"):
        picked.append(spec["x"])
    if spec.get("y") and spec["y"] not in picked:
        picked.append(spec["y"])
    for name in spec.get("series") or []:
        if name not in picked:
            picked.append(name)
    if not picked:
        picked = names
    total_columns = len(picked)
    picked = picked[:8]

    limit = max(1, min(int(spec["limit"]), 12))
    shown = rows[:limit]
    numeric_columns = [name for name in picked if by_name[name]["type"] == TYPE_NUMBER]

    best: dict[str, Any] | None = None
    worst: dict[str, Any] | None = None
    for column in picked:
        if by_name[column]["type"] != TYPE_NUMBER:
            continue
        index = int(by_name[column]["index"])
        for row_index, row in enumerate(shown):
            value = _number(row[index] if index < len(row) else None)
            if value is None:
                continue
            label = f"row {row_index + 1}"
            if best is None or value > best["value"]:
                best = {"series": column, "x": label, "value": value}
            if worst is None or value < worst["value"]:
                worst = {"series": column, "x": label, "value": value}

    facts = {
        "kind": spec["kind"],
        "x_label": spec.get("x") or "row",
        "y_label": "value",
        "series": numeric_columns,
        "points": len(shown),
        "rows": len(rows),
        "column_count": len(names),
        "trend": None,
        "first": None,
        "last": None,
        "max": None if best is None else {**best, "value": _round(best["value"])},
        "min": None if worst is None else {**worst, "value": _round(worst["value"])},
        "crossover": None,
    }
    return {
        "columns": picked,
        "rows": shown,
        "total_rows": len(rows),
        "total_columns": total_columns,
        "facts": facts,
    }


def prepare_chart(dataset: dict[str, Any], spec: dict[str, Any] | None = None) -> dict[str, Any]:
    """解析 spec、准备绘图数据并计算 caption 所需的统计事实（纯函数）。"""
    view = dataset_view(dataset)
    normalized = normalize_spec(view, spec)
    kind = normalized["kind"]
    if kind == "line":
        prepared = _prepare_line(view, normalized)
    elif kind == "scatter":
        prepared = _prepare_line(view, normalized, scatter=True)
    elif kind == "bar":
        prepared = _prepare_bar(view, normalized)
    elif kind == "radar3d":
        prepared = _prepare_radar(view, normalized)
    else:
        prepared = _prepare_table(view, normalized)
    return {
        "view": view,
        "spec": normalized,
        "prepared": prepared,
        "facts": prepared["facts"],
    }


# ------------------------------------------------------------------ SVG 组件
def _svg_start(parts: list[str], title: str) -> None:
    safe_title = _truncate(title, 80)
    parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-label="{_esc(safe_title)}">'
    )
    parts.append(f"<title>{_esc(safe_title)}</title>")
    parts.append(f'<rect x="0.00" y="0.00" width="{WIDTH}.00" height="{HEIGHT}.00" fill="#ffffff"/>')
    parts.append(
        f'<text x="{_f(WIDTH / 2)}" y="28.00" text-anchor="middle" font-family="{FONT}" '
        f'font-size="16" font-weight="600" fill="#111827">{_esc(safe_title)}</text>'
    )


def _svg_end(parts: list[str]) -> None:
    parts.append("</svg>")


def _note(parts: list[str], note: str, y: float = 410.0) -> None:
    if not note:
        return
    parts.append(
        f'<text x="{_f(WIDTH / 2)}" y="{_f(y)}" text-anchor="middle" font-family="{FONT}" '
        f'font-size="9" fill="#6b7280">{_esc(_truncate(note, 110))}</text>'
    )


def _legend(parts: list[str], names: list[str], right: float, top: float) -> None:
    names = [name for name in names if name][:8]
    if not names:
        return
    longest = max(len(str(name)) for name in names)
    width = max(92.0, 24.0 + longest * 6.4)
    height = 10.0 + len(names) * 16.0
    left = right - width
    parts.append('<g class="legend">')
    parts.append(
        f'<rect x="{_f(left)}" y="{_f(top)}" width="{_f(width)}" height="{_f(height)}" rx="4.00" '
        'fill="#ffffff" fill-opacity="0.92" stroke="#e5e7eb" stroke-width="1.00"/>'
    )
    for index, name in enumerate(names):
        color = PALETTE[index % len(PALETTE)]
        row_y = top + 8.0 + index * 16.0
        parts.append(
            f'<rect x="{_f(left + 8.0)}" y="{_f(row_y)}" width="9.00" height="9.00" fill="{color}"/>'
        )
        parts.append(
            f'<text x="{_f(left + 22.0)}" y="{_f(row_y + 8.5)}" font-family="{FONT}" font-size="10" '
            f'fill="#374151">{_esc(_truncate(name, 16))}</text>'
        )
    parts.append("</g>")


def _axis_labels(parts: list[str], x_label: str, y_label: str) -> None:
    if x_label:
        parts.append(
            f'<text x="{_f((PLOT_LEFT + PLOT_RIGHT) / 2)}" y="398.00" text-anchor="middle" '
            f'font-family="{FONT}" font-size="11" fill="#374151">{_esc(_truncate(x_label, 40))}</text>'
        )
    if y_label:
        center = (PLOT_TOP + PLOT_BOTTOM) / 2.0
        parts.append(
            f'<text x="16.00" y="{_f(center)}" text-anchor="middle" transform="rotate(-90 16.00 {_f(center)})" '
            f'font-family="{FONT}" font-size="11" fill="#374151">{_esc(_truncate(y_label, 40))}</text>'
        )


def _draw_y_grid(
    parts: list[str],
    ticks: list[float],
    scale,
) -> None:
    parts.append('<g class="grid">')
    for tick in ticks:
        y = scale(tick)
        parts.append(
            f'<line x1="{_f(PLOT_LEFT)}" y1="{_f(y)}" x2="{_f(PLOT_RIGHT)}" y2="{_f(y)}" '
            'stroke="#e5e7eb" stroke-width="1.00"/>'
        )
    parts.append("</g>")
    for tick in ticks:
        y = scale(tick)
        parts.append(
            f'<text x="{_f(PLOT_LEFT - 9.0)}" y="{_f(y + 3.5)}" text-anchor="end" font-family="{FONT}" '
            f'font-size="10" fill="#6b7280">{_esc(_num_text(tick))}</text>'
        )


def _draw_axis_frame(parts: list[str], x_label_name: str | None) -> None:
    parts.append(
        f'<line x1="{_f(PLOT_LEFT)}" y1="{_f(PLOT_TOP)}" x2="{_f(PLOT_LEFT)}" y2="{_f(PLOT_BOTTOM)}" '
        'stroke="#9ca3af" stroke-width="1.00"/>'
    )
    parts.append(
        f'<line x1="{_f(PLOT_LEFT)}" y1="{_f(PLOT_BOTTOM)}" x2="{_f(PLOT_RIGHT)}" y2="{_f(PLOT_BOTTOM)}" '
        'stroke="#9ca3af" stroke-width="1.00"/>'
    )


def _draw_numeric_x_axis(parts: list[str], values: list[float]) -> list[float]:
    unique = sorted({round(value, 9) for value in values})
    ticks = unique if len(unique) <= 12 else _nice_ticks(min(unique), max(unique), 5)
    scale = _scale(min(unique), max(unique), PLOT_LEFT, PLOT_RIGHT)
    positions = [scale(value) for value in values]
    for tick in ticks:
        x = scale(tick)
        parts.append(
            f'<line x1="{_f(x)}" y1="{_f(PLOT_BOTTOM)}" x2="{_f(x)}" y2="{_f(PLOT_BOTTOM + 4.0)}" '
            'stroke="#9ca3af" stroke-width="1.00"/>'
        )
        parts.append(
            f'<text x="{_f(x)}" y="{_f(PLOT_BOTTOM + 16.0)}" text-anchor="middle" font-family="{FONT}" '
            f'font-size="10" fill="#6b7280">{_esc(_num_text(tick))}</text>'
        )
    return positions


def _draw_category_x_axis(parts: list[str], labels: list[str]) -> list[float]:
    count = len(labels)
    if count == 0:
        return []
    if count == 1:
        positions = [(PLOT_LEFT + PLOT_RIGHT) / 2.0]
    else:
        step = (PLOT_RIGHT - PLOT_LEFT) / (count - 1)
        positions = [PLOT_LEFT + index * step for index in range(count)]
    stride = max(1, math.ceil(count / 8))
    shown = list(range(0, count, stride))
    if count - 1 not in shown:
        shown.append(count - 1)
    for index in shown:
        x = positions[index]
        parts.append(
            f'<line x1="{_f(x)}" y1="{_f(PLOT_BOTTOM)}" x2="{_f(x)}" y2="{_f(PLOT_BOTTOM + 4.0)}" '
            'stroke="#9ca3af" stroke-width="1.00"/>'
        )
        parts.append(
            f'<text x="{_f(x)}" y="{_f(PLOT_BOTTOM + 16.0)}" text-anchor="middle" font-family="{FONT}" '
            f'font-size="10" fill="#6b7280">{_esc(_truncate(labels[index], 12))}</text>'
        )
    return positions


# ------------------------------------------------------------------ SVG 渲染
def _render_line(plan: dict[str, Any], *, scatter: bool = False) -> str:
    spec = plan["spec"]
    prepared = plan["prepared"]
    parts: list[str] = []
    _svg_start(parts, spec["title"])

    y_low, y_high = _value_range(prepared["all_y"])
    y_scale = _scale(y_low, y_high, PLOT_BOTTOM, PLOT_TOP)
    y_ticks = _nice_ticks(y_low, y_high, 5)
    _draw_y_grid(parts, y_ticks, y_scale)
    _draw_axis_frame(parts, spec.get("x"))

    if prepared["x_numeric"]:
        positions = _draw_numeric_x_axis(parts, prepared["x_values"])
    else:
        positions = _draw_category_x_axis(parts, prepared["x_labels"])

    for series_index, name in enumerate(prepared["series_values"]):
        color = PALETTE[series_index % len(PALETTE)]
        values = prepared["series_values"][name]
        if scatter:
            for position, value in zip(positions, values):
                if value is None:
                    continue
                parts.append(
                    f'<circle cx="{_f(position)}" cy="{_f(y_scale(value))}" r="3.20" fill="{color}" '
                    'fill-opacity="0.82" stroke="#ffffff" stroke-width="1.00"/>'
                )
            continue
        segment: list[tuple[float, float]] = []
        for position, value in zip(positions, values):
            if value is None:
                if len(segment) >= 2:
                    points = " ".join(f"{_f(x)},{_f(y)}" for x, y in segment)
                    parts.append(
                        f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2.00" '
                        'stroke-linejoin="round" stroke-linecap="round"/>'
                    )
                segment = []
                continue
            segment.append((position, y_scale(value)))
            if not scatter:
                parts.append(
                    f'<circle cx="{_f(position)}" cy="{_f(y_scale(value))}" r="2.60" fill="{color}" '
                    'stroke="#ffffff" stroke-width="0.80"/>'
                )
        if len(segment) >= 2:
            points = " ".join(f"{_f(x)},{_f(y)}" for x, y in segment)
            parts.append(
                f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2.00" '
                'stroke-linejoin="round" stroke-linecap="round"/>'
            )

    _axis_labels(parts, spec.get("x") or "", prepared["facts"]["y_label"])
    _legend(parts, list(prepared["series_values"]), PLOT_RIGHT - 6.0, PLOT_TOP + 8.0)
    _note(parts, spec.get("note") or "")
    _svg_end(parts)
    return "\n".join(parts) + "\n"


def _render_bar(plan: dict[str, Any]) -> str:
    spec = plan["spec"]
    prepared = plan["prepared"]
    parts: list[str] = []
    _svg_start(parts, spec["title"])

    y_low, y_high = _bar_range(prepared["all_y"])
    y_scale = _scale(y_low, y_high, PLOT_BOTTOM, PLOT_TOP)
    y_ticks = _nice_ticks(y_low, y_high, 5)
    _draw_y_grid(parts, y_ticks, y_scale)
    _draw_axis_frame(parts, spec.get("x"))

    labels = prepared["x_labels"]
    count = len(labels)
    names = list(prepared["series_values"])
    width = (PLOT_RIGHT - PLOT_LEFT) / max(1, count)
    group_width = width * 0.78
    bar_width = group_width / max(1, len(names))
    stride = max(1, math.ceil(count / 10))
    baseline = y_scale(0.0)

    for index, label in enumerate(labels):
        center = PLOT_LEFT + width * (index + 0.5)
        for series_index, name in enumerate(names):
            value = prepared["series_values"][name][index]
            if value is None:
                continue
            color = PALETTE[series_index % len(PALETTE)]
            top = y_scale(value)
            bar_height = abs(baseline - top)
            left = center - group_width / 2.0 + series_index * bar_width
            parts.append(
                f'<rect x="{_f(left)}" y="{_f(min(top, baseline))}" width="{_f(max(0.5, bar_width - 1.0))}" '
                f'height="{_f(bar_height)}" fill="{color}" fill-opacity="0.88" rx="1.00"/>'
            )
        if index % stride == 0 or index == count - 1:
            parts.append(
                f'<text x="{_f(center)}" y="{_f(PLOT_BOTTOM + 16.0)}" text-anchor="middle" '
                f'font-family="{FONT}" font-size="10" fill="#6b7280">{_esc(_truncate(label, 12))}</text>'
            )

    _axis_labels(parts, spec.get("x") or "", prepared["facts"]["y_label"])
    _legend(parts, names, PLOT_RIGHT - 6.0, PLOT_TOP + 8.0)
    _note(parts, spec.get("note") or "")
    _svg_end(parts)
    return "\n".join(parts) + "\n"


def _render_radar(plan: dict[str, Any]) -> str:
    spec = plan["spec"]
    prepared = plan["prepared"]
    axes = prepared["axes"]
    layers = prepared["layers"]
    normalized = prepared["normalized"]
    offsets = prepared["offsets"]
    parts: list[str] = []
    _svg_start(parts, spec["title"])

    center_x, center_y, radius = 350.0, 220.0, 118.0
    angles = [-math.pi / 2.0 + 2.0 * math.pi * index / len(axes) for index in range(len(axes))]

    parts.append('<g class="grid">')
    for ring in (0.25, 0.5, 0.75, 1.0):
        points = " ".join(
            f"{_f(center_x + math.cos(angle) * radius * ring)},{_f(center_y + math.sin(angle) * radius * ring)}"
            for angle in angles
        )
        parts.append(
            f'<polygon points="{points}" fill="none" stroke="#e5e7eb" stroke-width="1.00"/>'
        )
    for angle in angles:
        parts.append(
            f'<line x1="{_f(center_x)}" y1="{_f(center_y)}" '
            f'x2="{_f(center_x + math.cos(angle) * radius)}" y2="{_f(center_y + math.sin(angle) * radius)}" '
            'stroke="#e5e7eb" stroke-width="1.00"/>'
        )
    parts.append("</g>")
    for ring in (0.25, 0.5, 0.75, 1.0):
        parts.append(
            f'<text x="{_f(center_x + 4.0)}" y="{_f(center_y - radius * ring + 3.0)}" font-family="{FONT}" '
            f'font-size="8" fill="#9ca3af">{_esc(f"{ring:.2f}")}</text>'
        )

    for axis_index, name in enumerate(axes):
        angle = angles[axis_index]
        label_x = center_x + math.cos(angle) * (radius + 20.0)
        label_y = center_y + math.sin(angle) * (radius + 20.0)
        parts.append(
            f'<text x="{_f(label_x)}" y="{_f(label_y)}" text-anchor="middle" font-family="{FONT}" '
            f'font-size="10" fill="#374151">{_esc(_truncate(name, 14))}</text>'
        )

    for layer_index, values in enumerate(normalized):
        offset_x, offset_y = offsets[layer_index]
        color = PALETTE[layer_index % len(PALETTE)]
        points = " ".join(
            f"{_f(center_x + offset_x + math.cos(angles[index]) * radius * value)},"
            f"{_f(center_y + offset_y + math.sin(angles[index]) * radius * value)}"
            for index, value in enumerate(values)
        )
        parts.append(
            f'<polygon points="{points}" fill="{color}" fill-opacity="0.10" stroke="{color}" '
            'stroke-width="1.40" stroke-linejoin="round"/>'
        )
        for index, value in enumerate(values):
            point_x = center_x + offset_x + math.cos(angles[index]) * radius * value
            point_y = center_y + offset_y + math.sin(angles[index]) * radius * value
            parts.append(
                f'<circle cx="{_f(point_x)}" cy="{_f(point_y)}" r="2.00" fill="{color}"/>'
            )

    _legend(parts, layers, PLOT_RIGHT - 6.0, PLOT_TOP + 8.0)
    _axis_labels(parts, spec.get("x") or "layer", "normalized")
    _note(parts, spec.get("note") or "")
    _svg_end(parts)
    return "\n".join(parts) + "\n"


def _render_table(plan: dict[str, Any]) -> str:
    spec = plan["spec"]
    prepared = plan["prepared"]
    columns = prepared["columns"]
    rows = prepared["rows"]
    parts: list[str] = []
    _svg_start(parts, spec["title"])

    left, right = 40.0, 680.0
    top = 64.0
    header_height = 30.0
    row_height = 22.0
    width = right - left
    column_width = width / max(1, len(columns))
    grid: list[str] = ['<g class="grid">']
    grid.append(
        f'<rect x="{_f(left)}" y="{_f(top)}" width="{_f(width)}" height="{_f(header_height)}" '
        'fill="#f3f4f6" stroke="#d1d5db" stroke-width="1.00"/>'
    )
    for index in range(len(rows) + 1):
        y = top + header_height + index * row_height
        grid.append(
            f'<line x1="{_f(left)}" y1="{_f(y)}" x2="{_f(right)}" y2="{_f(y)}" '
            'stroke="#e5e7eb" stroke-width="1.00"/>'
        )
    for index in range(len(columns) + 1):
        x = left + index * column_width
        grid.append(
            f'<line x1="{_f(x)}" y1="{_f(top)}" x2="{_f(x)}" y2="{_f(top + header_height + len(rows) * row_height)}" '
            'stroke="#e5e7eb" stroke-width="1.00"/>'
        )
    grid.append("</g>")
    parts.extend(grid)

    for index, name in enumerate(columns):
        center = left + column_width * (index + 0.5)
        parts.append(
            f'<text x="{_f(center)}" y="{_f(top + 19.0)}" text-anchor="middle" font-family="{FONT}" '
            f'font-size="11" font-weight="600" fill="#111827">{_esc(_truncate(name, max(4, int(column_width / 7))))}</text>'
        )

    for row_index, row in enumerate(rows):
        y = top + header_height + row_index * row_height
        for column_index, name in enumerate(columns):
            value = row[column_index] if column_index < len(row) else None
            cell = _truncate(_cell_text(value), max(4, int((column_width - 10.0) / 5.6)))
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                x = left + column_width * (column_index + 1) - 6.0
                anchor = "end"
            else:
                x = left + column_width * column_index + 6.0
                anchor = "start"
            parts.append(
                f'<text x="{_f(x)}" y="{_f(y + 15.0)}" text-anchor="{anchor}" font-family="{FONT}" '
                f'font-size="10" fill="#374151">{_esc(cell)}</text>'
            )

    note = spec.get("note") or ""
    if len(rows) < prepared["total_rows"]:
        note = f"showing {len(rows)} of {prepared['total_rows']} rows" + (f" · {note}" if note else "")
    if prepared["total_columns"] > len(columns):
        note = f"showing {len(columns)} of {prepared['total_columns']} columns" + (f" · {note}" if note else "")
    _note(parts, note, 406.0)
    _svg_end(parts)
    return "\n".join(parts) + "\n"


def render_svg(plan: dict[str, Any]) -> str:
    """把 :func:`prepare_chart` 的结果渲染为 SVG 字符串。"""
    kind = plan["spec"]["kind"]
    if kind == "table":
        return _render_table(plan)
    if kind == "radar3d":
        return _render_radar(plan)
    if kind == "bar":
        return _render_bar(plan)
    return _render_line(plan, scatter=(kind == "scatter"))


# ------------------------------------------------------------------ matplotlib 脚本
def _py(value: Any) -> str:
    return pprint.pformat(value, width=110, sort_dicts=False)


def _render_script(plan: dict[str, Any]) -> str:
    spec = plan["spec"]
    prepared = plan["prepared"]
    kind = spec["kind"]
    lines: list[str] = [
        "# -*- coding: utf-8 -*-",
        '"""Reproducible matplotlib script generated by AgentLit dataviz.',
        "",
        "Usage:",
        "    python dataviz_chart.py",
        "The script embeds the exact data used by the server-side SVG renderer,",
        'so it can be executed offline and still reproduces the same chart.',
        '"""',
        "",
        "import math",
        "",
        "import pandas as pd",
        "import matplotlib",
        "",
        'matplotlib.use("Agg")  # headless-friendly; remove to open an interactive window',
        "import matplotlib.pyplot as plt  # noqa: E402",
    ]
    if kind == "radar3d":
        lines.append("from matplotlib.patches import Polygon  # noqa: E402")

    lines += [
        "",
        f"TITLE = {_py(spec['title'])}",
        f"KIND = {_py(kind)}",
        "",
        "fig, ax = plt.subplots(figsize=(7.2, 4.2))",
    ]

    if kind in ("line", "scatter"):
        x_values = prepared["x_values"] if prepared["x_numeric"] else prepared["x_labels"]
        lines += [
            f"X_VALUES = {_py(x_values)}",
            f"SERIES_VALUES = {_py(prepared['series_values'])}",
            "",
        ]
        if kind == "line":
            lines += [
                "for name, values in SERIES_VALUES.items():",
                '    ax.plot(X_VALUES, values, marker="o", markersize=3, linewidth=1.8, label=name)',
            ]
        else:
            lines += [
                "for name, values in SERIES_VALUES.items():",
                "    ax.scatter(X_VALUES, values, s=18, label=name, alpha=0.85)",
            ]
        lines += [
            "ax.set_xlabel(" + _py(spec.get("x") or "x") + ")",
            "ax.set_ylabel(" + _py(prepared["facts"]["y_label"]) + ")",
            "ax.grid(True, linestyle='--', alpha=0.35)",
            "ax.legend()",
            "ax.set_title(TITLE)",
        ]
    elif kind == "bar":
        lines += [
            f"X_LABELS = {_py(prepared['x_labels'])}",
            f"SERIES_VALUES = {_py(prepared['series_values'])}",
            "",
            "positions = list(range(len(X_LABELS)))",
            "series_count = max(1, len(SERIES_VALUES))",
            "bar_width = 0.8 / series_count",
            "for index, (name, values) in enumerate(SERIES_VALUES.items()):",
            "    offsets = [p + (index - (series_count - 1) / 2) * bar_width for p in positions]",
            "    heights = [0.0 if value is None else value for value in values]",
            '    ax.bar(offsets, heights, width=bar_width, label=name, alpha=0.88)',
            "ax.set_xticks(positions)",
            "ax.set_xticklabels(X_LABELS, rotation=30, ha='right')",
            "ax.set_ylabel(" + _py(prepared["facts"]["y_label"]) + ")",
            "ax.grid(True, axis='y', linestyle='--', alpha=0.35)",
            "ax.legend()",
            "ax.set_title(TITLE)",
        ]
    elif kind == "radar3d":
        colors = [PALETTE[index % len(PALETTE)] for index in range(len(prepared["layers"]))]
        lines += [
            f"AXES = {_py(prepared['axes'])}",
            f"LAYERS = {_py(prepared['layers'])}",
            f"NORMALIZED = {_py(prepared['normalized'])}",
            f"OFFSETS = {_py(prepared['offsets'])}",
            f"COLORS = {_py(colors)}",
            "",
            "angles = [-math.pi / 2 + 2 * math.pi * i / len(AXES) for i in range(len(AXES))]",
            "for ring in (0.25, 0.5, 0.75, 1.0):",
            "    points = [(math.cos(a) * ring, math.sin(a) * ring) for a in angles]",
            "    ax.add_patch(Polygon(points, closed=True, fill=False, edgecolor='#e5e7eb'))",
            "for angle in angles:",
            "    ax.plot([0, math.cos(angle)], [0, math.sin(angle)], color='#e5e7eb', linewidth=1)",
            "for index, values in enumerate(NORMALIZED):",
            "    ox, oy = OFFSETS[index]",
            "    points = [",
            "        (ox + math.cos(angles[i]) * values[i], oy + math.sin(angles[i]) * values[i])",
            "        for i in range(len(AXES))",
            "    ]",
            "    ax.add_patch(Polygon(points, closed=True, facecolor=COLORS[index], edgecolor=COLORS[index],",
            "                         alpha=0.25, label=LAYERS[index]))",
            "for i, name in enumerate(AXES):",
            "    ax.text(1.08 * math.cos(angles[i]), 1.08 * math.sin(angles[i]), name, ha='center', va='center')",
            "ax.set_aspect('equal')",
            "ax.set_xlim(-1.45, 1.45)",
            "ax.set_ylim(-1.45, 1.45)",
            "ax.axis('off')",
            "ax.legend(loc='upper right', fontsize=8)",
            "ax.set_title(TITLE)",
        ]
    else:
        lines += [
            f"COLUMNS = {_py(prepared['columns'])}",
            f"ROWS = {_py(prepared['rows'])}",
            "",
            "DF = pd.DataFrame(ROWS, columns=COLUMNS)",
            "ax.axis('off')",
            "table = ax.table(cellText=DF.values, colLabels=DF.columns, loc='center')",
            "table.auto_set_font_size(False)",
            "table.set_fontsize(9)",
            "table.scale(1.0, 1.35)",
            "ax.set_title(TITLE, fontsize=11)",
        ]

    lines += [
        "",
        "fig.tight_layout()",
        'fig.savefig("dataviz_chart.png", dpi=200, bbox_inches="tight")',
        'print("saved dataviz_chart.png")',
        "",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------ 公开接口
def svg_from_spec(dataset: dict[str, Any], spec: dict[str, Any]) -> str:
    """独立可测的 SVG 生成函数（不访问数据库、不调用模型）。"""
    return render_svg(prepare_chart(dataset, spec))


def matplotlib_script(dataset: dict[str, Any], spec: dict[str, Any]) -> str:
    """生成可复现的 matplotlib 脚本（纯文本，不执行）。"""
    return _render_script(prepare_chart(dataset, spec))
