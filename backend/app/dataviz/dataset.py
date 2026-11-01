"""CSV 数据集：解码、类型推断、画像统计与入库。

设计要点
--------
- 只用标准库 ``csv`` 解析，不依赖 pandas / numpy；
- 同一份字节内容 + 文件名 → 同一个数据集主键，重复上传幂等；
- 列类型在写入时确定并随记录持久化，读取时无需再次猜测；
- 统计量使用 ``statistics`` 计算（标准差为总体标准差），保留 6 位小数。
"""

from __future__ import annotations

import csv
import io
import math
import re
import statistics
from pathlib import Path
from typing import Any

from ..core.config import Settings, get_settings
from ..core.errors import NotFoundError, ValidationError
from ..core.hashing import sha256_bytes, short_id
from ..db import repo

TYPE_NUMBER = "number"
TYPE_TEXT = "text"
TYPE_DATE = "date"
COLUMN_TYPES = (TYPE_NUMBER, TYPE_TEXT, TYPE_DATE)

ENCODINGS = ("utf-8-sig", "utf-8", "gbk")
PREVIEW_ROWS = 10

_NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_DATE_RES = (
    re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$"),
    re.compile(r"^\d{4}[-/]\d{1,2}$"),
    re.compile(r"^\d{4}\.\d{1,2}\.\d{1,2}$"),
    re.compile(r"^\d{1,2}[-/]\d{1,2}[-/]\d{4}$"),
    re.compile(r"^\d{4}年\d{1,2}月(?:\d{1,2}日)?$"),
    re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?$"),
)


# ------------------------------------------------------------------ 基础工具
def _round(value: float | None) -> float | None:
    if value is None:
        return None
    out = round(float(value), 6)
    return 0.0 if out == 0 else out


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def parse_number(raw: Any) -> float | None:
    """把单元格解析为有限浮点数；失败返回 ``None``。"""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        value = float(raw)
        return value if math.isfinite(value) else None
    text = str(raw).strip()
    if not text or not _NUMBER_RE.match(text):
        return None
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def is_date_value(raw: Any) -> bool:
    text = str(raw).strip()
    return bool(text) and any(pattern.match(text) for pattern in _DATE_RES)


def infer_type(values: list[Any]) -> str:
    """根据一列的非空值推断类型：number / date / text。"""
    filled = [value for value in values if value is not None and str(value).strip() != ""]
    if not filled:
        return TYPE_TEXT
    if all(parse_number(value) is not None for value in filled):
        return TYPE_NUMBER
    if all(is_date_value(value) for value in filled):
        return TYPE_DATE
    return TYPE_TEXT


