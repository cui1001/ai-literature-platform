"""时间工具：统一 UTC ISO 字符串，避免本地时区导致的产物漂移。"""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """秒级 ISO8601（带 Z），用于数据库时间戳。"""
    return utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def today_iso() -> str:
    return utc_now().date().isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def human_ts(value: str | None) -> str:
    dt = parse_iso(value)
    return dt.strftime("%Y-%m-%d %H:%M") if dt else ""