def _dedupe_names(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out: list[str] = []
    for name in names:
        if name in seen:
            seen[name] += 1
            candidate = f"{name}_{seen[name]}"
            while candidate in seen:
                seen[name] += 1
                candidate = f"{name}_{seen[name]}"
            seen[candidate] = 1
            out.append(candidate)
            continue
        seen[name] = 1
        out.append(name)
    return out


def _decode(data: bytes) -> str:
    for encoding in ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValidationError("CSV 编码无法识别（支持 UTF-8 / GBK）")


def _column_stats(values: list[Any]) -> dict[str, float | None]:
    numbers = [float(value) for value in values if _is_finite_number(value)]
    if not numbers:
        return {"min": None, "max": None, "mean": None, "median": None, "std": None}
    return {
        "min": _round(min(numbers)),
        "max": _round(max(numbers)),
        "mean": _round(sum(numbers) / len(numbers)),
        "median": _round(statistics.median(numbers)),
        "std": _round(statistics.pstdev(numbers)),
    }


def normalize_columns(columns: Any, rows: list[list[Any]]) -> list[dict[str, Any]]:
    """把仓库中可能不完整的列定义补齐为 ``{name,type,index}``。"""
    raw_columns = list(columns or [])
    out: list[dict[str, Any]] = []
    width = max((len(row) for row in rows), default=len(raw_columns))
    if not raw_columns:
        raw_columns = [f"col_{i + 1}" for i in range(width)]
    for position, raw in enumerate(raw_columns):
        if isinstance(raw, str):
            raw = {"name": raw}
        if not isinstance(raw, dict):
            raw = {"name": f"col_{position + 1}"}
        name = str(raw.get("name") or f"col_{position + 1}")
        try:
            index = int(raw.get("index", position))
        except (TypeError, ValueError):
            index = position
        if index < 0 or index >= max(width, len(raw_columns)):
            index = position
        column_type = str(raw.get("type") or "").lower()
        if column_type not in COLUMN_TYPES:
            values = [row[index] for row in rows if index < len(row)]
            column_type = infer_type(values)
        out.append({"name": name, "type": column_type, "index": index})
    return out


def dataset_view(dataset: dict[str, Any]) -> dict[str, Any]:
    """把数据集字典整理为渲染层可用的 ``{name,columns,rows}``。"""
    if not isinstance(dataset, dict):
        raise ValidationError("dataset 必须是对象")
    rows_raw = dataset.get("rows")
    if rows_raw is None:
        raise ValidationError("数据集缺少行数据")
    if not isinstance(rows_raw, (list, tuple)):
        raise ValidationError("数据集 rows 必须是数组")
    columns = normalize_columns(dataset.get("columns") or [], [])
    if rows_raw and isinstance(rows_raw[0], dict):
        names = [column["name"] for column in columns]
        if not names:
            names = list(rows_raw[0].keys())
            columns = [{"name": name, "type": infer_type([row.get(name) for row in rows_raw]), "index": i}
                       for i, name in enumerate(names)]
        rows = [[row.get(name) for name in names] for row in rows_raw]
    else:
        rows = [list(row) if isinstance(row, (list, tuple)) else [row] for row in rows_raw]
    if not rows:
        raise ValidationError("数据集没有数据行")
    columns = normalize_columns(columns, rows)
    width = max(len(row) for row in rows)
    padded = [list(row) + [None] * (width - len(row)) for row in rows]
    name = str(dataset.get("name") or dataset.get("id") or "dataset")
    return {"name": name, "columns": columns, "rows": padded}


def column_stats_for(rows: list[list[Any]], columns: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """按数值列计算统计量；键顺序与列顺序一致。"""
    stats: dict[str, dict[str, Any]] = {}
    for column in columns:
        if column["type"] != TYPE_NUMBER:
            continue
        index = int(column["index"])
        stats[column["name"]] = _column_stats([row[index] for row in rows if index < len(row)])
    return stats


# ------------------------------------------------------------------ 数据库载荷
def _payload(record: dict[str, Any], columns: list[dict[str, Any]], rows: list[list[Any]]) -> dict[str, Any]:
    stats = column_stats_for(rows, columns)
    preview = [
        {column["name"]: row[column["index"]] if column["index"] < len(row) else None for column in columns}
        for row in rows[:PREVIEW_ROWS]
    ]
    return {
        "id": str(record.get("id") or ""),
        "name": str(record.get("name") or "dataset"),
        "columns": columns,
        "row_count": len(rows),
        "preview": preview,
        "numeric_columns": [column["name"] for column in columns if column["type"] == TYPE_NUMBER],
        "stats": stats,
        "rows": rows,
        "size_bytes": int(record.get("size_bytes") or 0),
        "path": str(record.get("path") or ""),
        "created_at": str(record.get("created_at") or ""),
    }


# ------------------------------------------------------------------ 公开接口
def parse_csv_bytes(data: bytes, filename: str) -> dict[str, Any]:
    """解析 CSV 字节流，推断列类型，写入 SQLite 并返回数据集画像。"""
    if not isinstance(data, (bytes, bytearray, memoryview)) or not bytes(data):
        raise ValidationError("CSV 数据为空")
    raw_bytes = bytes(data)
    text = _decode(raw_bytes)

    try:
        reader = csv.reader(io.StringIO(text, newline=""))
        raw_rows = [row for row in reader]
    except csv.Error as exc:  # pragma: no cover - csv 模块极少抛错
        raise ValidationError(f"CSV 解析失败: {exc}") from exc

    raw_rows = [row for row in raw_rows if any(str(cell).strip() for cell in row)]
    if not raw_rows:
        raise ValidationError("CSV 数据为空")
    header = [str(cell).strip() for cell in raw_rows[0]]
    data_rows = raw_rows[1:]
    if not data_rows:
        raise ValidationError("CSV 没有数据行")

    width = max(len(row) for row in raw_rows)
    # 去掉所有行（含表头）都是空白的尾列
    while width > 0:
        last = width - 1
        if (last < len(header) and header[last]) or any(
            last < len(row) and str(row[last]).strip() for row in data_rows
        ):
            break
        width -= 1
    if width <= 0:
        raise ValidationError("CSV 缺少有效列")
    names = [
        header[index] if index < len(header) and header[index] else f"col_{index + 1}"
        for index in range(width)
    ]
    names = _dedupe_names(names)

    table: list[list[str]] = []
    for row in data_rows:
        table.append([str(row[index]).strip() if index < len(row) else "" for index in range(width)])
    if not table:
        raise ValidationError("CSV 没有数据行")

    types = [infer_type([row[index] for row in table]) for index in range(width)]
    typed_rows: list[list[Any]] = []
    for row in table:
        values: list[Any] = []
        for index, cell in enumerate(row):
            if cell == "":
                values.append(None)
                continue
            if types[index] == TYPE_NUMBER:
                number = parse_number(cell)
                if number is None:
                    raise ValidationError(f"列「{names[index]}」包含非法数值: {cell!r}")
                values.append(number)
            else:
                values.append(cell)
        typed_rows.append(values)

    columns = [
        {"name": names[index], "type": types[index], "index": index}
        for index in range(width)
    ]
    name = Path(str(filename or "")).name or "dataset.csv"
    dataset_id = short_id(f"dataset|{name}|{sha256_bytes(raw_bytes)}", 12)

    settings: Settings = get_settings()
    path = settings.data_dir / "datasets" / f"{dataset_id}.csv"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw_bytes)
    except OSError:  # 只读数据目录不应阻止入库
        path = Path("")

    repo.save_dataset(
        {
            "id": dataset_id,
            "name": name,
            "columns": columns,
            "rows": typed_rows,
            "path": str(path),
            "size_bytes": len(raw_bytes),
        }
    )
    record = repo.get_dataset(dataset_id) or {"id": dataset_id, "name": name, "size_bytes": len(raw_bytes)}
    return _payload(record, columns, typed_rows)


def get_dataset(dataset_id: str) -> dict[str, Any]:
    """读取数据集画像；不存在时抛 ``NotFoundError``。"""
    record = repo.get_dataset(str(dataset_id or ""))
    if not record:
        raise NotFoundError(f"数据集不存在: {dataset_id}")
    rows = [list(row) for row in (record.get("rows") or [])]
    columns = normalize_columns(record.get("columns") or [], rows)
    return _payload(record, columns, rows)


def list_datasets(limit: int = 20) -> list[dict[str, Any]]:
    """按创建时间倒序列出数据集摘要。"""
    try:
        count = int(limit)
    except (TypeError, ValueError) as exc:
        raise ValidationError("limit 必须是正整数") from exc
    if count <= 0:
        return []
    out: list[dict[str, Any]] = []
    for row in repo.list_datasets(limit=count):
        dataset_id = str(row.get("id") or "")
        full = repo.get_dataset(dataset_id)
        rows = [list(item) for item in ((full or {}).get("rows") or [])]
        columns = normalize_columns(row.get("columns") or [], rows)
        out.append(
            {
                "id": dataset_id,
                "name": str(row.get("name") or "dataset"),
                "columns": columns,
                "row_count": len(rows),
                "numeric_columns": [column["name"] for column in columns if column["type"] == TYPE_NUMBER],
                "size_bytes": int(row.get("size_bytes") or 0),
                "created_at": str(row.get("created_at") or ""),
            }
        )
    return out
